# 내부 계약 (포트 8001, 평가 저장, 에이전트 스키마)

음성 턴·최종 파일·재생 확인·message.send 확장은 [음성 계약](voice.md)을 따른다.

MCP 서버·report-worker·토론 그래프가 쓰는 내부 REST와 평가 저장 형식, 에이전트 내부 스키마다. 공통 규칙은 [계약 README](README.md).

## 내부 REST (`/internal`, 포트 8001, X-Internal-Token)

| 메서드 | 경로 | 요청 | 응답 | 호출자 |
|---|---|---|---|---|
| GET | /internal/sessions/{id} | | {id, status, phase, alias, display_name, position: {id, name, mission_id, session_minutes, scenario, catalog_version}, seats: [{seat: "candidate", display_name}, {seat, display_name, avatar_url, persona_type}], started_at, ends_at, phase_started_at, phase_deadline_at, completed_at, ended_at, t_min_now, generation, invite_delivered_phases, card_progress, connection_gaps} | debate-mcp, assessment-mcp, 워커 |
| GET | /internal/sessions/{id}/debate_state | | DebateState(아래) | debate-mcp |
| GET | /internal/sessions/{id}/messages?since_t_min=&phase=&limit=&offset=&tail= | | [Message + `meta`, `origin`, `intent`(message_intents 행 또는 null)]. 토론방(room) 메시지만. 정렬 created_at·seq, limit 기본 200·최대 1000. `tail=N`(1~1000)이면 최근 N개를 오래된 순으로 주고 limit·offset은 무시한다(debate-mcp `get_transcript`의 `tail`) | debate-mcp, assessment-mcp, 워커 |
| POST | /internal/sessions/{id}/messages | {sender: p1∣p2∣p3∣system, content, run_id, seq, expected_generation?: int, is_invite?: bool, card_ids?: [card_id], cited_material_ids?: [], meta?} | Message. (run_id, seq) 멱등(이미 있으면 generation 검사 없이 기존 행). `expected_generation`이 있고 현재 `sessions.generation`과 다르면 409 `stale_generation`(게시 안 함). is_invite=true면 그 단계를 `invite_delivered_phases`에 추가, 단계당 두 번째는 409 `conflict`. `card_ids`의 카드마다 그 `session_cards.delivered_seats`에 sender 자리를 더한다(같은 자리 중복 없음, 같은 트랜잭션). 메시지 행에는 `card_ids`로 저장 | debate-mcp |
| POST | /internal/sessions/{id}/options | {phase, label, proposer: p1∣p2∣p3, scenario_option_key?, source_message_id, run_id} | Option {option_id, phase, label, proposer, scenario_option_key, created_at, existing: bool}. 멱등 키는 위 표. 같은 scenario_option_key 행이 이미 있으면(지원자가 먼저 말한 경우 포함) 새로 만들지 않고 기존 행(`existing: true`, proposer 그대로). 현재 단계가 아니면 409 `phase_not_open` | debate-mcp |
| POST | /internal/sessions/{id}/stances | {participant: p1∣p2∣p3, option_id, stance: "support"∣"oppose", source_message_id, run_id} | {stance_id}. source_message_id는 그 입장을 말한 자기 메시지. 멱등 | debate-mcp |
| POST | /internal/sessions/{id}/slots/fill | {option_id, seat: p1∣p2∣p3, content, run_id} | {slot, message, phase: <다음 단계>}. **AI 합의 채우기 전용**(`resolution: consensus`, `filled_by` = seat). 대상 단계는 그 option의 phase다. 판정 순서: 같은 run_id로 이미 확정된 칸이 있으면 단계와 상관없이 기존 칸·공지(200) → option의 phase가 현재 단계가 아니면 409 `phase_not_open` → 칸이 이미 confirmed면 409 `slot_confirmed` → `consensus.judge` 결과가 이 option_id가 아니면 409 `no_consensus`. 성공은 한 트랜잭션: 칸 confirmed → `content`를 공지 메시지로 저장(sender = seat, run_id, seq 5, `meta.kind = "consensus_fill"`) → `advance_phase`(generation +1). 커밋 뒤 WS `message.new` → `slot.confirmed` → `phase.changed` → `timer`(keyword면 `materials.opened`). 409면 아무것도 저장하지 않는다. 시간 초과 채우기는 api 스케줄러가 직접 하므로 이 경로가 없다 | debate-mcp |
| GET | /internal/sessions/{id}/proposal | | Proposal(in_progress면 현재 초안, completed 이후면 제출본. `submitted_at`·`auto_submitted` 포함) | debate-mcp, assessment-mcp, 워커 |
| GET | /internal/sessions/{id}/materials | | {catalog_version, locked, keyword_key∣null, materials: [{material_id, title}]} | rag-mcp |
| GET | /internal/sessions/{id}/materials/{material_id} | | {material_id, title, content} 또는 423 `not_released`. 채용 건 `catalog_version`의 `materials` 행 | rag-mcp |
| POST | /internal/sessions/{id}/observations | {card_id, phase, trigger_message_id?, run_id} | {observation_id, fired_at, t_min}. (session_id, card_id) 멱등. 카드의 첫 발언 게시 뒤(`trigger_message_id` = 그 메시지), `honesty_credit`은 기획안 단계 `phase_started`에서(`trigger_message_id` = 단계 시작 시스템 안내). element는 api가 카드에서 정한다 | assessment-mcp |
| GET | /internal/sessions/{id}/observations | | [{observation_id, card_id, element, phase, fired_at, t_min, trigger_message_id, delivered: bool}] (`delivered`는 `session_cards` 기준) | assessment-mcp, 워커 |
| GET | /internal/sessions/{id}/transcript_span?message_id=&before=5&after=3 | | {target: Message, before: [Message], after: [Message]}. room 메시지만, meta·intent 포함. 기본 앞 5·뒤 3. 리포트 근거의 앞뒤 맥락 표시용(평가 입력을 자르는 용도가 아님) | assessment-mcp |
| GET | /internal/sessions/{id}/contributions | | Contributions(아래) | assessment-mcp |
| GET | /internal/sessions/{id}/rubric | | {rubric_version, elements: [{element, name, dimensions: [{dimension_id, name, definition, core_cards: [card_id], levels: [{level: 1~5, anchor, reviewer_hint}]}]}], no_record_reasons: [{code, label}], cards: [{card_id, element, stage_id, window: {start: stage_start∣card_message, end: "stage_end:<phase>"∣submit, label}}]} (`positions.scenario`에 복사된 BARS 척도, 5요소·19차원, 형식은 [product/rubric.md](../product/rubric.md). `window`는 `cards.yaml` 값 그대로이며 관찰 창이지 평가 입력을 자르는 경계가 아니다) | assessment-mcp |
| POST | /internal/sessions/{id}/evaluations | `save_evaluation` 입력에서 session_id를 뺀 본문(아래 EvaluationSave) | {run_id, existing: bool, saved: int}. `evaluation_runs` 1행 + `evaluations` 차원 행을 **한 트랜잭션**으로 저장. (session_id, run_id)에 행이 있으면 저장하지 않고 `existing: true`(재시도 이어하기). api 검증 실패 422 `invalid_input`(아래 "검증 경계") | assessment-mcp |
| GET | /internal/sessions/{id}/evaluations?run_id= | | {run_id, rubric_version, prompt_versions, model, elements: [ElementSummary], dimensions: [DimensionEvaluation], has_failed: bool, created_at}. run_id 생략 시 최신 실행. 요소 요약이 함께 오므로 저장 직후 장애가 나도 재실행이 체크포인트 없이 복구한다 | 워커 |
| GET | /internal/sessions/{id}/facts | | {<지표 id>: 값} ([product/rubric.md](../product/rubric.md) 6장 사실 지표의 규칙 계산 값. 평가 결과를 쓰지 않음) | assessment-mcp, 워커 |
| GET | /internal/sessions/{id}/slots | | [slot 전체 필드 + support] | assessment-mcp, 워커 |
| GET | /internal/sessions/{id}/assistant_calls | | [{id, request_message_id, response_message_id, request, response, cited_material_ids, created_at}]. `request`·`response`는 어시스턴트 대화 원문(text)이고 워커가 Report `ai_usage`의 `request_quote`·`response_quote`를 여기서 만든다(내부 messages는 토론방만 준다) | 워커 |
| POST | /internal/sessions/{id}/report/draft | {content, usage?} | {status: "report_ready"} (멱등) | 워커 |
| POST | /internal/sessions/{id}/report/final | {content} | {status: "reviewed", reviewed_at}. content는 승인된 draft content 그대로(조정 병합하지 않음). 해시가 draft와 다르면 409 `draft_mismatch` | 워커 |
| POST | /internal/sessions/{id}/report_failed | {error} | {status: "failed"} | 워커 |
| POST | /internal/test/sessions | {session_minutes: 60∣15, alias?, company_id?, admin_email?, start_at?: entry∣keyword∣problem∣solution∣proposal∣ended, stage?} | {session_id, token, invite_url, phase, session_token?, debrief_token?, admin_report_url?}. `start_at` 기본 `entry`(초대만 만든 상태). 그 밖이면 단계 시드 `scenarios/e2e/stages/<stage∣start_at>.yaml`의 행동을 SessionService로 재생해(가짜 시계, LLM 없음) 그 단계가 막 시작된 in_progress 세션을 만들고 `session_token`을 준다. `ended`는 제출까지 재생한 completed 세션이고 `debrief_token`·`admin_report_url`을 준다(리포트는 워커가 평소대로 만든다). 단계 마감은 재생이 끝난 시각 기준으로 다시 잡는다 | e2e 러너, 단계 시드(`make stage`) |
| POST | /internal/test/sessions/{id}/advance | {seconds?: int, to?: deadline∣idle∣"card:<card_id>"} | {shifted_seconds, phase, phase_deadline_at, ends_at}. in_progress 세션의 `started_at`·`phase_started_at`·`phase_deadline_at`·`ends_at`을 같은 초만큼 앞당겨 시간이 흐른 것처럼 만든다(스키마 변경 없음, 카드 예정 시각은 시작 시각 기준이라 함께 당겨짐). `to`는 그 시점에 닿는 초를 api가 계산한다(`deadline` = 현재 단계 마감, `idle` = 침묵 초대 조건, `card:<id>` = 그 카드 예정 시각). 처리는 다음 스케줄러 틱이 평소대로 한다. 둘 다 없거나 둘 다 있으면 422 | e2e 러너, 단계 시드(`make stage`) |

