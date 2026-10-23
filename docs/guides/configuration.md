# 설정과 실행 모드

env 이름과 기본값의 예시는 `infra/.env.example`, 코드의 기본값은 각 서비스 `config.py`가 정본이다. 이 문서는 실행 모드마다 무엇이 어디에 붙는지와 env가 어떤 순서로 읽히는지를 적는다.

## 실행 모드

| | 로컬 개발 | 전체 compose | 격리 e2e 스택 |
|---|---|---|---|
| 명령 | `make infra-up` + 앱 로컬 실행([quickstart.md](quickstart.md)) | `make up` / `make down` | `make e2e-stack-up` / `make e2e-stack-down` |
| compose 파일 | `docker-compose.yml` + `docker-compose.dev.yml` | `docker-compose.yml` | `docker-compose.yml` + `docker-compose.e2e.yml`, 프로젝트 `dayzero-debate` |
| 컨테이너 | postgres·rabbitmq·rag-mcp | 8개 전부 | 8개 전부 |
| env 파일 | 컨테이너 `infra/.env`, 로컬 앱 `infra/.env.local` → `infra/.env` | `infra/.env` | `infra/.env.e2e` |
| web | `localhost:3000`(`pnpm dev`) | `localhost:3000` | `localhost:3100` |
| api 공개 | `localhost:8000` | `localhost:8000`(`API_PORT`) | `localhost:8100` |
| api 내부(8001) | `localhost:8001`(로컬 프로세스) | 컨테이너 네트워크만 | `127.0.0.1:8101` |
| postgres(호스트) | `127.0.0.1:5432` | 열지 않음 | `127.0.0.1:5442` |
| rabbitmq | `127.0.0.1:5672`, 관리 UI `127.0.0.1:15672` | 관리 UI `127.0.0.1:15672`만 | 관리 UI `127.0.0.1:15674`만 |
| rag-mcp(호스트) | `127.0.0.1:9102` | 열지 않음 | `127.0.0.1:9112` |
| debate·assessment MCP | 로컬 9101·9103 | 컨테이너 네트워크만 | 컨테이너 네트워크만 |
| rag-mcp가 부르는 내부 API | `http://host.docker.internal:8001`(로컬 api) | `http://api:8001` | `http://api:8001` |
| 시드 | `make seed` | 호스트의 `make seed`는 닿지 않음. `make seed-compose`(compose 안 일회성 seed 서비스) | `make e2e-stack-seed` |
| 테스트 API | `ENABLE_TEST_API=1`이면 `localhost:8001/internal/test/*` | 컨테이너 안에서만 | `127.0.0.1:8101/internal/test/*` |

- **같은 DB·큐를 쓰는 api는 하나만 띄운다.** 세션 메일박스·WS 송신 큐·스케줄러가 api 프로세스 메모리에 있어 둘이면 같은 세션을 두 번 처리한다. 다른 DB·큐를 쓰는 격리 스택(위 e2e 스택)은 함께 띄워도 된다.
- 로컬 개발 모드와 전체 compose는 호스트 포트 3000·8000을 함께 쓰므로 동시에 띄우지 않는다. 같은 기계에서 두 번째 스택이 필요하면 격리 e2e 스택을 쓰거나 `API_PORT`·`WEB_PORT`·`RABBITMQ_UI_PORT`와 프로젝트 이름(`-p`)을 바꾼다.
- 전체 compose 모드는 postgres·rag-mcp를 호스트에 열지 않아 호스트에서 `scripts/seed.py`가 닿지 않는다. 이 모드의 시드는 `Makefile`의 compose용 시드 타깃을 쓴다.

## 로컬 실행용 `infra/.env.local`

