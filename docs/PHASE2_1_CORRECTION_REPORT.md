# Phase 2.1 교정 보고서 — Idle Ops

- 브랜치: `feature/phase2-core-evolution` (base `main` `326709d`)
- 작성일: 2026-11-25 (KST)
- 상태: **PO 리뷰 요청** — main 병합·운영 배포·운영 DB 마이그레이션은 수행하지 않았습니다.
- 동반 문서: [`PHASE2_1_QA_REPORT.md`](PHASE2_1_QA_REPORT.md),
  [`PHASE2_1_PERFORMANCE_REPORT.md`](PHASE2_1_PERFORMANCE_REPORT.md),
  [`PHASE2_1_RELEASE_GATE.md`](PHASE2_1_RELEASE_GATE.md)

## 1. 범위와 제약

Phase 2 제출 후 워크오더의 교정 항목(WO-01~10)을 순서대로 반영했습니다. 제약 준수:

| 제약 | 준수 |
| --- | --- |
| 기존 Phase 2 아키텍처 유지 | 표준 라이브러리 + SQLite(WAL) + Canvas 2D 구조 그대로. 신규 인프라·프레임워크 없음 |
| 신규 게임 콘텐츠 개발 금지 | 경제·전투·스테이지 콘텐츠 추가 없음. 밸런스 지문 `3c25713b78cb6bc6` 불변 |
| 기존 운영 DB·진행 기록 보존 | 운영 DB 미접속. 모든 검증은 임시 DB. 마이그레이션은 가산적(`ALTER TABLE`, `CREATE INDEX`) |
| 밸런스 무단 변경 금지 | `server/economy.py` 수치 변경 없음. `test_content` 지문 테스트 유지 |
| 신규 아트워크 임의 생성 금지 | 최종 아트 미생성. 테스트용 번호 타일만 `tests/fixtures/sprites/`에 커밋, 배포 트리 제외 |
| 테스트 삭제·조건 완화 금지 | 기존 테스트 유지. Phase 2 기준선 61 → 128 → **187**(Python) + 22(Node) |
| main 자동 병합·운영 배포 금지 | 커밋·푸시만 수행, 병합/배포/라이브 DB 마이그레이션 없음 |
| 권한 이름 추측 금지 | Hard Reset 권한은 `X-HD2Lab-Role=admiral` **과** `X-HD2Lab-Account-Role=chairman` 이중 신호(WO-02) |
| 미실측 성능을 운영 실측으로 보고 금지 | 모든 수치는 호스트·커밋과 함께 표기, Pi/운영 미측정 항목은 별도 표기 |

## 2. WO별 결과

| WO | 내용 | 커밋 | 검증 |
| --- | --- | --- | --- |
| WO-01 | Hard Reset을 pre-reset 백업 전부터 커밋 후까지 배타 게이트로 직렬화 | `72e6777` | `test_hard_reset.py`, `test_backup_hardening.py` |
| WO-02 | Hard Reset에 실제 chairman 계정 역할 요구(admiral 단독 불가) | `c573334` | `test_api.py` 권한·확인 케이스 |
| WO-03 | 상한 정산·비용 상한·확인 지점 단조성 | `fe0e77e` | `test_economy_limits.py` |
| WO-04 | 미검증 백업 거부·복원 가역성(pre-restore) | `93688f6` | `test_backup_hardening.py` |
| WO-05 | 관리자별 확인 지점(`admin_seen.observed_*`) | `e48c998` | `test_checkpoints.py` |
| WO-06 | `world.revision`·스냅샷 일관 공개 읽기·stale/503 | `3bf72df` | `test_world_revision.py` |
| WO-07 | 관리자 변경 op 직렬화(중복·충돌·재시도) | `3b93a1a` | Node `tests/js/op_coordinator.test.mjs` |
| WO-08 | 자산별 facing 정정, manifest v3, 상태별 지연 로딩, 아트 문서 | `7c6cfc2` | Node 13 + 브라우저 스크린샷/아틀라스 E2E |
| WO-09 | readiness 검증·공개 정보 최소화, 타임아웃/동시성, 보존 검토, 보고서 정정 | 이 보고서 커밋 | `test_ops_hardening.py` + 전체 187 |
| WO-10 | REG-01~20 회귀, 성능 비교, 320~1920 스크린샷, 1시간 실행, 릴리스 게이트 | 이후 커밋 | [`PHASE2_1_RELEASE_GATE.md`](PHASE2_1_RELEASE_GATE.md) |

### 2.1 WO-08 요약 — 방향·매니페스트·지연 로딩

