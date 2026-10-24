# scripts

저장소 루트에서 실행하는 보조 스크립트다. Python 스크립트는 `uv run --project services/api python scripts/<파일>`로 돌리며(api 가상환경의 httpx·asyncpg·python-dotenv를 쓴다), 자주 쓰는 것은 `make` 타깃으로 감싸 두었다.

## 한눈에

| 스크립트 | 역할 | make 타깃 |
|---|---|---|
| `seed.py` | 자료 원문 시드 → 관리자 가입·채용 건 생성 → rag 재색인 | `make seed PROFILE=…` |
| `make_session.py` | 단계 시드 세션을 즉시 생성 | `make stage` |
| `stage_advance.py` | 세션 시간 당기기 | `make stage-advance` |
| `save_eval_fixture.py` | 끝난 세션의 SessionRecord를 eval 픽스처로 저장 | - |
| `docs_gen.py` | README 생성 블록 갱신·검사, features.md 테스트 참조 검사 | `make docs-gen`, `make docs-check` |
| `profile.py` | 실행 프로필 해석: 유효 모드 출력·검사, compose용 env 파일 생성, 프로필 env로 명령 실행 | `make profile`, `make doctor`, `make up/down/seed/eval/measure/e2e/stage`가 내부에서 사용 |

## 스크립트별 사용

### seed.py

| 항목 | 내용 |
|---|---|
| 실행 위치 | 저장소 루트 |
| 필요한 서비스 | postgres(1단계), api 공개 앱 8000(2단계), rag-mcp 9102(3단계) |
| env | `SEED_DATABASE_URL`(기본 localhost postgres), `--api`·`--rag`·`--database-url`로도 지정 |
| 하는 일 | ① 미션 자료 원문을 현재 `catalog_version`으로 `materials` 테이블에 시드(이전 버전 유지, 같은 버전은 덮어쓰는 멱등) ② 관리자 가입(이미 있으면 로그인)과 채용 건 생성(`/admin/positions`, 요소 선택 없음) ③ rag-mcp `reindex_materials`로 같은 버전 청크 색인 |
| 인자 | `--minutes 60\|15`, `--mission`, `--email`, `--company`, `--position`, `--skip-position`, `--skip-reindex` |
| 출력 | `materials <개수> catalog_version=…`, `position <id> catalog_version=…`, `reindex <청크 수>` 한 줄씩 |

```
make seed SCOPE=infra                      # 로컬 개발(호스트 api 8000·postgres 5432·rag 9102)
make seed PROFILE=dev-fake                 # 전체 compose: compose 안 일회성 seed 서비스
make seed PROFILE=test-text                # 격리 스택: 호스트에서 5442·8100·9112로
uv run --project services/api python scripts/seed.py --minutes 15
uv run --project services/api python scripts/seed.py --skip-position --skip-reindex   # 자료만
```

### make_session.py

| 항목 | 내용 |
|---|---|
| 실행 위치 | 저장소 루트 |
| 필요한 서비스 | api 내부 앱(`E2E_INTERNAL_URL`, 기본 `http://localhost:8001`), `ENABLE_TEST_API=1` |
| env | `E2E_INTERNAL_URL`, `INTERNAL_TOKEN`(`infra/.env` → `infra/.env.local`, 환경변수가 우선. 값은 출력하지 않음) |
| 인자 | `--start-at entry\|keyword\|problem\|solution\|proposal\|ended`, `--minutes 15\|60`, `--stage`(재생할 시드 이름), `--llm fake\|live`(스택이 어떤 모드여야 하는지 안내만. `make stage`는 프로필의 `LLM_FAKE`로 채운다). env `LLM_FAKE=1`이 아니면(`--llm live`·`LLM_FAKE=0`·없음) `ALLOW_API=1` 없이는 시작하지 않는다(시드 세션은 곧 AI 동료의 모델 호출) |
| 출력 | 초대 링크, 세션 id, 단계별 확인용 정보(세션 토큰, 디브리핑 토큰, 관리자 리포트 링크) |

```
make stage STAGE=problem MIN=15 PROFILE=dev-fake
```

시드 재생은 card_due를 거치지 않아 직후 `session_cards.locked_option_id`·`card_progress`가 비어 있다. problem 시드의 기준안은 첫 card_due 처리 때 잠기고, solution·proposal·ended 시드는 ended 리포트·디브리핑에 문제 단계 카드 장면이 없다.

