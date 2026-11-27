"""SQLite persistence for the shared Idle Ops world.

Every read or action advances the single world to "now" inside one
BEGIN IMMEDIATE transaction, so concurrent tabs and the public viewer cannot
double-spend requests. Only the admin console applies actions; the public
viewer reads state and presentation events.

The legacy per-player table (`players`) is migrated once, from the row with
the highest lifetime total, and then preserved untouched.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote

try:  # POSIX only; the service runs on macOS/Linux.
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback keeps threads safe.
    fcntl = None

from . import economy

OP_TTL_SECONDS = 30 * 86400
SCHEMA_VERSION = 6
BACKUP_KEEP = 14
BACKUP_KINDS = ("daily", "pre-deploy", "pre-hardreset", "pre-restore", "manual")
MIN_FREE_BYTES = 256 * 1024 * 1024
# Normal world writes wait this long for a hard reset to finish before the API
# reports a retryable 503. A hard reset takes its exclusive gate before the
# backup starts and releases it only after the reset commits.
WORLD_LOCK_TIMEOUT = 20.0
HARD_RESET_LOCK_TIMEOUT = 60.0


class SchemaTooNew(RuntimeError):
    """The database was written by a newer release; refuse to touch it."""


class OpConflict(Exception):
    """Raised when one opId is reused for a different action."""


class WorldBusy(TimeoutError):
    """Another process holds the world write gate past the caller's deadline."""


class WorldUnavailable(RuntimeError):
    """The world row cannot be read; never substitute a fresh world."""


class _WriteGate:
    """Readers-writer gate around the SQLite world file.

    Normal world writes hold a shared gate; a hard reset holds it exclusively
    from before the pre-reset backup until after the reset commit. The lock is a
    ``flock`` on ``<db>.world.lock`` taken on its own descriptor for every
    acquisition, so it serialises threads of this process as well as other
    processes using the same database.

    The gate is deliberately taken *before* SQLite is touched and is released
    after the transaction commits. Backup file work never runs while a write
    transaction is open, so file I/O and SQLite cannot deadlock each other.
    """

    def __init__(self, path):
        self.path = path

    @contextlib.contextmanager
    def shared(self, timeout=WORLD_LOCK_TIMEOUT):
        with self._hold(False, timeout, WORLD_LOCK_TIMEOUT, "world write"):
            yield

    @contextlib.contextmanager
    def exclusive(self, timeout=HARD_RESET_LOCK_TIMEOUT):
        with self._hold(True, timeout, HARD_RESET_LOCK_TIMEOUT, "hard reset"):
            yield

    @contextlib.contextmanager
    def _hold(self, exclusive, timeout, default, label):
        if fcntl is None:  # pragma: no cover - non-POSIX fallback.
            yield
            return
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        wait = default if timeout is None else float(timeout)
        deadline = time.monotonic() + max(0.0, wait)
        handle = open(self.path, "a+b")
        try:
            while True:
                try:
                    fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise WorldBusy(f"{label} in progress; retry shortly")
                    time.sleep(0.01)
            try:
                yield
            finally:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:  # pragma: no cover - closing releases it anyway.
                    pass
        finally:
            handle.close()


EVENT_RETENTION = 400
EVENT_LIMIT = 60
EVENT_BYTES = 24 * 1024
EVENT_KINDS = {
    "buyServer": "install",
    "buyUpgrade": "upgrade",
    "reboot": "reboot",
    "reset": "reset",
    "hardReset": "reset",
}


@dataclass
class WorldResult:
    state: dict
    gains: dict
    events: list
    cursor: int
    message: str | None = None
    details: dict | None = None
    duplicate: bool = False
    now: float = 0.0
    away: float = 0.0
    timestamps: dict | None = None
    cursor_reset: bool = False
    event_gap: bool = False
    world_generation: int = 0
    revision: int = 0
    stale: bool = False

    @property
    def away_seconds(self):
        return float(self.away)


def _action_key(action):
    if not isinstance(action, dict):
        return None
    clean = {key: value for key, value in action.items() if key != "backup"}
    return json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _clean_actor(actor):
    if not isinstance(actor, str):
        return ""
    return "".join(ch for ch in actor if ch.isprintable())[:64]


def _event_payload(row):
    payload = json.loads(row[3])
    payload.update({"id": int(row[0]), "kind": row[2], "at": float(row[1])})
    return payload


