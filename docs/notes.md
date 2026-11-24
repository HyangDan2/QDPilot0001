# QDPilot0001 메모

## 목적

- HD2Lab Server-HQ 관리자 전용 방치형 게임 "Idle Ops" (서버실 운영 테마)

## 결정 사항

| 날짜 | 내용 | 비고 |
| --- | --- | --- |
| 2026-11-24 | 빈 프로젝트 스캐폴드 생성 | git 초기화 포함 |
| 2026-11-24 | 관리자 SSO 전용, 익명/공개 세션 없음 | Nginx가 `X-HD2Lab-User` 주입 |
| 2026-11-24 | 부재 기간 무제한 + 지연 시뮬레이션(24/7 tick 없음) | 요청 시 경과분 정산, 100년 overflow 보호 |
| 2026-11-24 | SQLite 단일 DB + opId 멱등 액션(30일 TTL) | 서버 권위 상태 |
| 2026-11-24 | 정본 경로 `/admin/idle-ops/` | Server-HQ `/admin/<slug>/` 규약 |
| 2026-11-24 | Server-HQ 통합 패치 파일 준비, 운영 반영 미실행 | Server-HQ `docs/IDLE_OPS_OPERATIONS.md` |
| 2026-11-24 | 운영 배포 완료 (`/admin/idle-ops/`) | 릴리스 `20261124-235022`, main `087246a` |
| 2026-11-25 | 공유 월드 전환 + 공개 읽기 전용 뷰 `/idle-ops/` | ADR-0019, 기존 최고 누적 플레이어 1회 이관, `world`/`world_ops`/`world_events` |
| 2026-11-25 | 공개 화면은 Canvas 2D 미니 엔진 + 향단이 연출 | About 카드 레퍼런스 스프라이트, 이벤트 커서 폴링, 관리자 콘솔은 그대로 유지 |
| 2026-11-25 | Phase 2 워크오더 진행 (branch `feature/phase2-core-evolution`) | 2A 정산·리셋·시계·수치, 2B 공개 스냅샷·멱등·관리자 타임스탬프·이벤트 복구·백업/복구/readiness, 2C 엔진 모듈 분리·스프라이트 상태머신, 2D 아트 구조·디렉션·에셋 요구사항(신규 아트 생성 없음), 2E 콘텐츠 인터페이스·밸런스 지문·설계 문서. 운영 배포·main 병합 금지 |
| 2026-11-25 | Phase 2F QA: 128개 테스트, UI 320~1920, API 1시간 soak, 브라우저 47분 실행 | 결과는 `docs/PHASE2_QA_REPORT.md`·`PERFORMANCE_REPORT.md`. 성능은 macOS M1 실측이며 Pi 실측 아님, backlog 128 |
