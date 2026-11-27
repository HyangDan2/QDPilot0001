"""WO-06: world revision, consistent snapshots and cursor recovery."""

import contextlib
import io
import os
import sqlite3
import tempfile
import time
import unittest

from server import backup
from server.store import SnapshotCache, Store, WorldUnavailable

NOW = 1_700_000_000.0
OP = "op_revision_01"


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "revision.sqlite3")
        self.store = Store(self.path)

    def stored_revision(self):
        con = sqlite3.connect(self.path)
        try:
            return con.execute("SELECT generation, revision FROM world WHERE id = 1").fetchone()
        finally:
            con.close()

    def test_every_committed_write_bumps_the_revision(self):
        first = self.store.world(NOW)
        self.assertEqual(first.revision, 1)
        second = self.store.world(NOW + 1)
        self.assertEqual(second.revision, 2)
        acted = self.store.world(
            NOW + 2, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP
        )
        self.assertEqual(acted.revision, 3)
        self.assertEqual(self.stored_revision(), (0, 3))

    def test_revision_survives_a_restart(self):
        self.store.world(NOW)
        self.store.world(NOW + 1)
        reopened = Store(self.path)
        self.assertEqual(reopened.world(NOW + 2).revision, 3)

    def test_public_reads_do_not_bump_the_revision(self):
        self.store.world(NOW)
        cache = SnapshotCache(self.store)
        first = cache.latest(0)
        second = cache.latest(0)
        self.assertEqual(first.revision, second.revision)
        self.assertEqual(self.stored_revision()[1], 1)

    def test_hard_reset_keeps_revision_monotonic_and_bumps_generation(self):
        self.store.world(NOW)
        reset = self.store.hard_reset(now=NOW + 10, op_id="op_rev_reset_1", actor="top")
        self.assertGreater(reset.revision, 1)
        self.assertEqual(reset.world_generation, 1)
        self.assertEqual(self.stored_revision(), (1, reset.revision))


class SnapshotConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "snapshot.sqlite3")
        self.store = Store(self.path)
        self.store.world(NOW)

    def test_events_are_capped_at_the_state_snapshot(self):
        cache = SnapshotCache(self.store, ttl=60)
        base = cache.latest(None)
        self.assertEqual(base.events, [])
        other = Store(self.path)
        other.world(
            NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP
        )
        # Same TTL window: the cached state revision must not be paired with an
        # event appended after it.
        capped = cache.latest(None)
        self.assertEqual(capped.revision, base.revision)
        self.assertEqual(capped.state["servers"], {"cache": 1})
        self.assertEqual(capped.events, [])
        self.assertEqual(capped.cursor, base.cursor)

    def test_snapshot_picks_up_cross_instance_writes_after_the_ttl(self):
        cache = SnapshotCache(self.store, ttl=0.05)
        first = cache.latest(0)
        other = Store(self.path)
        other.world(NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            latest = cache.latest(0)
            if latest.state["servers"] == {"cache": 2}:
                break
            time.sleep(0.02)
        self.assertEqual(latest.state["servers"], {"cache": 2})
        self.assertGreater(latest.revision, first.revision)

    def test_state_and_events_share_the_same_cursor_ceiling(self):
        cache = SnapshotCache(self.store, ttl=60)
        cache.latest(None)
        self.store.world(NOW + 1, action={"type": "reset"}, op_id="op_rev_reset_2")
        capped = cache.latest(None)
        self.assertEqual(capped.events, [])
        follow = cache.latest(capped.cursor)
        self.assertEqual(follow.events, [])
        self.assertFalse(follow.cursor_reset)

    def test_last_good_snapshot_is_served_when_the_world_row_vanishes(self):
        cache = SnapshotCache(self.store, ttl=0.01)
        base = cache.latest(0)
        con = sqlite3.connect(self.path)
        try:
            con.execute("DELETE FROM world WHERE id = 1")
            con.commit()
        finally:
            con.close()
        time.sleep(0.02)
        stale = cache.latest(0)
        self.assertTrue(stale.stale)
        self.assertEqual(stale.revision, base.revision)
        self.assertEqual(stale.state["requests"], base.state["requests"])

    def test_a_failed_first_read_never_fabricates_a_world(self):
        class Missing:
            path = os.path.join(self.tmp.name, "missing.sqlite3")
            generation = 0

        cache = SnapshotCache(Missing())
        with self.assertRaises(WorldUnavailable):
            cache.latest(0)

    def test_cursor_from_before_a_restore_is_reset(self):
        cache = SnapshotCache(self.store)
        for index in range(3):
            self.store.world(
                NOW + 3600 * (index + 1),
                action={"type": "buyServer", "tier": "cache", "qty": 1},
                op_id=f"op_revision_{index:02d}",
            )
        before = cache.latest(0)
        self.assertGreater(before.cursor, 0)
        snapshot = self.store.backup("manual", now=NOW + 20_000, keep=0)
        for index in range(3):
            self.store.world(
                NOW + 30_000 + 3600 * index,
                action={"type": "buyUpgrade", "id": "network"},
                op_id=f"op_revision_up_{index:02d}",
            )
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            backup.restore(self.path, snapshot)
        restored = SnapshotCache(Store(self.path))
        after = restored.latest(0)
        high_cursor = after.cursor + 50
        recovered = restored.latest(high_cursor)
        self.assertTrue(recovered.cursor_reset)
        self.assertEqual(recovered.events, [])
        self.assertLessEqual(recovered.cursor, after.cursor)
        follow = restored.latest(0)
        self.assertFalse(follow.cursor_reset)
        self.assertGreater(follow.cursor, 0)


if __name__ == "__main__":
    unittest.main()
