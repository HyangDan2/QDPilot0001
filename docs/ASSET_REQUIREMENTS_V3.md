# 에셋 요구사항 v3 — Idle Ops

Phase 2.1(WO-08) 기준 신규 아트 납품 규격입니다. 이 문서가 현재 정본이며
[`ASSET_REQUIREMENTS.md`](ASSET_REQUIREMENTS.md)는 v2 시점 기록으로 남깁니다.
캐릭터 카논·팔레트는 [`ART_DIRECTION.md`](ART_DIRECTION.md)를 따릅니다.

## 1. 원칙

- **개발팀은 최종 아트워크를 생성하지 않습니다.** 이 문서는 요청·규격·검수 기준만
  제공합니다. 테스트용 번호 타일/임시 프레임은 `tests/` 아래에서만 쓰고 배포하지
  않습니다.
- **기존 에셋을 삭제·덮어쓰지 않습니다.** 교체는 새 파일명 추가 + manifest 스위치로
  합니다. 납품 전 코드는 기존 PNG fallback으로 완전히 동작합니다.
- 엔진은 manifest를 읽지 못하거나 시트 로드에 실패해도 이전에 쓰던 이미지와 fallback으로
  계속 렌더링합니다(빈 화면 금지).
- 아트 에셋을 SVG로 대체 제출하지 않습니다(기존 `favicon.svg`는 유지).
- 캐릭터 리디자인은 PO 승인 후 `ART_DIRECTION.md`를 먼저 갱신합니다.
- 밸런스·상태 정의를 바꾸는 아트(프레임 수 변경 등)는 manifest와 본 문서를 함께
  갱신하고 별도 검수를 받습니다.

## 2. 제작(authoring) 표준

| 항목 | 규격 |
| --- | --- |
| 프레임 캔버스 | **768×768 px** (1:1), 모든 프레임 동일 크기 |
| 포맷 | PNG-32, sRGB, 투명 배경 |
| 납품 최대 | 단일 텍스처 **4096×4096** 이하(아틀라스·스트립 합산 폭/높이) |
| 앵커 | **발 접지점**. 상태 공통 `anchor`(기본 `[0.5, 1.0]`) 또는 프레임별 `anchors[n]` |
| 키 일관성 | 머리끝~발끝 높이를 모든 상태에서 동일하게 유지(전투/연출 자세에서도) |
| 방향 | **자산별 `facing`**: 현재 향단이 원본은 `"right"`, 버그는 `"left"` |
| 금지 요소 | 그림자, 바닥 접지 그림, 텍스트/UI, 프레임 번호, 워터마크 |
| 셀 경계 | 인접 프레임 침범·잘림 없음. 아틀라스는 셀마다 768×768 전체 캔버스 |
| 파일 크기 | 상태당 1MB 이하 권장(개별 PNG 합계 기준) |

- 좌우 반전은 엔진이 합니다. **미리 반전한 프레임을 넣지 않습니다.**
- 원본이 왼쪽을 보는 자산이면 `facing: "left"`로 선언합니다. 엔진은 바라보는 방향과
  이동 방향이 다를 때만 반전합니다.
- 서비스 표시 해상도는 별도입니다(3장). 768은 제작 기준이며, 런타임에는 화면 높이
  기준 최대 300 CSS px로 그립니다.

## 3. 상태별 프레임 명세 (총 64프레임)

| 상태 | 프레임 | FPS | 길이 | 루프 | 히트 이벤트 | 비고 |
| --- | --- | --- | --- | --- | --- | --- |
| Idle | 8 | 8 | 1.0s | 예 | — | 호흡·머리카락 |
| Run | 12 | 12 | 1.0s | 예 | — | 좌우 발 교대, 상체 리드 |
| Attack | 12 | 16 | 0.75s | 아니오 | **프레임 6(0-index), `strike`** | 0.375s에 해머가 목표에 닿음 |
| Repair | 8 | 10 | 0.8s | 예 | — | 해머를 랙에 대는 동작 |
| LevelUp | 12 | 12 | 1.0s | 아니오 | — | 마지막 프레임 정지 자세 |
| Install | 12 | 12 | 1.0s | 아니오 | — | 마지막 프레임 정지 자세 |

