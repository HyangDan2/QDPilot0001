# Phase 2.1 릴리스 게이트 — Idle Ops

- 브랜치: `feature/phase2-core-evolution`
- 작성일: 2026-11-25 (KST)
- 판정: **게이트 통과(개발 호스트 기준) — 운영 배포는 PO 승인 후**
- 근거: [`PHASE2_1_QA_REPORT.md`](PHASE2_1_QA_REPORT.md),
  [`PHASE2_1_PERFORMANCE_REPORT.md`](PHASE2_1_PERFORMANCE_REPORT.md),
  [`PHASE2_1_CORRECTION_REPORT.md`](PHASE2_1_CORRECTION_REPORT.md)

## 1. 게이트 조건

| 조건 | 결과 |
| --- | --- |
| 기존 테스트 삭제·완화 없음 | 충족 — main 61 → Phase 2 128 → Phase 2.1 207 |
| Python 전체 통과 | **207/207 OK** (11.2초) |
| Node 엔진 테스트 | **22/22 pass** |
| 릴리스 게이트 회귀 | **REG-01~20 20/20 OK** |
| 브라우저 E2E(방향·아틀라스) | 통과, 콘솔 오류 0 |
| UI 320~1920 캡처 | 통과, 가로 스크롤·콘솔 오류 0 (FPS 320~1440 60, 1920 headless 49~55) |
| 1시간 연속 실행 | 충족 — 서버 900+451요청 실패 0, 브라우저 3,600초 60샘플 |
| 운영 DB 변경 | 없음 (미접속·미마이그레이션) |
| main 병합·운영 배포 | 없음 |

## 2. REG-01~20

| ID | 불변식 | 자동 테스트 | 결과 |
| --- | --- | --- | --- |
| REG-01 | readiness가 world·schema·disk를 모두 참으로 보고 | `test_reg_01_readiness_requires_world_schema_and_disk` | PASS |
| REG-02 | world 행 부재 시 degraded | `test_reg_02_missing_world_row_is_degraded` | PASS |
| REG-03 | 스키마 버전 불일치 시 degraded | `test_reg_03_schema_mismatch_is_degraded` | PASS |
| REG-04 | 정산 결과가 폴링 빈도와 무관 | `test_reg_04_settlement_is_frequency_independent` | PASS |
| REG-05 | 시계 역행 시 재지급 없음 | `test_reg_05_clock_rewind_never_grants_again` | PASS |
| REG-06 | Reset이 누적·메타를 보존 | `test_reg_06_reset_keeps_meta_progress` | PASS |
| REG-07 | Hard Reset이 초기 상태(토큰·진행 0, 씨앗 캐시 1)로 되돌리고 pre-backup 생성 | `test_reg_07_hard_reset_zeroes_and_writes_pre_backup` | PASS |
| REG-08 | 같은 opId 재시도는 1회 적용·동일 결과 | `test_reg_08_same_op_id_replays_one_result` | PASS |
| REG-09 | 같은 opId 다른 액션은 충돌 | `test_reg_09_same_op_id_with_other_action_conflicts` | PASS |
| REG-10 | 이벤트 보존 상한(400) | `test_reg_10_event_retention_is_bounded` | PASS |
| REG-11 | 범위 밖 커서는 reset으로 복구 | `test_reg_11_cursor_past_newest_resets_cleanly` | PASS |
| REG-12 | 백업 quick_check 통과·복원 입력으로 사용 가능 | `test_reg_12_backup_is_verified_and_restorable_input` | PASS |
| REG-13 | 관리자 액션 후 공개 스냅샷 무효화 | `test_reg_13_public_snapshot_invalidates_after_an_action` | PASS |
| REG-14 | 공개 읽기는 DB를 쓰지 않음 | `test_reg_14_public_reads_do_not_write_the_database` | PASS |
| REG-15 | manifest v3·오른쪽 기준·attack 히트 프레임 6 | `test_reg_15_manifest_is_v3_right_facing_with_attack_hit` | PASS |
| REG-16 | fallback PNG 5종 존재 | `test_reg_16_fallback_assets_exist_and_are_png` | PASS |
| REG-17 | 만료 opId 30일 정리 | `test_reg_17_expired_operation_ids_are_pruned` | PASS |
| REG-18 | `world_ops(created_at)` 인덱스 | `test_reg_18_world_ops_created_at_index_exists` | PASS |
| REG-19 | 20초 읽기 타임아웃·daemon·backlog | `test_reg_19_handler_and_server_bound_connections` | PASS |
| REG-20 | 공개 readiness는 불리언만 노출 | `test_reg_20_public_readiness_exposes_only_booleans` | PASS |

