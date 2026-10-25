# 설정과 실행 모드

env 이름과 기본값의 예시는 `infra/.env.example`, 코드의 기본값은 각 서비스 `config.py`가 정본이다. 이 문서는 실행 프로필과 실행 모드마다 무엇이 어디에 붙는지, env가 어떤 순서로 읽히는지를 적는다.

## 실행 프로필 (`PROFILE`)

실행 모드(가짜/실제 LLM, 음성, 테스트 API, 포트, compose 프로젝트, 추적)는 `infra/profiles/<이름>.env`가 정한다. 비밀값은 `infra/.env`에만 두고 프로필 파일에는 비밀 아닌 선택값만 둔다(비밀 이름에 값이 있으면 해석기가 거부한다). `scripts/profile.py`가 **기본값 → `infra/.env` → 프로필** 순으로 겹쳐 유효 모드를 만들고, `make up/down/seed/eval/measure/e2e/stage PROFILE=…`이 그 값을 compose(`--env-file`과 컨테이너 `env_file` 모두 `infra/.env.resolved-<프로필>`, git 무시)와 호스트 러너(프로세스 env)에 같이 준다.

| 프로필 | 목적 | LLM | 음성 | 테스트 API | compose 프로젝트·포트 | 외부 사용 |
|---|---|---|---|---|---|
| `dev-fake`(기본) | dev | fake(`LLM_FAKE=1`) | fake | 1 | `infra`, web 3000·api 8000 | 없음 |
| `test-text` | test | fake | 끔 | 1 | `dayzero-debate`, web 3100·api 8100·내부 8101·postgres 5442·rag 9112 | 없음 |
| `eval-text` | test·측정 | 실제(제한 키) | 끔 | 1 | `dayzero-debate`, 위와 같음 | LLM, `ALLOW_API=1` 필요 |
| `test-voice` | test | fake | fake | 1 | `dayzero-debate`, 위와 같음 | 없음 |
| `eval-voice` | test·측정 | 실제(제한 키) | fake | 1 | `dayzero-debate`, 위와 같음 | LLM, `ALLOW_API=1` 필요 |
| `dev-real` | dev | 실제(제한 키) | Azure | 0 | `infra`, web 3000·api 8000 | LLM, `ALLOW_API=1` 필요 |
| `deploy` | deploy | 실제(제한 키) | Azure | 0(1 금지) | `infra`(공개 주소는 `.env`) | LLM, `ALLOW_API=1` 필요 |

- `make profile PROFILE=…`은 유효 모드(목적·역할별 모델·키 **이름과 있음/없음**·테스트 API·compose 프로젝트·포트)를 출력하고, `make doctor PROFILE=…`은 거기에 검사를 더한다(외부 호출 없음): deploy에 가짜 LLM·테스트 API·자리표시 비밀값 금지, 실제 LLM 프로필의 역할별 모델이 허용 목록에 있고 키가 있는지, 셸의 모드 변수(`LLM_FAKE`·`ENABLE_TEST_API`·`AGENT_RUNNER`·추적 플래그)가 프로필과 다르지 않은지. 비밀값은 어디에도 출력하지 않는다.
- **과금 동의**: 실제 LLM 프로필로 무언가를 띄우거나 부르는 명령(`make up`, `make eval`, `make measure`, live 테스트, 판정·가상 지원자 스크립트 직접 실행)은 매번 `ALLOW_API=1`을 명시해야 한다. 키가 있다는 이유로 호출하지 않는다. `make down`은 동의·키 없이 된다.
- `VOICE_ENABLED=0`(`false`도 허용)은 `ENABLE_TEST_API=1`일 때만 허용된다. 이 모드에서는 텍스트 입력과 WS `message.send`를 쓰고 음성 공급자·quota·재생 스케줄을 시작하지 않는다. `VOICE_ENABLED=1`은 채용 건의 `voice_policy.provider`와 실행 공급자가 같아야 시작한다. fake 프로필에서 시드한 채용 건은 fake 정책을 가지며, 예전 Azure 정책 채용 건은 fake 프로필에서 거부된다.
- `make doctor`는 음성 모드와 Azure 키의 이름·있음/없음만 보여 주며 Azure 검증 API는 호출하지 않는다.

