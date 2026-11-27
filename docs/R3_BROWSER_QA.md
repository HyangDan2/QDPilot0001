# R3 공개 관제실 브라우저 QA

macOS의 Chrome (headless), Python 표준 라이브러리 Idle Ops 서버, 임시 SQLite DB에서 실행했다. QA는 등록 프로젝트 9개(공개 8개, 비공개 1개), 테스트 이슈·CI 실패, 작업 중 및 하트비트가 만료된 에이전트를 합성해 실제 R3 읽기 API와 UI로 검증했다. 이 테스트에 사용한 프로젝트와 에이전트는 픽스처다.

| 확인 | 결과 |
| --- | --- |
| 데스크톱 1440 × 900 | 전체/선택 프로젝트 필터, 서버실 랙 이전·다음 페이지, 전체 화면 진입·종료 통과 |
| 모바일 390 × 844, 320 × 700 | 프로젝트 드로어 열기·필터·닫기 통과, 가로 넘침 없음, 강제 DPR 3에서 Canvas DPR 2 상한 적용 |
| 모션 감소 | OS 모션 감소 상태에서 정적 화면 유지 및 rAF 중지 |
| 전투와 캐릭터 | 열린 이슈의 수리 연출, 검증된 이슈 해결 이후 공격과 피격, 랙 설치·레벨업 재생 확인 |
| V3.1 아트 | Idle·Run·Repair·Attack·Install·LevelUp 모두 기존 384px 서비스 아틀라스 로드 및 재생. 검사 시 데스크톱 60 FPS |
| 공개 API | POST 405, 비공개 프로젝트 및 원본 작업 ID 비노출 |
| 텔레메트리 표시 | 관측 중인 에이전트와 하트비트 만료 에이전트 구분 |
| 데모 | 예시 표시와 별도 랙 확인, 라이브 프로젝트 및 XP 변화 없음 |
| 브라우저 오류 | 콘솔 오류, 페이지 예외, 자산 로딩 실패 0건 |

Playwright가 설치된 환경에서 실행한다. `PLAYWRIGHT_MODULE`을 지정하면 해당 설치를 사용하며, `CHROME_BIN`을 지정하면 기존 Chrome을 시작한다. `QA_PORT` 기본값은 8123이다.

```sh
node tools/qa_r3_public.mjs /tmp/idle-ops-r3-screenshots
node --test tests/js/*.test.mjs
```

- [공개 1440px](screenshots/r3/public-desktop.jpg)
- [공개 390px](screenshots/r3/public-mobile.jpg)

운영 SSO 로그인, 운영 웹훅 전달과 에이전트 연결, Raspberry Pi 장시간 브라우저 운용은 이 검사 범위 밖이다.
