"""Release-gate regressions REG-01..REG-20 for the Phase 2.1 gate.

Each test maps to one row of ``docs/PHASE2_1_RELEASE_GATE.md``. The suite is
intentionally independent of the detailed module suites: it asserts the
invariants the release decision depends on, using temporary databases only.
"""

import copy
import http.client
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from server import economy
from server.api import Handler, Server, make_handler
from server.store import EVENT_RETENTION, SCHEMA_VERSION, SnapshotCache, Store

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_700_000_000.0


class GateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "gate.sqlite3")

    def store(self):
        return Store(self.path)


class ReadinessGate(GateTestCase):
    def test_reg_01_readiness_requires_world_schema_and_disk(self):
        report = self.store().readiness()
        self.assertTrue(report["ok"])
        self.assertEqual((report["world"], report["schema"], report["disk"]), (True, True, True))

    def test_reg_02_missing_world_row_is_degraded(self):
        store = self.store()
        with sqlite3.connect(self.path) as con:
            con.execute("DELETE FROM world")
        report = store.readiness()
        self.assertFalse(report["ok"])
        self.assertFalse(report["world"])

    def test_reg_03_schema_mismatch_is_degraded(self):
        store = self.store()
        with sqlite3.connect(self.path) as con:
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
        report = store.readiness()
        self.assertFalse(report["ok"])
        self.assertFalse(report["schema"])


class EconomyGate(unittest.TestCase):
    def test_reg_04_settlement_is_frequency_independent(self):
        state = economy.initial_state(NOW)
        once = copy.deepcopy(state)
        economy.advance(once, NOW + 3600)
        stepped = copy.deepcopy(state)
        for second in range(1, 3601):
            economy.advance(stepped, NOW + second)
        self.assertAlmostEqual(once["total_lifetime"], stepped["total_lifetime"], delta=1e-6)
        self.assertEqual(once["servers"], stepped["servers"])
        self.assertEqual(once["tokens"], stepped["tokens"])

    def test_reg_05_clock_rewind_never_grants_again(self):
        state = economy.initial_state(NOW)
        economy.advance(state, NOW + 600)
        before = state["total_lifetime"]
        gains = economy.advance(state, NOW + 300)
        self.assertEqual(gains["seconds"], 0.0)
        self.assertEqual(state["total_lifetime"], before)
        self.assertEqual(state["last_sim_at"], NOW + 600)

    def test_reg_06_reset_keeps_meta_progress(self):
        state = economy.initial_state(NOW)
        economy.advance(state, NOW + 7200)
        economy.apply_action(state, {"type": "buyServer", "tier": "cache", "qty": 1}, NOW + 7201)
        total = state["total_lifetime"]
        economy.apply_action(state, {"type": "reset"}, NOW + 7202)
        self.assertEqual(state["total_lifetime"], total)
        self.assertEqual(state["servers"]["cache"], 1)
        self.assertEqual(state["run_lifetime"], 0)


class ResetGate(GateTestCase):
    def test_reg_07_hard_reset_zeroes_and_writes_pre_backup(self):
        store = self.store()
        store.world(NOW)
        store.world(
            NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id="op_gate_buy_01",
        )
        result = store.hard_reset(now=NOW + 2, op_id="op_gate_hard_01", actor="gate")
        self.assertEqual(result.state["servers"], {"cache": 1})
        self.assertEqual(result.state["tokens"], 0)
        backup = result.details["backup"]
        self.assertTrue(os.path.isfile(backup))
        self.assertEqual(Store.check_sqlite(backup), "ok")


class IdempotencyGate(GateTestCase):
    def setUp(self):
        super().setUp()
        self.store_instance = self.store()
        self.store_instance.world(NOW)

    def test_reg_08_same_op_id_replays_one_result(self):
        action = {"type": "buyServer", "tier": "cache", "qty": 1}
        first = self.store_instance.world(NOW + 1, action=action, op_id="op_gate_idem_01")
        second = self.store_instance.world(NOW + 2, action=action, op_id="op_gate_idem_01")
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(second.state["servers"], first.state["servers"])
        self.assertEqual(second.state["servers"], {"cache": 2})

    def test_reg_09_same_op_id_with_other_action_conflicts(self):
        from server.store import OpConflict

        self.store_instance.world(
            NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id="op_gate_idem_02",
        )
        with self.assertRaises(OpConflict):
            self.store_instance.world(
                NOW + 2, action={"type": "buyServer", "tier": "cache", "qty": 10}, op_id="op_gate_idem_02",
            )


