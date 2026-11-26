# QDPilot0001 — Idle Ops

![향단이가 서버실을 뛰어다니며 레벨업하고 버그를 잡는 일러스트](static/assets/hyangdani-hero.jpg)

HD2Lab Server-HQ의 **공유 월드 방치형 게임**입니다. 서버실을 운영해 요청 처리량을 키우고, 레벨·업적·가동 토큰·Reboot로 성장합니다. 앱을 닫아 두어도 진행은 서버에서 이어지며, 공개 화면(`/idle-ops/`)에서는 누구나 같은 월드를 관전할 수 있습니다.

## 핵심 설계

- **서버 권위 공유 월드**: 상태는 `world` 1행(id=1)이며 모든 방문자가 같은 월드를 봅니다. 관리자 콘솔(`/admin/idle-ops/`, SSO admiral 전용)만 액션을 보낼 수 있고, 공개 경로(`/idle-ops/`)는 읽기 전용입니다.
- **서버 권위 계산**: 클라이언트는 액션 의도(`type`, 티어, 수량, `opId`)만 보냅니다. 비용·보상·상태 전이는 전부 서버(`server/economy.py`)가 계산합니다.
- **지연 시뮬레이션(lazy catch-up)**: 상시 tick 루프 없이, 요청이 올 때 `현재 시각 - 마지막 시뮬레이션 시각` 경과분을 한 번에 정산합니다(`requests += rps × elapsed`). 부재 기간 상한은 없고 100년 overflow 보호만 둡니다.
- **멱등 액션**: `opId`(8~64자 `[A-Za-z0-9_-]`)가 30일간 유일 키(`world_ops`)로 기록되어 재시도·중복 요청이 상태를 두 번 바꾸지 않습니다.
- **연출 이벤트**: `install`·`upgrade`·`levelup`·`achievement`·`reboot`·`reset`을 `world_events`에 기록하고, 공개 클라이언트는 `?since=<cursor>`로 증분 조회해 설치 연출·레벨업 효과·로그를 재생합니다. 액터 이름은 공개 응답에 포함하지 않습니다.
- **전환 이관**: 최초 기동 시 기존 `players` 테이블에서 누적 생산량이 가장 큰 행의 상태를 1회 이관합니다. `players`는 삭제하지 않고 보존합니다.
- **공개 화면**: 의존성 없는 Canvas 2D 미니 엔진(`static/public/assets/engine/`)이 서버실·설치 애니메이션·향단이 이동/전투를 그리고, `world.js`가 8초 폴링·이벤트 로그·드로워·메뉴를 담당합니다. 스프라이트 시트가 없어도 [`sprites/manifest.json`](static/public/assets/sprites/manifest.json)의 단일 이미지 fallback으로 동작합니다.

## 성장 규칙 요약

| 항목 | 규칙 |
| --- | --- |
| 생산량 | 서버 티어별 RPS 합 × 레벨 보너스(+1%/Lv) × 토큰 보너스(+2%/개) × 업그레이드 × 열 효율 |
| 레벨 | `floor(sqrt(total_lifetime / 10000)) + 1` (누적 생산량 기준) |
| 발열 | 서버·오버클럭 발열 합 vs 냉각 용량, 부족분만큼 효율 감소(최저 25%) |
| Reboot | `floor(sqrt(run_lifetime / 1e6))` 가동 토큰 지급, 서버·업그레이드 초기화(토큰·레벨·업적 유지) |
| 초기화 | 진행 전체 초기화, 토큰·레벨·업적은 유지 |

전체 수치는 [`server/economy.py`](server/economy.py)의 티어·업그레이드·업적 정의가 정본입니다.

## 구조

