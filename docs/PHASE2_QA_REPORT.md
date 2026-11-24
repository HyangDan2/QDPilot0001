# QDPilot0001 Idle Ops — Phase 2 통합 QA 보고서

- 대상: `feature/phase2-core-evolution` (base `main` `326709d`)
- 작성일: 2026-11-25 (KST)
- 결론: 자동 테스트 128개 통과(개발 호스트·Raspberry Pi 모두), 2F 필수 매트릭스 전 항목 실행 완료.
  **운영 Nginx 경유·WAN 관전자·실기 디스플레이는 미실측이며**, Raspberry Pi는 1차 실측(서버 부하 +
  headless 브라우저)만 수행했습니다.

## 1. 검증 환경

| 항목 | 값 |
| --- | --- |
| 호스트 | macOS 15.7.9, Apple M1 (arm64) |
| Pi 호스트 | Raspberry Pi (hyogidevrsp), aarch64 4코어, 8GB, Debian 12, Python 3.11.2 |
| Python / SQLite | 3.13.2 / 3.45.3 (Pi: 3.11.2 / 시스템 SQLite) |
| 브라우저 | headless Chrome 158.0.8127.37 (CDP), Pi: Chromium 155 headless |
| 기능 브랜치 서버 | `127.0.0.1:8123` (프리뷰 DB, `--dev-user`) |
| QA 서버 | `127.0.0.1:8124` (임시 DB, 1시간 soak) |
| 비교(before) 서버 | `127.0.0.1:8125` (main `326709d` worktree) |
| Pi 임시 서버 | `127.0.0.1:18233` (Pi 클론 + 임시 DB, 측정 후 삭제) |
| 네트워크 | 로컬 루프백. 샌드박스 HTTP 프록시는 하네스에서 우회 |

## 2. 자동 테스트

```text
$ python3 -m unittest discover -s tests -t .
Ran 128 tests in 8.559s

OK

# Raspberry Pi (hyogidevrsp, Python 3.11.2):
Ran 128 tests in 19.704s

OK
```

