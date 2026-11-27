# Idle Ops R3 integration and deployment

R3 keeps the existing read-only viewer at `/idle-ops/` and admiral SSO console at
`/admin/idle-ops/`. Both use the Idle Ops SQLite database. The web service
stays Unix-socket-only in production. GitHub polling runs as a one-shot command
from the same release and writes to the same database.

## Register repositories and agents

Use a service-readable JSON file outside the document root and repositories.
Set `IDLE_OPS_CONFIG` to its path in the web service and collector. Example
with fictional IDs and a public display name approved for that project:

```json
{
  "repositories": [
    {
      "id": 123456,
      "repo": "example-org/example-repo",
      "name": "Example Project",
      "refs": ["main", "release/current"],
      "publish": true
    }
  ],
  "telemetry_tokens": {}
}
```

The allowlist uses stable numeric GitHub repository IDs and explicit branches.
A rename or path mismatch needs operator review. Verify the ID and permissions
before indexing. GitHub confirms identity and visibility at each collection.
Publication defaults to false and personal, internal and private repositories
stay out of the public snapshot. Keep sensitive text out of approved display
names. Changes to the allowlist apply when the web process restarts. The admin
console displays the registry and can queue sync; connection details are managed
in that file. See [Agent telemetry](R3_AGENTS.md) for token digests and adapters.

Grant the collector a fine-grained GitHub credential limited to the registered
repositories. Read permissions are Metadata, Contents, Issues, Pull requests,
Checks and Actions. Pass the secret via `--token-file` or the process-only
`IDLE_OPS_GITHUB_TOKEN`. The web application config never needs the REST token.
Run the collector against the same DB and with the same Unix owner as the app:

```sh
IDLE_OPS_CONFIG=/etc/hd2lab/idle-ops-r3.json python3 -m server.reconcile \
  --db /var/lib/hd2lab-idle-ops/game.sqlite3 \
  --token-file /etc/hd2lab/idle-ops-github-token
```

The first successful observation per project stores branch tips, open issues,
recently observed pull requests, current CI and published releases with zero XP.
Adding a tracked branch seeds its first tip without back pay. Later observations
compare immutable SHAs, deduplicate repository/SHA pairs and atomically commit
before advancing tips. Merge commits and commits whose GitHub committer date is
at or before the project baseline are observed without XP. Branch rewinds do
not make an existing SHA eligible again. Counts in the UI are distinct observed
records since tracking began, including baseline records collected by the
corresponding endpoint. They are sampled history and can differ from GitHub's
lifetime totals. A CI ref/app check, workflow or job has its own identity.
External checks on an ambiguous shared SHA require a matching branch.
GitHub Actions workflows and jobs are collected via Actions read endpoints.

## Webhook and remote agents

`POST /internal/idle-ops/v1/github` accepts authenticated delivery wakeups on the
application socket. Preserve the raw bytes and forward `X-GitHub-Delivery`,
`X-GitHub-Event` and `X-Hub-Signature-256`. Set a unique high-entropy
`webhook_secret` on the web process. A repeated delivery is acknowledged and
ignored. A signed hook queues work. REST confirms data before mutating XP or
project projections. A local relay using the same restricted socket can
submit deliveries. The existing public and admin proxies leave the internal
paths unexposed. The public GitHub service therefore needs an approved relay
to reach webhook ingress. Periodic REST collection works with the installed
route architecture.

`POST /internal/idle-ops/v1/telemetry` uses its own bearer verifier and accepts
only versioned observations from configured scopes. A remote operator can
forward through an authorized Unix socket channel or run an adapter on the
host. Standard GitHub signatures and project-scoped agent credentials cannot
substitute for each other. Live telemetry can label an existing issue as active
work and cannot award XP or resolve any issue.

## Capacity and failure behavior

- Registry maximum 40 repositories, 8 branches each. REST allows 8 pages of 100
  items per listing, 240 requests and a 90-second request window per repo.
  The worker walks due repositories for up to 4 minutes before starting the
  final in-flight observation. Configure the timer for a one-minute queue check,
  with periodic sync due after five minutes.
- A page cap, timeout, changed ref or incomplete response aborts that
  observation without advancing the checkpoint. An unusually large backlog
  beyond the page cap needs an operator-assisted collection before that repo
  can advance. For rate limits, `Retry-After` or `X-RateLimit-Reset` determines
  backoff, with a 60-second fallback. Repeated 403s require permission review.
- Observations older than 10 minutes are flagged. The reconciler immediately
  hides a repo after confirming it became private, inaccessible, or mismatched.
  REST observes current issue state and tracked-head CI, so intermediate
  transitions between polls can be missed. Unconfirmed resolution leaves a
  bug active. Production web service continues using the existing memory and
  task limits. Browser and API evidence appears in the [QA report](R3_QA.md).

References [GitHub commit comparison](https://docs.github.com/en/rest/commits/commits#compare-two-commits),
[GitHub check runs](https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference),
[GitHub Actions workflow runs](https://docs.github.com/en/rest/actions/workflow-runs),
[GitHub webhook events](https://docs.github.com/en/webhooks/webhook-events-and-payloads).

## Release and rollback

Follow Server-HQ's Idle Ops deployment and backup procedure after release
approval. Prepare the v0/v4 pre-deploy DB snapshot with the R3 backup CLI
and verify it before replacing the release. Copy the existing role/route config
unchanged. The optional collector service and timer are under `deploy/`. Set the
same `IDLE_OPS_CONFIG` for the existing web unit. Install credentials outside
the repository under service-only access. Validate both units and readiness,
the SSO-backed admin mutations, and the public read-only route before enabling
the collector. Activate adapters against their individual scopes. See
[Migration and rollback](DB_MIGRATION.md#r3-database-migration-and-rollback).
