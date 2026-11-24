"""Operational hardening: readiness truth, request timeouts, storage indexes."""

import http.client
import json
import os
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from server import store as store_module
from server.api import Handler, Server, make_handler
from server.store import SCHEMA_VERSION, Store

ADMIN = "/admin/idle-ops"


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "ready.sqlite3")
        self.store = Store(self.path)

    def test_healthy_store_reports_every_check(self):
        report = self.store.readiness()
        self.assertTrue(report["ok"])
        self.assertTrue(report["world"])
        self.assertTrue(report["schema"])
        self.assertTrue(report["disk"])
        self.assertEqual(report["schemaVersion"], SCHEMA_VERSION)

    def test_missing_world_row_is_not_ready(self):
        with sqlite3.connect(self.path) as con:
            con.execute("DELETE FROM world")
        report = self.store.readiness()
        self.assertFalse(report["ok"])
        self.assertFalse(report["world"])
        self.assertTrue(report["schema"])
        self.assertTrue(report["disk"])

    def test_corrupt_world_row_is_not_ready(self):
        with sqlite3.connect(self.path) as con:
            con.execute("UPDATE world SET state_json = ? WHERE id = 1", ("{not json",))
        report = self.store.readiness()
        self.assertFalse(report["ok"])
        self.assertFalse(report["world"])

    def test_schema_mismatch_is_not_ready(self):
        with sqlite3.connect(self.path) as con:
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
        report = self.store.readiness()
        self.assertFalse(report["ok"])
        self.assertTrue(report["world"])
        self.assertFalse(report["schema"])

    def test_low_free_space_is_not_ready(self):
        original = store_module.MIN_FREE_BYTES
        store_module.MIN_FREE_BYTES = 1 << 62
        try:
            report = self.store.readiness()
        finally:
            store_module.MIN_FREE_BYTES = original
        self.assertFalse(report["ok"])
        self.assertFalse(report["disk"])

    def test_ready_endpoint_reports_degraded_without_internals(self):
        with sqlite3.connect(self.path) as con:
            con.execute("DELETE FROM world")
        handler = make_handler(self.store)
        handler.log_message = lambda *args, **kwargs: None
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            connection.request("GET", ADMIN + "/api/v1/health/ready")
            response = connection.getresponse()
            body = json.loads(response.read())
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(response.status, 503)
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["checks"], {"world": False, "schema": True, "disk": True})
        for leaked in ("storage", "dbBytes", "freeBytes", "schemaVersion", "path"):
            self.assertNotIn(leaked, body)


class StorageHygieneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "hygiene.sqlite3")
        self.store = Store(self.path)

    def test_world_ops_has_a_created_at_index_for_pruning(self):
        with sqlite3.connect(self.path) as con:
            names = {
                row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'world_ops'"
                )
            }
        self.assertIn("world_ops_created_at", names)

    def test_expired_ops_rows_are_pruned_on_every_op_write(self):
        now = time.time()
        with sqlite3.connect(self.path) as con:
            con.execute(
                "INSERT INTO world_ops (op_id, created_at) VALUES (?, ?)",
                ("op_expired_01", now - store_module.OP_TTL_SECONDS - 1),
            )
        self.store.world(now, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id="op_fresh_01")
        with sqlite3.connect(self.path) as con:
            rows = {row[0] for row in con.execute("SELECT op_id FROM world_ops")}
        self.assertNotIn("op_expired_01", rows)
        self.assertIn("op_fresh_01", rows)


class ConnectionHygieneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(os.path.join(self.tmp.name, "conn.sqlite3"))

    def test_handler_and_server_bound_themselves(self):
        self.assertGreater(Handler.timeout, 0)
        self.assertTrue(Server.daemon_threads)
        self.assertGreaterEqual(Server.request_queue_size, 128)

    def test_silent_connection_is_closed_after_the_read_timeout(self):
        handler = make_handler(self.store)
        handler.log_message = lambda *args, **kwargs: None
        handler.timeout = 0.5
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = socket.create_connection(server.server_address, timeout=5)
            client.settimeout(5)
            started = time.monotonic()
            received = client.recv(1)
            elapsed = time.monotonic() - started
            client.close()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(received, b"")
        self.assertLess(elapsed, 4.0)


if __name__ == "__main__":
    unittest.main()
