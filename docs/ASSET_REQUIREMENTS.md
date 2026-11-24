# 에셋 요구사항 — Idle Ops Phase 2

> **이 문서는 Phase 2(v2 manifest) 시점 기록입니다.** 신규 아트 납품·검수는
> [`ASSET_REQUIREMENTS_V3.md`](ASSET_REQUIREMENTS_V3.md)를 따릅니다. v3에서는 기본
> 방향이 자산별 `facing`으로 바뀌었고(현행 향단이 원본은 **오른쪽**), 768×768 제작
> 캔버스·아틀라스/시퀀스 납품·프레임별 앵커/히트 이벤트를 지원합니다.

신규 아트 반입 요청 목록과 납품 규격을 정의합니다. 캐릭터 카논·팔레트는
[`ART_DIRECTION.md`](ART_DIRECTION.md)를 따릅니다.

## 원칙

- **개발팀은 새 아트를 생성하지 않습니다.** 이 문서는 요청·규격·검수 기준만 제공합니다.
- 엔진은 시트가 도착하기 전에도 단일 이미지 fallback으로 완전히 동작해야 합니다.
- **기존 에셋을 삭제·덮어쓰지 않습니다.** 교체는 새 파일명 추가 + manifest 스위치로 합니다.
- 아트 에셋을 **SVG로 대체 제출하지 않습니다**(기존 `favicon.svg`는 그대로 유지).
- 캐릭터 리디자인은 PO 승인 후 `ART_DIRECTION.md`를 먼저 갱신합니다.

## 현행 인벤토리 (2026-11-25 기준)

| 파일 | 크기 | 용도 |
| --- | --- | --- |
| `static/public/assets/sprites/hyangdani-idle.png` | 379×640, 320K | idle fallback, repair/levelup/install 임시 fallback |
| `static/public/assets/sprites/hyangdani-run.png` | 498×640, 320K | run fallback |
| `static/public/assets/sprites/hyangdani-strike.png` | 550×640, 384K | attack fallback |
| `static/public/assets/sprites/bug-alive.png` | 318×300, 128K | 버그 기본 |
| `static/public/assets/sprites/bug-hit.png` | 314×300, 128K | 버그 피격 |
| `static/public/assets/hero.jpg` | 1400×933, 200K | 공개 페이지 배경 일러스트 |
| `static/public/assets/favicon.svg` | 64×64 | 파비콘 |

시트는 모두 미납품 상태이며 `sprites/manifest.json`의 `sheet`가 `null`입니다.

## 요청 목록

### P0 — 수리 자세 (단일 이미지)

| 항목 | 값 |
| --- | --- |
| 파일 | `hyangdani-repair.png` |
| 방식 | 단일 이미지 (시트 불필요) |
| 권장 크기 | 384×640 PNG-32 |
| 내용 | 무릎을 굽히고 해머를 랙에 대는 수리 자세. 현재는 idle 이미지를 재사용 중이라 동작 구분이 안 됨 |
| manifest | `states.repair.fallback` 교체 |

### P1 — 원샷 연출 시트

| 항목 | 파일(안) | 프레임 | FPS | 길이 | 루프 | 비고 |
| --- | --- | --- | --- | --- | --- | --- |
| 레벨업 | `hyangdani-levelup.png` | 12 | 12 | 1.0s | 1회 | 마지막 프레임에서 만세/환호 포즈, 잔상 없음 |
| 설치 | `hyangdani-install.png` | 12 | 12 | 1.0s | 1회 | 해머를 내려치는 설치 동작, 마지막 프레임은 정지 자세 |

### P2 — 이동/전투 시트

