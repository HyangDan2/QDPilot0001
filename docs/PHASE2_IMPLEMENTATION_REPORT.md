# QDPilot0001 Idle Ops — Phase 2 구현 보고서

- 대상 워크오더: `QDPilot0001 — Idle Ops Phase 2 개발 워크오더` (Core Stabilization & Game Engine Evolution)
- 브랜치: `feature/phase2-core-evolution` (base: `main` `326709d`)
- 작성일: 2026-11-25 (KST)
- 상태: **PO 리뷰 요청** — 운영 배포·main 병합은 수행하지 않았습니다.

> **Phase 2.1 정정(2026-11-25)**: 이 문서는 Phase 2 제출(`bd63a75` 계열) 시점 기록입니다.
> 이후 교정 워크오더(WO-01~10)로 테스트 수(128→207 Python, +22 Node), 스프라이트 방향
> (왼쪽→자산별 `facing`, 향단이 원본은 오른쪽), manifest v2→v3 지연 로딩, 스키마 v2→v4,
> readiness 응답(상세 `storage`→불리언 `checks`)이 바뀌었습니다. 본문의 해당 부분에는
> 정정을 반영했고, 최신 결과는
> [`PHASE2_1_CORRECTION_REPORT.md`](PHASE2_1_CORRECTION_REPORT.md),
> [`PHASE2_1_QA_REPORT.md`](PHASE2_1_QA_REPORT.md),
> [`PHASE2_1_RELEASE_GATE.md`](PHASE2_1_RELEASE_GATE.md)를 따릅니다.

이 문서는 구현 범위와 근거를 정리합니다. QA 결과·성능 수치는
[`PHASE2_QA_REPORT.md`](PHASE2_QA_REPORT.md), [`PERFORMANCE_REPORT.md`](PERFORMANCE_REPORT.md)에,
경제·연출·아트·DB 규격은 각각 [`ECONOMY_SPEC.md`](ECONOMY_SPEC.md), [`ANIMATION_SPEC.md`](ANIMATION_SPEC.md),
[`ASSET_REQUIREMENTS.md`](ASSET_REQUIREMENTS.md), [`DB_MIGRATION.md`](DB_MIGRATION.md)에 있습니다.

## 1. 개발 원칙 준수

| 원칙 | 준수 내용 |
| --- | --- |
| 기존 정상 기능 유지 | 기존 관리자 콘솔·공개 관전·SSO 구조 유지. 기존 61개 테스트를 유지 또는 더 엄격한 테스트로 대체 |
| 기존 운영 DB 보존 | 스키마 변경은 가산적(`ALTER TABLE … ADD COLUMN`, `user_version` 0/1→2). 레거시 `players` 행은 1회 이관 후 원본 보존 |
| SSO·공개 권한 구조 유지 | 관리자 경로 `X-HD2Lab-User` 필수, 공개 경로 쓰기 405, `/content`·readiness는 공개에 미노출 |
| Raspberry Pi 적합 경량 구조 | 순수 표준 라이브러리 + SQLite 단일 파일. Redis 등 신규 인프라 없음. 상시 tick 루프 없음 |
| 하드코딩 최소화 / SRP | 경제 규칙은 `server/economy.py`, 저장·이벤트·스냅샷은 `server/store.py`, 콘텐츠 메타는 `server/content.py`, 화면은 `engine/*` 모듈로 분리 |
| 서버 권위 게임 로직 | 액션·보상·비용·정산은 서버 계산. Canvas는 서버 상태의 시각화만 담당 |
| 공식 향단이 디자인 준수 | 신규 아트 생성·기존 에셋 삭제·SVG 대체 없음. 캐논은 [`ART_DIRECTION.md`](ART_DIRECTION.md) 표로 고정 |
| Canvas 2D 유지 | 대형 엔진 전환 없음. 모듈 분리와 상태 머신만 추가 |
| 밸런스 무단 변경 금지 | 상수·공식 변경 없음. 밸런스 지문 `3c25713b78cb6bc6`를 테스트로 고정 |

## 2. 환경

- Python 3.13.2(표준 라이브러리만), SQLite 3.45.3(WAL), Vanilla ES 모듈 + Canvas 2D
- 로컬 검증 호스트: macOS 15.7.9(Apple M1, arm64) 개발 머신
- Raspberry Pi 1차 실측(2026-11-25, **Phase 2 시점 `bd63a75`**): `hyogidevrsp`(aarch64 4코어, Debian 12,
  Python 3.11.2)에 브랜치를 임시 클론해 128개 테스트 OK, 공개 1/10/100명·soak 전 구간 HTTP 200·lock 0,
  headless Chromium FPS 18~21을 확인했습니다(운영 Nginx 경유·실기 디스플레이는 미실측). **Phase 2.1
  교정 이후 Pi 재측정은 아직 수행하지 않았습니다.**
