"""SQLite persistence with lazy server-side simulation.

Every read or action advances the stored state to "now" inside a single
BEGIN IMMEDIATE transaction, so multiple tabs cannot double-spend requests.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field

from . import economy

OP_TTL_SECONDS = 30 * 86400


@dataclass
class SessionResult:
    state: dict
    gains: dict
    message: str | None = None
    details: dict | None = None
    away_seconds: float = 0.0
    duplicate: bool = False
    achievements: list[str] = field(default_factory=list)


class Store:
    def __init__(self, path):
        self.path = os.fspath(path)
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        with self._connect() as con:
            con.executescript(
                """
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
                """
            )
            con.execute("DELETE FROM action_ops WHERE created_at < ?", (self._now() - OP_TTL_SECONDS,))

    @staticmethod
    def _now():
        import time
        return time.time()

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def session(self, username, now, action=None, op_id=None) -> SessionResult:
        if not isinstance(username, str) or not username or len(username) > 128:
            raise ValueError("invalid username")
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            duplicate = False
            if op_id is not None:
                cur = con.execute(
                    "INSERT OR IGNORE INTO action_ops (username, op_id, created_at) VALUES (?, ?, ?)",
                    (username, op_id, float(now)),
                )
                duplicate = cur.rowcount == 0

            row = con.execute(
                "SELECT state_json FROM players WHERE username = ?", (username,)
            ).fetchone()
            if row is None:
                state = economy.initial_state(now)
                previous_seen = float(now)
            else:
                state = economy.normalize_state(json.loads(row["state_json"]))
                previous_seen = state["last_seen"]

            gains = economy.advance(state, now)
            message = None
            details = None
            if action is not None and not duplicate:
                details = economy.apply_action(state, action)
                message = details.get("message")
                extra = economy.check_achievements(state)
                for achievement_id in extra:
                    if achievement_id not in gains["achievements"]:
                        gains["achievements"].append(achievement_id)

            state["last_seen"] = float(now)
            con.execute(
                "INSERT INTO players (username, state_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(username) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at",
                (username, json.dumps(state, ensure_ascii=False, separators=(",", ":")), float(now)),
            )
            con.execute("COMMIT")
            return SessionResult(
                state=state,
                gains=gains,
                message=message,
                details=details,
                away_seconds=max(0.0, float(now) - previous_seen),
                duplicate=duplicate,
                achievements=gains["achievements"],
            )
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
