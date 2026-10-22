# 에이전트 설계 (api 안의 LangGraph 모듈)

음성 토론의 현재 구현은 [음성 구현](voice.md)을 따른다.

토론 그래프(턴 매니저 + 페르소나 에이전트 3)·의도 분류·어시스턴트·guard·평가 에이전트의 구조다. 결정 근거는 [decisions.md](../decisions.md) "2026-10-21 토론형 전환"(D10·D11·D12·D16·D17, R1~R7). 요소 id는 `persuasion, listening, inclusion, flexibility, honesty`, 카드 id는 `persuasion_keyword, flexibility_majority, listening_pressure, inclusion_silence, persuasion_solution, honesty_credit`, 페르소나 유형 id는 `stubborn, quiet, critic, agreeable, divergent`다(정본 [product/scenario.md](../product/scenario.md)). MVP 세션 자리는 **고정**이다(R2): `p1` 고집형 강한결(`stubborn`), `p2` 소극형 윤하람(`quiet`), `p3` 비판형 서지안(`critic`). 동조형·발산형은 MVP 이후 확장이며 아래 규칙의 우선순위 목록에 남아 있어도 MVP 세션에는 나오지 않는다. 참여자 id는 `candidate`·`p1`·`p2`·`p3`·`system`. 칸 결과는 `resolution: consensus | timeout`. 지원자 호칭은 "지원자", 대화 속에서는 표시 이름 + "님"이다.

모듈: `app/agent/{debate, assistant, intent, guard, llm, mcp_client}`, `runner.py`. `mcp_client`는 env `DEBATE_MCP_URL`·`RAG_MCP_URL`·`ASSESSMENT_MCP_URL`(+`ASSESSMENT_MCP_TOKEN`)로 접속한다([contracts/mcp.md](../contracts/mcp.md) "MCP 도구"). 평가(차원별 수준 판단)는 세션 후 report-worker 안의 평가 에이전트 몫이다(아래 "평가 에이전트", 실행 흐름은 [services/report-worker.md](../services/report-worker.md)).

## AgentRunner 인터페이스와 SessionPorts

api의 다른 부분은 이 인터페이스만 본다. 구현 선택은 env `AGENT_RUNNER` 하나이고 **기본값은 `session`(토론 그래프 실행)**이다. `config.py`와 runner 선택 코드가 같은 기본값을 쓴다. `fake`(`FakeAgentRunner`, 자리별 고정 문장을 post)는 api 단위 테스트가 주입하는 용도로만 남긴다. 실 스택에서 LLM 없이 결정적으로 돌릴 때는 runner를 바꾸지 않고 아래 `LLM_FAKE=1`을 쓴다.

```python
class AgentRunner(Protocol):
    async def run(self, session_id: UUID, stimulus: Stimulus, run_id: UUID) -> RunResult: ...
# Stimulus = {type: "message"|"phase_started"|"idle"|"card_due", phase: str,
#             generation: int, message_id?: UUID, card_id?: str}
# generation: message 자극은 그 메시지 저장이 올린 값(enqueue 때 다시 읽지 않음), 그 밖의 자극은 enqueue 시점 값
# RunResult = {ok: bool, fallback_used: bool, error?: str, decision: TurnDecision|None,
#              usage: {llm_calls, retries, tokens_in, tokens_out, cost_usd, latency_ms, tool_calls}}
```

- 그래프가 api에 기대는 것은 `SessionPorts`(api `ports.py`) 세 가지뿐이다: typing 전송(hub), generation 조회, 자극 enqueue. 나머지 읽기·쓰기는 MCP를 거친다.
- 실행은 api 세션 **메일박스**가 한다(R5-1, [services/api.md](../services/api.md)). 자극은 세션마다 직렬로 처리되므로 그래프 안에 Lock이 없고 `lock_held`·`handled`도 없다.
- `agent_runs`(run_id, graph=debate, stimulus, status, decision, usage). 멱등은 `agent_runs.status`와 DB 멱등 키만 쓴다. started로 200초(역할별 타임아웃 합 + 여유)를 넘긴 run은 스케줄러가 같은 run_id로 재실행한다.
- 자극 4종과 run_id 규칙은 [architecture/session-lifecycle.md](session-lifecycle.md) "그래프 자극"이 정본이다: message는 api가 새로 발급, phase_started는 `uuid5(session, "phase:"+phase)`, card_due는 enqueue마다 새 uuid4(담당 전원 delivered까지 스케줄러가 다시 넣음, 카드당 최대 3회·30초 간격, 멱등은 `session_cards.delivered_seats`), idle은 스케줄러가 enqueue할 때마다 발급(침묵 초대 중복은 `invite_delivered`와 `is_invite` 409가 막음). `timeout` 자극과 `room_quiet`는 없다(R5-2·R5-3).

## 토론 그래프 (`app/agent/debate/`)

MVP 구조는 **턴 매니저 + 페르소나 에이전트 3**(D10). 그래프는 체크포인터 없이 컴파일한다(`app/agent/factory.py` `build_debate_runner(..., checkpointer=None)`). 실행 기록은 `agent_runs`, 토론 상태는 DB가 원천이고 매 실행의 `load`가 다시 읽으므로 재시작 복구는 같은 자극을 처음부터 다시 실행하는 방식이다.

```
load ─▶ cards ─▶ turn ─┬─ (화자마다) respond → guard → stance_check → wait_typing → apply ─┬─ 다음 화자 → respond
       └ skipped·failed → END                              └ 미게시·generation 변경 → END ┘   └─▶ fill ─▶ END
                       └─ 화자 0명 ──────────────────────────────────────────────────────────▶ fill ─▶ END
```

등록된 노드는 9개다(`load`·`cards`·`turn`·`respond`·`guard`·`stance_check`·`wait_typing`·`apply`·`fill`, `graph.py` `build_graph`). typing 표시는 노드가 아니라 `respond`가 시작할 때 `start`, 게시·버림 때 `stop`을 SessionPorts로 보내는 부수 동작이다.

### State

`session_id, run_id, stimulus, phase, generation, ds, transcript, names, intent, brief, lead, decision, idx, plan, draft, status, posted, observed, fallback_used, last_error, llm_failed, notice` (`graph.py` `DebateState`)

