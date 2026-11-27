from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import subprocess
import sys
import unittest

from server import telemetry


NOW = 1_798_000_000
SCOPE = telemetry.Scope("test-codex", frozenset({123, 456}))


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(telemetry.SCHEMA)
        self.addCleanup(self.con.close)

    def payload(self, sequence=1, **fields):
        timestamp = datetime.fromtimestamp(fields.pop("at", NOW + sequence), timezone.utc)
        return {
            "version": 1, "event_id": f"event-{sequence}", "agent_id": SCOPE.agent_id,
            "repository_id": 123, "run_id": "run-one", "task_id": "task-one",
            "sequence": sequence, "event_type": "coding", "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            **fields,
        }

    def ingest(self, payload, now=None):
        with self.con:
            return telemetry.ingest(self.con, payload, SCOPE, NOW + 10 if now is None else now)

    def assert_status(self, payload, expected, now=None):
        with self.assertRaises(telemetry.TelemetryError) as error:
            self.ingest(payload, now)
        self.assertEqual(error.exception.status, expected)

    def test_scope_authentication_and_claims(self):
        token = "T" * 43
        digest = hashlib.sha256(token.encode()).hexdigest()
        raw = json.dumps({"test-codex": {"sha256": digest, "repository_ids": [123, 456]}})
        tokens = telemetry.load_tokens(raw)
        self.assertEqual(telemetry.authenticate("Bearer " + token, tokens), SCOPE)
        for header in (None, "Bearer " + "X" * 43, "Basic " + token, "Bearer " + token + " "):
            with self.assertRaises(telemetry.TelemetryError) as error:
                telemetry.authenticate(header, tokens)
            self.assertEqual(error.exception.status, 401)
        for raw_config in ("[]", "{broken", json.dumps({"test-codex": {"sha256": digest,
                                                                      "repository_ids": [True]}})):
            with self.assertRaises(ValueError):
                telemetry.load_tokens(raw_config)
        self.assert_status(self.payload(repository_id=789), 403)
        self.assert_status(self.payload(agent_id="test-other"), 403)

    def test_schema_and_timestamp_validation(self):
        for invalid in (
            {"version": True}, {"event_type": []}, {"sequence": True}, {"event_id": "has space"},
            {"progress": float("nan")}, {"progress": None}, {"progress": 101}, {"issue_id": True},
            {"username": "private"}, {"timestamp": "2026-02-30T01:02:03Z"},
            {"timestamp": "2026-02-02T01:02:03+03:00"},
            {"at": NOW - telemetry.MAX_EVENT_AGE}, {"at": NOW + 11 + telemetry.FUTURE_SKEW},
        ):
            with self.subTest(invalid):
                self.assert_status(self.payload(**invalid), 400)
        self.assert_status(self.payload(2), 409)
        self.assert_status(self.payload(event_type="heartbeat"), 409)
        self.assert_status(self.payload(event_type="heartbeat", issue_id=101), 400)

    def test_deduplication_is_exact_and_never_extends_heartbeat(self):
        event = self.payload()
        self.assertTrue(self.ingest(event)["changed"])
        replay = self.ingest(dict(reversed(list(event.items()))), NOW + telemetry.HEARTBEAT_SECONDS + 20)
        self.assertTrue(replay["duplicate"])
        self.assertFalse(replay["updated"])
        self.assert_status({**event, "progress": 10}, 409)
        current = telemetry.projection(self.con, NOW + telemetry.HEARTBEAT_SECONDS + 1)[0]
        self.assertEqual(current["status"], "offline")

    def test_run_sequence_timestamp_and_task_validation(self):
        self.ingest(self.payload())
        self.ingest(self.payload(3, event_type="testing"))
        self.assert_status(self.payload(2), 409)
        self.assert_status(self.payload(4, at=NOW + 2), 409)
        self.assert_status(self.payload(4, task_id="different"), 409)
        self.ingest(self.payload(4, event_type="completed"))
        self.assert_status(self.payload(5, event_type="heartbeat"), 409)
        current = telemetry.projection(self.con, NOW + 5_000)[0]
        self.assertEqual(current["status"], "completed")
        self.assertIsNone(current["progress"])
        self.assertFalse(current["stale"])

    def test_heartbeat_expiry_and_revival_preserve_explicit_status(self):
        self.ingest(self.payload(event_type="blocked", progress=25, issue_id=42))
        live = self.ingest(self.payload(2, event_type="heartbeat"))
        self.assertTrue(live["updated"])
        self.assertFalse(live["changed"])
        expired_at = NOW + 2 + telemetry.HEARTBEAT_SECONDS
        stale = telemetry.projection(self.con, expired_at)[0]
        self.assertEqual((stale["status"], stale["lastStatus"]), ("offline", "blocked"))
        revived = self.ingest(self.payload(3, event_type="heartbeat", at=expired_at + 10), expired_at + 10)
        self.assertTrue(revived["changed"])
        active = telemetry.projection(self.con, expired_at + 11)[0]
        self.assertEqual((active["status"], active["progress"], active["issueId"]), ("blocked", 25, 42))

    def test_late_new_run_and_retired_run_cannot_replace_current_run(self):
        self.ingest(self.payload(at=NOW + 4))
        newer = self.payload(event_id="fresh", run_id="run-two", task_id="task-two",
                             at=NOW + 5, event_type="testing")
        self.assertTrue(self.ingest(newer)["changed"])
        late = self.payload(event_id="delayed", run_id="run-zero", at=NOW)
        self.assertFalse(self.ingest(late)["changed"])
        self.assertFalse(self.ingest(self.payload(2, at=NOW + 8, event_type="failed"))["changed"])
        other_repo = self.payload(event_id="other-repo", repository_id=456)
        self.assertTrue(self.ingest(other_repo)["changed"])
        first, second = telemetry.projection(self.con, NOW + 10)
        self.assertEqual((first["status"], first["taskId"]), ("testing", "task-two"))
        self.assertEqual((second["status"], second["projectId"]), ("coding", 456))

    def test_old_delivery_does_not_override_current_observation_or_gain_freshness(self):
        self.ingest(self.payload(at=NOW), now=NOW + 500)
        self.assertEqual(telemetry.projection(self.con, NOW + 500)[0]["status"], "offline")
        late = self.payload(2, event_type="heartbeat", at=NOW + 1)
        result = self.ingest(late, now=NOW + 500)
        self.assertFalse(result["changed"])
        self.assertEqual(telemetry.projection(self.con, NOW + 500)[0]["status"], "offline")

    def test_offline_wrapper_preserves_command_exit_and_output(self):
        command = [
            sys.executable, "-m", "tools.agent_telemetry", "run", "--offline",
            "--agent-id", SCOPE.agent_id, "--repository-id", "123",
            "--task-id", "wrapper-test", "--initial-status", "testing",
            "--", sys.executable, "-c", "print('child-output'); raise SystemExit(7)",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "child-output\n")
        start, end = [json.loads(line) for line in result.stderr.splitlines()]
        self.assertEqual([start["event_type"], end["event_type"]], ["testing", "failed"])
        self.assertEqual([start["sequence"], end["sequence"]], [1, 2])
        self.assertEqual(start["run_id"], end["run_id"])
        self.assertNotEqual(start["event_id"], end["event_id"])
        self.assertNotIn("child-output", result.stderr)