`/internal/test/*`는 `ENABLE_TEST_API=1`일 때만 열리고 아니면 404다(운영 compose에서는 끈다). 내부 읽기는 상태 제한이 없다. 내부 쓰기는 세션이 in_progress일 때만 받고 아니면 409 `conflict`(이미 저장된 키의 재전달은 기존 결과). 단, `evaluations`·`report/*`는 completed 이후에만 받는다.

### DebateState

```json
{"phase":"problem","generation":12,"phase_deadline_at":"...","phase_remaining_seconds":840,
 "slot":{"phase":"problem","status":"empty","value":null},
 "options":[{"option_id":"...","label":"도심 폭우·침수 피해 증가","proposer":"p3","scenario_option_key":"water_p2","created_at":"...",
             "supporters":["p1","p3"],"opposers":["candidate"]}],
 "catalog_options":[{"scenario_option_key":"water_p1","label":"...","spoken":false}],
 "current_support":{"candidate":"...","p1":"...","p2":null,"p3":"..."},
 "consensus_option_id":null,"timeout_option_id":"...",
 "candidate":{"spoke_in_phase":true,"last_message_t_min":11.2,"typing":false},
 "invite_delivered":false,"last_room_message_t_min":11.4,
 "confirmed":[{"phase":"keyword","value":"물","option_id":"...","resolution":"consensus","filled_by":"candidate"}],
 "card_progress":[{"card_id":"<card_id>","locked_option_id":"...","delivered":["p1"]}],
 "cost_left":1.79}
```