- `generation`: 실행 시작 때 읽은 `sessions.generation`. 화자마다 게시 직전 SessionPorts 값과 비교한다.
- `ds`: debate-mcp `get_debate_state` 결과(DebateState + seats). 페르소나 컨텍스트에 미리 넣는다(R5-12, 조회 도구 왕복 줄이기). 페르소나에게 줄 때 `card_progress`·`cost_left`를 뺀다.
- `names`·자리 정보: 내부 `GET /internal/sessions/{id}`의 `seats`에서 만든다. 지원자 `{seat: candidate, display_name}`과 AI 자리 `{seat, display_name, avatar_url, persona_type}`(D13-3). `persona_type`은 그래프 안에서만 쓰고 페르소나 프롬프트에는 자기 성향 설명으로만 들어간다.
- 자리 기억: 별도 State 필드 없이 `transcript`(최근 대화 30개) 중 그 자리의 최근 발언 3개(`persona.py` `MEMORY_MESSAGES`)를 `[내 기억]`으로 넣는다. 밀었던 안·설득 이동·압박 횟수는 DB의 입장 이력과 메시지에서 매 실행 계산한다. 토큰 한도에 따른 요약 접기는 없다.
- `brief`: 이번 실행에서 말할 카드 담당과 고정 내용(`cards` 노드 결과). 카드 진행(`ds.card_progress`: 발동한 카드, 담당 화자별 `delivered`, 잠긴 기준안)은 api 기록이 원천이고 그래프는 읽기만 한다.

### 노드

| 노드 | 종류 | 하는 일 |
|---|---|---|
| load | 결정적 | debate-mcp `get_debate_state`·`get_transcript(phase, limit 30)`, SessionPorts로 generation. 낡은 자극이면 `skipped`로 END. 이미 `finished`인 run_id는 api 메일박스가 `agent_runs.status`를 보고 실행하지 않는다 |
| cards | 결정적 | `stimulus=card_due`면 그 카드와 **예정 시각이 지난 같은 단계의 미전달 카드**를 함께 발동 대상으로(공동 처리, `delivered_seats`에 이미 든 자리는 다시 넣지 않음), 그 밖의 자극이면 **이미 발동했지만 `delivered`가 아닌 담당 화자**를 이어서 말할 대상으로 정한다(R5-4). 결과는 이번 실행의 `card_brief`(누가 어떤 고정 내용을 말할지). `flexibility_majority`면 여기서 기준안을 계산해 잠근다(아래). 관찰 기록은 이 노드가 아니라 apply가 첫 카드 발언 게시 뒤에 한다 |
| turn | 결정적 규칙 + LLM(`MODEL_TURN`) | 화자 0~2명과 목적을 정한다(아래 규칙). 출력은 계약의 턴 매니저 결정 스키마 그대로 `agent_runs.decision`에 저장 |
| respond | LLM(`MODEL_PERSONA`) + 도구 | 자리의 페르소나 에이전트가 발언을 만든다(아래). 구조화 출력 |
| guard | 결정적 | 금지 문구 검사 → 재생성 1회 → 자리별 안전 문구(아래) |
| stance_check | 결정적 | 게시 문장과 입장이 `stance_plan`과 맞는지 검증. 계획 밖이면 응답 전체 1회 재생성 → 그래도 밖이면 계획과 맞는 고정 문구로 교체(R5-8, 아래) |
| wait_typing | 결정적 | 지원자 입력 중이면 최대 10초 기다린다. 그래도 입력 중이면 **게시하지 않고** 이 자극을 SessionPorts로 다시 enqueue하고 남은 화자를 끝낸다(R5-9). generation이 바뀌었으면 남은 화자를 버린다(R5-1) |
| apply | 결정적 | `post_message`(카드 발언이면 `card_ids` 포함, 두 카드를 겸하면 둘 다 → api가 카드마다 그 화자 `delivered` 기록) → (안을 처음 밀면) `propose_option` → (입장 표현) `set_stance`. 카드의 첫 발언이 게시된 직후 카드마다 assessment-mcp `record_observation{card_id, phase, trigger_message_id, run_id}` |
| fill | 결정적 | apply가 이번 발언의 입장을 저장한 뒤 `get_debate_state`로 지지를 **다시 읽어** 합의(지원자 포함 3/4, 지원자 지지)가 있으면 같은 run에서 채운다(`decision.fill`이 없었어도). 이 노드 전용 분기가 `decision.fill = {option_id, seat}`(또는 재판정 결과)로 공지 고정 문장을 만들고 `fill_slot{session_id, option_id, seat, content, run_id}` 한 번에 넘긴다(respond·apply를 거치지 않음). api가 한 트랜잭션에서 합의 재검사 → 칸 confirmed → 공지 메시지 저장(seq 5, `meta.kind=consensus_fill`) → generation +1을 하고 WS는 `message.new`(공지) → `slot.confirmed` → `phase.changed` → `timer` 순서로 보낸다. 별도 `post_message`는 없다. 409 `no_consensus`·`slot_confirmed`·`phase_not_open`이면 아무것도 저장되지 않고 run을 끝낸다 |

### 턴 매니저 규칙 (turn)

규칙을 위에서부터 적용한다. 결정·합의·채우기는 규칙이 하고 LLM은 "누가 자연스럽게 말할지"만 고른다(원칙 "결정은 결정적 코드가, 말투는 LLM이"). 시간 초과 채우기는 api가 결정적으로 하므로 그래프 규칙에 없다(R5-2).

