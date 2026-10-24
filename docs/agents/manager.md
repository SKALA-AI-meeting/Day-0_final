# 진행 매니저 (manager)

토론 그래프에서 자극(지원자 메시지·단계 시작·지원자 침묵·카드 시각)마다 **누가(0~2명) 어떤 메시지에 답할지**와 그 자리에 줄 상황 정보(답할 메시지, 지명 여부, 카드 차례, 내 지난 발언에 대한 반응)를 정하는 역할이다(결정 C1). 무엇을 어떻게 말할지는 정하지 않고 [토론 동료](persona.md)가 성향대로 정한다. 지원자가 동료를 부르면 그 동료가 반드시 답하고(상한을 넘으면 다음 실행), 지원자 메시지에는 최소 1명이 답한다. 결정·합의 판정·카드 담당·입장 계획·응답자 배정은 결정적 코드가 하고, 규칙으로 답할 자리가 정해지지 않는 지원자 메시지에서만 LLM이 답할 자리를 고른다. 상세 규칙은 [architecture/agents.md](../architecture/agents.md) "진행 매니저 규칙"·"카드별 실행"이 정본이다.

## 입력

- 자극 `{type, phase, generation, message_id?, card_id?}`([architecture/agents.md](../architecture/agents.md) "AgentRunner 인터페이스")
- DebateState(debate-mcp `get_debate_state`: 지지 현황·안·칸·카드 진행·남은 시간·그 단계 지원자 메시지의 저장된 의도 분류 `candidate_intents`·전체 기록에서 계산한 아직 답하지 않은 지명 `open_mentions`), 최근 대화(그 단계 30개, AI 발언은 답한 메시지 `reply_to` 포함. 진행 매니저 LLM에는 마지막 10개)
- 지원자 메시지의 의도 분류 결과([의도 분류](intent.md): 대상 안·`addressed_to`·`excluded`(답하지 말라고 뺀 자리, 지명·턴 LLM 화자에서 뺀다)·`reservation`(설득 가능 규칙에서 세지 않음))
- 채용 건에 고정된 카탈로그 복사본(`positions.scenario`)

## 출력

- 진행 매니저 결정(`agent_runs.decision`, 형식은 [contracts/internal.md](../contracts/internal.md) "진행 매니저 결정 스키마"): 화자별 답할 메시지(`reply_to`)·카드(`card_ids`)·침묵 초대(`invite`), 다음 실행으로 넘긴 지명(`queued`), 채우기
- 화자별 상황 정보(`[상황]` 블록, `rules.situation`: 답할 메시지·지명·카드 차례·반응·내 안을 두고 온 지원자 메시지 수·다른 안·이동 미발언)·입장 계획(`stance_plan`, 동료에게는 `[내 입장]` 상태로)·카드 차례 사실(장면 목적은 `cards.yaml` `purpose`) → 토론 동료 입력. 게시한 발언의 `meta.reply_to`·`meta.named`
- 합의가 있으면 `fill_slot`(공지 고정 문장 포함), 카드 첫 발언 뒤 assessment-mcp `record_observation`

## 데이터 파일 (`scenarios/missions/debate/`)

| 파일 | 쓰는 내용 |
|---|---|
| `prompts/manager/turn_manager.md` (키 `turn_manager.md`) | LLM 응답자 선택 지침(규칙 7) |
| `personas.yaml` | 자리별 고정 입장(`stances`), 설득 가능 규칙(`persuadable`: 세는 의도·대상·횟수·confidence) |
| `cards.yaml` | 카드 6장의 시각·담당·고정 내용 |
| `keywords.yaml` | 단계별 카탈로그 안(입장 계획의 대상) |
| `mission.yaml` | 침묵 초대(`silence_invite`), 합의 채우기 화자 우선순위(`consensus_fill`) |
| `prompts/shared/fixed_phrases.yaml` (키 `fixed_phrases.yaml`) | 합의 채우기 공지(`consensus_fill`), 정해진 안내(`notice_triggers`·`notices`) |

## 코드 입구

- `services/api/app/agent/manager/rules.py`: 결정적 규칙(`rule_decision`, 답하지 않은 지명 `open_mentions`·`pending_mentions`, 상황 정보 `situation`·`reactions`·`intents_toward`, 소극형 전체 질문 상태 조건 `answered_open_question_last`, 기본 응답 자리 `default_responder`, `card_brief`, `stance_plan`, `fill_seat`, `validate_turn_llm`, 선점 금지 `preempt_bans`)
- `services/api/app/agent/debate/graph.py`: 이 규칙을 부르는 노드 `cards`·`turn`(+ `_turn_llm`)·`fill`. 그래프는 여러 역할을 잇는 배선이라 `debate/`에 둔다
- 모델 역할 키 `turn`, env `MODEL_TURN`(역할 이름을 바꾸기 전 그대로). 타임아웃·재시도는 [architecture/harness.md](../architecture/harness.md) "역할별 모델"

## 검증

- 단위(LLM mock): `services/api/tests/agent/test_turn.py`(지명 우선·제외 자리·화자 ≤ 2·침묵 초대·합의 채우기(같은 run 재판정 없음)·설득 가능 규칙 횟수·재실행 멱등), `test_manager_context.py`(지명 응답·넘친 지명의 다음 실행 응답(재시작 포함)·반복 지명에서 굶지 않음·대화 창 밖 지명·고정 문장 카드는 지명을 닫지 않음·진행 발언 최소 1명·화자별 `reply_to`·`[상황]` 블록), `test_cards.py`(카드 전달·기준안 잠금), `test_script.py`(15분 대본)
- 평가셋: `tests/eval/persona_scripts.yaml`의 지명 무응답·질문 무시 장면(`make eval-live`, `tests/eval/persona_eval.py`의 `replies`: 지원자 메시지마다 `reply_to`로 답한 발언이 있는지, 부른 자리가 답했는지)