`consensus_option_id`는 합의 판정 결과(없으면 null), `timeout_option_id`는 지금 마감되면 고를 안. 턴 매니저는 이 값을 그대로 쓴다. `card_progress`는 `session_cards` 행(처리가 시작된 카드)의 잠긴 기준안과 전달된 자리이고, 결정적 노드가 아직 전달되지 않은 담당 화자를 다음 run의 화자로 이어서 넣는다. **페르소나 컨텍스트에는 `card_progress`·`cost_left`를 넣지 않는다**(카드 brief와 자기 입장 계획만 그 화자에게). `cost_left` = `SESSION_COST_HARD_USD`(기본 $2) − `sessions.debate_cost_usd`(리포트 비용 제외, `services/budget.py`가 계산)이고 0 이하면 페르소나는 LLM 없이 고정 안전 문구만 게시한다. 그래프 `load`는 위 필드를 걸러 낸 DebateState를 페르소나 컨텍스트에 미리 넣어 조회 도구 왕복을 줄인다.

### Contributions

```json
{"options":[{"option_id":"...","phase":"problem","label":"도심 폭우·침수 피해 증가","first_proposer":"p2","source_message_id":"...","t_min":12.3,"adopted":true}],
 "slots":[{"phase":"problem","option_id":"...","first_proposer":"p2","filled_by":"candidate","resolution":"consensus"}]}
```