1. `consensus_option_id`가 있고 칸이 `empty`: 화자 0명, `fill = {option_id, seat}`(합의 채우기 전용)만 정한다. 공지 화자는 화자 목록에 들어가지 않고 respond → guard → stance_check → apply 루프 없이 fill 노드 전용 분기가 처리한다. `seat` = 그 안 지지자 중 우선순위([product/scenario.md](../product/scenario.md), 고정 자리에서는 서지안 > 윤하람 > 강한결) 첫 자리. 합의는 지원자 포함 3/4 지지가 되는 즉시이고 대기 시간이 없다(R4). 이번 실행의 AI 발언으로 합의가 생기면 fill 노드가 apply 뒤에 다시 판정한다.
2. `card_brief`가 있으면 카드가 정한 화자·목적(`card`)을 넣는다. 미전달 화자가 남아 있으면 자극 종류와 상관없이 먼저 넣는다. `stimulus=card_due`는 이 규칙으로만 화자를 정한다.
3. `stimulus=message`이고 지원자가 부른 자리가 있으면 그 자리 `respond`(최대 2명). 부른 자리는 메시지에서 "이름님"으로 먼저 부른 순서이고, 의도 분류 `addressed_to`가 놓친 호명도 넣는다(`called_seats`). `quiet`는 이름이 불렸거나 전체 질문(intent=question, addressed_to=[])일 때만 대상이 된다. 정하는 방법을 묻는 진행 질문인데 턴 LLM이 화자 0명을 고르면 비판형 한 명이 답한다.
4. `stimulus=idle`이고 그 단계 `invite_delivered=false`: 화자 1명 `invite`. 묻는 자리 = 우선순위 `agreeable > divergent > critic` 중 세션에 있는 첫 자리(`stubborn`·`quiet` 제외, 고정 자리에서는 서지안). 게시 때 `is_invite=true`. 지원자가 입력 중이라 게시하지 못하면 플래그가 그대로라 스케줄러가 다시 넣는다.
5. `stimulus=phase_started`: 단계 첫 발언자 1~2명 `push_option`(시나리오 고정 입장을 가진 자리 중 `quiet` 제외). 문제 상황 단계에서는 카드 전이므로 `stubborn`도 입장을 밝히지 않고 일반 발언(`react`)만 한다(R5-4). proposal 단계는 화자 0명.
6. **턴 LLM은 1~5의 결과가 0명이고 자극이 `message`일 때만** 부른다(R5-12). LLM은 `respond`·`push_option`·`react` 중에서 최대 2명을 고른다. 입력은 DebateState 요약·최근 대화 10개·자리별 성향 한 줄·카드 선점 금지 목록. LLM 실패면 0명.

공통 제약: 한 실행 화자 최대 2명, 같은 자리 연속 3회 발언 금지(카드·지명 응답 제외), 카드 선점 금지([product/scenario.md](../product/scenario.md) 4.2 표)에 걸리는 목적은 제외. proposal 단계에서는 지원자가 묻거나 부른 경우만 `respond`.

### 페르소나 에이전트 (respond)

- 입력(Context, [architecture/harness.md](harness.md)): 자기 자리의 성향 프롬프트(`prompts/persona_<type>.md`의 말투·행동 규칙), 공통 규칙, 이번 목적과 대상 안, 자기 고정 입장(`stance_plan`), 카드 brief(해당 자리만), 자리 기억(자기 최근 발언 3개), **미리 넣은 DebateState**(지지 현황·확정된 칸·단계 남은 시간), 최근 대화 30개, 지원자 표시 이름(대화에서 "OO님"), 다른 자리 이름. 시간은 단계 남은 시간으로만 말한다.
- 도구(허용 목록, 응답당 3회): `get_transcript`(30개보다 앞 대화가 필요할 때), `search_materials`, `get_material`, `get_draft`(proposal 단계에서만 바인딩). `get_debate_state`는 컨텍스트에 이미 있으므로 바인딩하지 않는다. 자료는 확정 키워드 것만 열려 있고 키워드 단계에서는 상식 수준으로만 말한다.
- 출력(구조화): `{content, proposes?: {scenario_option_key∣label}, stance?: {option_id, stance: support∣oppose}}`. `content`는 1~3문장, 200자 이내 권장(guard 상한 400자).
- `stance_plan`은 결정적으로 계산한다: 단계별 고정 입장 표, 수용 카드 담당(고집형 1명 확정 전 불변 + 다른 1명 설득 가능, R1), 설득 가능 규칙(지원자가 그 자리 안에 반대·대안 제안을 두 번 하면 지원자 안으로 단계당 1회 이동, `stubborn`은 확정 전 불변).
- 정해진 안내(`fixed_phrases.yaml` `notices.decide`·`notice_triggers`): 지원자 메시지가 정하는 방법을 묻는 패턴에 걸리면 그 run 첫 화자 발언 앞에 코드가 고정 안내문을 붙이고, 페르소나에는 `[정해진 안내]` 블록으로 이어질 말만 쓰게 한다. 순서는 생성 직후 본문을 (400 − 안내문 길이 − 1)자로 자르기 → 잘린 본문으로 guard·stance_check(입장 판정도 잘린 본문 기준) → apply에서 안내문 부착이다. guard·입장 폴백과 비용 상한 안전 문구에도 붙이고, 카드 고정 한 줄(머뭇거림)에는 붙이지 않는다. 안내문은 화면 위치("결정 사항")와 방법만 말하고 어떤 안을 고를지는 말하지 않는다.
- 반복 재생성: 초안이 자기 최근 발언 3개 중 하나와 유사도 0.8 이상(공백·문장부호 제거 뒤 `SequenceMatcher`)이면 1회 다시 쓴다(`meta.repeat`, 최초 생성과 도구 3회 예산을 공유). 다시 써도 비슷하거나 실패하면 첫 초안을 쓴다. guard·stance_check는 그 뒤에 그대로 돈다.
- 비용 하드 상한($2, R5-11)을 넘은 세션(load가 읽은 DebateState `cost_left ≤ 0`, 원천은 `sessions.debate_cost_usd`)이면 respond는 LLM을 부르지 않고 자리별 고정 안전 문구를 낸다.
- 금지(모든 자리): 점수·평가·합격·연출될 상황 언급, 지원자 대신 결론 선언(칸 채우기 고정 문장 제외), 지원자 평가·판정, 카드 선점 금지 위반, 기획안 대필, `idea_credit` 내용 언급, 다른 자리의 성향 이름 언급. 페르소나는 assessment-mcp에 접근할 수 없다.

### 계획 밖 입장 (stance_check, R5-8)