- 테스트: `python3 -m unittest discover -s tests -t .` → **207개 통과**(Phase 2 기준선 61개 →
  Phase 2 128개 → Phase 2.1 207개, WO-09 커밋 시점 187). Node 테스트 22개(`node --test tests/js/*.test.mjs`)도 통과합니다.

## 3. Phase 2A — 게임 경제 엔진 안정화

### 2A-01 지연 시뮬레이션 정합성 (`e5185c2`)

- `economy.advance()`가 레벨 상승·업적 임계값(누적 생산량/레벨) 단위로 구간을 나눠 정산합니다.
  구간 경계를 넘는 순간 생산량(RPS)을 재계산하므로 폴링 횟수가 결과를 바꾸지 않습니다.
- 초 단위 반복 시뮬레이션 없음. 구간 상한 `MAX_SETTLE_SEGMENTS = 100,000`(초과 시 잔여분은 현재
  RPS로 1회 정산하는 극단 가드).
- 허용 오차: 절대 `1e-6`, 또는 상대 `1e-9` 중 큰 값. 정수 필드(토큰·Reboot·서버·업그레이드·업적)와
  레벨은 완전 일치를 요구합니다(`SettlementFrequencyTests.assert_equivalent`).
- 검증: 1/8/15초 간격 6시간 정산 == 6시간 일괄, 15초/1시간/1일 간격 30일 정산 == 30일 일괄.
- `advance()`는 `segments`, `level_from`, `level_to`를 반환해 구간 정산 사실을 관측할 수 있습니다.

### 2A-02 Reboot / Reset / Hard Reset 정책 (`8f2c190`)

| 항목 | Reboot | Reset | Hard Reset |
| --- | --- | --- | --- |
| 요청·설비·일반 업그레이드·현재 런 생산량 | 초기화 | 초기화 | 초기화 |
| 누적 생산량·레벨·업적 | 유지 | 유지 | 초기화 |
| 가동 토큰 | 유지 + 지급 | 유지 | 초기화 |
| Reboot 횟수 | +1 | 유지 | 초기화 |

- Hard Reset은 `admiral`(Server-HQ 최고 관리자) 역할에서만 가능하며, 본문 `confirm: "HARD RESET"`이
  정확히 일치해야 합니다. 일반 `/actions` 경로로는 실행할 수 없습니다(405/400).
- 실행 직전 `pre-hardreset` 백업을 만들고 실패 시 리셋을 중단(fail closed). `audit_log`에 이전 상태
  요약·opId·백업 파일명을 남기며, 공개 이벤트 스트림에는 actor·opId·백업 경로가 노출되지 않습니다.
- 관리자 화면에서 일반 Reset과 Hard Reset UI를 분리(위험 구역 + 확인 입력 다이얼로그).

### 2A-03 시간 역행·동시성 (`61daf4d`)

- 정산 시각은 `BEGIN IMMEDIATE` 획득 **이후** 서버 시계로 결정합니다(`world_reads_the_clock_inside_the_transaction`).
- `last_sim_at`은 절대 역행하지 않습니다. 5분 이내 소폭 역행은 대기(지급 없음), 24시간 초과 미래
  시각은 재동기화(이미 정산한 구간 재지급 없음), 100년 초과 경과는 가드 구간만 정산.
- 동시 요청·스테일 읽기에도 중복 지급이 없음을 저장소 레벨에서 검증합니다.

### 2A-04 수치 안정성 (`af889a5`)

- `MAX_STATE_VALUE = 2^53−1`로 상태를 JS 안전 정수 범위에 묶고, 모든 계산은 `isfinite` 검사 후 포화합니다.
- 서버 보유 상한 `1,000,000`대/티어, 구매 수량 `1/10/max`(max 계산 상한 1,000), 비정상 타입 거부.
- JSON `NaN`/`Infinity` 리터럴 거부, 직렬화 결과에 비유한 값이 없음을 테스트로 고정.
- 저장 포맷(JSON 문자열, 스키마 v2)은 변경하지 않았습니다 → 사전 마이그레이션 검증이 필요한 호환성
  변경 없음. UI는 `formatCount()`(만/억/조)로 압축 표기하고 정확한 값은 상태 필드에 그대로 둡니다.

## 4. Phase 2B — API 및 서버 운영 안정화

### 2B-01 공개 관전 스냅샷 캐시 (`f348e80`)

```text
Admin Action ──► Store (BEGIN IMMEDIATE, SQLite) ──► generation++
                               │
Public GET ──► SnapshotCache(read-only conn, TTL 1s, single-flight) ──► advance in memory
```

- 공개 GET은 SQLite에 쓰지 않습니다(`test_public_read_does_not_touch_the_database`). 저장 상태를
  읽기 전용 연결(`mode=ro`)로 복사해 메모리에서 현재 시각까지 정산 후 TTL 1초 동안 공유합니다.
