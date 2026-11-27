# R3.3 Agent Telemetry

Idle Ops shows agent-reported work state and the observation time. Only the GitHub collector awards XP or resolves GitHub bug records. An explicit `issue_id` can associate a repair animation with a currently open GitHub issue in the same registered repository.

## Publisher credentials

The ingest endpoint is `POST /internal/idle-ops/v1/telemetry` on the existing Idle Ops Unix socket. The existing production socket path is `/run/hd2lab-idle-ops/api.sock`. The publisher requires permission to connect to that socket and its own bearer credential. Keep the path outside the Nginx public and admin proxy locations.

Generate a different high-entropy base64url token per publisher. Store it in a file readable only by the publisher outside Git and the web root. Configure the service with `IDLE_OPS_TELEMETRY_TOKENS` or the `telemetry_tokens` field of its private `IDLE_OPS_CONFIG` file. Both take a JSON object keyed by operational agent ID. Each value contains the lowercase SHA-256 digest of the token text (without the newline) and an array of permitted numeric GitHub repository IDs. The array is also checked against the GitHub repository registry at ingest. Configure a scope only for repositories that the publisher is authorized to observe. Empty configuration disables authentication for all publishers. Rotation replaces the digest and updates that publisher's token file.

For example, this service configuration authorizes an independently generated publisher token for repository 123.

```json
{
  "codex-lab": {
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "repository_ids": [123]
  }
}
```

The digest above is a placeholder. The following command creates a private token file and prints its digest. Use a new path for each rotation.

```sh
python3 - /path/outside/git/codex-lab.token <<'PY'
import hashlib
import os
import secrets
import sys

token = secrets.token_urlsafe(32)
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="ascii") as output:
    output.write(token + "\n")
print(hashlib.sha256(token.encode("ascii")).hexdigest())
PY
```

## Event envelope v1

```json
{
  "version": 1,
  "event_id": "22a39c595a814c01aa028b7e4ae509f9",
  "agent_id": "codex-lab",
  "repository_id": 123,
  "run_id": "r3-qa-first",
  "task_id": "verification",
  "event_type": "testing",
  "timestamp": "2026-11-27T12:00:00Z",
  "sequence": 1,
  "progress": 25,
  "issue_id": 456
}
```

| Field | Meaning |
| --- | --- |
| `version` | Integer `1` |
| `event_id` | Unique ID per publisher for the original event |
| `agent_id` | ID assigned to the credential |
| `repository_id` | Numeric GitHub repository ID in the credential scope |
| `run_id` | Unique run ID per agent and repository |
| `task_id` | Opaque task reference. Fixed during the run and visible to admin only |
| `event_type` | `coding`, `testing`, `blocked`, `waiting`, `completed`, `failed`, or `heartbeat` |
| `timestamp` | UTC RFC 3339 with `Z`. New events may be up to 24 hours old or 60 seconds ahead |
| `sequence` | Starts at 1 with a status event. Increases within a run with a nondecreasing timestamp |
| `progress` | Optional finite numeric percentage from 0 to 100, reported explicitly by the publisher |
| `issue_id` | Optional stable GitHub issue ID, as distinct from its repository-local issue number |

Identifiers permit 1–96 ASCII letters, digits, underscores, or hyphens. Numeric IDs and sequences are limited to positive JSON-safe integers. The endpoint accepts the fields in this table. An omitted progress or issue value on a status event clears the previous value. Heartbeats preserve both and carry neither. Keep task identifiers opaque and send only the listed fields. Prompts, transcripts, model output, command arguments, and credentials belong outside the event body.

An exact retry reuses the same event ID and envelope. It leaves sequence and observation time unchanged. The delivery digest is retained for 48 hours. A conflicting ID, non-increasing sequence, older timestamp, or update to a completed or failed run returns HTTP 409. Invalid envelopes return HTTP 400, invalid or missing credentials HTTP 401, and a credential scope or registry mismatch HTTP 403.

Each agent has a latest-run projection per repository, ordered by the first event's source timestamp with the run ID as a deterministic tie-breaker. A delayed report from a superseded run cannot replace the current run. After 90 seconds without a fresh observation a nonterminal run displays `offline` with `stale` set and `lastStatus` preserved. A healthy heartbeat can restore it. Completed and failed are recorded outcomes and keep their original observation time.

## Local publisher

Run the CLI from the checkout. Use the same socket and private token file for either subcommand.

```sh
python3 -m tools.agent_telemetry run \
  --unix-socket /run/hd2lab-idle-ops/api.sock --token-file /path/to/codex-lab.token \
  --agent-id codex-lab --repository-id 123 --task-id r3-tests \
  --initial-status testing -- python3 -m unittest

python3 -m tools.agent_telemetry emit \
  --unix-socket /run/hd2lab-idle-ops/api.sock --token-file /path/to/supervisor.token \
  --agent-id supervisor --repository-id 123 --task-id r3-review \
  --run-id r3-review-1 --event-type waiting --sequence 1
```