- 검사: 구조화 출력의 `stance`·`proposes`가 `stance_plan`이 허용하는 안·방향과 같은지, 그리고 `content`가 계획과 다른 안을 지지하는 문장인지(의도 분류 모듈로 같은 형식의 판정을 받아 대조) 본다. 둘 중 하나라도 어긋나면 계획 밖이다. 자기 계획 안에 `oppose`를 내거나 그 안에 반대하는 문장도 계획 밖이다. 설득 가능 규칙으로 계획이 지원자 안으로 옮겨졌는데 그 자리의 현재 지지가 아직 계획 안이 아니면, 이번 발언은 그 안 지지를 밝혀야 하고(`stance` support·`proposes`·문장 판정 support 중 하나) 아니면 계획 밖이다(다른 안 `oppose`만 낸 경우 포함). 지지가 기록된 뒤에는 이 조건을 다시 걸지 않는다. 이 경우의 고정 문구는 이동을 드러내는 `stance_fallback.yaml`의 `moved`("듣고 보니 {option}이 더 낫겠네요. 저도 그쪽으로 옮길게요.", 설득 가능 자리만)다.
- 처리: 응답 **전체**를 같은 입력에 위반 사유를 붙여 1회 재생성한다. 그래도 어긋나면 계획과 맞는 자리별 고정 문구(시나리오 카탈로그)로 교체하고 입장도 계획 값으로 둔다. 메타데이터만 버리고 문장을 게시하는 일은 없다. 결과는 `messages.meta.stance_check`에 기록.

### 카드별 실행

카드 트리거는 **시각 기반 `card_due` 하나**다(R5-4, 카드별 `offset_min`, 최소 20초). 이벤트 트리거(지원자 첫 제안 직후 등)는 없다. 카드 전에도 페르소나는 토론에 참여하지만 카드가 정한 입장 표명은 카드 뒤에 한다.

| 카드 | 담당(고정 자리) | 그래프 동작 |
|---|---|---|
| `persuasion_keyword` | 그 시점 지원자와 다른 키워드를 민 자리 중 서지안 우선(`critic > stubborn`) | 자기 키워드 + 이유 하나 + 지원자 안 약점 질문. 지원자가 아직 제안하지 않았으면 전체에게 자기 키워드를 민다 |
| `flexibility_majority` | 강한결(`stubborn`, 확정 전 불변) + 서지안(`critic`, 설득 가능 규칙) | 카드 시점에 기준안을 잠그고(아래) 두 자리가 각자 기준안 지지를 밝힌다. 서지안은 지원자 반대·대안 2회면 지원자 안으로 옮길 수 있으므로 지원자 안이 채택될 수 있다(R1) |
| `listening_pressure` | 강한결 | 기준안을 강하게 밀고 지원자 안을 짧게 일축, 지원자가 반박하면 재반박(`pressure_count` 최대 3). 같은 화자가 `flexibility_majority`도 맡으므로 한 발언으로 합쳐 두 카드의 첫 발언을 겸할 수 있다. 그 메시지는 `card_ids`에 두 카드를 담고 두 카드의 `trigger_message_id`가 된다 |
| `inclusion_silence` | 윤하람(`quiet`) | 머뭇거림 한 줄(고정 골격). 이후 이름이 불리거나 전체 질문이 올 때까지 turn이 `quiet`를 고르지 않음. 불리면 자기 안을 자료 근거와 함께 |
| `persuasion_solution` | 서지안(그 시점 지원자가 서지안 안 S2를 지지하면 강한결) | 비용·실현 가능성 약점을 자료 근거로 짚고 담당 자신의 고정 방향(서지안 S2, 강한결 S1)을 민다. 담당만 바뀌고 입장은 바뀌지 않는다 |
| `honesty_credit` | 없음 | 발언이 없는 유일한 카드다. 기획안 단계의 `phase_started` 실행에서 cards 노드가 `record_observation`만 한다(`trigger_message_id`는 단계 시작 시스템 안내). 페르소나 행동 없음, 선점 금지(누가 냈는지 먼저 말하지 않음)만 강화 |

- **전달 보장(R5-4)**: 카드는 담당 화자가 실제로 게시했을 때만 그 화자를 `delivered`로 기록한다. 새 지원자 메시지(generation 변경)·입력 중 대기·합의 채우기 우선순위로 게시하지 못한 화자는 다음 run의 cards 노드가 이어서 넣는다. observation은 카드마다 첫 카드 발언 게시 뒤 한 번만 기록한다.
- **기준안 잠금(R5-4)**: 문제 상황 기준안은 `flexibility_majority` 카드 시점(`card_due`)에 "지원자가 그 순간 지지하는 안이 아닌 첫 시나리오 안"(`lead_option` → `alt_lead_option` → 다음 안 순)으로 계산해 잠근다. 잠근 뒤에는 바꾸지 않으며 고집형의 확정 전 불변과 충돌하지 않는다. 카드 전에는 고집형도 입장을 밝히지 않는다. 잠긴 기준안은 시간 초과 선택의 기준안으로도 쓰인다.
- 단계가 카드 시각 전에 끝나면 미발생(`event_not_fired`)이다.

### 고정 문장과 seq

`seq`는 노드별 고정 슬롯이다: 화자1 발언=1, 화자2 발언=2, 칸 채우기 공지=5(`fill_slot` 트랜잭션에서 api가 저장). api 시간 초과 공지는 run_id `uuid5(session, "timeout:"+phase)`·seq 0(api 소유). 같은 run_id 재실행은 초안을 다시 생성하고, 게시는 api의 (run_id, seq) 멱등이 기존 메시지를 돌려준다. 그래서 재실행에서 다시 만든 초안의 입장이 원 게시와 다를 수 있다(`LLM_FAKE=1`에서는 같다).

칸 채우기 공지 골격(시나리오 고정, 이름만 치환): consensus "그럼 X로 정리할게요." fill 노드가 LLM 없이 만들어 `fill_slot`의 `content`로 넘기고, api가 칸 확정과 같은 트랜잭션에서 저장한다. 칸 확정과 공지는 함께 남거나 함께 남지 않는다. 시간 초과 공지("시간이 다 돼서 대화에서 가장 많이 모인 의견인 X로 할게요.", R7)는 api가 게시한다.

### 끼어들기와 순서 (D17)

