# 경제 명세 — Idle Ops

## R3 · Commit economy

| Measure | Rule |
| --- | --- |
| First observation | Zero XP, including projects migrated from Phase 2. |
| Eligible commit | Verified reachable commit, added after baseline, with at most one parent. Each repository/SHA pays once. |
| Award | 10 XP multiplied by the current project bonus. At 1.1×, a new commit awards 11 XP. |
| Project Level | `1 + floor(project XP / 100)`. |
| World XP and Level | Sum of the audience's project XP. The same level curve applies, with 100 XP per level and the remainder shown toward the next level. |
| Automatic tier | Cache at level 1; App 5; DB 10; GPU 20; Quantum 40. |
| Reboot | Requires 100 eligible commits since the last reboot. Resets the run counter and adds +0.1× for future awards, capped at 2×. |
| Retained on reboot | Lifetime XP, tier, ledger, issue and CI state, and prior awards. |

Time, telemetry and demo interactions produce zero XP. Issue and CI events alter
the visual world through verified state. The code constants are in
[`server/twin.py`](../server/twin.py). Sections below describe the archived
Phase 2 economy and its preserved records.

수치·공식의 정본은 [`server/economy.py`](../server/economy.py)입니다. 이 문서는 현재
밸런스를 사람이 검토할 수 있게 정리한 것이며, 코드와 다르면 코드가 우선합니다.

## 1. 원칙

- 서버 권위: 클라이언트는 액션 의도만 제출하고 비용·생산·보상은 서버가 계산합니다.
- 결정성: `advance(state, now)`는 시작 상태와 경과 시간만으로 결과가 정해집니다.
- 분할 정산: 레벨·누적 업적 임계값 단위로 구간을 나눠 정산하므로 폴링 빈도가 결과를
  바꾸지 않습니다(구간 상한 100,000).
- 상한: 부재 기간 상한은 없고(100년 overflow 가드만), 저장 값은 JS 안전 정수 범위
  `2^53−1`을 넘지 않습니다.

## 2. 초기 상태

| 항목 | 값 |
| --- | --- |
| 보유 요청 | 25 |
| 서버 | 캐시 1대 |
| 업그레이드 | 없음 |
| 누적 생산 | 0 |
| 레벨 | 1 (기본 RPS 0.5) |

## 3. 서버 비용과 생산

비용: `ceil(기본 비용 × 성장률^보유 수)`, 성장률은 전 티어 1.15.

| 티어 | 기본 비용 | 1대 | 2대 | 3대 | RPS/대 | 발열/대 |
| --- | --- | --- | --- | --- | --- | --- |
| cache | 15 | 18 | 20 | 23 | 0.5 | 0.4 |
| app | 150 | 173 | 199 | 229 | 5 | 1.5 |
| db | 2,000 | 2,300 | 2,645 | 3,042 | 45 | 5 |
| gpu | 25,000 | 28,750 | 33,063 | 38,022 | 400 | 14 |
| quantum | 400,000 | 460,000 | 529,000 | 608,350 | 4,000 | 40 |

- 구매 수량은 1, 10, `max`만 허용합니다. `max`는 보유 요청으로 살 수 있는 만큼이며
  최대 1,000대까지 계산합니다.
- 티어당 보유 상한은 1,000,000대입니다.

## 4. 업그레이드

비용: `ceil(기본 비용 × 성장률^레벨)`.

| ID | 이름 | 기본 비용 | 성장률 | 최대 | Lv1→Lv2→Lv3 비용 | 효과/레벨 |
| --- | --- | --- | --- | --- | --- | --- |
| network | Network Fabric | 300 | 2.4 | 15 | 300 → 720 → 1,728 | 전체 RPS +12% |
| cooling | Cooling Loop | 200 | 2.2 | 15 | 200 → 441 → 969 | 냉각 +6 |
| balancer | Load Balancer | 1,500 | 2.8 | 10 | 1,500 → 4,200 → 11,760 | 전체 RPS +8% |
| overclock | Overclock | 5,000 | 3.0 | 5 | 5,000 → 15,000 → 45,000 | RPS +30%, 발열 +25% |

## 5. RPS

```text
base_rps   = Σ(보유 수 × 티어 RPS)
global     = 1 + 업적 보너스 합 + 0.01 × (레벨 − 1)
upgrades   = (1 + 0.12·network) × (1 + 0.08·balancer) × (1 + 0.30·overclock)
tokens     = 1 + 0.02 × 가동 토큰
efficiency = min(1, 냉각 / 발열)   (최저 0.25)
rps        = base_rps × global × upgrades × tokens × efficiency
```

예시: 캐시 3·앱 2·DB 1·GPU 1·양자 1, network 3·cooling 5·balancer 1·overclock 1,
토큰 4, 누적 100만(레벨 11, 업적 8종 보유)

```text
base_rps = 1.5 + 10 + 45 + 400 + 4,000 = 4,456.5
global   = 1 + 0.27 + 0.10 = 1.37
upgrades = 1.36 × 1.08 × 1.30 ≈ 1.909
tokens   = 1.08
발열 79 / 냉각 40 → efficiency ≈ 0.5063
rps ≈ 4,456.5 × 1.37 × 1.909 × 1.08 × 0.5063 ≈ 6,375
```

## 6. 레벨

```text
level             = floor(sqrt(total_lifetime / 10,000)) + 1
level 시작 누적    = (level − 1)² × 10,000
다음 레벨 누적     = level² × 10,000
```

