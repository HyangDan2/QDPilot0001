"""Operational backup, verification and restore for the Idle Ops database.

Run with the idle-ops service running for backups (SQLite online backup) and
stopped for restores. ``restore`` refuses to proceed while the service lock is
held, validates the source and a staged copy before replacing anything, keeps
the previous database until the replacement is verified, and preserves its
owner and mode.

    python3 -m server.backup create --db data/idle-ops.sqlite3 --kind daily
    python3 -m server.backup create --db data/idle-ops.sqlite3 --kind pre-deploy
    python3 -m server.backup verify data/backups/idle-ops-daily-<stamp>.sqlite3
    python3 -m server.backup restore --db data/idle-ops.sqlite3 --file <backup>
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
import time
from urllib.parse import quote

from . import economy
from .twin import REQUIRED_TABLES as TWIN_TABLES
from .store import BACKUP_KEEP, SCHEMA_VERSION, Store, WorldBusy

REQUIRED_TABLES = {
    "world": {"id", "state_json", "updated_at"},
    "world_ops": {"op_id", "created_at"},
    "world_events": {"id", "created_at", "kind", "payload_json"},
}


def backup_dir(db, directory=None):
    if directory:
        return os.path.abspath(directory)
    parent = os.path.dirname(os.path.abspath(db))
    return os.path.abspath(os.environ.get("IDLE_OPS_BACKUP_DIR") or os.path.join(parent, "backups"))


def create(db, directory=None, kind="manual", keep=BACKUP_KEEP, force=False):
    if not os.path.isfile(db):
        raise SystemExit(f"database not found: {db}")
    target_dir = backup_dir(db, directory)
    if kind == "daily" and not force:
        today = time.strftime("%Y%m%d", time.gmtime())
        if os.path.isdir(target_dir):
            prefix = f"idle-ops-daily-{today}T"
            if any(name.startswith(prefix) for name in os.listdir(target_dir)):
                print("daily backup already recorded today; pass --force to add another")
                return None
    path = Store(db, backup_dir=target_dir, initialize=False).backup(kind, keep=keep)
    print(path)
    return path


def verify_database(path):
    """Inspect an archive beyond ``quick_check``.

    Returns ``{"ok": bool, "quickCheck": str, "schemaVersion": int|None,
    "problems": [str, ...]}``. A snapshot must be this application's schema,
    hold exactly one world row, and contain parseable state, operation and
    event payloads before it may replace the live database.
    """
    problems = []
    schema = None
    quick = None
    try:
        quick = Store.check_sqlite(path)
    except sqlite3.Error as exc:
        return {"ok": False, "quickCheck": str(exc), "schemaVersion": None,
                "problems": [f"not a readable SQLite database: {exc}"]}
    if quick != "ok":
        problems.append(f"quick_check failed: {quick}")
    con = sqlite3.connect(f"file:{quote(os.path.abspath(path))}?mode=ro", uri=True, timeout=5.0)
    try:
        try:
            schema = int(con.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.Error as exc:
            problems.append(f"cannot read schema version: {exc}")
        if schema is not None and (schema < 0 or schema > SCHEMA_VERSION):
            problems.append(f"unsupported schema version: {schema}")
        required_tables = {**REQUIRED_TABLES, **TWIN_TABLES} if schema is not None and schema >= 5 else REQUIRED_TABLES
        tables = {
            row[0]: {column[1] for column in con.execute(f'PRAGMA table_info("{row[0]}")')}
            for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            if row[0] in required_tables
        }
        for name, required in required_tables.items():
            if name not in tables:
                problems.append(f"missing table: {name}")
            else:
                missing = required - tables[name]
                if missing:
                    problems.append(f"table {name} missing columns: {', '.join(sorted(missing))}")
        if not problems:
            rows = con.execute("SELECT id, state_json, updated_at FROM world").fetchall()
            if len(rows) != 1 or int(rows[0][0]) != 1:
                problems.append("world table must contain exactly the id=1 row")
            else:
                try:
                    state = economy.normalize_state(json.loads(rows[0][1]))
                    if not isinstance(rows[0][2], (int, float)) or rows[0][2] <= 0:
                        problems.append("world.updated_at is not a positive timestamp")
                    if state["created_at"] <= 0:
                        problems.append("world state has no creation time")
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    problems.append(f"world state is invalid: {exc}")
            op_columns = tables["world_ops"]
            action_column = "action_json" if "action_json" in op_columns else "NULL"
            result_column = "result_json" if "result_json" in op_columns else "NULL"
            if schema is not None and schema >= 2 and (
                "action_json" not in op_columns or "result_json" not in op_columns
            ):
                problems.append("world_ops is missing operation payload columns")
            for op_id, action, result in con.execute(
                f"SELECT op_id, {action_column}, {result_column} FROM world_ops"
            ):
                if not isinstance(op_id, str) or not op_id:
                    problems.append("world_ops contains an empty op id")
                    continue
                for label, payload in (("action", action), ("result", result)):
                    if payload is None:
                        continue
                    try:
                        json.loads(payload)
                    except (TypeError, json.JSONDecodeError):
                        problems.append(f"world_ops {op_id} has invalid {label} JSON")
            for event_id, kind, payload in con.execute(
                "SELECT id, kind, payload_json FROM world_events"
            ):
                if not isinstance(kind, str) or not kind:
                    problems.append(f"world_events {event_id} has no kind")
                try:
                    json.loads(payload)
                except (TypeError, json.JSONDecodeError):
                    problems.append(f"world_events {event_id} has invalid JSON")
    except sqlite3.Error as exc:
        problems.append(f"schema inspection failed: {exc}")
    finally:
        con.close()
    return {"ok": not problems, "quickCheck": quick, "schemaVersion": schema, "problems": problems}


def verify(path, deep=True):
    if not os.path.isfile(path):
        raise SystemExit(f"backup not found: {path}")
    if not deep:
        try:
            status = Store.check_sqlite(path)
        except sqlite3.Error as exc:
            print(f"invalid: {exc}", file=sys.stderr)
            return False
        print(status)
        return status == "ok"
    report = verify_database(path)
    if report["ok"]:
        print("ok")
        return True
    for problem in report["problems"]:
        print(f"invalid: {problem}", file=sys.stderr)
    return False


def _copy_database(source, target):
    if os.path.exists(target):
        os.remove(target)
    src = sqlite3.connect(source, timeout=5.0)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
    finally:
        src.close()


def _fsync(path):
    with open(path, "rb") as handle:
        os.fsync(handle.fileno())


def _checkpoint(path):
    """Fold the WAL into the main file so the archive and rollback are complete.

    The live database runs in WAL mode; the newest commits may exist only in
    ``-wal``. Renaming or deleting the main file without checkpointing first
    would silently drop them.
    """
    con = sqlite3.connect(path, timeout=5.0)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()


def restore(db, source, directory=None):
    report = verify_database(source)
    if not report["ok"]:
        detail = "; ".join(report["problems"][:3]) or report["quickCheck"]
        raise SystemExit(f"refusing to restore an unverified backup: {source} ({detail})")
    target = os.path.abspath(db)
    if Store.service_running(target):
        raise SystemExit("idle-ops service is running; stop it before restoring")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    store = Store(target, backup_dir=backup_dir(target, directory), initialize=False)
    previous = None
    saved = None
    try:
        with store.gate.exclusive():
            if Store.service_running(target):  # re-check while the world is quiet
                raise SystemExit("idle-ops service is running; stop it before restoring")
            if os.path.exists(target):
                saved = store.backup("pre-restore", keep=0)
                print(f"current database saved to {saved}")
                _checkpoint(target)
                previous = target + ".restore-old"
                if os.path.exists(previous):
                    os.remove(previous)
                os.replace(target, previous)
            for suffix in ("-wal", "-shm"):
                side = target + suffix
                if os.path.exists(side):
                    os.remove(side)
            staging = target + ".restore-tmp"
            try:
                _copy_database(source, staging)
                staged = verify_database(staging)
                if not staged["ok"]:
                    detail = "; ".join(staged["problems"][:3])
                    raise SystemExit(f"staged database failed validation: {detail}")
                if previous is not None:
                    info = os.stat(previous)
                    if hasattr(os, "chown"):
                        try:
                            os.chown(staging, info.st_uid, info.st_gid)
                        except PermissionError:
                            pass
                    os.chmod(staging, stat.S_IMODE(info.st_mode))
                _fsync(staging)
                os.replace(staging, target)
                _fsync_dir(os.path.dirname(target))
            except BaseException:
                if os.path.exists(staging):
                    os.remove(staging)
                if previous is not None and os.path.exists(previous):
                    os.replace(previous, target)
                raise
            if previous is not None:
                os.remove(previous)
    except WorldBusy as exc:
        raise SystemExit(str(exc)) from exc
    final = verify_database(target)
    if not final["ok"]:
        if saved:
            raise SystemExit(
                f"restored database failed verification ({'; '.join(final['problems'][:3])}); "
                f"previous database is available at {saved}"
            )
        raise SystemExit(f"restored database failed verification: {target}")
    print(f"restored {source} -> {target}")


def _fsync_dir(path):
    try:
        handle = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover - platform without directory fsync
        return
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m server.backup")
    sub = parser.add_subparsers(dest="command", required=True)

    make = sub.add_parser("create", help="write a verified snapshot")
    make.add_argument("--db", required=True)
    make.add_argument("--dir", default=None)
    make.add_argument("--kind", default="manual")
    make.add_argument("--keep", type=int, default=BACKUP_KEEP)
    make.add_argument("--force", action="store_true")

    check = sub.add_parser("verify", help="validate schema, world row and payloads")
    check.add_argument("file")
    check.add_argument("--quick", action="store_true", help="only run PRAGMA quick_check")

    put = sub.add_parser("restore", help="replace the live database with a backup")
    put.add_argument("--db", required=True)
    put.add_argument("--file", required=True)
    put.add_argument("--dir", default=None)

    args = parser.parse_args(argv)
    if args.command == "create":
        create(args.db, args.dir, args.kind, args.keep, args.force)
    elif args.command == "verify":
        return 0 if verify(args.file, deep=not args.quick) else 1
    else:
        restore(args.db, args.file, args.dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