기존 모듈 테스트가 추가로 고정하는 계약: 관리자 401/공개 POST 405, 정적 경로 traversal 404,
Hard Reset admiral+chairman+`"HARD RESET"` 확인, 공개 payload의 내부 필드 제거,
스냅샷 stale/503, v1 DB 제자리 업그레이드, 백업 미검증 거부.

## 3. 운영 확인

```text
$ curl -sS http://127.0.0.1:8797/admin/idle-ops/api/v1/health/ready
{"status":"ok","service":"idle-ops","checks":{"world":true,"schema":true,"disk":true}}

$ python3 -m unittest tests.test_release_gate -v
Ran 20 tests in 0.678s
OK
```

- 임시 DB에 v4 스키마가 생성되고 v1 스키마 업그레이드 테스트가 통과합니다.
- 운영 DB는 열지 않았습니다. 마이그레이션은 PO 승인 후 `DB_MIGRATION.md` 3~4장 절차를 따릅니다.

## 4. 성능·장시간 실행

- 환경: macOS M1 개발 호스트, 로컬 루프백, Playwright Chromium 145 headless. **Raspberry Pi·운영 Nginx
  실측이 아닙니다.**
- Phase 2.1 재측정(상세: [`PHASE2_1_PERFORMANCE_REPORT.md`](PHASE2_1_PERFORMANCE_REPORT.md)):
  - 공개 100명·1초: 3,000/3,000 HTTP 200, p95 19.04~28.41ms, p99 34.16~39.71ms, lock 0, 계약 위반 0
  - 동시 구매 10×5·관리자 조회 20×10: 전 요청 200, lock 0 (구매 p99 82.51ms, 조회 p99 151.68ms)
  - 서버 1시간: 공개 900/900 · 관리자 451/451, p95 4.8ms, RSS 23.1MiB 고정
  - 브라우저 1시간: FPS 중앙 60(최소 54), 콘솔 오류 0, 가로 스크롤 0, 지연 로딩 상태 4개·경고 0
  - UI 폭: 320~1440 60 FPS, 1920은 headless 소프트웨어 래스터에서 49~55
- 비교 기준(Phase 2, 같은 호스트): 공개 100명·1초 p95 **14.96ms**, p99 **47.19ms**, SQLite lock 0,
  계약 위반 0, M1 FPS 60, Pi headless 18~21(`bd63a75`). Phase 2.1과의 차이는 같은 범위의 실행 변동이며,
  실패·lock은 양쪽 모두 0입니다.

## 5. 증거물

- UI: `docs/screenshots/wo10/ui-{320,390,768,1440,1920}.jpg`
- 1시간 브라우저: `docs/screenshots/wo10/browser-soak-final.png`, `browser-soak.json`
- 방향·아틀라스: `docs/screenshots/wo08/` 9장
- 자동: `tests/test_release_gate.py`, `tests/test_ops_hardening.py`, `tests/js/*.test.mjs`

## 6. 남은 위험과 배포 전 조건

1. 운영 Nginx 경유·WAN·실기 디스플레이 성능은 미측정입니다. 배포 승인 후 운영 경로에서
   `qa_perf.py`와 브라우저 확인이 필요합니다.
2. Phase 2.1 코드의 Raspberry Pi 재측정이 필요합니다(테스트 207개 포함).
3. 모바일 Safari·실제 chairman SSO 역할은 미검증입니다.
4. Rate limit은 서버에 없고 Nginx 정책만 문서화했습니다. 적용 여부는 Server-HQ 운영과 합의가
   필요합니다.
5. 최종 아트 미납품 상태로, fallback 이미지가 표시됩니다. 이는 게임 진행에 영향이 없습니다.

## 7. 승인

main 병합·운영 배포·라이브 DB 마이그레이션은 **PO 승인 전 금지**입니다. 이 게이트는 브랜치
푸시와 외부 리뷰 요청까지를 승인 범위로 합니다.
