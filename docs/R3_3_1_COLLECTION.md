# R3.3.1 commit collection hotfix

## Production audit · 2026-11-28 KST

The production registry held one allowlisted repository, `HyangDan2/QDPilot0001`,
and the deployed Collector at `93d5d92` followed only its `main` branch.
Its immutable tip was still `93d5d92`. The Collector logged 222 successful
observations and zero observed errors at audit time. The production credential
received HTTP 200 with core limit 5,000 and 4,922 remaining. The DB baseline was
00:03:50.709 KST. The approved repo's ledger contained one zero-XP baseline tip
and zero awarded SHAs. Public and admin worlds both had zero XP. There were
zero Reboot operations.

GitHub Events showed no visible Push to the approved repository in the
requested window. GitHub REST listed five reachable branch tips and returned
zero distinct commits dated in that KST window across those tips. The
authenticated account timeline exposed two Push events in two other
repositories. Their comparisons contained two distinct repository/SHA pairs
outside this registry. Event feeds are delayed and incomplete, so the
reported approximate volume could not be reconstructed from that feed.

The deployed tracking configuration does miss eligible work pushed only to
nonconfigured branches, but the evidence shows no recoverable eligible
2026-11-28 commits in the currently approved repo. A recovery rehearsal using
the production credential and a local SQLite copy found **0 eligible commits,
0 expected XP**. The subsequent regular scan discovered all five branches.
The operational DB and settings were read-only during the audit.

## Behavior

- The allowlist continues to use verified repository IDs. The existing
  `refs` config specifies the bounded CI view. Commit collection discovers
  all current remote branches of each approved repository through paginated
  REST, with a second complete ref check before committing. Deleted refs are
  removed on successful collection. Merges and recreated refs use the same
  repository/SHA ledger.
- Existing branch tips are compared as before. New branch and unavailable
  comparison-base histories are scanned from the immutable current tip
  within the repository baseline. The initial enrollment records zero-XP
  tips. A duplicate SHA or a commit at or before the baseline never pays.
  Merge commits remain excluded. XP uses the current Reboot multiplier for
  a newly eligible commit. Existing XP, ledger entries and Reboot history
  are retained.
- Admin `/integrations` shows registered repo count, observed branch count,
  KST-day unique newly recorded commits and award count, exclusions by
  reason, duplicate sightings in the last successful scan, and latest
  observation, error and retry time per repository. Historical rows lack
  collection timestamps; the dashboard counts v7 observations from first
  use. Public snapshots exclude these details.
- REST page, request, time and rate limits remain in force. A partial list
  does not advance checkpoints. A branch or commit never visible in a
  successful REST snapshot, and no longer reachable from any surviving tip,
  cannot be reconstructed by this recovery command.

## Recovery

Run against a verified SQLite copy first to estimate impact. The operator
command uses the same verified repository ID and scoped credential as normal
reconciliation. The example covers 2026-11-28 KST. Optional UTC bounds are
inclusive by commit time and cannot bypass the existing baseline. A large
range can be split into smaller windows under the same REST budgets.

```sh
python3 -m server.reconcile --db /path/to/verified-copy.sqlite3 \
  --project 1470250170 --recover \
  --since 2026-11-27T15:00:00Z --until 2026-11-28T14:59:59Z \
  --token-file /path/to/approved-read-token
```

A recovery run records eligible SHAs atomically. It leaves the regular
branch checkpoints in place and queues a regular sync. Repeating the command
does not pay any SHA twice. Schema v7 adds collection diagnostics in new
tables. Back up the live DB before upgrading and restore its v6 archive
before restarting the previous release if rollback is required.

References [GitHub commit lists and comparisons](https://docs.github.com/en/rest/commits/commits) and
[GitHub Events availability and lag](https://docs.github.com/en/rest/activity/events).

## Targeted verification

- 17 Python tests passed in the twin/Collector suite. Coverage includes new
  branch history, duplicate and merge exclusion, branch deletion, unavailable
  compare bases, partial pagination, bounded recovery, XP/Reboot preservation,
  KST counters and admin-only diagnostics.
- A 375 px Chrome fixture showed the repository and branch counts, collected
  and awarded commits, merge exclusion, latest error and expandable branch
  names, with zero page errors or horizontal overflow.
- A pre-push recovery and normal scan against a temporary SQLite copy on the
  production host used the current fine-grained credential and actual GitHub
  REST. Both returned 0 awards. The v7 copy tracked five branches and kept the
  public and admin XP at zero. The production application remains on R3.3.
