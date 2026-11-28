import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from server import discovery, github, reconcile, twin
from server.store import Store


def repository(repo, name, *, owner="Example", owner_type="User", visibility="public", **flags):
    return {
        "id": repo, "name": name, "owner": {"login": owner, "type": owner_type},
        "visibility": visibility, "private": visibility != "public", "default_branch": "main",
        "archived": False, "disabled": False, "fork": False, **flags,
    }


class Inventory:
    def __init__(self, items):
        self.items = items

    def pages(self, path, params=None):
        assert path == "/user/repos" and params["visibility"] == "public"
        yield from self.items


class CoverageTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.db = Path(scratch.name) / "qa.sqlite3"
        self.store = Store(self.db)
        self.policy = discovery.policy({"owner": "example", "families": ["AGPilot", "AGWS"]})
        self.manual = twin.registry(json.dumps([
            {"id": 1, "repo": "Example/QDPilot0001", "name": "Existing", "refs": ["main"], "publish": True},
            {"id": 2, "repo": "Example/Server-HQ", "name": "Control", "refs": ["main"], "publish": True},
        ]))

    def test_public_family_inventory_cannot_add_private_forks_or_other_owners(self):
        self.assertIsNone(discovery.policy(None))
        rows = [
            repository(3, "AGPilot0002"),
            repository(4, "AGWS-Dev"),
            repository(5, "AGPilot0099", visibility="private"),
            repository(6, "AGPilot0098", visibility="internal"),
            repository(7, "Server-HQ"),
            repository(8, "PKPilot0003"),
            repository(9, "AGPilot0100", archived=True),
            repository(10, "AGPilot0101", disabled=True),
            repository(11, "AGPilot0102", fork=True),
            repository(12, "AGPilot0103", owner="Other"),
            repository(13, "AGPilot0104", owner_type="Organization"),
            repository(14, "AGPilotsMaintenance"),
        ]
        self.assertEqual(set(discovery.candidates(Inventory(rows), self.policy)), {3, 4})
        deny = discovery.policy({"owner": "example", "families": ["AGPilot"], "exclude_ids": [3]})
        self.assertEqual(discovery.candidates(Inventory(rows), deny), {})
        self.assertFalse(self.manual[2]["publish"])

    def test_cap_precedence_revocation_and_stable_progress(self):
        service = twin.Twin(self.store, self.manual, discovery_policy=self.policy)
        other_process = twin.Twin(self.store, self.manual, discovery_policy=self.policy)
        many = {repo: ("Example", f"AGPilot{repo:04d}", "main") for repo in range(3, 60)}
        self.assertEqual(service.register_discovered(many), 38)
        self.assertEqual(len(other_process.projects), 40)
        disabled = twin.Twin(self.store, self.manual)
        self.assertEqual(set(disabled.projects), {1, 2})
        self.assertEqual(disabled.register_discovered({}), 0)
        self.assertEqual(len(other_process.projects), 40)
        self.assertFalse(other_process.projects[3]["publish"])
        with service.transaction(write=True) as con:
            con.execute("UPDATE twin_projects SET xp=200, reboots=1, baseline_at=100 WHERE repo=3")
        self.assertEqual(other_process.snapshot()["world"]["xp"], 200)
        self.assertEqual(other_process.snapshot(public=True)["world"]["xp"], 0)
        service.register_discovered({4: many[4]})
        self.assertNotIn(3, other_process.projects)
        service.register_discovered({3: many[3]})
        restored = next(p for p in other_process.snapshot()["projects"] if p["id"] == twin.opaque("project", 3))
        self.assertEqual((restored["xp"], restored["reboots"]), (200, 1))

    def test_visibility_change_evicts_auto_repo_before_subresource_reads(self):
        policy = discovery.policy({"owner": "example", "families": ["AGPilot"], "publish_public": True})
        service = twin.Twin(self.store, self.manual, discovery_policy=policy)
        service.register_discovered({3: ("Example", "AGPilot0003", "main")})
        with service.transaction(write=True) as con:
            con.execute("UPDATE twin_projects SET public_verified=1 WHERE repo=3")
        self.assertEqual(len(service.snapshot(public=True)["projects"]), 1)

        class Private:
            def get(self, path):
                self_path = "/repos/Example/AGPilot0003"
                assert path == self_path
                return repository(3, "AGPilot0003", visibility="private"), False

        with self.assertRaises(github.RepositoryUnavailable):
            github.collect(service, 3, Private())
        self.assertNotIn(3, service.projects)
        self.assertEqual(service.snapshot(public=True)["projects"], [])

    def test_hourly_inventory_claim_and_batched_fairness(self):
        service = twin.Twin(self.store, self.manual, discovery_policy=self.policy)
        self.assertTrue(discovery.mark_attempt(service, self.policy, 4000))
        self.assertFalse(discovery.mark_attempt(service, self.policy, 4001))
        self.assertTrue(discovery.mark_attempt(service, self.policy, 7600))
        projects = twin.registry(json.dumps([
            {"id": repo, "repo": f"Example/Repo{repo}", "name": str(repo), "refs": ["main"]}
            for repo in range(1, 7)
        ]))
        collected = []

        def observe(service, repo, client, **kwargs):
            collected.append(repo)
            client.requests = 8
            now = time.time()
            with service.transaction(write=True) as con:
                con.execute("UPDATE twin_projects SET observed_at=?, served=requested WHERE repo=?", (now, repo))
            return {}

        with (patch.object(reconcile.r3_config, "load_deployment", return_value=(projects, {}, None, None)),
              patch.object(github, "collect", side_effect=observe),
              patch.dict(os.environ, {"IDLE_OPS_GITHUB_TOKEN": "local-only"}),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(reconcile.main(["--db", str(self.db)]), 0)
            self.assertEqual(len(collected), 4)
            self.assertEqual(reconcile.main(["--db", str(self.db)]), 0)
            self.assertEqual(collected, list(range(1, 7)))

    def test_permissions_do_not_trigger_credential_wide_backoff(self):
        class Forbidden:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 403, "forbidden",
                                {"X-RateLimit-Remaining": "4000"},
                                io.BytesIO(b'{"message":"Resource not accessible by personal access token"}'))

        with self.assertRaisesRegex(github.CollectionError, "HTTP 403") as result:
            github.GitHub("local-only", opener=Forbidden()).get("/user/repos")
        self.assertNotIsInstance(result.exception, github.Deferred)

    def test_http_headers_enforce_shared_rate_reserve_before_next_request(self):
        calls = []
        headers = {"X-RateLimit-Remaining": str(github.RATE_RESERVE),
                   "X-RateLimit-Reset": str(int(time.time() + 600))}

        class Endpoint:
            @contextlib.contextmanager
            def open(self, request, timeout):
                calls.append(request.full_url)
                yield type("Response", (), {"headers": headers})()

        quota = {}
        for _ in range(2):
            with self.assertRaises(github.Deferred):
                github.GitHub("local-only", opener=Endpoint(), quota=quota).get("/user/repos")
        self.assertEqual(len(calls), 1)