- 화자가 정해지면 `typing start` → 생성 → guard → stance_check → wait_typing → 게시 → `typing stop`. 둘째 화자는 1~3초 간격.
- **지원자가 입력 중이면 게시하지 않는다**(R5-9): typing은 클라이언트가 5초마다 갱신하고 hub가 6초 갱신이 없으면 만료한다. wait_typing은 최대 10초 기다리고, 그래도 입력 중이면 이번 자극을 다시 큐에 넣고 끝낸다(만든 문장은 버린다).
- 게시 직전 generation이 실행 시작 때와 다르면(새 지원자 메시지) 아직 말하지 않은 화자는 `typing stop` 후 버리고, 새 메시지 자극이 다시 정한다.
- 토큰 스트리밍 없음. 완성 문장 한 번에 게시.

### 재실행 멱등

- 같은 run_id가 `agent_runs`에서 `finished`면 api 메일박스가 다시 실행하지 않는다.
- 모든 변경은 run_id를 가지고 api가 멱등 키로 막는다(메시지 (run_id, seq), 안 (scenario_option_key∣source_message_id), 입장 (source_message_id, participant, option_id), 칸 (session, phase), 관찰 (session, card_id)).
- 같은 run_id의 `fill_slot` 재호출은 기존 결과(칸과 공지 메시지)를 돌려준다. 칸 확정과 공지가 한 트랜잭션이라 확정 뒤 공지만 빠지는 상태가 없다.
- LangGraph config 메타데이터 키는 `agent_run_id`(`run_id`를 쓰면 같은 run_id 재호출을 끝난 실행으로 보고 건너뛴다).

## 실시간 의도 분류 (`app/agent/intent/`)

D16·R4·R5-6. api 메일박스가 `message` 자극마다 들어온 순서대로 그래프 전에 호출한다. 그래프 노드가 아니다. 자극이 낡아(generation이 현재 값보다 작음) 그래프를 건너뛸 때도 분류와 안·지지 반영은 반드시 한다. 건너뛰는 것은 턴 선택·페르소나 발언 생성뿐이다.

- 모델 `MODEL_INTENT`, temperature 0, JSON 스키마 출력, 타임아웃 10초, 재시도 없음.
- 입력: 메시지, **저장된 `messages.phase`**(현재 단계가 아님), 그 단계의 **카탈로그 안 전체**(아직 발언되지 않은 것 포함, `scenario_option_key`·라벨), 이미 생긴 안 목록 `[{option_id, label}]`, 직전 메시지 5개, 자리 이름 목록.
- 메일박스가 밀린 사이 단계가 바뀌어도 분류와 안·입장 반영은 메시지의 단계에 한다. 현재 단계와 다른지는 턴 선택·발언 생성을 생략할지 판단하는 데만 쓴다.
- 출력: 계약의 의도 분류 스키마(`intent`, 대상 안(생긴 안이면 option_id, 아직 안 생긴 카탈로그 안이면 `scenario_option_key`), `new_option_label`, `addressed_to`, `confidence: high|low`). 허용 값 밖·없는 option_id·스키마 위반은 폴백 `{intent: other, ...}`, `fallback=true`.
- 쓰임: 안별 지지자 누적(high인 agree·propose만 지지), 카탈로그 안의 최초 제안자 귀속, 턴 매니저 규칙 3, 합의 판정, 침묵 초대 조건, 설득 가능 규칙, stance_check의 문장 판정. **평가 수준에 직접 쓰지 않는다.** 평가 에이전트에는 참고 입력(사실 지표 `intent_counts`, 메시지별 의도)으로만 가고, 원문과 다르면 원문을 따른다([product/rubric.md](../product/rubric.md) 1.2).
- 지원자 칸 확정 값의 안 연결에는 쓰지 않는다. 확정 경로에는 LLM이 없다(R5-5, [services/api.md](../services/api.md)).
- 실 모델 정확도 목표는 픽스처 20문장에서 0.85이고 최근 측정은 0.90이다(2026-10-22, gpt-4o-mini, [harness.md](harness.md) "실측 기준선"). MVP 통과 조건은 아니다.

## 어시스턴트 (`app/agent/assistant/`)

LangGraph가 아닌 **Python 도구 호출 루프**다(R5-13, 모델의 도구 선택 시연. `app/agent/assistant/runner.py` `AssistantGraphRunner`). 모델 `MODEL_ASSISTANT`. 이어지는 질문을 위한 대화 이력은 세션별 프로세스 메모리에만 있어 api를 재시작하면 비고, 요청·응답 원문은 `messages`(channel=assistant)와 `assistant_calls`에 남는다. 같은 세션의 요청은 세션별 asyncio Lock으로 한 번에 하나씩 처리한다.

```
agent(LLM, 도구 바인딩) ⇄ tools(search_materials·get_material) … 최대 3회 → answer → guard
```

- 모델이 질문을 보고 `search_materials`·`get_material` 호출 여부와 인자를 스스로 고른다. 도구 호출은 응답당 최대 3회, 넘으면 지금까지의 결과로 답한다.
- 열린 자료만 검색된다. 잠겨 있으면(`not_released`) "키워드가 정해지면 자료를 찾을 수 있어요"로 답한다.
- answer: 질문·요약·초안 도움. 인용한 자료 id를 `cited_material_ids`로 내고 본문에 출처 제목을 붙인다. 답 본문에서는 이번 응답이 받은 자료의 id 표기를 지우고, 남은 맨 id는 자료 제목으로 바꾼다(지원자에게 내부 id가 보이지 않게, `cited_material_ids`는 그대로). 도구 호출 내역은 `assistant_calls`에 사실로 기록한다. 토론방 대화·다른 참여자 정보는 보지 않는다.
- guard: 공통 금지 패턴. 60회 초과는 api가 429로 막아 그래프가 호출되지 않는다.

## guard (`app/agent/guard/`)

모든 LLM 생성 텍스트(페르소나·어시스턴트)에 코드에서 무조건 실행한다.
- 패턴(`positions.scenario`의 `guard_patterns`, 카탈로그 `prompts/guard_patterns.yaml`): 점수·합격·순위·평가·연출 언급, 평가 기준 질문 응답, 지원자 판정 표현("OO님은 경청을 잘하시네요" 류), 결론 대리 선언("이걸로 확정할게요" 류, 칸 채우기 공지 제외), 다른 자리 성향 이름, 미래 카드 암시.
- 처리: 걸리면 같은 입력으로 재생성 1회 → 그래도 걸리면 자리별 안전 문구(`prompts/safe_<type>.md`, 성향에 맞는 짧은 중립 문장)로 교체. 400자 초과는 자른다. 결과는 `messages.meta.guard`에 기록.
- 입력 쪽: 지원자 메시지 마스킹은 api가 저장 전에 한다.