## 실행 모드

| | 로컬 개발(`SCOPE=infra`) | 전체 compose | 격리 스택 |
|---|---|---|---|
| 명령 | `make up SCOPE=infra` + 앱 로컬 실행([quickstart.md](quickstart.md)) | `make up PROFILE=dev-fake∣dev-real∣deploy` / `make down` | `make up PROFILE=test-text∣test-voice∣eval-text∣eval-voice` / `make down PROFILE=…` |
| compose 파일 | `docker-compose.yml` + `docker-compose.dev.yml` | `docker-compose.yml` | `docker-compose.yml` + `docker-compose.e2e.yml`, 프로젝트 `dayzero-debate` |
| 컨테이너 | postgres·rabbitmq·rag-mcp | 8개 전부 | 8개 전부 |
| env 파일 | 컨테이너 `infra/.env.resolved-<프로필>`, 로컬 앱 `infra/.env.local` → `infra/.env`(모드 값은 프로필과 맞춘다) | `infra/.env.resolved-<프로필>` | `infra/.env.resolved-<프로필>` |
| web | `localhost:3000`(`pnpm dev`) | `localhost:3000` | `localhost:3100` |
| api 공개 | `localhost:8000` | `localhost:8000`(`API_PORT`) | `localhost:8100` |
| api 내부(8001) | `localhost:8001`(로컬 프로세스) | 컨테이너 네트워크만 | `127.0.0.1:8101` |
| postgres(호스트) | `127.0.0.1:5432` | 열지 않음 | `127.0.0.1:5442` |
| rabbitmq | `127.0.0.1:5672`, 관리 UI `127.0.0.1:15672` | 관리 UI `127.0.0.1:15672`만 | 관리 UI `127.0.0.1:15674`만 |
| rag-mcp(호스트) | `127.0.0.1:9102` | 열지 않음 | `127.0.0.1:9112` |
| debate·assessment MCP | 로컬 9101·9103 | 컨테이너 네트워크만 | 컨테이너 네트워크만 |
| rag-mcp가 부르는 내부 API | `http://host.docker.internal:8001`(로컬 api) | `http://api:8001` | `http://api:8001` |
| 시드 | `make seed SCOPE=infra` | `make seed PROFILE=…`(compose 안 일회성 seed 서비스. 호스트에서는 닿지 않음) | `make seed PROFILE=test-text∣test-voice∣eval-text∣eval-voice`(호스트에서 5442·8100·9112로) |
| 테스트 API | `ENABLE_TEST_API=1`이면 `localhost:8001/internal/test/*` | 컨테이너 안에서만 | `127.0.0.1:8101/internal/test/*` |

- **같은 DB·큐를 쓰는 api는 하나만 띄운다.** 세션 메일박스·WS 송신 큐·스케줄러가 api 프로세스 메모리에 있어 둘이면 같은 세션을 두 번 처리한다. 다른 DB·큐를 쓰는 격리 스택(위 e2e 스택)은 함께 띄워도 된다.
- 로컬 개발 모드와 전체 compose는 호스트 포트 3000·8000을 함께 쓰므로 동시에 띄우지 않는다. 같은 기계에서 두 번째 스택이 필요하면 격리 e2e 스택을 쓰거나 `API_PORT`·`WEB_PORT`·`RABBITMQ_UI_PORT`와 프로젝트 이름(`-p`)을 바꾼다.
- 전체 compose 모드는 postgres·rag-mcp를 호스트에 열지 않아 호스트에서 `scripts/seed.py`가 닿지 않는다. `make seed`가 프로필을 보고 compose 안 일회성 seed 서비스로 돌린다.
- 옛 타깃 이름(`infra-up`·`seed-compose`·`e2e-stack-up` 등)은 별칭으로 남아 새 이름을 안내하고 그대로 실행한다(`make help` 아래 절).

