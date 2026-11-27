# Phase 2.2 에셋 핸드오프 — 2단 해상도 납품 규격

이 문서는 Phase 2.2 아트 브랜치(`feature/phase22-hyangdani-assets`)의
`PHASE2_2_IDLE_HANDOFF.md`와 `tools/install_phase22_*.py` 3종을 Phase 2.2.1 납품
규격에 맞춰 일반화한 기록입니다. 그 스크립트들은 상태마다 SHA-256을 하나씩 고정하고
384 셀 런타임 파일만 쓰는 형태였습니다. 이제는 **768 제작 원본을 받아 보존하고 384
서비스 파일을 함께 만드는 단일 게이트**를 씁니다.

## 바뀐 점

| 이전 (Phase 2.2 브랜치) | 현재 (Phase 2.2.1) |
| --- | --- |
| `install_phase22_idle.py` / `_run.py` / `_attack.py` — 상태별 스크립트 3개 | `tools/import_phase22_atlas.py --state <state>` 하나 |
| 이미지 SHA-256을 코드에 고정 | 구조 검증(그리드·PNG-32·알파·중복·경계·프레임 수)으로 판정 |
| 384 셀 런타임 파일만 썼음 | 768 제작 원본(`art/…/export/<state>-v31/`) + 384 서비스 아틀라스 |
| `manifest.source`만 갱신 | `authoring`과 `source`를 함께 갱신, 나머지 상태·fallback 불변 |
| 상태별 반입 문서 없음(핸드오프 문서 1장) | 이 문서 + `ASSET_REQUIREMENTS_V3.md` 9장 절차 |

## 납품 계약

| 항목 | 값 |
| --- | --- |
| 번들 | ZIP 또는 디렉터리. `hyangdani-<state>-*.png` 중 **768 셀 그리드와 크기가 일치하는 파일 하나** |
| 제작 원본 | PNG-32(sRGB, 알파), `columns×768 × rows×768` |
| 그리드 | idle·repair 4×2(8F), run·attack·levelup·install 4×3(12F) |
| 서비스 | `tools/import_phase22_atlas.py`가 LANCZOS 절반 축소, 상태당 1 MiB 이하 |
| 프레임 검사 | 셀마다 알파 1,000px 이상, 셀 경계 접촉 없음, 중복 프레임 없음 |
| 충돌 | 같은 경로에 다른 픽셀이 있으면 거부(덮어쓰지 않음) |

## 절차

```sh
# 1) 검사만 (파일을 쓰지 않음)
python3 tools/import_phase22_atlas.py --state run --bundle /abs/path/delivery.zip --check

# 2) 반입: 제작 원본 + 서비스 아틀라스 + manifest 갱신
python3 tools/import_phase22_atlas.py --state run --bundle /abs/path/delivery.zip

# 3) 검증
python3 tools/build_sprite_atlases.py --check
python3 tools/wo0221_asset_audit.py
node --test tests/js/sprite_manifest.test.mjs tests/js/op_coordinator.test.mjs
python3 -m unittest discover -s tests -t .
git diff -- static/public/assets/sprites/manifest.json
```

`python3 -m unittest tests.test_asset_import`가 드라이런·반입·재반입·손상 번들·
모호 번들·잘못된 그리드·빈 프레임·비PNG·다른 상태 보존을 고정해 둡니다.

## 실제 반입 기록

- `run`: `art/hyangdani/export/run-v31/hyangdani-run-v31-4x3-768.png` →
  `static/public/assets/sprites/hyangdani-run-v31-4x3.png`(338,199 B, PNG-8 256색).
  생성 프롬프트·프레임 해시는 같은 폴더의 `PROMPTS.md`.
- `idle`·`attack`·`repair`·`levelup`·`install`: 제작 원본은 Phase 2.1에서 납품된
  768 셀 `hyangdani-<state>-v3-…png`를 그대로 보존하고, 서비스 파일만 새로 만들었습니다
  (`hyangdani-<state>-v31-…png`). 기존 파일은 삭제·덮어쓰기 없음.

## 남은 결정 (PO)

- v3 768 셀 아틀라스 6장은 브라우저가 더 이상 요청하지 않지만 “기존 에셋 삭제 금지”
  규칙에 따라 저장소에 남아 있습니다. 배포 트리에서 빼려면 별도 승인이 필요합니다.
- Raspberry Pi·모바일 실기 측정은 아직 수행하지 않았습니다(이번 단계는 로컬
  Chromium 측정까지).