## 프롬프트와 카탈로그 (`scenarios/missions/debate/prompts/`)

| 파일 | 내용 |
|---|---|
| `common_rules.md` | 모든 자리 공통: 동등한 팀원, 짧게 말하기, 시간은 남은 시간으로 말하기, 금지 목록, 기능 설명만 하고 평가 대상 행동을 유도하지 않음, 평가 질문 안내문 |
| `persona_<type>.md` | 성향·말투·행동 규칙·must_not([product/scenario.md](../product/scenario.md) 2.1). MVP에서 쓰는 것은 `stubborn`·`quiet`·`critic` 3개 |
| `turn_manager.md` | LLM 화자 선택 지침(규칙 6) |
| `intent.md` | 의도 분류 지침과 예시(`confidence` 기준 포함) |
| `assistant.md` | 어시스턴트 지침(도구 선택·인용 규칙) |
| `evaluator/common.md`, `evaluator/<element>.md` ×5 | 평가 에이전트 공통 지침(판단 순서·근거·출력 형식)과 요소별 특수 지침. 척도·앵커 본문은 [product/rubric.md](../product/rubric.md) 복사본을 `get_rubric`으로 주입 |
| `safe_<type>.md`, `stance_fallback`(자리·단계별 계획 일치 고정 문구), `guard_patterns.yaml` | 안전 문구, 계획 밖 입장 교체 문구, 금지 패턴 |
| `fixtures/` | 프롬프트 픽스처(유출·선점·판정 유도 질문, 계획 밖 입장 유도) |

프롬프트는 `catalog_version` 해시에 포함되고 본문이 `positions.scenario`에 복사돼 채용 건에 고정된다.

## 평가 에이전트 (report-worker, `report_worker/evaluate.py`)

D18·D19 개정과 R5-14. 세션 종료 후 report-worker의 `evaluate` 노드에서 돈다. api 안의 모듈이 아니지만 프롬프트·LLM 규칙을 이 문서가 정한다. 판단 규칙·앵커·검증 규칙의 정본은 [product/rubric.md](../product/rubric.md)(2장 공통 판단 규칙, 3장 척도, 4장 경계표, 7장 출력 형식), 저장 형식은 [contracts/internal.md](../contracts/internal.md) `save_evaluation`이다. 원칙: AI 평가 에이전트가 공개된 척도로 근거를 들어 수준을 판단하고, 담당자가 그것을 참고해 최종 평가한다. 합격·불합격·순위·총점은 만들지 않는다.

**호출 단위**: 요소마다 1회(5요소 고정이므로 기본 5회). 검증에 걸린 요소의 재요청 1회와, 검증으로 차원 행이 바뀐 요소의 summary 재작성 1회가 조건부로 더해진다(`evaluate.py`). 한 호출이 그 요소의 차원 3~4개를 모두 낸다. 다른 요소의 판단 결과·요약은 입력에 넣지 않는다(후광 방지). 모델 `MODEL_EVALUATOR`, temperature 0~0.2 **[초안]**, JSON 스키마 구조화 출력, 도구 바인딩 없음(입력은 워커가 미리 모은다).

**프롬프트 구성**(순서 고정):

| 부분 | 내용 | 원천 |
|---|---|---|
| 역할·원칙 | 지원자 행동만 판단, 실력(안의 질·자료 정확성·기획안 품질) 제외, 합격·순위·점수 표현 금지, 근거 없는 수준 금지 | `prompts/evaluator/common.md` |
| 판단 순서 | [product/rubric.md](../product/rubric.md) 2.1 순서(전체 읽기 → 장면 위치 → 기록 없음 검사 → 앵커 대조 → 보조 장면 ±1 → 근거 수 → 확신도) 그대로 | `prompts/evaluator/common.md` |
| 공통 규칙 | 근거 단위·최소 개수(1~3은 1개, 4·5는 서로 다른 2개), 기록 없음 사유 5종과 우선순위, 확신도 기준·상한, 편향 방지 표, 경계표 중 이 요소 관련 행(확정 행위는 `flex_decision_respect`, `flex_acceptance`는 결정 후 행동만 — 결정 후 발언이 없으면 `no_activity`, R5-17) | `get_rubric` |
| 척도 주입 | 이 요소의 차원마다 id·정의·핵심 장면(카드 창)·**수준 1~5 앵커와 예시 발언**·요소별 특수 지침(수용 차원은 "근거 있는 이의 제기도 높은 수준", 지원자 안이 채택되면 수용 차원은 `not_applicable`, R1) | `get_rubric`(세션에 복사된 척도, `rubric_version`) |
| 세션 입력 | **대화 전체**(메시지 id·자리·오피스 시각·단계·origin·의도), 칸 기록(지지 현황 스냅샷), 기획안 제출본, 카드 발생 기록과 창 시각, 사실 지표, Contributions, 자리 → 유형 대응. 카드 창으로 자르지 않는다 | `get_session_record` |
| 출력 지시 | 아래 스키마, 차원마다 정확히 한 행, 근거 참조는 `msg:<id>`·`slot:<phase>`·`proposal:<field>`·`contribution:<option_id>` 문자열만 | `prompts/evaluator/common.md` |

**출력 JSON 스키마**([product/rubric.md](../product/rubric.md) 7.1·7.2, 저장 형식은 [contracts/README.md](../contracts/README.md)):

```json
{"element": "listening",
 "summary": "1~2줄 서술",
 "dimensions": [
   {"dimension_id": "listen_engage", "level": 4, "no_record_reason": null,
    "evidence_refs": ["msg:<id>", "msg:<id>"], "rationale": "1~3문장", "confidence": "high"}
 ]}
```