## 로컬 실행용 `infra/.env.local`

`infra/.env.example`의 서비스 주소는 compose 호스트명(`postgres`·`rabbitmq`·`api`·`debate-mcp` 등)이라 호스트에서 띄운 Python 서비스는 닿지 않는다. 로컬 개발 모드(`make up SCOPE=infra` + 호스트에서 앱 실행)에서는 `cp infra/.env infra/.env.local` 뒤 아래 항목만 바꾼다. 로컬 Python 서비스는 `.env.local`을 먼저 읽고, 컨테이너는 이 파일을 읽지 않는다.

| 이름 | 로컬 값 |
|---|---|
| `DATABASE_URL` | 호스트를 `postgres` → `localhost`(포트 5432) |
| `RABBITMQ_URL` | 호스트를 `rabbitmq` → `localhost`(포트 5672) |
| `API_INTERNAL_URL` | `http://localhost:8001` |
| `DEBATE_MCP_URL` | `http://localhost:9101/mcp` |
| `RAG_MCP_URL` | `http://localhost:9102/mcp`(컨테이너 rag-mcp든 로컬 rag-mcp든 같은 포트) |
| `ASSESSMENT_MCP_URL` | `http://localhost:9103/mcp` |
| `HF_HOME` | rag-mcp를 로컬로 띄울 때만: 쓰기 가능한 로컬 폴더(예시 값 `/models`는 컨테이너 경로) |
| `SCENARIOS_DIR` | 예시 값 `/scenarios` 그대로(로컬에 그 경로가 없으면 저장소 `scenarios/`를 쓴다). 빈 값으로 두지 않는다 |

비밀값(키·토큰)은 `infra/.env`와 같게 둔다. compose 컨테이너 rag-mcp는 `docker-compose.dev.yml`이 내부 API 주소를 `http://host.docker.internal:8001`로 따로 준다.

## env를 읽는 순서

| 대상 | 순서(앞이 이김) |
|---|---|
| 프로필 해석(`scripts/profile.py`) | `infra/profiles/<PROFILE>.env` > `infra/.env` > 기본값. 셸의 모드 변수가 다르면 오류 |
| compose 컨테이너 | `docker-compose*.yml`의 `environment:`(DB·큐·내부 API·MCP 주소를 compose 서비스 이름으로 고정) > `env_file`(`DAYZERO_ENV_FILE`, make가 주는 `infra/.env.resolved-<프로필>`; 없으면 `infra/.env`). `infra/.env`에 호스트용 주소가 있거나 파일이 없어도(가짜 LLM 프로필) 컨테이너 주소는 바뀌지 않는다 |
| `make eval/measure/e2e/stage`의 호스트 러너 | 프로필 해석 결과를 프로세스 env로 받는다(`E2E_PUBLIC_URL`·`E2E_INTERNAL_URL`·`WEB_URL`·`VC_API_URL`·`E2E_DATABASE_URL`·`SEED_DATABASE_URL` 호스트 주소 포함). 셸에 같은 이름이 다른 값으로 있으면 모드 변수와 같이 오류다 > 아래 로컬 규칙 |
| 로컬 Python 서비스(api·MCP 3개·report-worker) | 이미 설정된 프로세스 env > `infra/.env.local` > `infra/.env`(`python-dotenv`, 덮어쓰지 않음) |
| 로컬 web(`pnpm dev`) | 프로세스 env와 `apps/web/.env*`(Next.js 규칙). `infra/.env`는 읽지 않는다 |
| web 컨테이너 | `NEXT_PUBLIC_*`는 **이미지 빌드 시점**의 build arg(`apps/web/Dockerfile`). 컨테이너 env를 바꿔도 번들은 바뀌지 않으므로 다시 빌드한다. 값이 없으면 `http://localhost:8000`·`ws://localhost:8000` |
| `scripts/seed.py` | `--database-url`(기본 env `SEED_DATABASE_URL`, 없으면 `localhost:5432`), `--api`(기본 8000), `--rag`(기본 9102) |
| api DB 테스트 | `TEST_DATABASE_URL`(기본 `localhost:5432`). 없거나 닿지 않으면 skip |