### stage_advance.py

| 항목 | 내용 |
|---|---|
| 실행 위치 | 저장소 루트 |
| 필요한 서비스 | api 내부 앱, `ENABLE_TEST_API=1` |
| env | `E2E_INTERNAL_URL`, `INTERNAL_TOKEN` |
| 인자 | `--sid <세션 id>`, `--to deadline\|idle\|card:<카드 id>\|<초>`. env `LLM_FAKE=1`이 아니면 `ALLOW_API=1` 없이는 시작하지 않는다(당긴 시계가 곧 모델 호출) |
| 하는 일 | 세션 시계를 앞당긴다. 시간 초과·침묵 초대·카드·자동 제출은 다음 스케줄러 틱(5초)이 평소 경로로 처리한다 |

```
make stage-advance SID=<세션 id> TO=deadline PROFILE=dev-fake
```

### save_eval_fixture.py

| 항목 | 내용 |
|---|---|
| 실행 위치 | 저장소 루트 |
| 필요한 서비스 | assessment-mcp 9103 (끝난 세션이 있는 실 스택) |
| env | `ASSESSMENT_MCP_TOKEN`(출력하지 않음), `E2E_ASSESSMENT_MCP_URL` 또는 `ASSESSMENT_MCP_URL` |
| 입력 → 출력 | 세션 id → `tests/eval/fixtures/sessions/<session_id>.json` (`--out`으로 폴더 변경) |

```
uv run --project services/api python scripts/save_eval_fixture.py <session_id>
```

### docs_gen.py

| 항목 | 내용 |
|---|---|
| 실행 위치 | 저장소 루트 |
| 필요한 서비스 | 없음(정적 파싱과 `pytest --collect-only`) |
| 하는 일 | 서비스 README의 `<!-- gen:api -->`·`<!-- gen:mcp-tools -->`·`<!-- gen:tests -->` 블록을 채운다. `--check`는 커밋된 README와 다르면 차이를 출력하고 실패, `--features`는 `docs/features.md`의 테스트 참조가 실제로 있는지 검사 |

```
make docs-gen      # 블록 갱신
make docs-check    # 생성 블록 최신 여부 + features.md 참조 검사
```

### profile.py

| 항목 | 내용 |
|---|---|
| 실행 위치 | 저장소 루트, `python3`만 필요(표준 라이브러리) |
| 입력 | `infra/profiles/<PROFILE>.env`(비밀 아닌 선택값) + `infra/.env`(비밀값) + 기본값. 셸의 모드 변수(`LLM_FAKE`·`ENABLE_TEST_API`·`AGENT_RUNNER`·추적 플래그)가 프로필과 다르면 오류 |
| 하위 명령 | `show [PROFILE]` 유효 모드 출력(목적·역할별 모델·키 **이름과 있음/없음**·테스트 API·compose 프로젝트·포트), `doctor [PROFILE] [--starting]` 출력 + 검사(deploy에 가짜 LLM·테스트 API 금지, 허용 목록 밖 모델·빈 키, `--starting`이면 실제 LLM에 `ALLOW_API=1` 요구), `get PROFILE KEY` 비밀 아닌 값 하나(Makefile용. 비밀 이름은 빈 줄), `render PROFILE [--starting] [--out]` compose용 합친 env 파일(`infra/.env.resolved-<프로필>`, git 무시), `exec PROFILE [--starting] [--need real∣fake] -- CMD…` 합친 env와 호스트 주소(`E2E_INTERNAL_URL`·`WEB_URL`·`SEED_DATABASE_URL` 등)로 명령 실행 |
| 출력하지 않는 것 | 키·토큰·비밀번호 값. 외부 API 호출도 없다 |

```
make profile PROFILE=eval-text
make doctor PROFILE=deploy
python3 scripts/profile.py exec test-text -- env | grep E2E_INTERNAL_URL
```

## 서비스 이름과 포트

api(8000·내부 8001), debate-mcp(9101), rag-mcp(9102), assessment-mcp(9103), report-worker(포트 없음), web(3000). 자료 원문은 api가 `materials` 테이블에서 읽고 rag-mcp는 `material_chunks`만 쓴다([`docs/architecture/data-model.md`](../docs/architecture/data-model.md)). env 이름은 [`docs/guides/configuration.md`](../docs/guides/configuration.md)에 있다.
