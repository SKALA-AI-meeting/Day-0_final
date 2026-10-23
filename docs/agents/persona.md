# 토론 동료 (persona)

지원자와 동등하게 토론하는 AI 동료 3명(`p1` 고집형 강한결 `stubborn`, `p2` 소극형 윤하람 `quiet`, `p3` 비판형 서지안 `critic`)의 발언을 만드는 역할이다. [진행 매니저](manager.md)가 정한 화자·목적·입장 계획·카드 지시를 받아 자기 성향의 말투로 한 번 말한다. 어떤 안을 밀지는 입장 계획(데이터)이 정하고 말투만 LLM이 정한다. 상세는 [architecture/agents.md](../architecture/agents.md) "페르소나 에이전트 (respond)"·"계획 밖 입장"·"guard"가 정본이다.

## 입력

- 자기 성향 프롬프트, 공통 규칙, 이번 목적과 대상 안, 자기 입장 계획(`stance_plan`), 카드 brief(자기 자리만)
- 자리 기억(자기 최근 발언 3개), 걸러 낸 DebateState(지지 현황·확정 칸·남은 시간), 최근 대화 30개, 지원자 표시 이름
- 도구(허용 목록, 응답당 3회): debate-mcp `get_transcript`·`get_draft`, rag-mcp `search_materials`·`get_material`. assessment-mcp는 주지 않는다

## 출력

- 구조화 출력 `{content, proposes?, stance?}`([contracts/mcp.md](../contracts/mcp.md)) → guard·stance_check 통과 뒤 `post_message`·`propose_option`·`set_stance`
- 걸리면 재생성 1회 → 자리별 안전 문구 또는 계획 일치 고정 문구(`messages.meta.guard`·`meta.stance_check`)

## 데이터 파일 (`scenarios/missions/debate/`)

| 파일 | 쓰는 내용 |
|---|---|
| `prompts/persona/persona_<type>.md` (키 `persona_<type>.md`) | 성향·말투·행동 규칙·must_not |
| `prompts/persona/common_rules.md` (키 `common_rules.md`) | 모든 자리 공통 규칙과 코드가 붙이는 블록 순서 |
| `prompts/persona/safe_<type>.md` (키 `safe_<type>.md`) | guard 2회 실패·비용 상한 때 쓰는 자리별 안전 문구(한 줄에 하나, 직전에 쓴 줄은 건너뜀) |
| `prompts/persona/stance_fallback.yaml` (키 `stance_fallback.yaml`) | 계획 밖 입장 2회 때 쓰는 자리·상황별 고정 문구(`text`·`alternatives`, 직전에 쓴 문구는 건너뜀) |
| `prompts/shared/guard_patterns.yaml` (키 `guard_patterns.yaml`) | 금지 패턴·`max_chars` |
| `prompts/shared/fixed_phrases.yaml` (키 `fixed_phrases.yaml`) | 정해진 안내문(`notices`) |
| `personas.yaml` | 이름·성향 설명·고정 입장 |
| `materials/<keyword>/*.md` | 확정 키워드 자료(rag-mcp 도구로만 읽음) |

## 코드 입구

- `services/api/app/agent/persona/__init__.py`: 컨텍스트 조립 `build_messages`, 도구 루프 `generate`, 계획 판정 `plan_violation`·`sentence_violation`, 고정 문구 `stance_fallback`·`safe_draft`
- `services/api/app/agent/debate/graph.py`: 노드 `respond`·`guard`·`stance_check`·`wait_typing`·`apply`
- `services/api/app/agent/guard/`: 금지 문구 검사(어시스턴트와 공용)
- 모델 역할 키 `persona`, env `MODEL_PERSONA`

## 검증

- 단위(LLM mock): `services/api/tests/agent/test_persona.py`(계획 밖 입장·판정 유도·비용 상한·컨텍스트 경계), `test_cards.py`
- 픽스처: `prompts/fixtures/stance_offplan.yaml`·`verdict.yaml`·`leak.yaml`·`preemption.yaml`(채용 건에 복사하지 않음)
- 평가셋: `tests/eval/persona_scripts.yaml`(지명 무응답·질문 무시·오인용·카드), `tests/eval/leak_cases.yaml`(유출·유도 질문). `make eval-live`
