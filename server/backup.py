"""Operational backup, verification and restore for the Idle Ops database.

Run with the idle-ops service stopped for restore; backups are safe while it
runs because they use SQLite's online backup API.

    python3 -m server.backup create --db data/idle-ops.sqlite3 --kind daily
    python3 -m server.backup create --db data/idle-ops.sqlite3 --kind pre-deploy
    python3 -m server.backup verify data/backups/idle-ops-daily-<stamp>.sqlite3
    python3 -m server.backup restore --db data/idle-ops.sqlite3 --file <backup>
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

from .store import BACKUP_KEEP, Store


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
    path = Store(db, backup_dir=target_dir).backup(kind, keep=keep)
    print(path)
    return path


def verify(path):
    if not os.path.isfile(path):
        raise SystemExit(f"backup not found: {path}")
    try:
        status = Store.check_sqlite(path)
    except sqlite3.Error as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return False
    print(status)
    return status == "ok"


def restore(db, source, directory=None):
    if not verify(source):
        raise SystemExit(f"refusing to restore an unverified backup: {source}")
    target = os.path.abspath(db)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    lock = None
    if os.path.exists(target):
        saved = Store(target, backup_dir=backup_dir(target, directory)).backup("pre-restore", keep=0)
        print(f"current database saved to {saved}")
        lock = sqlite3.connect(target, timeout=1.0, isolation_level=None)
        try:
            lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            lock.close()
            raise SystemExit("database is locked; stop the idle-ops service before restoring") from exc
    try:
        staging = target + ".restore-tmp"
        if os.path.exists(staging):
            os.remove(staging)
        src = sqlite3.connect(source, timeout=5.0)
        try:
            dst = sqlite3.connect(staging)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        os.replace(staging, target)
        for suffix in ("-wal", "-shm"):
            side = target + suffix
            if os.path.exists(side):
                os.remove(side)
    finally:
        if lock is not None:
            lock.close()
    if not verify(target):
        raise SystemExit(f"restored database failed verification: {target}")
    print(f"restored {source} -> {target}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m server.backup")
    sub = parser.add_subparsers(dest="command", required=True)

    make = sub.add_parser("create", help="write a verified snapshot")
    make.add_argument("--db", required=True)
    make.add_argument("--dir", default=None)
    make.add_argument("--kind", default="manual")
    make.add_argument("--keep", type=int, default=BACKUP_KEEP)
    make.add_argument("--force", action="store_true")

    check = sub.add_parser("verify", help="run PRAGMA quick_check on a file")
    check.add_argument("file")

    put = sub.add_parser("restore", help="replace the live database with a backup")
    put.add_argument("--db", required=True)
    put.add_argument("--file", required=True)
    put.add_argument("--dir", default=None)

    args = parser.parse_args(argv)
    if args.command == "create":
        create(args.db, args.dir, args.kind, args.keep, args.force)
    elif args.command == "verify":
        return 0 if verify(args.file) else 1
    else:
        restore(args.db, args.file, args.dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
