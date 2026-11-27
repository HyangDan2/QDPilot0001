"""SQLite projections for the Development Digital Twin."""
from contextlib import contextmanager, closing
import hashlib
import hmac
import json
import re
import time

from . import telemetry

XP_PER_COMMIT = 10
REBOOT_COMMITS = 100
REBOOT_STEP = 1000
MAX_BONUS = 20000
STALE_SECONDS = 600
MAX_PROJECTS = 40
MAX_BUGS = 24
EVENT_LIMIT = 60
EVENT_RETENTION = 1200

SCHEMA = """
CREATE TABLE IF NOT EXISTS twin_clock (
    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
    public_revision INTEGER NOT NULL, public_cursor INTEGER NOT NULL, alias_key BLOB NOT NULL
);
INSERT OR IGNORE INTO twin_clock VALUES(1,0,0,0,randomblob(32));
CREATE TABLE IF NOT EXISTS twin_projects (
    repo INTEGER PRIMARY KEY, baseline_at REAL, observed_at REAL,
    public_verified INTEGER NOT NULL DEFAULT 0,
    xp INTEGER NOT NULL DEFAULT 0, run_commits INTEGER NOT NULL DEFAULT 0,
    reboots INTEGER NOT NULL DEFAULT 0, commit_count INTEGER NOT NULL DEFAULT 0,
    requested INTEGER NOT NULL DEFAULT 1, served INTEGER NOT NULL DEFAULT 0,
    retry_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS twin_refs (
    repo INTEGER NOT NULL, ref TEXT NOT NULL, sha TEXT NOT NULL,
    PRIMARY KEY(repo, ref)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS twin_commits (
    repo INTEGER NOT NULL, sha TEXT NOT NULL, xp INTEGER NOT NULL,
    PRIMARY KEY(repo, sha)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS twin_signals (
    repo INTEGER NOT NULL, key TEXT NOT NULL, kind TEXT NOT NULL,
    status TEXT NOT NULL, updated REAL NOT NULL, ordinal INTEGER NOT NULL,
    PRIMARY KEY(repo, key)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS twin_signals_kind ON twin_signals(repo,kind,status,updated);
CREATE TABLE IF NOT EXISTS twin_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, repo INTEGER NOT NULL, at REAL NOT NULL,
    kind TEXT NOT NULL, entity TEXT, agent TEXT, level INTEGER, tier TEXT,
    public_id INTEGER UNIQUE
);
CREATE TABLE IF NOT EXISTS twin_operations (
    op_id TEXT PRIMARY KEY, repo INTEGER NOT NULL, actor TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS twin_deliveries (
    delivery TEXT PRIMARY KEY, repo INTEGER NOT NULL, received REAL NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS twin_deliveries_received ON twin_deliveries(received);
"""

REQUIRED_TABLES = {
    **telemetry.REQUIRED_TABLES,
    "twin_clock": {"id", "revision", "public_revision", "public_cursor", "alias_key"},
    "twin_projects": {"repo", "baseline_at", "observed_at", "xp", "public_verified"},
    "twin_refs": {"repo", "ref", "sha"},
    "twin_commits": {"repo", "sha", "xp"},
    "twin_signals": {"repo", "key", "kind", "status", "updated", "ordinal"},
    "twin_events": {"id", "repo", "kind", "public_id"},
    "twin_operations": {"op_id", "repo", "actor"},
    "twin_deliveries": {"delivery", "repo", "received"},
}

REPO_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", re.ASCII)
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,159}", re.ASCII)


class Conflict(ValueError):
    pass


def opaque(prefix, *parts):
    digest = hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()
    return prefix + "-" + digest[:20]


def agent_alias(key, repo, agent):
    identity = json.dumps([repo, agent], separators=(",", ":")).encode()
    return "agent-" + hmac.new(key, identity, hashlib.sha256).hexdigest()[:20]