## env 목록

| 이름 | 읽는 곳 | 뜻·기본값 |
|---|---|---|
| `OPENAI_API_KEY`, `GEMINI_API_KEY` | api·report-worker LLM 래퍼, 호스트 러너(가상 지원자·판정) | 모델 id로 키를 고른다: 제한 키 허용 목록(`gpt-4o-mini`·`gpt-4.1-mini`·`gpt-5.4-mini`·`gpt-5.4-nano`·`gpt-5-nano`)의 gpt-* → `OPENAI_API_KEY`, gemini-* → `GEMINI_API_KEY`, 목록 밖 gpt-*는 키가 있어도 설정 오류(대체 모델·개인 키 경로 없음). 비어 있으면 설정 오류. 정본은 `services/api/app/agent/llm/roles.py`, 사본은 worker `config.py`·`tests/eval/llm_access.py`·`scripts/profile.py` |
| `ALLOW_API` | Makefile·live 테스트·판정·가상 지원자 진입점 | `1`일 때만 실제 API(과금) 호출을 시작한다. 명령마다 붙이고 env 파일에 두지 않는다 |
| `VOICE_ENABLED`, `VOICE_PROVIDER` | api·web 빌드·프로필 | 기본 `1`·`azure`. `0`은 테스트 API가 켜져야 하며 음성 공급자 생성과 검증을 건너뛴다. fake는 테스트용 |
| `PROFILE`, `DAYZERO_ENV_FILE` | Makefile·compose | 실행 프로필 이름(기본 `dev-fake`), compose 컨테이너 env 파일(make가 준다) |
| `VC_JUDGE_MODEL`, `PERSONA_EVAL_JUDGE` | 판정 모델(귀속·가상 지원자), 페르소나 live | 판정 모델은 기본값 없음(켤 때 명시). 페르소나 live의 귀속 판정은 기본 끔(`1`이면 켬, 모델 필수). [testing.md](testing.md) "귀속 판정" |
| `OPENAI_MODEL` | 같음 | 역할 env가 비었을 때의 모델, 기본 `gpt-4o-mini` |
| `MODEL_INTENT`·`MODEL_TURN`·`MODEL_PERSONA`·`MODEL_ASSISTANT`·`MODEL_EVALUATOR` | 같음 | 역할별 모델. 타임아웃·재시도는 [architecture/harness.md](../architecture/harness.md) "역할별 모델" |
| `DATABASE_URL`, `RABBITMQ_URL` | api·rag-mcp·report-worker | DB·큐 주소 |
| `JWT_SECRET`, `INTERNAL_TOKEN`, `ASSESSMENT_MCP_TOKEN` | api·MCP·report-worker | 서명 키와 내부 토큰. `ASSESSMENT_MCP_TOKEN`은 api 결정적 노드·report-worker·assessment-mcp에만 |
| `API_INTERNAL_URL` | MCP 3개·report-worker | 내부 API, compose 기본 `http://api:8001` |
| `DEBATE_MCP_URL`·`RAG_MCP_URL`·`ASSESSMENT_MCP_URL` | api(report-worker는 assessment만) | MCP 주소(`/mcp`까지) |
| `EMBEDDING_MODEL`, `HF_HOME` | rag-mcp | `BAAI/bge-m3`, 모델 캐시 위치(compose 볼륨 `hfcache`) |
| `SCENARIOS_DIR` | api·rag-mcp | 카탈로그 위치(compose `/scenarios`). 기본값이 없으면 저장소 `scenarios/`, 지정한 경로가 없으면 설정 오류 |
| `ENABLE_TEST_API` | api | `1`이면 `/internal/test/*`(단계 시드·시간 당기기). 운영은 비운다 |
| `LLM_FAKE` | api·report-worker | `1`이면 LLM 호출만 결정적 고정 응답(`[fake]` 표시 없음). 지금 모드 확인은 [quickstart.md](quickstart.md) "7. 실제 LLM으로 돌고 있는지 확인하기" |
| `LLM_FAULT` | api·report-worker | `<role>:timeout:<N>` 장애 주입(e2e 전용) |
| `AGENT_RUNNER` | api | `session`(기본, 토론 그래프) ∣ `fake`(단위 테스트 주입용) |
| `SESSION_COST_HARD_USD` | api | 토론 비용 하드 상한, 기본 `2`(`.env.example`에 없음) |
| `CONTACT_EMAIL` | api | 입장·디브리핑 문의 창구, 기본 `recruit@example.com`(`.env.example`에 없음) |
| `WEB_ORIGIN`, `WEB_BASE_URL` | api | CORS 허용 origin(쉼표 구분), 초대 링크의 웹 주소 |
| `LANGCHAIN_TRACING_V2`, `LANGSMITH_TRACING`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` | api·report-worker | LangSmith trace. 기본 끔, `test-text`·`eval-text` 프로필은 항상 `false` |
| `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`, `NEXT_PUBLIC_MOCK` | web(빌드 시점) | api·WS 주소, `1`이면 mock 모드 |
| `API_PORT`, `WEB_PORT`, `RABBITMQ_UI_PORT` | compose | 공개 포트 번호 |
| `SEED_DATABASE_URL`, `TEST_DATABASE_URL` | `scripts/seed.py`, api 테스트 | 위 "env를 읽는 순서" |
| `WEB_URL`, `E2E_INTERNAL_URL`, `E2E_DATABASE_URL`, `E2E_RESTART_CMD` | Playwright·e2e 러너 | 대상 스택 주소, `restart` 대본의 재시작 명령 |
| `PLAYTEST_PORT` | playtest 드라이버 | 드라이버 서버 포트, 기본 9400 |

`.env.example`에 없는 키를 코드에 더하면 `.env.example`과 이 표를 함께 고친다.


## 음성 모드

기본 `VOICE_PROVIDER=azure`는 F0만 허용한다. `AZURE_SPEECH_KEY`·`AZURE_SPEECH_REGION`·`AZURE_SPEECH_RESOURCE_ID`와 DefaultAzureCredential용 관리 ID 또는 서비스 계정 설정은 `.env`에만 둔다. F0 자원 조회·listKeys 권한이 필요하며 요금제 변경 권한은 주지 않는다. `VOICE_QUOTA_PERIOD`는 사용량 원장 기간 ID다. `auto`(`.env.example` 기본)는 UTC 연-월마다 새 원장이고, 다른 값은 그대로 기간 ID로 쓴다. 값이 없으면 음성 호출이 차단된다. `VOICE_USER_TURN_SECONDS` 기본 60(1~60)은 새 채용 건에 고정된다. `VOICE_TURN_HOLD_SECONDS` 기본 20(5~120)은 내 차례(일반·손들기·강제)를 지켜 주는 시간이다. 모든 차례는 자동 녹음 없이 지원자가 [발언하기]를 눌러 시작하고, 이 시간이 지나면 동료가 이어 말한다(손들기로 다시 잡을 수 있음). 크면 지원자가 생각할 시간이 늘지만 침묵이 길어지고, 작으면 반대다. 새 채용 건에 고정된다. `VOICE_CONFIRM_SECONDS` 기본 8(0~30)은 서버가 새 채용 건에 고정하는 확인 시각 설정이다. 현재 음성 화면은 이 시각을 기다리지 않고 받아 적은 글을 바로 제출한다. dev-fake 프로필은 0으로 설정한다. `VOICE_FORCED_WAIT_SECONDS` 기본 30(0~120)은 강제 차례(그 단계에서 아직 말하지 않은 지원자)가 지금 동료 발언이 끝나기를 기다릴 몫이고, 새 채용 건에 고정된다. 강제 차례는 단계 마감 "이 값 + 발언 한도 + 10초" 전에 오되, 단계 길이(시작~마감)의 절반보다 일찍 오지 않는다(짧은 세션에서 단계 시작 직후 끼어들지 않게). 크면 60초 발언이 마감에 잘릴 일이 줄지만 더 일찍 끼어들고, 작으면 반대다. 말하는 동안 받아 적기(`/voice/stream`)는 `VOICE_SEGMENT_SECONDS` 기본 10(4~30)초가 쌓이면 그 직전 `VOICE_SEGMENT_SEARCH_SECONDS` 기본 4초 안에서 소리가 가장 작은 `VOICE_SEGMENT_PAUSE_MS` 기본 300ms 구간(말이 멈춘 곳)을 찾아 잘라 미리 받아 적는다. SECONDS가 작으면 녹음 끝에 남는 꼬리가 짧아 기다림이 줄지만 요청이 늘고(무료 요금제 요청 한도), 크면 반대다. PAUSE가 너무 짧으면 단어 사이 짧은 틈에서 잘릴 수 있다. 채용 건에 고정하지 않는 운영 설정이다.

격리 e2e override는 API에 FakeSpeech·FakeLLM·테스트 API를 강제한다. 새 음성 스키마는 기존 볼륨에 자동 적용되지 않으므로 테스트/개발 DB를 구분하고 DB 가이드를 따른다. 실제 Azure 설정이 없어도 Fake 기반 검증은 가능하지만 음질·인식 정확도 검증과 구분한다.

### Azure 서비스 계정 값 입력

Azure Portal → Microsoft Entra ID → 앱 등록 → 만든 앱의 개요에서 디렉터리(테넌트) ID를 `AZURE_TENANT_ID`, 애플리케이션(클라이언트) ID를 `AZURE_CLIENT_ID`에 넣는다. 인증서 및 암호 → 새 클라이언트 암호를 생성한 뒤 **값(Value)**을 `AZURE_CLIENT_SECRET`에 넣는다. 비밀 ID를 넣지 않는다. 생성 직후 값 복사가 필요하다([Microsoft 공식 절차](https://learn.microsoft.com/en-us/entra/identity-platform/howto-create-service-principal-portal)).

서비스 계정은 Speech F0 리소스 범위에서 `Microsoft.CognitiveServices/accounts/read`와 `Microsoft.CognitiveServices/accounts/listKeys/action`을 허용하는 사용자 지정 역할을 부여한다. 요금제 변경·생성·삭제 권한은 필요 없다. [사용자 지정 역할 작성](https://learn.microsoft.com/en-us/azure/role-based-access-control/custom-roles-portal), [작업 이름](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/ai-machine-learning)을 따른다. 계정 생성과 리소스 역할 할당은 별개다.

`AZURE_SPEECH_KEY`는 Speech 리소스의 키이며 서비스 계정 암호와 별개다. `AZURE_SPEECH_REGION`에는 리소스 위치 코드, `AZURE_SPEECH_RESOURCE_ID`에는 `/subscriptions/.../resourceGroups/.../providers/Microsoft.CognitiveServices/accounts/...` 형식 리소스 ID를 넣는다. `VOICE_QUOTA_PERIOD`는 기본 `auto`(UTC 달 단위)로 두며, 고정 기간 ID를 쓰면 그 ID를 바꿀 때 원장이 새로 시작된다. 실제 값은 로컬 `infra/.env`에만 입력한다.