- 히트 프레임은 엔진의 데미지·슬래시·링 이펙트 시점과 같아야 합니다. 프레임 수를
  바꾸면 manifest `hits`와 본 표를 함께 고칩니다.
- 프레임별 지속 시간이 다르면 `durations`(초 배열, 길이 = frames)를 씁니다. `seconds`를
  함께 쓸 경우 **`sum(durations)`와 같아야** 하며, 다르면 검증 경고 후 `durations` 합으로
  계산합니다. 선언이 없으면 `frames ÷ fps`입니다.

## 4. 납품 레이아웃 3종

엔진은 상태마다 아래 중 하나를 `source`로 받습니다.

### 4.1 개별 PNG 시퀀스 (`sequence`)

프레임마다 파일 1장. 프레임 크기가 달라도 되지만 768×768 통일을 권장합니다.

```json
"run": {
  "frames": 12,
  "source": {
    "type": "sequence",
    "files": ["hyangdani-run-0.png", "hyangdani-run-1.png", "…", "hyangdani-run-11.png"]
  }
}
```

- `files` 길이는 `frames`와 같아야 합니다.
- 각 파일은 셀 슬라이스 없이 이미지 전체를 한 프레임으로 그립니다.

### 4.2 그리드 아틀라스 (`atlas`) — 768 제작 권장

행 우선(row-major) 배치, 셀 크기 균일.

| 프레임 | 배치 | 아틀라스 크기 | 셀 |
| --- | --- | --- | --- |
| 8F (Idle·Repair) | 4 columns × 2 rows | **3072×1536** | 768×768 |
| 12F (Run·Attack·LevelUp·Install) | 4 columns × 3 rows | **3072×2304** | 768×768 |

```json
"idle": {
  "frames": 8,
  "source": {
    "type": "atlas",
    "file": "hyangdani-idle-4x2.png",
    "columns": 4,
    "rows": 2,
    "width": 3072,
    "height": 1536
  }
}
```

검증 규칙(위반 시 해당 `source`를 로드하지 않고 fallback으로 내려감):

- `columns`·`rows`는 1 이상 정수, `columns × rows ≥ frames`.
- `width`·`height`(선언 시) ≤ 4096, 각 축이 `columns`·`rows`로 나누어떨어져야 함.
- 프레임 인덱스는 좌상단부터 행 우선으로 증가합니다.

### 4.3 가로 스트립 (`strip`)

`프레임 폭 = 이미지 폭 ÷ frames`, 높이는 이미지 전체입니다.

```json
"attack": {
  "frames": 12,
  "source": { "type": "strip", "file": "hyangdani-attack-strip.png", "width": 3840 }
}
```

- **768 셀 12프레임 스트립은 9216 px로 4096 제한을 넘습니다.** 768 제작에서는
  아틀라스(4.2) 또는 개별 PNG(4.1)를 쓰십시오.
- 스트립은 서비스 해상도 셀(예: 320×640 × 12 = 3840)처럼 총 폭이 4096 이하일 때
  적합합니다. `width`는 검증용 선언이며 실제 슬라이스는 로드된 이미지 폭을 씁니다.

### 4.4 서비스 해상도와 모바일

- 제작 기준은 768이지만 런타임 표시 높이는 최대 300 CSS px입니다. 대역폭이 중요한
  배포에서는 384×384 셀 아틀라스나 320×640 스트립을 **별도 export**로 만들 수
  있습니다. 엔진은 픽셀 크기와 무관하게 같은 앵커 규격으로 그립니다.
- 자동 기기별 manifest 전환은 구현되어 있지 않습니다. 필요하면 PO 승인 후 별도 WO로
  다룹니다.

## 5. Manifest v3 스키마

