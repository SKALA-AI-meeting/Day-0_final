# 시나리오·척도·프롬프트 바꾸기

미션 카탈로그(`scenarios/missions/debate/`)를 바꾸는 순서다. 카탈로그 구조와 규칙은 [product/scenario.md](../product/scenario.md), 평가 척도는 [product/rubric.md](../product/rubric.md)가 정본이다.

## 버전이 정해지는 방식

- `catalog_version`은 미션 폴더 전체(파일 경로와 내용)의 해시다(`scenarios/version.py`, 16자리). 미션 바로 아래 `tests/`와 점으로 시작하는 파일은 넣지 않는다. 자료·프롬프트·픽스처 본문 한 글자만 바꿔도 버전이 바뀐다.
- 채용 건은 생성 때 그 시점의 시나리오 전체(자리·키워드·고정 입장·카드·척도·프롬프트·평가 프롬프트·금지 패턴·자료 목록)를 `positions.scenario`에 복사하고 `catalog_version`을 저장한다. 세션 실행과 평가는 그 복사본을 읽는다. 복사본에 필요한 값이 없으면 현재 파일로 채우지 않는다(세션 시작 409 `position_outdated`, 평가는 리포트 실패).
- 그래서 **바꾼 시나리오를 반영하려면 새 채용 건을 만든다.** 기존 채용 건은 자기 복사본으로 계속 진행·평가된다.
- 자료 원문(`materials`)과 검색 청크(`material_chunks`)는 `catalog_version`별로 저장되고 이전 버전 행을 지우지 않는다. 먼저 만든 채용 건은 자기 버전 원문을 계속 읽는다. 청크 행의 키는 `catalog_version`에 rag-mcp 청크 규칙 버전을 붙인 값(`index_version`)이라, 청크 규칙(`services/rag-mcp/rag_mcp/materials.py` `CHUNKER_VERSION`)을 올린 배포에서는 `make seed PROFILE=…`(`reindex_materials`)로 현재 카탈로그를 새 키로 색인한다. 먼저 만든 채용 건은 같은 catalog_version의 옛 키 색인을 계속 쓰므로(검색이 현재 키 → 옛 키 순서로 찾음) 재색인 없이도 검색되고 결과도 그대로다. 현재 카탈로그의 색인이 어느 키로도 없을 때만 `upstream_unavailable`.

## 순서

1. **문서 먼저**: 규칙이나 표가 바뀌면 [product/scenario.md](../product/scenario.md)·[product/rubric.md](../product/rubric.md)를 고친다. 기획 방향이 바뀌는 변경이면 [decisions.md](../decisions.md)에 날짜·이유·대안을 적는다.
2. **카탈로그 수정**: `mission.yaml`·`keywords.yaml`·`personas.yaml`·`cards.yaml`·`rubric.yaml`·`materials/<keyword>/*.md`·`prompts/<역할>/` 중 해당 파일.
   - 프롬프트는 역할 폴더(`manager`·`persona`·`intent`·`assistant`·`evaluator`, 공용 `shared`, 픽스처 `fixtures`)에 둔다. 실행 코드와 채용 건 복사본은 역할 폴더를 뗀 키(`turn_manager.md`, 평가자만 `evaluator/common.md`)로 찾는다. 새 역할 폴더를 만들면 `scenarios/version.py` `PROMPT_KEY_PREFIX`에 등록한다(등록하지 않으면 조립이 실패한다). 역할별로 읽는 파일은 `docs/agents/<역할>.md`(예: [agents/persona.md](../agents/persona.md)).
   - `rubric.yaml`의 차원·앵커·담당자 참고 문구·기록 없음 사유는 rubric.md 3장·5장 표와 글자 그대로 같아야 한다.
   - 키워드·카드 표는 scenario.md 2.4·4.2 표와 같아야 한다.
   - 카드를 더하거나 `card_id`를 바꾸면 카드별 지시·선점 금지 사전이 있는 `services/api/app/agent/manager/rules.py`도 함께 고친다(카드별 담당과 고정 내용은 [architecture/agents.md](../architecture/agents.md) 카드 표).
   - 아바타는 파일을 덮어쓰지 않고 새 중립 이름(`avatar-NN.png`)으로 더한 뒤 `personas.yaml`의 `avatar`를 바꾼다(진행 중 채용 건이 옛 경로를 쓰기 때문).
3. **정합 검사**: `make test`(루트의 `scenarios/missions/debate/tests`가 카탈로그 ↔ 문서 표, 프롬프트 픽스처를 대조한다). 계약 예시(`tests/fixtures/contracts/`)에 카탈로그 값이 들어 있으면 함께 고친다.
4. **프롬프트 확인**: 페르소나·의도 분류·평가 프롬프트를 바꿨으면 `make eval PROFILE=eval-text ALLOW_API=1`로 실제 모델 결과를 한 번 남긴다(기준선과 비교). 평가 프롬프트를 바꾸면 `prompt_versions` 해시가 바뀌어 리포트에 새 버전이 기록된다.
5. **시드·재색인**: 자료나 카탈로그가 바뀌면 새 `catalog_version`으로 원문 시드와 재색인을 한다. `make seed PROFILE=… [SCOPE=infra]`(원문 시드 → 관리자·채용 건 → rag-mcp `reindex_materials`. 전체 compose는 compose 안 seed 서비스, 격리 스택·로컬 개발은 호스트에서). 파일만 옮기거나 이름을 바꿔도 `catalog_version`이 바뀌므로 같은 절차가 필요하다. api·rag-mcp는 `scenarios/`를 마운트로 읽으므로 컨테이너 재빌드는 필요 없다. report-worker는 카탈로그를 마운트하지 않고 채용 건 복사본(`positions.scenario.prompts`)만 읽으므로 바뀐 평가 프롬프트는 새 채용 건 복사본으로만 전달된다(재빌드·시드 불필요).
6. **새 채용 건으로 확인**: 관리자 화면 또는 `make stage`로 새 채용 건·세션을 만들어 바뀐 내용이 보이는지 본다. 기존 채용 건의 세션은 바뀌지 않는 것이 정상이다.

## 주의

- **코드 쪽 내장 지침은 복사본 밖이다.** 의도 분류(`intent.md`)와 동료 입장 판정(`stance_check.md`)은 채용 건 복사본에 그 키가 없으면 코드의 최소 내장 지침(`app/agent/intent/DEFAULT_INSTRUCTIONS`, `app/agent/persona/STANCE_DEFAULT_INSTRUCTIONS`)을 쓴다. 이것은 "복사본에 없으면 채우지 않는다"의 예외이며, 판정 스키마·위반 규칙과 함께 배포된 코드 버전을 따르는 코드 경계다. 새 프롬프트 키를 더한 배포 뒤에는 기존 채용 건이 새 코드 + 내장 지침으로 돌므로, 같은 조건을 유지하려면 `make seed PROFILE=…`로 채용 건을 다시 만든다. 내장 지침은 최소로 유지하고 카탈로그 파일을 정본으로 둔다.

- 카탈로그 변경은 기존 채용 건에 적용되지 않는다. 변경은 새 채용 건 단위로 공지한다.
- 리포트 재생성(`report/regenerate`)은 채용 건 복사본의 척도·평가 프롬프트를 다시 쓴다. 카탈로그의 새 프롬프트로 옛 세션을 다시 평가하는 경로는 없다.
- 카드 시각·단계 예산을 바꾸면 15분 압축 값(최소 20초)과 테스트의 시각 단언이 함께 바뀐다([testing.md](testing.md)).
