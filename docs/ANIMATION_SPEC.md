# 애니메이션 명세 — Idle Ops

## R3 · Live animation

R3 retains the following V3.1 manifest, timing and artwork. Live snapshots
supply rack, agent and bug data. Idle is the default. Run bridges movement
between authoritative events. Repair requires an explicit current agent/issue
association. Attack plays for a fresh matching resolution, and defeat occurs
at the manifest hit frame. Dismissal and reconnect use current state. LevelUp
and Install use server level/tier changes. Reboot leaves unresolved bugs visible.
Reduced motion draws the same authoritative entities with static poses.

R3 caps visible actors at three bugs and four agents, with at most six queued
confirmed encounters. Rack capacity is 3/5/7/9 by canvas width. Projects can be
filtered and paged. Future polls, visibility returns and cursor discontinuities
recover from snapshots without replaying old events.

The Phase 2 state-machine and rack behavior below document the archived mode.
Atlas timing, slicing, fallback, boundaries and the render architecture carry
forward. See [R3 browser QA](R3_BROWSER_QA.md) for playback evidence.

공개 화면의 Canvas 2D 캐릭터·연출 규격입니다. 구현 정본은
[`static/public/assets/engine/`](../static/public/assets/engine)이며, 아트 납품 규격은
[`ASSET_REQUIREMENTS_V3.md`](ASSET_REQUIREMENTS_V3.md)를 따릅니다(구
`ASSET_REQUIREMENTS.md`는 v2 시점 기록).

## 1. 엔진 구조

| 파일 | 역할 |
| --- | --- |
| `config.js` | 티어 정의, 파티클/버그 상한, 이동·공격 상수, 수량 포맷 |
| `animation.js` | 상태 기본값(프레임·FPS·길이·루프·히트 프레임)과 `Animator` |
| `sprites.js` | manifest v2/v3 정규화·검증, 상태별 지연 로딩, 스트립/아틀라스/시퀀스 슬라이스, 자산별 facing |
| `motion.js` | 향단이 행동 상태 머신(대기/이동/공격/수리 + 원샷), manifest 기반 공격 길이·히트 시점 |
| `effects.js` | 파티클·슬래시·링·부유 텍스트 상한 풀 |
| `room.js` | 랙 배치(실제 보유 수), 정적 배경 캐시, 랙 드로잉 |
| `scene.js` | 60 Hz 시뮬레이션, rAF 렌더, DPR·가시성·모션 설정, FPS 미터 |

`engine.js`는 `WorldScene`과 `STATES`, `formatCount`만 다시 내보냅니다.
`engine/package.json`은 `"type": "module"`만 선언해 Node 테스트가 같은 파일을
직접 import하게 합니다(브라우저 동작에는 영향 없음).

## 2. 상태 정의

| 상태 | 프레임 | FPS | 길이 | 루프 | 히트 프레임 | 비고 |
| --- | --- | --- | --- | --- | --- | --- |
| idle | 8 | 8 | 1.0s | 예 | — | 대기 호흡 |
| run | 12 | 12 | 1.0s | 예 | — | 이동 |
| attack | 12 | 16 | 0.75s | 아니오 | 6 (0.375s) | 공격 1회 |
| repair | 8 | 10 | 0.8s | 예 | — | 수리 루프 |
| levelup | 12 | 12 | 1.0s | 아니오 | — | 레벨업 연출 1회 |
| install | 12 | 12 | 1.0s | 아니오 | — | 설치 연출 1회 |

원샷 상태의 길이는 항상 `프레임 수 ÷ FPS`와 일치합니다(attack 12/16=0.75,
levelup·install 12/12=1.0). **manifest v3가 상태별 `seconds`/`durations`/`fps`를
선언하면 로드 후 `Animator`가 그 값을 우선 사용**합니다(현행 manifest는 위 표와 동일).
`MOTION.attackSeconds`·`MOTION.repairSeconds`는 manifest를 읽기 전의 기본값입니다.

### 2.1 Run 12F 위상 (Phase 2.2.1 교정본)

12프레임을 좌·우 두 걸음(각 6프레임)으로 나눈 표준 달리기 사이클입니다. 무게 이동은
포즈가 만들고(지지 다리가 굽으면 골반이 내려감), 착지 프레임만 발바닥을 바닥선에
붙이며 공중·도달 프레임은 바닥선에서 띄웁니다.