class EventGate(GateTestCase):
    def test_reg_10_event_retention_is_bounded(self):
        store = self.store()
        store.world(NOW)
        with sqlite3.connect(self.path) as con:
            con.executemany(
                "INSERT INTO world_events (created_at, kind, actor, payload_json) VALUES (?, 'install', '', '{}')",
                [(NOW,)] * (EVENT_RETENTION + 50),
            )
        store.world(NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id="op_gate_evt_01")
        with sqlite3.connect(self.path) as con:
            count = con.execute("SELECT COUNT(*) FROM world_events").fetchone()[0]
        self.assertLessEqual(count, EVENT_RETENTION + 1)

    def test_reg_11_cursor_past_newest_resets_cleanly(self):
        store = self.store()
        store.world(NOW, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id="op_gate_cur_01")
        result = store.world(NOW + 1, since=10**9)
        self.assertTrue(result.cursor_reset)
        self.assertGreater(result.cursor, 0)


class StorageGate(GateTestCase):
    def test_reg_12_backup_is_verified_and_restorable_input(self):
        store = self.store()
        store.world(NOW)
        snapshot = store.backup("manual", now=NOW, keep=0)
        self.assertEqual(Store.check_sqlite(snapshot), "ok")
        copy = Store(os.path.join(self.tmp.name, "from-backup.sqlite3"))
        source = sqlite3.connect(snapshot)
        target = sqlite3.connect(copy.path)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
        self.assertEqual(copy.world(NOW + 60).state["servers"], {"cache": 1})

    def test_reg_13_public_snapshot_invalidates_after_an_action(self):
        store = self.store()
        store.world(NOW)
        cache = SnapshotCache(store, ttl=60)
        before = cache.latest()
        store.world(NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id="op_gate_snap_01")
        after = cache.latest()
        self.assertGreater(after.revision, before.revision)
        self.assertEqual(after.state["servers"], {"cache": 2})

    def test_reg_14_public_reads_do_not_write_the_database(self):
        store = self.store()
        store.world(NOW)
        with sqlite3.connect(self.path) as con:
            updated_before = con.execute("SELECT updated_at FROM world").fetchone()[0]
        cache = SnapshotCache(store, ttl=0)
        for _ in range(3):
            cache.latest()
        with sqlite3.connect(self.path) as con:
            updated_after = con.execute("SELECT updated_at FROM world").fetchone()[0]
        self.assertEqual(updated_before, updated_after)


class AssetGate(unittest.TestCase):
    def test_reg_15_manifest_is_v3_right_facing_with_attack_hit(self):
        manifest = json.loads(
            (ROOT / "static/public/assets/sprites/manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["version"], 3)
        self.assertEqual(manifest["facing"], "right")
        self.assertEqual(manifest["states"]["attack"]["hits"], [{"frame": 6, "type": "strike"}])
        for state in manifest["states"].values():
            self.assertIsNone(state["source"])
            self.assertEqual(state["fallback"]["facing"], "right")

    def test_reg_16_fallback_assets_exist_and_are_png(self):
        sprites = ROOT / "static/public/assets/sprites"
        for name in ("hyangdani-idle.png", "hyangdani-run.png", "hyangdani-strike.png",
                     "bug-alive.png", "bug-hit.png"):
            data = (sprites / name).read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n", name)


class OpsGate(GateTestCase):
    def test_reg_17_expired_operation_ids_are_pruned(self):
        store = self.store()
        store.world(NOW)
        with sqlite3.connect(self.path) as con:
            con.execute(
                "INSERT INTO world_ops (op_id, created_at) VALUES (?, ?)",
                ("op_gate_old_01", NOW - 31 * 86400),
            )
        store.world(NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id="op_gate_new_01")
        with sqlite3.connect(self.path) as con:
            rows = {row[0] for row in con.execute("SELECT op_id FROM world_ops")}
        self.assertNotIn("op_gate_old_01", rows)
        self.assertIn("op_gate_new_01", rows)

    def test_reg_18_world_ops_created_at_index_exists(self):
        self.store()
        with sqlite3.connect(self.path) as con:
            names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn("world_ops_created_at", names)

    def test_reg_19_handler_and_server_bound_connections(self):
        self.assertGreater(Handler.timeout, 0)
        self.assertTrue(Server.daemon_threads)
        self.assertGreaterEqual(Server.request_queue_size, 128)

    def test_reg_20_public_readiness_exposes_only_booleans(self):
        store = self.store()
        handler = make_handler(store)
        handler.log_message = lambda *args, **kwargs: None
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            connection.request("GET", "/admin/idle-ops/api/v1/health/ready")
            response = connection.getresponse()
            body = json.loads(response.read())
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(response.status, 200)
        self.assertEqual(set(body), {"status", "service", "checks"})
        self.assertEqual(set(body["checks"]), {"world", "schema", "disk"})


if __name__ == "__main__":
    unittest.main()
