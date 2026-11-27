# QDPilot0001 · Idle Ops

HD2Lab Idle Ops turns verified GitHub activity and agent observations into a
shared development server room. Each tracked repository has a rack. Eligible
commits award XP, real issues and CI failures appear as bugs, and matched
resolution triggers a defeat animation. The existing Canvas2D renderer uses
the six 향단이 V3.1 atlases.

- Public viewer `/idle-ops/` displays approved public projects and supports
  a separate synthetic demo.
- Admiral SSO console `/admin/idle-ops/` provides project and agent status,
  sync requests and project Reboot after 100 eligible commits.
- Project progress, ledger and integration observations live in the existing
  SQLite database. Legacy progress is preserved separately.

## Local preview

```sh
python3 -m server --dev-user hd2service --static-root static --public-root static/public \
  --port 8123 --db /tmp/idle-ops-r3.sqlite3
```

Open `http://127.0.0.1:8123/idle-ops/?demo=1` for synthetic activity or
`http://127.0.0.1:8123/admin/idle-ops/` for the admin view. The live viewer starts
empty until a repository is configured, verified and approved for display.
`--dev-user` is for local testing only.

## Guides

- [GitHub registration and integration](docs/R3_INTEGRATIONS.md)
- [Agent telemetry](docs/R3_AGENTS.md)
- [API and event contract](docs/R3_API.md)
- [Game rules](docs/GAME_DESIGN.md), [economy](docs/ECONOMY_SPEC.md) and [animation](docs/ANIMATION_SPEC.md)
- [DB migration and recovery](docs/DB_MIGRATION.md#r3-database-migration-and-rollback)
- [Parity audit](docs/R3_PARITY_AUDIT.md), [integrated QA](docs/R3_QA.md) and [browser evidence](docs/R3_BROWSER_QA.md)

## Verification

```sh
# The asset QA suite additionally requires Pillow and NumPy.
python3 -m unittest discover -s tests -t .
node --test tests/js/*.test.mjs
python3 tools/qa_r3_runtime.py
```

Production uses the existing Idle Ops systemd unit, SSO and Nginx routes.
See the [approved scope](docs/R3_SUPERVISOR_WORK_ORDER.md) and [release procedure](docs/R3_INTEGRATIONS.md#release-and-rollback).
