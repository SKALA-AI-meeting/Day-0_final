# 공통 타깃. 실행 모드는 PROFILE(infra/profiles/*.env, 기본 dev-fake)이 정하고 scripts/profile.py가 해석한다.
# 비용 표시: [API 없음] = 외부 호출 없음, [제한키·과금] = 실제 LLM 호출이라 ALLOW_API=1을 명시해야 돈다.
# 서비스 목록은 docs/architecture/deployment.md "Docker Compose"(8개 서비스)와 같다.
PROFILE    ?= dev-fake
SUITE      ?=
SCOPE      ?=
PROF        = python3 scripts/profile.py
# 프로필의 비밀 아닌 값 하나(레시피 안에서만 평가된다)
pget        = $(shell $(PROF) get $(PROFILE) $(1))
EXEC        = $(PROF) exec $(PROFILE)
ENV_FILE    = infra/$(call pget,ENV_FILE)
COMPOSE     = DAYZERO_ENV_FILE=$(call pget,ENV_FILE) docker compose -p $(call pget,COMPOSE_PROJECT) \
	--env-file $(ENV_FILE) $(call pget,COMPOSE_FILES)
COMPOSE_INFRA = $(COMPOSE) -f infra/docker-compose.dev.yml
INFRA_SERVICES = postgres rabbitmq rag-mcp
export ALLOW_API

SERVICES    = services/api services/debate-mcp services/rag-mcp services/assessment-mcp services/report-worker
# 저장소 루트에서 도는 검사: 계약 픽스처(G-C)·미션 카탈로그 정합성·프로필 해석기
ROOT_TESTS  = tests/test_contract_fixtures.py tests/test_docs_gen.py tests/test_profile.py scenarios/missions/debate/tests
XFAIL       = tests/expected-failures.txt

# 기대 실패 목록에서 접두어(셸 변수 p) 아래 줄을 그 폴더 기준 경로로 꺼내 건너뛰기 인자로 만든다.
# 노드 id(::)는 FLAG_NODE, 파일·디렉터리는 FLAG_PATH. 주석·빈 줄은 무시한다.
XFAIL_ARGS = awk -F'|' -v p="$$p" -v fn="$$fn" -v fp="$$fp" '!/^[[:space:]]*(\#|$$)/ { \
	gsub(/[[:space:]]+/, "", $$1); if (index($$1, p) != 1) next; x = substr($$1, length(p) + 1); \
	if (index(x, "::")) { if (fn != "") print fn " " x } else print fp " " x }' $(XFAIL)

E2E_PYTEST = PYTHONPATH=services/api:. uv run --project services/api pytest -q -p no:cacheprovider \
	-o asyncio_mode=auto -o asyncio_default_fixture_loop_scope=session \
	-o asyncio_default_test_loop_scope=session -o markers="live: 실제 LLM 호출"

# ---- 프로필 --------------------------------------------------------------------------------

profile:         ## [API 없음] 유효 모드 출력: make profile PROFILE=dev-fake|test-text|eval-text|dev-real|deploy
	@$(PROF) show $(PROFILE)

doctor:          ## [API 없음] 유효 모드 출력 + 조합·키 존재 검사(외부 호출 없음): make doctor PROFILE=…
	@$(PROF) doctor $(PROFILE)

# ---- 스택 ----------------------------------------------------------------------------------

up:              ## [프로필 따라] 스택 기동: make up PROFILE=… [SCOPE=infra](postgres·rabbitmq·rag-mcp만, 앱은 로컬). 실제 LLM 프로필은 ALLOW_API=1 필요
	@$(PROF) render $(PROFILE) --starting >/dev/null
ifeq ($(SCOPE),infra)
	$(COMPOSE_INFRA) up -d $(INFRA_SERVICES)
else
	$(COMPOSE) up -d --build --wait
endif

down:            ## [API 없음] 스택 내림(키·동의 불필요): make down PROFILE=… [SCOPE=infra]
	@$(PROF) render $(PROFILE) >/dev/null
ifeq ($(SCOPE),infra)
	$(COMPOSE_INFRA) down
else
	$(COMPOSE) down
endif