최초 제안자 = 그 안 행의 proposer(안 행은 처음 말한 순간 한 번만 만들어진다). 정직 대조의 근거.

### SessionRecord (`get_session_record` 출력)

평가 에이전트의 입력 묶음이다. **세션 대화 전체**를 담고 카드 관찰 창으로 자르지 않는다([product/rubric.md](../product/rubric.md) 1.1).

```json
{"session":{"id":"...","elements":["persuasion","listening","inclusion","flexibility","honesty"],"session_minutes":60,"started_at":"...","completed_at":"...","connection_gaps":[]},
 "seats":[{"seat":"candidate","display_name":"김지원"},{"seat":"p3","display_name":"서지안","persona_type":"critic"}],
 "messages":[{"id":"...","sender":"candidate","content":"...","phase":"problem","t_min":14.2,"origin":"spontaneous","is_invite":false,"card_ids":[],
              "intent":{"intent":"oppose","target_option_id":"...","addressed_to":["p2"]}}],
 "slots":[{"phase":"problem","value":"...","option_id":"...","filled_by":"candidate","resolution":"consensus","support":[],"t_min":28.0}],
 "proposal":{"sections":{"idea_credit":"..."},"submitted_at":"...","auto_submitted":false},
 "observations":[{"card_id":"listening_pressure","element":"listening","phase":"problem","t_min":13.0,"trigger_message_id":"...","delivered":true,"window":{"start_min":13.0,"end_min":28.0}}],
 "facts":{"message_count":{"total":24}},
 "contributions":{"options":[],"slots":[]}}
```

- `messages`는 room 채널 전체(시간순), 지원자 메시지에만 `origin`·`intent`. 어시스턴트 채널은 넣지 않고 `facts.assistant_calls`로만 보인다.
- `observations[].window`는 카드의 `window` 규칙([product/scenario.md](../product/scenario.md) 4.2)으로 계산한 핵심 장면 위치다. 입력 경계가 아니다. `trigger_message_id`는 카드 첫 발언(장면을 찾는 용도), `delivered`는 담당 화자 전원 게시 여부.
- `seats[].persona_type`은 장면을 찾는 용도로만 넣는다(관리자·워커 전용).

### EvaluationSave (`save_evaluation` 입력)

예시는 발췌다(실제 본문은 19개 차원 행과 5개 요소 행).

```json
{"session_id":"...","run_id":"...","rubric_version":"...","prompt_versions":{"common":"<hash>","persuasion":"<hash>","listening":"<hash>","inclusion":"<hash>","flexibility":"<hash>","honesty":"<hash>"},"model":"...",
 "dimensions":[{"dimension_id":"listen_engage","level":4,"evidence_refs":["msg:<m1>","msg:<m2>","msg:<m0>"],
                "rationale":"세 번의 압박 중 두 번은 상대 근거를 받아 답함(앵커 4).","confidence":"high"},
               {"dimension_id":"listen_civility","level":null,"no_record_reason":"no_activity","evidence_refs":[],
                "rationale":"핵심 창 안 지원자 메시지 없음.","confidence":null}],
 "elements":[{"element":"listening","level":4,"summary":"...","dimension_count":4,"no_record_count":1}]}
```

