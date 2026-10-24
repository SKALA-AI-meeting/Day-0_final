# 토론 동료 (persona)

지원자와 동등하게 토론하는 AI 동료 3명(`p1` 고집형 강한결 `stubborn`, `p2` 소극형 윤하람 `quiet`, `p3` 비판형 서지안 `critic`)의 발언을 만드는 역할이다. [진행 매니저](manager.md)가 정한 화자·상황 정보(답할 메시지·지명 여부·카드 차례·내 지난 발언에 대한 반응·내 안을 두고 온 지원자 메시지·지지 이동 미발언)와 자기 입장의 상태·카드 장면의 목적을 받아, 무엇을 어떻게 말할지 자기 성향대로 정해 한 번 말한다. 어떤 안이 자기 안인지는 입장 계획(데이터)이 정하고, 언제 어떻게 말할지는 성향 프롬프트와 LLM이 정한다(결정 C2). 상세는 [architecture/agents.md](../architecture/agents.md) "페르소나 에이전트 (respond)"·"계획 밖 입장"·"guard"가 정본이다.

## 입력

- 자기 성향 프롬프트, 공통 규칙, 상황 정보(`[상황]` 블록, 사실만), 자기 입장의 상태(`[내 입장]`: `stance_plan`과 기록된 지지에서 계산), 카드 차례(`[카드 차례]`: 사실 + `cards.yaml` `purpose`, 자기 자리만)
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
| `prompts/persona/stance_check.md` (키 `stance_check.md`) | 입장 판정 지침: 초안이 `[내 안]`에 대해 `keep`(계속 지지)·`move`(다른 안으로)·`withdraw`(거둠·보류)·`none`(입장 없음) 중 무엇인지만 묻는다. 지원자 의도 분류 프롬프트와 따로 둔다 |
| `prompts/shared/guard_patterns.yaml` (키 `guard_patterns.yaml`) | 금지 패턴·`max_chars` |
| `prompts/shared/fixed_phrases.yaml` (키 `fixed_phrases.yaml`) | 정해진 안내문(`notices`) |
| `personas.yaml` | 이름·성향 설명·고정 입장 |
| `cards.yaml` `purpose`·`followup_purpose` | 카드 차례에 그대로 보이는 설계된 장면의 목적(데이터, 같은 채용 건 모두 같음) |
| `materials/<keyword>/*.md` | 확정 키워드 자료(rag-mcp 도구로만 읽음) |

## 코드 입구

- `services/api/app/agent/persona/__init__.py`: 컨텍스트 조립 `build_messages`(`stance_text`·`situation_text`·`card_text`), 도구 루프 `generate`, 계획 판정 `plan_violation`·입장 판정 `judge_stance`·`sentence_violation`, 고정 문구 `stance_fallback`·`safe_draft`
- `services/api/app/agent/debate/graph.py`: 노드 `respond`·`guard`·`stance_check`·`wait_typing`·`apply`
- `services/api/app/agent/guard/`: 금지 문구 검사(어시스턴트와 공용)
- 모델 역할 키 `persona`, env `MODEL_PERSONA`

## 입장 판정

`stance_check`는 구조화 출력(`stance`·`proposes`)을 코드로 대조한 뒤, 초안 글이 기록될 입장과 갈라지는지를 LLM에 한 번 묻는다(`judge_stance`). 질문은 지원자 메시지 분류가 아니라 "이 동료가 자기 안에 대해 `keep`·`move`·`withdraw`·`none` 중 무엇을 말하는가"이고, 입력은 `[내 안]`·`[다른 안]`·`[직전 메시지]`·`[초안]`, 출력은 `{verdict, target, confidence}`다. 거부하는 것은 high인 `move`(고른 안이 내 안이 아닐 때)와 `withdraw`뿐이고, 양보·비교 뒤 자기 안에 서는 `keep`("A도 좋지만 저는 B")·`none`·low는 통과한다. 입장이 없어야 하는 상태(`plan.declared` False)는 내 안이 없어 묻지 않고 구조화 출력만 본다. 모델은 의도 분류 역할(`MODEL_INTENT`, temperature 0, 타임아웃 10초, 재시도 없음)을 그대로 쓰고 지시만 `stance_check.md`다. 실패·스키마 위반이면 판정 없이 구조화 출력만으로 정한다.

## 검증

- 단위(LLM mock): `services/api/tests/agent/test_persona.py`(계획 밖 입장·판정 유도·비용 상한·컨텍스트 경계), `test_persona_voice.py`(입장 상태 블록·상황 사실·카드 차례 목적·입장 판정 전용 질문·실제 지지 이동만 거부·소극형 전체 질문 규칙), `test_cards.py`
- 픽스처: `prompts/fixtures/stance_offplan.yaml`·`verdict.yaml`·`leak.yaml`·`preemption.yaml`(채용 건에 복사하지 않음)
- 평가셋: `tests/eval/persona_scripts.yaml`(지명 무응답·질문 무시·오인용·카드), `tests/eval/leak_cases.yaml`(유출·유도 질문). `make eval PROFILE=eval-text ALLOW_API=1`

## 알려진 한계

- 오인용: 동료가 다른 참여자의 말을 실제보다 세게 옮길 수 있다. 예: 지원자가 "캠페인은 효과가 오래 안 가지 않나요?"라고 물었는데 동료가 "OO님 말씀처럼 캠페인은 일시적인 효과가 있을 수 있지만"으로 받는다(질문을 주장으로). 주로 "OO님 말씀처럼"으로 이어 붙이는 생성 단계의 습관이며 게시 전에 거르지 않는다. 측정 방법과 수치는 [tests/e2e/records/misattribution-experiment-20261024.md](../../tests/e2e/records/misattribution-experiment-20261024.md), 결정은 [decisions.md](../decisions.md) 2026-10-24 "인용 검사를 빼고 오인용을 알려진 한계로 둔다". 후속 이슈에서 다룬다.