seed:            ## [API 없음] 자료 원문 시드 → 관리자·채용 건 → rag 재색인. 전체 compose는 compose 안 일회성 seed, 격리 스택·SCOPE=infra는 호스트에서: make seed PROFILE=… [SEED_ARGS="--minutes 15"]
	@if [ "$(SCOPE)" = infra ]; then \
	  echo "$(EXEC) -- uv run --project services/api python scripts/seed.py $(SEED_ARGS)"; \
	  $(EXEC) -- uv run --project services/api python scripts/seed.py $(SEED_ARGS); \
	elif [ -z "$(call pget,SEED_DATABASE_URL)" ]; then \
	  $(PROF) render $(PROFILE) >/dev/null && echo "$(COMPOSE) run --rm seed $(SEED_ARGS)" && $(COMPOSE) run --rm seed $(SEED_ARGS); \
	else \
	  echo "$(EXEC) -- uv run --project services/api python scripts/seed.py --api $(call pget,PUBLIC_URL) --rag $(call pget,RAG_URL) $(SEED_ARGS)"; \
	  $(EXEC) -- uv run --project services/api python scripts/seed.py --api $(call pget,PUBLIC_URL) --rag $(call pget,RAG_URL) $(SEED_ARGS); \
	fi

# ---- 테스트 --------------------------------------------------------------------------------

test:            ## [API 없음] make test [SUITE=unit|tools|all](기본 all). unit: 루트 검사·서비스 5개(LLM mock, live 제외)·web. tools: e2e 러너·eval 픽스처 형태(가짜 api). 기대 실패 목록(tests/expected-failures.txt)은 건너뛴다
ifneq ($(filter $(or $(SUITE),all),unit all),)
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
endif
ifneq ($(filter $(or $(SUITE),all),tools all),)
	@echo "== tools tests/e2e tests/eval (가짜 api, 스택·DB·LLM 불필요)"
	$(E2E_PYTEST) -m "not live" tests/e2e tests/eval
endif

lint:            ## [API 없음] ruff(저장소 전체, scripts 포함)와 web eslint·prettier
	uv run ruff check . && uv run ruff format --check .
	@if [ -f apps/web/package.json ]; then (cd apps/web && pnpm lint) || exit 1; fi

eval:            ## [제한키·과금] 실제 모델 평가: make eval PROFILE=eval-text SUITE=api|fixtures|persona ALLOW_API=1. api: services/api live. fixtures(기본): tests/eval live 전체(결과 tests/e2e/records/eval-live-<일시>.md). persona: 페르소나 품질만(귀속 판정은 PERSONA_EVAL_JUDGE=1 VC_JUDGE_MODEL=…)
ifeq ($(or $(SUITE),fixtures),api)
	$(EXEC) --starting --need real -- sh -c 'cd services/api && uv run pytest -q -m live'
else ifeq ($(or $(SUITE),fixtures),persona)
	$(EXEC) --starting --need real -- $(E2E_PYTEST) -m live tests/eval/test_persona_eval.py
else
	$(EXEC) --starting --need real -- $(E2E_PYTEST) -m live tests/eval
endif

measure:         ## [제한키·과금] 가상 지원자 15분 세션 1회(실 스택·실 모델): make measure PROFILE=eval-text MODE=scripted|autonomous ALLOW_API=1. 기록 tests/e2e/records/virtual/. 판정은 별도(docs/guides/testing.md "귀속 판정")
	$(EXEC) --starting --need real -- uv run --project services/api python tests/eval/virtual_candidate/drive.py --mode $(or $(MODE),scripted)

e2e:             ## [프로필 따라] 실 스택 e2e: make e2e SUITE=flow|auth|faults|stages PROFILE=… flow(기본): 대본 1회(SCRIPT=all_five). auth: 단언 8. faults: 장애 주입(가짜 LLM 전용, 인프라만). stages: Playwright 단계별 화면(STAGE=entry|…; test-text 권장)
ifeq ($(or $(SUITE),flow),auth)
	$(EXEC) --starting -- uv run --project services/api python tests/e2e/run.py --auth
else ifeq ($(or $(SUITE),flow),faults)
	$(EXEC) --starting --need fake -- uv run --project services/api python tests/e2e/run.py --faults all
else ifeq ($(or $(SUITE),flow),stages)
	@[ "$(call pget,LLM_MODE)" != "live" ] || echo "실제 LLM 프로필: 결정적 LLM 문장에 맞춘 단언은 실제 모델에서 달라질 수 있다"
	$(EXEC) --starting -- sh -c 'cd apps/web && pnpm exec playwright test $(STAGE)'
else
	$(EXEC) --starting -- uv run --project services/api python tests/e2e/run.py --script $(or $(SCRIPT),all_five)
