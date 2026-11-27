# QDPilot0001 Idle Ops — DB 마이그레이션·백업·복구

이 문서는 Idle Ops 공유 월드 SQLite(`world`/`world_ops`/`world_events`/`admin_seen`/`audit_log`)의
스키마 버전, 자동 마이그레이션, 정합성 백업, 복구 절차를 정의한다.

## 1. 스키마 버전

스키마 버전은 `PRAGMA user_version`에 기록된다(`server/store.py`의 `SCHEMA_VERSION`).

| 버전 | 내용 |
| --- | --- |
| 0 | 스키마 표식 이전(레거시). 기동 시 최신 버전으로 올린다. |
| 1 | 단일 `world` 행, `world_ops`(opId), `world_events`, 레거시 `players`, `action_ops` |
| 2 | `world_ops.action_json/result_json`(멱등 재실행), `admin_seen`(관리자별 타임스탬프), `audit_log`(Hard Reset 감사) |
| 3 | `admin_seen.observed_requests/observed_level/world_generation`(관리자별 확인 지점) |
| 4 | `world.revision`(커밋마다 증가, 공개 스냅샷·이벤트 상한 정합) + `world_ops(created_at)` 인덱스 |

- 기동 시 `Store.__init__`이 `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE`(누락 컬럼)로
  멱등 마이그레이션을 수행하고 `user_version`을 올린다. 별도 마이그레이션 명령은 필요 없다.
- **롤백 호환**: 저장된 `user_version`이 코드의 `SCHEMA_VERSION`보다 크면 `SchemaTooNew`를
  발생시키고 기동을 거부한다. 신버전 릴리스가 쓴 DB를 구버전 코드가 덮어쓰는 사고를 막기 위함이다.
  구버전으로 롤백할 때는 4장의 pre-deploy 백업을 복구한다.
- 레거시 `players` 테이블은 최초 기동 시 최고 누적 생산량 1행만 `world`로 이관하고 원본은 보존한다.

## 2. 백업

백업은 SQLite 온라인 백업 API(`Connection.backup`)를 사용하므로 **서비스 중에도 정합성 있는 스냅샷**을
만든다. 스냅샷은 WAL이 아닌 단일 파일(`journal_mode=DELETE`)로 떨어지고, 생성 직후
`PRAGMA quick_check`로 검증에 실패하면 파일을 지우고 예외를 낸다(성공 위장 금지).

백업 파일 이름: `idle-ops-<kind>-<UTCstamp>.sqlite3` (같은 초 충돌 시 `-2`, `-3` 접미사)
기본 디렉터리: DB 옆 `backups/` 또는 `IDLE_OPS_BACKUP_DIR`.

| kind | 생성 시점 | 보존 |
| --- | --- | --- |
| `daily` | 일 1회(스케줄) | 같은 kind 최근 `BACKUP_KEEP`(14)개 |
| `pre-deploy` | 릴리스 배포 직전 | CLI 기본 `--keep 14`; 전부 보관하려면 `--keep 0` |
| `pre-hardreset` | Hard Reset 실행 직전(자동) | 자동 정리 없음(`keep=0`) — 수동 정리 |
| `pre-restore` | 복구 실행 직전(자동) | 자동 정리 없음(`keep=0`) — 수동 정리 |
| `manual` | 운영자 수동 | CLI 기본 `--keep 14` |

```bash
# 일일 백업 (cron 예: 04:10 KST)
10 4 * * * cd /srv/hd2lab/idle-ops && python3 -m server.backup create --db data/idle-ops.sqlite3 --kind daily

# 배포 직전 백업 — 릴리스 교체 전 필수 단계
python3 -m server.backup create --db data/idle-ops.sqlite3 --kind pre-deploy

# 무결성 검증
python3 -m server.backup verify data/backups/idle-ops-daily-<stamp>.sqlite3
```

`daily`는 같은 날짜의 백업이 이미 있으면 아무 것도 만들지 않고 종료한다(`--force`로 추가 생성).
생성 시 같은 kind의 오래된 파일을 `--keep`(기본 14)개까지만 남긴다.

## 3. 복구

복구는 **서비스를 멈춘 상태**에서 실행한다. 스크립트는 원본을 검증하고, 현재 DB를
`pre-restore`로 백업한 뒤, 온라인 백업 API로 스테이징 파일을 만들어 `os.replace`로 교체하고,
잔여 `-wal`/`-shm`을 정리한 뒤 다시 `quick_check`로 검증한다. 서비스가 DB를 잡고 있으면
(`BEGIN EXCLUSIVE` 실패) 복구를 중단한다.

