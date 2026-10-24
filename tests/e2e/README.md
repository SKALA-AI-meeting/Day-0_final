# e2e

`run.py`가 `scenarios/e2e/*.yaml` 대본으로 실제 서비스를 상대해 세션을 끝까지 진행하고 `records/<일시>-<대본>/`에 기록을 남긴다. 형식과 단언은 `docs/guides/testing.md`, step 키는 `scenarios/e2e/README.md`. 단계별 화면은 Playwright(`apps/web/e2e/stages/`)가 맡고 이 러너는 세션 전체를 API·WS 수준에서 본다.

| 파일 | 역할 |
|---|---|
| `run.py` | 러너(CLI). 관리자 가입 → 채용 건(15분)·초대 → 입장·동의·시작 → WS·REST로 step 진행 → 제출·자동 제출 → report_ready → 검토(단언 6)·final·전체 기록 → 내부 API·DB 수집 → 단언 → 기록 |
| `asserts.py` | 단언. 함수 이름의 번호가 `testing.md` 번호(0 러너 step 오류, R restart) |
| `records.py` | 기록 폴더(transcript.jsonl·turn_decisions·evaluations·report_draft/final·record·summary.md) |
| `client.py` | REST(공개·내부)·WS 클라이언트. 오간 것은 전부 transcript(토큰 값은 가림) |
| `catalog.py` | 시나리오 카탈로그(`scenarios/missions/debate`)에서 카드 시각·자리-유형 읽기 |
| `db.py` | `agent_runs`(턴 결정)·세션 비용·의도 분류 지연 읽기(읽기 전용, 없으면 건너뜀) |
| `faults.py` | 장애 주입 프록시(워커 → api 내부)와 단언 10(큐 DLQ·LLM timeout) 흐름 |
| `stack.py` | 장애 주입용 실 스택(api·debate-mcp·assessment-mcp·report-worker를 하위 프로세스로) |
| `auth.py` | 단언 8 인증 경계(`records/<일시>-auth/auth.md`) |
| `fake_stack.py`, `conftest.py`, `test_*.py` | mock: 계약만으로 만든 가짜 api 위에서 러너를 그대로 돌리는 단위 테스트 |

## 실행

```
make test SUITE=tools          # mock: 가짜 api에서 러너·단언·장애 주입·인증 경계 + tests/eval 형태 검사 (스택·DB·LLM 불필요)
make e2e PROFILE=…             # 실 스택에서 all_five 15분 1회(벽시계). SCRIPT=timeout_all 등으로 대본 변경
uv run --project services/api python tests/e2e/run.py --script timeout_all --clock fake   # 시간을 당겨 빠르게(ENABLE_TEST_API=1)
# env LLM_FAKE=1로 결정적 스택이 명시되지 않았으면(--live·LLM_FAKE=0·없음) --script·--auth는 ALLOW_API=1이 있어야 시작한다
make e2e SUITE=faults          # 단언 10: 러너가 앱을 띄우고 큐 DLQ·LLM timeout 주입(가짜 LLM 프로필)
make e2e SUITE=auth PROFILE=…  # 단언 8
make eval PROFILE=eval-text ALLOW_API=1   # tests/eval을 실제 모델로 1회 → records/eval-live-<일시>.md
```

환경: `infra/.env` → `infra/.env.local`(환경변수가 우선). `E2E_PUBLIC_URL`(기본 localhost:8000), `E2E_INTERNAL_URL`(기본 localhost:8001), `INTERNAL_TOKEN`, `JWT_SECRET`(만료 토큰 단언), `E2E_DATABASE_URL`(없으면 `DATABASE_URL`, 읽기 전용), `E2E_ASSESSMENT_MCP_URL`, `E2E_RESTART_CMD`(`restart` 대본에서 api 재시작 명령). 실 스택 api는 `ENABLE_TEST_API=1`·`AGENT_RUNNER=session`으로 떠 있어야 한다. 결정적 LLM 모드(`LLM_FAKE=1`) 스택이면 `--live` 없이, 실제 모델이면 `--live`(수준 값 기대를 건너뜀).

패키지 이름: 이 폴더는 `e2e`로 import한다(`tests/`를 경로에 넣음). 최상위 이름 `tests`는 api 테스트 패키지가 쓰므로 러너·가짜 api는 `tests.`를 import하지 않는다.
