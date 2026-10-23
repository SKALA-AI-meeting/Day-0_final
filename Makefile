# 공통 타깃. 서비스 목록은 docs/architecture/deployment.md "Docker Compose"(8개 서비스)와 같다.
COMPOSE     = docker compose -f infra/docker-compose.yml --env-file infra/.env
COMPOSE_DEV = $(COMPOSE) -f infra/docker-compose.dev.yml
SERVICES    = services/api services/debate-mcp services/rag-mcp services/assessment-mcp services/report-worker
# 저장소 루트에서 도는 검사: 계약 픽스처(G-C)와 미션 카탈로그 정합성
ROOT_TESTS  = tests/test_contract_fixtures.py tests/test_docs_gen.py scenarios/missions/debate/tests
XFAIL       = tests/expected-failures.txt

# 기대 실패 목록에서 접두어(셸 변수 p) 아래 줄을 그 폴더 기준 경로로 꺼내 건너뛰기 인자로 만든다.
# 노드 id(::)는 FLAG_NODE, 파일·디렉터리는 FLAG_PATH. 주석·빈 줄은 무시한다.
XFAIL_ARGS = awk -F'|' -v p="$$p" -v fn="$$fn" -v fp="$$fp" '!/^[[:space:]]*(\#|$$)/ { \
	gsub(/[[:space:]]+/, "", $$1); if (index($$1, p) != 1) next; x = substr($$1, length(p) + 1); \
	if (index(x, "::")) { if (fn != "") print fn " " x } else print fp " " x }' $(XFAIL)

infra-up:        ## 개발용 인프라만, 로컬 포트 바인딩: postgres 5432, rabbitmq 5672, rag-mcp 9102
	$(COMPOSE_DEV) up -d postgres rabbitmq rag-mcp

infra-down:      ## 개발용 인프라 내림
	$(COMPOSE_DEV) down

up:              ## 전체 compose 8개 서비스(G-F 확인, 통합)
	$(COMPOSE) up -d --build --wait

down:            ## 전체 compose 내림
	$(COMPOSE) down

seed:            ## 자료 원문 시드 → 관리자·채용 건 → rag 재색인 (postgres 5432, api 8000, rag-mcp 9102)
	uv run --project services/api python scripts/seed.py

seed-compose:    ## 전체 compose(make up)에 시드. 포트를 열지 않고 compose 안 일회성 seed 서비스로 실행: make seed-compose [SEED_ARGS="--minutes 15"]
	$(COMPOSE) run --rm seed $(SEED_ARGS)

test:            ## 단위 테스트 전부(LLM mock, live 제외). 기대 실패 목록(tests/expected-failures.txt)의 테스트는 건너뛴다
	@p=""; fn=--deselect; fp=--ignore; skips=$$($(XFAIL_ARGS)); \
	  echo "== root $(ROOT_TESTS)"; uv run pytest -q -p no:cacheprovider $$skips $(ROOT_TESTS) || exit 1
	@for s in $(SERVICES); do \
	  if [ -f $$s/pyproject.toml ]; then \
	    p="$$s/"; fn=--deselect; fp=--ignore; skips=$$($(XFAIL_ARGS)); \
	    echo "== $$s $$skips"; \
	    (cd $$s && uv run pytest -q -m "not live" $$skips) || exit 1; \
	  fi; \
	done
	@if [ -f apps/web/package.json ]; then \
	  p="apps/web/"; fn=""; fp=--exclude; skips=$$($(XFAIL_ARGS)); \
	  echo "== apps/web $$skips"; (cd apps/web && pnpm test --silent $$skips) || exit 1; \
	fi

test-live:       ## 실제 LLM 호출 테스트만
	cd services/api && uv run pytest -q -m live

lint:            ## ruff(저장소 전체, scripts/docs_gen.py 포함)와 web eslint·prettier
	uv run ruff check . && uv run ruff format --check .
	@if [ -f apps/web/package.json ]; then (cd apps/web && pnpm lint) || exit 1; fi

e2e:             ## 실 스택 all_five 15분 1회(벽시계). 스택이 떠 있어야 한다. 다른 대본은 SCRIPT=timeout_all 등
	uv run --project services/api python tests/e2e/run.py --script $(or $(SCRIPT),all_five)