- 단일 락으로 single-flight 갱신 → 동시 100명도 TTL 구간당 1회 읽기. 관리자 액션 커밋 시
  `generation`이 올라가 즉시 무효화되므로 공개 뷰가 관리자 변경을 바로 봅니다.
- Rate Limit: **서버 내장 제한은 두지 않았습니다.** 공개 GET 비용은 TTL 1초 스냅샷으로 읽기
  1회/초 수준이고, 요청 본문은 8KiB, listen backlog는 128입니다. 외부 폭주는 Nginx
  `limit_req`/`limit_conn`으로 막는 정책을 권고하며, **운영 Nginx 설정에는 아직 적용·검증하지
  않았습니다**(정책 상세: [`PHASE2_1_CORRECTION_REPORT.md`](PHASE2_1_CORRECTION_REPORT.md) 5장).

### 2B-02 멱등 처리 (`9c1bbf9`)

- `world_ops`에 `action_json`·`result_json`을 저장해 같은 `opId` 재시도는 기록된 결과를 그대로 반환합니다.
- 같은 `opId`에 다른 액션이 오면 충돌(409) — 조용히 덮어쓰지 않습니다.
- 30일 TTL 정리(`world_ops`, 레거시 `action_ops`).
- 프론트엔드(`static/assets/app.js`)는 `localStorage`에 진행 중 opId를 보관하고, 응답 유실 시 같은
  opId로 재전송합니다. 이전 opId의 성공 여부를 확인하기 전에는 새 opId를 발급하지 않습니다.

### 2B-03 관리자 부재 보상 (`4a701c5`)

- 월드 정산 시각(`world.last_sim_at`)과 관리자별 확인 시각을 분리했습니다. `admin_seen`은 관리자별
  `last_seen`(마지막 접속)·`last_reward_at`(마지막 보상 확인)·확인 횟수를 저장합니다.
- 부재 보상은 저장된 정산 결과를 "관리자가 마지막으로 본 시점" 기준으로 표시할 뿐, 별도 지급을
  하지 않습니다. 공개 관전자의 접속 여부는 월드 성장에 영향을 주지 않습니다.

### 2B-04 이벤트 스트림 (`20888e4`)

- 이벤트 ID = 커서. `since` 이후 이벤트를 오름차순으로 반환하며, 응답 크기는 `EVENT_LIMIT=60`,
  `EVENT_BYTES=24KB`로 제한합니다. 보존은 `EVENT_RETENTION=400`개(쓰기 시 프루닝).
- 커서가 저장 범위를 지나면 `cursorReset: true` + 현재 커서를 내려 클라이언트가 재동기화합니다
  (DB 복원 후에도 안전). 오래 미접속 후에는 최신 구간만 반환해 연출 폭주를 막습니다.
- 공개 응답에는 actor/opId/백업 경로/message를 넣지 않으며(`test_public_event_stream_keeps_admin_details_out`),
  감사 로그(`audit_log`)와 공개 이벤트(`world_events`)는 테이블부터 분리되어 있습니다.
- 설치·강화·레벨업 이벤트는 한 트랜잭션 안에서 커밋 순서대로 기록됩니다.

### 2B-05 운영·백업 (`c1d5b06`)

- `python3 -m server.backup create|verify|restore` CLI. SQLite 온라인 백업 API + `quick_check` 검증,
  실패 시 파일 삭제 후 예외. `daily`(보존 14)·`pre-deploy`·`pre-hardreset`·`pre-restore`·`manual`.
- 복구는 서비스 정지 상태에서 `pre-restore` 백업 → 스테이징 → `os.replace` → `quick_check` 순서이며,
  DB가 잡혀 있으면 중단합니다.
- `user_version=4` 관리(Phase 2.1에서 2→4), 신버전 DB를 구버전 코드가 열면 `SchemaTooNew`로 기동
  거부(롤백은 pre-deploy 백업 복구).
- `GET /admin/idle-ops/api/v1/health/ready`는 필수 `world` 행·스키마 버전 일치·여유 공간(256MiB)을
  검증하고, 공개 응답에는 불리언 `checks`만 노출합니다(Phase 2.1에서 상세 `storage` 제거, 실패
  상세는 서버 로그로 이동). 실패 시 503.
- 절차 전문: [`DB_MIGRATION.md`](DB_MIGRATION.md).

## 5. Phase 2C — Canvas 게임 엔진 고도화

### 2C-01 모듈 분리 (`fdd06a8`)

```text
static/public/assets/
  engine.js            # 기존 import 경로 유지용 재수출
  engine/
    config.js          # 수치·티어·파티클 상한·표기
    animation.js       # STATES + Animator(상태 머신)
    sprites.js         # manifest 로드·시트 슬라이스·fallback
    effects.js         # 상한 있는 파티클/텍스트/링/슬래시 풀
    room.js            # 랙 슬롯 계산 + 정적 배경 캐시 + 랙 페인터
    motion.js          # 향단이 상태 머신(이동·공격·수리·연출)
    scene.js           # WorldScene(루프·리사이즈·DPR·FPS·이벤트 브리지)
  sprites/manifest.json
```