| 프레임 | 위상 | 접지 | 비고 |
| --- | --- | --- | --- |
| 0 | contact (오른발 착지) | 바닥 (sole=하단선) | 앞다리 거의 편 자세, 머리카락이 위로 튐 |
| 1 | down (충격 흡수) | 바닥 | 골반 최저, 머리카락이 들려 늘어짐(관성) |
| 2 | passing | 바닥 | 반대 무릎이 앞으로 통과 |
| 3 | push-off | 발끝만 (2px) | 뒷다리 완전 신전, 상체 최고 |
| 4 | airborne | 22px 띄움 | 두 발 공중, 다리 교차 |
| 5 | reach | 8px 띄움 | 다음 착지를 향해 앞발 뻗기 |
| 6–11 | 위 6위상을 왼발로 반복 | 동일 | 두 번째 걸음(복제가 아님) |

`tools/wo0221_asset_audit.py`가 프레임마다 접지선·띄움 프레임·루프 유사도(IoU)를
측정하고, `tests/test_sprite_assets.py`가 12장 중복 없음·좌우 걸음 분리·공중
프레임 2~4개·루프 이음매가 사이클 최악값보다 나쁘지 않음을 검증합니다.

## 3. 행동 상태 머신

```text
idle ──(타이머 종료, 버그 있음)──▶ run ──(도착)──▶ attack ──(타이머)──▶ idle
  └──(타이머 종료, 버그 없음)────▶ run ──(도착)──▶ repair ──(타이머)──▶ idle
levelup / install : 원샷 오버레이(이동 일시정지 후 이전 모드 복귀)
reboot / reset    : 버그 제거, 이펙트 비움, idle로 복귀
```

- 대기 타이머는 1.2초(초기), 행동 후 0.5~1.6초 랜덤입니다.
- 살아 있는 버그가 있으면 무작위로 한 마리를 골라 쫓아가 공격하고, 없으면 랙으로
  가서 수리합니다.
- 이동 속도는 화면 폭의 0.32/초, 이동 중 12 Hz로 튀는 hop과 먼지 파티클을 냅니다.
- 공격 중에는 전진 lunge 26px, 회전 0.1 rad의 사인 곡선을 적용하고, 공격 히트
  이벤트 시점에 데미지 이펙트를 한 번 발생시킵니다.
- 공격 타이머와 lunge 진행률은 manifest의 attack 길이(`durationOf("attack")`)를 쓰고,
  히트 시점은 manifest의 히트 이벤트(`hitSeconds("attack")`)를 씁니다. 값이 없으면
  각각 0.75초·0.16초로 폴백합니다.
- 수리 타이머는 0.8초로 상태 길이와 동일합니다.
- 레벨업/설치 이벤트는 이동을 멈추는 원샷 오버레이로 재생하고 끝나면 이전 모드로
  돌아갑니다. 이미 원샷이 재생 중이면 새 원샷은 무시합니다.

## 4. Animator

- `elapsed`를 누적하고 `frameIndex = floor(elapsed × fps) % frames`(루프) 또는
  `min(frames−1, floor(elapsed × fps))`(원샷)로 프레임을 고릅니다.
- 상태에 `durations`(프레임별 초 배열, 길이 = `frames`)를 주면 프레임마다 다른 지속
  시간을 쓸 수 있습니다. 루프는 `sum(durations)` 주기로 감고, 없으면 위 균등 FPS를
  씁니다(현재 전 상태 균등).
- 원샷은 `elapsed ≥ seconds`에서 완료됩니다.
- `consumeHit()`는 히트 이벤트가 있는 원샷 상태에서 첫 히트 프레임에 도달했을 때
  **정확히 한 번** true를 돌려줍니다. 단일 이미지 fallback 재생에서도 manifest의
  프레임·FPS로 계산한 같은 시점을 씁니다(별도 하드코딩 없음).

## 5. 스프라이트 로딩과 슬라이스

- `sprites/manifest.json`을 `cache: "no-cache"`로 매 로드 시 재검증합니다(정적 서버가
  `Cache-Control: no-cache`를 보냄). 실패하면 `sprites.js` 내장 기본값(v3)을 씁니다.
- manifest는 v2(단일 `sheet` 문자열 + `fallback` 문자열)와 v3(상태별 `source`)를 모두
  읽습니다. 정규화·검증 결과는 `library.problems`에 남고, 잘못된 소스는 로드하지 않고
  fallback으로 내려갑니다. 상세 스키마는
  [`ASSET_REQUIREMENTS_V3.md`](ASSET_REQUIREMENTS_V3.md)를 따릅니다.
