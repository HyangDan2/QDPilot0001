"""SQLite store behaviour: shared world, migration, events, idempotency."""

import json
import os
import sqlite3
import tempfile
import threading
import unittest

from server import economy
from server.store import Store

NOW = 1_700_000_000.0
OP = "op_aaaaaaaa"


def legacy_state(total_lifetime):
    state = economy.initial_state(NOW)
    state["total_lifetime"] = total_lifetime
    state["requests"] = 500.0
    state["servers"] = {"cache": 3}
    return state


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "game.sqlite3")

    def open(self):
        return Store(self.path)

    def test_first_world_seeds_the_shared_start(self):
        result = self.open().world(NOW)
        self.assertEqual(result.state["requests"], 25.0)
        self.assertEqual(result.state["servers"], {"cache": 1})
        self.assertIn("first_server", result.state["achievements"])
        self.assertEqual(result.duplicate, False)
        self.assertIsNone(result.message)
        self.assertEqual(result.events[0]["kind"], "achievement")

    def test_legacy_progress_is_migrated_from_the_best_player_once(self):
        with sqlite3.connect(self.path) as con:
            con.execute(
                "CREATE TABLE players (username TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at REAL NOT NULL)"
            )
            for name, total in (("small", 50.0), ("best", 5000.0), ("middle", 900.0)):
                con.execute(
                    "INSERT INTO players VALUES (?, ?, ?)",
                    (name, json.dumps(legacy_state(total)), NOW),
                )
        store = self.open()
        result = store.world(NOW)
        self.assertEqual(result.state["total_lifetime"], 5000.0)
        self.assertEqual(result.state["servers"], {"cache": 3})

        store.world(NOW + 5, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP)
        reopened = Store(self.path).world(NOW + 10)
        self.assertNotEqual(reopened.state["servers"], {"cache": 3})
        with sqlite3.connect(self.path) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM players").fetchone()[0], 3)

    def test_world_advances_with_elapsed_time(self):
        store = self.open()
        first = store.world(NOW)
        second = store.world(NOW + 100)
        self.assertAlmostEqual(second.away_seconds, 100.0)
        self.assertGreater(second.gains["requests"], 0)
        self.assertGreater(second.state["requests"], first.state["requests"])

    def test_every_actor_sees_the_same_world(self):
        store = self.open()
        store.world(NOW, actor="alice")
        store.world(NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP, actor="alice")
        bob = store.world(NOW + 2, actor="bob")
        self.assertEqual(bob.state["servers"], {"cache": 2})

    def test_action_is_idempotent_per_op_id(self):
        store = self.open()
        store.world(NOW)
        first = store.world(
            NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP, actor="hd2service",
        )
        self.assertFalse(first.duplicate)
        self.assertEqual(first.state["servers"], {"cache": 2})
        self.assertIsNotNone(first.message)
        self.assertIn("install", [event["kind"] for event in first.events])

        second = store.world(
            NOW + 2, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP, actor="hd2service",
        )
        self.assertTrue(second.duplicate)
        self.assertEqual(second.state["servers"], {"cache": 2})
        self.assertIsNone(second.message)
        self.assertGreaterEqual(second.state["requests"], first.state["requests"])

    def test_failed_action_rolls_back_and_op_is_not_reserved(self):
        store = self.open()
        store.world(NOW)
        action = {"type": "buyUpgrade", "id": "network"}
        with self.assertRaises(ValueError):
            store.world(NOW + 1, action=action, op_id=OP, actor="hd2service")
        saved = store.world(NOW + 2)
        self.assertGreaterEqual(saved.state["requests"], 25.0)

        with self.assertRaises(ValueError):
            store.world(NOW + 3, action=action, op_id=OP, actor="hd2service")

    def test_level_up_event_and_cursor(self):
        con = sqlite3.connect(self.path)
        self.addCleanup(con.close)
        con.execute("CREATE TABLE players (username TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at REAL NOT NULL)")
        con.execute(
            "INSERT INTO players VALUES ('hd2service', ?, ?)",
            (json.dumps(legacy_state(9_999.5)), NOW),
        )
        con.commit()
        store = Store(self.path)
        first = store.world(NOW)
        second = store.world(NOW + 20)
        kinds = [event["kind"] for event in second.events]
        self.assertIn("levelup", kinds)
        self.assertEqual(second.gains["level_from"], 1)
        self.assertEqual(second.gains["level_to"], 2)
        self.assertGreater(first.cursor, -1)

        later = store.world(NOW + 21, since=second.cursor)
        self.assertEqual(later.events, [])

    def test_reset_action_restores_the_run_and_keeps_meta(self):
        store = self.open()
        store.world(NOW)
        store.world(NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP, actor="hd2service")
        before = store.world(NOW + 2).state
        result = store.world(NOW + 2, action={"type": "reset"}, op_id="op_reset_01", actor="hd2service")
        self.assertEqual(result.state["servers"], {"cache": 1})
        self.assertEqual(result.state["tokens"], before["tokens"])
        self.assertEqual(result.state["total_lifetime"], before["total_lifetime"])
        self.assertEqual(result.state["reboots"], before["reboots"])
        self.assertEqual(result.state["achievements"], before["achievements"])
        self.assertIn("reset", [event["kind"] for event in result.events])

    def test_hard_reset_backs_up_and_audits(self):
        backup_dir = os.path.join(self.tmp.name, "backups")
        store = Store(self.path, backup_dir=backup_dir)
        store.world(NOW)
        store.world(NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP, actor="hd2service")
        populated = store.world(NOW + 2).state
        self.assertGreater(populated["total_lifetime"], 0)
        result = store.hard_reset(NOW + 3, "op_hard_01", actor="overlord")
        self.assertEqual(result.state["tokens"], 0)
        self.assertEqual(result.state["total_lifetime"], 0.0)
        self.assertEqual(result.state["reboots"], 0)
        self.assertEqual(result.state["servers"], {"cache": 1})
        self.assertIn("reset", [event["kind"] for event in result.events])
        backup = result.details["backup"]
        self.assertTrue(os.path.isfile(backup))
        with sqlite3.connect(backup) as con:
            self.assertEqual(con.execute("PRAGMA quick_check").fetchone()[0], "ok")
            state = json.loads(con.execute("SELECT state_json FROM world WHERE id = 1").fetchone()[0])
        self.assertGreater(state["total_lifetime"], 0)
        with sqlite3.connect(self.path) as con:
            row = con.execute("SELECT actor, action, detail_json FROM audit_log").fetchall()
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0][0], "overlord")
        self.assertEqual(row[0][1], "hardReset")
        detail = json.loads(row[0][2])
        self.assertEqual(detail["backup"], backup)
        self.assertEqual(detail["op_id"], "op_hard_01")
        self.assertGreater(detail["total_lifetime"], 0)

    def test_hard_reset_retry_does_not_reapply_or_rebackup(self):
        backup_dir = os.path.join(self.tmp.name, "backups")
        store = Store(self.path, backup_dir=backup_dir)
        store.world(NOW)
        first = store.hard_reset(NOW + 1, "op_hard_02", actor="overlord")
        second = store.hard_reset(NOW + 2, "op_hard_02", actor="overlord")
        self.assertTrue(second.duplicate)
        self.assertEqual(len(os.listdir(backup_dir)), 1)
        self.assertEqual(second.state["tokens"], 0)
        self.assertLess(second.state["total_lifetime"], 1.0)

    def test_stale_read_does_not_regress_last_sim_at(self):
        store = self.open()
        store.world(NOW)
        store.world(NOW + 100)
        stale = store.world(NOW + 50)
        self.assertEqual(stale.gains["seconds"], 0.0)
        fresh = store.world(NOW + 100)
        self.assertEqual(fresh.gains["seconds"], 0.0)

    def test_clock_anomaly_never_double_grants(self):
        store = self.open()
        store.world(NOW)
        spikes = store.world(NOW + economy.MAX_SIM_SECONDS * 2)
        self.assertEqual(spikes.gains["clock"], "clamped")
        settled = spikes.state["total_lifetime"]
        corrected = store.world(NOW + 1)
        self.assertEqual(corrected.gains["clock"], "resync")
        self.assertEqual(corrected.state["total_lifetime"], settled)

    def test_concurrent_worlds_settle_the_interval_once(self):
        store = self.open()
        seeded = store.world(NOW)
        copy = json.loads(json.dumps(seeded.state))
        economy.advance(copy, NOW + 100)
        expected = copy["total_lifetime"]

        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            results.append(store.world(NOW + 100))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 8)
        for result in results:
            self.assertAlmostEqual(result.state["total_lifetime"], expected, places=6)
        self.assertEqual(store.world(NOW + 100).gains["seconds"], 0.0)

    def test_world_reads_the_clock_inside_the_transaction(self):
        store = self.open()
        store.world(NOW)
        result = store.world()
        self.assertGreater(result.now, NOW)
        self.assertGreaterEqual(result.state["last_sim_at"], NOW)
        self.assertEqual(result.gains["seconds"], result.now - NOW)

    def test_corrupt_world_state_is_rejected(self):
        store = self.open()
        store.world(NOW)
        with sqlite3.connect(self.path) as con:
            con.execute("UPDATE world SET state_json = ? WHERE id = 1", ("{not json",))
        with self.assertRaises(ValueError):
            store.world(NOW + 10)

    def test_state_json_round_trips(self):
        store = self.open()
        store.world(NOW)
        with sqlite3.connect(self.path) as con:
            raw = con.execute("SELECT state_json FROM world WHERE id = 1").fetchone()[0]
        payload = json.loads(raw)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["servers"], {"cache": 1})


if __name__ == "__main__":
    unittest.main()