The wrapper observes the explicitly configured start, sends a heartbeat every 20 seconds while the command runs, and reports completed or failed from the command exit code. Its return code follows the wrapped command. The wrapper leaves child output on its original streams and sends only the envelope to Idle Ops. Each socket attempt has a two-second timeout and retries at most once with the same envelope. Telemetry delivery is best effort and a network interruption can omit observations. The CLI supports `--offline` in place of the socket and token arguments. Offline `emit` writes one JSON envelope to stdout. Offline `run` writes envelopes to stderr while leaving the child streams available.

## Adapter examples and verification

Each publisher needs a unique credential and agent ID. Hooks can call the local CLI instead of forwarding their complete platform payload. The following examples consume hook input without saving it and emit a new `waiting` run when an interactive turn stops. Use separate IDs for lifecycle observations and command wrappers if both are enabled for the same project. Only an explicit command exit or task outcome report establishes completion. Native hooks do not infer coding, testing, issue association, or progress from tool names or prompts.

Place a helper script outside the published document root, use verified absolute paths in your hook configuration, and configure the local socket and publisher file for the selected client.

```sh
#!/bin/sh
cat >/dev/null
cd /path/to/QDPilot0001 || exit 0
python3 -m tools.agent_telemetry emit \
  --unix-socket /run/hd2lab-idle-ops/api.sock --token-file /path/to/native-agent.token \
  --agent-id native-agent --repository-id 123 --task-id turn-stop --event-type waiting \
  >/dev/null 2>&1 || :
```

| Client | Wiring | Evidence |
| --- | --- | --- |
| Codex | In the client's hooks configuration, add the script as a command handler for `Stop`. For a bounded CLI job, wrap the chosen `codex exec` invocation in `run` and set its initial phase explicitly. | Official contract verified. Local Codex CLI 0.169.1 recognizes hook controls. Hook execution with an enrolled credential remains unverified. [Codex hooks](https://developers.openai.com/codex/hooks) |
| Qoder | In the local Qoder CLI or Qoder settings JSON, add a `Stop` group with a command hook. Use `run` for a bounded CLI job when the Qoder CLI is available. | Official CLI and app contract verified. The local Qoder app is 0.5.18. Hook execution with an enrolled credential remains unverified. [CLI hooks](https://docs.qoder.com/cli/hooks) · [Qoder app hooks](https://docs.qoder.com/qoder/hooks) |
| Antigravity | In the local `.agents/hooks.json` configuration, add the script as a `Stop` command handler with no matcher and have the handler print `{"decision":"allow"}` after the send attempt. | Official command, configuration, and Stop result contract verified. Hook execution with an enrolled credential remains unverified. [Antigravity hooks](https://antigravity.google/docs/hooks) |
| Supervisor | A local review orchestrator can issue explicit `emit` observations with a Supervisor-scoped credential. Increment the sequence for the same run, sending `heartbeat` when there is no state change. | CLI envelope and server ingestion tested locally. A native ChatGPT conversation bridge or live Supervisor feed has not been verified. OpenAI API webhooks cover platform API events, such as background response completion. [OpenAI API webhooks](https://platform.openai.com/docs/guides/webhooks) |

Codex uses `.codex/hooks.json` in a trusted project. Qoder CLI accepts `.qoder/settings.local.json` for project-local configuration. Merge the same event group into the selected file and review the hook through the client before activating it.

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {"type": "command", "command": "/path/to/idle-ops-stop.sh", "timeout": 6}
        ]
      }
    ]
  }
}
```

Antigravity uses a direct handler list under the named group. Use a separate launcher for the Antigravity publisher which invokes its configured helper, then prints `{"decision":"allow"}` to stdout and exits with status 0.

```json
{
  "idle-ops-observation": {
    "Stop": [
      {"type": "command", "command": "/path/to/antigravity-idle-ops-stop.sh", "timeout": 6}
    ]
  }
}
```

The runtime deployment needs per-publisher secrets, repository assignments, and socket access. For clients outside the server, the adapter needs an approved authenticated transport to the local Unix socket. The feature uses the existing routing and authentication conventions and ships without a new network listener for telemetry.

## Integrated QA

The eight telemetry tests cover authentication, strict schema, deduplication, out-of-order delivery, heartbeat expiry and revival, explicit issue and progress retention, and offline command wrapping.

An isolated R3 process with a new SQLite file and Unix socket accepted the real CLI wrapper. The installed `codex --version` command reported completed without a model invocation. A 21-second local shell command reported testing, refreshed its observation with a real heartbeat, and reported failed while preserving exit status 7. The public projection linked the explicit association to the fixture's open issue, withheld the operational agent and task IDs, and left the issue open and XP at zero. The same process returned HTTP 401 without a bearer credential, 403 for a repository outside the credential scope, and 405 for a public write. Native vendor hooks and the live Supervisor feed remain unverified.
