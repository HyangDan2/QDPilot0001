"""WO-01: the hard reset is exclusive from its backup through its commit."""

import os
import sqlite3
import tempfile
import threading
import time
import unittest

from server.store import Store, WorldBusy

NOW = 1_700_000_000.0
OP = "op_hard_aaaa"


class HardResetExclusivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "game.sqlite3")
        self.backups = os.path.join(self.tmp.name, "backups")
        self.store = Store(self.path, backup_dir=self.backups)
        self.store.world(NOW)
        self.store.world(
            NOW + 1,
            action={"type": "buyServer", "tier": "cache", "qty": 1},
            op_id=OP,
            actor="admiral",
        )

    def pre_hardreset_backups(self):
        return sorted(
            name for name in os.listdir(self.backups)
            if name.startswith("idle-ops-pre-hardreset-")
        )

    def test_world_writes_wait_until_the_reset_commits(self):
        started = threading.Event()
        release = threading.Event()
        original = self.store.backup

        def slow_backup(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(10))
            return original(*args, **kwargs)

        self.store.backup = slow_backup
        reset_result = {}
        buy_result = {}

        def reset():
            reset_result["value"] = self.store.hard_reset(NOW + 2, "op_hard_lock", actor="admiral")

        def buy():
            buy_result["value"] = self.store.world(
                NOW + 3,
                action={"type": "buyServer", "tier": "cache", "qty": 1},
                op_id="op_buy_during_reset",
                actor="operator",
            )

        reset_thread = threading.Thread(target=reset)
        reset_thread.start()
        self.assertTrue(started.wait(5))
        buy_thread = threading.Thread(target=buy)
        buy_thread.start()
        time.sleep(0.25)
        self.assertTrue(buy_thread.is_alive(), "a buy must not commit during the reset backup")

        release.set()
        reset_thread.join(10)
        buy_thread.join(10)
        self.assertFalse(reset_thread.is_alive())
        self.assertFalse(buy_thread.is_alive())

        self.assertEqual(reset_result["value"].state["tokens"], 0)
        self.assertEqual(reset_result["value"].state["total_lifetime"], 0.0)
        # The purchase was serialised after the reset, so it applies to the
        # fresh world instead of being wiped by it.
        self.assertEqual(buy_result["value"].state["servers"], {"cache": 2})
        self.assertGreater(buy_result["value"].state["total_lifetime"], 0.0)

    def test_backup_copies_the_exact_state_the_reset_replaces(self):
        populated = self.store.world(NOW + 2).state
        result = self.store.hard_reset(NOW + 3, "op_hard_exact", actor="admiral")
        with sqlite3.connect(result.details["backup"]) as con:
            row = con.execute("SELECT state_json FROM world WHERE id = 1").fetchone()[0]
        import json
        backup_state = json.loads(row)
        expected = self.store.world(NOW + 3).state
        # The settle before the backup advanced to NOW + 3; the reset zeroed the
        # live world, so compare against the audit snapshot instead.
        self.assertGreater(populated["total_lifetime"], 0)
        self.assertGreater(backup_state["total_lifetime"], 0)
        self.assertLessEqual(backup_state["last_sim_at"], NOW + 3)
        self.assertLessEqual(backup_state["last_sim_at"], expected["last_sim_at"])
        with sqlite3.connect(self.path) as con:
            detail = json.loads(con.execute("SELECT detail_json FROM audit_log").fetchone()[0])
        self.assertAlmostEqual(detail["total_lifetime"], backup_state["total_lifetime"], places=6)

    def test_simultaneous_resets_are_serialised_with_one_backup_each(self):
        second = Store(self.path, backup_dir=self.backups)
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def reset(store, moment, op_id):
            try:
                barrier.wait()
                results.append(store.hard_reset(moment, op_id, actor="admiral"))
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [
            threading.Thread(target=reset, args=(self.store, NOW + 4, "op_hard_race_a")),
            threading.Thread(target=reset, args=(second, NOW + 5, "op_hard_race_b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(len(self.pre_hardreset_backups()), 2)
        final = self.store.world(NOW + 6)
        self.assertEqual(final.state["tokens"], 0)
        self.assertEqual(final.state["servers"], {"cache": 1})
        with sqlite3.connect(self.path) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 2)

    def test_a_failed_backup_aborts_before_touching_the_world(self):
        before = self.store.world(NOW + 2).state

        def broken_backup(*args, **kwargs):
            raise sqlite3.OperationalError("disk I/O error")

        self.store.backup = broken_backup
        with self.assertRaises(sqlite3.OperationalError):
            self.store.hard_reset(NOW + 3, "op_hard_fail", actor="admiral")
        after = self.store.world(NOW + 4).state
        self.assertEqual(after["tokens"], before["tokens"])
        self.assertGreater(after["total_lifetime"], 0)
        self.assertEqual(after["servers"], before["servers"])
        with sqlite3.connect(self.path) as con:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM world_ops WHERE op_id = ?", ("op_hard_fail",)).fetchone()[0],
                0,
            )
            self.assertEqual(con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 0)

        self.store.backup = Store.backup.__get__(self.store, Store)
        retried = self.store.hard_reset(NOW + 5, "op_hard_fail", actor="admiral")
        self.assertFalse(retried.duplicate)
        self.assertEqual(retried.state["tokens"], 0)
        self.assertEqual(len(self.pre_hardreset_backups()), 1)

    def test_retry_after_success_replays_without_another_backup(self):
        first = self.store.hard_reset(NOW + 3, "op_hard_retry", actor="admiral")
        second = self.store.hard_reset(NOW + 4, "op_hard_retry", actor="admiral")
        self.assertTrue(second.duplicate)
        self.assertEqual(second.details["backup"], first.details["backup"])
        self.assertEqual(len(self.pre_hardreset_backups()), 1)

    def test_a_writer_that_exceeds_its_deadline_is_told_to_retry(self):
        started = threading.Event()
        release = threading.Event()

        def slow_backup(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(10))
            raise sqlite3.OperationalError("stop here")

        self.store.backup = slow_backup

        def reset():
            with self.assertRaises(sqlite3.OperationalError):
                self.store.hard_reset(NOW + 2, "op_hard_busy", actor="admiral")

        thread = threading.Thread(target=reset)
        thread.start()
        self.assertTrue(started.wait(5))
        with self.assertRaises(WorldBusy):
            self.store.world(NOW + 3, actor="operator", gated=True, lock_timeout=0.1)
        release.set()
        thread.join(10)
        self.assertFalse(thread.is_alive())

    def test_service_lock_is_probed_for_restores(self):
        lock = self.store.acquire_service_lock()
        try:
            self.assertTrue(Store.service_running(self.path))
            other = Store(self.path)
            with self.assertRaises(WorldBusy):
                other.acquire_service_lock()
        finally:
            lock.close()
        self.assertFalse(Store.service_running(self.path))


if __name__ == "__main__":
    unittest.main()
