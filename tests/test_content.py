"""Content interface, feature flags and balance fingerprint tests."""

import http.client
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from http.server import ThreadingHTTPServer

from server import content
from server.api import make_handler
from server.store import Store

USER = "hd2service"
ADMIN = "/admin/idle-ops/api/v1"
PUBLIC = "/idle-ops/api/v1"


class ContentModelTests(unittest.TestCase):
    def test_feature_flags_default_off(self):
        with mock.patch.dict("os.environ", clear=False):
            for name in content.FEATURE_FLAGS:
                self.assertFalse(content.enabled(name))
            self.assertEqual(content.features(), {name: False for name in content.FEATURE_FLAGS})

    def test_feature_flag_env_override(self):
        env = {"IDLE_OPS_FEATURE_ACHIEVEMENT_REWARDS": "1", "IDLE_OPS_FEATURE_DAILY_BONUS": "off"}
        with mock.patch.dict("os.environ", env, clear=False):
            self.assertTrue(content.enabled("achievement_rewards"))
            self.assertFalse(content.enabled("daily_bonus"))

    def test_unknown_feature_rejected(self):
        with self.assertRaises(ValueError):
            content.enabled("not_a_flag")

    def test_overview_is_static_and_server_sourced(self):
        first = content.overview()
        second = content.overview()
        self.assertEqual(first, second)
        self.assertEqual(first["rewardPolicy"], "server-computed")
        self.assertEqual(first["balanceVersion"], content.BALANCE_VERSION)
        self.assertEqual(first["balanceHash"], content.balance_hash())
        self.assertEqual(len(first["tiers"]), 5)
        self.assertEqual(len(first["upgrades"]), 4)
        self.assertEqual(len(first["achievements"]), 8)
        blob = json.dumps(first)
        for leaked in ("requests", "actor", "opId", "world", "admin_seen"):
            self.assertNotIn(leaked, blob)

    def test_balance_fingerprint_requires_approval(self):
        # New balance needs PO approval, a BALANCE_VERSION bump and doc updates.
        self.assertEqual(content.balance_hash(), "3c25713b78cb6bc6")


class ContentApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(os.path.join(cls.tmp.name, "content.sqlite3"))
        handler = make_handler(cls.store)
        handler.log_message = lambda *args, **kwargs: None
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def call(self, path, user=USER):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"X-HD2Lab-User": user} if user else {}
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b"{}")
        finally:
            connection.close()

    def test_admin_content_requires_auth(self):
        status, body = self.call(ADMIN + "/content", user=None)
        self.assertEqual(status, 401)
        self.assertIn("detail", body)

    def test_admin_content_returns_balance_and_flags(self):
        status, body = self.call(ADMIN + "/content")
        self.assertEqual(status, 200)
        self.assertEqual(body["balanceHash"], content.balance_hash())
        self.assertEqual(body["features"], content.features())
        self.assertFalse(body["features"]["achievement_rewards"])

    def test_public_content_is_not_exposed(self):
        status, _ = self.call(PUBLIC + "/content")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