```text
server/economy.py   # 밸런스, 상태 정규화, 액션 검증, 레벨·발열·토큰 계산
server/store.py     # SQLite(world, world_ops, world_events + 레거시 players) 트랜잭션·멱등 처리 (WAL)
server/backup.py    # 정합성 백업·검증·복구 CLI (python3 -m server.backup)
server/content.py   # 밸런스 지문·피처 플래그·콘텐츠 메타데이터 (기본 off)
server/api.py       # 표준 라이브러리 HTTP API + 관리자/공개 정적 파일 서빙
server/__main__.py  # UDS/TCP 실행 진입점 (--uds, --host/--port, --static-root, --public-root, --dev-user)
static/             # Vanilla ES 모듈 관리자 콘솔 (index.html, assets/)
static/public/      # 공개 월드 뷰 (index.html, world.js)
static/public/assets/engine/   # Canvas 2D 엔진 모듈 (config·animation·sprites·effects·room·motion·scene)
static/public/assets/sprites/  # 스프라이트와 manifest.json (시트 미납품 시 단일 이미지 fallback)
art/                # 웹 루트 밖 아트 원본(source)·반입 대기(export) — 서빙되지 않음
docs/               # GAME_DESIGN, ECONOMY_SPEC, ANIMATION_SPEC, ART_DIRECTION, ASSET_REQUIREMENTS, DB_MIGRATION, PHASE2_*, PERFORMANCE_REPORT, notes
tools/              # qa_perf.py(부하·soak), build_sprite_atlases.py(384 서비스 export),
                    # import_phase22_atlas.py(납품 반입 게이트), wo0221_*(에셋 감사·GIF·브라우저 QA)
tests/              # 222개 단위·통합 테스트 (UDS·릴리스 게이트·에셋 감사 포함)
```

## 로컬 실행

```bash
python3 -m server --dev-user hd2service --static-root static --public-root static/public \
  --port 8123 --db /tmp/idle-ops.sqlite3
# 관리자 콘솔: http://127.0.0.1:8123/admin/idle-ops/
# 공개 월드:   http://127.0.0.1:8123/idle-ops/
```

운영에서는 Nginx가 공개 정적 파일을 릴리스의 `static/public/`에서 직접 서빙하고, 공개 API(`GET /idle-ops/api/v1/state`)만 UDS로 프록시합니다.

`--dev-user`는 로컬 확인용 fallback이며, 운영에서는 사용하지 않습니다(Nginx가 `X-HD2Lab-User`를 주입).

## API

관리자 경로 기준: `/admin/idle-ops/api/v1` (SSO admiral 전용)

| 메서드·경로 | 설명 |
| --- | --- |
| `GET /state` | 현재 상태·파생값·카탈로그·부재 보상·이벤트(`since` 지원) |
| `POST /actions` | `{"opId":"...","action":{"type":"buyServer","tier":"cache","qty":10}}` (qty: 1, 10, "max") |
| 〃 | `{"opId":"...","action":{"type":"buyUpgrade","id":"network"}}` |
| 〃 | `{"opId":"...","action":{"type":"reboot"}}` |
| `POST /reset` | `{"opId":"..."}` — 월드 초기화(토큰·레벨·업적 유지) |
| `POST /hard-reset` | `{"opId":"...","confirm":"HARD RESET"}` — Chairman 전용(admiral 서비스 권한 + chairman 계정 역할) |
| `GET /whoami` | 호출자의 신뢰된 SSO 역할과 `canHardReset` (UI에서 위험 버튼 숨김용, 서버가 다시 강제) |
| `GET /content` | 밸런스 버전·지문·피처 플래그(기본 off)·임계값 (상태 미포함) |
| `GET /health/ready` | `{"status":"ok","service":"idle-ops","checks":{"world":true,"schema":true,"disk":true}}` — world 행·스키마 버전·여유 공간만 불리언으로 노출, 실패 시 503 (인증 없음) |

Hard Reset 권한은 Server-HQ admin-auth의 실제 계정 체계를 따릅니다. `admiral_only` 서비스에서
`X-HD2Lab-Role`은 Chairman과 grant된 admiral 모두에게 `admiral`로 내려오므로, 파괴적 권한은
추가로 `X-HD2Lab-Account-Role: chairman`(실제 최고 관리자: `is_overlord` 또는 `role=chairman`)을
요구합니다. 헤더가 없거나 해석할 수 없으면 거부합니다(fail closed). Nginx가 두 헤더를 SSO 결과로
덮어쓰므로 클라이언트가 역할을 위조할 수 없습니다. 배포 시
`deploy/nginx-idle-ops-account-role.patch`를 적용해야 합니다.

공개 경로 기준: `/idle-ops/api/v1`