| 항목 | 파일(안) | 프레임 | FPS | 길이 | 루프 | 비고 |
| --- | --- | --- | --- | --- | --- | --- |
| 대기 | `hyangdani-idle.png`(시트) | 8 | 8 | 1.0s | 루프 | 호흡·머리카락 흔들림. 기존 단일 파일과 이름이 겹치므로 `hyangdani-idle-sheet.png`로 납품 |
| 달리기 | `hyangdani-run-sheet.png` | 12 | 12 | 1.0s | 루프 | 좌우 발 교대, 머리카락 지연 |
| 공격 | `hyangdani-strike-sheet.png` | 12 | 16 | 0.75s | 1회 | **6번 프레임(0-index)에서 타격**. 그 프레임에 해머가 목표에 닿아야 함 |

### P3 — 선택: 버그 시트

| 항목 | 파일(안) | 프레임 | FPS | 길이 | 루프 |
| --- | --- | --- | --- | --- | --- |
| 버그 기본 | `bug-alive-sheet.png` | 4 | 6 | 0.67s | 루프 |
| 버그 피격 | `bug-hit-sheet.png` | 3 | 12 | 0.25s | 1회 |

P3는 엔진이 아직 시트를 읽지 않습니다. 납품 시 별도 승인 후 엔진 확장을 진행합니다.

## 공통 규격

| 항목 | 규격 |
| --- | --- |
| 포맷 | PNG-32, sRGB, 투명 배경 |
| 시트 배치 | 가로 스트립, 모든 셀 동일 크기, 셀 경계에 캐릭터 잘림 없음 |
| 향단이 셀 | 320×640 (12프레임 시트 폭 3840, 4096 이하) |
| 버그 셀 | 512×512 |
| 앵커 | 발끝이 셀 하단 중앙(`[0.5, 1.0]`) |
| 방향 | 기본 왼쪽(오른쪽은 엔진이 반전) |
| 그림자 | 넣지 않음(엔진이 그림자·이펙트를 그림) |
| 텍스트/UI | 넣지 않음 |
| 상태 간 키 | 머리끝~발끝 높이를 상태마다 동일하게 유지 |
| 파일 크기 | 시트 1개당 1MB 이하 권장 |

## 납품 절차

1. `art/hyangdani/export/`(또는 `art/bugs/export/`)에 PNG를 둡니다.
2. 아래 체크리스트로 자체 검수합니다.
3. 통과 시 `static/public/assets/sprites/`에 **새 파일명**으로 복사합니다.
4. `sprites/manifest.json`의 해당 `sheet` 값을 파일명으로 채웁니다.
5. 검증 후 커밋합니다(한 번에 한 상태씩).

### 체크리스트

- [ ] 셀 크기가 규격과 같고 `가로 폭 ÷ 프레임 수`가 정수
- [ ] 프레임 수·FPS가 이 문서 표와 일치
- [ ] 발끝이 셀 하단에, 몸통이 가로 중앙에 정렬
- [ ] 상태 시작/끝 프레임이 자연스럽게 이어짐(루프) 또는 정지 자세로 끝남(원샷)
- [ ] 투명 배경, 그림자·텍스트·프레임 번호 없음
- [ ] 기본 방향이 왼쪽이며 좌우 반전해도 어색하지 않음
- [ ] 320 CSS px 폭에서 실루엣이 읽힘
- [ ] 파일명이 `hyangdani-<state>.png` 규칙을 따름

## 개발팀 검증 (반입 후)

- 헤드리스 Chrome에서 320/390/768/1440/1920 폭 스크린샷으로 프레임 잘림·앵커 확인
- 시트 교체 전후 `?fps=1` 진단에서 FPS·파티클·랙 수 비교
- `sheet`가 `null`로 되돌아가도 단일 fallback으로 렌더링되는지 확인
- 모든 수치는 로컬/테스트 환경 측정값이며, Raspberry Pi 실측과 구분해 보고

## 미납 상태에서의 동작 (검증됨)

- idle/run/attack: 각 단일 이미지로 렌더링
- repair/levelup/install: idle 이미지 + 엔진 이펙트(링·파티클·텍스트)로 연출
- 버그: `bug-alive.png`/`bug-hit.png` 단일 이미지
- manifest를 불러오지 못해도 내장 기본값([`engine/sprites.js`](../static/public/assets/engine/sprites.js))으로 동작
