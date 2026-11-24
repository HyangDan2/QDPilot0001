# 애니메이션 명세 — Idle Ops

공개 화면의 Canvas 2D 캐릭터·연출 규격입니다. 구현 정본은
[`static/public/assets/engine/`](../static/public/assets/engine)이며, 아트 납품 규격은
[`ASSET_REQUIREMENTS.md`](ASSET_REQUIREMENTS.md)를 따릅니다.

## 1. 엔진 구조

| 파일 | 역할 |
| --- | --- |
| `config.js` | 티어 정의, 파티클/버그 상한, 이동·공격 상수, 수량 포맷 |
| `animation.js` | 상태 정의(프레임·FPS·길이·루프·히트 프레임)와 `Animator` |
| `sprites.js` | manifest 로드, 시트 슬라이스와 단일 이미지 fallback |
| `motion.js` | 향단이 행동 상태 머신(대기/이동/공격/수리 + 원샷) |
| `effects.js` | 파티클·슬래시·링·부유 텍스트 상한 풀 |
| `room.js` | 랙 배치(실제 보유 수), 정적 배경 캐시, 랙 드로잉 |
| `scene.js` | 60 Hz 시뮬레이션, rAF 렌더, DPR·가시성·모션 설정, FPS 미터 |

`engine.js`는 `WorldScene`과 `STATES`, `formatCount`만 다시 내보냅니다.

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
levelup·install 12/12=1.0). 프레임 정의를 바꾸면 `MOTION.attackSeconds`·
`MOTION.repairSeconds`도 함께 맞춥니다.

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
- 공격 중에는 전진 lunge 26px, 회전 0.1 rad의 사인 곡선을 적용하고, 히트 프레임에서
  데미지 이펙트를 한 번 발생시킵니다.
- 공격·수리 타이머는 각각 0.75초·0.8초로 상태 길이와 동일합니다.
- 레벨업/설치 이벤트는 이동을 멈추는 원샷 오버레이로 재생하고 끝나면 이전 모드로
  돌아갑니다. 이미 원샷이 재생 중이면 새 원샷은 무시합니다.

## 4. Animator

- `elapsed`를 누적하고 `frameIndex = floor(elapsed × fps) % frames`(루프) 또는
  `min(frames−1, floor(elapsed × fps))`(원샷)로 프레임을 고릅니다.
- 상태에 `durations`(프레임별 초 배열, 길이 = `frames`)를 주면 프레임마다 다른 지속
  시간을 쓸 수 있습니다. 루프는 `sum(durations)` 주기로 감고, 없으면 위 균등 FPS를
  씁니다(현재 전 상태 균등).
- 원샷은 `elapsed ≥ seconds`에서 완료됩니다.
- `consumeHit()`는 공격 상태에서 히트 프레임에 도달했을 때 **정확히 한 번** true를
  돌려줍니다(단일 이미지 fallback은 0.16초 시점에 대체 처리).

## 5. 스프라이트 로딩과 슬라이스

- `sprites/manifest.json`을 `cache: "no-cache"`로 매 로드 시 재검증합니다(정적 서버가
  `Cache-Control: no-cache`를 보냄). 실패하면 `sprites.js` 내장 기본값을 씁니다.
- 시트가 있으면 `프레임 폭 = 이미지 폭 ÷ 프레임 수`로 잘라 그립니다. 프레임 높이는
  이미지 높이 전체입니다.
- 시트가 없거나 이미지 로드에 실패하면 `fallback` 단일 이미지를 사용합니다.
  즉 아트 미납품 상태에서도 모든 상태가 렌더링됩니다.
- 앵커는 `[0.5, 1.0]`(하단 중앙), 기본 방향은 왼쪽이며 `dir ≥ 0`일 때 좌우 반전합니다.
- 화면상 높이는 `clamp(min(화면 높이×0.31, 화면 폭×0.6), 150, 300)` px입니다.

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

## 8. 검증 훅

헤드리스 QA용으로 다음을 노출합니다.

- `window.__scene.stats` — `{fps, particles, bugs, racks}`
- `window.__scene.animator.snapshot()` — `{state, frame, elapsed, done}`
- `window.__scene.library.manifest` — 로드된 manifest(버전·셀·상태)
- `window.__scene.library.images` — 로드된 이미지(시트 주입 테스트에 사용)

시트 슬라이스는 12프레임 합성 시트를 주입해 프레임 인덱스와 렌더 결과를 확인하는
방식으로 검증했습니다.

## 9. 아트 매핑

| 상태 | manifest 키 | 납품 파일(안) | 현재 fallback |
| --- | --- | --- | --- |
| idle | `states.idle` | `hyangdani-idle-sheet.png` | `hyangdani-idle.png` |
| run | `states.run` | `hyangdani-run-sheet.png` | `hyangdani-run.png` |
| attack | `states.attack` | `hyangdani-strike-sheet.png` | `hyangdani-strike.png` |
| repair | `states.repair` | `hyangdani-repair.png`(단일) | `hyangdani-idle.png` |
| levelup | `states.levelup` | `hyangdani-levelup.png` | `hyangdani-idle.png` |
| install | `states.install` | `hyangdani-install.png` | `hyangdani-idle.png` |
| 버그 | `bugs` | `bug-alive.png`, `bug-hit.png` | 좌동(단일) |
