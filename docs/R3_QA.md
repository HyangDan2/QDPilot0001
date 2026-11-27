# R3 integrated QA and release status

Application commit `907d6457f832478d39d191946f825fc3c4f37be8`.
Review target [Draft PR #2](https://github.com/HyangDan2/QDPilot0001/pull/2).

## Results

| Gate | Observed result |
| --- | --- |
| Baseline and deduplication against GitHub | A disposable database collected the actual public QDPilot0001 `main` and R3 branch over GitHub REST. Baseline awarded 0 XP. The pushed application commit added 1 eligible SHA and 10 XP. Reconciliation of the same tips awarded 0 more. This used the existing local GitHub credential for read-only QA. |
| XP and Reboot regression | Initial and newly tracked tips are free of retroactive XP, historical and merge SHAs excluded, same SHA deduplicated across refs, time does not qualify. Server-side eligibility, duplicate replay, project/actor conflicts, permanent XP/tier and future bonus tested. |
| GitHub and public authority | HMAC verified over the raw request, tamper rejected, exact delivery deduplicated, repository scope checked, bounded pagination and rate-limit recovery tested. Private repo, raw identifiers and task data excluded from public snapshots. Legacy purchase, upgrade and reset mutations unavailable on R3 routes. |
| Issue and CI projection | Stable issue identity, reopen, completed resolution, non-resolution closure, stale observation handling. Check app/ref and workflow/job matching were tested. Unrelated or older successes leave active bugs intact. |
| Backup and rollback | Disposable shared-world v0 archive retained its input version and operation records. Additive v5 migration retained legacy state and initialized zero twin XP. Restored the verified pre-deploy v0 file and retained a pre-restore backup. The inherited v4-era backup and corruption regressions also passed. |
| Python suites | Full 238-test suite passed after including Pillow and NumPy for the optional asset tooling. One subsequently added workflow/job regression passed in the 9-test targeted suite. The final 17 R3 and telemetry tests passed on Linux aarch64 / Python 3.11.2. |
| JavaScript | 28 tests passed. New scene regressions included historical replay suppression, explicit repair, resolution/dismissal/reopen and rack/queue bounds. |
| Public browser | Headless Chrome at 1440, 390 and 320 px against an isolated R3 instance. Six V3.1 atlas states loaded and observed in playback, 60 FPS desktop, zero console/asset errors. Verified filter, rack pages, full screen, mobile drawer, reduced motion, demo isolation and stale agents. [Detailed evidence](R3_BROWSER_QA.md) |
| Admin browser | Real local R3 app and SQLite with an SSO gateway simulation. Verified CSRF refresh, lost-response retry with the same opId and exactly one reboot, collection queue, stale/private distinctions, explicit telemetry progress, shell offset, persistent theme and 390/320 px overflow checks. Zero page errors. |
| Telemetry end to end | Real CLI wrapper and isolated Unix socket. Installed Codex `--version` produced a completed observation. A 21-second command emitted a heartbeat and preserved exit code 7 in its failed state. Tested missing credentials, wrong repository scope, active issue association, stale state and public redaction. [Agent evidence](R3_AGENTS.md) |
| Target runtime | Raspberry Pi isolated `/tmp` process and synthetic SQLite fixture. 40 projects, 960 active bugs, 12 concurrent pollers over 30 seconds. 1,596 Unix socket state requests, 0 errors. Median 13.92 ms, p95 103.19 ms, max 350.10 ms, ending RSS 30,656 KiB. Collector service and timer passed `systemd-analyze verify`. The host emitted an existing unrelated unit's path warning. |

Reproduce the target smoke/benchmark with `python3 tools/qa_r3_runtime.py --seconds 30 --viewers 12`.
The benchmark uses an independent process and fixture DB.

## Screenshots

- [Public desktop](screenshots/r3/public-desktop.jpg)
- [Public mobile](screenshots/r3/public-mobile.jpg)
- [Admin desktop](screenshots/r3/admin-1440.jpg)
- [Admin mobile](screenshots/r3/admin-390.jpg)

## Remaining release gates

Ready for Supervisor review. Production cutover requires Overlord authorization,
a verified pre-deploy game DB backup, repository-scoped credentials, and an
SSO smoke test with the actual Chairman/admiral grants. The scheduled collector
was validated as unit files and by manual execution in isolation. Production
scheduling and long-duration browser/runtime soak remain unverified.

Signed webhooks were tested locally. The existing production proxy exposes no
machine publisher routes. An approved relay and real GitHub webhook delivery
would be separate from the periodic REST rollout. Adapter documentation covers
published Codex, Qoder and Antigravity hooks plus a scoped Supervisor publisher.
Native hooks and a live Supervisor feed remain unverified. No deployed service,
proxy configuration, production SQLite data or main branch was changed.

## Closure correction · FIX-01–03

Supervisor review target `070aacc63e28cd8176ba60d95bb96221d1a54cd1`.
The existing R3 QA above is accepted evidence. The closure was checked with
focused tests only.

| Change | Executed evidence |
| --- | --- |
| FIX-01 | Label normalization and precedence, ordinary enhancement exclusion, same-second label change, relabel and reopen, dismissal without defeat, verified resolution, matched CI and issue-linked telemetry. |
| FIX-02 | PR Open and verified Merged, unmerged closure exclusion, actual tracked-tip commit timestamp, last source activity, current running/failed/passed CI and observation freshness. A running CI retry retained the original unresolved bug. Actual GitHub REST returned two open PRs and separate commit/activity timestamps in a fresh QA DB. |
| FIX-03 | Audience-scoped World XP aggregation, level and next-level remainder, continued project Level/Reboot behavior and unchanged lifetime XP at Reboot. Chrome at 1440px and 320px rendered the HUD, source timestamps and status breakdown. Project filtering left world progress intact; the mobile drawer had no horizontal overflow. |
| Migration | v0 backup, migration and restore exercised for v6. A v5 fixture retained stored XP and legacy issue records, and gained a bug only after GitHub label confirmation. |

Ten focused Python tests passed across the changed paths. The updated
REST projection was exercised against the public repository. Desktop and mobile
Chrome checks exercised the changed HUD and metrics with the isolated demo.
The prior full suite, soak, native art playback and admin SSO simulation were
reused. No production backup, login, timer activation or deployment was run.

Initial rollout uses the existing REST collector. External GitHub webhook relay,
daily/weekly statistics, Qoder and Antigravity native-hook verification, live
Supervisor telemetry and long-duration stability remain backlog items. The
production backup, SSO, collector activation and rollback sequence is prepared
in the [operator checklist](R3_INTEGRATIONS.md#release-and-rollback).