- **버그**: 향단이 원본(`hyangdani-idle/run/strike.png`)은 오른쪽을 보는데 엔진이 왼쪽 기준으로
  반전해 왼쪽 이동에서 뒤로 걷는 것처럼 보였습니다. 수정 후 `flipFor(facing, dir)`는 **바라보는
  방향과 이동 방향이 다를 때만** 반전합니다. 버그 아트(왼쪽)는 그대로입니다.
- manifest v3: 상태별 `source`(strip/atlas/sequence), `fallback {file,facing}`, `anchors`,
  `durations`, `seconds`, `hits`, `maxTexture:4096`. v2(`sheet`, `hitFrame`, 전역 `facing`)는
  호환 로드합니다. 잘못된 소스는 `library.problems`에 남고 fallback으로 내려갑니다.
- 지연 로딩: 부팅 시 idle+버그만 디코드하고 나머지는 상태 진입 시 로드합니다. 실패는 30초
  쿨다운으로 재요청 폭주를 막고, manifest 로드 전 기본값으로 그린 상태가 lazy 로딩을 막던
  버그는 generation 무효화로 수정했습니다(회귀 테스트 포함).
- 증거: `docs/screenshots/wo08/{run,attack,idle}-{right,left}.png`,
  `tools/wo08_atlas_check.py`(아틀라스 슬라이스·fallback E2E).

## 3. WO-09 코드 변경

### 3.1 Readiness가 실제 필수 행을 검증

이전 구현은 `SELECT 1 FROM world WHERE id = 1`의 결과를 확인하지 않아 **world 행이 없어도
`ok: true`가 될 수 있었습니다**. 또한 `version <= SCHEMA_VERSION`이라 구버전 스키마도 통과했습니다.

`Store.readiness()`는 이제 다음을 모두 만족해야 `ok: true`입니다.

| 검사 | 내용 |
| --- | --- |
| `world` | `id=1` 행이 존재하고 `state_json`이 JSON 객체로 파싱됨 |
| `schema` | `PRAGMA user_version`이 `SCHEMA_VERSION`(4)과 **정확히 일치** |
| `disk` | 데이터 볼륨 여유 공간 ≥ `MIN_FREE_BYTES`(256MiB) |

### 3.2 공개 readiness 응답 최소화

이전 응답은 `storage.schemaVersion`, `dbBytes`, `freeBytes`, `backupCount`를 그대로 노출했습니다.
이제 HTTP 응답은 다음뿐입니다.

```json
{"status":"ok","service":"idle-ops","checks":{"world":true,"schema":true,"disk":true}}
```

실패 시 503 + `status: "degraded"`이며, 스키마 버전·여유 공간·DB 크기 등 상세는 **서비스 로그**에만
남깁니다. 내부 `Store.readiness()`는 모니터링용 상세를 계속 반환합니다(테스트로 고정).

### 3.3 타임아웃·동시성

- `Handler.timeout = 20`: 요청 라인/본문이 도착하지 않거나 keep-alive 연결이 유휴이면 20초 후
  닫아 스레드를 붙잡지 않습니다.
- `Server.daemon_threads = True`, `allow_reuse_address = True`를 명시했습니다(종료 시 워커가
  남지 않음). listen backlog는 128을 유지합니다.
- 요청 본문 상한 8KiB, 공개 GET은 TTL 1초 스냅샷 single-flight로 DB 읽기를 공유합니다.
- **스레드 수 상한은 두지 않았습니다.** stdlib `ThreadingHTTPServer`는 연결당 1스레드이며, 상한을
  두려면 대기 큐·거부 정책이 필요합니다. 현재 규모(관전자 수십~100명)에서는 backlog+타임아웃으로
  충분하다고 판단했고, 초과 시 Nginx `limit_conn`으로 연결 수를 제한하는 정책을 권고합니다(5.1).

### 3.4 보존·메모리

- `world_ops(created_at)` 인덱스를 추가해 30일 TTL 삭제(모든 op 쓰기마다 실행)가 행 수에 비례해
  느려지지 않게 했습니다.
- 이벤트는 최신 400개, 응답은 60개/24KiB 상한으로 제한됩니다.
- `audit_log`는 **자동 삭제하지 않습니다**(감사 기록). 액션 1건당 수백 바이트로 디스크 증가는
  완만하며, 필요 시 PO 승인 후 보존 정책을 정합니다.
- 서버 메모리는 요청별 연결 종료, 단일 스냅샷, 이벤트 상한으로 유지됩니다. 브라우저는 상태별
  이미지 맵·실패 쿨다운 맵이 manifest 키 수로 고정되고, 파티클·텍스트·링·슬래시·버그 풀에 상한이
  있습니다.

## 4. 보고서 정정 내역

