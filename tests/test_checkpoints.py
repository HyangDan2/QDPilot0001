"""WO-05: per-admin observation checkpoints are display-only."""

import os
import sqlite3
import tempfile
import unittest

from server import economy
from server.store import SCHEMA_VERSION, Store

NOW = 1_700_000_000.0


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "checkpoints.sqlite3")
        self.store = Store(self.path)

    def row(self, user):
        con = sqlite3.connect(self.path)
        try:
            con.row_factory = sqlite3.Row
            return con.execute("SELECT * FROM admin_seen WHERE username = ?", (user,)).fetchone()
        finally:
            con.close()

    def test_first_check_has_no_prior_observation(self):
        first = self.store.world(NOW, actor="alice")
        stamps = first.timestamps
        self.assertIsNone(stamps["observedRequests"])
        self.assertIsNone(stamps["observedLevel"])
        self.assertIsNone(stamps["observedGeneration"])
        self.assertEqual(stamps["worldGeneration"], 0)
        self.assertFalse(stamps["observedFromEarlierWorld"])

    def test_next_check_reports_what_the_admin_last_saw(self):
        first = self.store.world(NOW, actor="alice")
        later = self.store.world(NOW + 100, actor="alice")
        stamps = later.timestamps
        self.assertAlmostEqual(stamps["observedRequests"], first.state["requests"], places=6)
        self.assertEqual(stamps["observedLevel"], economy.derived(first.state)["level"])
        self.assertEqual(stamps["observedGeneration"], 0)
        self.assertEqual(stamps["worldGeneration"], 0)
        self.assertFalse(stamps["observedFromEarlierWorld"])
        self.assertAlmostEqual(stamps["awaySeconds"], 100.0)

        row = self.row("alice")
        self.assertAlmostEqual(row["observed_requests"], later.state["requests"], places=6)
        self.assertEqual(row["observed_level"], economy.derived(later.state)["level"])
        self.assertEqual(row["world_generation"], 0)

    def test_absence_is_time_only_not_a_production_diff(self):
        self.store.world(NOW, actor="alice")
        stamps = self.store.world(NOW + 10 * 365 * 86400, actor="alice").timestamps
        self.assertGreater(stamps["awaySeconds"], 300_000_000)
        for key in stamps:
            self.assertNotIn("diff", key)
            self.assertNotIn("whileAway", key)
            self.assertNotIn("owed", key)
        self.assertEqual(
            set(stamps),
            {
                "worldSettledAt", "adminLastSeen", "adminLastCheck", "lastRewardAt",
                "awaySeconds", "observedRequests", "observedLevel",
                "observedGeneration", "worldGeneration", "observedFromEarlierWorld",
            },
        )

    def test_checkpoints_are_independent_per_admin(self):
        self.store.world(NOW, actor="alice")
        self.store.world(NOW + 300, actor="alice")
        bob = self.store.world(NOW + 500, actor="bob")
        self.assertIsNone(bob.timestamps["observedRequests"])
        self.assertEqual(bob.timestamps["awaySeconds"], 0.0)
        alice_row = self.row("alice")
        bob_row = self.row("bob")
        self.assertEqual(alice_row["last_seen"], NOW + 300)
        self.assertEqual(bob_row["last_seen"], NOW + 500)
        self.assertEqual(alice_row["checks"], 2)
        self.assertEqual(bob_row["checks"], 1)

        # Another admin acting does not move alice's last-seen checkpoint.
        alice_seen = alice_row["last_seen"]
        self.store.world(NOW + 900, actor="bob", action={"type": "buyServer", "tier": "cache", "qty": 1},
                         op_id="op_bob_00001")
        self.assertEqual(self.row("alice")["last_seen"], alice_seen)

    def test_hard_reset_bumps_generation_and_marks_old_observation(self):
        before = self.store.world(NOW, actor="alice")
        reset = self.store.hard_reset(now=NOW + 10, op_id="op_reset_gen_01", actor="top")
        self.assertEqual(reset.world_generation, 1)
        after = self.store.world(NOW + 20, actor="alice")
        stamps = after.timestamps
        self.assertEqual(stamps["worldGeneration"], 1)
        self.assertEqual(stamps["observedGeneration"], 0)
        self.assertTrue(stamps["observedFromEarlierWorld"])
        self.assertAlmostEqual(stamps["observedRequests"], before.state["requests"], places=6)
        with sqlite3.connect(self.path) as con:
            self.assertEqual(con.execute("SELECT generation FROM world WHERE id = 1").fetchone()[0], 1)
        self.assertEqual(self.row("alice")["world_generation"], 1)
        # The marker clears once the admin has observed the new world.
        next_check = self.store.world(NOW + 30, actor="alice")
        self.assertFalse(next_check.timestamps["observedFromEarlierWorld"])

    def test_retrying_a_hard_reset_does_not_bump_generation_twice(self):
        self.store.world(NOW, actor="alice")
        self.store.hard_reset(now=NOW + 10, op_id="op_reset_gen_02", actor="top")
        again = self.store.hard_reset(now=NOW + 20, op_id="op_reset_gen_02", actor="top")
        self.assertTrue(again.duplicate)
        self.assertEqual(again.world_generation, 1)
        with sqlite3.connect(self.path) as con:
            self.assertEqual(con.execute("SELECT generation FROM world WHERE id = 1").fetchone()[0], 1)

    def test_soft_reset_and_restart_keep_the_generation(self):
        self.store.world(NOW, actor="alice")
        self.store.hard_reset(now=NOW + 10, op_id="op_reset_gen_03", actor="top")
        self.store.world(NOW + 20, actor="alice", action={"type": "reset"}, op_id="op_soft_0001")
        reopened = Store(self.path)
        result = reopened.world(NOW + 30, actor="alice")
        self.assertEqual(result.world_generation, 1)
        self.assertEqual(result.timestamps["observedGeneration"], 1)

    def test_checking_in_never_pays_out(self):
        self.store.world(NOW, actor="alice")
        state = self.store.world(NOW + 60, actor="alice").state
        # A second store without an admin actor earns exactly the same time-based
        # production; the checkpoint itself adds nothing.
        other = Store(os.path.join(self.tmp.name, "anonymous.sqlite3"))
        other.world(NOW)
        anonymous = other.world(NOW + 60).state
        self.assertAlmostEqual(state["requests"], anonymous["requests"], places=6)
        self.assertEqual(state["tokens"], 0)
        self.assertEqual(state["reboots"], 0)


class SchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "legacy.sqlite3")

    def test_a_v2_database_is_upgraded_in_place(self):
        Store(self.path).world(NOW)
        con = sqlite3.connect(self.path)
        try:
            con.execute("ALTER TABLE world DROP COLUMN generation")
            con.execute("ALTER TABLE admin_seen DROP COLUMN observed_requests")
            con.execute("ALTER TABLE admin_seen DROP COLUMN observed_level")
            con.execute("ALTER TABLE admin_seen DROP COLUMN world_generation")
            con.execute("PRAGMA user_version = 2")
            con.commit()
        finally:
            con.close()

        store = Store(self.path)
        result = store.world(NOW + 5, actor="alice")
        self.assertEqual(result.world_generation, 0)
        con = sqlite3.connect(self.path)
        try:
            self.assertEqual(int(con.execute("PRAGMA user_version").fetchone()[0]), SCHEMA_VERSION)
            columns = {row[1] for row in con.execute("PRAGMA table_info(admin_seen)")}
            self.assertLessEqual(
                {"observed_requests", "observed_level", "world_generation"}, columns
            )
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