- `level`은 1~5 정수 또는 null. null이면 `no_record_reason`(`no_activity`·`not_applicable`·`event_not_fired`·`auto_submitted_blank`; `evaluate_failed`는 워커만 쓴다)과 `confidence: null`.
- 요소 `level`은 모델이 내지 않는다. 워커가 차원 평균 반올림(.5 올림)으로 계산한다. 모델은 `summary`만 쓴다.
- 검증 경계(R5-14, 정본 [product/rubric.md](../product/rubric.md) 7.3): 형식 오류(스키마 위반·차원 누락·중복·금지어)나 근거 부재(수준이 있는데 지원자 근거 단위 0개)면 **그 요소 호출 전체를 1회 재요청**한다(차원별 실패 사유를 붙여서). 그래도 남은 실패 차원은 워커가 `evaluate_failed`로 둔다. 수준 4·5에 지원자 근거 단위가 1개뿐이면 거부하지 않고 수준 3·확신도 최대 medium으로 깎고, 확신도 상한 위반도 워커가 상한으로 깎는다(clamp). LLM 전송 오류·타임아웃은 저장하지 않고 큐 재시도로 넘긴다.

**버전**: 평가 프롬프트 파일(`prompts/evaluator/common.md`와 `prompts/evaluator/<element>.md` 5개)마다 개별 해시를 `prompt_versions` 맵(형식은 [contracts/internal.md](../contracts/internal.md) EvaluationSave)으로, 세션 척도 버전을 `rubric_version`으로, 실제 모델 id를 `model`로 `save_evaluation`에 함께 저장한다(api가 `evaluation_runs`에 보관). 단일 `prompt_version` 필드는 쓰지 않는다.

**평가 프롬프트 고정**: 채용 건 생성 때 api `scenario.assemble`이 카탈로그 `prompts/` 전체를 `positions.scenario.prompts`에 복사한다(따로 둔 열 없음). 워커는 내부 `GET /internal/sessions/{id}`의 `position.scenario.prompts`에서 `"evaluator/*.md"`(common과 5요소)와 `"guard_patterns.yaml"`의 `evaluator_forbidden`을 읽고, `prompt_versions` 해시(키 `{common, persuasion, listening, inclusion, flexibility, honesty}`)도 이 복사본 내용으로 계산한다. draft의 금지어 검사도 같은 복사본을 쓴다. 복사본에 평가 파일이나 `evaluator_forbidden`이 하나라도 없으면 현재 파일로 채우지 않고 `PinnedCopyMissing`(채용 건 id 포함)으로 실패한다. 재시도해도 같으므로 백오프·재발행 없이 바로 `report.dlq`로 보내 리포트 실패(`failed_stage=report`)가 된다.

**리포트 요약 문장과의 관계**: 요소 서술(`elements[].summary`)은 이 평가 호출이 쓰고 draft에서 다시 만들지 않는다. 리포트의 카드 장면(`cards[].scene`)은 LLM 없이 카드의 고정 문장 `report_template`을 그대로 쓴다. draft 단계에는 LLM 호출이 없다. 리포트용 요약 호출은 검증으로 차원 행이 바뀐 요소의 summary 재작성 1회뿐이다. 평가 호출이 실패해 요소의 모든 차원이 `evaluate_failed`면 요소 서술은 템플릿 고정 문장으로 대신하고, 차원 수준을 템플릿이나 다른 모델로 메우지 않는다.

## 역할별 모델

| 역할 | env | 쓰는 곳 |
|---|---|---|
| 페르소나 | `MODEL_PERSONA` | respond |
| 턴 매니저 | `MODEL_TURN` | turn 규칙 6(규칙 결과 0명 + message 자극일 때만) |
| 의도 분류 | `MODEL_INTENT` | intent, stance_check 문장 판정 |
| 어시스턴트 | `MODEL_ASSISTANT` | assistant tool-calling 루프 |
| 평가 에이전트 | `MODEL_EVALUATOR` | report-worker 평가 에이전트(요소별 차원 수준·근거·요약). 카드 장면 `cards[].scene`은 고정 문장이라 LLM을 쓰지 않는다 |

비어 있으면 `OPENAI_MODEL`(기본 gpt-4o-mini). 키는 모델 id로 고른다(gpt-4o-mini → 제한 키, 그 외 gpt-* → 개인 키, gemini-* → Gemini 키). 규칙은 공용 LLM 래퍼(`app/agent/llm`, 워커는 같은 규칙의 사본) 한 곳이고, 래퍼가 호출마다 토큰과 환산 달러를 `usage.cost_usd`로 낸다(R5-11). 프롬프트·규칙 수정으로 안 되는 역할만 사용자 결정으로 올린다(`docs/decisions.md`). 역할별 타임아웃·재시도·temperature는 [harness.md](harness.md) "역할별 모델" 표가 정본이다.

### 결정적 LLM 모드 (`LLM_FAKE=1`)

실 스택(compose·로컬)에서 그래프·의도 분류·어시스턴트·평가 에이전트를 **그대로 돌리고 LLM 호출만** 결정적 고정 응답으로 바꾸는 모드다. 단계별 화면 테스트·단계 시드·e2e mock 실행이 쓴다([guides/testing.md](../guides/testing.md) "단계별 테스트"). 공용 LLM 래퍼(`app/agent/llm`, 워커는 같은 규칙의 사본)가 env를 보고 `FakeLLM`을 돌려주므로 노드 코드는 모드를 모른다.

| 역할 | 고정 응답 |
|---|---|
| 페르소나 | 그 자리·그 단계의 시나리오 고정 입장 문장(`personas.yaml` 단계별 입장 + 안 라벨 치환). 카드 발언은 카드 `content`. guard·stance_check를 통과하는 문장만 쓴다 |
| 턴 LLM | 규칙 결과 0명일 때 우선순위 첫 자리 |
| 의도 분류 | 키워드 규칙: 카탈로그 안 라벨·키워드 라벨이 문장에 있으면 그 안, "좋아요·동의·찬성"이면 agree, "아니·반대·글쎄"면 oppose, 물음표면 question, 그 밖 other. confidence는 규칙이 맞으면 high, 아니면 low |
| 어시스턴트 | 도구 1회(`search_materials` 질문 그대로) 뒤 첫 결과 제목을 인용한 고정 문장 |
| 평가 에이전트 | 요소마다 형식 검증을 통과하는 고정 출력(차원마다 지원자 메시지 근거가 있으면 수준 3·confidence medium, 없으면 `no_activity`) |

