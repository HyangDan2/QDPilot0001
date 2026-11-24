"""WO-04: verify beyond quick_check, restore only validated data, keep the old DB."""

import contextlib
import io
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from unittest import mock

from server import backup
from server.store import SCHEMA_VERSION, Store

NOW = 1_700_000_000.0


class BackupHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "game.sqlite3")
        self.store = Store(self.db, backup_dir=os.path.join(self.tmp.name, "backups"))
        self.store.world(NOW)
        self.store.world(
            NOW + 1,
            action={"type": "buyServer", "tier": "cache", "qty": 1},
            op_id="op_backup_01",
            actor="admiral",
        )

    def run_quiet(self, fn, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return fn(*args, **kwargs)

    def copy_db(self, name="copy.sqlite3"):
        """Copy through SQLite so WAL-only commits are included."""
        path = os.path.join(self.tmp.name, name)
        src = sqlite3.connect(self.db)
        dst = sqlite3.connect(path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return path

    def snapshot(self, kind="manual"):
        return self.run_quiet(backup.create, self.db, kind=kind)

    def test_deep_verification_accepts_a_healthy_snapshot(self):
        path = self.snapshot()
        report = backup.verify_database(path)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(report["schemaVersion"], SCHEMA_VERSION)
        self.assertTrue(self.run_quiet(backup.verify, path))
        self.assertEqual(backup.verify_database(path)["problems"], [])

    def test_garbage_is_rejected(self):
        path = os.path.join(self.tmp.name, "garbage.sqlite3")
        with open(path, "wb") as handle:
            handle.write(b"not a database")
        self.assertFalse(backup.verify_database(path)["ok"])
        with self.assertRaises(SystemExit):
            self.run_quiet(backup.restore, self.db, path)

    def test_another_applications_database_is_rejected(self):
        path = os.path.join(self.tmp.name, "other.sqlite3")
        with contextlib.closing(sqlite3.connect(path)) as con:
            with con:
                con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
                con.execute("INSERT INTO notes (body) VALUES ('unrelated')")
                con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        report = backup.verify_database(path)
        self.assertFalse(report["ok"])
        self.assertTrue(any("missing table" in problem for problem in report["problems"]))

    def test_unsupported_schema_is_rejected(self):
        path = self.copy_db()
        with contextlib.closing(sqlite3.connect(path)) as con:
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
            con.commit()
        report = backup.verify_database(path)
        self.assertFalse(report["ok"])
        self.assertTrue(any("unsupported schema" in problem for problem in report["problems"]))

    def test_corrupt_world_payload_is_rejected(self):
        path = self.copy_db()
        with contextlib.closing(sqlite3.connect(path)) as con:
            con.execute("UPDATE world SET state_json = ? WHERE id = 1", ("{not json",))
            con.commit()
        report = backup.verify_database(path)
        self.assertFalse(report["ok"])
        self.assertTrue(any("world state" in problem for problem in report["problems"]))

    def test_bad_operation_or_event_payload_is_rejected(self):
        path = self.copy_db()
        with contextlib.closing(sqlite3.connect(path)) as con:
            con.execute(
                "INSERT INTO world_ops (op_id, created_at, action_json, result_json) VALUES (?, ?, ?, ?)",
                ("op_bad_0001", NOW, "{oops", None),
            )
            con.execute(
                "INSERT INTO world_events (created_at, kind, actor, payload_json) VALUES (?, ?, ?, ?)",
                (NOW, "install", "", "{oops"),
            )
            con.commit()
        report = backup.verify_database(path)
        self.assertFalse(report["ok"])
        self.assertTrue(any("world_ops op_bad_0001" in problem for problem in report["problems"]))
        self.assertTrue(any("world_events" in problem for problem in report["problems"]))

    def test_restore_is_refused_while_the_service_lock_is_held(self):
        path = self.snapshot()
        held = self.store.acquire_service_lock()
        try:
            with self.assertRaises(SystemExit):
                self.run_quiet(backup.restore, self.db, path)
        finally:
            held.close()
        self.assertEqual(self.store.world(NOW + 2).state["servers"], {"cache": 2})

    def test_failure_during_staging_keeps_the_previous_database(self):
        path = self.snapshot()
        before = self.store.world(NOW + 2).state

        def broken_copy(source, target):
            with open(target, "wb") as handle:
                handle.write(b"half written")
            raise sqlite3.OperationalError("disk full")

        with mock.patch.object(backup, "_copy_database", side_effect=broken_copy):
            with self.assertRaises(sqlite3.OperationalError):
                self.run_quiet(backup.restore, self.db, path)
        after = Store(self.db).world(NOW + 3).state
        self.assertEqual(after["servers"], before["servers"])
        self.assertGreaterEqual(after["total_lifetime"], before["total_lifetime"])
        self.assertFalse(os.path.exists(self.db + ".restore-tmp"))
        self.assertFalse(os.path.exists(self.db + ".restore-old"))

    def test_restore_preserves_mode_and_adds_a_pre_restore_backup(self):
        os.chmod(self.db, 0o640)
        path = self.snapshot("pre-deploy")
        self.store.world(NOW + 3600, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id="op_after_backup")
        self.assertEqual(self.store.world(NOW + 3605).state["servers"], {"cache": 3})
        self.run_quiet(backup.restore, self.db, path)
        self.assertEqual(stat.S_IMODE(os.stat(self.db).st_mode), 0o640)
        restored = Store(self.db).world(NOW + 7200)
        self.assertEqual(restored.state["servers"], {"cache": 2})
        names = os.listdir(os.path.join(self.tmp.name, "backups"))
        self.assertTrue(any(name.startswith("idle-ops-pre-restore-") for name in names))

    def test_pre_deploy_snapshot_restores_after_a_later_release_writes(self):
        path = self.snapshot("pre-deploy")
        self.store.world(NOW + 3600, action={"type": "buyUpgrade", "id": "network"}, op_id="op_release_2")
        self.run_quiet(backup.restore, self.db, path)
        restored = Store(self.db).world(NOW + 7200).state
        self.assertEqual(restored["upgrades"], {})

    def test_failed_backup_leaves_no_partial_archive(self):
        locked = os.path.join(self.tmp.name, "readonly")
        os.makedirs(locked)
        os.chmod(locked, 0o500)
        self.addCleanup(os.chmod, locked, 0o700)
        with self.assertRaises(sqlite3.Error):
            Store(self.db, backup_dir=locked).backup("manual", now=NOW, keep=0)
        self.assertEqual(os.listdir(locked), [])


if __name__ == "__main__":
    unittest.main()