| 레벨 | 시작 누적 | 다음 레벨 |
| --- | --- | --- |
| 1 | 0 | 10,000 |
| 2 | 10,000 | 40,000 |
| 3 | 40,000 | 90,000 |
| 4 | 90,000 | 160,000 |
| 5 | 160,000 | 250,000 |
| 6 | 250,000 | 360,000 |

레벨 보너스는 `+1% × (레벨 − 1)`입니다. 레벨과 업적 임계값은 누적 생산량만 보므로
요청 구매로 잔액이 줄어도 레벨은 내려가지 않습니다.

## 7. 업적

| ID | 조건 | 보너스 |
| --- | --- | --- |
| first_server | 서버 1대 보유 | +2% |
| ten_servers | 서버 10대 보유 | +2% |
| rps_100 | 기본 RPS 100 | +3% |
| rps_10k | 기본 RPS 10,000 | +3% |
| lifetime_1m | 누적 생산 100만 | +4% |
| level_10 | 레벨 10 | +3% |
| first_reboot | 첫 Reboot | +5% |
| tokens_10 | 토큰 10개 | +5% |

전부 달성 시 보너스 합계 +27%. 업적은 해제 후 영구 유지됩니다.

## 8. 발열과 냉각

```text
heat     = Σ(보유 수 × 티어 발열) × (1 + 0.25 × overclock)
cooling  = 10 + 6 × cooling 레벨
thermal% = min(1, heat / cooling)
```

발열이 냉각을 넘으면 효율이 `cooling / heat`로 떨어지고 최저 25%에서 멈춥니다.

## 9. Reboot와 토큰

```text
tokens_gain = floor(sqrt(run_lifetime / 1,000,000))   (run_lifetime < 1,000,000이면 0)
```

| run_lifetime | 지급 토큰 |
| --- | --- |
| 999,999 | 0 |
| 1,000,000 | 1 |
| 4,000,000 | 2 |
| 25,000,000 | 5 |

Reboot 시 요청 25, 런 누적 0, 서버 캐시 1, 업그레이드 초기화. 누적 생산·토큰(+지급)·
Reboot 횟수·업적은 유지합니다. 토큰은 개당 전체 RPS +2%입니다.

## 10. Reset과 Hard Reset

- **Reset**(소프트): 런만 초기화합니다. 서버·업그레이드·요청은 초기값, 누적 생산·토큰·
  Reboot 횟수·업적·생성 시각은 유지.
- **Hard Reset**: 모든 값을 초기화합니다. admiral 역할과 `confirm: "HARD RESET"`이
  필요하며, 실행 전 `pre-hardreset` 백업을 남깁니다.

## 11. 시간·시계 정책

| 상황 | 처리 |
| --- | --- |
| 정상 경과 | 전 구간 정산 |
| 0~300초 뒤로 간 시계 | 대기(보상 없음, `last_sim_at` 유지) |
| 300초 초과 과거로 점프 | 저장 시각이 24시간 이내 미래면 대기 |
| 저장 시각이 24시간 초과 미래 | 현재 시각으로 재동기화(`resync`) |
| 100년 초과 경과 | 100년분만 정산(`clamped`) |

관리자별 접속 시각·부재 시간은 월드 정산과 분리해 `admin_seen`에 기록합니다.

## 12. 상한과 가드

| 상수 | 값 | 의미 |
| --- | --- | --- |
| MAX_STATE_VALUE | 2^53−1 | 저장 수치 상한(JSON/JS 정밀도) |
| MAX_SIM_SECONDS | 100년 | 정산 가드 |
| MAX_BUY | 1,000 | `max` 구매 계산 상한 |
| MAX_SERVERS_PER_TIER | 1,000,000 | 티어 보유 상한 |
| MAX_BODY | 8 KiB | 요청 본문 상한 |
| CLOCK_REWIND_TOLERANCE | 300초 | 소규모 역행 허용 |
| MAX_FUTURE_SKEW | 24시간 | 미래 시각 재동기화 기준 |

## 13. 멱등성과 저장

- 액션은 `opId`(8~64자 `[A-Za-z0-9_-]`)로 30일간 유일 키를 유지하며, 같은 `opId` 재시도는
  상태를 두 번 바꾸지 않고 동일 결과를 돌려줍니다.
- 모든 상태 전이는 `BEGIN IMMEDIATE` 트랜잭션 안에서 처리하고, 시계도 트랜잭션 안에서
  읽습니다.
- 저장은 SQLite WAL, 스키마 버전은 `PRAGMA user_version`(현재 4). 자세한 백업·복구는
  [`DB_MIGRATION.md`](DB_MIGRATION.md).

## 14. 밸런스 변경 절차

- 버전: `server/content.py`의 `BALANCE_VERSION`(현재 **1**).
- 지문: `GET /admin/idle-ops/api/v1/content`의 `balanceHash`(현재 `3c25713b78cb6bc6`).
  `tests/test_content.py`가 이 값을 고정 검증하므로, 승인 없이 수치를 바꾸면 테스트가
  실패합니다.
- 변경 시: PO 승인 → `economy.py` 수정 → `BALANCE_VERSION` 증가 → 이 문서 표 갱신 →
  지문 테스트 갱신 → 근거(시뮬레이션·QA) 첨부.
- 밸런스는 코드 상수이며 환경변수로 바꾸지 않습니다. 피처 플래그는 기능 노출만 제어하고
  수치에는 관여하지 않습니다.
