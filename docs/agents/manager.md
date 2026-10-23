# 진행 매니저 (manager)

토론 그래프에서 자극(지원자 메시지·단계 시작·지원자 침묵·카드 시각)마다 **누가 말할지(0~2명)와 그 목적**을 정하는 역할이다. 결정·합의 판정·카드 담당·입장 계획은 결정적 코드가 하고, 규칙으로 화자가 정해지지 않는 지원자 메시지에서만 LLM이 화자를 고른다. 무엇을 어떻게 말할지는 [토론 동료](persona.md)가 정한다. 상세 규칙은 [architecture/agents.md](../architecture/agents.md) "진행 매니저 규칙"·"카드별 실행"이 정본이다.

## 입력

- 자극 `{type, phase, generation, message_id?, card_id?}`([architecture/agents.md](../architecture/agents.md) "AgentRunner 인터페이스")
- DebateState(debate-mcp `get_debate_state`: 지지 현황·안·칸·카드 진행·남은 시간), 최근 대화 30개
- 지원자 메시지의 의도 분류 결과([의도 분류](intent.md): 대상 안·`addressed_to`)
- 채용 건에 고정된 카탈로그 복사본(`positions.scenario`)

## 출력

- 진행 매니저 결정(`agent_runs.decision`, 형식은 [contracts/internal.md](../contracts/internal.md) "진행 매니저 결정 스키마"): 화자·목적·대상 안·카드 brief·채우기
- 화자별 입장 계획(`stance_plan`)과 카드 지시 → 토론 동료 입력
- 합의가 있으면 `fill_slot`(공지 고정 문장 포함), 카드 첫 발언 뒤 assessment-mcp `record_observation`

## 데이터 파일 (`scenarios/missions/debate/`)

| 파일 | 쓰는 내용 |
|---|---|
| `prompts/manager/turn_manager.md` (키 `turn_manager.md`) | LLM 화자 선택 지침(규칙 6) |
| `personas.yaml` | 자리별 고정 입장(`stances`), 설득 가능 규칙(`persuadable`) |
| `cards.yaml` | 카드 6장의 시각·담당·고정 내용 |
| `keywords.yaml` | 단계별 카탈로그 안(입장 계획의 대상) |
| `mission.yaml` | 침묵 초대(`silence_invite`), 합의 채우기 화자 우선순위(`consensus_fill`) |
| `prompts/shared/fixed_phrases.yaml` (키 `fixed_phrases.yaml`) | 합의 채우기 공지(`consensus_fill`), 정해진 안내(`notice_triggers`·`notices`) |

## 코드 입구

- `services/api/app/agent/manager/rules.py`: 결정적 규칙(`rule_decision`, `card_brief`, `stance_plan`, `fill_seat`, `validate_turn_llm`, 카드 지시 `CARD_INSTRUCTIONS`·선점 금지 `preempt_bans`)
- `services/api/app/agent/debate/graph.py`: 이 규칙을 부르는 노드 `cards`·`turn`(+ `_turn_llm`)·`fill`. 그래프는 여러 역할을 잇는 배선이라 `debate/`에 둔다
- 모델 역할 키 `turn`, env `MODEL_TURN`(역할 이름을 바꾸기 전 그대로). 타임아웃·재시도는 [architecture/harness.md](../architecture/harness.md) "역할별 모델"

## 검증

- 단위(LLM mock): `services/api/tests/agent/test_turn.py`(지명 우선·화자 ≤ 2·침묵 초대·합의 채우기·재실행 멱등), `test_cards.py`(카드 전달·기준안 잠금), `test_script.py`(15분 대본)
- 평가셋: `tests/eval/persona_scripts.yaml`의 지명 무응답·질문 무시 장면(`make eval-live`, `tests/eval/persona_eval.py`)