endif

stage:           ## [API 없음] 단계 시드 세션 생성: make stage STAGE=keyword|problem|solution|proposal|ended [MIN=15] PROFILE=…(스택이 떠 있어야 한다. LLM 모드는 프로필이 정한다)
	$(EXEC) -- uv run --project services/api python scripts/make_session.py --start-at $(STAGE) --minutes $(or $(MIN),15) --llm $(call pget,LLM_MODE)

stage-advance:   ## [API 없음] 시간 당기기: make stage-advance SID=<세션 id> TO=deadline|idle|card:<카드 id>|<초> PROFILE=…
	$(EXEC) -- uv run --project services/api python scripts/stage_advance.py --sid $(SID) --to $(TO)

# ---- 문서 ----------------------------------------------------------------------------------

docs-gen:        ## [API 없음] 서비스 README의 생성 블록(API·MCP 도구·테스트 목록) 갱신
	uv run --project services/api python scripts/docs_gen.py

docs-check:      ## [API 없음] 생성 블록이 코드와 같은지, docs/features.md의 테스트 참조가 실제로 있는지 검사(스택·DB 불필요)
	uv run --project services/api python scripts/docs_gen.py --check --features

help:            ## 타깃 목록(옛 이름은 아래 별칭 절)
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{ printf "%-16s %s\n", $$1, $$2 }'

# ---- 옛 이름 별칭(문서·습관 호환. 새 이름을 안내하고 그대로 실행한다) -------------------------

# 실제 LLM이 필요한 옛 타깃은 PROFILE을 주지 않았으면 eval-text로 돌린다
REAL_PROFILE = $(if $(filter dev-fake,$(PROFILE)),eval-text,$(PROFILE))
define deprecated
	@echo "[deprecated] make $(1) → make $(2)" >&2
	@$(MAKE) --no-print-directory $(2)
endef

infra-up:        ## (별칭) → up SCOPE=infra
	$(call deprecated,infra-up,up SCOPE=infra PROFILE=$(PROFILE))
infra-down:      ## (별칭) → down SCOPE=infra
	$(call deprecated,infra-down,down SCOPE=infra PROFILE=$(PROFILE))
seed-compose:    ## (별칭) → seed
	$(call deprecated,seed-compose,seed PROFILE=$(PROFILE) SEED_ARGS="$(SEED_ARGS)")
test-live:       ## (별칭) → eval SUITE=api
	$(call deprecated,test-live,eval SUITE=api PROFILE=$(REAL_PROFILE))
test-e2e:        ## (별칭) → test SUITE=tools
	$(call deprecated,test-e2e,test SUITE=tools)
eval-live:       ## (별칭) → eval SUITE=fixtures
	$(call deprecated,eval-live,eval SUITE=fixtures PROFILE=$(REAL_PROFILE))
virtual-candidate: ## (별칭) → measure
	$(call deprecated,virtual-candidate,measure MODE=$(or $(MODE),scripted) PROFILE=$(REAL_PROFILE))
e2e-auth:        ## (별칭) → e2e SUITE=auth
	$(call deprecated,e2e-auth,e2e SUITE=auth PROFILE=$(PROFILE))
e2e-faults:      ## (별칭) → e2e SUITE=faults
	$(call deprecated,e2e-faults,e2e SUITE=faults PROFILE=$(PROFILE))
e2e-stack-up:    ## (별칭) → up PROFILE=test-text
	$(call deprecated,e2e-stack-up,up PROFILE=test-text)
e2e-stack-seed:  ## (별칭) → seed PROFILE=test-text
	$(call deprecated,e2e-stack-seed,seed PROFILE=test-text SEED_ARGS="--minutes 15")
e2e-stack-down:  ## (별칭) → down PROFILE=test-text
	$(call deprecated,e2e-stack-down,down PROFILE=test-text)
e2e-stages:      ## (별칭) → e2e SUITE=stages PROFILE=test-text
	$(call deprecated,e2e-stages,e2e SUITE=stages PROFILE=test-text STAGE=$(STAGE))

.PHONY: profile doctor up down seed test lint eval measure e2e stage stage-advance docs-gen docs-check help \
	infra-up infra-down seed-compose test-live test-e2e eval-live virtual-candidate e2e-auth e2e-faults \
	e2e-stack-up e2e-stack-seed e2e-stack-down e2e-stages
