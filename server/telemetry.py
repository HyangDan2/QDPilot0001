"""Versioned, repo-scoped agent observations stored in the shared SQLite DB."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import re


VERSION = 1
HEARTBEAT_SECONDS = 90
MAX_EVENT_AGE = 24 * 60 * 60
EVENT_RETENTION = 2 * MAX_EVENT_AGE
FUTURE_SKEW = 60
MAX_INT = (1 << 53) - 1
STATUSES = frozenset({"coding", "testing", "blocked", "waiting", "completed", "failed"})
TERMINAL = frozenset({"completed", "failed"})
EVENT_TYPES = STATUSES | {"heartbeat"}
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,96}")
TOKEN = re.compile(r"[A-Za-z0-9_-]{32,256}")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", re.ASCII)
REQUIRED_FIELDS = frozenset({
    "version", "event_id", "agent_id", "repository_id", "run_id", "task_id",
    "event_type", "timestamp", "sequence",
})
OPTIONAL_FIELDS = frozenset({"progress", "issue_id"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS r3_telemetry_deliveries (
    agent_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    received_at REAL NOT NULL,
    PRIMARY KEY (agent_id, event_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS r3_telemetry_deliveries_age
    ON r3_telemetry_deliveries(received_at);
CREATE TABLE IF NOT EXISTS r3_telemetry_runs (
    agent_id TEXT NOT NULL,
    repository_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    started_at REAL NOT NULL,
    event_at REAL NOT NULL,
    observed_at REAL NOT NULL,
    status TEXT NOT NULL,
    progress REAL,
    issue_id INTEGER,
    PRIMARY KEY (agent_id, repository_id, run_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS r3_telemetry_latest_run
    ON r3_telemetry_runs(agent_id, repository_id, started_at DESC, run_id DESC);
CREATE INDEX IF NOT EXISTS r3_telemetry_observation_age
    ON r3_telemetry_runs(observed_at);
"""

REQUIRED_TABLES = {
    "r3_telemetry_deliveries": {"agent_id", "event_id", "digest", "received_at"},
    "r3_telemetry_runs": {
        "agent_id", "repository_id", "run_id", "task_id", "sequence", "started_at", "event_at",
        "observed_at", "status", "progress", "issue_id",
    },
}


@dataclass(frozen=True)
class Scope:
    agent_id: str
    repositories: frozenset


class TelemetryError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _positive_int(value):
    return type(value) is int and 0 < value <= MAX_INT


def _identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value)


def load_tokens(raw):
    """Load a server-only mapping of SHA-256 token digests to agent scopes."""
    if not raw:
        return {}
    try:
        config = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError("invalid telemetry credential configuration") from None
    if not isinstance(config, dict):
        raise ValueError("invalid telemetry credential configuration")
    tokens = {}
    for agent_id, details in config.items():
        if not _identifier(agent_id) or not isinstance(details, dict):
            raise ValueError("invalid telemetry credential configuration")
        digest = details.get("sha256")
        repositories = details.get("repository_ids")
        if (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or digest in tokens or not isinstance(repositories, list)
                or not repositories or not all(map(_positive_int, repositories))):
            raise ValueError("invalid telemetry credential configuration")
        tokens[digest] = Scope(agent_id, frozenset(repositories))
    return tokens


def authenticate(header, tokens):
    if isinstance(header, str) and header[:7].lower() == "bearer ":
        token = header[7:]
        if TOKEN.fullmatch(token):
            digest = hashlib.sha256(token.encode("ascii")).hexdigest()
            scope = None
            for expected, candidate in tokens.items():
                if hmac.compare_digest(digest, expected):
                    scope = candidate
            if scope is not None:
                return scope
    raise TelemetryError("invalid telemetry credential", 401)


def _parse(payload, scope):
    if (not isinstance(payload, dict) or not REQUIRED_FIELDS <= payload.keys()
            or payload.keys() - REQUIRED_FIELDS - OPTIONAL_FIELDS
            or type(payload["version"]) is not int or payload["version"] != VERSION):
        raise TelemetryError("invalid telemetry v1 envelope")
    if not all(_identifier(payload[key]) for key in ("event_id", "agent_id", "run_id", "task_id")):
        raise TelemetryError("invalid telemetry identifier")
    repository_id = payload["repository_id"]
    if not _positive_int(repository_id) or not _positive_int(payload["sequence"]):
        raise TelemetryError("invalid repository_id or sequence")
    if payload["agent_id"] != scope.agent_id or repository_id not in scope.repositories:
        raise TelemetryError("agent or repository outside credential scope", 403)
    event_type = payload["event_type"]
    if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
        raise TelemetryError("invalid telemetry event_type")
    progress = payload.get("progress")
    if "progress" in payload and (type(progress) not in (int, float) or not 0 <= progress <= 100):
        raise TelemetryError("progress must be a finite number from 0 to 100")
    if "issue_id" in payload and not _positive_int(payload["issue_id"]):
        raise TelemetryError("issue_id must be a positive integer")
    if event_type == "heartbeat" and OPTIONAL_FIELDS & payload.keys():
        raise TelemetryError("heartbeat cannot change progress or issue association")
    timestamp = payload["timestamp"]
    try:
        if not isinstance(timestamp, str) or not TIMESTAMP.fullmatch(timestamp):
            raise ValueError
        return datetime.fromisoformat(timestamp[:-1] + "+00:00").timestamp()
    except (ValueError, OverflowError, OSError):
        raise TelemetryError("timestamp must be RFC 3339 in UTC with a Z suffix") from None


