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
        self.extra_refs = {}
        self.histories = {}
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
        if "/git/commits/" in path:
            identifier = path.rsplit("/", 1)[-1]
            commit = next((item["commit"] for item in self.commits if item["sha"] == identifier), None)
            return {"sha": identifier, **(commit or {"committer": {"date": iso(NOW - 1)}})}, False
        if "/compare/" in path:
            return {"status": "behind"}, False
        return {"id": self.repo, "private": self.private, "visibility": "private" if self.private else "public"}, False

    def pages(self, path, key=None, params=None):
        if "/compare/" in path:
            return iter(self.commits)
        if self.error:
            raise self.error
        if path.endswith("/branches"):
            refs = {"main": self.head, "feature/release": OTHER, **self.extra_refs}
            return ({"name": name, "commit": {"sha": tip}} for name, tip in refs.items())
        if path.endswith("/commits"):
            return iter(self.histories.get(params["sha"], ()))
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
        self.assertEqual(github.collect(self.service, 1, self.source, NOW + 2)["xp"], 0)
        self.source.head = "1" * 40
        self.source.commits.append(make_commit(self.source.head, NOW + 3))
        self.assertEqual(github.collect(self.service, 1, self.source, NOW + 3)["xp"], 10)
        config = {**self.projects[1], "refs": (*self.projects[1]["refs"], "new-branch")}
        self.service = twin.Twin(self.store, {**self.projects, 1: config})
        self.assertEqual(github.collect(self.service, 1, self.source, NOW + 3.5)["xp"], 0)
        self.source.error = github.Deferred(NOW + 100)
        with self.assertRaises(github.Deferred):
            github.collect(self.service, 1, self.source, NOW + 4)
        self.assertEqual(self.row()["observed_at"], NOW + 3.5)
        self.source.error = None
        with self.assertRaises(github.Deferred):
            github.collect(self.service, 1, self.source, NOW + 99)
        self.assertEqual(github.collect(self.service, 1, self.source, NOW + 101)["xp"], 0)

    def test_discovery_awards_unseen_branch_history_and_survives_deletion(self):
        github.collect(self.service, 1, self.source, NOW)
        fresh_tip = "a" * 40
        fresh_parent = "b" * 40
        self.source.extra_refs["work/new"] = fresh_tip
        self.source.histories[fresh_tip] = [
            make_commit(MAIN, NOW - 1),
            make_commit("c" * 40, NOW - 2),
            make_commit(fresh_parent, NOW + 1),
            make_commit(fresh_tip, NOW + 2),
        ]
        outcome = github.collect(self.service, 1, self.source, NOW + 3)
        self.assertEqual(outcome, {"commits": 3, "eligible": 2, "xp": 20})
        metrics = self.service.integrations(now=NOW + 3)
        self.assertIn("work/new", metrics["projects"][0]["trackedRefs"])
        self.assertEqual(metrics["collection"]["awardedToday"], 2)
        self.assertEqual(metrics["collection"]["excludedToday"]["before_baseline"], 1)
        self.assertEqual(metrics["projects"][0]["duplicatesLastSuccess"], 1)
        self.assertNotIn("trackedRefs", json.dumps(self.service.snapshot(public=True)))
        self.assertNotIn("work/new", json.dumps(self.service.snapshot(public=True)))

        # The branch is merged into main and removed; only the merge itself is new.
        self.source.extra_refs.clear()
        self.source.head = "d" * 40
        self.source.commits = [
            make_commit(fresh_parent, NOW + 1), make_commit(fresh_tip, NOW + 2),
            make_commit(self.source.head, NOW + 4, parents=2),
        ]
        self.assertEqual(github.collect(self.service, 1, self.source, NOW + 5), {
            "commits": 1, "eligible": 0, "xp": 0,
        })
        metrics = self.service.integrations(now=NOW + 5)
        self.assertNotIn("work/new", metrics["projects"][0]["trackedRefs"])
        self.assertEqual(metrics["collection"]["excludedToday"]["merge"], 1)
        self.assertEqual(self.row()["xp"], 20)

    def test_recovery_window_keeps_checkpoints_and_never_replays_awards(self):
        github.collect(self.service, 1, self.source, NOW)
        with self.service.transaction(write=True) as con:
            con.execute("UPDATE twin_projects SET xp=250,run_commits=8,reboots=2 WHERE repo=1")
            con.execute("INSERT INTO twin_commits VALUES(1,?,10)", ("a" * 40,))
        self.source.extra_refs["work/backfill"] = "c" * 40
        self.source.histories["c" * 40] = [
            make_commit("f" * 40, NOW - 1), make_commit("a" * 40, NOW + 1),
            make_commit("b" * 40, NOW + 2), make_commit("c" * 40, NOW + 3),
        ]
        window = (NOW - 2, NOW + 2)
        first = github.collect(self.service, 1, self.source, NOW + 4, recover=True, window=window)
        self.assertEqual(first, {"commits": 2, "eligible": 1, "xp": 12})
        self.assertNotIn("work/backfill", self.service.integrations()["projects"][0]["trackedRefs"])
        self.assertGreater(self.row()["requested"], self.row()["served"])
        second = github.collect(self.service, 1, self.source, NOW + 5, recover=True, window=window)
        self.assertEqual(second["xp"], 0)
        self.assertEqual((self.row()["xp"], self.row()["reboots"], self.row()["run_commits"]), (262, 2, 9))
        third = github.collect(self.service, 1, self.source, NOW + 6)
        self.assertEqual(third["xp"], 12)
        self.assertEqual(self.row()["xp"], 274)
        self.assertEqual(self.service.integrations(now=NOW + 86400)["collection"]["collectedToday"], 0)

    def test_discovery_page_failure_preserves_checkpoint(self):
        github.collect(self.service, 1, self.source, NOW)
        before = self.service.integrations()["projects"][0]["trackedRefs"]
        pages = self.source.pages

        def failing_pages(path, **kwargs):
            if path.endswith("/branches"):
                yield {"name": "main", "commit": {"sha": "a" * 40}}
                raise github.CollectionError("GitHub pagination limit exceeded; checkpoint preserved")
            yield from pages(path, **kwargs)

        self.source.pages = failing_pages
        with self.assertRaisesRegex(github.CollectionError, "checkpoint preserved"):
            github.collect(self.service, 1, self.source, NOW + 1)
        self.service.record_collection_failure(1, "GitHub pagination limit exceeded", NOW + 1)
        entry = self.service.integrations(now=NOW + 1)["projects"][0]
        self.assertEqual(entry["trackedRefs"], before)
        self.assertEqual(entry["lastError"], "GitHub pagination limit exceeded")
        self.assertEqual(entry["lastAttemptAt"], NOW + 1)
        self.assertEqual(self.row()["observed_at"], NOW)
        self.assertNotIn("lastError", self.service.snapshot(public=True))

    def test_force_replacement_can_recover_when_the_compare_base_is_gone(self):
        github.collect(self.service, 1, self.source, NOW)
        self.source.head = "a" * 40
        self.source.histories[self.source.head] = [make_commit(self.source.head, NOW + 1)]
        pages = self.source.pages

        def missing_compare(path, **kwargs):
            if "/compare/" in path:
                raise github.ComparisonUnavailable("comparison base unavailable")
            yield from pages(path, **kwargs)

        self.source.pages = missing_compare
        self.assertEqual(github.collect(self.service, 1, self.source, NOW + 2)["xp"], 10)
        self.assertEqual(github.collect(self.service, 1, self.source, NOW + 3)["xp"], 0)

    def test_issues_stale_close_dismiss_reopen_and_resolution(self):
        issue = {"id": 88, "title": "never-serialize", "labels": [{"name": "bug"}], "state": "open", "updated_at": iso(NOW)}
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
        self.source.checks = [{"app": {"id": 11}, "name": "compile", "head_sha": MAIN, "status": "completed",
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

    def test_issue_label_changes_and_reopen(self):
        labels = lambda *names: [{"name": name} for name in names]
        issue = {"id": 11, "state": "open", "updated_at": iso(NOW), "labels": labels("enhancement")}
        regression = dict(issue, id=12, labels=labels(" Regression ", "bug"))
        critical = dict(issue, id=13, labels=labels("Priority: P0", "regression"))
        self.source.issues = [issue, regression, critical]
        github.collect(self.service, 1, self.source, NOW)
        project = lambda: self.service.snapshot(public=True)["projects"][0]
        self.assertEqual(project()["development"]["openTasks"], 1)
        self.assertEqual({bug["classification"] for bug in project()["bugs"]}, {"regression", "critical"})
        self.assertEqual(project()["stats"]["issues"], 3)
        self.assertEqual(project()["bugCount"], 2)

        issue["labels"] = labels(" BUG ")
        github.collect(self.service, 1, self.source, NOW + 1)
        self.assertEqual(project()["bugCount"], 3)
        self.assertEqual(project()["development"]["openTasks"], 0)
        bug_id = twin.opaque("bug", 1, "issue:11")
        issue.update(labels=labels("enhancement"), updated_at=iso(NOW + 2))
        github.collect(self.service, 1, self.source, NOW + 2)
        issue.update(state="closed", state_reason="completed", updated_at=iso(NOW + 3))
        github.collect(self.service, 1, self.source, NOW + 3)
        issue.update(state="open", labels=labels("critical/P0"), updated_at=iso(NOW + 4))
        github.collect(self.service, 1, self.source, NOW + 4)
        payload = self.service.snapshot(public=True)
        self.assertEqual([(event["type"], event["entityId"]) for event in payload["events"]],
                         [(kind, bug_id) for kind in ("bug_opened", "bug_dismissed", "bug_opened")])
        self.assertEqual(project()["bugCount"], 3)

    def test_development_status_uses_git_sources_and_current_ci(self):
        normal = {"id": 1, "state": "open", "updated_at": iso(NOW - 1)}
        self.source.issues = [
            normal, dict(normal, id=2, pull_request={"merged_at": None}),
            dict(normal, id=3, state="closed", pull_request={"merged_at": iso(NOW - 1)}),
            dict(normal, id=4, state="closed", merged=True, pull_request={}),
        ]
        check = {"name": "build", "head_sha": MAIN, "id": 1, "app": {"id": 3},
                 "status": "completed", "conclusion": "failure", "completed_at": iso(NOW - 1)}
        self.source.checks = [check, dict(check, name="queue", id=2, status="queued", conclusion=None)]
        github.collect(self.service, 1, self.source, NOW)
        project = lambda: self.service.snapshot(public=True, now=NOW + 700)["projects"][0]
        self.assertEqual(project()["development"], {
            "lastCommitAt": NOW - 1, "lastActivityAt": NOW - 1, "lastActivityKind": "commit",
            "openTasks": 1, "pulls": {"open": 1, "merged": 1},
            "ci": {"running": 0, "passed": 0, "failed": 1},
        })
        self.source.checks = [dict(check, id=3, status="in_progress", conclusion=None, started_at=iso(NOW + 3))]
        github.collect(self.service, 1, self.source, NOW + 5)
        self.assertEqual(project()["development"]["ci"], {"running": 1, "passed": 0, "failed": 0})
        self.assertEqual(project()["bugCount"], 1)
        self.assertEqual(project()["development"]["lastActivityAt"], NOW + 3)
        self.assertEqual(project()["development"]["lastActivityKind"], "ci")
        self.assertTrue(project()["stale"])
        self.assertEqual(project()["observedAt"], NOW + 5)
        self.source.checks = [dict(check, id=3, conclusion="success", completed_at=iso(NOW + 6))]
        github.collect(self.service, 1, self.source, NOW + 7)
        self.assertEqual(project()["development"]["ci"], {"running": 0, "passed": 1, "failed": 0})
        self.assertEqual(project()["bugCount"], 0)
        self.source.checks = []
        github.collect(self.service, 1, self.source, NOW + 8)
        self.assertEqual(project()["development"]["ci"], {"running": 0, "passed": 0, "failed": 0})
        self.assertEqual(project()["development"]["lastActivityAt"], NOW + 6)

    def test_world_progress_aggregates_visible_xp_and_reboot_leaves_it_intact(self):
        with self.service.transaction(write=True) as con:
            con.execute("UPDATE twin_projects SET xp=1090, run_commits=100, public_verified=1 WHERE repo=1")
            con.execute("UPDATE twin_projects SET xp=45 WHERE repo=2")
        world = lambda public=False: self.service.snapshot(public=public)["world"]
        self.assertEqual(world(), {"xp": 1135, "level": 12, "levelXp": 35, "levelXpRequired": 100})
        self.assertEqual(world(public=True), {"xp": 1090, "level": 11, "levelXp": 90, "levelXpRequired": 100})
        result = self.service.reboot(self.public_id, "world-reboot", "admin")
        self.assertEqual(result["world"], world())
        self.assertEqual(result["projects"][0]["xp"], 1090)
        self.assertEqual(result["projects"][0]["level"], 11)
        self.assertEqual(result["projects"][0]["bonusBps"], 11000)
        self.assertEqual(result["projects"][0]["runCommits"], 0)
        self.assertEqual(twin.demo_snapshot(now=NOW)["world"]["xp"], 420)

    def test_v5_migration_preserves_xp_and_requires_verified_labels(self):
        with self.service.transaction(write=True) as con:
            con.execute("UPDATE twin_projects SET xp=155, baseline_at=?, observed_at=?, public_verified=1 WHERE repo=1",
                        (NOW - 1, NOW))
            twin.signal(con, 1, ("issue:22", "issue", "open", NOW, 0), NOW, False)
            con.execute("DROP TABLE twin_development")
            con.execute("PRAGMA user_version=5")
        self.assertTrue(backup.verify_database(self.path)["ok"])
        migrated = twin.Twin(Store(self.path, preserve_legacy=True), self.projects)
        before = migrated.snapshot(public=True)["projects"][0]
        self.assertEqual(before["xp"], 155)
        self.assertEqual(before["bugCount"], 0)
        self.assertIsNone(before["development"])
        self.source.issues = [{"id": 22, "state": "open", "labels": [{"name": "bug"}], "updated_at": iso(NOW)}]
        github.collect(migrated, 1, self.source, NOW + 1)
        after = migrated.snapshot(public=True)
        self.assertEqual(after["projects"][0]["bugCount"], 1)
        self.assertEqual(after["events"], [])
        self.assertEqual(after["world"]["xp"], 155)
        self.assertTrue(backup.verify_database(self.path)["ok"])

    def test_public_project_agent_redaction_and_private_cursor(self):
        github.collect(self.service, 1, self.source, NOW)
        current = self.service.snapshot(public=True, now=NOW)
        hidden = Source(2)
        github.collect(self.service, 2, hidden, NOW)
        with self.service.transaction(write=True) as con:
            twin.emit(con, 2, NOW, "commit")
        self.assertEqual(self.service.snapshot(public=True, now=NOW), current)
        self.source.issues = [{"id": 44, "state": "open", "labels": [{"name": "bug"}], "updated_at": iso(NOW + 1)}]
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
        self.assertEqual(request("GET", admin + "/integrations")[0], 401)
        self.assertEqual(request("GET", admin + "/integrations", headers=auth)[1]["collection"]["repositories"], 2)
        self.assertNotIn("collection", request("GET", public + "/state")[1])
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
        unregistered = signed.replace(b'"id": 1', b'"id": 999')
        outside = {**headers, "X-Hub-Signature-256":
                   "sha256=" + hmac.new(SECRET.encode(), unregistered, hashlib.sha256).hexdigest()}
        self.assertEqual(request("POST", hook, unregistered, outside)[0], 401)
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