def _read_events(con, since, until=None):
    """Return (events, cursor, cursor_reset, gap) for events after ``since``.

    ``cursor_reset`` is set when the cursor is past the newest stored id, which
    happens after the database is restored from an older backup. ``gap`` is set
    when the cursor fell out of the retention window or when more events are
    pending than one response may carry; in that case only the newest window is
    returned so a long absence never replays a flood of old events.

    ``until`` caps the read at the event id observed together with the state
    snapshot, so a response never mixes a state revision with events appended
    after it.
    """
    ceiling = " AND id <= ?" if until is not None else ""
    ceiling_args = (int(until),) if until is not None else ()
    bounds = con.execute(
        f"SELECT MIN(id), MAX(id) FROM world_events WHERE 1 = 1{ceiling}", ceiling_args
    ).fetchone()
    low = int(bounds[0] or 0)
    high = int(bounds[1] or 0)
    if since is None:
        return [], high, False, False
    start = int(since)
    cursor_reset = start > high
    if cursor_reset:
        start = high
    gap = start < low - 1
    pending = int(
        con.execute(
            f"SELECT COUNT(*) FROM world_events WHERE id > ?{ceiling}",
            (start, *ceiling_args),
        ).fetchone()[0]
    )
    if pending > EVENT_LIMIT:
        gap = True
        rows = con.execute(
            "SELECT id, created_at, kind, payload_json FROM world_events "
            f"WHERE id > ?{ceiling} ORDER BY id DESC LIMIT ?",
            (start, *ceiling_args, EVENT_LIMIT),
        ).fetchall()
        rows.reverse()
    else:
        rows = con.execute(
            "SELECT id, created_at, kind, payload_json FROM world_events "
            f"WHERE id > ?{ceiling} ORDER BY id ASC LIMIT ?",
            (start, *ceiling_args, EVENT_LIMIT),
        ).fetchall()
    chosen = []
    size = 0
    for row in reversed(rows):
        cost = len(str(row[3])) + 64
        if chosen and size + cost > EVENT_BYTES:
            gap = True
            break
        chosen.append(row)
        size += cost
    chosen.reverse()
    events = [_event_payload(row) for row in chosen]
    cursor = events[-1]["id"] if events else (high if cursor_reset else start)
    return events, cursor, cursor_reset, gap