def ingest(con, payload, scope, now):
    """Apply one observation inside the caller's immediate write transaction."""
    event_at = _parse(payload, scope)
    agent_id, project_id = scope.agent_id, payload["repository_id"]
    event_id, run_id = payload["event_id"], payload["run_id"]
    task_id, sequence = payload["task_id"], payload["sequence"]
    event_type = payload["event_type"]
    identity = (agent_id, project_id)
    result = {"accepted": True, "duplicate": False, "changed": False, "updated": False,
              "agentId": agent_id, "projectId": project_id}
    digest = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    previous = con.execute(
        "SELECT digest FROM r3_telemetry_deliveries WHERE agent_id=? AND event_id=?",
        (agent_id, event_id),
    ).fetchone()
    if previous:
        if previous[0] != digest:
            raise TelemetryError("event_id already used for different telemetry", 409)
        return dict(result, duplicate=True)
    if now - event_at > MAX_EVENT_AGE or event_at - now > FUTURE_SKEW:
        raise TelemetryError("timestamp outside telemetry acceptance window")

    prior = con.execute(
        """SELECT task_id, sequence, event_at, observed_at, status, progress, issue_id
           FROM r3_telemetry_runs WHERE agent_id=? AND repository_id=? AND run_id=?""",
        (*identity, run_id),
    ).fetchone()
    if prior:
        if prior[0] != task_id:
            raise TelemetryError("task_id cannot change during a run", 409)
        if sequence <= prior[1] or event_at < prior[2]:
            raise TelemetryError("telemetry sequence or timestamp moved backwards", 409)
        if prior[4] in TERMINAL:
            raise TelemetryError("run already ended", 409)
    elif sequence != 1 or event_type == "heartbeat":
        raise TelemetryError("start a run with a status event at sequence 1", 409)

    con.execute(
        "INSERT INTO r3_telemetry_deliveries VALUES (?, ?, ?, ?)",
        (agent_id, event_id, digest, now),
    )
    status, progress, issue_id = (prior[4], prior[5], prior[6]) if event_type == "heartbeat" else (
        event_type, payload.get("progress"), payload.get("issue_id"),
    )
    observed_at = max(prior[3], min(now, event_at)) if prior else min(now, event_at)
    con.execute(
        """INSERT INTO r3_telemetry_runs
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(agent_id, repository_id, run_id) DO UPDATE SET
             sequence=excluded.sequence, event_at=excluded.event_at, observed_at=excluded.observed_at,
             status=excluded.status, progress=excluded.progress, issue_id=excluded.issue_id""",
        (*identity, run_id, task_id, sequence, event_at, event_at, observed_at, status, progress, issue_id),
    )
    latest = con.execute(
        """SELECT run_id FROM r3_telemetry_runs WHERE agent_id=? AND repository_id=?
           ORDER BY started_at DESC, run_id DESC LIMIT 1""",
        identity,
    ).fetchone()[0]
    con.execute("DELETE FROM r3_telemetry_deliveries WHERE received_at < ?", (now - EVENT_RETENTION,))
    con.execute(
        """DELETE FROM r3_telemetry_runs
           WHERE observed_at < ?
             AND EXISTS (
                SELECT 1 FROM r3_telemetry_runs newer
                WHERE newer.agent_id = r3_telemetry_runs.agent_id
                  AND newer.repository_id = r3_telemetry_runs.repository_id
                  AND (newer.started_at, newer.run_id) > (r3_telemetry_runs.started_at, r3_telemetry_runs.run_id)
             )""",
        (now - EVENT_RETENTION,),
    )
    revived = (event_type == "heartbeat" and now - prior[3] >= HEARTBEAT_SECONDS
               and now - observed_at < HEARTBEAT_SECONDS)
    updated = latest == run_id
    changed = updated and (event_type != "heartbeat" or revived)
    return dict(result, changed=changed, updated=updated)


def projection(con, now):
    """Return the latest run per scoped agent and repository.

    Raw agent/task identifiers are for the admin layer. Public output must
    replace the agent ID with an opaque server-generated alias.
    """
    rows = con.execute(
        """SELECT latest.agent_id, latest.repository_id, latest.status,
                  latest.progress, latest.observed_at, latest.task_id, latest.issue_id
           FROM r3_telemetry_runs latest
           WHERE NOT EXISTS (
             SELECT 1 FROM r3_telemetry_runs newer
             WHERE newer.agent_id = latest.agent_id
               AND newer.repository_id = latest.repository_id
               AND (newer.started_at, newer.run_id) > (latest.started_at, latest.run_id)
           )
           ORDER BY latest.repository_id, latest.agent_id"""
    )
    agents = []
    for agent_id, project_id, status, progress, observed_at, task_id, issue_id in rows:
        stale = status not in TERMINAL and now - observed_at >= HEARTBEAT_SECONDS
        agents.append({
            "id": agent_id,
            "projectId": project_id,
            "status": "offline" if stale else status,
            "progress": progress,
            "observedAt": observed_at,
            "taskId": task_id,
            "issueId": issue_id,
            "lastStatus": status,
            "stale": stale,
        })
    return agents