평가 에이전트의 요소 하나 출력 예시(자리표시 값, 요소 level = (4+3+4)/3 = 3.67 → 4, `no_record_count` = 1):

```json
{"element":"listening","level":4,"summary":"압박에 대체로 내용으로 응답하고 차분했으나 상대 근거 인정은 형식적이었습니다. 이해 확인 차원은 판단할 장면이 없었습니다.",
 "dimension_count":4,"no_record_count":1,
 "dimensions":[
  {"dimension_id":"listen_engage","level":4,"evidence_refs":["msg:<m1>","msg:<m2>","msg:<m0>"],
   "rationale":"세 번의 압박 중 두 번은 상대 근거를 받아 답함(앵커 4). 보조 장면과 같은 방향.","confidence":"high"},
  {"dimension_id":"listen_understand","level":null,"no_record_reason":"not_applicable","evidence_refs":[],
   "rationale":"창 안 메시지가 모두 다른 참여자·주제를 향해 이해 확인을 볼 장면이 없음.","confidence":null},
  {"dimension_id":"listen_acknowledge","level":3,"evidence_refs":["msg:<m2>"],
   "rationale":"인정이 '좋은 의견인데요' 수준의 형식적 표현(앵커 3).","confidence":"medium"},
  {"dimension_id":"listen_civility","level":4,"evidence_refs":["msg:<m1>","msg:<m3>"],
   "rationale":"반대가 일관되게 안을 향하고 정중함(앵커 4).","confidence":"high"}]}
```

| 필드 | 값 |
|---|---|
| run_id | 평가 실행 id. 워커가 `uuid5(session_id, "evaluate:" + eval_round)`로 정한다. `eval_round`는 큐 메시지 `report.generate`의 값(첫 발행 1). 큐 재시도는 같은 round라 같은 평가를 재사용한다. 리포트 재생성 때 api는 최신 `evaluation_runs.has_failed`가 true면 `sessions.eval_round`를 +1(새 run_id로 다시 평가), 아니면 그대로(재사용) 두고 그 값으로 발행한다. 파일럿 재실행 안정성 측정만 별도 run_id를 쓴다 |
| rubric_version, prompt_versions, model | 척도 버전(`get_rubric`), 평가 프롬프트 버전 맵 `{common: prompts/evaluator/common.md 해시, <element>: prompts/evaluator/<element>.md 해시}`(5요소 모두). 해시는 채용 건 복사본 `positions.scenario.prompts[\"evaluator/<file>.md\"]` 내용으로 계산한다(복사본에 평가 파일이 없으면 현재 파일로 채우지 않고 평가를 실패시킨다), `MODEL_EVALUATOR` 모델 이름. 단일 `prompt_version` 필드는 없다 |
| dimensions[] (DimensionEvaluation) | `dimension_id`(19개 차원마다 정확히 한 행) · `level` 1~5 ∣ null · `no_record_reason`(level null일 때만): `no_activity` ∣ `evaluate_failed` ∣ `not_applicable` ∣ `event_not_fired` ∣ `auto_submitted_blank` · `evidence_refs[]`([api.md](api.md) "근거 참조" 형식) · `rationale` 1~3문장 · `confidence` `high`∣`medium`∣`low`, 기록 없음이면 null |
| elements[] (ElementSummary) | `element` · `level`(워커가 차원 평균 반올림, .5 올림, 모두 기록 없음이면 null) · `summary`(평가 에이전트 1~2줄) · `dimension_count` · `no_record_count` |

**검증 경계** (항목마다 실행 주체 하나):

