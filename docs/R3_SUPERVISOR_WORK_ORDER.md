# R3.0–R3.3 Development Digital Twin — Supervisor Work Order

Status: IMPLEMENTED AND VERIFIED FOR SUPERVISOR REVIEW. See [parity audit](R3_PARITY_AUDIT.md) and [integrated QA](R3_QA.md). Production release awaits Overlord approval.
Baseline: 937243c (V3.1 art, includes Phase 2). Main and existing art PR remain untouched.

## Authority and source of truth
Overlord (PO) approves production deployment, main merge, irreversible operations and major scope changes. Supervisor assigns work, coordinates contracts and reviews deltas; engineers implement and test. Lean QA: reuse PASS evidence, no repeated review of unchanged commits; one integrated gate.
A local engineer with authorized existing SSH ControlMaster access must inspect the existing service checkout, deployment revision, local changes, API, DB schema, backup and runtime configuration read-only. Record sanitized findings and remote/local differences before modifying overlapping code. Preserve local uncommitted work; do not copy databases, credentials, internal repository metadata or host-specific configuration into this public repository. Existing service implementation is the reference for compatibility; this feature branch is the proposed source evolution. No production changes without PO approval.

## A — Backend and integration
- Remove buyServer, buyUpgrade and ordinary reset from UI, API and reachable game mutation paths; retain authenticated, idempotent reboot as sole manual growth action. Isolate destructive recovery behind existing highest-role protection and explicit approval.
- GitHub collector: allowlisted repository IDs, scoped read credentials, HMAC-SHA256 raw-body verification for webhooks, delivery deduplication, REST reconciliation, initial baseline without retroactive XP, bounded pagination and rate-limit recovery. Reuse existing ingress, auth, database and deployment patterns; no parallel service unless required.
- Commit ledger UNIQUE(repository_id, SHA); atomic XP award and projection. Exclude merge commits from XP; baseline and repeated pushes/branches do not pay. Initial tunables: 10 XP per eligible commit, 100 new eligible commits since reboot to unlock, +10 percentage points future XP per reboot, cap 2x. No retroactive multiplication; no time-generated XP or reboot qualification. Keep historical records, permanent project tier and unresolved bugs on reboot. Preserve legacy economy records, backup/migration/rollback; new XP starts at zero.
- Project, bug, agent and event projections with revision/snapshot and sanitized event feed. Existing SQLite and server-authoritative state; keep polling fallback. CI problems keyed by repository, tracked ref and matching workflow/job/check identity; only corresponding verified success resolves them. Bug issues keyed by stable issue ID; reopen restores, non-resolution close dismisses without defeat. Handle out-of-order events and stale status.
- Agent telemetry: versioned event envelope (event_id, agent_id, repository_id, run_id, task_id, event_type, timestamp), scoped auth, dedup, heartbeat expiry, sequence validation; coding/testing/blocked/waiting/completed/failed and optional explicitly reported progress. CLI/shell wrapper and adapter examples; verify actual supported hooks for Codex, Qoder, Antigravity and Supervisor before claiming live integration. Telemetry cannot award XP or resolve GitHub bugs.

## B — Game engine
- Reuse Canvas2D, V3.1 atlas and manifest; no new mandatory art. One logical rack per repo; tiers at levels 1/5/10/20/40; auto upgrades, paging/rotation; game metrics explicitly fictional.
- Live mode: remove random bug spawning; real issue/CI entity creates a bug. Verified resolution schedules attack and defeat, not the inverse. Repair only when work is explicitly associated; unrelated commit is not a fix. Bound visible entities and queue, preserve full counts. Demo isolated from live state and ledger.
- Idle/Run/Repair/Attack/LevelUp/Install driven by state and events. Coalesce bursts, resume from snapshot, do not replay historical effects. Agents use shared sprites with labels/colors; cap visible count.

## C — Dashboard
- Keep existing public world and admin SSO routes. Desktop: HUD, central game, project panel, event feed, full-screen watch mode; mobile: game-first with drawer. Project filters, commit/PR/CI/release/issue stats, freshness and observed agent state. No fabricated completion percentages. Stale data labeled.
- Admin: repository connection/registry, agent setup, reboot only; remove all manual purchase controls. Public projection denies private repo names, branches, messages, user names, raw payloads and secrets; default private repo excluded unless PO permits anonymized aggregate.
- Respect existing Server-HQ registration, reverse-proxy and public GET/HEAD-only contract. Any authenticated telemetry/webhook ingress must be separately scoped and tested; never weaken public read-only route.

## Execution sequence
A publishes API/event/schema contract early; B and C develop against mock snapshots concurrently. Use isolated worktrees, preserve unrelated changes, report commit/PR and essential tests. R3.0 core, R3.1 living world, R3.2 dashboard and R3.3 agents run continuously without intermediate PO gates. Final gate only: commit dedup, baseline, reboot idempotency and eligibility, removed actions, migration/rollback, webhook and agent auth, private info protection, matching issue/CI resolution, stale agent status, V3.1 playback, desktop/mobile and target runtime stability. Fix P0 and blocking P1, backlog the rest.

Deliverables: working feature branch, draft PR, game/economy spec, versioned API/event schema, integration guides, demo, migration/rollback, essential test evidence, screenshots and runtime limits. Report Progress / Completed / QA / Remaining / Release Readiness / PO Decision Required. Do not mark local audit, adapter live test or production deployment complete without evidence.