E2E_PYTEST = PYTHONPATH=services/api:. uv run --project services/api pytest -q -p no:cacheprovider \
	-o asyncio_mode=auto -o asyncio_default_fixture_loop_scope=session \
	-o asyncio_default_test_loop_scope=session -o markers="live: 실제 LLM 호출"

test-e2e:        ## e2e 러너·단언·장애 주입·인증 경계(가짜 api)와 eval 픽스처 형태 검사. 스택·DB·LLM 불필요
	$(E2E_PYTEST) -m "not live" tests/e2e tests/eval

eval-live:       ## eval 픽스처(의도 분류·BARS·유출 질문·페르소나·어시스턴트)를 실제 모델로 1회(결과는 tests/e2e/records/eval-live-<일시>.md)
	$(E2E_PYTEST) -m live tests/eval

virtual-candidate: ## 가상 지원자 15분 세션 1회(실 스택·실 모델): make virtual-candidate MODE=scripted|autonomous. 기록은 tests/e2e/records/virtual/
	uv run --project services/api python tests/eval/virtual_candidate/drive.py --mode $(or $(MODE),scripted)

e2e-auth:        ## 단언 8(인증 경계). 스택이 떠 있어야 한다
	uv run --project services/api python tests/e2e/run.py --auth

e2e-faults:      ## 단언 10(장애 주입 2종: 큐 DLQ·LLM timeout). 인프라만 떠 있으면 러너가 앱을 띄운다
	uv run --project services/api python tests/e2e/run.py --faults all

stage:           ## 단계 시드 세션 생성: make stage STAGE=keyword|problem|solution|proposal|ended [MIN=15] [LLM=fake|live]. api 내부 8001이 떠 있어야 한다
	uv run --project services/api python scripts/make_session.py --start-at $(STAGE) --minutes $(or $(MIN),15) --llm $(or $(LLM),fake)

stage-advance:   ## 시간 당기기: make stage-advance SID=<세션 id> TO=deadline|idle|card:<카드 id>|<초>
	uv run --project services/api python scripts/stage_advance.py --sid $(SID) --to $(TO)

E2E_COMPOSE = docker compose -p dayzero-debate -f infra/docker-compose.yml -f infra/docker-compose.e2e.yml
E2E_PORTS   = API_PORT=8100 WEB_PORT=3100 RABBITMQ_UI_PORT=15674

e2e-stack-up:    ## 단계별 화면 테스트용 토론형 스택(별도 프로젝트 dayzero-debate, web 3100·api 8100·내부 8101). infra/.env.e2e 필요
	$(E2E_PORTS) $(E2E_COMPOSE) up -d --build --wait

e2e-stack-seed:  ## 위 스택에 자료 원문·재색인(postgres 5442·rag 9112)
	SEED_DATABASE_URL=postgresql://dayzero:dayzero@localhost:5442/dayzero uv run --project services/api python scripts/seed.py --skip-position --rag http://localhost:9112/mcp --minutes 15

e2e-stack-down:  ## 위 스택 내림(볼륨 유지)
	$(E2E_PORTS) $(E2E_COMPOSE) down

e2e-stages:      ## 단계별 화면 테스트(Playwright): make e2e-stages [STAGE=entry|keyword|problem|solution|proposal|debrief|admin-report] [LLM=fake|live] (LLM은 스택 env가 정하며 이 변수는 경고만 출력)
	@[ "$(LLM)" != "live" ] || echo "LLM=live: 결정적 LLM 문장에 맞춘 단언은 실제 모델에서 달라질 수 있다"
	cd apps/web && WEB_URL=$${WEB_URL:-http://localhost:3100} E2E_INTERNAL_URL=$${E2E_INTERNAL_URL:-http://localhost:8101} pnpm exec playwright test $(STAGE)

docs-gen:        ## 서비스 README의 생성 블록(API·MCP 도구·테스트 목록) 갱신
	uv run --project services/api python scripts/docs_gen.py

docs-check:      ## 생성 블록이 코드와 같은지, docs/features.md의 테스트 참조가 실제로 있는지 검사(스택·DB 불필요)
	uv run --project services/api python scripts/docs_gen.py --check --features

help:            ## 타깃 목록
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{ printf "%-16s %s\n", $$1, $$2 }'

.PHONY: help docs-gen docs-check infra-up infra-down up down seed seed-compose test test-live lint e2e test-e2e eval-live virtual-candidate e2e-auth e2e-faults stage stage-advance e2e-stack-up e2e-stack-seed e2e-stack-down e2e-stages
