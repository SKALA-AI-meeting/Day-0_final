# 기여 가이드

사람과 코딩 도구(에이전트 포함)가 같이 따르는 작업 규칙이다. 처음 실행은 [quickstart.md](quickstart.md), 테스트 범위는 [testing.md](testing.md).

## 정본과 문서

- 기획 정본은 루트 `PRD.md` 하나다. `PRD.html`은 `sh build/build.sh`로 만들고 직접 고치지 않는다.
- 설계 문서는 [docs/README.md](../README.md)의 목적별 색인을 따른다. 문서와 PRD가 어긋나면 PRD가 맞고, 어느 쪽을 고칠지는 PR에서 정한다.
- 기획 방향이 바뀌는 결정은 [decisions.md](../decisions.md)에 날짜·결정·이유·대안으로 남긴다. 문구 정리·기능 ID 추가 같은 작은 PRD 변경은 바로 고친다.
- 문서는 현재 동작을 쓴다. "이전에는"·"바꿨다" 같은 이력 표현은 쓰지 않고 이력은 git과 decisions.md에 둔다. 필요 없어진 문서는 지운다.
- 문서 사이 참조는 상대 Markdown 링크로 쓰고, 코드 주석에서 문서를 가리킬 때는 `docs/...` 경로를 쓴다. 문서를 옮기면 두 곳을 모두 고친다(`scenarios/missions/debate/tests`는 `docs/product/scenario.md`·`docs/product/rubric.md`를 직접 읽는다).
- 금지 표현: 리포트·화면·프롬프트·문서에 합격 여부·순위·총점을 만들지 않는다. 지원자 호칭은 "지원자"(대화 속 "OO님")만 쓴다.

## 브랜치와 PR

- `main`은 항상 기동 가능하게 둔다. 작업은 `main`에서 갈라진 브랜치(예: `feat/<주제>`, `fix/<주제>`, `docs/<주제>`)에서 하고 PR로 합친다.
- 한 PR은 한 목적이다. 계약·DB·카탈로그 변경이 섞이면 PR 설명에 각각 적는다.
- 커밋 메시지는 한국어 한 문장. 비밀값·로컬 산출물(`.env*`, 테스트 영상·trace, `node_modules`)을 커밋하지 않는다.
- 리뷰는 작성자가 아닌 사람(또는 작성에 쓰지 않은 다른 도구)이 한다. 리뷰어는 계약 준수, 동작 검증 방식, 테스트 범위, 금지 표현을 본다.

## 병합 전 필수 검사

| 바뀐 것 | 돌릴 것 |
|---|---|
| 모든 PR | `make test`, `make lint`(postgres를 띄운 상태에서. 없으면 api DB 테스트가 skip된다) |
| 계약·러너·eval 픽스처 | `make test SUITE=tools`(`make test`에 포함) |
| 화면·api 흐름 | `make e2e SUITE=stages PROFILE=test-text`(격리 스택, 가짜 LLM) |
| 프롬프트·모델·평가 규칙 | `make eval PROFILE=eval-text ALLOW_API=1` 1회, 결과 파일을 PR에 링크 |
| 세션 전체 흐름·시간 로직 | 실 스택 `make e2e`(`all_five` 15분) 1회 |

PR 설명에는 실행한 명령과 검증 방식(mock / 격리 DB / 실 API / 실 LLM)을 적는다. test·lint 통과만으로 실제 서비스 연결을 확인했다고 보지 않는다. `tests/expected-failures.txt`는 비어 있어야 하며, 항목을 더하려면 PR에서 이유와 해소 계획을 적고 리뷰어 동의를 받는다.

## 테스트 작성

- happy path 중심: 기능마다 정상 경로 1개와 계약·평가가 요구하는 실패 경로(권한 403, 공개 전 423, 상태 409, 재시도·DLQ, 멱등)만 쓴다. 입력 조합 전수·경계값 나열은 쓰지 않는다.
- LLM은 테스트에서 항상 mock(`FakeLLM`)이다. 실제 모델을 부르는 테스트는 `@pytest.mark.live`로 표시해 기본 실행에서 뺀다.
- 시간 로직은 시계를 주입한 가짜 시계로 검증한다.

## 계약 변경

- 경로·필드·오류 코드·이벤트·MCP 도구·큐 본문의 정본은 [contracts/](../contracts/README.md)다. 구현이 계약과 달라야 하면 먼저 계약 문서를 고치는 PR(또는 같은 PR의 첫 커밋)으로 올린다.
- 계약 예시 JSON은 `tests/fixtures/contracts/`가 원본이고 `apps/web/mocks/fixtures/`에 같은 이름·같은 내용으로 복사한다(web 전용 `debate_script.json`·`office_script.json` 제외). `tests/test_contract_fixtures.py`가 두 곳이 같은지, `tests/contract_shape.py`가 형태를 검사한다. 고친 뒤 `apps/web`의 prettier로 포맷한다.
- 계약을 바꾸면 그 계약을 쓰는 쪽(MCP·워커·web mock·e2e 가짜 api `tests/e2e/fake_stack.py`)을 같은 PR에서 맞춘다.

## DB 변경

- `infra/db/schema.sql`과 [architecture/data-model.md](../architecture/data-model.md)를 함께 고친다. 마이그레이션은 두지 않으므로 PR 설명에 "로컬 볼륨을 지우고 다시 만들 것"을 적는다([database.md](database.md)).
- DB 쓰기는 api만 한다. 다른 서비스는 내부 API(8001)를 거친다.

## 시나리오·프롬프트 변경

[scenario-changes.md](scenario-changes.md) 순서를 따른다. 카탈로그를 바꾸면 `catalog_version`이 바뀌고 새 채용 건부터 적용된다.

## 같은 기계·같은 스택을 함께 쓸 때

- **같은 DB·큐를 쓰는 api 프로세스는 하나만** 띄운다(메일박스·스케줄러가 프로세스 메모리에 있다). 여러 작업을 동시에 실 스택으로 확인하려면 격리 e2e 스택처럼 compose 프로젝트 이름과 포트를 달리한 별도 스택을 쓴다([configuration.md](configuration.md)).
- 공용 개발 DB에 스키마 적용·시드·재색인을 할 때는 같은 DB를 쓰는 사람에게 먼저 알린다.
- 단위 테스트는 임시 DB(api)·가짜 서버(MCP·워커)로 격리돼 서로 영향을 주지 않는다. 격리를 깨는 테스트(공용 DB·큐에 직접 쓰기)는 넣지 않는다.

## 코드 스타일

- Python 3.12, `uv`, ruff(루트 `ruff.toml` 하나). Node 22, pnpm, Next 기본 eslint + prettier.
- 주석은 모듈·클래스·함수의 역할, 읽는 사람이 알아야 하는 로직(왜 그렇게 하는지), 예외 처리의 의도만 짧게 쓴다. 줄마다 설명하지 않는다.
- 결정(누가 어떤 안을 미는지·합의·시간 초과·칸 채우기 허용)은 결정적 코드가, 말투만 LLM이 정한다. 페르소나 에이전트는 assessment-mcp에 접근하지 못한다([architecture/security.md](../architecture/security.md)).
