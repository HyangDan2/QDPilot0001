"""HTTP contract tests for the admin console, the public world and static UI."""

import http.client
import json
import os
import socket
import socketserver
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from http.server import HTTPServer, ThreadingHTTPServer

from server.api import make_handler
from server.store import Store

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
PUBLIC = STATIC / "public"
USER = "hd2service"
ADMIN = "/admin/idle-ops"
WORLD = "/idle-ops"


class UnixHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    address_family = socket.AF_UNIX
    daemon_threads = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = "unix"
        self.server_port = 0


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(os.path.join(cls.tmp.name, "api.sqlite3"))
        handler = make_handler(
            cls.store,
            dev_user=None,
            static_root=str(STATIC),
            public_root=str(PUBLIC),
        )
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

    def call(self, method, path, body=None, user=USER, raw_body=None, role=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if user:
            headers["X-HD2Lab-User"] = user
        if role:
            headers["X-HD2Lab-Role"] = role
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

    def test_duplicate_operation_replays_and_applies_once(self):
        self.call("POST", ADMIN + "/api/v1/reset", body={"opId": "op_rst_idem_01"}, user="buyer")
        payload = {"opId": "op_idem_http_1", "action": {"type": "buyServer", "tier": "cache", "qty": 1}}
        first_status, _, first = self.call("POST", ADMIN + "/api/v1/actions", body=payload, user="buyer")
        second_status, _, second = self.call("POST", ADMIN + "/api/v1/actions", body=payload, user="buyer")
        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["state"]["servers"], first["state"]["servers"])
        self.assertEqual(second["message"], first["message"])

    def test_conflicting_operation_returns_conflict(self):
        self.call("POST", ADMIN + "/api/v1/reset", body={"opId": "op_rst_idem_02"}, user="buyer")
        status, _, _ = self.call(
            "POST", ADMIN + "/api/v1/actions",
            body={"opId": "op_idem_http_2", "action": {"type": "buyServer", "tier": "cache", "qty": 1}},
            user="buyer",
        )
        self.assertEqual(status, 200)
        status, _, body = self.call(
            "POST", ADMIN + "/api/v1/actions",
            body={"opId": "op_idem_http_2", "action": {"type": "buyServer", "tier": "cache", "qty": 10}},
            user="buyer",
        )
        self.assertEqual(status, 409)
        self.assertIn("opId", body["detail"])

    def test_admin_payload_has_independent_timestamps(self):
        status, _, body = self.call("GET", ADMIN + "/api/v1/state", user="stamp_admin")
        self.assertEqual(status, 200)
        stamps = body["timestamps"]
        for key in ("worldSettledAt", "adminLastSeen", "adminLastCheck", "lastRewardAt", "awaySeconds"):
            self.assertIn(key, stamps)
        self.assertGreaterEqual(stamps["worldSettledAt"], stamps["adminLastCheck"])
        public = self.call("GET", WORLD + "/api/v1/state", user=None)[2]
        self.assertNotIn("timestamps", public)

    def test_public_event_stream_keeps_admin_details_out(self):
        self.call("POST", ADMIN + "/api/v1/reset", body={"opId": "op_rst_priv_01"}, user="privacy")
        self.call(
            "POST", ADMIN + "/api/v1/actions",
            body={"opId": "op_priv_install", "action": {"type": "buyServer", "tier": "cache", "qty": 1}},
            user="privacy",
        )
        status, _, body = self.call("GET", WORLD + "/api/v1/state?since=0", user=None)
        self.assertEqual(status, 200)
        self.assertIn("cursorReset", body)
        self.assertIn("eventGap", body)
        self.assertTrue(body["events"])
        for event in body["events"]:
            for secret in ("actor", "opId", "op_id", "message", "backup", "user"):
                self.assertNotIn(secret, event)

    def test_public_cursor_past_the_newest_event_signals_reset(self):
        newest = self.call("GET", WORLD + "/api/v1/state?since=0", user=None)[2]["cursor"]
        status, _, body = self.call("GET", WORLD + "/api/v1/state?since=999999", user=None)
        self.assertEqual(status, 200)
        self.assertTrue(body["cursorReset"])
        self.assertEqual(body["events"], [])
        self.assertEqual(body["cursor"], newest)

    def test_public_read_does_not_touch_the_database(self):
        self.call("GET", ADMIN + "/api/v1/state")
        with sqlite3.connect(self.store.path) as con:
            before = (
                con.execute("SELECT state_json, updated_at FROM world WHERE id = 1").fetchone(),
                con.execute("SELECT COUNT(*) FROM world_events").fetchone()[0],
            )
        for _ in range(3):
            status, _, body = self.call("GET", WORLD + "/api/v1/state", user=None)
            self.assertEqual(status, 200)
            self.assertNotIn("message", body["events"][:1] if body["events"] else [])
        with sqlite3.connect(self.store.path) as con:
            after = (
                con.execute("SELECT state_json, updated_at FROM world WHERE id = 1").fetchone(),
                con.execute("SELECT COUNT(*) FROM world_events").fetchone()[0],
            )
        self.assertEqual(before, after)

    def test_public_view_sees_admin_writes_immediately(self):
        self.call("GET", WORLD + "/api/v1/state", user=None)
        self.call("POST", ADMIN + "/api/v1/actions",
                  body={"opId": "op_public_sync", "action": {"type": "buyServer", "tier": "cache", "qty": 1}},
                  user="buyer")
        status, _, body = self.call("GET", WORLD + "/api/v1/state", user=None)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(body["state"]["servers"]["cache"], 2)

    def test_state_reports_server_time(self):
        status, _, body = self.call("GET", ADMIN + "/api/v1/state")
        self.assertEqual(status, 200)
        self.assertLess(abs(body["serverTime"] - time.time()), 10)

    def test_health_is_available_without_identity(self):
        status, _, body = self.call("GET", ADMIN + "/api/v1/health/ready", user=None)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_public_state_needs_no_identity_and_is_sanitised(self):
        status, headers, body = self.call("GET", WORLD + "/api/v1/state", user=None)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("derived", body)
        self.assertIn("catalog", body)
        self.assertIn("events", body)
        self.assertIn("cursor", body)
        self.assertNotIn("last_seen", body["state"])
        self.assertNotIn("last_sim_at", body["state"])
        self.assertGreaterEqual(body["state"]["requests"], 0.0)
        self.assertIn("cache", body["state"]["servers"])

    def test_public_api_rejects_writes(self):
        status, headers, _ = self.call(
            "POST", WORLD + "/api/v1/state",
            body={"opId": "op_public_1", "action": {"type": "reset"}}, user=None,
        )
        self.assertEqual(status, 405)
        self.assertIn("GET", headers["Allow"])

    def test_admin_state_requires_identity(self):
        status, _, body = self.call("GET", ADMIN + "/api/v1/state", user=None)
        self.assertEqual(status, 401)
        self.assertIn("authentication", body["detail"])

    def test_state_is_server_authoritative(self):
        status, _, body = self.call("GET", ADMIN + "/api/v1/state")
        self.assertEqual(status, 200)
        self.assertIn("derived", body)
        self.assertIn("catalog", body)
        self.assertGreaterEqual(len(body["catalog"]["tiers"]), 5)
        self.assertGreaterEqual(body["state"]["requests"], 0.0)

    def test_shared_world_actions_reach_the_public_view(self):
        self.call("GET", ADMIN + "/api/v1/state", user="operator")
        op_id = "op_shared_buy"
        body = {"opId": op_id, "action": {"type": "buyServer", "tier": "cache", "qty": 1}}
        status, _, first = self.call("POST", ADMIN + "/api/v1/actions", body=body, user="operator")
        self.assertEqual(status, 200)
        self.assertFalse(first["duplicate"])
        self.assertEqual(first["state"]["servers"], {"cache": 2})
        self.assertIn("설치", first["message"])
        self.assertIn("install", [event["kind"] for event in first["events"]])

        status, _, public = self.call("GET", WORLD + "/api/v1/state?since=0", user=None)
        self.assertEqual(status, 200)
        self.assertEqual(public["state"]["servers"], {"cache": 2})
        self.assertIn("install", [event["kind"] for event in public["events"]])

    def test_action_is_idempotent_per_op_id(self):
        op_id = "op_repeat_buy"
        body = {"opId": op_id, "action": {"type": "buyServer", "tier": "cache", "qty": 1}}
        status, _, first = self.call("POST", ADMIN + "/api/v1/actions", body=body, user="buyer")
        self.assertEqual(status, 200)
        status, _, second = self.call("POST", ADMIN + "/api/v1/actions", body=body, user="buyer")
        self.assertEqual(status, 200)
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["state"]["servers"], first["state"]["servers"])
        self.assertGreaterEqual(second["state"]["requests"], first["state"]["requests"])

    def test_invalid_requests_are_rejected(self):
        status, _, body = self.call("POST", ADMIN + "/api/v1/actions",
                                    body={"opId": "short", "action": {"type": "buyServer", "tier": "cache"}})
        self.assertEqual(status, 400)
        self.assertIn("opId", body["detail"])

        status, _, body = self.call("POST", ADMIN + "/api/v1/actions",
                                    body={"opId": "op_unknown_action", "action": {"type": "dropTable"}})
        self.assertEqual(status, 400)

        status, _, _ = self.call("POST", ADMIN + "/api/v1/actions", raw_body="{bad json", user="buyer")
        self.assertEqual(status, 400)

        big = "x" * 9000
        status, _, _ = self.call("POST", ADMIN + "/api/v1/actions", raw_body=big, user="buyer")
        self.assertEqual(status, 413)

    def test_insufficient_funds_is_a_client_error(self):
        self.call("GET", ADMIN + "/api/v1/state", user="poor")
        status, _, body = self.call(
            "POST", ADMIN + "/api/v1/actions",
            body={"opId": "op_poor_upgrade", "action": {"type": "buyUpgrade", "id": "network"}},
            user="poor",
        )
        self.assertEqual(status, 400)
        self.assertIn("not enough", body["detail"])

    def test_reset_restores_seed(self):
        status, _, body = self.call(
            "POST", ADMIN + "/api/v1/reset",
            body={"opId": "op_reset_0001"}, user="resetter",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["state"]["servers"], {"cache": 1})
        self.assertEqual(body["state"]["tokens"], 0)
        self.assertIn("초기화", body["message"])

    def test_hard_reset_requires_overlord_role(self):
        status, _, body = self.call(
            "POST", ADMIN + "/api/v1/hard-reset",
            body={"opId": "op_hard_role", "confirm": "HARD RESET"}, user="crew", role="crew",
        )
        self.assertEqual(status, 403)
        self.assertIn("admiral", body["detail"])
        status, _, _ = self.call(
            "POST", ADMIN + "/api/v1/hard-reset",
            body={"opId": "op_hard_role", "confirm": "HARD RESET"}, user=None,
        )
        self.assertEqual(status, 401)

    def test_hard_reset_requires_exact_confirmation(self):
        status, _, body = self.call(
            "POST", ADMIN + "/api/v1/hard-reset",
            body={"opId": "op_hard_conf", "confirm": "hard reset"}, user="boss", role="admiral",
        )
        self.assertEqual(status, 400)
        self.assertIn("HARD RESET", body["detail"])

    def test_hard_reset_zeroes_world_and_returns_backup(self):
        self.call("GET", ADMIN + "/api/v1/state", user="boss")
        status, _, body = self.call(
            "POST", ADMIN + "/api/v1/hard-reset",
            body={"opId": "op_hard_ok_1", "confirm": "HARD RESET"}, user="boss", role="admiral",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["state"]["tokens"], 0)
        self.assertEqual(body["state"]["total_lifetime"], 0)
        self.assertEqual(body["state"]["reboots"], 0)
        self.assertTrue(os.path.isfile(body["backup"]))

    def test_generic_actions_endpoint_rejects_hard_reset(self):
        status, _, _ = self.call(
            "POST", ADMIN + "/api/v1/actions",
            body={"opId": "op_hard_gen", "action": {"type": "hardReset"}},
            user="boss", role="admiral",
        )
        self.assertEqual(status, 400)

    def test_json_constants_are_rejected(self):
        for token in ("NaN", "Infinity", "-Infinity"):
            raw = '{"opId":"op_nan_0001","action":{"type":"buyServer","tier":"cache","qty":1},"x":%s}' % token
            status, _, body = self.call("POST", ADMIN + "/api/v1/actions", raw_body=raw, user="buyer")
            self.assertEqual(status, 400, msg=token)
            self.assertIn("invalid JSON", body["detail"])

    def test_non_string_tier_is_a_client_error(self):
        status, _, body = self.call(
            "POST", ADMIN + "/api/v1/actions",
            body={"opId": "op_bad_tier_1", "action": {"type": "buyServer", "tier": 5, "qty": 1}},
            user="buyer",
        )
        self.assertEqual(status, 400)
        self.assertIn("tier", body["detail"])

    def test_unauthorised_action(self):
        status, _, _ = self.call(
            "POST", ADMIN + "/api/v1/actions",
            body={"opId": "op_no_auth_1", "action": {"type": "buyServer", "tier": "cache", "qty": 1}},
            user=None,
        )
        self.assertEqual(status, 401)

    def test_unknown_api_path(self):
        status, _, _ = self.call("GET", ADMIN + "/api/v1/nope")
        self.assertEqual(status, 404)
        status, _, _ = self.call("GET", WORLD + "/api/v1/nope", user=None)
        self.assertEqual(status, 404)

    def test_serves_admin_ui_and_assets(self):
        status, headers, body = self.call("GET", ADMIN + "/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn(b"assets/app.js", body)
        self.assertIn(b'class="hq-admin-page"', body)

        status, headers, body = self.call("GET", ADMIN + "/assets/styles.css")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/css; charset=utf-8")
        self.assertGreater(len(body), 1000)

        status, headers, body = self.call("GET", ADMIN + "/assets/hyangdani-hero.jpg", user=None)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertEqual(body[:2], b"\xff\xd8")

    def test_serves_public_world_ui(self):
        status, headers, body = self.call("GET", WORLD + "/", user=None)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn(b"assets/world.js", body)
        self.assertNotIn(b"hq-admin-page", body)

        status, headers, body = self.call("GET", WORLD + "/assets/world.css", user=None)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/css; charset=utf-8")
        self.assertGreater(len(body), 1000)

        status, headers, body = self.call("GET", WORLD + "/assets/sprites/hyangdani-idle.png", user=None)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n")

    def test_redirects_base_paths_without_slash(self):
        status, headers, _ = self.call("GET", ADMIN)
        self.assertEqual(status, 308)
        self.assertEqual(headers["Location"], ADMIN + "/")
        status, headers, _ = self.call("GET", WORLD, user=None)
        self.assertEqual(status, 308)
        self.assertEqual(headers["Location"], WORLD + "/")

    def test_rejects_traversal_and_unknown_extensions(self):
        for path in (
            ADMIN + "/%2e%2e/%2e%2e/etc/passwd",
            ADMIN + "/server.py",
            ADMIN + "/assets/../../server/api.py",
            WORLD + "/%2e%2e/%2e%2e/etc/passwd",
            WORLD + "/assets/../../server/api.py",
        ):
            status, _, _ = self.call("GET", path, user=None)
            self.assertEqual(status, 404, path)


class UnixSocketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "unix.sqlite3"))
        self.socket_path = os.path.join(self.tmp.name, "api.sock")
        handler = make_handler(
            self.store,
            dev_user=None,
            static_root=str(STATIC),
            public_root=str(PUBLIC),
        )
        self.server = UnixHTTPServer(self.socket_path, handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, path, user=None):
        lines = ["GET %s HTTP/1.1" % path, "Host: localhost", "Connection: close"]
        if user:
            lines.append("X-HD2Lab-User: %s" % user)
        raw = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(5)
        client.connect(self.socket_path)
        client.sendall(raw)
        data = b""
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            data += chunk
        client.close()
        return data

    def test_health_over_unix_socket(self):
        data = self.request("/admin/idle-ops/api/v1/health/ready")
        self.assertIn(b"HTTP/1.1 200", data)
        self.assertIn(b'"status":"ok"', data)

    def test_public_state_over_unix_socket(self):
        data = self.request("/idle-ops/api/v1/state")
        self.assertIn(b"HTTP/1.1 200", data)
        self.assertIn(b'"derived"', data)

    def test_admin_unauthenticated_over_unix_socket(self):
        data = self.request("/admin/idle-ops/api/v1/state")
        self.assertIn(b"HTTP/1.1 401", data)

    def test_static_over_unix_socket(self):
        data = self.request("/admin/idle-ops/")
        self.assertIn(b"HTTP/1.1 200", data)
        self.assertIn(b"Idle Ops", data)
        data = self.request("/idle-ops/")
        self.assertIn(b"HTTP/1.1 200", data)
        self.assertIn(b"world.js", data)


if __name__ == "__main__":
    unittest.main()