| 메서드·경로 | 설명 |
| --- | --- |
| `GET /state?since=<cursor>` | 공개용으로 정제된 `state`·`derived`·`catalog`·`gains`·`events`·`cursor` (인증 없음) |

오류: 인증 없음 `401`, 잘못된 액션/opId/JSON `400`, 자금 부족 `400`, 본문 초과 `413`, 공개 쓰기 `405`(`Allow: GET, HEAD`), 저장 실패 `500`.

`events` 항목은 `type`·`kind`·`at`·`id`(커서)와 이벤트별 필드를 가집니다: `install`(`tier`,`qty`,`cost`), `upgrade`(`upgrade`,`level`,`cost`), `levelup`(`level`), `achievement`(`achievement`), `reboot`(`tokens`), `reset`.

## 테스트

```bash
python3 -m unittest discover -s tests -t . -v   # 222 tests (실제 UDS 4건 포함)

# 부하·soak (실행 중인 서버 대상, 프록시 우회 내장)
python3 tools/qa_perf.py public --base http://127.0.0.1:8123 --viewers 100 --seconds 30 --interval 1
python3 tools/qa_perf.py action --base http://127.0.0.1:8123 --workers 10 --ops 5
```

Phase 2 검증 결과: [`docs/PHASE2_QA_REPORT.md`](docs/PHASE2_QA_REPORT.md),
[`docs/PERFORMANCE_REPORT.md`](docs/PERFORMANCE_REPORT.md), 구현 범위·커밋은
[`docs/PHASE2_IMPLEMENTATION_REPORT.md`](docs/PHASE2_IMPLEMENTATION_REPORT.md).

Phase 2.1(교정·릴리스 게이트) 결과: [`docs/PHASE2_1_CORRECTION_REPORT.md`](docs/PHASE2_1_CORRECTION_REPORT.md),
[`docs/PHASE2_1_QA_REPORT.md`](docs/PHASE2_1_QA_REPORT.md),
[`docs/PHASE2_1_RELEASE_GATE.md`](docs/PHASE2_1_RELEASE_GATE.md),
[`docs/PHASE2_1_PERFORMANCE_REPORT.md`](docs/PHASE2_1_PERFORMANCE_REPORT.md).

## 배포 (요약)

2026-11-24 운영(hyogidevrsp)에 배포했습니다: `https://hd2lab.duckdns.org/admin/idle-ops/` (관리자 SSO). 릴리스 `/opt/hd2lab-idle-ops/releases/20261124-235022`, systemd `hd2lab-idle-ops`, UDS `/run/hd2lab-idle-ops/api.sock`, DB `/var/lib/hd2lab-idle-ops/game.sqlite3`. 절차·검증·복원은 Server-HQ `docs/IDLE_OPS_OPERATIONS.md`를 따릅니다. 재배포 요점:

- 정본 경로 `/admin/idle-ops/`, Nginx는 `admin-sso-page.conf`/`admin-sso-api.conf`로 보호하고 게임 백엔드는 `/run/hd2lab-idle-ops/api.sock`에만 바인딩합니다.
- systemd `hd2lab-idle-ops.service`(전용 사용자, `StateDirectory=/var/lib/hd2lab-idle-ops`)로 실행합니다.
- admin-auth에 서비스 endpoint를 등록해야 `/auth/verify` prefix 허용 목록에 포함되어 403 없이 접근할 수 있습니다.

## 데이터·백업

- 운영 DB: `/var/lib/hd2lab-idle-ops/game.sqlite3` (SQLite WAL, 스키마 `PRAGMA user_version`)
- 백업: `python3 -m server.backup create --db <db> --kind daily|pre-deploy` — 온라인 백업 API + `quick_check` 검증, 일일 보존 14개
- 복구: 서비스 중지 후 `python3 -m server.backup restore --db <db> --file <backup>` (자동 pre-restore 백업·검증)
- 준비 상태: `GET /admin/idle-ops/api/v1/health/ready`가 저장소·스키마·여유 공간을 반영(실패 503)
- 저장 내용: 관리자 `username`별 상태 JSON과 30일 TTL 액션 로그. 비밀·토큰·쿠키는 저장하지 않습니다.
- 상세 절차: [`docs/DB_MIGRATION.md`](docs/DB_MIGRATION.md)