def registry(raw):
    values = json.loads(raw) if raw else []
    if not isinstance(values, list) or len(values) > MAX_PROJECTS:
        raise ValueError("invalid repository registry")
    result = {}
    for value in values:
        if not isinstance(value, dict) or set(value) - {"id", "repo", "name", "refs", "publish"}:
            raise ValueError("invalid repository entry")
        identifier, repo = value.get("id"), value.get("repo")
        name, refs, publish = value.get("name"), value.get("refs"), value.get("publish", False)
        if type(identifier) is not int or not 0 < identifier < 2**63 or identifier in result:
            raise ValueError("invalid or duplicate repository id")
        if not isinstance(repo, str) or not REPO_NAME.fullmatch(repo) or any(
            part in (".", "..") for part in repo.split("/")
        ):
            raise ValueError("invalid GitHub repository name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or not name.isprintable():
            raise ValueError("invalid display name")
        if not isinstance(refs, list) or not 1 <= len(refs) <= 8 or not all(
            isinstance(ref, str) and REF.fullmatch(ref) and ".." not in ref
            and not ref.endswith(("/", ".", ".lock")) and "//" not in ref for ref in refs
        ) or len(set(refs)) != len(refs):
            raise ValueError("invalid tracked branches")
        if type(publish) is not bool:
            raise ValueError("invalid repository publication flag")
        result[identifier] = {"id": opaque("project", identifier), "repo": repo,
                              "name": name.strip(), "refs": tuple(refs), "publish": publish}
    return result


def level_tier(xp):
    level = 1 + xp // 100
    return level, next(tier for threshold, tier in (
        (40, "quantum"), (20, "gpu"), (10, "db"), (5, "app"), (1, "cache")
    ) if level >= threshold)


def bonus(reboots):
    return min(MAX_BONUS, 10000 + reboots * REBOOT_STEP)


def revise(con, public):
    con.execute("UPDATE twin_clock SET revision=revision+1,public_revision=public_revision+? WHERE id=1",
                (int(public),))


def emit(con, repo, now, kind, entity=None, agent=None, level=None, tier=None):
    public = con.execute("SELECT public_verified FROM twin_projects WHERE repo=?", (repo,)).fetchone()[0]
    public_id = None
    if public:
        con.execute("UPDATE twin_clock SET public_cursor=public_cursor+1 WHERE id=1")
        public_id = con.execute("SELECT public_cursor FROM twin_clock WHERE id=1").fetchone()[0]
    con.execute("INSERT INTO twin_events(repo,at,kind,entity,agent,level,tier,public_id) VALUES(?,?,?,?,?,?,?,?)",
                (repo, now, kind, entity, agent, level, tier, public_id))


def prune(con):
    con.execute("DELETE FROM twin_events WHERE id <= "
                "(SELECT COALESCE(MAX(id),0)-? FROM twin_events) AND public_id IS NULL", (EVENT_RETENTION,))
    con.execute("DELETE FROM twin_events WHERE public_id <= "
                "(SELECT public_cursor-? FROM twin_clock WHERE id=1)", (EVENT_RETENTION,))


def signal(con, repo, item, now, animate):
    key, kind, status, stamp, ordinal = item
    old = con.execute("SELECT status,updated,ordinal FROM twin_signals WHERE repo=? AND key=?",
                      (repo, key)).fetchone()
    # Source timestamps come from GitHub. A stable id or lifecycle priority
    # breaks ties when timestamps only have whole-second precision.
    if old:
        if (stamp, ordinal) < (old["updated"], old["ordinal"]):
            return
        if (stamp, ordinal, status) == (old["updated"], old["ordinal"], old["status"]):
            return
    con.execute("""
        INSERT INTO twin_signals VALUES(?,?,?,?,?,?)
        ON CONFLICT(repo,key) DO UPDATE SET status=excluded.status,
            updated=excluded.updated,ordinal=excluded.ordinal
    """, (repo, key, kind, status, stamp, ordinal))
    prior = old["status"] if old else None
    if animate and kind in ("issue", "ci"):
        entity = opaque("bug", repo, key)
        if status == "open" and prior != "open":
            emit(con, repo, now, "bug_opened", entity)
        elif prior == "open" and status in ("resolved", "dismissed"):
            emit(con, repo, now, "bug_" + status, entity)
    elif animate and kind in ("pull", "release") and (kind == "pull" or prior is None):
        emit(con, repo, now, kind)


class Twin:
    def __init__(self, store, projects, tokens=None):
        self.store = store
        self.projects = projects
        self.tokens = tokens or {}
        self.by_id = {item["id"]: repo for repo, item in projects.items()}
        with self.transaction(write=True) as con:
            for repo, config in projects.items():
                con.execute("INSERT OR IGNORE INTO twin_projects(repo) VALUES(?)", (repo,))
                if not config["publish"]:
                    hidden = con.execute(
                        "UPDATE twin_projects SET public_verified=0 WHERE repo=? AND public_verified=1", (repo,))
                    if hidden.rowcount:
                        revise(con, True)

    def hide(self, repo):
        with self.transaction(write=True) as con:
            result = con.execute("UPDATE twin_projects SET public_verified=0 WHERE repo=? AND public_verified=1",
                                 (repo,))
            if result.rowcount:
                revise(con, True)

    @contextmanager
    def transaction(self, write=False):
        with closing(self.store._connect()) as con:
            con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with con:
                yield con
                if write:
                    prune(con)

    def project_repo(self, project_id):
        if not isinstance(project_id, str) or project_id not in self.by_id:
            raise ValueError("unknown project")
        return self.by_id[project_id]

    def integrations(self):
        with self.transaction() as con:
            rows = []
            now = time.time()
            for repo, config in self.projects.items():
                row = con.execute("SELECT * FROM twin_projects WHERE repo=?", (repo,)).fetchone()
                rows.append({"id": config["id"], "name": config["name"], "repository": config["repo"],
                             "public": bool(config["publish"] and row["public_verified"]),
                             "trackedRefs": config["refs"], "observedAt": row["observed_at"],
                             "stale": not row["observed_at"] or now - row["observed_at"] > STALE_SECONDS,
                             "pending": row["requested"] > row["served"],
                             "retryAt": row["retry_at"] or None})
        agents = [{"id": scope.agent_id, "projectIds": [self.projects[r]["id"] for r in scope.repositories
                                                       if r in self.projects]}
                  for scope in self.tokens.values()]
        return {"projects": rows, "agents": agents, "collectorConfigured": bool(rows)}

    def enqueue(self, project_id):
        repo = self.project_repo(project_id)
        with self.transaction(write=True) as con:
            con.execute("UPDATE twin_projects SET requested=requested+1 WHERE repo=?", (repo,))

    def reboot(self, project_id, op_id, actor, since=None, now=None):
        repo = self.project_repo(project_id)
        now = time.time() if now is None else now
        with self.transaction(write=True) as con:
            previous = con.execute("SELECT repo,actor FROM twin_operations WHERE op_id=?", (op_id,)).fetchone()
            if previous:
                if (previous["repo"], previous["actor"]) != (repo, actor):
                    raise Conflict("opId already used")
            else:
                updated = con.execute("""
                    UPDATE twin_projects SET reboots=reboots+1,run_commits=0
                    WHERE repo=? AND run_commits>=?
                """, (repo, REBOOT_COMMITS))
                if not updated.rowcount:
                    raise Conflict("100 new eligible commits are required for reboot")
                con.execute("INSERT INTO twin_operations VALUES(?,?,?)", (op_id, repo, actor))
                emit(con, repo, now, "reboot")
                public = con.execute("SELECT public_verified FROM twin_projects WHERE repo=?", (repo,)).fetchone()[0]
                revise(con, public)
            snapshot = self._snapshot(con, False, since, now)
            snapshot["duplicate"] = bool(previous)
            return snapshot

    def ingest_agent(self, payload, scope, now=None):
        now = time.time() if now is None else now
        if type(payload.get("repository_id")) is not int or payload["repository_id"] not in self.projects:
            raise PermissionError("repository is outside the registry")
        with self.transaction(write=True) as con:
            result = telemetry.ingest(con, payload, scope, now)
            repo = payload["repository_id"]
            if result["updated"]:
                public = con.execute("SELECT public_verified FROM twin_projects WHERE repo=?", (repo,)).fetchone()[0]
                revise(con, public and self.projects[repo]["publish"])
            if result["changed"]:
                emit(con, repo, now, "agent", agent=scope.agent_id)
            return {key: result[key] for key in ("accepted", "duplicate")}

    def snapshot(self, public=False, since=None, now=None):
        with self.transaction() as con:
            return self._snapshot(con, public, since, time.time() if now is None else now)

    def _snapshot(self, con, public, since, now):
        projects = []
        visible = {}
        freshness = []
        for repo, config in self.projects.items():
            row = con.execute("SELECT * FROM twin_projects WHERE repo=?", (repo,)).fetchone()
            if public and not (config["publish"] and row["public_verified"]):
                continue
            visible[repo] = config["id"]
            level, tier = level_tier(row["xp"])
            stats = {"commits": row["commit_count"], "pulls": 0, "issues": 0, "ci": 0, "releases": 0}
            names = {"issue": "issues", "pull": "pulls", "ci": "ci", "release": "releases"}
            for count in con.execute("SELECT kind,count(*) AS n FROM twin_signals WHERE repo=? GROUP BY kind",
                                     (repo,)):
                stats[names[count["kind"]]] = count["n"]
            bugs = [{"id": opaque("bug", repo, bug["key"]), "kind": bug["kind"]} for bug in con.execute(
                "SELECT key,kind FROM twin_signals WHERE repo=? AND status='open' "
                "AND kind IN ('issue','ci') ORDER BY updated DESC,key LIMIT ?", (repo, MAX_BUGS))]
            bug_count = con.execute("SELECT count(*) FROM twin_signals WHERE repo=? AND status='open' "
                                    "AND kind IN ('issue','ci')", (repo,)).fetchone()[0]
            observed = row["observed_at"]
            if observed is not None:
                freshness.append(observed)
            projects.append({
                "id": config["id"], "name": config["name"], "level": level, "tier": tier, "xp": row["xp"],
                "runCommits": row["run_commits"], "bonusBps": bonus(row["reboots"]), "reboots": row["reboots"],
                "rebootEligible": row["run_commits"] >= REBOOT_COMMITS, "rebootRequired": REBOOT_COMMITS,
                "stats": stats, "bugs": bugs, "bugCount": bug_count, "observedAt": observed,
                "stale": observed is None or now - observed > STALE_SECONDS,
            })
        clock = con.execute("SELECT * FROM twin_clock WHERE id=1").fetchone()
        aliases = clock["alias_key"]
        enabled = {(scope.agent_id, repo) for scope in self.tokens.values() for repo in scope.repositories}
        agents = []
        for agent in telemetry.projection(con, now):
            repo = agent["projectId"]
            if repo not in visible or (agent["id"], repo) not in enabled:
                continue
            current = {k: agent.get(k) for k in ("status", "progress", "observedAt", "stale", "lastStatus")}
            current.update(id=agent_alias(aliases, repo, agent["id"]), projectId=visible[repo])
            if not public:
                current.update(name=agent["id"], taskId=agent.get("taskId"))
            if agent.get("issueId"):
                key = "issue:" + str(agent["issueId"])
                if con.execute("SELECT 1 FROM twin_signals WHERE repo=? AND key=? AND status='open'",
                               (repo, key)).fetchone():
                    current["bugId"] = opaque("bug", repo, key)
            agents.append(current)
        key = "public_id" if public else "id"
        where = " WHERE repo IN (" + ",".join("?" for _ in visible) + ")"
        if public:
            where += " AND public_id IS NOT NULL"
        ids = tuple(visible)
        cursor = con.execute("SELECT coalesce(max(" + key + "),0) FROM twin_events" + where, ids).fetchone()[0]
        frontier = (clock["public_cursor"] if public else con.execute(
            "SELECT coalesce(max(id),0) FROM twin_events").fetchone()[0]) - EVENT_RETENTION
        reset = since is not None and (since > cursor or (0 < since <= frontier and since < cursor))
        lower = 0 if since is None or reset else since
        rows = con.execute("SELECT * FROM twin_events" + where + f" AND {key}>? ORDER BY {key} DESC LIMIT ?",
                           (*ids, lower, EVENT_LIMIT + 1)).fetchall()
        gap = len(rows) > EVENT_LIMIT and since is not None and not reset
        events = []
        for row in reversed(rows[:EVENT_LIMIT]):
            event = {"id": row[key], "type": row["kind"], "projectId": visible[row["repo"]], "at": row["at"]}
            for field, name in (("entity", "entityId"), ("level", "level"), ("tier", "tier")):
                if row[field] is not None:
                    event[name] = row[field]
            if row["agent"] is not None:
                event["agentId"] = agent_alias(aliases, row["repo"], row["agent"])
            events.append(event)
        revision = clock["public_revision" if public else "revision"]
        return {"version": 1, "revision": revision, "cursor": cursor,
                "cursorReset": reset, "eventGap": gap, "projects": projects, "agents": agents,
                "events": events, "updatedAt": max(freshness, default=None), "serverTime": now,
                "demo": False}


def demo_snapshot(since=None, now=None):
    """Synthetic time-dependent view. No registry, token, database or ledger access."""
    now = time.time() if now is None else now
    tick = int(now // 8)
    phase = tick % 5
    project = {"id": "demo-lab", "name": "Demo Lab", "level": 5, "tier": "app", "xp": 420,
               "runCommits": 42, "bonusBps": 10000, "reboots": 0, "rebootEligible": False,
               "rebootRequired": REBOOT_COMMITS, "stale": False, "observedAt": now,
               "stats": {"commits": 42, "issues": 3, "pulls": 8, "ci": 4, "releases": 2},
               "bugs": [{"id": "demo-issue", "kind": "issue"}] if phase < 2 else [],
               "bugCount": int(phase < 2)}
    kinds = ("bug_opened", "agent", "bug_resolved", "levelup", "install")
    events = [{"id": n, "type": kinds[n % 5], "projectId": project["id"], "entityId": "demo-issue",
               "agentId": "demo-agent", "level": 5, "tier": "app", "at": n * 8}
              for n in range(max(tick - 4, (since or 0) + 1), tick + 1)]
    agent = {"id": "demo-agent", "projectId": project["id"], "status": "coding" if phase < 2 else "waiting",
             "bugId": "demo-issue" if phase < 2 else None, "progress": None, "observedAt": now, "stale": False}
    return {"version": 1, "revision": tick, "cursor": tick, "cursorReset": since is not None and since > tick,
            "eventGap": since is not None and since < tick - 5, "projects": [project], "agents": [agent],
            "events": events, "updatedAt": now, "serverTime": now, "demo": True}
