# R3.3.1 repository discovery and coverage

Reviewed hotfix baseline `81028ae110228bf5bbd5cc01e081a90a42974fa6`.
This change prepares an opt-in policy. Production keeps its current registry,
credentials, and balances until an approved rollout.

## Candidate inventory and 2026-11-28 estimates

The existing production read credential returned 108 account-owned repositories
(73 public, 35 private). QDPilot0001 is the sole existing registration. The 24
new public candidates below belong to the requested namespaces. The 18 private
candidates, including Server-HQ, require separate per-repository approval for
admin-only registration. Other namespaces and organization-owned repositories
are outside automatic coverage.

| Family | New public candidates | Approval-only private candidates | Eligible new public commits | Potential XP |
| --- | ---: | ---: | ---: | ---: |
| AGPilot | 5 | 12 | 106 | 1,060 |
| PKPilot | 14 | 5 | 13 | 130 |
| LMPilot | 5 | 0 | 0 | 0 |
| QDPilot | 0 | 0 | 0 | 0 |
| AGWS | 0 | 0 | 0 | 0 |
| Server-HQ | 0 | 1 | 0 | 0 |
| Total | 24 | 18 | 119 | 1,190 |

| Public candidate with eligible commits | Unique non-merge commits | Potential XP |
| --- | ---: | ---: |
| AGPilot0005 | 11 | 110 |
| AGPilot0006 | 4 | 40 |
| AGPilot0007 | 91 | 910 |
| PKPilot0018 | 13 | 130 |

The zero-count candidates are AGPilot0001 and AGPilot0004; LMPilot0001,
LMPilot0002, LMPilot0003, LMPilot0004 and LMPilot0006; and PKPilot0002,
PKPilot0003, PKPilot0004, PKPilot0005, PKPilot0006, PKPilot0007, PKPilot0008,
PKPilot0011, PKPilot0012, PKPilot0013, PKPilot0014, PKPilot0015 and PKPilot0019.

The reachability audit read all current branch heads and deduplicated
repository/SHA pairs with a verified GitHub committer timestamp inside the
KST day. It excludes merges and does not reconstruct deleted or unreachable
commits. The 119 eligible commits in the new public candidates are estimated at the unchanged
initial multiplier (10 XP). Enrollment starts at zero XP, and this change
does not authorize or pay a pre-enrollment award. The 18 private candidates also passed branch-read checks. Five had eligible
commits, with 57 distinct commits and 570 potential XP in total. Per-repository
private estimates stay in the local operator inventory. QDPilot0001 has one later post-baseline hotfix
commit reachable only on its feature branch at audit time, worth 10 XP under
the ordinary reconciliation policy. It is separate from the new-repository
retroactive estimate and was absent from the live ledger.

## Activation policy

Optional server-side config, stored outside Git and outside the web root:

```json
{
  "discovery": {
    "owner": "HyangDan2",
    "families": ["AGPilot", "PKPilot", "QDPilot", "LMPilot", "AGWS"],
    "publish_public": false,
    "exclude_ids": []
  }
}
```

The operator enables only the approved personal public namespaces. The
family name must be complete, or followed by a number, hyphen, or underscore.
The inventory checks the GitHub owner, stable repository ID and exact public
visibility. Organization-owned, archived, disabled, forked, excluded and
private repositories cannot auto-register. The collector rechecks eligibility
before reading an automatically enrolled repository and again before applying
its result. If a previously public repository loses eligibility, it disappears
from automatic membership and keeps its XP and ledger for authorized reuse.
Public snapshots include an auto-enrolled project only with explicit policy
publication enabled and a verified public observation.

Manual registration retains priority and existing project identity. A private
or internal repository requires explicit operator approval and a manual
numeric ID entry with `publish: false`. Server-HQ is always admin-only.
A corporate project needs separate approval and should stay outside the
approved personal namespaces or be listed in `exclude_ids`. GitHub calls use the
existing read credential. The original registry remains active when
`discovery` is absent.

## Request budget and rollout

- The combined manual and automatic registry cap stays at 40. The 25 public
  projects in the rehearsal leave 15 slots. Existing auto-enrolled repos keep
  their slots before new candidates. Excess candidates remain unregistered.
  All 18 approval-only candidates would bring the total to 43, three over
  capacity, so only up to 15 can be added alongside all public candidates.
- The public inventory runs at most hourly per policy. Worker ticks start at
  most four due projects, oldest attempt first. A 48-request per-tick target
  stops the worker from starting another project. An already started project
  can complete under the existing 240-request, 8-page and 90-second limits.
  The worker pauses the credential cohort before GitHub's core balance drops
  through the 250-request reserve. Permission denials back off per repository.
- A stable one-CI-branch repo usually uses eight calls per successful poll.
  At four projects per minute, 40 continuously due projects imply roughly
  ten minutes of rotation and 1,920 calls/hour before extra pages and CI jobs.
  For 25 projects, the first pass takes about seven successful timer ticks at
  four projects per tick. These are capacity estimates, with rate headers,
  variable CI load and time budgets determining actual cadence.
- Schema v8 adds only automatic membership and inventory timing. Use a verified
  pre-upgrade archive for rollback. Preserve read credentials, manual registry,
  and the production CI baselines. Backfill for new repositories requires its
  own approval and independently verified commit ledger.

References [GitHub repository inventory](https://docs.github.com/en/rest/repos/repos#list-repositories-for-the-authenticated-user),
[commit lists](https://docs.github.com/en/rest/commits/commits#list-commits), and
[rate limits](https://docs.github.com/en/rest/rate-limit/rate-limit).

## Targeted verification

- The production read-only inventory and commit audit took 120 REST calls for
  the public estimate and 75 for the private estimate. A separate capability
  probe returned HTTP 200 for branch reads on all 18 approval-only candidates.
- A rehearsal used a temporary copy of the live database, the production read
  credential and the opt-in policy with public publication disabled. It found
  24 automatic candidates alongside the existing project. The first two worker
  ticks collected four repositories each, using 41 and 42 REST calls. Eight new
  baselines awarded zero XP; the public projection kept one project.
- The 23 changed-scope tests cover the combined cap, opt-in persistence across
  processes, removal on visibility change, history retention, hourly inventory,
  worker rotation, permission errors, rate reserve and existing Collector
  behavior. No production configuration or database changes were made.
