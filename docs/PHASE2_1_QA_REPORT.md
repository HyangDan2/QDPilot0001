# Phase 2.1 QA 보고서 — Idle Ops

- 대상: `feature/phase2-core-evolution` (Phase 2.1, WO-01~10)
- 작성일: 2026-11-25 (KST)
- 결론: **Python 207개 + Node 22개 통과, 릴리스 게이트 REG-01~20 통과**. 브라우저 E2E(방향·아틀라스·
  UI 5폭)와 1시간 서버 soak, 1시간 브라우저 soak 결과는 아래에 기록합니다.
- **미실행**: 운영 Nginx 경유 부하, 실제 chairman 계정 SSO, 운영 DB 마이그레이션, 모바일 실기
  Safari, Phase 2.1 코드의 Raspberry Pi 재측정.

## 1. 환경

| 항목 | 값 |
| --- | --- |
| 호스트 | macOS 15.7.9, Apple M1 (arm64) |
| Python / SQLite | 3.13.2 / 3.45.3 |
| Node | v24.22.1 |
| 브라우저 | Playwright Chromium headless (로컬), viewport 320~1920 |
| 서버 | `python3 -m server` 127.0.0.1:8797, 임시 DB `/tmp/idle-gate/game.sqlite3` |
| 네트워크 | 로컬 루프백 |

모든 수치는 개발 호스트 실측입니다. Raspberry Pi·운영 서버 값이 아닙니다.

## 2. 자동 테스트

```text
$ python3 -m unittest discover -s tests -t .
Ran 207 tests in 11.182s
OK

$ node --test tests/js/sprite_manifest.test.mjs tests/js/op_coordinator.test.mjs
# pass 22
# fail 0
```

| 파일 | 테스트 | 비고 |
| --- | ---: | --- |
| `test_economy.py` | 46 | 정산·액션·수치 |
| `test_store.py` | 36 | 저장·이벤트·멱등·readiness 상세 |
| `test_api.py` | 35 | HTTP 계약·권한·정적 서빙 |
| `test_release_gate.py` | 20 | **REG-01~20** (WO-10) |
| `test_backup_hardening.py` | 11 | 백업 검증·복원 가역성 |
| `test_economy_limits.py` | 11 | 상한·가드 |
| `test_ops_hardening.py` | 10 | **readiness 실제 검증·타임아웃·인덱스** (WO-09) |
| `test_world_revision.py` | 10 | 스냅샷 일관성·stale |
| `test_checkpoints.py` | 9 | 관리자별 확인 지점 |
| `test_content.py` | 8 | 밸런스 지문·플래그 |
| `test_hard_reset.py` | 7 | 배타 게이트·감사 |
| `test_backup.py` | 4 | CLI |
| Node 2파일 | 22 | manifest v3·lazy loading·facing·op 조정 |

- Phase 2 기준선(main) 61개 → Phase 2 128개 → Phase 2.1 **207개**. 삭제·조건 완화 없음.
- WO-09 커밋 시점은 187개이며, WO-10에서 REG-01~20(20개)을 추가했습니다.

## 3. WO-09 운영 하드닝 검증

| 검사 | 테스트 | 결과 |
| --- | --- | --- |
| 필수 `world` 행 부재 시 not ready | `test_missing_world_row_is_not_ready` | PASS |
| `state_json` 파손 시 not ready | `test_corrupt_world_row_is_not_ready` | PASS |
| 스키마 버전 불일치 시 not ready | `test_schema_mismatch_is_not_ready` | PASS |
| 여유 공간 미달 시 not ready | `test_low_free_space_is_not_ready` | PASS |
| HTTP 503 + 불리언 checks만 노출 | `test_ready_endpoint_reports_degraded_without_internals` | PASS |
| 정상 시 200 + `checks` 3종 | `test_health_is_available_without_identity` | PASS |
| `world_ops(created_at)` 인덱스 | `test_world_ops_has_a_created_at_index_for_pruning` | PASS |
| 30일 TTL op 정리 | `test_expired_ops_rows_are_pruned_on_every_op_write` | PASS |
| 20초 읽기 타임아웃으로 유휴 연결 종료 | `test_silent_connection_is_closed_after_the_read_timeout` | PASS (0.5초로 단축한 테스트) |
| daemon 스레드·backlog | `test_handler_and_server_bound_themselves` | PASS |

## 4. 엔진·브라우저 E2E

### 4.1 방향·히트

`python3 tools/wo08_facing_shots.py --url http://127.0.0.1:8797/idle-ops/`

```text
manifest version 3, problems []
state facing: idle/run/attack/repair/levelup/install = right
fallback facing: 전 상태 right
bug facing: alive/hit = left
hitSeconds 0.375, attackSeconds 0.75
console errors 0
```

- 좌/우 캡처 6장: `docs/screenshots/wo08/{run,attack,idle}-{right,left}.png`. 발 위치 고정,
  이동 방향과 다른 경우에만 반전.

### 4.2 아틀라스 슬라이스·fallback

`python3 tools/wo08_atlas_check.py --url http://127.0.0.1:8797/idle-ops/` → `ok: true`

