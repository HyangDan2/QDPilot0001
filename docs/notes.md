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
