"""Send explicit Idle Ops observations or wrap a local agent command.

Run from the checkout with python3 -m tools.agent_telemetry.
"""

import argparse
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import uuid

from server.telemetry import EVENT_TYPES, IDENTIFIER, MAX_INT, TOKEN, VERSION

PATH = "/internal/idle-ops/v1/telemetry"
HEARTBEAT_INTERVAL = 20
REQUEST_TIMEOUT = 2
RETRYABLE = {429, 500, 502, 503, 504}


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=REQUEST_TIMEOUT)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def envelope(args, event_type, run_id, sequence):
    payload = {
        "version": VERSION,
        "event_id": uuid.uuid4().hex,
        "agent_id": args.agent_id,
        "repository_id": args.repository_id,
        "run_id": run_id,
        "task_id": args.task_id,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sequence": sequence,
    }
    if event_type != "heartbeat":
        if args.issue_id is not None:
            payload["issue_id"] = args.issue_id
        if getattr(args, "progress", None) is not None:
            payload["progress"] = args.progress
    return payload


def deliver(args, payload, stream):
    if args.offline:
        print(json.dumps(payload, separators=(",", ":")), file=stream, flush=True)
        return True
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    status = None
    for _ in range(2):
        conn = UnixHTTPConnection(args.unix_socket)
        try:
            conn.request("POST", PATH, body, {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + args.token,
            })
            status = conn.getresponse().status
            if 200 <= status < 300:
                return True
            if status not in RETRYABLE:
                break
        except (OSError, http.client.HTTPException):
            status = None
        finally:
            conn.close()
    detail = f"HTTP {status}" if status else "socket unavailable"
    print(f"Idle Ops telemetry delivery failed ({detail}).", file=sys.stderr)
    return False


def wrap(args):
    command = args.command
    if command[0] == "--":
        command = command[1:]
    if not command:
        return 127
    run_id = args.run_id or uuid.uuid4().hex
    initial = envelope(args, args.initial_status, run_id, 1)
    sequence = 1
    try:
        process = subprocess.Popen(command)
    except OSError:
        print("Agent command could not start.", file=sys.stderr)
        deliver(args, envelope(args, "failed", run_id, sequence), sys.stderr)
        return 127

    def forward(signum, _frame):
        process.send_signal(signum)

    previous = {signum: signal.signal(signum, forward) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        started = deliver(args, initial, sys.stderr)
        while True:
            try:
                code = process.wait(timeout=HEARTBEAT_INTERVAL)
                break
            except subprocess.TimeoutExpired:
                if started:
                    sequence += 1
                    deliver(args, envelope(args, "heartbeat", run_id, sequence), sys.stderr)
                else:
                    started = deliver(args, initial, sys.stderr)
        if started or deliver(args, initial, sys.stderr):
            status = "completed" if code == 0 else "failed"
            deliver(args, envelope(args, status, run_id, sequence + 1), sys.stderr)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return code if code >= 0 else 128 - code


def positive_int(raw):
    value = int(raw)
    if not 0 < value <= MAX_INT:
        raise argparse.ArgumentTypeError("expected a positive integer at most 2^53-1")
    return value


def identifier(raw):
    if not IDENTIFIER.fullmatch(raw):
        raise argparse.ArgumentTypeError("expected 1–96 letters, digits, underscores or dashes")
    return raw


def percent(raw):
    value = float(raw)
    if not 0 <= value <= 100:
        raise argparse.ArgumentTypeError("expected a finite number from 0 to 100")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    emit = subparsers.add_parser("emit", help="publish one explicit observation")
    run = subparsers.add_parser("run", help="observe the lifetime of a local command")
    for child in (emit, run):
        transport = child.add_mutually_exclusive_group(required=True)
        transport.add_argument("--unix-socket")
        transport.add_argument("--offline", action="store_true", help="print envelopes without sending")
        child.add_argument("--token-file", type=Path)
        child.add_argument("--agent-id", required=True, type=identifier)
        child.add_argument("--repository-id", required=True, type=positive_int)
        child.add_argument("--task-id", required=True, type=identifier)
        child.add_argument("--run-id", type=identifier)
        child.add_argument("--issue-id", type=positive_int)
    emit.add_argument("--event-type", choices=sorted(EVENT_TYPES), required=True)
    emit.add_argument("--sequence", type=positive_int, default=1)
    emit.add_argument("--progress", type=percent)
    run.add_argument("--initial-status", choices=("coding", "testing", "waiting"), default="waiting")
    run.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.offline:
        if not args.token_file:
            parser.error("--unix-socket requires --token-file")
        try:
            with args.token_file.open(encoding="ascii") as token_file:
                args.token = token_file.read(512).strip()
        except (OSError, UnicodeError):
            parser.error("could not read telemetry token")
        if not TOKEN.fullmatch(args.token):
            parser.error("invalid telemetry token")
    if args.mode == "run":
        if not args.command or args.command == ["--"]:
            parser.error("run requires a command after --")
        return wrap(args)
    if args.event_type == "heartbeat":
        if args.progress is not None or args.issue_id is not None:
            parser.error("heartbeat carries neither progress nor issue_id")
        if not args.run_id or args.sequence == 1:
            parser.error("heartbeat requires a run_id and sequence greater than 1")
    if args.sequence != 1 and not args.run_id:
        parser.error("sequence greater than 1 requires a run_id")
    payload = envelope(args, args.event_type, args.run_id or uuid.uuid4().hex, args.sequence)
    return 0 if deliver(args, payload, sys.stdout) else 1


if __name__ == "__main__":
    sys.exit(main())
