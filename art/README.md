# 아트 소스 작업 공간

이 디렉터리는 **웹 루트(`static/public/`) 밖**에 두는 아트 원본·반입 대기 공간입니다.
브라우저에 서빙되지 않으며, 검수를 통과한 내보내기만 `static/public/assets/sprites/`로
복사되어 런타임에 사용됩니다.

## 구조

```text
art/
  hyangdani/source/   # 향단이 PSD/Aseprite 등 편집 원본
  hyangdani/export/   # 검수 전 PNG 내보내기 (시트·단일 이미지)
  bugs/source/        # 버그 몬스터 원본
  bugs/export/
```

디렉터리는 `.gitkeep`으로 유지됩니다. 원본(source)은 용량이 크므로 `art/.gitignore`가
`*/source/*`를 제외합니다. 원본을 Git LFS로 추적하기로 결정하면 해당 규칙만 제거합니다.

## 흐름

1. 원본 작업은 `art/<대상>/source/`에서 합니다. 캐릭터 카논과 규격은
   [`docs/ART_DIRECTION.md`](../docs/ART_DIRECTION.md)를 따릅니다.
2. 내보내기는 `art/<대상>/export/`에 두고, [`docs/ASSET_REQUIREMENTS.md`](../docs/ASSET_REQUIREMENTS.md)의
   납품 체크리스트로 검수합니다.
3. 통과한 PNG만 `static/public/assets/sprites/`에 **새 파일명으로** 추가합니다.
   기존 파일은 덮어쓰거나 삭제하지 않습니다.
4. `sprites/manifest.json`의 해당 `sheet` 값을 채운 뒤, 단일 이미지 fallback이 계속
   동작하는지 확인하고 커밋합니다.

## 원칙

- 이 단계에서는 개발팀이 새 아트를 생성하지 않습니다. 엔진은 시트가 없어도 단일 이미지
  fallback으로 완전히 동작해야 합니다.
- 기존 스프라이트를 삭제하거나 저장소에서 제거하지 않습니다.
- 아트 에셋을 SVG로 대체 제출하지 않습니다(기존 `favicon.svg`는 유지).
- 캐릭터 리디자인(색·비율·의상·무기 변경)은 PO 승인과 `ART_DIRECTION.md` 갱신이 먼저입니다.