- 소스 3종:
  - `strip`: `프레임 폭 = 이미지 폭 ÷ 프레임 수`, 높이는 이미지 전체.
  - `atlas`: `columns`×`rows` 행 우선 그리드. `프레임 폭 = 이미지 폭 ÷ columns`,
    `프레임 높이 = 이미지 높이 ÷ rows`.
  - `sequence`: 프레임별 개별 PNG 목록(각 이미지 전체가 한 프레임).
- 시트가 없거나 이미지 로드에 실패하면 `fallback` 단일 이미지를 사용하고, 그것도
  없으면 idle 아트, 최후에는 절차적 도형으로 그립니다. 즉 아트 미납품 상태에서도 모든
  상태가 렌더링됩니다.
- **지연 로딩**: 시작 시 idle과 버그만 미리 읽고, run/attack/repair/levelup/install은
  처음 그릴 때 상태 단위로 읽습니다. 같은 파일은 한 번만 디코드하며(`inflight` 중복
  제거), 실패한 파일은 30초 쿨다운 동안 재요청하지 않습니다(프레임마다 404를 반복하지
  않음). 쿨다운 후 다시 시도하며, 이미 디코드된 이미지는 실패 후에도 유지됩니다.
- **자산별 방향(facing)**: 상태·fallback마다 `facing: "left"|"right"`를 따로 선언합니다.
  현재 향단이 원본은 오른쪽, 버그는 왼쪽을 봅니다. 그리기에서는
  `flipFor(facing, dir)`가 **바라보는 방향과 이동 방향이 다를 때만** 좌우 반전합니다.
- 앵커는 상태 공통 `anchor`(기본 `[0.5, 1.0]`, 발끝) 또는 프레임별 `anchors[n]`을
  씁니다. 화면상 높이는 `clamp(min(화면 높이×0.31, 화면 폭×0.6), 150, 300)` px입니다.
- **해상도 2단(Phase 2.2.1)**: 제작 캔버스는 768×768 셀이며, 브라우저가 받는 서비스
  아틀라스는 384×384 셀입니다(`manifest.cell` = 서비스, `manifest.authoring.cell` = 제작,
  상태별 `authoring`에 원본 경로·크기). 엔진은 선언된 `columns`/`rows`와 실제 이미지
  크기로 슬라이스하므로 해상도를 하드코딩하지 않습니다. 제작 원본을 그대로 넣어도
  같은 규격으로 그려집니다.

## 6. 이펙트 상한

| 이펙트 | 상한 |
| --- | --- |
| 파티클 | 160 |
| 부유 텍스트 | 24 |
| 링 | 14 |
| 슬래시 | 8 |
| 버그 | 3 |

- 공격 히트: 슬래시 + 보라/흰 파티클 + 데미지 텍스트 + 링, 화면 흔들림 5.
- 레벨업: 골드/보라 링, 파티클, `LEVEL n` 텍스트, 흔들림 8, 화면 플래시.
- 설치: 랙 펄스·링·파티클, `+n 티어` 텍스트, install 원샷 재생.
- Reboot/Reset: 화면 플래시, 흔들림, 버그 폭발 후 제거, idle 복귀.
- 발열 70% 초과: 로즈색 비네트(강도 최대 0.22).

## 7. 렌더링과 성능 규칙

- 시뮬레이션은 고정 60 Hz 누적기(프레임당 최대 6스텝), 렌더는 rAF입니다.
- DPR은 2로 제한합니다.
- 탭이 숨겨지면 rAF를 중지하고, 복귀 시 재개합니다.
- `prefers-reduced-motion` 또는 메뉴의 "연출 줄이기"면 단일 정적 프레임만 그리고 버그·
  파티클·흔들림을 멈춥니다.
- 정적 배경(벽·바닥·그리드)은 `폭x높이@dpr` 키로 캔싱해 재사용합니다.
- 랙 슬롯은 화면 폭에 따라 3~9개이며, 같은 티어는 보유 수를 슬롯별로 나눠 라벨에
  표시합니다(실제 수와 일치).