`infra/.env.example`의 서비스 주소는 compose 호스트명(`postgres`·`rabbitmq`·`api`·`debate-mcp` 등)이라 호스트에서 띄운 Python 서비스는 닿지 않는다. 로컬 개발 모드(`make infra-up` + 호스트에서 앱 실행)에서는 `cp infra/.env infra/.env.local` 뒤 아래 항목만 바꾼다. 로컬 Python 서비스는 `.env.local`을 먼저 읽고, 컨테이너는 이 파일을 읽지 않는다.

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
| compose 컨테이너 | `docker-compose*.yml`의 `environment:`(서비스 주소) > `env_file`(`infra/.env` 또는 `infra/.env.e2e`) |
| 로컬 Python 서비스(api·MCP 3개·report-worker) | 이미 설정된 프로세스 env > `infra/.env.local` > `infra/.env`(`python-dotenv`, 덮어쓰지 않음) |
| 로컬 web(`pnpm dev`) | 프로세스 env와 `apps/web/.env*`(Next.js 규칙). `infra/.env`는 읽지 않는다 |
| web 컨테이너 | `NEXT_PUBLIC_*`는 **이미지 빌드 시점**의 build arg(`apps/web/Dockerfile`). 컨테이너 env를 바꿔도 번들은 바뀌지 않으므로 다시 빌드한다. 값이 없으면 `http://localhost:8000`·`ws://localhost:8000` |
| `scripts/seed.py` | `--database-url`(기본 env `SEED_DATABASE_URL`, 없으면 `localhost:5432`), `--api`(기본 8000), `--rag`(기본 9102) |
| api DB 테스트 | `TEST_DATABASE_URL`(기본 `localhost:5432`). 없거나 닿지 않으면 skip |

## env 목록