`static/public/assets/sprites/manifest.json` 최상위:

| 필드 | 필수 | 설명 |
| --- | --- | --- |
| `version` | 예 | `3`. `1`/`2`는 하위 호환 로드, 그 외는 경고 |
| `character` | 아니오 | `"hyangdani"` |
| `anchor` | 아니오 | 기본 `[0.5, 1.0]` |
| `facing` | 아니오 | 기본 방향 `"right"`(현행 원본 기준). 상태가 덮어쓸 수 있음 |
| `maxTexture` | 아니오 | 기본 4096, 256 이상 |
| `cell` | 아니오 | 제작 셀 크기(문서용), 기본 `[768, 768]` |
| `states` | 예 | `idle`·`run`·`attack`·`repair`·`levelup`·`install` |
| `bugs` | 아니오 | `alive`/`hit` 각각 문자열 또는 `{file, facing}` |

상태 항목:

| 필드 | 설명 |
| --- | --- |
| `frames` | 1 이상 정수(기본값은 내장 상태 표) |
| `fps` | 1 이상(기본값 내장) |
| `loop` | 불리언 |
| `seconds` | 상태 총 길이(초). 없으면 `durations` 합 또는 `frames ÷ fps` |
| `durations` | 프레임별 초 배열(길이 = frames, 각 > 0) |
| `facing` | `"left"`/`"right"` (없으면 최상위 `facing`) |
| `anchor` | `[x, y]`, (없으면 최상위) |
| `anchors` | 프레임별 `[x, y]` 배열(길이 = frames) |
| `hits` | `[{ "frame": 6, "type": "strike" }]` 또는 숫자 배열. 0-index |
| `source` | `strip`/`atlas`/`sequence` 객체 또는 `null`(미납품) |
| `fallback` | 문자열 또는 `{file, facing}`. 소스 실패 시 사용 |

예시(현행 attack):

```json
"attack": {
  "frames": 12, "fps": 16, "loop": false, "seconds": 0.75,
  "facing": "right",
  "hits": [{ "frame": 6, "type": "strike" }],
  "source": { "type": "atlas", "file": "hyangdani-attack-4x3.png", "columns": 4, "rows": 3 },
  "fallback": { "file": "hyangdani-strike.png", "facing": "right" }
}
```

## 6. 하위 호환(v2)

| v2 | v3 정규화 결과 |
| --- | --- |
| `states.<s>.sheet: "x.png"` | `source: {type: "strip", file: "x.png"}` |
| `states.<s>.fallback: "y.png"` | `fallback: {file: "y.png", facing: 최상위 facing}` |
| `facing: "left"` | 상태·fallback 기본 facing(상태가 덮어쓰지 않으면) |
| `hitFrame: 6` | `hits: [{frame: 6, type: "strike"}]` |
| `sheetFormat: "horizontal-strip"`, `cell` | 문서용 메타(슬라이스는 strip 규칙) |
| 상태 미기재 | 내장 기본값(frames/fps/loop/fallback) 유지 |

v2 manifest는 그대로 로드되지만, 신규 납품은 v3로 작성합니다.

## 7. 로드 실패·검증 동작

1. manifest fetch/JSON 실패 → 내장 v3 기본값(현행 fallback)으로 동작.
2. 상태 소스 로드 실패 → 해당 상태 fallback → fallback도 실패 → idle 아트 → 절차적 도형.
3. 이미 디코드된 이미지는 실패 후에도 유지(이전 화면이 사라지지 않음).
4. 실패한 파일은 30초 쿨다운 동안 재요청하지 않습니다(프레임마다 404 반복 금지).
   쿨다운 후 다시 시도하므로 파일을 나중에 배포해도 새로고침 없이 복구될 수 있습니다.
5. 잘못된 소스·앵커·duration·히트 프레임은 `library.problems`에 남고 해당 항목만
   무시합니다. 검증 실패가 게임 진행(서버 상태)에 영향을 주지 않습니다.
