import contextlib
import hashlib
import hmac
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from server import backup, economy, github, telemetry, twin
from server.api import Server
from server.r3_api import make_twin_handler
from server.store import Store, SCHEMA_VERSION

NOW = 1760000000
MAIN = "0" * 39 + "1"
OTHER = "0" * 39 + "2"
SECRET = "test-local-secret-no-external-use"


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_commit(identifier, at, parents=1):
    return {"sha": identifier, "commit": {"committer": {"date": iso(at)}}, "parents": [{}] * parents}


def registry():
    return twin.registry(json.dumps([
        {"id": 1, "repo": "Owner/Example", "name": "Example", "refs": ["main", "feature/release"], "publish": True},
        {"id": 2, "repo": "Owner/Secret", "name": "PRIVATE-REPO", "refs": ["private-ref"], "publish": True},
    ]))


class Source:
    def __init__(self, repo=1):
        self.repo = repo
        self.head = MAIN
        self.commits = []
        self.issues = []
        self.checks = []
        self.workflows = []
        self.jobs = []
        self.private = repo != 1
        self.error = None

    def get(self, path, params=None):
        if self.error:
            raise self.error
        if "/git/ref/heads/" in path:
            return {"ref": "refs/heads/" + path.split("/git/ref/heads/")[1],
                    "object": {"sha": self.head if path.endswith("/main") else OTHER}}, False
        if "/compare/" in path:
            return {"status": "behind"}, False
        return {"id": self.repo, "private": self.private, "visibility": "private" if self.private else "public"}, False

    def pages(self, path, key=None, params=None):
        if "/compare/" in path:
            return iter(self.commits)
        if self.error:
            raise self.error
        if path.endswith("/issues"):
            return iter(self.issues)
        if path.endswith("/check-runs"):
            return iter(self.checks)
        if path.endswith("/actions/runs"):
            return iter(self.workflows)
        if path.endswith("/jobs"):
            return iter(self.jobs)
        return iter(())


class TwinTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "idle.sqlite3"
        self.store = Store(self.path, preserve_legacy=True)
        self.projects = registry()
        self.scope = telemetry.Scope("private-agent", frozenset((1,)))
        self.token = "integration-test-" * 4
        self.service = twin.Twin(self.store, self.projects, {hashlib.sha256(self.token.encode()).hexdigest(): self.scope})
        self.public_id = self.projects[1]["id"]
        self.source = Source()

    def row(self, repo=1):
        with self.service.transaction() as con:
            return con.execute("SELECT * FROM twin_projects WHERE repo=?", (repo,)).fetchone()

    def test_baseline_dedup_and_reboot(self):
        self.source.commits = [make_commit(MAIN, NOW - 1)]
        self.assertEqual(github.collect(self.service, 1, self.source, NOW)["xp"], 0)
        with self.assertRaises(twin.Conflict):
            self.service.reboot(self.public_id, "eligible-op", "admin")
        self.source.commits = [make_commit(f"{i:040x}", NOW + 1) for i in range(100, 200)] + [
            make_commit(f"{200:040x}", NOW + 1, 2),
            make_commit(f"{201:040x}", NOW - 1),
        ]
        self.source.head = self.source.commits[-1]["sha"]
        self.assertEqual(github.collect(self.service, 1, self.source, NOW + 30), {
            "commits": 102, "eligible": 100, "xp": 1000,
        })
        self.assertEqual(github.collect(self.service, 1, self.source, NOW + 40)["xp"], 0)
        response = self.service.reboot(self.public_id, "eligible-op", "admin")
        self.assertFalse(response["duplicate"])
        self.assertTrue(self.service.reboot(self.public_id, "eligible-op", "admin")["duplicate"])
        with self.assertRaises(twin.Conflict):
            self.service.reboot(self.public_id, "eligible-op", "other")
        self.assertEqual((self.row()["xp"], self.row()["reboots"], self.row()["run_commits"]), (1000, 1, 0))
        self.source.head = "a" * 40
        self.source.commits += [make_commit(self.source.head, NOW + 50)]
        self.assertEqual(github.collect(self.service, 1, self.source, NOW + 60)["xp"], 11)
        self.assertEqual(self.service.snapshot(now=NOW + 86400)["projects"][0]["xp"], 1011)
        self.assertEqual(twin.bonus(20), 20000)

    def test_duplicate_branches_and_reconciliation_backoff(self):
        self.source.head = "f" * 40
        github.collect(self.service, 1, self.source, NOW)
        self.source.head = OTHER
        self.source.commits = [make_commit(OTHER, NOW + 2)]
        self.assertEqual(github.collect(self.service, 1, self.source, NOW + 3)["xp"], 10)
        self.source.error = github.Deferred(NOW + 100)
        with self.assertRaises(github.Deferred):
            github.collect(self.service, 1, self.source, NOW + 4)
        self.assertEqual(self.row()["observed_at"], NOW + 3)
        self.source.error = None
        with self.assertRaises(github.Deferred):
            github.collect(self.service, 1, self.source, NOW + 99)
        self.assertEqual(github.collect(self.service, 1, self.source, NOW + 101)["xp"], 0)

    def test_issues_stale_close_dismiss_reopen_and_resolution(self):
        issue = {"id": 88, "title": "never-serialize", "state": "open", "updated_at": iso(NOW)}
        self.source.issues = [issue]
        github.collect(self.service, 1, self.source, NOW)
        payload = self.service.snapshot(public=True, now=NOW)
        self.assertEqual(len(payload["projects"][0]["bugs"]), 1)
        self.assertEqual(payload["events"], [])
        bug = payload["projects"][0]["bugs"][0]["id"]
        issue.update(state="closed", state_reason="not_planned", updated_at=iso(NOW + 2))
        github.collect(self.service, 1, self.source, NOW + 2)
        issue.update(state="open", updated_at=iso(NOW + 1))
        github.collect(self.service, 1, self.source, NOW + 3)
        self.assertEqual(self.service.snapshot(public=True)["projects"][0]["bugs"], [])
        issue.update(state="open", updated_at=iso(NOW + 4))
        github.collect(self.service, 1, self.source, NOW + 4)
        issue.update(state="closed", state_reason="completed", updated_at=iso(NOW + 5))
        github.collect(self.service, 1, self.source, NOW + 5)
        payload = self.service.snapshot(public=True)
        self.assertEqual([(e["type"], e["entityId"]) for e in payload["events"]],
                         [(kind, bug) for kind in ("bug_dismissed", "bug_opened", "bug_resolved")])
        self.assertNotIn("never-serialize", json.dumps(payload))

    def test_ci_success_matches_ref_source_identity_and_time(self):
        github.collect(self.service, 1, self.source, NOW)
        self.source.checks = [{"app": {"id": 11}, "name": "compile", "head_sha": MAIN,
                               "conclusion": "failure", "completed_at": iso(NOW + 5), "id": 11}]
        github.collect(self.service, 1, self.source, NOW + 6)
        with self.service.transaction() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM twin_signals WHERE status='open'").fetchone()[0], 1)
        self.source.checks = [
            dict(self.source.checks[0], app={"id": 12}, conclusion="success", completed_at=iso(NOW + 7)),
            dict(self.source.checks[0], head_sha=OTHER, conclusion="success", completed_at=iso(NOW + 7)),
            dict(self.source.checks[0], conclusion="success", completed_at=iso(NOW + 4)),
        ]
        github.collect(self.service, 1, self.source, NOW + 8)
        self.assertEqual(self.service.snapshot(public=True)["projects"][0]["bugCount"], 1)
        self.source.checks = [dict(self.source.checks[0], app={"id": 11}, completed_at=iso(NOW + 9), id=12)]
        github.collect(self.service, 1, self.source, NOW + 9)
        public = self.service.snapshot(public=True)
        self.assertEqual(public["projects"][0]["bugCount"], 0)
        self.assertEqual([e["type"] for e in public["events"]], ["bug_opened", "bug_resolved"])

    def test_workflow_and_job_resolution_require_same_workflow_and_name(self):
        github.collect(self.service, 1, self.source, NOW)
        run = {"id": 33, "head_sha": MAIN, "head_branch": "main", "workflow_id": 23,
               "head_repository": {"full_name": "Owner/Example"}, "status": "completed",
               "run_number": 3, "run_attempt": 1, "conclusion": "failure", "updated_at": iso(NOW + 1)}
        job = {"id": 81, "name": "qa (linux)", "status": "completed", "conclusion": "failure",
               "completed_at": iso(NOW + 1)}
        self.source.workflows, self.source.jobs = [run], [job]
        github.collect(self.service, 1, self.source, NOW + 1)
        self.assertEqual(self.service.snapshot(public=True)["projects"][0]["bugCount"], 2)
        self.source.workflows = [dict(run, workflow_id=24, conclusion="success", updated_at=iso(NOW + 2))]
        self.source.jobs = [dict(job, conclusion="success", completed_at=iso(NOW + 2))]
        github.collect(self.service, 1, self.source, NOW + 2)
        self.assertEqual(self.service.snapshot(public=True)["projects"][0]["bugCount"], 2)
        self.source.workflows = [dict(run, conclusion="success", run_attempt=2, updated_at=iso(NOW + 3))]
        self.source.jobs = [dict(job, name="qa (windows)", conclusion="success", completed_at=iso(NOW + 3))]
        github.collect(self.service, 1, self.source, NOW + 3)
        self.assertEqual(self.service.snapshot(public=True)["projects"][0]["bugCount"], 1)
        self.source.jobs = [dict(job, conclusion="success", id=82, completed_at=iso(NOW + 4))]
        github.collect(self.service, 1, self.source, NOW + 4)
        self.assertEqual(self.service.snapshot(public=True)["projects"][0]["bugCount"], 0)

    def test_public_project_agent_redaction_and_private_cursor(self):
        github.collect(self.service, 1, self.source, NOW)
        current = self.service.snapshot(public=True, now=NOW)
        hidden = Source(2)
        github.collect(self.service, 2, hidden, NOW)
        with self.service.transaction(write=True) as con:
            twin.emit(con, 2, NOW, "commit")
        self.assertEqual(self.service.snapshot(public=True, now=NOW), current)
        self.source.issues = [{"id": 44, "state": "open", "updated_at": iso(NOW + 1)}]
        github.collect(self.service, 1, self.source, NOW + 1)
        self.service.ingest_agent({
            "version": 1, "agent_id": "private-agent", "repository_id": 1,
            "event_id": "event-001", "run_id": "run-001", "task_id": "private-task",
            "sequence": 1, "event_type": "testing", "timestamp": iso(NOW + 1), "issue_id": 44,
        }, self.scope, NOW + 1)
        exposed = self.service.snapshot(public=True, now=NOW + 200)
        self.assertEqual(exposed["agents"][0]["status"], "offline")
        self.assertEqual(exposed["agents"][0]["bugId"], exposed["projects"][0]["bugs"][0]["id"])
        for private in ("private-agent", "PRIVATE-REPO", "private-ref", "private-task", "Owner/Example"):
            self.assertNotIn(private, json.dumps(exposed))
        self.source.private = True
        github.collect(self.service, 1, self.source, NOW + 201)
        self.assertEqual(self.service.snapshot(public=True)["projects"], [])

    def test_real_http_boundaries_and_signed_raw_body(self):
        server = Server(("127.0.0.1", 0), make_twin_handler(self.service, SECRET))
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)

        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                conn.request(method, path, body, headers or {})
                response = conn.getresponse()
                result = response.read()
                return response.status, json.loads(result) if result else None
            finally:
                conn.close()

        admin = "/admin/idle-ops/api/v1"
        public = "/idle-ops/api/v1"
        hook = "/internal/idle-ops/v1/github"
        auth = {"X-HD2Lab-User": "tester", "X-HD2Lab-Role": "admiral"}
        self.assertEqual(request("GET", admin + "/state")[0], 401)
        self.assertEqual(request("GET", admin + "/state", headers={"X-HD2Lab-User": "tester"})[0], 403)
        self.assertEqual(request("HEAD", public + "/state")[0], 200)
        self.assertEqual(request("POST", public + "/state", "{}")[0], 405)
        live_before = request("GET", public + "/state")[1]["projects"]
        self.assertTrue(request("GET", public + "/state?demo=1")[1]["demo"])
        self.assertEqual(request("GET", public + "/state")[1]["projects"], live_before)
        for old in ("buyServer", "buyUpgrade", "reset"):
            body = json.dumps({"opId": "valid-id", "action": {"type": old}})
            self.assertEqual(request("POST", admin + "/actions", body, auth)[0], 400)
        for old in ("reset", "hard-reset"):
            self.assertEqual(request("POST", admin + "/" + old, "{}", auth)[0], 404)
        queue = {"projectId": self.public_id}
        self.assertEqual(request("POST", admin + "/reconcile", json.dumps(queue), auth), (202, {"queued": True}))
        original = self.row()["requested"]
        signed = b'{ "repository": {"id": 1}, "ref": "refs/heads/main" }'
        headers = {"X-GitHub-Event": "push", "X-GitHub-Delivery": "delivery-0001",
                   "X-Hub-Signature-256": "sha256=" + hmac.new(SECRET.encode(), signed, hashlib.sha256).hexdigest()}
        self.assertEqual(request("POST", hook, signed + b" ", headers)[0], 401)
        self.assertEqual(request("POST", hook, signed, headers), (202, {"accepted": True, "duplicate": False}))
        self.assertEqual(request("POST", hook, signed, headers), (202, {"accepted": True, "duplicate": True}))
        self.assertEqual(self.row()["requested"], original + 1)

    def test_version_zero_archive_migration_and_restore(self):
        legacy = self.path.with_name("legacy.sqlite3")
        with contextlib.closing(sqlite3.connect(legacy)) as con, con:
            for sql in (
                "CREATE TABLE world(id INTEGER PRIMARY KEY,state_json TEXT,updated_at REAL)",
                "CREATE TABLE world_ops(op_id TEXT PRIMARY KEY,created_at REAL)",
                "CREATE TABLE world_events(id INTEGER PRIMARY KEY,created_at REAL,kind TEXT,payload_json TEXT)",
            ):
                con.execute(sql)
            con.execute("INSERT INTO world VALUES(1,?,?)", (json.dumps(dict(economy.initial_state(NOW), tokens=12)), NOW))
            con.execute("INSERT INTO world_ops VALUES('old-record',?)", (NOW,))
        archive = backup.create(legacy, kind="pre-deploy")
        self.assertTrue(backup.verify_database(archive)["ok"], backup.verify_database(archive))
        self.assertEqual(backup.verify_database(archive)["schemaVersion"], 0)
        migrated = Store(legacy, preserve_legacy=True)
        with contextlib.closing(migrated._connect()) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(json.loads(con.execute("SELECT state_json FROM world").fetchone()[0])["tokens"], 12)
            self.assertIsNotNone(con.execute("SELECT 1 FROM world_ops WHERE op_id='old-record'").fetchone())
        self.assertEqual(twin.Twin(migrated, self.projects).snapshot()["projects"][0]["xp"], 0)
        backup.restore(legacy, archive)
        self.assertEqual(backup.verify_database(legacy)["schemaVersion"], 0)


class GitHubLimitsTest(unittest.TestCase):
    def test_pagination_preserves_bounded_size_and_rate_reset(self):
        class Fake(github.GitHub):
            def get(self, path, params):
                return {"commits": [params["page"]]}, True

        data = Fake("local-test")
        with self.assertRaises(github.CollectionError):
            list(data.pages("/repos/example/repo/compare", key="commits"))
        self.assertEqual(github.retry_time({"Retry-After": "10"}, NOW), NOW + 10)
        self.assertEqual(github.retry_time({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(NOW + 20)},
                                          NOW), NOW + 21)