- 기준선(main): 61개 → 현재: 128개. 신규 70개, 기존 3개는 더 엄격한 테스트로 대체(손실 없음).
- 파일별: `test_api.py` 34, `test_economy.py` 46, `test_store.py` 36, `test_backup.py` 4, `test_content.py` 8.
- 신규 테스트 목록과 대체 관계는 [구현 보고서 12장](PHASE2_IMPLEMENTATION_REPORT.md#12-신규-테스트-및-실행-결과).

## 3. 2F 필수 QA 매트릭스

| 영역 | 검증 항목 | 증거 | 결과 |
| --- | --- | --- | --- |
| 경제 | 접속 빈도 독립성 | `SettlementFrequencyTests`(1/8/15초 vs 6시간, 15초/1시간/1일 vs 30일, 오차 1e-6/1e-9) | PASS |
| 경제 | Reset·Reboot 정합성 | `ActionTests.test_reset_keeps_meta_progress`, `hard_reset_zeroes_everything`, API Hard Reset 5종 | PASS |
| 경제 | 장기 방치·수치 안정성 | 30일 일괄 정산, `NumericSafetyTests`(포화·NaN·타입·상한) | PASS |
| 동시성 | 중복 구매·시간 역행 | `test_concurrent_worlds_settle_the_interval_once`, `ClockTests`, `test_clock_anomaly_never_double_grants` | PASS |
| API | 관리자 인증·공개 쓰기 차단 | 인증 없음 401, 공개 POST 405, 공개 `/content` 미노출 테스트 | PASS |
| API | 응답 유실·동일 opId 재시도 | `test_repeating_an_operation_replays…`, `test_conflicting_operation…` + 프론트 pending opId | PASS |
| DB | 백업·복원 | `BackupTests` 4종, `test_restore_rolls_back…`, restore 후 `quick_check` | PASS |
| 이벤트 | 커서 복구·순서 보장 | `test_public_events_are_ordered…`, `test_cursor_past…`, `test_retention_prunes…` | PASS |
| 엔진 | State 전환 | CDP: idle→run→attack→repair, levelup 원샷 mid/after 스크린샷 | PASS |
| 엔진 | 방향·Anchor | 매니페스트 `[0.5,1.0]`·`facing:left`, 이동 방향에 따른 반전 스크린샷 | PASS |
| 엔진 | 공격 거리·타격 판정 | 90초 CDP 추적: 공격 12회, 목표 갱신 55회, `hit` 상태 진입, 오류 0 | PASS |
| 엔진 | 설비 수량·랙 표시 | `room.js` 슬롯 계산 + 320~1920 스크린샷, 보유 0 티어 미표시 | PASS |
| 엔진 | 에셋 누락 Fallback | 시트 `null` 상태에서 단일 PNG fallback으로 전 상태 재생 | PASS |
| UI | 320/390/768/1440/1920px | 5개 폭 스크린샷, FPS 60, 콘솔 오류 0, 가로 스크롤 0 | PASS |
| UI | 장시간 실행 | 브라우저 47분 연속 실행, FPS/힙/오류 샘플링 | PASS (6장) |
| 운영 | DB 마이그레이션·롤백 | `test_v1_database_upgrades_in_place…`(v1 스키마 실 DB), 신버전 거부, pre-deploy 복구 절차 | PASS (운영 미적용) |
| 성능 | Pi 1차 재측정 | Pi 테스트 128 OK, 1/10/100명+soak 전부 200·lock 0, headless FPS 18~21 (6.3장) | PASS (서버) |

## 4. 엔진·브라우저 검증 상세 (CDP)

| 검증 | 방법 | 결과 |
| --- | --- | --- |
| 스프라이트 시트 슬라이스 | 12타일 번호 스트립을 런타임 주입해 attack 시트로 등록, 프레임 전환 확인 | `frames=12`, 현재 프레임 진행·`hitFrame` 도달 확인(`/tmp/idle-2c-sheet.png`) |
| 상태 머신 | `?fps=1`에서 250ms 간격 샘플링 | idle↔run↔attack↔repair 전환, levelup 중간 프레임 4 → 종료 후 idle |
| 원샷 길이 | levelup 12f@12fps = 1.0초 | 타이머 단축으로 잘리지 않음(수정 후 재확인) |
| 프레임별 지속 시간 | 모듈 직접 import, `durations=[0.1]×12` 주입 | 0.15초→1프레임, 0.35초→3프레임, 0.65초 hitFrame 6 통과, 4초 루프 주기 복귀 |
| 버그 추적 | 90초, 200ms 샘플 | 공격 12회, 추적 갱신 55회, 공격 진입 거리 0.2~4.4px, `alive`→`hit` 관측 |
| 방향·앵커 | 좌우 이동 스크린샷 | 발 위치 고정, 이동 방향으로 반전 |
| Reduced Motion | `prefers-reduced-motion: reduce` | 버그 0, 장식 파티클 중지, FPS 60 |
| DPR | `setDeviceMetricsOverride(deviceScaleFactor=2)` | `devicePixelRatio` 2에서 캔버스 백킹/CSS = 2.0, 상한 2 준수 |
| 콘솔 | `Runtime.exceptionThrown` + `Log.entryAdded` | 전 시나리오 오류 0 |
| 탭 숨김 | 다른 탭 활성화 후 `scene.time` 샘플 | 숨김 동안 시간 0으로 정지, 복귀 후 2.07초로 재개 |

스크린샷(저장소): [`docs/images/phase2/`](images/phase2) — `qa-ui-{320-fixed,390,768,1440,1920}.jpg`,
`qa-ui-reduced.jpg`, `qa-after-1440.jpg`, `qa-after-390.jpg`, `qa-before-1440.jpg`, `qa-before-390.jpg`,
`qa-ui-soak-final.jpg`, `pi-headless-1440.jpg`. 원본 PNG는 `/tmp`에 남아 있습니다(커밋 제외).

## 5. UI 매트릭스

| 폭 | FPS(4회 샘플) | 랙 수 | 가로 스크롤 | 콘솔 오류 | 비고 |
| --- | --- | --- | --- | --- | --- |
| 320×700 | 60 | 3 | 없음 | 0 | 수정 후 향단이 완전 노출(수정 전 우측 잘림) — [이미지](images/phase2/qa-ui-320-fixed.jpg) |
| 390×844 | 60 | 3 | 없음 | 0 | HUD 2행, 드로워 하단 배치 — [이미지](images/phase2/qa-ui-390.jpg) |
| 768×1024 | 60 | 3 | 없음 | 0 | 태블릿 비율 — [이미지](images/phase2/qa-ui-768.jpg) |
| 1440×900 | 60 | 3 | 없음 | 0 | 기본 데스크톱 — [이미지](images/phase2/qa-ui-1440.jpg) |
| 1920×1080 | 60 | 3 | 없음 | 0 | 배경 확장, 좌표 클램프 정상 — [이미지](images/phase2/qa-ui-1920.jpg) |

Reduced Motion 결과: [qa-ui-reduced.jpg](images/phase2/qa-ui-reduced.jpg) (버그 0, 장식 파티클 없음).

## 6. 장시간 실행

### 6.1 공개 뷰어 + API (1시간)

- 구성: QA 서버(8124)에 공개 관전자 2명(8초 간격) + 관리자 `/state` 8초 간격 폴링(450회)을 1시간
- 결과: 공개 900/900 HTTP 200, p50 3.94ms / p95 11.63ms / p99 19.40ms / max 50.16ms, 계약 위반 0,
  lock 오류 0
- 관리자 폴링 로그: 450/450 HTTP 200 (`/tmp/soak-admin-loop.log`)
- RSS: 15.9MiB 고정(719회 샘플 최대도 15.9MiB, 증가 없음)

### 6.2 브라우저 (47분)

- headless Chrome 1440×900, `?fps=1`로 공개 월드 유지, 60초 간격 샘플
- 결과: 48샘플 전부 FPS 60 · JS 힙 2.31→5.04MiB(중앙값 2.97, 최대 5.65, 단조 증가 없음) ·
  콘솔 오류 0 · 월드 레벨 1→2 정상 진행 · 가로 스크롤 없음 —
  [qa-ui-soak-final.jpg](images/phase2/qa-ui-soak-final.jpg)

### 6.3 Raspberry Pi 1차 (2026-11-25)

- 서버: Pi 클론(`bd63a75`) + 임시 DB, 포트 18233. 1/10/100명 180초 + 100명 300초(8s) + 2명 900초 soak
  → **전 구간 23,896/23,896 HTTP 200, 계약 위반 0, lock 0**, RSS 29.6MB(peak 31.1MB).
  100명 구간 CPU idle 88~90%.
- 브라우저: Pi Chromium 155 headless 1440×900 — FPS 18~21(중앙 19), 콘솔 오류 0, 가로 스크롤 0 —
  [pi-headless-1440.jpg](images/phase2/pi-headless-1440.jpg). GPU 플래그(`--use-gl=egl`) 재시도도 동일.
- 해석·한계: 서버는 Pi에서도 100명·1초를 무리 없이 처리. headless Chromium ~20 FPS는 GPU 없는
  소프트웨어 렌더링 특성으로 보이며 실기 디스플레이 키오스크 성능은 미검증입니다. 임시 인스턴스·클론·
  DB는 측정 후 삭제했습니다. 상세: [PERFORMANCE_REPORT.md](PERFORMANCE_REPORT.md) 10장.

## 7. 성능 요약

개발용 탭을 모두 닫은 상태에서 최종 재측정한 값입니다(M1 호스트, 로컬 루프백).

| 시나리오 | p50 | p95 | p99 | max | 상태 |
| --- | --- | --- | --- | --- | --- |
| 공개 관전 1명 | 6.31ms | 7.01ms | 7.14ms | 7.15ms | 30/30 200 |
| 공개 관전 10명 | 8.36ms | 12.11ms | 13.06ms | 14.74ms | 300/300 200 |
| 공개 관전 50명 | 10.21ms | 22.58ms | 31.31ms | 36.02ms | 1500/1500 200 |
| 공개 관전 100명 | 5.59ms | 14.96ms | 47.19ms | 63.98ms | 3000/3000 200 |
| 공개 관전 100명 (main `326709d`) | 2.88ms | 48.54ms | **2,059.25ms** | **7,826.10ms** | 2,783/2,800, **타임아웃 17** |
| 동시 구매 10×5 | 2.92ms | 29.31ms | 85.75ms | 116.57ms | 50/50 200, lock 0 |
| 관리자 동시 조회 20×10 | 2.93ms | 31.61ms | 80.27ms | 126.93ms | 200/200 200, lock 0 |
| **Pi** 공개 100명·1s | 2.65ms | 20.69ms | 39.65ms | 161.52ms | 17900/17900 200, lock 0 |
| **Pi** 공개 100명·8s | 2.37ms | 14.10ms | 68.35ms | 81.61ms | 3800/3800 200, lock 0 |
| **Pi** soak 2명·8s (15분) | 3.03ms | 5.28ms | 12.33ms | 12.73ms | 226/226 200, lock 0 |

계약 위반 0, SQLite lock 오류 0. 상세·방법·한계는 [`PERFORMANCE_REPORT.md`](PERFORMANCE_REPORT.md).

## 8. Before / After UI

| 화면 | Before (main `326709d`) | After (feature) |
| --- | --- | --- |
| 1440 | [qa-before-1440.jpg](images/phase2/qa-before-1440.jpg) | [qa-after-1440.jpg](images/phase2/qa-after-1440.jpg) |
| 390 | [qa-before-390.jpg](images/phase2/qa-before-390.jpg) | [qa-after-390.jpg](images/phase2/qa-after-390.jpg) |

차이: After에서 ① 다음 증설 슬롯(점선) 안내 ② 좁은 화면에서 향단이가 랙 라벨과 겹치지 않고
화면 안에 유지 ③ 상단 HUD RPS/보유 요청이 최신 상태로 표시. 두 서버 모두 같은 DB 스키마(운영 미적용).

## 9. 미실행·한계

- **운영 서버(Nginx 경유) 및 실기 디스플레이 미실측**: Pi 1차는 임시 인스턴스(루프백)와 headless
  Chromium뿐입니다. GPU 있는 실기 브라우저 FPS는 확인하지 않았습니다.
- 실제 다중 네트워크 클라이언트가 아닌 한 호스트의 멀티프로세스 관전자입니다. WAN 지연·프록시는 미측정.
  Pi 측정도 하네스 클라이언트가 서버와 같은 4코어를 공유했습니다.
- 모바일 실기기(Safari/Firefox) 미검증. Chrome 에뮬레이션(DPR 1)만 확인했습니다.
- 카메라 줌/팬, 쿨링 시각화는 구현 전이라 검증 대상이 아닙니다.
- 운영 DB 마이그레이션은 실행하지 않았습니다(배포 금지). 로컬 복제 DB와 테스트로만 검증했습니다.

## 10. 재현 방법

```bash
# 단위·통합 테스트
python3 -m unittest discover -s tests -t .

# 프리뷰 서버
python3 -m server --dev-user hd2service --static-root static --public-root static/public \
  --port 8123 --db /tmp/idle-qa.sqlite3

# 부하 (프록시 우회 내장)
python3 tools/qa_perf.py public --base http://127.0.0.1:8123 --viewers 100 --seconds 30 --interval 1
python3 tools/qa_perf.py action --base http://127.0.0.1:8123 --workers 10 --ops 5
python3 tools/qa_perf.py adminread --base http://127.0.0.1:8123 --workers 20 --ops 10

# 1시간 soak
python3 tools/qa_perf.py public --base http://127.0.0.1:8124 --viewers 2 --seconds 3600 --interval 8 \
  --rss-pid <server-pid> --out /tmp/soak-public-1h.json

# Raspberry Pi 1차 재측정 (운영 서비스와 분리된 임시 인스턴스, 측정 후 삭제)
git clone -b feature/phase2-core-evolution https://github.com/HyangDan2/QDPilot0001.git /tmp/p2qa
cd /tmp/p2qa && python3 -m unittest discover -s tests -t .
python3 -m server --host 127.0.0.1 --port 18233 --db /tmp/p2qa-perf.sqlite3 \
  --dev-user piqa --dev-role admiral --static-root static --public-root static/public &
python3 tools/qa_perf.py public --base http://127.0.0.1:18233 --viewers 100 --seconds 180 --interval 1
```
