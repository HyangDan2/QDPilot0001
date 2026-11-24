"""SQLite persistence for the shared Idle Ops world.

Every read or action advances the single world to "now" inside one
BEGIN IMMEDIATE transaction, so concurrent tabs and the public viewer cannot
double-spend requests. Only the admin console applies actions; the public
viewer reads state and presentation events.

The legacy per-player table (`players`) is migrated once, from the row with
the highest lifetime total, and then preserved untouched.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass

from . import economy

OP_TTL_SECONDS = 30 * 86400
EVENT_RETENTION = 400
EVENT_LIMIT = 60
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

    @property
    def away_seconds(self):
        return float(self.gains.get("seconds") or 0.0)


def _clean_actor(actor):
    if not isinstance(actor, str):
        return ""
    return "".join(ch for ch in actor if ch.isprintable())[:64]


class Store:
    def __init__(self, path, backup_dir=None):
        self.path = os.fspath(path)
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        self.backup_dir = os.fspath(
            backup_dir or os.environ.get("IDLE_OPS_BACKUP_DIR") or os.path.join(parent, "backups")
        )
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS world (
                    id         INTEGER PRIMARY KEY CHECK (id = 1),
                    state_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS world_ops (
                    op_id      TEXT PRIMARY KEY,
                    created_at REAL NOT NULL
                ) WITHOUT ROWID;
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
                CREATE TABLE IF NOT EXISTS audit_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at  REAL NOT NULL,
                    actor       TEXT NOT NULL,
                    action      TEXT NOT NULL,
                    detail_json TEXT NOT NULL
                );
                """
            )
            con.execute("DELETE FROM action_ops WHERE created_at < ?", (self._now() - OP_TTL_SECONDS,))
            con.execute("DELETE FROM world_ops WHERE created_at < ?", (self._now() - OP_TTL_SECONDS,))
            self._seed_world(con, self._now())

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
            (json.dumps(state, ensure_ascii=False, separators=(",", ":")), float(now)),
        )

    def _append_event(self, con, now, kind, actor, payload):
        cur = con.execute(
            "INSERT INTO world_events (created_at, kind, actor, payload_json) VALUES (?, ?, ?, ?)",
            (float(now), kind, actor, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        )
        return int(cur.lastrowid)

    def _events(self, con, since):
        rows = con.execute(
            "SELECT id, created_at, kind, payload_json FROM world_events "
            "WHERE id > ? ORDER BY id ASC LIMIT ?",
            (int(since), EVENT_LIMIT),
        ).fetchall()
        events = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload.update({"id": int(row["id"]), "kind": row["kind"], "at": float(row["created_at"])})
            events.append(payload)
        cursor = events[-1]["id"] if events else int(since)
        return events, cursor

    def _op_seen(self, op_id):
        con = self._connect()
        try:
            row = con.execute("SELECT 1 FROM world_ops WHERE op_id = ?", (op_id,)).fetchone()
            return row is not None
        finally:
            con.close()

    def _backup(self, now):
        os.makedirs(self.backup_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
        base = os.path.join(self.backup_dir, f"idle-ops-pre-hardreset-{stamp}")
        target = base + ".sqlite3"
        suffix = 2
        while os.path.exists(target):
            target = f"{base}-{suffix}.sqlite3"
            suffix += 1
        source = self._connect()
        try:
            dest = sqlite3.connect(target)
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
        check = sqlite3.connect(target)
        try:
            status = check.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            check.close()
        if status != "ok":
            raise sqlite3.DatabaseError(f"backup verification failed: {status}")
        return target

    def hard_reset(self, now=None, op_id=None, actor=""):
        """Back up the database, then zero the world. Fails closed on backup error."""
        if self._op_seen(op_id):
            return self.world(now, action={"type": "hardReset"}, op_id=op_id, actor=actor)
        backup = self._backup(self._now() if now is None else now)
        return self.world(
            now,
            action={"type": "hardReset", "backup": backup},
            op_id=op_id,
            actor=actor,
        )

    def world(self, now=None, action=None, op_id=None, actor="", since=None) -> WorldResult:
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
            if op_id is not None:
                cur = con.execute(
                    "INSERT OR IGNORE INTO world_ops (op_id, created_at) VALUES (?, ?)",
                    (op_id, float(now)),
                )
                duplicate = cur.rowcount == 0

            row = con.execute("SELECT state_json FROM world WHERE id = 1").fetchone()
            if row is None:
                state = economy.initial_state(now)
                state["last_sim_at"] = float(now)
            else:
                state = economy.normalize_state(json.loads(row["state_json"]))

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

            message = None
            details = None
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
                         json.dumps(before, ensure_ascii=False, separators=(",", ":"))),
                    )
                for achievement_id in economy.check_achievements(state):
                    if achievement_id not in gains["achievements"]:
                        gains["achievements"].append(achievement_id)
                        created.append(self._append_event(
                            con, now, "achievement", actor, {"achievement": achievement_id},
                        ))

            state["last_seen"] = float(now)
            con.execute(
                "INSERT INTO world (id, state_json, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at",
                (json.dumps(state, ensure_ascii=False, separators=(",", ":")), float(now)),
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
            events, cursor = self._events(con, int(since))
            con.execute("COMMIT")
            return WorldResult(
                state=state,
                gains=gains,
                events=events,
                cursor=cursor,
                message=message,
                details=details,
                duplicate=duplicate,
                now=now,
            )
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
