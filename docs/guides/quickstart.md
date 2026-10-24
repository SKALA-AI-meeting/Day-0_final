# 처음 실행하기

처음 저장소를 받은 사람이 테스트를 돌리고, 스택을 띄워 지원자 화면을 열어 보기까지의 순서다. 실행 모드별 포트·DB·env 차이는 [configuration.md](configuration.md), 테스트 명령의 범위는 [testing.md](testing.md).

## 1. 필요한 도구

| 도구 | 버전·비고 |
|---|---|
| Python | 3.12, [`uv`](https://docs.astral.sh/uv/)로 가상환경과 lockfile을 다룬다(서비스마다 `pyproject.toml`) |
| Node | 22, `pnpm`(`corepack enable`) |
| Docker | Docker Compose v2. 격리 e2e 스택은 `!override` 태그를 쓰므로 2.24.4 이상 |
| libpq | macOS는 `brew install libpq && brew link --force libpq`(`psql`·드라이버 빌드용) |

## 2. 받기와 의존성

```
git clone <저장소 주소> && cd Day-0_zaeho
for s in services/api services/debate-mcp services/rag-mcp services/assessment-mcp services/report-worker; do (cd $s && uv sync); done
(cd apps/web && pnpm install)
make test && make lint
```

`make test`는 postgres가 없으면 api의 DB 테스트를 건너뛴다(skip). DB까지 확인하려면 아래 3의 `make up SCOPE=infra` 뒤에 다시 돌린다.

## 3. env 준비

```
cp infra/.env.example infra/.env
```

- `OPENAI_API_KEY`(실제 모델을 쓸 때), `JWT_SECRET`(32바이트 이상 임의 값), `INTERNAL_TOKEN`, `ASSESSMENT_MCP_TOKEN`(임의 문자열)을 채운다. 키 값은 이 파일에만 두고 커밋하지 않는다.
- LLM 없이 결정적으로 돌리려면 `LLM_FAKE=1`. 단계 시드·시간 당기기를 쓰려면 `ENABLE_TEST_API=1`(예시 값 그대로).
- 앱을 로컬에서 띄울 때는 `infra/.env`를 복사한 `infra/.env.local`을 만들고 서비스 주소를 `localhost`로 바꾼다(바꿀 항목은 [configuration.md](configuration.md) "로컬 실행용 `infra/.env.local`"). Python 서비스는 `.env.local`을 먼저 읽는다.

## 4. 띄우기 (로컬 개발 모드)

앱을 고치면서 볼 때의 기본 방식이다. 인프라와 무거운 rag-mcp만 컨테이너로 띄우고 나머지는 로컬에서 띄운다.

```
make up SCOPE=infra                                              # postgres 5432, rabbitmq 5672, rag-mcp 9102 (127.0.0.1). 프로필 dev-fake
cd services/api && uv run python -m app.run --reload             # 공개 8000 + 내부 8001 + 스케줄러(한 프로세스)
cd services/debate-mcp && uv run python -m debate_mcp            # 9101
cd services/assessment-mcp && uv run python -m assessment_mcp    # 9103
cd services/report-worker && uv run python -m report_worker
cd apps/web && pnpm dev                                          # 3000
make seed SCOPE=infra                                            # 자료 원문 시드 → 관리자·채용 건 → rag 재색인
```

- rag-mcp는 첫 기동 때 BGE-M3 모델(약 2.3GB)을 받는다. healthy가 될 때까지 몇 분 걸릴 수 있다.
- `make seed`는 postgres 5432·api 8000·rag-mcp 9102에 붙는다. 관리자 계정 `admin@dayzero.example`(회사 "데모 회사")과 채용 건을 만든다. 비밀번호 기본값과 바꾸는 옵션은 `scripts/seed.py`의 `--email`·`--password`.
- 각 명령은 저장소 루트 기준 경로다. 터미널을 나눠 띄운다.

## 5. 지원자 화면 열기

```
make stage STAGE=keyword MIN=15        # 단계 시드 세션을 만들고 초대 링크·세션 id를 출력
```

출력된 링크를 브라우저로 열면 그 단계부터 진행된다. 시간 초과·침묵 초대·카드를 기다리지 않고 보려면 `make stage-advance SID=<세션 id> TO=deadline|idle|card:<카드 id>`. 관리자 화면은 `http://localhost:3000/admin/login`.

계정은 둘로 나뉜다. `make seed`가 만든 `admin@dayzero.example` 회사와 `make stage`(테스트 API)가 만드는 세션의 회사는 다르다. 단계 시드 세션은 별도 기업의 관리자 `e2e@example.com`이 소유하므로(기업 간 격리), 그 세션의 리포트(`STAGE=ended`가 출력하는 `admin_report_url`)는 `e2e@example.com`으로 로그인해 본다. 이 계정의 비밀번호는 `services/api/app/services/test_sessions.py`의 `SEED_PASSWORD`, 다른 관리자로 만들려면 `scripts/make_session.py --admin-email`.

## 6. 정상 확인과 종료

- api `curl -fs http://localhost:8000/health`, web `http://localhost:3000/api/health`.
- 세션이 끝나면(`make stage STAGE=ended` 또는 제출) report-worker가 리포트 초안을 만든다. 관리자 지원자 목록에서 상태가 "검토 필요"로 바뀌면 정상이다.
- 끝낼 때는 로컬 앱을 멈추고 `make down SCOPE=infra`. 볼륨(DB 데이터·모델 캐시)은 남는다.

## 7. 실제 LLM으로 돌고 있는지 확인하기

LLM 없이 도는 모드가 세 가지 있고, 화면에 `[fake]` 표시가 붙는 것은 그중 하나뿐이다. `[fake]`가 안 보여도 실제 LLM이 아닐 수 있으니, 피드백을 남기기 전에 확인한다.

| 설정 | 무엇이 도나 | 채팅 모습 | 리포트 |
|---|---|---|---|
| api `AGENT_RUNNER=fake` | 가짜 러너. 토론 그래프가 돌지 않는다 | `[fake] 네, 그 의견 잘 들었어요.` 같은 고정 문장 | — |
| api·report-worker `LLM_FAKE=1` | 토론 그래프·의도 분류·평가는 그대로 돌고 LLM 호출만 고정 응답 | **`[fake]` 없이** 자연스러운 문장이지만, 같은 자리가 단계마다 같은 입장 문장을 반복 | 지원자 메시지가 있으면 모든 차원이 첫 지원자 메시지를 근거로 수준 3, 없으면 기록 없음 |
| web `NEXT_PUBLIC_MOCK=1`(로컬 `pnpm dev`만) | api 없이 `apps/web/mocks/`의 예시 데이터 | 예시 대화가 그대로 보이고 입력에 맞춰 바뀌지 않음 | 예시 리포트 |
| `AGENT_RUNNER=session`, `LLM_FAKE`가 없거나 `0`, mock 아님 | 실제 LLM | 지원자 말에 맞춰 대답 | 실제 평가 |

`LLM_FAKE`는 api와 report-worker가 각각 읽는다. 둘이 다르면 토론과 리포트 중 한쪽만 고정 응답이 된다. 격리 e2e 스택(web `localhost:3100`, `infra/.env.e2e`)은 단계 테스트용이라 기본이 `LLM_FAKE=1`이다.

### 설정 확인

전체 compose(`make up`)로 띄웠을 때:

```
docker compose -f infra/docker-compose.yml --env-file infra/.env exec -T api env | grep -E '^(AGENT_RUNNER|LLM_FAKE)='
docker compose -f infra/docker-compose.yml --env-file infra/.env exec -T report-worker env | grep -E '^LLM_FAKE='
```

`AGENT_RUNNER=session`이 나오고 `LLM_FAKE=1`이 없으면 설정은 실제 LLM이다(`LLM_FAKE` 줄 자체가 안 나와도 된다). web 컨테이너는 mock 없이 빌드된다.

로컬 개발 모드(호스트에서 앱 실행)는 이미 설정된 프로세스 env > `infra/.env.local` > `infra/.env` 순으로 읽으므로 두 파일과 셸 env를 모두 본다.

```
grep -E '^(AGENT_RUNNER|LLM_FAKE)=' infra/.env.local infra/.env
env | grep -E '^(AGENT_RUNNER|LLM_FAKE|NEXT_PUBLIC_MOCK)='
```

### 실제 호출 확인

설정이 맞아도 키가 없으면 실제 호출은 일어나지 않는다. 모델에 맞는 키(gpt-4o-mini면 `OPENAI_API_KEY`)가 비어 있으면 LLM을 만드는 단계에서 설정 오류로 멈추고, 실행 중 호출이 실패하면 그 발언은 생략된다(연속 3회면 세션 실패). 세션을 하나 진행한 뒤 기록으로 확인한다. 고정 응답 모드는 토큰을 0으로 기록한다.

```
# 토론: 세션별 토론 그래프 출력 토큰 합. 0보다 크면 토론 중 실제 LLM 호출이 있었다
docker compose -f infra/docker-compose.yml --env-file infra/.env exec -T postgres psql -U dayzero -d dayzero -c \
  "SELECT s.id, s.status, COALESCE(SUM((r.usage->>'tokens_out')::int),0) AS debate_tokens_out
   FROM sessions s LEFT JOIN agent_runs r ON r.session_id = s.id AND r.graph = 'debate'
   WHERE s.started_at IS NOT NULL GROUP BY s.id ORDER BY max(s.started_at) DESC LIMIT 3;"

# 평가: 리포트 토큰과 평가 모델. model이 fake가 아니고 tokens_out이 0보다 크면 실제 평가다
docker compose -f infra/docker-compose.yml --env-file infra/.env exec -T postgres psql -U dayzero -d dayzero -c \
  "SELECT r.session_id, r.usage->>'tokens_out' AS tokens_out, e.model AS eval_model
   FROM reports r LEFT JOIN evaluation_runs e ON e.session_id = r.session_id AND e.run_id = r.eval_run_id
   ORDER BY r.generated_at DESC LIMIT 3;"
```

토큰 합은 의도 분류·턴·AI 동료 발언을 합친 값이라 "토론 중 실제 호출이 있었는지"만 알려 준다. `llm_calls`는 고정 응답 모드에서도 늘어나므로 판단에 쓰지 않는다.

### 실제 LLM으로 바꾸기

1. `infra/.env`에서 `AGENT_RUNNER=session`으로 두고 `LLM_FAKE=0`으로 바꾸거나 그 줄을 지운다. `OPENAI_API_KEY`가 채워져 있는지 본다.
2. 컨테이너를 다시 만든다. env는 시작할 때만 읽으므로 파일만 고치면 반영되지 않는다.
   ```
   docker compose -f infra/docker-compose.yml --env-file infra/.env up -d --wait api report-worker
   ```
3. 위 "설정 확인"으로 다시 본다.
4. **새 초대 링크로 새 세션을 시작한다.** 고정 응답으로 진행한 대화·입장 기록이 남아 있어 이어서 하면 섞인다.

### 실제 LLM이어도 나오는 고정 문장

- "자료부터 같이 보고 얘기해요.": 문제 상황 단계의 카드 전에 AI 동료가 만든 문장이 정해진 입장과 다를 때 쓰는 대체 문장
- "시간이 다 돼서 …로 할게요": 시간 초과 때 코드가 만드는 결정 안내

피드백을 남길 때는 위 확인 결과(`AGENT_RUNNER`·`LLM_FAKE` 값과 토큰 확인 결과)를 함께 적는다.

## 다른 실행 방식

- 8개 서비스를 모두 컨테이너로: `make up PROFILE=dev-fake` / `make down`(기본 프로필은 가짜 LLM·테스트 API. 실제 LLM은 `PROFILE=dev-real ALLOW_API=1`). 이 모드는 postgres·rag-mcp를 호스트에 열지 않으므로 `make seed PROFILE=dev-fake [SEED_ARGS="--minutes 15"]`가 compose 안 일회성 seed 서비스로 돈다([configuration.md](configuration.md) "실행 프로필"·"실행 모드").
- 단계별 화면 테스트용 격리 스택: `make up PROFILE=test-text` → `make seed PROFILE=test-text` → `make e2e SUITE=stages PROFILE=test-text` → `make down PROFILE=test-text`([testing.md](testing.md)). 현재 모드 확인은 `make profile PROFILE=…`.
