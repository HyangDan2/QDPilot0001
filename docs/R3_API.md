# Development Digital Twin API v1

The shared snapshots use `GET /idle-ops/api/v1/state` and
`GET /admin/idle-ops/api/v1/state`. The second requires the existing admiral
service role and SSO user. Each result is a complete current snapshot from one
SQLite read transaction.

| Field | Contract |
| --- | --- |
| `version` | Integer `1` for this contract. R3 replaces the previous economy payload. |
| `revision`, `cursor` | Snapshot revision and latest event ID. Admin and public use separate cursor and revision sequences. |
| `cursorReset`, `eventGap` | Reset after restore/truncation, or bounded event window. Use snapshots to recover, skip historical effects. |
| `world` | `xp`, `level`, `levelXp`, `levelXpRequired`. Sum of lifetime XP from all projects in the audience, across dashboard filters. Public includes approved public repositories and admin includes all registered repositories. |
| `projects[]` | `id`, `name`, `level`, `tier`, `xp`, `runCommits`, `bonusBps`, `reboots`, `rebootEligible`, `rebootRequired`, `stats`, `development`, `bugs`, `bugCount`, `observedAt`, `stale`. |
| `stats` | Historical counts of distinct observed `commits`, `pulls`, `issues`, `ci` identities and `releases`. |
| `development` | Null until the first corrected REST observation. Otherwise `lastCommitAt`, `lastActivityAt`, `lastActivityKind`, `openTasks`, `pulls.{open,merged}`, `ci.{running,passed,failed}`. Timestamps use GitHub source times, in Unix seconds. CI counts use the latest observed identities on the tracked tips. `pulls.merged` counts observed records with a verified merge timestamp. |
| `bugs[]` | Up to 24 active `{id,kind}` records per project, with the full open count in `bugCount`. Kinds are `issue` and `ci`. Issue bugs include `classification` set to `bug`, `regression` or `critical`. |
| `agents[]` | Pseudonymous `id`, `projectId`, `status`, nullable `progress`, `observedAt`, `stale`, `lastStatus`, optional `bugId`. Admin adds `name` and `taskId`. |
| `events[]` | Bounded, ordered `id`, `type`, `projectId`, `at` and optional `entityId`, `agentId`, `level`, `tier`. |
| `updatedAt`, `serverTime` | Most recent visible GitHub observation or null, and server timestamp. Unix seconds. |
| `demo` | True only for the isolated synthetic public `?demo=1` response. |

Use `?since=<cursor>` within the same audience and live/demo mode. First load,
page changes, gaps and visibility resumes consume state without replaying prior
effects. Event types are `commit`, `pull`, `release`, `install`, `levelup`, `reboot`, `bug_opened`,
`bug_resolved`, `bug_dismissed` and `agent`. GitHub-authoritative closures
actually marked completed and matched CI successes generate `bug_resolved`.
Non-resolution closures and loss of the last qualifying label generate `bug_dismissed`. Newly classified or reopened bugs and CI failures generate `bug_opened`. Events omit messages, raw GitHub identifiers,
branches, payloads, users and credentials.

SSO applies to the following admin API operations. Calls from the browser
use the shared SSO CSRF token. Each response uses `Cache-Control: no-store`.

| Method and suffix | Contract |
| --- | --- |
| `POST /actions` | `{"opId":"unique-op-id","action":{"type":"reboot","projectId":"project-..."}}`. Returns snapshot with `duplicate`. 409 when ineligible or an opId is reused by another user/project. |
| `POST /reconcile` | `{"projectId":"project-..."}`. 202 queues a collector observation. |
| `GET /integrations` | Registry names/IDs, repository paths, tracked refs, observation times, public visibility, backoff and pending status. Agent names and configured project scopes. |
| `GET /content` | XP and Reboot constants. |
| `GET /whoami` | Trusted caller and role. Browser hard reset is disabled. |
| `GET /health/ready` | Existing unauthenticated health endpoint with world, schema and disk checks. |

Buy, upgrade, reset and hard reset are absent from the R3 HTTP action surface.
The public API supports GET and HEAD. Webhook and agent ingestion use separate
Unix-socket paths and credentials as described in [Integration](R3_INTEGRATIONS.md)
and [Agent telemetry](R3_AGENTS.md).