6. 파일명은 스프라이트 디렉터리 내부 상대 경로만 허용합니다(`..`, 절대 경로, 역슬래시
   거부).

## 8. 성능·메모리 규칙

- 상태별 **지연 로딩**: 시작 시 idle과 버그만 읽고, 처음 등장하는 상태만 그때 읽습니다.
- 같은 파일은 동시에 한 번만 fetch/decode(`inflight` 공유). 실패는 30초 쿨다운 후
  재시도합니다.
- 아틀라스는 한 장을 여러 프레임으로 공유하므로 프레임별 PNG보다 디코드 메모리가
  작습니다. 12F를 개별 PNG로 낼 경우 합계 픽셀 수를 확인합니다.
- 768 원본은 제작용입니다. 실제 표시는 최대 300 px 높이이므로, 모바일 대역폭이
  문제면 4.4의 서비스 해상도 export를 검토합니다.
- Raspberry Pi·모바일 실기 측정은 아직 수행하지 않았습니다. 로컬 Chromium 수치는
  [`PHASE2_1_PERFORMANCE_REPORT.md`](PHASE2_1_PERFORMANCE_REPORT.md)에 측정 환경과 함께
  기록하고, Pi 실측은 별도 승인 후 동일 하네스로 진행합니다.

## 9. 납품 절차

1. `art/hyangdani/export/`(또는 `art/bugs/export/`)에 프레임을 둡니다.
2. 상태 하나 단위로 `static/public/assets/sprites/`에 **새 파일명**으로 복사합니다.
3. `sprites/manifest.json`에서 해당 상태의 `source`(와 필요 시 `facing`/`anchors`/
   `durations`/`hits`)만 채웁니다. fallback은 유지합니다.
4. `node --test tests/js/sprite_manifest.test.mjs`로 정규화·슬라이스 검증을 통과시킵니다.
5. `python3 tools/wo08_facing_shots.py --url <로컬 공개 주소>`로 좌/우 캡처를 확인합니다.
6. 320/390/768/1440/1920 폭에서 잘림·앵커·FPS(`?fps=1`)를 확인합니다.
7. `source`를 다시 `null`로 되돌렸을 때 기존 fallback으로 복구되는지 확인합니다.
8. 한 상태씩 커밋합니다.

### 검수 체크리스트

- [ ] 프레임 캔버스 768×768, 모든 프레임 동일
- [ ] 아틀라스 columns/rows·크기가 4.2 표와 일치, 4096 이하, 셀 균등 분할
- [ ] 프레임 수·FPS·길이가 3장 표와 일치, attack 히트 프레임 6
- [ ] 발 접지점이 앵커와 일치(프레임별 앵커는 `anchors`로 선언)
- [ ] 상태 시작/끝 프레임이 루프에 자연스럽거나 원샷 정지 자세
- [ ] 원본 방향이 `facing`과 일치(향단이 `right`, 버그 `left`)
- [ ] 투명 배경, 그림자·텍스트·프레임 번호 없음
- [ ] 320 CSS px 폭에서 실루엣이 읽힘
- [ ] 파일명이 `hyangdani-<state>-…` 규칙을 따르고 기존 파일을 덮어쓰지 않음

## 10. 현재 상태와 미결

- 최종 아트는 **미납품**입니다. manifest의 모든 `source`는 `null`이며 기존 fallback
  PNG로 렌더링합니다. 개발팀이 대체 아트를 생성하지 않습니다.
- 버그 시트(다중 프레임)는 엔진이 `bugs.<state>.frames`를 읽도록 되어 있으나 현재
  단일 이미지 1프레임으로 동작합니다.
- 공격 히트 시점은 manifest의 `hits`로 계산됩니다. fallback 단일 이미지에서도 같은
  시점(기본 0.375s)에 이펙트가 발생합니다.
- 768 제작 + 12프레임 가로 스트립은 4096 제한을 넘으므로 아틀라스/시퀀스를 권장합니다.