| 검증 | 주체 | 실패 시 |
|---|---|---|
| 본문 스키마·타입, 19개 차원이 빠짐없이 한 행씩(중복·없는 id 없음), level 1~5와 `no_record_reason` 배타·사유 코드 값, 기록 없음이면 confidence null, 요소 5행 | api(저장 전) | 422 `invalid_input`, 저장 안 함 |
| 모든 `evidence_refs`가 형식 4종이고 **같은 세션의 실제 행** | api(저장 전) | 422 `invalid_input` |
| LLM 출력 형식 오류(파싱·스키마, 위 api 항목과 같은 규칙을 저장 전에 미리 봄), level이 있는데 지원자 근거 단위 0개([product/rubric.md](../product/rubric.md) 2.2) | 워커(저장 전) | 그 **요소**를 차원별 거부 사유와 함께 1회 다시 요청 → 그래도 실패한 차원만 `evaluate_failed`(`rationale`에 사유 코드) |
| level 4·5인데 서로 다른 지원자 근거 단위 1개([product/rubric.md](../product/rubric.md) 2.2) | 워커 | 거부하지 않고 level 3·confidence 최대 medium으로 깎는다, `rationale` 끝에 상한 적용 표시 |
| 확신도 상한([product/rubric.md](../product/rubric.md) 2.4) | 워커 | 거부하지 않고 상한으로 깎는다(clamp), `rationale`은 그대로 |
| 핵심 카드가 하나도 발동하지 않았는데 `rationale`에 "보조 장면" 표시 없이 낸 수준([product/rubric.md](../product/rubric.md) 2.4) | 워커 | 거부하지 않고 `event_not_fired`(level·confidence null)로 바꾼다, `rationale` 끝에 변환 표시 |
| 위 검증으로 차원 행이 바뀐 요소의 `summary` | 워커 | 최종 차원 행만 입력으로 1회 다시 쓴다(실패하면 고정 문장, 재요청 없음). 이 호출도 `usage.llm_calls`에 든다 |
| 요소 level = 차원 평균 반올림(.5 올림) | 워커가 계산해 채움 | — (모델 값을 쓰지 않음) |
| `rationale`·`summary` 금지어(합격·불합격·순위·추천·점수) | 워커 | 그 요소 1회 재요청 → 실패 차원 `evaluate_failed`, 실패한 summary는 고정 문장 |

차원 행은 `evaluations`, 실행 메타와 요소 요약(`elements`)은 `evaluation_runs`에 한 트랜잭션으로 저장한다. draft는 `GET /evaluations`에서 둘을 함께 읽어 만든다(체크포인트에 의존하지 않음).

**평가 호출 규칙**: 요소마다 별도 LLM 호출(`MODEL_EVALUATOR`, temperature 0~0.2), 다른 요소의 결과는 입력에 넣지 않는다. 재요청은 **요소 단위 1회**(형식 오류·근거 부재일 때)이고, 그래도 실패한 차원은 `level: null, no_record_reason: "evaluate_failed"`(화면 문구는 [product/rubric.md](../product/rubric.md) 5장)로 저장한다. **LLM 전송 실패·타임아웃은 저장하지 않고** 예외로 올려 큐 재시도(`attempt+1`)에 맡긴다. 워커는 근거를 고쳐 쓰지 않는다. 차원 판단을 바꾸는 예외는 근거 1개인 수준 4·5의 상한(수준 3·확신도 최대 medium), 확신도 clamp, 핵심 카드 미발생 변환(`event_not_fired`)뿐이고, 그로 바뀐 요소의 `summary`만 위 표대로 다시 쓴다.

## 의도 분류 출력 스키마

지원자 메시지마다 **세션 메일박스가 `message` 자극을 꺼낼 때** 순서대로 호출한다(`MODEL_INTENT`, 타임아웃 10초, 재시도 없음). 큐 밖에서는 메시지 저장·generation +1·enqueue만 한다. 분류와 아래 안·입장 반영은 그 자극이 낡았어도(generation이 현재 값보다 작음) **반드시 수행**하고, 낡은 자극에서 건너뛰는 것은 턴 선택·페르소나 발언 생성뿐이다. 분류 결과는 `message_id` 멱등이라 같은 자극의 재실행(재큐·재시작)에서 다시 부르지 않는다. **분류 입력과 안·입장 저장 대상의 단계는 저장된 `messages.phase`**다. 메일박스가 밀린 동안 단계가 바뀌었어도 현재 단계로 바꿔 분류하지 않는다. 현재 단계 검사는 턴 선택·발언 생성을 생략할지 판단하는 데만 쓴다. 입력: 메시지, 메시지의 단계(`messages.phase`), 그 단계의 기존 안 목록 `[{option_id, label, scenario_option_key}]`, **그 단계의 카탈로그 안 전체** `[{scenario_option_key, label}]`(아직 아무도 말하지 않은 안 포함. 키워드 단계는 후보 4개), 직전 메시지 5개.