```bash
systemctl stop hd2lab-idle-ops            # 예시 — 실제 유닛명은 Server-HQ 배포 정의를 따른다
python3 -m server.backup verify data/backups/idle-ops-daily-<stamp>.sqlite3
python3 -m server.backup restore --db data/idle-ops.sqlite3 \
  --file data/backups/idle-ops-daily-<stamp>.sqlite3
python3 -m server.backup verify data/idle-ops.sqlite3
systemctl start hd2lab-idle-ops
curl -fsS --unix-socket /run/hd2lab/idle-ops.sock \
  http://localhost/admin/idle-ops/api/v1/health/ready
```

복구 후 클라이언트는 이벤트 커서가 저장된 최대 id보다 커질 수 있다. 이때 서버는
`cursorReset: true`와 새 커서를 내려주고, 공개 뷰어는 이벤트 로그를 다시 맞춘다(중복 재생 없음).

## 4. 배포·롤백 절차

1. `python3 -m server.backup create --db <live-db> --kind pre-deploy`
2. 새 릴리스 배치 → 재기동 → `health/ready` 200 확인 → 관리자/공개 화면 스모크 테스트
3. 문제 시: 이전 릴리스로 되돌리고 3장 절차로 pre-deploy 백업 복구

`user_version` 상향은 새 릴리스에서만 일어나며, 구버전 코드는 더 높은 버전의 DB를 열지 않고 거부한다
(1장의 롤백 호환). 따라서 롤백 = 이전 바이너리 + pre-deploy 백업 복구다.

## 5. Readiness

`GET /admin/idle-ops/api/v1/health/ready`(인증 없음)는 저장소 상태를 세 가지 불리언으로만 노출한다.
크기·스키마 버전·경로·백업 개수는 응답에 넣지 않고, 실패 시 서비스 로그에만 남긴다.

```json
{
  "status": "ok",
  "service": "idle-ops",
  "checks": { "world": true, "schema": true, "disk": true }
}
```

- `checks.world`: 필수 `world` 행이 존재하고 `state_json`이 객체로 파싱된다.
- `checks.schema`: `PRAGMA user_version`이 이 릴리스의 `SCHEMA_VERSION`과 **정확히 같다**.
- `checks.disk`: 데이터 볼륨 여유 공간이 `MIN_FREE_BYTES`(256MiB) 이상이다.
- 셋 중 하나라도 false면 HTTP 503 + `status: "degraded"`.
- `Store.readiness()`는 내부 진단용 상세(`schemaVersion`, `dbBytes`, `freeBytes`, `backupCount`)를
  계속 반환하지만 HTTP 계층은 이를 직렬화하지 않는다.
- 공개 경로(`/idle-ops/`)에는 readiness를 노출하지 않는다.

## 6. 보존·정리 정책

| 대상 | 정책 | 근거 |
| --- | --- | --- |
| `world_ops` / 레거시 `action_ops` | 30일(`OP_TTL_SECONDS`) 초과 삭제. `world_ops`는 opId가 있는 모든 쓰기에서, 레거시 테이블은 기동 시 정리 | `world_ops(created_at)` 인덱스로 삭제 비용 고정 |
| `world_events` | 최신 400개(`EVENT_RETENTION`)만 유지, 이벤트 생성 시 프루닝 | 공개 연출용 스트림, 복원 후 커서 재동기화 |
| `audit_log` | 자동 삭제 없음(감사 기록 보존) | 액션 1건당 수백 바이트 수준, 디스크 증가는 완만 |
| `admin_seen` | 관리자 1명당 1행 | 확인 지점만 저장 |
| 백업 | 위 2장 표의 kind별 보존 | pre-hardreset/pre-restore는 자동 삭제하지 않음 |

- 서버 메모리는 요청마다 열고 닫는 SQLite 연결, 단일 `SnapshotCache` 상태, `EVENT_LIMIT`/`EVENT_BYTES`
  상한으로 제한된다. 요청 처리 스레드는 daemon이고 20초 읽기 타임아웃으로 유휴 연결이 스레드를
  무한히 붙잡지 않는다.

## 7. 하드 리셋과 감사 로그

Hard Reset은 실행 직전 `pre-hardreset` 백업을 남기고, `audit_log`에 이전 상태 요약
(`requests`, `run_lifetime`, `total_lifetime`, `tokens`, `reboots`, `achievements`, `op_id`, `backup`)을
기록한다. 백업 실패 시 리셋은 실행되지 않는다(fail closed). 공개 이벤트 스트림에는
`backup` 경로·actor·opId·message가 포함되지 않는다.