| 문서 | 이전 서술 | 정정 |
| --- | --- | --- |
| `PHASE2_IMPLEMENTATION_REPORT` | 테스트 128, 왼쪽 facing, 스키마 v2, `storage` readiness | 정정 배너 + 187/오른쪽 원본·자산별 facing/v4/불리언 checks/rate limit 정책 반영 |
| `PHASE2_QA_REPORT` | "2F 필수 매트릭스 전 항목 실행 완료" | 범위 명시, 47분 브라우저 실행을 **PARTIAL(1시간 미달)**로 표기, SSO/Pi/Nginx 미실행 명시 |
| `PERFORMANCE_REPORT` | Pi 실측이 현재 트리처럼 읽힘, manifest v2 | Phase 2 시점(`bd63a75`) 측정임을 명시, Pi 재측정 필요 |
| `DB_MIGRATION` | `user_version=2`, `storage` JSON 상세 | v3/v4 추가, 불리언 checks, kind별 실제 백업 보존, 보존·정리 정책표 |
| `ECONOMY_SPEC` | 스키마 버전 2 | 4 |
| `ART_DIRECTION` | 기본 방향 왼쪽, 320×640 시트 | 자산별 facing(향단이 오른쪽)·v3 768×768·프레임별 앵커, 납품 링크를 V3로 |
| `ANIMATION_SPEC` | (없음) | 테스트 아틀라스는 `tests/fixtures/` 전용임을 명시 |
| `ASSET_REQUIREMENTS` | manifest `sheet` | v2 기록임을 명시, 현행 v3 `source` 안내 |
| `ASSET_REQUIREMENTS_V3` | Pi·모바일 측정은 WO-10 수행 | **아직 미수행**으로 정정 |

## 5. 운영 정책 검토

### 5.1 Rate limit

- **서버 내장 rate limit은 없습니다.** 공개 GET은 TTL 1초 스냅샷으로 읽기 1회/초 수준이고,
  관리자 액션은 사람이 누르는 빈도입니다. 대신 요청 본문 8KiB, listen backlog 128, 20초 읽기
  타임아웃으로 자원 고갈을 제한합니다.
- 권고(Nginx, **미적용·미부하검증**): 공개 API `limit_req rate=5r/s burst=10 nodelay`,
  관리자 API `rate=2r/s burst=5`, `limit_conn 10`/IP. 관전자는 1~8초 간격 폴링이므로 5r/s면
  정상 사용에 걸리지 않습니다. 프록시 뒤 실제 IP는 `real_ip` 설정을 전제로 합니다.
- 이 정책은 운영 Nginx 설정 파일에 반영하지 않았고, 부하 테스트도 하지 않았습니다. Server-HQ
  운영 규약과 함께 검토가 필요합니다.

### 5.2 타임아웃·동시성

| 항목 | 값/정책 |
| --- | --- |
| HTTP 연결 읽기 타임아웃 | 20초 (`Handler.timeout`) |
| 월드 쓰기 게이트 대기 | 20초 → 초과 시 재시도 가능한 503 |
| Hard Reset 배타 게이트 대기 | 60초 |
| SQLite `busy_timeout` | 5초 |
| listen backlog | 128 (TCP/UDS) |
| 워커 스레드 | 요청당 1개, daemon. 상한 없음(위 3.3) |

### 5.3 보존

| 대상 | 정책 |
| --- | --- |
| `world_ops`/`action_ops` | 30일 TTL, op 쓰기마다/기동 시 삭제(`created_at` 인덱스) |
| `world_events` | 최신 400개 |
| `audit_log` | 무기한(감사) |
| 백업 | daily/manual/pre-deploy 기본 14개, pre-hardreset/pre-restore 자동 삭제 없음 |
| 브라우저 메모리 | 상태별 이미지·쿨다운 맵 고정, 이펙트 상한 |

## 6. 미실행·미검증 항목

- 운영 Nginx 경유 부하·`limit_req` 검증: **미실행**
- Raspberry Pi 재측정(Phase 2.1 코드): **미실행** (Pi 수치는 `bd63a75` 기준)
- 모바일 실기 Safari/Firefox: **미검증**
- 운영 SSO(실제 chairman 계정) 역할 검증: **미실행** (헤더 주입 테스트만)
- 운영 DB 마이그레이션: **미실행** (배포 금지)
- 실기 디스플레이/GPU 키오스크 FPS: **미측정**
- 카메라 줌·팬, 쿨링 시각화: **미구현**
- 최종 아트 납품: **없음** (fallback 렌더링)

## 7. 승인 게이트

main 병합, 운영 서버 배포, 라이브 DB 마이그레이션은 PO 승인 전 수행하지 않습니다. 브랜치 푸시와
외부 리뷰 요청까지가 이번 제출 범위입니다.