```json
{"intent": "propose", "target_option_id": null, "scenario_option_key": "water_p2", "new_option_label": null, "confidence": "high", "addressed_to": ["p2"]}
```

| 필드 | 값 |
|---|---|
| intent | `propose`(새 안 또는 기존 안을 자기 의견으로 냄) ∣ `agree` ∣ `oppose` ∣ `question` ∣ `other` |
| target_option_id | 대상이 기존 안 행이면 그 id, 아니면 null |
| scenario_option_key | 대상이 아직 행이 없는 카탈로그 안이면 그 키, 아니면 null |
| new_option_label | intent=propose이고 기존 안·카탈로그 안 어느 것과도 다를 때 짧은 라벨(20자 이내), 아니면 null |
| confidence | `high` ∣ `low`. 의도와 대상이 분명하면 high |
| addressed_to | 이름을 부르거나 답한 자리 id 목록(턴 매니저 규칙 입력). 없으면 [] |

처리(결정적 코드):

- `propose` + scenario_option_key → 그 키로 안 행을 만들거나(이미 있으면 기존 행, 멱등 키 (session_id, phase, scenario_option_key)) proposer = candidate. 뒤에 AI가 같은 안을 `propose_option`해도 같은 행이 돌아오므로 최초 제안자가 보존된다. `propose` + new_option_label → 자유 안(proposer = candidate). **안 행은 confidence와 상관없이 만든다**(최초 제안 사실 기록).
- 입장: confidence = high일 때만 stances 행을 쓴다. `propose`·`agree` + 대상 → support, `oppose` + 대상 → oppose(같은 안의 이전 support를 철회). confidence = low·`question`·`other` → 입장 없음.
- 실패·스키마 위반은 `{"intent": "other", "target_option_id": null, "scenario_option_key": null, "new_option_label": null, "confidence": "low", "addressed_to": []}`, `fallback=true`.
- 분류는 토론 진행용이다. 세션 후 평가 에이전트에는 참고 입력으로만 들어가고 원문과 다르면 원문을 따른다([product/rubric.md](../product/rubric.md) 1.2). 정확도 목표는 `tests/eval/` 의도 분류 20문장 기준 0.85다(최근 측정은 [architecture/harness.md](../architecture/harness.md) "실측 기준선").

## 턴 매니저 결정 스키마

토론 그래프 `turn` 노드의 출력. `agent_runs.decision`에 저장한다(관측·테스트용).

```json
{"stimulus": "message", "generation": 12, "speakers": [{"seat": "p1", "purpose": "push_option", "option_id": "...", "card_ids": []}], "fill": null, "invite": false, "llm_used": false, "dropped_seats": [], "reason": "지원자가 p1의 안에 반대함"}
```

- `purpose`: `respond`(지원자에게 답) ∣ `push_option`(자기 고정 안 주장) ∣ `react`(다른 동료 발언에 반응) ∣ `invite`(침묵 초대) ∣ `card`(카드가 정한 발언. 이전 run에서 전달되지 않은 카드 화자를 이어서 포함). `card_ids`는 그 발언이 전달하는 카드 id 배열(카드 발언이 아니면 []).
- `speakers`는 0~2명이고 일반 발언 루프(respond → apply)를 탄다. 합의 채우기 공지 화자는 `speakers`에 넣지 않는다.
- `fill`은 `{option_id, seat}`(합의 채우기만, seat = 공지·채우는 자리) 또는 null. `fill` 노드의 `announce_fill` 분기가 이 값으로 `fill_slot{option_id, seat, content, run_id}`를 한 번 불러 칸 확정과 공지를 함께 저장한다([세션 생명주기](../architecture/session-lifecycle.md) "합의 채우기"). load 때 합의가 없었어도 apply 뒤 재판정에서 합의가 생기면 같은 분기를 탄다.
- 규칙 결과가 0명이고 자극이 `message`일 때만 턴 LLM(`MODEL_TURN`)을 부른다(`llm_used`). 나머지 자극은 규칙만.