워크오더의 권장 구조에서 `core/renderer/combat/enemy/facility/particles/camera/asset-loader/event-bridge`는
파일 수를 늘리는 대신 `scene`·`room`·`effects`·`sprites`로 통합했습니다(워크오더가 허용한 "과도한
파일 분리 회피"). `world.js`는 기존 import 경로(`engine.js`)만 유지합니다.

### 2C-02 스프라이트 애니메이션 (`fdd06a8`, `5e1a997`, `b23d52d`)

- 워크오더 표를 그대로 반영: idle 8@8 loop, run 12@12 loop, attack 12@16 once(hitFrame 6),
  repair 8@10 loop, levelup 12@12 once, install 12@12 once.
- `Animator`는 상태별 프레임 수·FPS·loop, `restart`, `hitFrame` 도달 콜백, 완료 상태를 제공합니다.
  프레임마다 다른 지속 시간이 필요하면 상태에 `durations`(초 배열, 길이 = 프레임 수)를 주면 되고,
  없으면 균등 `1/fps`를 씁니다(현재 전 상태 균등).
- 시트는 v2 `horizontal-strip`(셀 320×640)과 v3 `atlas`/`sequence`(768×768 셀)를 모두 읽습니다.
  셀 크기 차이는 중앙 정렬로 보정합니다. 앵커는 `[0.5, 1.0]`(발 위치)이고, 캐릭터 원본 방향은
  자산별 `facing`으로 선언합니다 — **현행 향단이 원본은 오른쪽**, 버그는 왼쪽입니다. 엔진은
  바라보는 방향과 이동 방향이 다를 때만 반전합니다(Phase 2.1 WO-08에서 수정).
- 시트가 없으면(`sheet: null`) 단일 PNG fallback으로 동작합니다. 이번 단계에서 신규 시트는 만들지
  않았고, fallback 경로만으로 모든 상태 전환을 검증했습니다.
- 매니페스트는 `no-cache`로 재검증합니다(캐시된 구버전 매니페스트 문제 수정).
- 일회성 동작(공격·수리·레벨업·설치)은 프레임 수/FPS로 계산한 실제 길이만큼 재생합니다(타이머가
  더 짧아 동작이 잘리던 문제 수정).

### 2C-03 캐릭터 동작 (`fdd06a8`, `95f95d5`, `d7a1f62`)

- 상태: idle → (run → attack | repair) + levelup/install 일회성 오버레이. 정지 시 사인 호흡.
- 이동 방향과 스프라이트 방향 일치, 걷기 먼지·홉 연출, 도착 후 상태 전환.
- **이동 중 버그 위치를 매 프레임 재추적**하고, 공격 사거리(`MOTION.attackRange=26px`)에서 멈춰
  공격합니다. 타격은 시트가 있으면 `hitFrame/16`, 없으면 0.16초에 발생하고, 이후 후딜레이 뒤 idle로
  복귀합니다. 추적 검증: 90초 동안 공격 12회, 추적 목표 갱신 55회, 콘솔 오류 0.
- 리사이즈 시 좌표·목표를 화면 안으로 재클램프하며, 좁은 화면에서 큰 동작이 잘리지 않도록
  화면 크기 기반 마진을 사용합니다(320px 검증).
- `PATROL`(자유 배회)과 `CELEBRATE`의 독립 상태는 만들지 않았습니다. 레벨업/설치 일회성 연출이 그
  역할을 대신하며, 별도 배회 상태는 후속 아이디어로 남깁니다.

### 2C-04 서버실 시각화 (`fdd06a8`)

- 랙 슬롯은 실제 보유 수량에서 계산합니다. 보유 0인 티어는 그리지 않고, 티어별 팔레트·높이·우선순위로
  차별화하며, 다음 증설 위치는 점선 슬롯으로 표시합니다.
- 설치 이벤트(서버 수 증가)는 링·버스트·플로팅 텍스트로 연출하고, 발열이 70%를 넘으면 화면 틴트로
  표시합니다.
- 320/390/768/1440/1920px에서 랙 수·겹침·가로 스크롤을 검증했습니다(스크린샷: QA 보고서).
- 카메라 줌·팬은 이번 단계에서 구현하지 않았습니다(고정 시점 유지). 확장 시점에 `WorldScene`의
  변환 행렬 도입으로 붙일 수 있는 상태입니다.

### 2C-05 렌더링 최적화 (`fdd06a8`)

- 정적 배경(벽·바닥)은 `RoomPainter`가 오프스크린 캔버스에 1회 렌더 후 재사용합니다.
- 이벤트 연출은 `world.js`가 event id 집합으로 중복을 제거하고 커서 오름차순으로 적용합니다.
  첫 로드·복원(`cursorReset`/`eventGap`) 때는 과거 연출을 재생하지 않고 피드만 다시 채웁니다.
- 파티클 160·텍스트 24·링 14·슬래시 8·버그 3으로 상한을 두고, 초과분은 가장 오래된 것부터 버립니다.
- 탭이 숨겨지면 rAF 루프를 멈추고, Reduced Motion에서는 버그 이동·장식 파티클을 끕니다.
- DPR은 2로 제한, 60Hz 고정 스텝 accumulator. `?fps=1`에서 FPS·프레임 타임 오버레이,
  `window.__scene.stats`로 자동화 측정이 가능합니다.
- 장시간 실행: Phase 2에서 브라우저 **47분** 연속 실행으로 JS 힙·FPS·콘솔 오류를 샘플링했습니다
  (목표 1시간에는 미달, QA 보고서 6.2). Phase 2.1 릴리스 게이트에서 1시간 재실행 결과를 별도
  기록합니다.

## 6. Phase 2D — 향단이 아트워크 표준화 (`6566929`)

- `art/`는 웹 루트 밖에 두고, 원본(`source/`)은 Git 추적에서 제외, 검수 전 내보내기(`export/`)만
  유지합니다. 런타임 반입은 `static/public/assets/sprites/`로 복사 후 매니페스트 `sheet`를 채우는
  절차입니다. 워크오더의 권장 하위 폴더(`sprites/idle/...`)는 캐릭터·버그 단위(`art/hyangdani`,
  `art/bugs`)로 단순화했습니다 — 파일명이 상태를 이미 포함하고, 캐릭터 추가 시 폴더만 늘리면 되기
  때문입니다.
- [`ART_DIRECTION.md`](ART_DIRECTION.md): 캐논 11항목 표, 팔레트 토큰, 실루엣, 시트 규격(320×640,
  최대 4096px 폭, 앵커·방향), bug 512×512, 연출은 이미지에 굽지 않음.
- 매니페스트는 Phase 2의 v2에서 **v3**로 확장했습니다(Phase 2.1 WO-08): 상태별 `source`
  (strip/atlas/sequence), `fallback {file,facing}`, 자산별 `facing`, `anchor`/`anchors`,
  `durations`/`seconds`, `hits`, `maxTexture`. v2는 계속 읽습니다(호환표:
  [`ASSET_REQUIREMENTS_V3.md`](ASSET_REQUIREMENTS_V3.md) 6장). **에셋별 개별 버전 필드는 넣지
  않았습니다** — 현 단계는 캐릭터 단위 버전으로 충분합니다.
- 신규 아트 생성·기존 삭제·SVG 대체 없음. 제작 요청 목록은
  [`ASSET_REQUIREMENTS_V3.md`](ASSET_REQUIREMENTS_V3.md)를 따릅니다(P0 수리, P1 레벨업/설치,
  P2 idle/run/attack/repair, P3 버그 시트). 테스트용 번호 타일 아틀라스는 `tests/fixtures/`에만
  있으며 배포되지 않습니다.

## 7. Phase 2E — 게임 콘텐츠 확장 설계 (`9073ed4`)

- [`GAME_DESIGN.md`](GAME_DESIGN.md): 캐릭터 레벨/EXP/전투·수리 파워/장비·스킬 슬롯, 버그 등급
  (Null Bug~System Crash), 서버실 5단계(T1 Cache ~ T5 Quantum Lab), 자동 전투 성장 루프를 설계로
  정리했습니다. 보상·전투 계산은 서버, Canvas는 시각화 전용, 공개 관전자 수는 결과에 영향 없음,
  오프라인 정산은 기존 지연 시뮬레이션 위에 얹는 방향을 명시했습니다.
- [`ECONOMY_SPEC.md`](ECONOMY_SPEC.md): 현재 밸런스의 계산 가능한 표(비용·RPS·레벨 바닥·토큰·가드)와
  **밸런스 변경 절차**(PO 승인 → `BALANCE_VERSION` 증가 → 지문 갱신 → 테스트 갱신).
- `server/content.py`: `BALANCE_VERSION`, 밸런스 지문(SHA-256 앞 16자리), 임계값, 피처 플래그
  (`achievement_rewards`, `daily_bonus`, 기본 **off**, 환경변수 `IDLE_OPS_FEATURE_<NAME>`로만 on).
  `GET /admin/idle-ops/api/v1/content`(인증 필요, 공개 미노출). 지문은 테스트로 고정되어 밸런스를
  무단으로 바꾸면 실패합니다.
- 활성화된 신규 밸런스·전투 기능은 없습니다. 확장 기능은 플래그 뒤의 설계/인터페이스 단계입니다.

## 8. Phase 2F — 통합 QA (`cd936af`, `b5fa2f4` + 이 보고서 커밋)

- `tools/qa_perf.py`(표준 라이브러리): public/action/burst/adminread 모드, 멀티프로세스 관전자,
  P50/P95/P99/max, 계약 위반·SQLite lock 오류 검출, RSS 샘플. 로컬 프록시를 우회해 서버 자체를 측정합니다.
- 연결 폭주 시 stdlib 기본 backlog(5)가 SYN을 드롭해 ~1초 재전송 꼬리가 생기던 문제를 수정
  (TCP·UDS 모두 128). 성능 수치·UI 매트릭스·장시간 실행 결과는 QA 보고서에 정리했습니다.

## 9. 기존 결함 수정 결과

| 결함 | 원인 | 수정 | 검증 |
| --- | --- | --- | --- |
| 접속 빈도에 따라 성장 결과가 달라짐 | 정산 시작 RPS를 전체 구간에 적용 | 임계값 단위 구간 정산 | `SettlementFrequencyTests` |
| Reset 구현과 README 불일치 | Reset이 토큰·레벨을 지움 | Reboot/Reset/Hard Reset 분리 | `test_reset_keeps_meta_progress` 등 |
| 시계 역행·동시 요청 중복 지급 | 트랜잭션 밖 시각, 역행 미처리 | 트랜잭션 내 시각, 역행/미래 정책 | `ClockTests`, `test_clock_anomaly_never_double_grants` |
| 대형 수치 오버플로/NaN | 무한 성장 | 2^53−1 포화, 유한성 검사 | `NumericSafetyTests` |
| 공개 조회마다 SQLite 쓰기 | 공개 GET이 정산을 저장 | 스냅샷 캐시(읽기 전용) | `SnapshotCacheTests`, API 테스트 |
| 재시도 시 이중 적용 | opId 결과 미저장 | action/result 기록·충돌 처리 | `test_repeating_an_operation_replays…` |
| 공개 접속에 묶인 관리자 부재 표시 | 단일 타임스탬프 | `admin_seen` 분리 | `test_admin_timestamps_are_per_admin` |
| DB 복원 후 커서 불일치 | 최대 ID 초과 커서 미처리 | `cursorReset`/`gap` | `test_cursor_past_the_newest_event…` |
| 백업·복구 수단 없음 | - | `server.backup` CLI, readiness | `BackupTests`, `StoreTests` |
| 원샷 애니메이션이 잘림 | 타이머 < 프레임 길이 | 프레임 길이로 타이머 계산 | CDP 스크린샷(2E) |
| 구버전 매니페스트 캐시 | `force-cache` | `no-cache` 재검증 | Chrome에서 version 2 확인 |
| 320px에서 향단이 잘림 | 고정 40~60px 마진 | 화면 크기 기반 높이·마진 | [qa-ui-320-fixed.jpg](images/phase2/qa-ui-320-fixed.jpg) |
| 버그를 쫓지 않고 빈 곳을 타격 | 목표 좌표 스냅샷 | 매 프레임 재추적 + 사거리 | 90초 추적 테스트(공격 12회) |
| 대량 접속 시 1초 지연 꼬리 | backlog 5 | 128 | 성능 보고서 |

## 10. 변경 파일 목록

```text
README.md
docs/ANIMATION_SPEC.md          (신규)
docs/ART_DIRECTION.md           (신규)
docs/ASSET_REQUIREMENTS.md      (신규)
docs/DB_MIGRATION.md            (신규)
docs/ECONOMY_SPEC.md            (신규)
docs/GAME_DESIGN.md             (신규)
docs/PERFORMANCE_REPORT.md      (신규, 이번 제출)
docs/PHASE2_IMPLEMENTATION_REPORT.md (신규)
docs/PHASE2_QA_REPORT.md        (신규)
docs/notes.md
art/.gitignore                  (신규)
art/README.md                   (신규)
art/bugs/{source,export}/.gitkeep        (신규)
art/hyangdani/{source,export}/.gitkeep   (신규)
server/__main__.py
server/api.py
server/backup.py                (신규)
server/content.py               (신규)
server/economy.py
server/store.py
static/assets/app.js
static/assets/styles.css
static/index.html
static/public/assets/engine.js
static/public/assets/engine/{config,animation,sprites,effects,room,motion,scene}.js (신규)
static/public/assets/sprites/manifest.json (신규)
static/public/assets/world.js
tests/test_api.py
tests/test_backup.py            (신규)
tests/test_content.py           (신규)
tests/test_economy.py
tests/test_store.py
tools/qa_perf.py                (신규)
```

## 11. Phase별 커밋 SHA

| Phase | 커밋 | 내용 |
| --- | --- | --- |
| 2A-01 | `e5185c2` | 구간별 정산 |
| 2A-02 | `8f2c190` | Reset/Reboot/Hard Reset 분리 |
| 2A-03 | `61daf4d` | 트랜잭션 내 시각·시계 정책 |
| 2A-04 | `af889a5` | 수치 가드 |
| 2B-01 | `f348e80` | 공개 스냅샷 캐시 |
| 2B-02 | `9c1bbf9` | 멱등 결과 저장·충돌 |
| 2B-03 | `4a701c5` | 관리자별 타임스탬프 |
| 2B-04 | `20888e4` | 이벤트 커서·보존 |
| 2B-05 | `c1d5b06` | 백업·복구·readiness |
| 2B-05 | `115e79d` | v1→v2 제자리 업그레이드 회귀 테스트 |
| 2C-01/02 | `fdd06a8` | 엔진 모듈 분리·스프라이트 상태 머신·월드씬 |
| 2C-02 | `5e1a997` | 매니페스트 캐시 재검증 |
| 2C-02 | `b23d52d` | 원샷 동작 길이 |
| 2C-02 | `70c7270` | 프레임별 지속 시간 |
| 2C-03 | `d7a1f62` | 이동 중 버그 재추적·사거리 |
| 2C-03 | `95f95d5` | 좁은 화면 마진 |
| 2D | `6566929` | 아트 워크스페이스·디렉션·요구사항 |
| 2E | `9073ed4` | 콘텐츠 인터페이스·밸런스 지문·설계 문서 |
| 2F | `cd936af` | listen backlog |
| 2F | `b5fa2f4` | QA 하네스 |

Phase 2.1 교정(별도 커밋, 각 WO별 독립):

| WO | 커밋 | 내용 |
| --- | --- | --- |
| WO-01 | `72e6777` | Hard Reset을 백업 전부터 커밋 후까지 배타 게이트로 |
| WO-02 | `c573334` | Hard Reset에 실제 chairman 계정 역할 요구 |
| WO-03 | `fe0e77e` | 상한 정산·비용 가드·확인 지점 단조성 |
| WO-04 | `93688f6` | 미검증 백업 거부·복원 가역성 |
| WO-05 | `e48c998` | 관리자별 확인 지점 |
| WO-06 | `3bf72df` | `world.revision`·스냅샷 일관 공개 읽기 |
| WO-07 | `3b93a1a` | 관리자 변경 op 직렬화 |
| WO-08 | `7c6cfc2` | 자산별 facing·manifest v3·지연 로딩·아트 문서 |
| WO-09 | `2d21721` | readiness·타임아웃·보존 검토·보고서 정정 |
| WO-10 | 이 커밋 | 릴리스 게이트 회귀·장시간 실행·릴리스 문서 |

이 보고서·QA 보고서·성능 보고서는 2F 문서 커밋 하나로 제출합니다(자기 SHA는 커밋 목록에 표기하지 않음).

## 12. 신규 테스트 및 실행 결과

- Phase 2 제출 시점: **128개 통과** (기준선 main 61개 → 신규 70개, 기존 3개는 더 엄격한 테스트로 대체)
- Phase 2.1 시점(현재): **207개 통과** — `test_api.py` 35, `test_economy.py` 46, `test_store.py` 36,
  `test_backup.py` 4, `test_content.py` 8, `test_backup_hardening.py`, `test_checkpoints.py`,
  `test_economy_limits.py`, `test_hard_reset.py`, `test_ops_hardening.py`, `test_world_revision.py`,
  `test_release_gate.py` 포함.
- Node: `node --test tests/js/sprite_manifest.test.mjs tests/js/op_coordinator.test.mjs` → **22개 통과**
  (manifest v3 정규화·슬라이스·facing·지연 로딩/쿨다운, 관리자 op 조정).
- 실행 명령: `python3 -m unittest discover -s tests -t .`
- 파일별(Phase 2 시점): `test_api.py` 34(+14), `test_economy.py` 46(+17), `test_store.py` 36(+27),
  `test_backup.py` 4(신규), `test_content.py` 8(신규). Phase 2.1 신규 파일·케이스는
  [`PHASE2_1_QA_REPORT.md`](PHASE2_1_QA_REPORT.md)에 정리했습니다.

주요 신규 테스트:

```text
경제   SettlementFrequencyTests(fine/coarse/segments), ClockTests(5), NumericSafetyTests(6),
       ActionTests.reset_keeps_meta_progress / hard_reset_zeroes_everything
저장   멱등 재실행·충돌·레거시 이관·TTL 정리, 스키마 버전 거부, v1 제자리 업그레이드,
       백업·복원·readiness, 스테일 읽기·시계 이상·동시 정산 1회, 트랜잭션 내 시각,
       SnapshotCacheTests(읽기 전용·비영속·single-flight·무효화·이벤트)
API    중복 opId 재실행, 관리자/공개 타임스탬프, 공개 이벤트 정제, 커서 리셋,
       공개 읽기 무쓰기, 즉시 반영, Hard Reset 권한·확인·백업·감사, NaN/Infinity 거부
백업   daily 1회, verify 거부, 미검증 복원 거부, 복원 후 이전 DB 보존
콘텐츠 플래그 기본 off/환경변수 on/미지 플래그 거부, 지문 고정, 인증 필요, 공개 미노출
```

대체된 기존 테스트(커버리지 손실 없음): `test_backwards_clock_does_not_block_progress` →
`ClockTests` 5종, `test_reset_keeps_timestamps` → `test_reset_keeps_meta_progress`,
`test_reset_action_restores_the_world` → `test_reset_action_restores_the_run_and_keeps_meta`.

## 13. DB 변경 및 마이그레이션 검증

- `user_version` 0/1 → 2(Phase 2) → 3(관리자 확인 지점) → 4(`world.revision`).
  추가: `world_ops.action_json/result_json`, `admin_seen`, `audit_log`, `world.revision`,
  `admin_seen.observed_*`, `world_ops(created_at)` 인덱스. 기존 컬럼 삭제·의미 변경 없음.
- 기동 시 `CREATE TABLE IF NOT EXISTS` + 누락 컬럼 `ALTER TABLE`로 멱등 마이그레이션.
- 검증(실행 완료):
  - v1 스키마(신규 컬럼·테이블 없음) DB를 열어 제자리 업그레이드되고 월드 상태가 보존되는지
    (`test_v1_database_upgrades_in_place_and_keeps_the_world`). 프리뷰 DB 스냅샷으로도 동일 절차를
    수동 실행해 `user_version` 상향, 컬럼·테이블 추가, `state_json` 바이트 동일을 확인했습니다.
    Phase 2.1에서 v2→v4 가산 마이그레이션이 같은 테스트로 커버됩니다.
  - 신버전 DB를 구버전 코드가 거부하는지 (`test_schema_version_is_recorded_and_newer_schemas_are_refused`)
  - 백업 생성→검증→복원→검증 왕복 (`test_daily_backups_are_verified_and_pruned`,
    `test_restore_rolls_back_to_a_verified_snapshot`)
- 운영 DB에는 아무 변경도 적용하지 않았습니다(배포 금지 준수). 절차: [`DB_MIGRATION.md`](DB_MIGRATION.md).

## 14. 신규 아트워크 제작 요청 목록

[`ASSET_REQUIREMENTS.md`](ASSET_REQUIREMENTS.md) 3~5장에 규격·체크리스트로 정리했습니다. 요약:

- P0: 수리(repair) 전용 단일 이미지 1장 — 현재 idle 이미지로 대체 중
- P1: levelup 12프레임@12fps, install 12프레임@12fps 시트
- P2: idle 8@8, run 12@12, attack 12@16(히트 프레임 6), repair 8@10
- P3: 버그 alive/hit 시트
- 공통: v3 제작 캔버스 768×768, 자산별 `facing`(향단이 원본 오른쪽·버그 왼쪽), 앵커
  `[0.5, 1.0]` 또는 프레임별 `anchors`, 4096px 이하, 연출(파티클·글로우)은 이미지에 굽지 않음

## 15. 운영 배포 및 복원 절차

이번 제출에서는 배포하지 않았습니다. PO 승인 후 절차:

1. `python3 -m server.backup create --db <운영 DB> --kind pre-deploy`
2. 새 릴리스 배치(Server-HQ `docs/IDLE_OPS_OPERATIONS.md` 규약) → `systemctl restart hd2lab-idle-ops`
3. `GET /admin/idle-ops/api/v1/health/ready` 200, 관리자/공개 화면 스모크 테스트
4. 문제 시: 서비스 정지 → 이전 릴리스 복원 → `server.backup restore`로 pre-deploy DB 복구 → readiness 확인

세부 명령·경고는 [`DB_MIGRATION.md`](DB_MIGRATION.md) 3~4장을 따릅니다. Nginx 경로·인증 규약은 변경하지
않았습니다(예외 설계 없음).

## 16. 미해결 이슈와 후속 제안

1. **Raspberry Pi 1차만 실측** — 2026-11-25에 Pi 임시 인스턴스로 서버 부하(1/10/100명·soak)와
   headless Chromium FPS를 측정했습니다. 운영 Nginx 경유·WAN 관전자·GPU 있는 실기 디스플레이는
   미측정이므로, 배포 승인 시 운영 경로로 한 번 더 확인해야 합니다.
2. 공개 API Rate Limit 미도입 — 스냅샷 캐시로 요청 비용은 낮아졌으나, 외부에서 폭주하면 Nginx
   `limit_req`로 막는 것을 제안합니다.
3. 이벤트 보존 400개는 고정값입니다. 공개 관전 로그를 더 길게 보여주려면 보존·최대 응답 크기 정책을
   함께 조정해야 합니다.
4. 카메라 줌·팬, 자유 배회(PATROL) 상태, 쿨링 시각화는 미구현(2C 후속).
5. 캐릭터 레벨/전투는 설계·플래그까지만 준비되었습니다. 활성화하려면 별도 승인과 밸런스 검증이
   필요합니다.
6. 신규 아트 시트 납품 전까지 수리·레벨업·설치 연출은 fallback 이미지(수리는 idle)로 표시됩니다.
