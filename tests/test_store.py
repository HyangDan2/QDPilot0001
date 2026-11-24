"""SQLite store behaviour: lazy simulation, isolation, idempotency."""

import json
import os
import sqlite3
import tempfile
import unittest

from server.store import Store

NOW = 1_700_000_000.0
OP = "op_aaaaaaaa"


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "game.sqlite3")
        self.store = Store(self.path)

    def test_first_session_seeds_a_player(self):
        result = self.store.session("alice", NOW)
        self.assertEqual(result.state["requests"], 25.0)
        self.assertEqual(result.state["servers"], {"cache": 1})
        self.assertIn("first_server", result.state["achievements"])
        self.assertEqual(result.away_seconds, 0.0)
        self.assertIsNone(result.message)

    def test_lazy_simulation_uses_elapsed_time(self):
        first = self.store.session("alice", NOW)
        second = self.store.session("alice", NOW + 100)
        self.assertAlmostEqual(second.away_seconds, 100.0)
        self.assertGreater(second.gains["requests"], 0)
        self.assertGreater(second.state["requests"], first.state["requests"])

    def test_players_are_isolated(self):
        self.store.session("alice", NOW)
        self.store.session("bob", NOW)
        self.store.session("alice", NOW, action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP)
        bob = self.store.session("bob", NOW + 1)
        self.assertEqual(bob.state["servers"], {"cache": 1})
        alice = self.store.session("alice", NOW + 1)
        self.assertEqual(alice.state["servers"], {"cache": 2})

    def test_action_is_idempotent_per_op_id(self):
        self.store.session("alice", NOW)
        first = self.store.session(
            "alice", NOW + 1,
            action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP,
        )
        self.assertFalse(first.duplicate)
        self.assertEqual(first.state["servers"], {"cache": 2})
        self.assertIsNotNone(first.message)

        second = self.store.session(
            "alice", NOW + 2,
            action={"type": "buyServer", "tier": "cache", "qty": 1}, op_id=OP,
        )
        self.assertTrue(second.duplicate)
        self.assertEqual(second.state["servers"], {"cache": 2})
        self.assertIsNone(second.message)
        self.assertGreaterEqual(second.state["requests"], first.state["requests"])

    def test_failed_action_is_rolled_back_and_op_is_not_reserved(self):
        self.store.session("alice", NOW)
        action = {"type": "buyUpgrade", "id": "network"}
        with self.assertRaises(ValueError):
            self.store.session("alice", NOW + 1, action=action, op_id=OP)
        saved = self.store.session("alice", NOW + 2)
        self.assertEqual(saved.state["requests"], 25.0 + saved.gains["requests"])

        with self.assertRaises(ValueError):
            self.store.session("alice", NOW + 3, action=action, op_id=OP)

    def test_corrupt_state_is_rejected(self):
        self.store.session("alice", NOW)
        with sqlite3.connect(self.path) as con:
            con.execute("UPDATE players SET state_json = ? WHERE username = 'alice'", ("{not json",))
        with self.assertRaises(ValueError):
            self.store.session("alice", NOW + 10)

    def test_state_json_round_trips(self):
        self.store.session("alice", NOW)
        with sqlite3.connect(self.path) as con:
            raw = con.execute("SELECT state_json FROM players WHERE username = 'alice'").fetchone()[0]
        payload = json.loads(raw)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["servers"], {"cache": 1})


if __name__ == "__main__":
    unittest.main()
