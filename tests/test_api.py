"""HTTP contract tests for the Idle Ops API and static UI."""

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from http.server import ThreadingHTTPServer

from server.api import make_handler
from server.store import Store

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
USER = "hd2service"


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(os.path.join(cls.tmp.name, "api.sqlite3"))
        handler = make_handler(cls.store, dev_user=None, static_root=str(STATIC))
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

    def call(self, method, path, body=None, user=USER, raw_body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if user:
            headers["X-HD2Lab-User"] = user
        payload = raw_body
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            data = response.read()
            content_type = response.getheader("Content-Type") or ""
            parsed = json.loads(data) if data and content_type.startswith("application/json") else data
            return response.status, dict(response.getheaders()), parsed
        finally:
            connection.close()

    def test_health_is_available_without_identity(self):
        status, _, body = self.call("GET", "/admin/idle-ops/api/v1/health/ready", user=None)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_state_requires_identity(self):
        status, _, body = self.call("GET", "/admin/idle-ops/api/v1/state", user=None)
        self.assertEqual(status, 401)
        self.assertIn("authentication", body["detail"])

    def test_state_is_server_authoritative(self):
        status, headers, body = self.call("GET", "/admin/idle-ops/api/v1/state")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("derived", body)
        self.assertIn("catalog", body)
        self.assertEqual(body["state"]["servers"], {"cache": 1})
        self.assertGreaterEqual(body["state"]["requests"], 25.0)

    def test_identities_are_isolated(self):
        self.call("GET", "/admin/idle-ops/api/v1/state", user="alice")
        status, _, body = self.call("GET", "/admin/idle-ops/api/v1/state", user="bob")
        self.assertEqual(status, 200)
        self.assertEqual(body["state"]["servers"], {"cache": 1})

    def test_buy_server_and_repeat_with_same_op_id(self):
        self.call("GET", "/admin/idle-ops/api/v1/state", user="buyer")
        op_id = "op_initial_buy"
        body = {"opId": op_id, "action": {"type": "buyServer", "tier": "cache", "qty": 1}}
        status, _, first = self.call("POST", "/admin/idle-ops/api/v1/actions", body=body, user="buyer")
        self.assertEqual(status, 200)
        self.assertFalse(first["duplicate"])
        self.assertEqual(first["state"]["servers"], {"cache": 2})
        self.assertIn("설치", first["message"])

        status, _, second = self.call("POST", "/admin/idle-ops/api/v1/actions", body=body, user="buyer")
        self.assertEqual(status, 200)
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["state"]["servers"], {"cache": 2})
        self.assertGreaterEqual(second["state"]["requests"], first["state"]["requests"])

    def test_invalid_requests_are_rejected(self):
        status, _, body = self.call("POST", "/admin/idle-ops/api/v1/actions",
                                    body={"opId": "short", "action": {"type": "buyServer", "tier": "cache"}})
        self.assertEqual(status, 400)
        self.assertIn("opId", body["detail"])

        status, _, body = self.call("POST", "/admin/idle-ops/api/v1/actions",
                                    body={"opId": "op_unknown_action", "action": {"type": "dropTable"}})
        self.assertEqual(status, 400)

        status, _, _ = self.call("POST", "/admin/idle-ops/api/v1/actions", raw_body="{bad json",
                                 user="buyer")
        self.assertEqual(status, 400)

        big = "x" * 9000
        status, _, _ = self.call("POST", "/admin/idle-ops/api/v1/actions",
                                 raw_body=big, user="buyer")
        self.assertEqual(status, 413)

    def test_insufficient_funds_is_a_client_error(self):
        self.call("GET", "/admin/idle-ops/api/v1/state", user="poor")
        status, _, body = self.call(
            "POST", "/admin/idle-ops/api/v1/actions",
            body={"opId": "op_poor_upgrade", "action": {"type": "buyUpgrade", "id": "network"}},
            user="poor",
        )
        self.assertEqual(status, 400)
        self.assertIn("not enough", body["detail"])

    def test_reset_restores_seed(self):
        self.call("GET", "/admin/idle-ops/api/v1/state", user="resetter")
        status, _, body = self.call(
            "POST", "/admin/idle-ops/api/v1/reset",
            body={"opId": "op_reset_0001"}, user="resetter",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["state"]["servers"], {"cache": 1})
        self.assertEqual(body["state"]["tokens"], 0)
        self.assertIn("초기화", body["message"])

    def test_unauthorised_action(self):
        status, _, _ = self.call(
            "POST", "/admin/idle-ops/api/v1/actions",
            body={"opId": "op_no_auth_1", "action": {"type": "buyServer", "tier": "cache", "qty": 1}},
            user=None,
        )
        self.assertEqual(status, 401)

    def test_unknown_api_path(self):
        status, _, _ = self.call("GET", "/admin/idle-ops/api/v1/nope")
        self.assertEqual(status, 404)

    def test_serves_ui_and_assets(self):
        status, headers, body = self.call("GET", "/admin/idle-ops/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn(b"assets/app.js", body)
        self.assertIn(b"Idle Ops", body)

        status, headers, body = self.call("GET", "/admin/idle-ops/assets/styles.css")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/css; charset=utf-8")
        self.assertGreater(len(body), 1000)

        status, _, _ = self.call("GET", "/admin/idle-ops/assets/app.js")
        self.assertEqual(status, 200)

        status, headers, body = self.call("GET", "/admin/idle-ops/assets/hyangdani-hero.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertEqual(body[:2], b"\xff\xd8")

    def test_redirects_base_path_without_slash(self):
        status, headers, _ = self.call("GET", "/admin/idle-ops")
        self.assertEqual(status, 308)
        self.assertEqual(headers["Location"], "/admin/idle-ops/")

    def test_rejects_traversal_and_unknown_extensions(self):
        status, _, _ = self.call("GET", "/admin/idle-ops/%2e%2e/%2e%2e/etc/passwd")
        self.assertEqual(status, 404)
        status, _, _ = self.call("GET", "/admin/idle-ops/server.py")
        self.assertEqual(status, 404)
        status, _, _ = self.call("GET", "/admin/idle-ops/assets/../../server/api.py")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