- FPS 미터는 `?fps=1`에서 상단 중앙에 `FPS · particles · bugs · racks`를 표시합니다.
- 지연 로딩 효과(로컬 Chromium 1280×800, 2026-11-25): 시작 시 디코드된 이미지는
  idle+버그 2장으로 **432,160 px(≈1.65MB RGBA 추정)**, 15초 플레이 후 run/attack이
  추가되어 **1,102,880 px(≈4.21MB)**입니다. 이전처럼 5장을 모두 선행 로드하면 시작
  시점부터 4.21MB였습니다. FPS는 부팅 직후 55 → 15초 후 60으로 측정됐습니다.
  픽셀 합계 기반 추정치이며 JS heap 실측·Raspberry Pi 실측은 WO-10에서 별도로
  보고합니다.
- **Phase 2.2.1 서비스 해상도(2026-11-26, 헤드리스 Chromium 156, 1440×900 및
  390×844 DPR 3)**: 6개 상태를 모두 디코드한 합계가 384 셀 서비스 아틀라스에서
  **40.21 MiB**, 768 셀 제작 원본에서 **148.21 MiB**(3.7배)였고, 전송량은
  **4,066 KiB vs 22,504 KiB**(5.5배)였습니다. 측정 조건과 원본 JSON은
  [`QA_REPORT.md`](../QA_REPORT.md)·`docs/screenshots/wo0221/browser-qa.json`에 있습니다.

## 8. 검증 훅

헤드리스 QA용으로 다음을 노출합니다.

- `window.__scene.stats` — `{fps, particles, bugs, racks}`
- `window.__scene.animator.snapshot()` — `{state, frame, elapsed, done}`
- `window.__scene.library.manifest` — 정규화된 manifest(버전·상태·방향·소스)
- `window.__scene.library.problems` — manifest 검증 경고 목록(정상이면 `[]`)
- `window.__scene.library.stateReady` — 지연 로딩이 끝난 상태 집합
- `window.__scene.library.images` — 디코드된 이미지(시트 주입 테스트에 사용)
- `window.__scene.library.durationOf(state)` / `hitSeconds(state)` — manifest 기반 길이·히트 시점

자동 검증: `node --test tests/js/sprite_manifest.test.mjs`(manifest v2/v3 정규화, 아틀라스
슬라이스, 방향 반전, 프레임별 duration·히트 시점, 실패 검증). 좌/우 실화면 캡처는
`python3 tools/wo08_facing_shots.py --url <로컬 공개 주소>`로 재현할 수 있습니다.

## 9. 아트 매핑

| 상태 | manifest 키 | 납품 파일(현행, 384 서비스) | 제작 원본(768) | 현재 fallback |
| --- | --- | --- | --- | --- |
| idle | `states.idle` | `hyangdani-idle-v31-4x2.png`(8F) | `hyangdani-idle-v3-4x2.png` | `hyangdani-idle.png` |
| run | `states.run` | `hyangdani-run-v31-4x3.png`(12F) | `art/hyangdani/export/run-v31/hyangdani-run-v31-4x3-768.png` | `hyangdani-run.png` |
| attack | `states.attack` | `hyangdani-attack-v31-4x3.png`(12F) + manifest `hits` | `hyangdani-attack-v3-4x3.png` | `hyangdani-strike.png` |
| repair | `states.repair` | `hyangdani-repair-v31-4x2.png`(8F) | `hyangdani-repair-v3-4x2.png` | `hyangdani-idle.png` |
| levelup | `states.levelup` | `hyangdani-levelup-v31-4x3.png`(12F) | `hyangdani-levelup-v3-4x3.png` | `hyangdani-idle.png` |
| install | `states.install` | `hyangdani-install-v31-4x3.png`(12F) | `hyangdani-install-v3-4x3.png` | `hyangdani-idle.png` |
| 버그 | `bugs` | `bug-alive.png`, `bug-hit.png`(facing 명시) | 좌동 | 좌동 |

아틀라스 파일명·배치는 [`ASSET_REQUIREMENTS_V3.md`](ASSET_REQUIREMENTS_V3.md) 4장을 따릅니다.
현행 manifest는 6개 상태 모두 아틀라스 `source`를 가지며(Phase 2.2.1), fallback은
로드 실패 시에만 쓰입니다. v3(768 셀) 아틀라스 6장은 삭제하지 않고 저장소에 남아
있습니다. 회귀 테스트용 번호 타일 아틀라스(`test-idle-4x2.png`, `test-run-4x3.png`)는
`tests/fixtures/sprites/`에만 있고 배포 트리에는 포함되지 않습니다.