- 4×2 idle 아틀라스(3072×1536) 주입: `idleKind=atlas`, frame 3 rect `(2304,0,768,768)`.
- 4×3 run 아틀라스(3072×2304) 주입: `runKind=atlas`, frame 7 rect `(2304,768,768,768)`.
- 존재하지 않는 아틀라스 주입: `kind=fallback`, `facing=right`, page error 0.
- 캡처: `docs/screenshots/wo08/atlas-idle-frame3.png`, `atlas-run-frame7.png`,
  `fallback-on-missing-atlas.png`.

### 4.3 UI 폭 매트릭스

`python3 tools/wo10_ui_matrix.py --url http://127.0.0.1:8797/idle-ops/`

| 폭×높이 | FPS | 랙 | 가로 스크롤 | 콘솔 오류 | 캡처 |
| --- | ---: | ---: | --- | ---: | --- |
| 320×700 | 60 | 3 | 0 | 0 | `ui-320.jpg` |
| 390×844 | 60 | 3 | 0 | 0 | `ui-390.jpg` |
| 768×1024 | 60 | 3 | 0 | 0 | `ui-768.jpg` |
| 1440×900 | 60 | 3 | 0 | 0 | `ui-1440.jpg` |
| 1920×1080 | 55 | 3 | 0 | 0 | `ui-1920.jpg` |

- 캡처 위치: `docs/screenshots/wo10/`. FPS는 장면 로드 후 2.5초 시점의 `__scene.stats` 값입니다.
- 1920은 soak와 동시에 측정하면 49까지 내려가므로, soak 종료 후 부하가 없는 상태에서 다시 측정했습니다.
  단독 반복 측정에서 **49~55**(headless Chromium 145, 소프트웨어 래스터, GPU 없음)로 나왔고 표에는
  마지막 실행값을 적었습니다. Phase 2의 1920=60은 Chrome 158 CDP 측정으로, 브라우저 빌드·측정
  방식이 달라 직접 비교하지 않습니다. 320~1440은 두 실행 모두 60입니다.
- 실기 GPU 디스플레이 FPS는 여전히 미측정입니다.

## 5. 장시간 실행

### 5.1 서버 1시간

- 구성: 공개 관전자 2명(8초 간격) + 관리자 `/state` 8초 폴링, 1시간. 임시 DB·별도 포트.
- 결과: 2026-11-25 08:17~09:18 KST, 서버 `127.0.0.1:8797`, RSS는 `qa_perf.py --rss-pid` 719회 샘플.

| 구분 | 요청 | HTTP 200 | p50 | p95 | p99 | max | RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 공개 soak | 900 | 900 | 3.0ms | 4.8ms | 7.03ms | 10.9ms | 23.1MiB (최대도 23.1) |
| 관리자 폴링 | 451 | 451 | — | — | — | — | — |

- 계약 위반 0, SQLite lock 오류 0. 관리자 폴링은 `curl`로 상태 코드만 기록해 백분위는 측정하지
  않았습니다(전부 200).
- RSS가 1시간 동안 23.1MiB에서 증가하지 않았습니다.

### 5.2 브라우저 1시간

`python3 tools/wo10_browser_soak.py --url http://127.0.0.1:8797/idle-ops/ --seconds 3600 --interval 60`

| 항목 | 값 |
| --- | --- |
| 실행 시간 / 샘플 | 3,600.4초 / 60회 |
| FPS | 최소 54 · 중앙 60 · 최대 60 (첫 샘플만 54, 이후 59회 연속 60) |
| JS 힙 | 9.54MiB (60회 모두 동일 — 브라우저가 일정 단위로 양자화해 보고하는 값, 증가 없음) |
| 콘솔 오류 | 0 |
| 가로 스크롤 | 0 |
| 지연 로딩 | 상태 4개(idle·run·attack·repair) 디코드, manifest 경고 0 |
| 최종 캡처 | `docs/screenshots/wo10/browser-soak-final.png` |

- 1시간 동안 월드는 레벨 1에서 1,880요청까지 정상 진행했고, 마지막 샘플의 애니메이터는 repair
  루프였습니다.
- 힙 수치는 브라우저가 일정 단위로 양자화해 보고하는 값이라 "증가 없음" 이상의 해상도는 없습니다.

> Phase 2에서는 47분에서 세션이 끝나 1시간 목표에 미달했습니다. 이번 실행은 3,600초 전체를
> 채웠습니다.

## 6. 릴리스 게이트

REG-01~20 표와 성능 비교는 [`PHASE2_1_RELEASE_GATE.md`](PHASE2_1_RELEASE_GATE.md)에 있습니다.
`python3 -m unittest tests.test_release_gate -v` → 20/20 OK (0.7초).

## 7. 미실행·한계

- 운영 Nginx 경유 부하·`limit_req` 검증: 미실행.
- Raspberry Pi 재측정(Phase 2.1): 미실행. Pi 수치는 Phase 2 `bd63a75` 기준입니다.
- 모바일 실기 Safari/Firefox: 미검증(Chrome 에뮬레이션만).
- 실제 chairman 계정 SSO 역할 검증: 미실행(헤더 주입 테스트만).
- 운영 DB 마이그레이션: 미실행(배포 금지).
- 실기 디스플레이/GPU 키오스크 FPS: 미측정.
- 카메라 줌·팬, 쿨링 시각화: 미구현.
- 최종 아트: 미납품. 테스트용 번호 타일만 `tests/fixtures/sprites/`에 존재.
