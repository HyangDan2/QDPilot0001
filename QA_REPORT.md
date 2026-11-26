# Phase 2.2.1 아트 교정 QA 보고서 — Idle Ops

- 브랜치: `feature/v3-hyangdani-assets` (base `d60337f`, PR #1 head)
- 작성일: 2026-11-26 (KST)
- 상태: **PO 리뷰 요청** — `main` 병합·운영 배포·운영 DB 접속은 하지 않았습니다.
- 동반 문서: [`docs/PHASE2_2_ASSET_HANDOFF.md`](docs/PHASE2_2_ASSET_HANDOFF.md),
  [`docs/ASSET_REQUIREMENTS_V3.md`](docs/ASSET_REQUIREMENTS_V3.md) 9장,
  [`docs/ANIMATION_SPEC.md`](docs/ANIMATION_SPEC.md) 2.1,
  [`art/hyangdani/export/run-v31/PROMPTS.md`](art/hyangdani/export/run-v31/PROMPTS.md)

## 0. 한눈에

| 항목 | 결과 |
| --- | --- |
| WO-01 Run 교정 | 12F 사이클 재제작(좌·우 두 걸음, 착지/흡수/통과/차기/공중/도달), 접지선 고정 → 공중 2프레임 + 루프 이음매 개선 |
| WO-02 런타임 최적화 | 서비스 아틀라스 384 셀 6종, 상태당 289,886~862,325 B(전부 1 MiB 이하), 디코드 148.2 → 40.2 MiB |
| WO-03 무결성 검증 | `tools/wo0221_asset_audit.py` — 문제 0건, 경고 11건(모두 기존 아트의 셀 여백), Attack index 6 타격 자세 5개 조건 충족 |
| WO-04 실 화면 QA | GIF·프레임시트 6종, PC 1440 / 모바일 390 스크린샷, FPS·전송량·메모리 비교, 로딩 실패 fallback 확인 |
| WO-05 회귀 | Python 215건 중 207 통과(8건은 base 커밋에서도 동일하게 실패하는 환경 제약), JS 23/23 |
| WO-06 형상관리 | 반입 게이트 통합·핸드오프 문서 정합화·프레임/프롬프트 백업·커밋 5건·PR #1 갱신 |

## 1. 범위와 제약 준수

| 제약 | 준수 |
| --- | --- |
| 서버 경제 로직 변경 금지 | `server/` 무변경. 경제 지문 테스트 유지 |
| 불필요한 엔진 변경 금지 | `static/public/assets/engine/` **무변경**. 해상도는 manifest가 선언하고 엔진은 선언값으로 슬라이스 |
| 기존 에셋 삭제 금지 | v3 아틀라스 6장·fallback PNG 전부 그대로. 신규는 `-v31-` 새 파일명 |
| 승인 없는 main 병합·운영 배포 금지 | 커밋·푸시만 수행. 병합·배포·운영 DB 접속 없음 |

추가로 지킨 것: 테스트 조건 임의 완화 없음(아래 5장), 아트 대체 수단(SVG) 사용 없음.

## 2. WO-01 — Run 애니메이션 교정

### 2.1 무엇이 문제였나 (교정 전 v3 아틀라스 실측)

| 결함 | 측정값 (`hyangdani-run-v3-4x3.png`) |
| --- | --- |
| 공중 구간 없음 = 미끄러지는 느낌 | 12프레임 전부 최저 알파 행이 y=762/768 (접지선 고정) |
| 체중 이동 없음 | 골반 높이가 포즈와 무관하게 일정 |
| 캐릭터 크기 편차 | 머리끝~발끝 679~753 px (11.0%) |
| 좌우 발 교대 없음 | 같은 쪽 다리의 무릎 들기 동작이 12프레임에 반복 |
| 상체 리드·관성 표현 부족 | 머리카락이 거의 같은 방향으로만 흐름 |

### 2.2 교정 방식

12프레임을 표준 2스텝 사이클로 다시 구성했습니다(스텝당 6프레임, 12 fps, 1.0 s 루프).
포즈가 체중 이동을 만들고(지지 다리가 굽으면 골반이 내려감), 착지·흡수 프레임만
발바닥을 바닥선에 붙이며 공중·도달 프레임은 바닥선에서 띄웁니다. 머리카락은 몸의
수직 가속과 반대로 움직이도록 프레임별로 지시했습니다(내려갈 때 들림, 차기에서 뒤로
늘어짐).

| 프레임 | 위상 | 접지 | 실측 접지/머리끝 |
| --- | --- | --- | --- |
| 0 | contact (오른발) | 바닥 | sole 762, head 59 |
| 1 | down (흡수) | 바닥 | sole 762, head 6 |
| 2 | passing | 바닥 | sole 762, head 12 |
| 3 | push-off | 발끝 2px | sole 760, head 26 |
| 4 | airborne | 22px 띄움 | sole 740, head 9 |
| 5 | reach | 8px 띄움 | sole 754, head 51 |
| 6–11 | 같은 6위상을 왼발로 | 동일 규칙 | 6: 762/37, 7: 762/43, 8: 762/16, 9: 760/39, 10: 740/24, 11: 754/49 |

- 캐릭터 카논(얼굴·라벤더 실버 트윈번·화이트 테크웨어·보라 해머)은 유지했습니다.
  12프레임 모두 같은 기준 프레임에서 같은 카메라·비율 지시로 생성해, 한 장의 768
  원본을 전역 스케일 하나로 정규화했습니다(프레임별 확대/축소 없음 → 크기 튐 없음).
  예외는 프레임 2 하나로, 공중 머리카락이 셀 상단에 닿아 1.8%만 축소했습니다.
- 가로 정렬은 해머 머리 안쪽 모서리를 기준으로 고정해 몸통이 좌우로 흔들리지 않게
  했습니다. 프레임별 실루엣 무게중심 x는 325~383 px(폭 58 px)로, 기존 v3 아트의
  371±26 px 수준입니다.

### 2.3 교정 후 측정

| 지표 | 교정 전 v3 | 교정 후 v31 |
| --- | --- | --- |
| 접지선 고정(공중 없음) | 12/12 프레임 | 10 프레임 접지 + 2 프레임 공중(4, 10) |
| 머리끝~발끝 편차 | 11.0% | 7.1% (포즈가 만드는 정상 편차) |
| 루프 이음매 유사도 (f11→f0 IoU) | 0.50 (사이클 평균 0.52보다 낮음) | **0.53 (사이클 평균 0.464보다 높음)** |
| 프레임 간 평균 IoU | 0.52 | 0.464 (프레임당 동작량이 커짐) |
| 중복 프레임 | 0 | 0 |

`f11(오른발 도달) → f0(오른발 착지)`로 이음매가 끊기지 않으며, 사이클에서 가장
유사도가 낮은 쌍이 없는지(이음매가 최악값이 아닌지)는
`tests/test_sprite_assets.py::test_run_cycle_keeps_its_motion_properties`가 고정합니다.

**한계(정직하게)**: 정지 프레임 검수 + 정량 지표로 판정했고, 실제 재생 속도에서 눈으로
보는 모션 검수는 자동화하지 못했습니다(사람 리뷰 필요). GIF 미리보기
(`docs/screenshots/wo0221/previews/run.gif`)로 바로 확인하실 수 있습니다.

## 3. WO-02 — 런타임 에셋 최적화

- 제작 원본(768×768 셀) 보존: Run은 `art/hyangdani/export/run-v31/hyangdani-run-v31-4x3-768.png`
  (3,072×2,304, 4,966,343 B) + 개별 프레임 12장. 나머지 5종은 기존 `…-v3-…png` 6장을
  그대로 보존(브라우저 비요청).
- 서비스 아틀라스 384×384 셀 별도 생성:

| 상태 | 서비스 파일 | 크기 | 셀 | 인코딩 | 이전(v3, 768) |
| --- | --- | --- | --- | --- | --- |
| idle | `hyangdani-idle-v31-4x2.png` | 862,325 B | 384 | PNG-32 | 2,556,603 B |
| run | `hyangdani-run-v31-4x3.png` | 338,199 B | 384 | PNG-8 256색 | 4,966,343 B(신규) / 4,119,897 B(v3) |
| attack | `hyangdani-attack-v31-4x3.png` | 354,911 B | 384 | PNG-8 256색 | 4,516,264 B |
| repair | `hyangdani-repair-v31-4x2.png` | 727,177 B | 384 | PNG-32 | 2,181,238 B |
| levelup | `hyangdani-levelup-v31-4x3.png` | 289,886 B | 384 | PNG-8 256색 | 3,752,708 B |
| install | `hyangdani-install-v31-4x3.png` | 293,764 B | 384 | PNG-8 256색 | 3,774,436 B |
| 합계 | | **2,866,262 B (2.73 MiB)** | | | 21,600,592 B (20.6 MiB) |

- 예산 규칙: 상태당 1 MiB. PNG-32로 예산을 넘는 상태만 같은 그림을 256색 팔레트로
  재인코딩했습니다(384 셀에서 육안 차이 없음, 768 대비 PSNR 36 dB 이상). 인코딩 선택은
  `docs/screenshots/wo0221/atlas-build-report.json`에 남습니다.
- Manifest: 최상위 `cell: [384,384]`(서비스), `authoring.cell: [768,768]`(제작),
  상태별 `authoring {file,width,height}` + `source {…384 크기}`. 엔진 하드코딩 추가 없음.
- 테스트가 두 해상도를 구분해 검증합니다: `tests/js/sprite_manifest.test.mjs`
  (제작 = 서비스의 정확히 2배, 파일명 규칙, 서비스 파일이 `-v3-`를 가리키지 않음, 디스크의
  PNG 헤더와 manifest 일치, 상태당 1 MiB 이하), `tests/test_sprite_assets.py`(동일 검증을
  Pillow로, + 서비스 export가 원본의 절반 축소본인지 PSNR 30 dB 이상).

## 4. WO-03 — 에셋 무결성 검증 (`tools/wo0221_asset_audit.py`)

Pillow로 서비스 아틀라스 6종과 제작 원본 6종을 각각 검사합니다. 결과: **문제 0건**.

| 상태 | 프레임 | 셀 | 알파 있는 프레임 | 중복 | 셀 경계 침범 | 접지선 | 앵커 오차 | 접지선에서 띄운 프레임 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| idle | 8 | 384 | 8/8 | 0 | 0 | 381 | −3.0 px | — |
| run | 12 | 384 | 12/12 | 0 | 0 | 382 | −2.0 px | 4, 10 |
| attack | 12 | 384 | 12/12 | 0 | 0 | 383 | −1.0 px | — |
| repair | 8 | 384 | 8/8 | 0 | 0 | 381 | −3.0 px | — |
| levelup | 12 | 384 | 12/12 | 0 | 0 | 382 | −2.0 px | — |
| install | 12 | 384 | 12/12 | 0 | 0 | 382 | −2.0 px | — |

- 발 접지점·캐릭터 크기 편차 보고: 상태별 머리끝~발끝 편차 idle 2.9%, run 7.1%,
  attack 18.9%, repair 10.6%, levelup 16.9%, install 23.5%. attack·install의 큰 편차는
  웅크림/설치 자세가 실제로 낮아지는 연출이며, 상태 간 키가 아니라 프레임 간 포즈 차이입니다.
  접지선은 모든 상태에서 셀 하단 −1~−3 px(768 스케일 −2~−6 px)로, `anchor [0.5,1.0]`
  대비 1% 이내입니다(기존 v3 아트도 동일한 오프셋).
- 가로 접지점 평균은 idle −41.6 px, run −23.2 px, repair −60.4 px, levelup −47.5 px,
  install −37.0 px, attack −101.3 px(런지 자세). 엔진이 셀 중앙을 앵커로 쓰는데 공격
  자세만 왼쪽으로 크게 치우쳐 있어 경고로 남겼습니다(기존 아트 특성, 이번 교정 범위 밖).
- Attack index 6 타격 자세: 해머 중심이 몸 앞(x=684/768), 사이클 최고·최저 사이의 목표
  높이(y=279, 사이클 87~556), 전방 도달 상위 3프레임, 타격 후 하강 지속(프레임 7 y=556),
  인접 프레임과 구분 — **5개 조건 모두 충족**. 즉 `hits: [{frame: 6}]`과 그림이 일치합니다.
- 경고 11건은 모두 기존 아트의 셀 여백(idle 상단 2 px, attack 좌 3 px/우 2 px 등)과 위
  공격 가로 접지점입니다. 잘림(경계 접촉)은 6종 66프레임 전부 0건입니다.

## 5. WO-04 — 실제 애니메이션 QA

### 5.1 미리보기 (6종)

| 상태 | GIF | 프레임 시트 |
| --- | --- | --- |
| idle | `docs/screenshots/wo0221/previews/idle.gif` (223 KiB) | `idle-frames.png` |
| run | `docs/screenshots/wo0221/previews/run.gif` (373 KiB) | `run-frames.png` |
| attack | `docs/screenshots/wo0221/previews/attack.gif` (395 KiB) | `attack-frames.png` |
| repair | `docs/screenshots/wo0221/previews/repair.gif` (185 KiB) | `repair-frames.png` |
| levelup | `docs/screenshots/wo0221/previews/levelup.gif` (325 KiB) | `levelup-frames.png` |
| install | `docs/screenshots/wo0221/previews/install.gif` (322 KiB) | `install-frames.png` |

GIF은 manifest의 FPS(8/12/16/12/12/10 ms)로, 화면 표시 높이 300 px 기준입니다.
MP4는 이 환경에 ffmpeg 바이너리가 없어 만들지 못했습니다(GIF로 대체).

### 5.2 실제 게임 화면 (Playwright, 헤드리스 Chromium 156.0.8078.4)

| 패스 | 파일 |
| --- | --- |
| PC 1440×900, Run | `desktop-1440-run.jpg`, 확대 `desktop-1440-run-hero.png` |
| PC 1440×900, Attack | `desktop-1440-attack.jpg`, 확대 `desktop-1440-attack-hero.png` |
| 모바일 390×844(DPR 3), Run/Attack | `mobile-390-run.jpg`, `mobile-390-run-hero.png`, `mobile-390-attack.jpg`, `mobile-390-attack-hero.png` |
| 768 비교 패스 | `desktop-1440-768-*.jpg`, `mobile-390-768-*.jpg` |
| 로딩 실패 패스 | `desktop-1440-fallback-*.jpg` |

포즈는 엔진의 공개 훅(`window.__scene`)으로 고정해 실제 아틀라스 프레임이 그려진 상태를
캡처했습니다(프레임 인덱스까지 확인: run→frame 3, kind=atlas).

### 5.3 FPS·전송량·디코드 메모리 (384 서비스 vs 768 제작 원본)

같은 manifest·같은 엔진으로, 요청만 제작 원본 바이트로 바꿔 비교했습니다.

| 조건 | rAF FPS | scene FPS | 디코드 메모리 | 전송량 |
| --- | --- | --- | --- | --- |
| PC 1440, 384 서비스 | 56 | 55 | **42,160,256 B (40.2 MiB)** | **4,163,092 B (4,065 KiB)** |
| PC 1440, 768 원본 | 40 | 44 | 155,406,464 B (148.2 MiB) | 23,044,419 B (22,504 KiB) |
| 모바일 390, 384 서비스 | 60 | 60 | 42,160,256 B | 4,163,089 B |
| 모바일 390, 768 원본 | 45 | 55 | 155,406,464 B | 23,044,417 B |

→ 디코드 메모리 **3.7배**, 전송량 **5.5배** 감소. FPS는 헤드리스 Chromium(로컬 호스트,
GPU 없음) 측정으로 384 쪽이 15~20% 높게 나왔지만, 이 수치는 기기 실측이 아닙니다.
Raspberry Pi·실기 모바일 측정은 여전히 미수행입니다.

### 5.4 로딩 실패 fallback

Run 아틀라스 요청을 404로 차단한 상태로 앱을 띄운 결과:
`library.artFor("run").kind === "fallback"`, 캔버스 픽셀 샘플 10,453/35,027이 배경이 아닌
값(빈 화면 아님), `library.problems` 0건, 콘솔 오류는 예상한 404 한 건뿐입니다. 이전에
디코드된 다른 상태 아트는 유지됩니다. 스크린샷: `desktop-1440-fallback-run.jpg`.

## 6. WO-05 — 회귀 테스트

| 스위트 | 결과 |
| --- | --- |
| `node --test tests/js/sprite_manifest.test.mjs tests/js/op_coordinator.test.mjs` | **23/23 통과** (기존 22 + 신규 1) |
| `python3 -m unittest discover -s tests -t .` | **215건 중 207 통과, 8 실패** |

8건의 실패는 전부 readiness/디스크 검사 테스트이며 **이번 변경과 무관한 환경 제약**입니다:
이 컨테이너의 `/tmp`는 128 MiB tmpfs인데 `server/store.py`의 `MIN_FREE_BYTES`가 256 MiB라
`readiness.ok`가 false가 됩니다. 근거: 기준 커밋 `d60337f` 워크트리에서 같은 8건이 동일하게
실패함을 확인했습니다(`test_readiness_reports_storage_health`,
`test_healthy_store_reports_every_check`, `test_missing_world_row_is_not_ready`,
`test_ready_endpoint_reports_degraded_without_internals`,
`test_health_is_available_without_identity`, `test_health_over_unix_socket`,
`test_reg_01_readiness_requires_world_schema_and_disk`,
`test_reg_20_public_readiness_exposes_only_booleans`). 코드·테스트 조건은 건드리지 않았습니다.

신규 테스트(기존 조건 완화 없음):

| 파일 | 내용 |
| --- | --- |
| `tests/test_sprite_assets.py` (6) | 서비스 예산·해상도·알파, 제작 원본 선언·보존, export가 원본의 절반 축소본인지(PSNR≥30), Run 사이클 운동 특성(12장 중복 없음·좌우 걸음 분리·공중 2~4프레임·루프 이음매가 최악값 아님·셀 경계 비접촉), Attack 6 타격 자세, 감사 도구 문제 0건 |
| `tests/test_asset_import.py` (7) | 반입 게이트: 드라이런 무변경, 반입/재반입 멱등, 충돌 거부, 잘못된 그리드·빈 프레임·모호한 번들·비PNG 거부, 다른 상태 보존 |
| `tests/js/sprite_manifest.test.mjs` (+1) | 디스크의 서비스·제작 PNG 헤더와 manifest 일치, 예산, 레거시 v3 파일 보존 |

기존 테스트 변경 2건은 **완화가 아니라 새 납품 규격으로의 재정의**입니다:
`tests/js/sprite_manifest.test.mjs`·`tests/test_release_gate.py`의 “제공 아틀라스가 768 셀”이라는
단정을 “서비스 384 셀 · 제작 원본 768 셀 · 예산 1 MiB · 레거시 보존”으로 바꿨고, 검증 항목은
늘었습니다(파일 헤더 실측, authoring 2배 관계, 예산, 레거시 존재).

## 7. WO-06 — 형상관리·원본 보존

- 반입 스크립트 정합: 기존 브랜치의 `install_phase22_{idle,run,attack}.py`(SHA 고정·384만 기록)를
  `tools/import_phase22_atlas.py`로 통합(768 검증 → 제작 원본 보관 → 384 서비스 생성 →
  해당 상태 `authoring`·`source`만 갱신). 문서는 `docs/PHASE2_2_ASSET_HANDOFF.md`로 정합화.
- 백업: 개별 프레임 12장(768) `art/hyangdani/export/run-v31/frames/`, 제작 아틀라스,
  생성 프롬프트·프레임 SHA-256 `art/hyangdani/export/run-v31/PROMPTS.md`. 생성기 원본
  1254×1254 12장은 `art/hyangdani/source/run-v31/`(art/.gitignore로 Git 제외, 작업
  공간에 보관).
- 커밋(이 보고서 이전, 모두 작성자 `HD2 <chunxiang2022@gmail.com>`):

| 커밋 | 내용 |
| --- | --- |
| `7b8807f` | WO-01 Run 12F 교정 아트·프롬프트·해시 |
| `b324344` | WO-02 384 서비스 아틀라스 6종·manifest·빌드 도구·테스트 재정의 |
| `e605b39` | WO-03/04 감사·미리보기·브라우저 QA 도구와 증거 |
| `11e29d4` | WO-06 반입 게이트·픽스처 테스트·핸드오프 문서 |
| (이 보고서 커밋) | docs 갱신 + `QA_REPORT.md` |

- 원격 검증: `git rev-parse HEAD` == `git rev-parse origin/feature/v3-hyangdani-assets`
  (푸시 후 확인값은 인계 메시지에 기재).

## 8. 미결·권고

1. **v3 768 아틀라스 6장의 거취**: 브라우저가 더 이상 요청하지 않지만 “기존 에셋 삭제 금지”
   규칙에 따라 그대로 두었습니다. 저장소·배포 용량(약 20.6 MiB)을 줄이려면 웹 루트 밖으로
   옮기는 별도 승인이 필요합니다.
2. **모션 주관 검수**: 정지 프레임과 정량 지표로 판정했습니다. 런 GIF를 실제 속도로 보고
   “달리기로 읽히는가”를 확인해 주십시오(필요 시 프레임 단위 재조정 가능).
3. **Attack 가로 접지점(−101 px)**: 공격 자세가 셀 중앙 앵커에서 크게 벗어나 있습니다.
   공격 순간 위치가 왼쪽으로 밀려 보이면 별도 WO로 앵커/포즈를 맞출 수 있습니다.
4. **Pi·실기 측정**: FPS·메모리는 로컬 헤드리스 측정입니다. 실기 측정은 미수행입니다.
5. **공격 히트 프레임**: 그림상 타격 시점은 프레임 6으로 타당하나, 가장 빠른 하강은
   프레임 7입니다(임팩트 후 팔로스루). 연출상 히트를 프레임 7로 옮기려면 manifest `hits`와
   `ASSET_REQUIREMENTS_V3` 표를 함께 바꿔야 하며, 이번 범위에서는 값(6)을 유지했습니다.

## 9. 재현 명령

```sh
python3 tools/build_sprite_atlases.py --check
python3 tools/wo0221_asset_audit.py
node --test tests/js/sprite_manifest.test.mjs tests/js/op_coordinator.test.mjs
python3 -m unittest discover -s tests -t .
python3 tools/wo0221_previews.py
python3 -m server --dev-user hd2service --static-root static --public-root static/public \
  --port 8795 --db /tmp/idle-ops-qa.sqlite3 &
python3 tools/wo0221_browser_qa.py --url http://127.0.0.1:8795/idle-ops/
python3 tools/import_phase22_atlas.py --state run --bundle art/hyangdani/export/run-v31 --check
```

브라우저 QA는 Playwright Chromium이 필요하며, 이 환경에서는
`LD_LIBRARY_PATH=/workspace/.tools/chromedeps/usr/lib/x86_64-linux-gnu:/workspace/.tools/syslibs/usr/lib/x86_64-linux-gnu`
를 지정해야 실행됩니다(시스템 라이브러리 부재).