class Store:
    def __init__(self, path, backup_dir=None, initialize=True, preserve_legacy=False):
        self.path = os.fspath(path)
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        self.backup_dir = os.fspath(
            backup_dir or os.environ.get("IDLE_OPS_BACKUP_DIR") or os.path.join(parent, "backups")
        )
        # Bumped on every committed world write; read-only snapshots use it to
        # invalidate their cache immediately after an admin action.
        self.generation = 0
        self.gate = _WriteGate(self.path + ".world.lock")
        self.service_lock_path = self.path + ".service.lock"
        self._service_lock = None
        if not initialize:
            return
        with self.gate.exclusive(), contextlib.closing(self._connect()) as con:
            stored = int(con.execute("PRAGMA user_version").fetchone()[0])
            if stored > SCHEMA_VERSION:
                raise SchemaTooNew(
                    f"database schema v{stored} is newer than this release (v{SCHEMA_VERSION})"
                )
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS world (
                    id         INTEGER PRIMARY KEY CHECK (id = 1),
                    state_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 0,
                    revision   INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS world_ops (
                    op_id       TEXT PRIMARY KEY,
                    created_at  REAL NOT NULL,
                    action_json TEXT,
                    result_json TEXT
                ) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS world_ops_created_at ON world_ops (created_at);
                CREATE TABLE IF NOT EXISTS world_events (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at   REAL NOT NULL,
                    kind         TEXT NOT NULL,
                    actor        TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS players (
                    username   TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS action_ops (
                    username   TEXT NOT NULL,
                    op_id      TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (username, op_id)
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS admin_seen (
                    username       TEXT PRIMARY KEY,
                    last_seen      REAL NOT NULL,
                    last_reward_at REAL NOT NULL,
                    checks         INTEGER NOT NULL DEFAULT 0,
                    observed_requests REAL NOT NULL DEFAULT 0,
                    observed_level INTEGER NOT NULL DEFAULT 1,
                    world_generation INTEGER NOT NULL DEFAULT 0
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS audit_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at  REAL NOT NULL,
                    actor       TEXT NOT NULL,
                    action      TEXT NOT NULL,
                    detail_json TEXT NOT NULL
                );
                """
            )
            columns = {row[1] for row in con.execute("PRAGMA table_info(world_ops)")}
            if "action_json" not in columns:
                con.execute("ALTER TABLE world_ops ADD COLUMN action_json TEXT")
            if "result_json" not in columns:
                con.execute("ALTER TABLE world_ops ADD COLUMN result_json TEXT")
            world_columns = {row[1] for row in con.execute("PRAGMA table_info(world)")}
            if "generation" not in world_columns:
                con.execute("ALTER TABLE world ADD COLUMN generation INTEGER NOT NULL DEFAULT 0")
            if "revision" not in world_columns:
                con.execute("ALTER TABLE world ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
            seen_columns = {row[1] for row in con.execute("PRAGMA table_info(admin_seen)")}
            for name, ddl in (
                ("observed_requests", "REAL NOT NULL DEFAULT 0"),
                ("observed_level", "INTEGER NOT NULL DEFAULT 1"),
                ("world_generation", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in seen_columns:
                    con.execute(f"ALTER TABLE admin_seen ADD COLUMN {name} {ddl}")
            if not preserve_legacy:
                con.execute("DELETE FROM action_ops WHERE created_at < ?", (self._now() - OP_TTL_SECONDS,))
                con.execute("DELETE FROM world_ops WHERE created_at < ?", (self._now() - OP_TTL_SECONDS,))
            from . import telemetry, twin

            self._seed_world(con, self._now())
            if stored < SCHEMA_VERSION:
                con.executescript("BEGIN IMMEDIATE;\n" + twin.SCHEMA + telemetry.SCHEMA
                                  + twin.DEVELOPMENT_SCHEMA + f"PRAGMA user_version = {SCHEMA_VERSION}; COMMIT;")

    @staticmethod
    def _now():
        return time.time()

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def _seed_world(self, con, now):
        if con.execute("SELECT 1 FROM world WHERE id = 1").fetchone():
            return
        best_state = None
        best_total = -1.0
        for row in con.execute("SELECT state_json FROM players"):
            try:
                candidate = economy.normalize_state(json.loads(row["state_json"]))
            except (ValueError, TypeError):
                continue
            if candidate["total_lifetime"] > best_total:
                best_state = candidate
                best_total = candidate["total_lifetime"]
        state = best_state or economy.initial_state(now)
        state["last_sim_at"] = float(now)
        state["last_seen"] = float(now)
        con.execute(
            "INSERT INTO world (id, state_json, updated_at) VALUES (1, ?, ?)",
            (json.dumps(state, ensure_ascii=False, separators=(",", ":"), allow_nan=False), float(now)),
        )

    def _append_event(self, con, now, kind, actor, payload):
        cur = con.execute(
            "INSERT INTO world_events (created_at, kind, actor, payload_json) VALUES (?, ?, ?, ?)",
            (float(now), kind, actor, json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)),
        )
        return int(cur.lastrowid)

    def _events(self, con, since):
        return _read_events(con, since)

    def _op_seen(self, op_id):
        con = self._connect()
        try:
            row = con.execute("SELECT 1 FROM world_ops WHERE op_id = ?", (op_id,)).fetchone()
            return row is not None
        finally:
            con.close()

    def _backup_path(self, kind, now):
        os.makedirs(self.backup_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
        base = os.path.join(self.backup_dir, f"idle-ops-{kind}-{stamp}")
        target = base + ".sqlite3"
        suffix = 2
        while os.path.exists(target):
            target = f"{base}-{suffix}.sqlite3"
            suffix += 1
        return target

    @staticmethod
    def check_sqlite(path):
        """Return the result of PRAGMA quick_check for one database file."""
        con = sqlite3.connect(f"file:{quote(os.path.abspath(path))}?mode=ro", uri=True, timeout=5.0)
        try:
            return str(con.execute("PRAGMA quick_check").fetchone()[0])
        finally:
            con.close()

    def backup(self, kind="manual", now=None, keep=BACKUP_KEEP):
        """Write a consistent snapshot with SQLite's online backup API.

        The copy is verified before it is reported, so a failed backup never
        silently replaces a good one. ``keep`` prunes older archives of the
        same kind (daily retention); hard-reset/pre-restore snapshots pass 0.
        """
        if kind not in BACKUP_KINDS:
            raise ValueError(f"unknown backup kind: {kind}")
        target = self._backup_path(kind, self._now() if now is None else float(now))
        staging = target + ".partial"
        if os.path.exists(staging):
            os.remove(staging)
        source = self._connect()
        try:
            dest = sqlite3.connect(staging)
            try:
                source.backup(dest)
                # The live DB is WAL; the archive must be a self-contained file.
                dest.execute("PRAGMA journal_mode=DELETE")
            finally:
                dest.close()
        except BaseException:
            if os.path.exists(staging):
                os.remove(staging)
            raise
        finally:
            source.close()
        status = self.check_sqlite(staging)
        if status != "ok":
            os.remove(staging)
            raise sqlite3.DatabaseError(f"backup verification failed: {status}")
        os.replace(staging, target)
        if keep:
            self.prune(keep=keep, kind=kind)
        return target

    def prune(self, keep=BACKUP_KEEP, kind="daily"):
        """Delete the oldest ``idle-ops-<kind>-*`` archives beyond ``keep``."""
        os.makedirs(self.backup_dir, exist_ok=True)
        prefix = f"idle-ops-{kind}-"
        names = sorted(
            name for name in os.listdir(self.backup_dir)
            if name.startswith(prefix) and name.endswith(".sqlite3")
        )
        removed = []
        for name in names[:-keep] if keep > 0 else []:
            path = os.path.join(self.backup_dir, name)
            os.remove(path)
            for suffix in ("-wal", "-shm"):
                side = path + suffix
                if os.path.exists(side):
                    os.remove(side)
            removed.append(path)
        return removed

    def integrity(self):
        con = self._connect()
        try:
            return str(con.execute("PRAGMA quick_check").fetchone()[0])
        finally:
            con.close()

    def acquire_service_lock(self):
        """Hold ``<db>.service.lock`` for the lifetime of the running service.

        ``server.backup restore`` probes this lock and refuses to replace the
        database while the service is up. A ``BEGIN EXCLUSIVE`` probe only sees
        the instant it runs, so it cannot prove the service is stopped.
        """
        if self._service_lock is not None:
            return self._service_lock
        handle = open(self.service_lock_path, "a+b")
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                raise WorldBusy("another idle-ops service instance is running") from exc
        self._service_lock = handle
        return handle

    @staticmethod
    def service_running(path):
        """True when another process holds the service lock for ``path``."""
        lock_path = os.fspath(path) + ".service.lock"
        if fcntl is None or not os.path.exists(lock_path):  # pragma: no cover
            return False
        handle = open(lock_path, "a+b")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return False
        finally:
            handle.close()

    def readiness(self):
        """Report storage health for monitoring.

        ``ok`` is true only when the required ``world`` row is present and
        parseable, the schema is exactly this release's version, and the data
        volume keeps the minimum free space. Sizes, versions and backups stay in
        the returned dict for server-side diagnostics; the HTTP layer exposes
        only the three ``checks`` booleans to callers.
        """
        try:
            con = self._connect()
            try:
                version = int(con.execute("PRAGMA user_version").fetchone()[0])
                row = con.execute("SELECT state_json FROM world WHERE id = 1").fetchone()
                from . import twin
                tables = {entry[0] for entry in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                twin_ok = (twin.REQUIRED_TABLES.keys() | twin.DEVELOPMENT_TABLES.keys()) <= tables
            finally:
                con.close()
            world_ok = False
            if row is not None:
                try:
                    world_ok = isinstance(json.loads(row["state_json"]), dict)
                except (TypeError, ValueError):
                    world_ok = False
            usage = shutil.disk_usage(os.path.dirname(os.path.abspath(self.path)))
            backups = 0
            if os.path.isdir(self.backup_dir):
                backups = sum(1 for name in os.listdir(self.backup_dir) if name.endswith(".sqlite3"))
            schema_ok = version == SCHEMA_VERSION and twin_ok
            disk_ok = usage.free >= MIN_FREE_BYTES
            return {
                "ok": world_ok and schema_ok and disk_ok,
                "world": world_ok,
                "schema": schema_ok,
                "disk": disk_ok,
                "schemaVersion": version,
                "expectedSchemaVersion": SCHEMA_VERSION,
                "dbBytes": os.path.getsize(self.path),
                "freeBytes": usage.free,
                "backupCount": backups,
            }
        except (sqlite3.Error, OSError):
            return {
                "ok": False,
                "world": False,
                "schema": False,
                "disk": False,
                "schemaVersion": None,
                "expectedSchemaVersion": SCHEMA_VERSION,
            }

    def hard_reset(self, now=None, op_id=None, actor=""):
        """Back up the database, then zero the world. Fails closed on backup error.

        Operation ordering (all inside the exclusive write gate):
          1. settle the world to ``now`` and commit it, so the action and its
             audit record start from one exact, persisted state;
          2. take and verify a ``pre-hardreset`` snapshot of that committed
             state — no other world write can slip in between;
          3. advance to ``now`` again (zero elapsed), apply the reset, audit and
             commit.

        A retry with the same opId replays the recorded result without a second
        backup or reset. Concurrent hard resets and ordinary world writes queue
        on the gate; if the queue outlives the deadline the API returns 503.
        """
        with self.gate.exclusive():
            moment = self._now() if now is None else float(now)
            if op_id is not None and self._op_seen(op_id):
                return self._world(
                    moment, action={"type": "hardReset"}, op_id=op_id, actor=actor,
                )
            # Commit the exact state the backup copies and the reset replaces.
            self._world(moment)
            backup = self.backup("pre-hardreset", now=moment, keep=0)
            return self._world(
                moment,
                action={"type": "hardReset", "backup": backup},
                op_id=op_id,
                actor=actor,
            )

    def world(self, now=None, action=None, op_id=None, actor="", since=None, gated=True,
              lock_timeout=WORLD_LOCK_TIMEOUT) -> WorldResult:
        if not gated:
            return self._world(now, action=action, op_id=op_id, actor=actor, since=since)
        with self.gate.shared(timeout=lock_timeout):
            return self._world(now, action=action, op_id=op_id, actor=actor, since=since)

    def _world(self, now=None, action=None, op_id=None, actor="", since=None) -> WorldResult:
        actor = _clean_actor(actor)
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            if now is None:
                # Read the clock only after the write lock is held, so a request
                # that waited on another writer never simulates stale time.
                now = self._now()
            now = float(now)
            duplicate = False
            message = None
            details = None
            if op_id is not None:
                con.execute("DELETE FROM world_ops WHERE created_at < ?", (now - OP_TTL_SECONDS,))
                op_key = _action_key(action)
                cur = con.execute(
                    "INSERT OR IGNORE INTO world_ops (op_id, created_at, action_json) VALUES (?, ?, ?)",
                    (op_id, now, op_key),
                )
                duplicate = cur.rowcount == 0
                if duplicate:
                    stored = con.execute(
                        "SELECT action_json, result_json FROM world_ops WHERE op_id = ?",
                        (op_id,),
                    ).fetchone()
                    if (
                        op_key is not None
                        and stored["action_json"] is not None
                        and stored["action_json"] != op_key
                    ):
                        raise OpConflict("opId already used for a different action")
                    if stored["result_json"]:
                        recorded = json.loads(stored["result_json"])
                        message = recorded.get("message")
                        details = recorded.get("details")

            row = con.execute(
                "SELECT state_json, generation, revision FROM world WHERE id = 1"
            ).fetchone()
            if row is None:
                state = economy.initial_state(now)
                state["last_sim_at"] = float(now)
                generation = 0
                revision = 0
            else:
                state = economy.normalize_state(json.loads(row["state_json"]))
                generation = int(row["generation"] or 0)
                revision = int(row["revision"] or 0)

            created = []
            gains = economy.advance(state, now)
            if gains["level_to"] > gains["level_from"]:
                created.append(self._append_event(
                    con, now, "levelup", actor, {"level": gains["level_to"]},
                ))
            for achievement_id in gains["achievements"]:
                created.append(self._append_event(
                    con, now, "achievement", actor, {"achievement": achievement_id},
                ))

            away = float(gains["seconds"])
            timestamps = None
            if actor:
                seen = con.execute(
                    "SELECT last_seen, last_reward_at, observed_requests, observed_level, "
                    "world_generation FROM admin_seen WHERE username = ?",
                    (actor,),
                ).fetchone()
                previous_seen = seen["last_seen"] if seen else now
                previous_reward = seen["last_reward_at"] if seen else now
                # A corrected (backwards) clock must not make this admin's
                # checkpoints jump back in time or produce a negative absence.
                away = max(0.0, now - previous_reward) if seen else 0.0
                settled_at = max(now, state["last_sim_at"])
                # Checkpoints are display-only observations. Absence is measured
                # as time (awaySeconds); it is never converted into a production
                # diff or extra currency. The console can compare the revision
                # and generation of the last observation with the live world.
                timestamps = {
                    "worldSettledAt": settled_at,
                    "adminLastSeen": previous_seen,
                    "adminLastCheck": max(previous_seen, now),
                    "lastRewardAt": previous_reward,
                    "awaySeconds": away,
                    "observedRequests": float(seen["observed_requests"]) if seen else None,
                    "observedLevel": int(seen["observed_level"]) if seen else None,
                    "observedGeneration": int(seen["world_generation"]) if seen else None,
                    "worldGeneration": generation,
                    "observedFromEarlierWorld": bool(
                        seen and int(seen["world_generation"]) != generation
                    ),
                }

            if action is not None and not duplicate:
                before = None
                if action.get("type") == "hardReset":
                    before = {
                        "requests": state["requests"],
                        "run_lifetime": state["run_lifetime"],
                        "total_lifetime": state["total_lifetime"],
                        "tokens": state["tokens"],
                        "reboots": state["reboots"],
                        "achievements": list(state["achievements"]),
                    }
                details = economy.apply_action(state, action, now)
                if details["type"] == "hardReset":
                    details["backup"] = action.get("backup")
                    generation += 1
                message = details.get("message")
                payload = {
                    k: v for k, v in details.items()
                    if k not in ("message", "backup")
                }
                if payload.get("type") == "buyUpgrade":
                    payload["upgrade"] = payload.pop("id", None)
                created.append(self._append_event(
                    con, now, EVENT_KINDS.get(details["type"], details["type"]), actor, payload,
                ))
                if before is not None:
                    before.update({"op_id": op_id, "backup": details.get("backup")})
                    con.execute(
                        "INSERT INTO audit_log (created_at, actor, action, detail_json) VALUES (?, ?, ?, ?)",
                        (float(now), actor, "hardReset",
                         json.dumps(before, ensure_ascii=False, separators=(",", ":"), allow_nan=False)),
                    )
                for achievement_id in economy.check_achievements(state):
                    if achievement_id not in gains["achievements"]:
                        gains["achievements"].append(achievement_id)
                        created.append(self._append_event(
                            con, now, "achievement", actor, {"achievement": achievement_id},
                        ))
                if op_id is not None:
                    con.execute(
                        "UPDATE world_ops SET result_json = ? WHERE op_id = ?",
                        (json.dumps(
                            {"message": message, "details": details},
                            ensure_ascii=False, separators=(",", ":"), allow_nan=False,
                        ), op_id),
                    )

            if actor:
                # Record what this admin has now observed. Checkpoints stay
                # separate per admin and never pay out; monotonic MAX guards
                # against a corrected (backwards) clock.
                con.execute(
                    "INSERT INTO admin_seen (username, last_seen, last_reward_at, checks, "
                    "observed_requests, observed_level, world_generation) "
                    "VALUES (?, ?, ?, 1, ?, ?, ?) ON CONFLICT(username) DO UPDATE SET "
                    "last_seen = MAX(admin_seen.last_seen, excluded.last_seen), "
                    "last_reward_at = MAX(admin_seen.last_reward_at, excluded.last_reward_at), "
                    "checks = admin_seen.checks + 1, "
                    "observed_requests = excluded.observed_requests, "
                    "observed_level = excluded.observed_level, "
                    "world_generation = excluded.world_generation",
                    (
                        actor, now, now,
                        float(state["requests"]), int(economy.derived(state)["level"]),
                        generation,
                    ),
                )

            state["last_seen"] = float(now)
            revision += 1
            con.execute(
                "INSERT INTO world (id, state_json, updated_at, generation, revision) "
                "VALUES (1, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "state_json = excluded.state_json, updated_at = excluded.updated_at, "
                "generation = excluded.generation, revision = excluded.revision",
                (
                    json.dumps(state, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                    float(now), int(generation), int(revision),
                ),
            )
            if created:
                con.execute(
                    "DELETE FROM world_events WHERE id <= (SELECT MAX(id) FROM world_events) - ?",
                    (EVENT_RETENTION,),
                )
            cursor_row = con.execute("SELECT MAX(id) AS cursor FROM world_events").fetchone()
            max_cursor = int(cursor_row["cursor"] or 0)
            if since is None:
                since = max(0, (created[0] if created else max_cursor + 1) - 1)
            events, cursor, cursor_reset, gap = self._events(con, int(since))
            con.execute("COMMIT")
            self.generation += 1
            return WorldResult(
                state=state,
                gains=gains,
                events=events,
                cursor=cursor,
                message=message,
                details=details,
                duplicate=duplicate,
                now=now,
                away=away,
                timestamps=timestamps,
                cursor_reset=cursor_reset,
                event_gap=gap,
                world_generation=generation,
                revision=revision,
            )
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()


class SnapshotCache:
    """Read-only projection of the world for public viewers.

    Public GETs must never write SQLite: the persisted state is opened read-only,
    copied, advanced in memory to "now", and shared for a short TTL. A single
    lock gives single-flight refresh, so a hundred viewers still cause at most
    one state read per TTL window. Committed admin writes bump ``generation``
    and invalidate the snapshot immediately.
    """

    def __init__(self, store, ttl=1.0):
        self.store = store
        self.ttl = float(ttl)
        self._lock = threading.Lock()
        self._base = None
        self._at = 0.0
        self._generation = -1

    def _ro_connect(self):
        if not os.path.exists(self.store.path):
            raise WorldUnavailable("world database is not available")
        uri = "file:%s?mode=ro" % quote(os.path.abspath(self.store.path))
        try:
            return sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
        except sqlite3.OperationalError:
            # Read-only WAL access can fail if the -shm file is unavailable;
            # fall back to a normal connection that is forbidden to write.
            con = sqlite3.connect(self.store.path, timeout=5.0, isolation_level=None)
            con.execute("PRAGMA query_only = ON")
            return con

    def read_world(self):
        """Read the state, generation and newest event id from one snapshot.

        BEGIN (deferred) pins one WAL read snapshot, so the returned revision
        and event ceiling always belong together.
        """
        con = self._ro_connect()
        try:
            con.execute("BEGIN")
            row = con.execute(
                "SELECT state_json, generation, revision FROM world WHERE id = 1"
            ).fetchone()
            cursor_row = con.execute("SELECT MAX(id) FROM world_events").fetchone()
            con.execute("COMMIT")
        except sqlite3.Error as exc:
            raise WorldUnavailable(f"world read failed: {exc}") from exc
        finally:
            con.close()
        if row is None:
            raise WorldUnavailable("world row is missing")
        return {
            "state": economy.normalize_state(json.loads(row[0])),
            "generation": int(row[1] or 0),
            "revision": int(row[2] or 0),
            "event_cursor": int(cursor_row[0] or 0),
        }

    def read_events(self, since, until=None):
        con = self._ro_connect()
        try:
            con.execute("BEGIN")
            events, cursor, cursor_reset, gap = _read_events(con, since, until)
            con.execute("COMMIT")
            return events, cursor, cursor_reset, gap
        except sqlite3.Error as exc:
            raise WorldUnavailable(f"event read failed: {exc}") from exc
        finally:
            con.close()

    def _refresh(self, now):
        world = self.read_world()
        gains = economy.advance(world["state"], now)
        return {
            "state": world["state"],
            "gains": gains,
            "now": float(now),
            "generation": world["generation"],
            "revision": world["revision"],
            "event_cursor": world["event_cursor"],
        }

    def latest(self, since=None):
        stale = False
        with self._lock:
            now = time.time()
            fresh = (
                self._base is not None
                and self._generation == self.store.generation
                and (time.monotonic() - self._at) < self.ttl
            )
            if not fresh:
                try:
                    self._base = self._refresh(now)
                    self._at = time.monotonic()
                    self._generation = self.store.generation
                except WorldUnavailable:
                    # Never invent a fresh world from a failed read: either keep
                    # serving the last good snapshot or surface the error.
                    if self._base is None:
                        raise
                    stale = True
            base = self._base
        events, cursor, cursor_reset, gap = self.read_events(since, base["event_cursor"])
        return WorldResult(
            state=base["state"],
            gains=base["gains"],
            events=events,
            cursor=cursor,
            now=base["now"],
            cursor_reset=cursor_reset,
            event_gap=gap,
            world_generation=base["generation"],
            revision=base["revision"],
            stale=stale,
        )
