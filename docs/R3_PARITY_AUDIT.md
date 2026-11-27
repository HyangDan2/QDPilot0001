# R3 starting-point audit

| Surface | Observed |
| --- | --- |
| QDPilot local | Clean `main` at `90b37d31863ada89a30dbfeb24739df0456dba14`, matching GitHub main. |
| Review branch | `f1a9f663fbd5929e6656f2d5fe6bce35af58b05c` adds the approved work order on `937243c`. Draft PR #2 targets the V3 art branch. Isolated sibling worktree created from the remote feature head. |
| Server-HQ | Local `feat/r13-phase-a-server-prep` at `30bb6a9` has an existing README modification. `services/idle-ops/` holds the service manifest. Production Server-HQ `codex/server-runtime-20261124` is at `3a1233a` with the service manifest directory untracked. |
| Runtime | Existing SSH ControlMaster reused read-only. Active `hd2lab-idle-ops` points to `/opt/hd2lab-idle-ops/releases/20261125-211239`. The 22 compared server, static and docs files match QDPilot local main by SHA-256. The release is a tarball without Git metadata. Ten compared files differ at R3 baseline, which also adds modular Canvas, V3.1 atlases, backup and release gates. |
| API and authentication | Live shared world supports legacy game actions. Public GET state returned 200 over the existing UDS. Nginx hosts read-only `/idle-ops/` (ADR-0019) and SSO-protected `/admin/idle-ops/`. Current proxy forwards service role, with account-role forwarding absent from the installed snippet. The separate Phase 2.1 patch documents account-role forwarding. |
| Database | Read-only inspection found `user_version=0`, and `players`, `action_ops`, `world`, `world_ops`, `world_events`. Feature baseline is version 4 with checkpoints and audit log. The R3 migration starts XP separately and preserves legacy records. |
| Backup | Both Nov 24 and Nov 25 deployment backup directories exist. The inspected Nov 25 directory contains auth and configuration snapshots, with no game SQLite backup present. The dedicated Idle Ops backup timer is inactive and the default game backup directory was absent. |
| Target runtime | Linux aarch64, Python 3.11.2. Existing service runs with AF_UNIX only, 192 MiB and 32 tasks. The worker shares the application library and DB, with GitHub HTTPS outside the web service sandbox. |

Server-HQ's Nov 24 operations document describes the initial private-only release at `087246a`. The current runtime is the later Phase 2 shared world with a public viewer and CSRF-aware admin action requests. The R3 worktree uses the Phase 2.1 migration and backup framework and the V3.1 manifest as its starting point.
