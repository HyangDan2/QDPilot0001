"""Backup, verify and restore command-line behaviour."""

import contextlib
import io
import os
import tempfile
import unittest

from server import backup
from server.store import Store


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "game.sqlite3")
        Store(self.db).world(1_700_000_000.0)

    def run_quiet(self, fn, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return fn(*args, **kwargs)

    def corrupt(self, name="bad.sqlite3"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as handle:
            handle.write(b"not a database")
        return path

    def test_daily_snapshot_is_written_once_per_day(self):
        first = self.run_quiet(backup.create, self.db, kind="daily")
        second = self.run_quiet(backup.create, self.db, kind="daily")
        forced = self.run_quiet(backup.create, self.db, kind="daily", force=True)
        self.assertTrue(os.path.isfile(first))
        self.assertIsNone(second)
        self.assertTrue(os.path.isfile(forced))
        self.assertNotEqual(first, forced)

    def test_verify_accepts_a_snapshot_and_rejects_garbage(self):
        path = self.run_quiet(backup.create, self.db, kind="manual")
        self.assertTrue(self.run_quiet(backup.verify, path))
        self.assertFalse(self.run_quiet(backup.verify, self.corrupt()))

    def test_restore_refuses_an_unverified_backup(self):
        with self.assertRaises(SystemExit):
            self.run_quiet(backup.restore, self.db, self.corrupt())

    def test_restore_keeps_the_previous_database(self):
        snapshot = self.run_quiet(backup.create, self.db, kind="pre-deploy")
        self.run_quiet(backup.restore, self.db, snapshot)
        backups = os.listdir(os.path.join(self.tmp.name, "backups"))
        self.assertTrue(any(name.startswith("idle-ops-pre-restore-") for name in backups))
