"""SQLite store behaviour: shared world, migration, events, idempotency."""

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest

from server import economy
from server.store import (
    BACKUP_KEEP,
    EVENT_LIMIT,
    EVENT_RETENTION,
    SCHEMA_VERSION,
    OpConflict,
    SchemaTooNew,
    SnapshotCache,
    Store,
)

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

    def seed_events(self, count):
        with sqlite3.connect(self.path) as con:
            for index in range(count):
                con.execute(
                    "INSERT INTO world_events (created_at, kind, actor, payload_json) VALUES (?, ?, ?, ?)",
                    (NOW + index, "install", "admiral", json.dumps({"tier": "cache", "qty": 1})),
                )

    def max_event_id(self):
        with sqlite3.connect(self.path) as con:
            return int(con.execute("SELECT MAX(id) FROM world_events").fetchone()[0] or 0)

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

    def test_admin_timestamps_are_per_admin(self):
        store = self.open()
        store.world(NOW, actor="alice")
        alice = store.world(NOW + 100, actor="alice")
        self.assertAlmostEqual(alice.away_seconds, 100.0)
        self.assertEqual(alice.timestamps["adminLastSeen"], NOW)
        self.assertEqual(alice.timestamps["worldSettledAt"], NOW + 100)
        settled_total = alice.state["total_lifetime"]

        bob = store.world(NOW + 100, actor="bob")
        self.assertEqual(bob.away_seconds, 0.0)
        self.assertEqual(bob.gains["seconds"], 0.0)
        self.assertEqual(bob.state["total_lifetime"], settled_total)

        again = store.world(NOW + 105, actor="alice")
        self.assertAlmostEqual(again.away_seconds, 5.0)
        self.assertAlmostEqual(
            again.state["total_lifetime"] - bob.state["total_lifetime"],
            again.gains["requests"],
            places=6,
        )

    def test_every_actor_sees_the_same_world(self):
        store = self.open()
        store.world(NOW, actor="alice")
        store.world(NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP, actor="alice")
        bob = store.world(NOW + 2, actor="bob")
        self.assertEqual(bob.state["servers"], {"cache": 2})

    def test_public_events_are_ordered_and_resume_by_cursor(self):
        store = self.open()
        store.world(NOW)
        self.seed_events(5)
        cache = SnapshotCache(store)
        events, cursor, cursor_reset, gap = cache.read_events(0)
        ids = [event["id"] for event in events]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(cursor_reset)
        self.assertFalse(gap)

        again, next_cursor, _, _ = cache.read_events(cursor)
        self.assertEqual(again, [])
        self.assertEqual(next_cursor, cursor)

    def test_long_absence_returns_only_the_newest_window(self):
        store = self.open()
        store.world(NOW)
        self.seed_events(200)
        events, cursor, cursor_reset, gap = SnapshotCache(store).read_events(0)
        self.assertTrue(gap)
        self.assertFalse(cursor_reset)
        self.assertEqual(len(events), EVENT_LIMIT)
        self.assertEqual([event["id"] for event in events], sorted(event["id"] for event in events))
        self.assertEqual(cursor, events[-1]["id"])
        self.assertEqual(cursor, self.max_event_id())

    def test_cursor_past_the_newest_event_signals_reset(self):
        store = self.open()
        store.world(NOW)
        cache = SnapshotCache(store)
        events, cursor, _, _ = cache.read_events(0)
        self.assertTrue(events)
        with sqlite3.connect(self.path) as con:
            con.execute("DELETE FROM world_events")
        resumed, reset_cursor, cursor_reset, _ = cache.read_events(cursor)
        self.assertTrue(cursor_reset)
        self.assertEqual(resumed, [])
        self.assertEqual(reset_cursor, 0)

    def test_world_cursor_past_the_newest_event_signals_reset(self):
        store = self.open()
        first = store.world(NOW, since=0, actor="alice")
        self.assertFalse(first.cursor_reset)
        with sqlite3.connect(self.path) as con:
            con.execute("DELETE FROM world_events")
        resumed = store.world(NOW + 1, since=first.cursor + 5, actor="alice")
        self.assertTrue(resumed.cursor_reset)
        self.assertEqual(resumed.events, [])
        self.assertEqual(resumed.cursor, 0)

    def test_retention_prunes_old_events_on_write(self):
        store = self.open()
        store.world(NOW)
        self.seed_events(EVENT_RETENTION + 50)
        store.world(NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP)
        with sqlite3.connect(self.path) as con:
            count = int(con.execute("SELECT COUNT(*) FROM world_events").fetchone()[0])
        self.assertLessEqual(count, EVENT_RETENTION)

    def test_schema_version_is_recorded_and_newer_schemas_are_refused(self):
        store = self.open()
        with sqlite3.connect(self.path) as con:
            version = int(con.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(version, SCHEMA_VERSION)
        with sqlite3.connect(self.path) as con:
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        with self.assertRaises(SchemaTooNew):
            Store(self.path)

    def test_v1_database_upgrades_in_place_and_keeps_the_world(self):
        state = legacy_state(5000.0)
        with sqlite3.connect(self.path) as con:
            con.executescript(
                """
                CREATE TABLE world (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    state_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE world_ops (
                    op_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL
                ) WITHOUT ROWID;
                CREATE TABLE world_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    kind TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE players (
                    username TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE action_ops (
                    username TEXT NOT NULL,
                    op_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (username, op_id)
                ) WITHOUT ROWID;
                PRAGMA user_version = 1;
                """
            )
            con.execute("INSERT INTO world VALUES (1, ?, ?)", (json.dumps(state), NOW))
        store = self.open()
        result = store.world(NOW + 10)
        self.assertGreaterEqual(result.state["total_lifetime"], 5000.0)
        with sqlite3.connect(self.path) as con:
            self.assertEqual(int(con.execute("PRAGMA user_version").fetchone()[0]), SCHEMA_VERSION)
            self.assertTrue(
                {"action_json", "result_json"}
                <= {row[1] for row in con.execute("PRAGMA table_info(world_ops)")}
            )
            self.assertTrue(
                {"admin_seen", "audit_log"}
                <= {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            )

    def test_daily_backups_are_verified_and_pruned(self):
        store = self.open()
        store.world(NOW)
        for _ in range(BACKUP_KEEP + 3):
            store.backup("daily", now=NOW, keep=BACKUP_KEEP)
        names = sorted(
            name for name in os.listdir(store.backup_dir)
            if name.startswith("idle-ops-daily-") and name.endswith(".sqlite3")
        )
        self.assertEqual(len(names), BACKUP_KEEP)
        for name in names:
            self.assertEqual(Store.check_sqlite(os.path.join(store.backup_dir, name)), "ok")

    def test_restore_rolls_back_to_a_verified_snapshot(self):
        from server.backup import restore

        store = self.open()
        store.world(NOW)
        with sqlite3.connect(self.path) as con:
            state = json.loads(con.execute("SELECT state_json FROM world WHERE id = 1").fetchone()[0])
            state["requests"] = 10_000.0
            con.execute("UPDATE world SET state_json = ? WHERE id = 1", (json.dumps(state),))
        store.world(NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP)
        snapshot = store.backup("manual", now=NOW)
        store.world(NOW + 2, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP + "2")
        self.assertEqual(store.world(NOW + 3).state["servers"], {"cache": 3})

        with contextlib.redirect_stdout(io.StringIO()):
            restore(self.path, snapshot)
        restored = Store(self.path).world(NOW + 4)
        self.assertEqual(restored.state["servers"], {"cache": 2})

    def test_readiness_reports_storage_health(self):
        store = self.open()
        report = store.readiness()
        self.assertTrue(report["ok"])
        self.assertEqual(report["schemaVersion"], SCHEMA_VERSION)
        self.assertGreater(report["dbBytes"], 0)
        self.assertGreater(report["freeBytes"], 0)

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
        self.assertEqual(second.message, first.message)
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

    def test_repeating_an_operation_replays_the_recorded_result(self):
        store = self.open()
        store.world(NOW)
        action = {"type": "buyServer", "tier": "cache", "qty": 1}
        first = store.world(NOW + 1, action=action, op_id="op_idem_01")
        again = store.world(NOW + 2, action=action, op_id="op_idem_01")
        self.assertTrue(again.duplicate)
        self.assertEqual(again.message, first.message)
        self.assertEqual(again.details, first.details)
        self.assertEqual(again.state["servers"], {"cache": 2})

    def test_same_operation_id_with_another_action_conflicts(self):
        store = self.open()
        store.world(NOW)
        store.world(NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id="op_idem_02")
        with self.assertRaises(OpConflict):
            store.world(NOW + 2, action={"type": "buyServer", "tier": "cache", "qty": 10}, op_id="op_idem_02")
        self.assertEqual(store.world(NOW + 3).state["servers"], {"cache": 2})

    def test_legacy_operation_rows_are_migrated(self):
        with sqlite3.connect(self.path) as con:
            con.execute("CREATE TABLE world_ops (op_id TEXT PRIMARY KEY, created_at REAL NOT NULL) WITHOUT ROWID")
            con.execute("INSERT INTO world_ops VALUES (?, ?)", ("op_legacy_1", time.time()))
        store = self.open()
        result = store.world(NOW, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id="op_legacy_1")
        self.assertTrue(result.duplicate)
        self.assertEqual(result.state["servers"], {"cache": 1})

    def test_expired_operation_ids_are_pruned(self):
        store = self.open()
        store.world(NOW)
        with sqlite3.connect(self.path) as con:
            con.execute("INSERT INTO world_ops (op_id, created_at) VALUES (?, ?)", ("op_old_0001", NOW - 31 * 86400))
        result = store.world(NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id="op_old_0001")
        self.assertFalse(result.duplicate)
        self.assertEqual(result.state["servers"], {"cache": 2})

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


class SnapshotCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "snapshot.sqlite3")
        self.store = Store(self.path)
        self.store.world(NOW)

    def stored(self):
        with sqlite3.connect(self.path) as con:
            state = json.loads(con.execute("SELECT state_json FROM world WHERE id = 1").fetchone()[0])
            updated = con.execute("SELECT updated_at FROM world WHERE id = 1").fetchone()[0]
            events = con.execute("SELECT COUNT(*) FROM world_events").fetchone()[0]
        return state, updated, events

    def test_public_snapshot_is_read_only(self):
        before = self.stored()
        cache = SnapshotCache(self.store)
        cache.latest(0)
        cache.latest(0)
        self.assertEqual(self.stored(), before)

    def test_projection_advances_without_persisting(self):
        state, _, _ = self.stored()
        cache = SnapshotCache(self.store)
        snapshot = cache.latest(0)
        self.assertGreater(snapshot.state["requests"], state["requests"])
        self.assertEqual(self.stored()[0], state)

    def test_single_flight_refresh(self):
        cache = SnapshotCache(self.store, ttl=5)
        calls = []
        original = cache.read_world

        def slow_read():
            calls.append(1)
            time.sleep(0.1)
            return original()

        cache.read_world = slow_read
        barrier = threading.Barrier(8)
        results = []

        def worker():
            barrier.wait()
            results.append(cache.latest(0))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 8)
        self.assertEqual(len(calls), 1)
        totals = {result.state["total_lifetime"] for result in results}
        self.assertEqual(len(totals), 1)

    def test_admin_write_invalidates_the_snapshot(self):
        cache = SnapshotCache(self.store, ttl=60)
        self.assertEqual(cache.latest(0).state["servers"], {"cache": 1})
        self.store.world(NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP)
        self.assertEqual(cache.latest(0).state["servers"], {"cache": 2})

    def test_snapshot_with_events_since_cursor(self):
        self.store.world(NOW + 1, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP)
        cache = SnapshotCache(self.store)
        snapshot = cache.latest(0)
        kinds = [event["kind"] for event in snapshot.events]
        self.assertIn("install", kinds)
        follow = cache.latest(snapshot.cursor)
        self.assertEqual(follow.events, [])