| 이름 | 읽는 곳 | 뜻·기본값 |
|---|---|---|
| `OPENAI_API_KEY`, `OPENAI_API_KEY_PERSONAL`, `GEMINI_API_KEY` | api·report-worker LLM 래퍼 | 모델 id로 키를 고른다(gpt-4o-mini → `OPENAI_API_KEY`, 그 밖 gpt-* → 개인 키, gemini-* → Gemini 키). 비어 있으면 설정 오류 |
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
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` | api·report-worker | LangSmith trace |
| `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`, `NEXT_PUBLIC_MOCK` | web(빌드 시점) | api·WS 주소, `1`이면 mock 모드 |
| `API_PORT`, `WEB_PORT`, `RABBITMQ_UI_PORT` | compose | 공개 포트 번호 |
| `SEED_DATABASE_URL`, `TEST_DATABASE_URL` | `scripts/seed.py`, api 테스트 | 위 "env를 읽는 순서" |
| `WEB_URL`, `E2E_INTERNAL_URL`, `E2E_DATABASE_URL`, `E2E_RESTART_CMD` | Playwright·e2e 러너 | 대상 스택 주소, `restart` 대본의 재시작 명령 |
| `PLAYTEST_PORT` | playtest 드라이버 | 드라이버 서버 포트, 기본 9400 |

`.env.example`에 없는 키를 코드에 더하면 `.env.example`과 이 표를 함께 고친다.


## 음성 모드

기본 `VOICE_PROVIDER=azure`는 F0만 허용한다. `AZURE_SPEECH_KEY`·`AZURE_SPEECH_REGION`·`AZURE_SPEECH_RESOURCE_ID`와 DefaultAzureCredential용 관리 ID 또는 서비스 계정 설정은 `.env`에만 둔다. F0 자원 조회·listKeys 권한이 필요하며 요금제 변경 권한은 주지 않는다. `VOICE_QUOTA_PERIOD`는 공급자 잔여 무료량·리셋 경계를 확인한 기간 ID다. 값이 없으면 음성 호출이 차단된다. `VOICE_USER_TURN_SECONDS` 기본 60(1~60)은 새 채용 건에 고정된다. `VOICE_THINK_SECONDS` 기본 5(0~60)은 강제 차례에서 녹음이 자동으로 시작되기까지의 대기 시간이고, 새 채용 건에 고정된다. `VOICE_TURN_HOLD_SECONDS` 기본 20(5~120)은 일반 내 차례를 지켜 주는 시간이다. 일반 차례는 자동 녹음 없이 지원자가 [발언하기]를 눌러 시작하고, 이 시간이 지나면 동료가 이어 말한다(손들기로 다시 잡을 수 있음). 크면 지원자가 생각할 시간이 늘지만 침묵이 길어지고, 작으면 반대다. 새 채용 건에 고정된다. `VOICE_CONFIRM_SECONDS` 기본 3(0~30)은 받아 적은 글을 보여 주고 자동 제출하기까지의 확인 시간이고, 새 채용 건에 고정된다. 크면 다시 녹음할지 판단할 시간이 늘지만 대화가 늦어지고, 0이면 예전처럼 바로 제출한다. `VOICE_FORCED_WAIT_SECONDS` 기본 30(0~120)은 강제 차례(그 단계에서 아직 말하지 않은 지원자)가 지금 동료 발언이 끝나기를 기다릴 몫이고, 새 채용 건에 고정된다. 강제 차례는 단계 마감 "이 값 + 생각 시간 + 발언 한도 + 10초" 전에 온다. 크면 60초 발언이 마감에 잘릴 일이 줄지만 더 일찍 끼어들고, 작으면 반대다. 말하는 동안 받아 적기(`/voice/stream`)는 `VOICE_SEGMENT_SECONDS` 기본 10(4~30)초가 쌓이면 그 직전 `VOICE_SEGMENT_SEARCH_SECONDS` 기본 4초 안에서 소리가 가장 작은 `VOICE_SEGMENT_PAUSE_MS` 기본 300ms 구간(말이 멈춘 곳)을 찾아 잘라 미리 받아 적는다. SECONDS가 작으면 녹음 끝에 남는 꼬리가 짧아 기다림이 줄지만 요청이 늘고(무료 요금제 요청 한도), 크면 반대다. PAUSE가 너무 짧으면 단어 사이 짧은 틈에서 잘릴 수 있다. 채용 건에 고정하지 않는 운영 설정이다.

격리 e2e override는 API에 FakeSpeech·FakeLLM·테스트 API를 강제한다. 새 음성 스키마는 기존 볼륨에 자동 적용되지 않으므로 테스트/개발 DB를 구분하고 DB 가이드를 따른다. 실제 Azure 설정이 없어도 Fake 기반 검증은 가능하지만 음질·인식 정확도 검증과 구분한다.

### Azure 서비스 계정 값 입력

Azure Portal → Microsoft Entra ID → 앱 등록 → 만든 앱의 개요에서 디렉터리(테넌트) ID를 `AZURE_TENANT_ID`, 애플리케이션(클라이언트) ID를 `AZURE_CLIENT_ID`에 넣는다. 인증서 및 암호 → 새 클라이언트 암호를 생성한 뒤 **값(Value)**을 `AZURE_CLIENT_SECRET`에 넣는다. 비밀 ID를 넣지 않는다. 생성 직후 값 복사가 필요하다([Microsoft 공식 절차](https://learn.microsoft.com/en-us/entra/identity-platform/howto-create-service-principal-portal)).

서비스 계정은 Speech F0 리소스 범위에서 `Microsoft.CognitiveServices/accounts/read`와 `Microsoft.CognitiveServices/accounts/listKeys/action`을 허용하는 사용자 지정 역할을 부여한다. 요금제 변경·생성·삭제 권한은 필요 없다. [사용자 지정 역할 작성](https://learn.microsoft.com/en-us/azure/role-based-access-control/custom-roles-portal), [작업 이름](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/ai-machine-learning)을 따른다. 계정 생성과 리소스 역할 할당은 별개다.

`AZURE_SPEECH_KEY`는 Speech 리소스의 키이며 서비스 계정 암호와 별개다. `AZURE_SPEECH_REGION`에는 리소스 위치 코드, `AZURE_SPEECH_RESOURCE_ID`에는 `/subscriptions/.../resourceGroups/.../providers/Microsoft.CognitiveServices/accounts/...` 형식 리소스 ID를 넣는다. `VOICE_QUOTA_PERIOD`는 운영자가 무료 잔여량·갱신 경계를 확인한 기간 이름으로 지정한다. 예시 값으로 원장을 임의 초기화하지 않는다. 실제 값은 로컬 `infra/.env`에만 입력한다.