사용량은 0으로 기록하고 LLM 호출 trace는 남기지 않는다(`LANGCHAIN_TRACING_V2`가 켜져 있으면 그래프 실행 trace는 남는다). 운영 compose에서는 끈다. 실제 LLM 확인은 같은 실행을 `LLM_FAKE` 없이 다시 돌린다(`make stage … LLM=live`).

### 측정 기준선

지연·비용·의도 분류 정확도 기준선은 [harness.md](harness.md) "실측 기준선".


## 복구

[architecture/harness.md](harness.md) Recovery 행. 그래프는 `last_error`에 기록하고 `fallback_used=True`를 돌려준다. 카운터와 세션 failed 전이는 api `SessionService`.
- 페르소나 발언 실패(LLM 재시도 2회 후): 그 발언 생략, `llm_failures+1`(연속 3회면 세션 failed, 성공 시 0). 카드 발언이면 `delivered`가 아니므로 다음 run이 이어서 말한다.
- turn LLM 실패: 0명.
- 의도 분류 실패: other.
- 시간 초과: 그래프와 무관하게 api가 결정적으로 채운다.
- MCP upstream_unavailable: 1회 재시도, 그래도 실패면 그 변경을 건너뛰고 실행 failed(스케줄러 재실행).

## 검증 항목

가짜 MCP·가짜 hub(SessionPorts 가짜 구현) 위 단위 테스트가 확인하는 항목이다. WS·재시작은 e2e와 단계별 화면 테스트가 확인한다.

- 최소 흐름: 지원자 메시지 1개 → 의도 분류 → 토론 그래프(turn이 지명된 자리 선택) → respond → guard → stance_check → apply(post_message·set_stance) → 가짜 hub에 `typing` start/stop(FakeAgentRunner 교체).
- 15분 세션 대본(가짜 시계): 단계 시작마다 고정 입장 화자가 말함(문제 상황은 카드 전 입장 표명 없음), 합의 3/4 즉시 지지자 자리의 `fill_slot` 한 번으로 칸과 공지(seq 5)가 함께 저장, 이미 합의가 있는 자극에서 respond·apply 호출 0회·공지용 `post_message` 0건, 이번 AI 발언으로 3/4가 되면 같은 run에서 채움, `fill_slot` 409면 칸·공지 0건, 같은 run_id 재실행은 기존 결과, 합의 전에는 칸을 채우지 않음(409 `no_consensus` 흐름 포함), 그래프가 timeout 채우기를 하지 않음, 카드 6장이 `card_due`로 발동하고 `record_observation`이 첫 카드 발언 게시 뒤 1회씩.
- 카드 전달: 카드 화자 중 일부가 generation 변경으로 버려지면 다음 run(새 run_id의 card_due 포함)에서 그 화자가 이어서 말하고 observation은 중복되지 않음, 강한결의 공동 발언이 `card_ids` 두 개로 두 카드를 함께 전달하고 관찰 2건, 기준안이 card_due 시점의 지원자 지지 안과 다르게 잠기고 이후 바뀌지 않음.
- 턴 규칙: 한 실행 화자 ≤ 2, 지명 응답 우선, `quiet`가 불리기 전 발언하지 않음(머뭇거림 카드 제외), 침묵 초대가 단계당 1회·서지안·`is_invite=true`, 턴 LLM이 규칙 0명 + message일 때만 호출됨.
- typing·generation: 지원자 입력 중이면 10초 뒤에도 게시 0건이고 자극이 다시 enqueue됨, 게시 직전 generation 변경 시 남은 화자 버림.
- 입장: 설득 가능 규칙(반대·대안 두 번에 단계당 1회 이동), `stubborn` 확정 전 불변, 지원자가 S2를 지지하면 `persuasion_solution`을 강한결이 S1로 맡고 서지안 입장은 S2 그대로, 계획 밖 입장 픽스처(자기 계획 안 `oppose` 포함)가 재생성 → 고정 문구로 바뀌고 게시 문장·저장 입장이 계획과 일치.
- 경계: 페르소나 노드의 MCP 클라이언트에 assessment 도구가 없고 호출 시 거부, 키워드 확정 전 자료 질문에 `not_released` 처리, 페르소나 입력에 `cards_fired`·카드 진행·관찰·평가표·성향 이름·어시스턴트 대화가 없음(컨텍스트 스냅샷 검사), 지원자를 표시 이름으로 부름.
- guard: 판정·결론 대리·점수 언급 픽스처가 재생성 → 안전 문구로 바뀜.
- 비용: 하드 상한 초과 주입 시 respond LLM 호출 0, 안전 문구만.
- 어시스턴트: 질문 픽스처에서 모델이 도구를 골라 호출(최대 3회), 응답에 `cited_material_ids`, 잠금 시 안내 문장.
- 복구: 강제 LLM 실패 3회 연속에 세션 failed, 같은 run_id 재실행에 중복 메시지·안·입장 없음.
- 의도 분류: 픽스처 20문장에서 스키마 위반 0과 실 모델 정확도 기준선 기록, `confidence` 출력, 단계 전환 뒤 꺼낸 이전 단계 메시지가 그 메시지 단계의 카탈로그로 분류·저장됨, 카탈로그 안을 지원자가 먼저 말하면 `scenario_option_key`로 판정, 실패 주입 시 other·fallback=true.
- LangSmith trace에 `agent_run_id`, `agent_runs.decision`에 턴 결정 저장.
- 결정적 LLM 모드: `LLM_FAKE=1`에서 같은 단계 시드·같은 지원자 입력을 두 번 돌리면 게시 문장·안·입장·칸 결과가 같고 LLM 실제 호출 0회, `AGENT_RUNNER` 기본값이 config와 runner 선택에서 `session`으로 같다.
- 평가 에이전트: 픽스처 세션에서 검증에 걸리지 않으면 요소마다 호출 1회, 프롬프트 스냅샷에 대화 전체·그 요소 척도와 앵커만 있고 다른 요소 결과가 없음, 출력 스키마 위반 0, 근거 참조가 4종 문자열 형식, 모델이 요소 `level`을 내지 않음, 형식 오류 주입 시 요소 단위 재요청 1회, 확신도 상한 위반은 clamp. 같은 세션 3회 재평가의 차원 수준 변동 폭 기록(R7, [product/rubric.md](../product/rubric.md) 9.2).
