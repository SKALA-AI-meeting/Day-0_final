# 세션 생명주기

음성 토론의 현재 구현은 [음성 구현](voice.md)을 따른다.

세션 상태와 단계, 지원자 메시지·단계 전환·제출·리포트의 실행 순서, 그래프 자극과 세션 메일박스, 재시작 복구의 정본이다. 단계·칸·카드의 제품 규칙(수치·우선순위)은 [시나리오 모델](../product/scenario.md), 필드와 오류 코드는 [계약](../contracts/README.md).

## 세션 상태 머신

### status (생명주기)

```
invited ──동의·시작──▶ in_progress ──제출 / 세션 만료 자동 제출──▶ completed ──워커 draft──▶ report_ready ──검토 완료──▶ approving ──워커 final──▶ reviewed
   │                        │                                              └──워커 3회 실패──▶ failed(report) ──재생성──▶ completed(초안 전 실패) ∣ approving(확정 실패)
   └──취소──▶ cancelled ◀────┘ (시작 후 취소 = 중도 포기, 리포트 없음)
                            └──페르소나 LLM 연속 실패 3회 ∣ 채용 건 시나리오 복사본 없음──▶ failed(session)
```

- `ended_at`: 세션이 끝난 시각. completed(제출·자동 제출)·failed(session)·cancelled로 바뀌는 트랜잭션에서 한 번 쓴다(시작 전 취소 포함). failed(report)는 이미 completed라 바꾸지 않는다. **삭제 예정일 `purge_at = ended_at + 90일`, 디브리핑 기한 = `ended_at + 7일`**.

### phase (in_progress 안의 진행)

| phase | 칸 | 예산(60분 기준, 15분은 ÷4) | 다음으로 가는 조건 |
|---|---|---|---|
| keyword | keyword | 10분 (2.5) | 칸 confirmed (지원자 확정 / AI 합의 채우기 / api 시간 초과 채우기) |
| problem | problem | 18분 (4.5) | 같음 |
| solution | solution | 18분 (4.5) | 같음 |
| proposal | 없음 | 14분 (3.5) | 기획안 제출 또는 `ends_at` 자동 제출 |
| completed | — | — | status=completed와 같은 트랜잭션 |

- 시작 전 phase는 null. start에서 `keyword`.
- 단계가 바뀌면(`advance_phase`) `sessions.generation`도 +1 한다(아래 "세션 메일박스").
- 단계 마감은 누적 고정: `phase_deadline_at(k) = started_at + (예산 1..k의 합) × session_minutes / 60`. 일찍 확정하면 남은 시간이 다음 단계로 이월된다. proposal 마감 = `ends_at`.
- 상태·단계 변경은 api `SessionService` 한 곳만 한다(start, advance_phase, submit, cancel, fail, mark_report_ready, approve, mark_reviewed, regenerate).

### 단계 진행과 재시작 복구

in_progress 안의 phase: `keyword → problem → solution → proposal → completed`. 토론 단계는 칸 confirmed로만 넘어가고(지원자 확정·AI 합의 채우기·api 시간 초과 채우기), proposal은 제출로만 끝난다. 상태·단계 변경은 api `SessionService` 한 곳만 한다. 지원자 목록 표시는 다섯 묶음(대기·진행·검토 필요·완료·문제), `fail(stage, reason)`은 failed_stage(session∣report)와 담당자 행동 안내를 남긴다. 세션 실패는 리포트를 만들지 않고 재응시는 새 초대(previous_session_id, 같은 별칭 가능). 시작 전 만료는 재발급(같은 세션)으로 연장한다. 리포트 실패의 재생성은 실패 단계에 따라 둘로 나뉜다(정본 [contracts/api.md](../contracts/api.md) `POST /admin/sessions/{id}/report/regenerate`). 초안 전 실패(`failed_step` draft)는 남은 draft를 지우고 completed로 돌려 `report.generate`를 재발행한다(최신 평가에 실패 차원이 있으면 `eval_round` +1). 확정 실패(`failed_step` finalize)는 approving으로 돌려 같은 `eval_round`의 `report.approve`를 재발행하고, 워커는 그 회차의 finalize만 재개한다. `ended_at`은 그대로다.

재시작 복구: api 기동 때와 스케줄러 틱마다 (1) `phase_deadline_at`이 지났는데 칸이 empty인 세션은 api가 바로 시간 초과 채우기, (2) `ends_at`이 지났는데 completed가 아닌 in_progress 세션에 자동 제출, (3) `agent_runs`의 `queued`·`started`(기동 때 전부, 실행 중에는 started 200초 초과)를 같은 run_id로 메일박스에 다시 넣기, (4) 침묵 초대 미전달·카드 미전달 조건을 다시 평가해 idle·card_due enqueue(같은 단계 안일 때만), (5) `report_enqueued_at`·`approve_enqueued_at`이 빈 세션 재발행을 한다. 모든 쓰기가 위 멱등 키를 가지므로 재실행해도 중복이 없다.

## 세션 흐름 한눈에

```
입장·동의·시작 ─▶ keyword(10) ─확정─▶ problem(18) ─확정─▶ solution(18) ─확정─▶ proposal(14) ─제출─▶ completed ─큐─▶ 리포트
                  └ 마감이면 api가 가장 많이 모인 의견으로 채우고 넘어감(resolution=timeout) ┘        └ 세션 만료면 현재 초안 자동 제출
```

- 단계 예산(분)은 60분 기준 10·18·18·14이고 `session_minutes`에 비례한다(데모·e2e 15분 = 2.5·4.5·4.5·3.5, 카드·유휴 오프셋은 최소 20초).
- **단계 마감 시각은 누적 고정**이다: `deadline_k = started_at + (예산 1..k의 합) × session_minutes / 60`. 일찍 확정하면 다음 단계가 그만큼 길어진다(남은 시간 이월). 마지막 proposal 마감 = `ends_at`.
- 오피스 시계: `오피스 시각 = 09:00 + t_min × 60 / session_minutes`(60분·15분 모두 09:00~10:00). 지원자 화면은 남은 시간 중심이고 오피스 시각은 메시지 타임스탬프에만 쓴다.

## 요청 흐름

### 지원자 메시지

1. web → api WS `message.send{client_msg_id, content}`
2. api(메일박스 밖): 단계 확인(in_progress 아니면 오류) → 민감정보 마스킹 → 저장(client_msg_id 멱등, `phase`, `origin` = 이 단계에서 지원자를 부른 초대 메시지 뒤 첫 발언이면 `invited`, 아니면 `spontaneous`)과 같은 트랜잭션에서 `sessions.generation += 1` → 커밋 직후(의도 분류 전) `message.ack{run_id}`·`message.new` 송신 큐 → `agent_runs` 행 `queued` → 세션 메일박스에 `message` 자극 enqueue(자극의 `generation`은 이 저장이 올린 값, `phase`는 `messages.phase`). 큐 밖 처리는 여기까지다
3. api(메일박스 안, 자극을 꺼낸 순서대로): **실시간 의도 분류**(작은 모델, 입력 단계는 저장된 `messages.phase`이고 그 단계 카탈로그 안 전체 포함, 타임아웃 10초, 재시도 없음) → `message_intents` 저장 → 토론 상태 누적: 제안이면 안 행(시나리오 키면 그 키 행, 아니면 자유 안, 최초 제안자 = candidate), confidence high인 제안·동의·반대면 `stances` 행(참여자당 현재 지지 하나, 다른 안 지지는 대체, 같은 안 반대는 철회). 실패하면 `intent=other`(fallback=true)로 두고 진행. 안·입장도 `messages.phase` 단계에 저장한다. **자극이 낡았어도 이 단계는 반드시 수행**한다
4. 자극이 낡았으면(단계가 현재 단계와 다름, 또는 generation이 현재 값보다 작아 뒤의 메시지 자극이 응답을 맡음) 턴 선택·페르소나 발언 생성 없이 `skipped`. 아니면 토론 그래프 실행:
   - `load`(결정적): DebateState(합의·시간 초과 후보, `card_progress`, `cost_left` 포함)와 최근 대화를 읽어 페르소나용으로 걸러 컨텍스트에 미리 넣는다
   - `turn`(턴 매니저): 규칙(지원자가 부른 사람, 미전달 카드 화자, 침묵 초대, 합의 성립 → `fill = {option_id, seat}`)을 먼저 적용하고, 규칙 결과가 0명이고 자극이 `message`일 때만 LLM이 **화자 0~2명**과 목적을 정한다
   - 화자마다 `typing` 시작 → `respond`(페르소나 에이전트 LLM. 자기 자리의 성향·입장 계획·카드 brief·기억, 허용 도구만) → `guard`(금지 문구 검사, 걸리면 재생성 1회 → 안전 문구) → `stance_check`(입장 계획 밖이면 응답 전체 재생성 1회 → `stance_fallback` 고정 문구) → `wait_typing`(지원자 입력 중이면 최대 10초, 그래도 입력 중이면 미게시·자극 재큐·`deferred`) → generation 확인(바뀌었으면 남은 화자 버림) → `apply`(결정적): debate-mcp `post_message{expected_generation, card_ids?}`(한 발언이 여러 카드를 전달할 수 있음), 안을 처음 밀면 `propose_option`, 입장을 표현하면 `set_stance`, 카드의 첫 발언이면 카드마다 assessment-mcp `record_observation` → `typing` 종료. 둘째 화자는 1~3초 간격
   - `fill`(결정적): `apply`가 이번 발언의 입장을 저장한 **뒤** 지지를 다시 읽어 판정한다. 지원자 포함 3/4 지지 안이 있고 칸이 empty면 `announce_fill` 분기: 지지자 중 우선순위 자리와 고정 문장 "그럼 X로 정리할게요"를 정해 `fill_slot{option_id, seat, content, run_id}`를 한 번 부른다. 일반 발언 루프(respond → apply)와 LLM을 거치지 않는다. api가 한 트랜잭션으로 합의 재검사 → 칸 confirmed → 공지 메시지 저장(seq 5, `meta.kind=consensus_fill`) → generation +1을 하고 WS `message.new`·`slot.confirmed`를 보낸다. 409면 아무것도 저장하지 않고 끝낸다
5. debate-mcp → api `/internal/*` → 저장(멱등) → 세션 송신 큐 → WS(`seq` 붙임)

토큰 스트리밍은 없다(완성 문장 한 번에 게시, 금지 문구 검사와 충돌하지 않게). 비용이 하드 상한에 닿으면(`cost_left ≤ 0`, `services/budget.py`) 페르소나는 LLM 없이 고정 안전 문구만 게시한다.

경계: DB 커밋은 2·3·5. 외부 호출은 3·4(OpenAI, MCP)이고 모두 메일박스 안이다. 재시도는 LLM 2회(의도 분류는 0회), MCP upstream 1회. 페르소나 발언이 실패하면 그 발언은 생략하고 `llm_failures+1`(연속 3회면 세션 failed).

### 단계 전환과 타이머

| 사건 | 처리 |
|---|---|
| 지원자 확정 | `POST /sessions/{id}/slots/{phase}/confirm` → api가 칸을 `confirmed`(filled_by=candidate, resolution=consensus, 그 시점 지지 현황 스냅샷)로 바꾸고 다음 단계로 전환. 키워드는 버튼의 후보 키, 문제·해결책은 옵션 키·정규화 라벨 일치로만 안에 연결(LLM 없음) |
| AI 합의 채우기 | 턴 매니저 `fill`의 `announce_fill` 분기 → `fill_slot{option_id, seat, content, run_id}`. api가 한 트랜잭션에서 `consensus.judge`(지원자 포함 3/4 지지, 대기 시간 없음)를 다시 검사하고 아니면 409 `no_consensus`(AI는 합의 전에 칸을 채울 수 없다, 아무것도 저장 안 함). 성공하면 칸 확정·공지 메시지(seq 5)·전환이 함께 커밋되므로 공지가 빠진 확정은 없다. 같은 run_id 재호출은 기존 결과 |
| 단계 마감 | 스케줄러가 `now ≥ phase_deadline_at`이고 칸이 empty이면 **api `services/timeout.py`가 직접**: 시간 초과 선택 규칙으로 안을 고르고 우선순위 자리 이름으로 시나리오 고정 문장("시간이 다 돼서 대화에서 가장 많이 모인 의견인 X로 할게요")을 저장, 칸 `confirmed{resolution: timeout, filled_by: 그 자리}` → 전환. 그래프·LLM·MCP를 거치지 않는다 |
| 전환 공통 | api `SessionService.advance_phase`: `phase`·`phase_started_at`·`phase_deadline_at` 갱신, `generation += 1`, 시스템 안내 메시지("지금 할 일"과 남은 시간, 시나리오 고정 문구) 저장, WS `slot.confirmed`·`phase.changed`·`timer`. keyword 확정이면 그 키워드 자료를 열고 WS `materials.opened`. proposal 시작이면 기획안 초안의 빈 칸(키워드·문제 상황·해결책)을 확정 칸 값으로 채운다. 이어서 `phase_started`를 메일박스에 넣기만 한다(기획안 단계 `phase_started` 실행이 `honesty_credit` 관찰을 남긴다) |
| 마감 임박 | 단계 마감 `L/30` 전(60분 기준 2분, 압축 비율)에 시스템 안내 메시지 1건. 시점은 시나리오 데이터 |
| 침묵 초대 | 토론 단계에서 침묵 초대 조건([product/scenario.md](../product/scenario.md) 3.1)이 참이고 그 단계 `invite_delivered`가 false면 스케줄러가 `idle`을 넣는다(같은 단계 idle이 큐·실행 중이면 안 넣음, 지원자 입력 중이면 다음 틱). 초대 메시지(`is_invite`)가 실제 게시돼야 `invite_delivered_phases`에 들어가고, 안 됐으면 다음 틱에 다시 넣는다. 묻는 자리는 시나리오의 `silence_inviter`(비판형) |
| 카드 예정 시각 | 카드의 `offset_min`(단계 시작 기준, 최소 20초)에 도달했고 `session_cards`상 아직 delivered가 아니면 `card_due{card_id}`. 첫 처리 때 `session_cards` 행을 만들고 문제 상황 카드는 기준안을 잠근다(`locked_option_id`). 담당 화자가 게시할 때마다 `card_ids`의 카드마다 `delivered_seats`가 늘고, 남은 화자는 다음 run이 이어서 말한다. 전원 delivered 전까지 `card_due`를 새 uuid4 run_id로 다시 넣되 같은 카드는 마지막 run 뒤 30초 간격·카드당 최대 3회다. 상한에 닿으면 더 넣지 않고 남은 담당 화자는 미전달로 남는다(리포트 `cards[].delivered: false`) |
| 세션 만료 | `ends_at` 도달 → 현재 기획안 초안으로 자동 제출(아래) |

### 기획안 작성과 제출

1. 지원자가 우측 기획안 탭에 혼자 쓴다(`PUT /sessions/{id}/proposal`, 디바운스 저장. 진행 중 언제든 저장 가능, 제출은 proposal 단계에서만). AI 동료는 채팅으로만 기여한다(물어보면 내용 제공, 요청하면 debate-mcp `get_draft`로 초안을 읽고 의견).
2. 제출(`POST /sessions/{id}/proposal/submit`) 또는 `ends_at` 도달 → `SessionService.submit(reason)`: 한 트랜잭션에서 초안을 제출본으로 고정(`proposals.submitted`, 자동 제출이면 `auto_submitted=true`) → `status=completed`·`phase=completed`·`completed_at`·`ended_at`(커밋) → WS `session.ended{submitted∣timeout}`. 메일박스의 남은 자극은 상태 검사로 `skipped`. 중간 상태는 없다.
3. api가 RabbitMQ `report.generate`에 `{session_id, attempt, eval_round}` 발행(persistent, publisher confirm 뒤 `report_enqueued_at` 기록). 비어 있으면 스케줄러가 60초마다 재발행.

### 리포트 (report-worker)

그래프 `collect → aggregate → evaluate → draft → interrupt → finalize`.

| 노드 | 하는 일 | 도구 |
|---|---|---|
| collect | 척도와 평가 입력 묶음 수집: 세션 대화 전체·칸 기록·기획안 제출본·카드 관찰 observations(창·전달 여부 포함)·사실 지표·Contributions | assessment-mcp `get_rubric`·`get_session_record`·`get_contributions` |
| aggregate | 사실 지표 정리, 카드 창 연결, `contribution_check`(안별 실제 최초 제안자 / 기획안에 적힌 기여자) 구성. 결정적 코드, LLM 없음 | — |
| evaluate | **평가 에이전트**: 요소마다 별도 LLM 호출(`MODEL_EVALUATOR`, temperature 0~0.2, 다른 요소 결과는 입력 안 함)로 세션 전체를 읽고 차원마다 `{level 1~5 ∣ null + no_record_reason, evidence_refs, rationale, confidence}`와 요소 summary를 낸다. 검증 경계([contracts/internal.md](../contracts/internal.md) EvaluationSave): 워커가 형식·근거 개수·금지어를 보고 실패하면 **그 요소를 1회** 다시 요청 → 그래도 실패한 차원은 `evaluate_failed`. 확신도 상한 위반은 거부하지 않고 깎는다(clamp). 요소 level은 워커가 차원 평균 반올림(.5 올림)으로 계산. LLM 전송·타임아웃 실패는 저장하지 않고 큐 재시도. api는 저장 전 형식·세션 소속만 검증하고 `evaluation_runs`(prompt_versions·elements)와 `evaluations`를 한 트랜잭션으로 저장. run_id = `uuid5(session_id, "evaluate:"+eval_round)`이고 같은 run_id가 이미 있으면 LLM을 부르지 않고 `GET /evaluations`(요소 요약 포함)로 재사용 | assessment-mcp `save_evaluation`, 내부 `GET /evaluations` |
| draft | 근거마다 원문·시각·앞뒤 맥락(`get_transcript_span` 앞 5·뒤 3, 표시용)을 붙여 Report(`elements`·`dimensions`·`facts`·`contribution_check`·`seats`·`slots`·`cards`·`debrief`, 총점 없음) 조립. 카드 장면 `cards[].scene`은 카드의 고정 문장 `report_template` 그대로(LLM 없음) → 내부 `report/draft` → `report_ready` → ack | assessment-mcp `get_transcript_span`, 내부 REST |
| interrupt → finalize | 담당자 검토 완료(차원 단위 조정·메모, `reviewer_name`) → `report.approve` → 같은 thread 재개 → **승인된 draft content를 그대로** `report/final`(해시 동일, 조정은 조회 때 병합) → `reviewed` | 내부 REST |

실패는 attempt+1 재발행(백오프 5·15·45초), 3회면 `report.dlq` → `report_failed` → `status=failed`(failed_stage=report). 담당자의 "리포트 다시 생성"은 위 "단계 진행과 재시작 복구"의 두 경로를 따른다. 초안 전 실패를 다시 만들 때 최신 평가 실행에 `evaluate_failed`가 없으면 같은 run_id를 재사용하고, 있으면 `eval_round`+1로 새 run_id로 다시 평가한다. 그래프 thread는 `report:{session_id}:{eval_round}`라 새 회차는 이전 interrupt 체크포인트의 draft를 재사용하지 않고 처음부터 실행하며, 같은 회차의 큐 재시도만 체크포인트에서 이어 간다. 리포트는 **담당자가 평가할 때 보는 참고 자료**다. AI 평가 에이전트가 공개된 척도로 근거를 들어 수준을 판단하고 담당자가 그것을 참고해 최종 평가한다. 합격·불합격·순위·총점은 만들지 않는다.

## 칸 확정 규칙

### 합의 판정과 시간 초과 선택 (api의 함수 하나 `consensus.judge`)

수치·우선순위·채우는 자리 선택은 [product/scenario.md](../product/scenario.md) "AI 동료가 칸을 채우는 조건"·"시간 초과 규칙"이 정본이다. 계약은 입력과 결과만 고정한다.

- 입력: 그 단계의 안과 `stances` 이력. **현재 지지는 참여자당 하나**다 = 참여자별로 그 단계의 마지막 `support` 행의 안. 다른 안을 support하면 이전 지지를 대체한다(두 안을 동시에 지지하지 않는다). 그 뒤에 **같은 안에 대한 `oppose`가 있으면 지지를 철회**한 것으로 보고 현재 지지는 null이다. 다른 안에 대한 oppose는 현재 지지를 바꾸지 않는다. 제안(propose)은 제안자의 지지를 포함한다. 지원자 입장은 의도 분류 `confidence = high`일 때만 stances 행이 된다([contracts/internal.md](../contracts/internal.md) "의도 분류 출력 스키마"). AI 입장은 항상 유효하다.
- 합의 결과 `consensus_option_id`: 한 안의 현재 지지자가 4명 중 3명 이상이고 **지원자가 그 지지자**이면 그 안, 아니면 null. 대기 시간 조건은 없다(성립하는 즉시 AI가 채운다).
- 시간 초과 결과 `timeout_option_id`: 지지자 최다 → 동률이면 지원자 지지 안 → 먼저 제안된 안 → 안이 없으면 단계 기준안(문제 상황은 카드가 잠근 `session_cards.locked_option_id`가 있으면 그것, 키워드는 `default_keyword`). 기준안 행이 없으면 채우기 트랜잭션에서 그 안을 만든다(proposer는 공지하는 자리).
- 두 값은 DebateState에 실리고, api의 `fill` 검사와 시간 초과 채우기가 같은 함수를 쓴다.
- **같은 run 재판정**: 그래프 `fill` 노드는 `apply`가 이번 run의 발언 입장을 저장한 **뒤** 지지를 다시 읽어 판정한다(load 때 값만 보지 않는다). AI 발언으로 지원자 포함 3/4 지지가 생기면 다음 자극을 기다리지 않고 같은 run에서 채운다.

### 시간 초과 채우기 (api가 직접, 그래프 없음)

스케줄러가 `now ≥ phase_deadline_at`이고 칸이 empty인 토론 단계를 찾으면 api `services/timeout.py`가 한 트랜잭션으로: `timeout_option_id` 계산 → 공지 자리(시나리오의 시간 초과 우선순위 첫 자리, [product/scenario.md](../product/scenario.md)) 이름으로 시나리오 고정 문장("시간이 다 돼서 대화에서 가장 많이 모인 의견인 X로 할게요")을 messages에 저장(sender = 그 자리, `meta.kind = "timeout_fill"`) → 칸 confirmed(`resolution: timeout`, `filled_by` = 그 자리) → `advance_phase`. LLM·MCP·메일박스를 거치지 않는다. 그래프 자극에 `timeout`은 없다.

### 합의 채우기 (그래프 `fill` → `fill_slot`, 한 트랜잭션)

그래프 `fill` 노드의 `announce_fill` 분기가 지지자 중 우선순위 자리([product/scenario.md](../product/scenario.md))와 시나리오 고정 문장("그럼 X로 정리할게요.", 이름만 치환)을 정해 `fill_slot{session_id, option_id, seat, content, run_id}`를 한 번 부른다. 이 분기는 일반 발언 루프(`respond → guard → stance_check → apply(post_message)`)를 거치지 않고 LLM도 부르지 않는다. api는 한 트랜잭션으로: `consensus.judge` 재검사(이 option_id가 합의 안이 아니면 409 `no_consensus`) → 칸 confirmed(`resolution: consensus`, `filled_by` = seat) → 공지 메시지 저장(sender = seat, run_id, seq 5, `meta.kind = "consensus_fill"`) → `advance_phase`(generation +1). 커밋 뒤 WS로 `message.new`(공지)와 `slot.confirmed`를 보내고 이어서 전환 공통 프레임(`phase.changed`·`timer`, keyword면 `materials.opened`)을 보낸다. 409면 칸·공지·단계 어느 것도 저장하지 않는다. 칸 확정과 공지가 함께 커밋되므로 "칸은 확정됐는데 공지가 없는" 상태가 생기지 않고, 재실행이 낡은 자극으로 `skipped`돼도 보충할 공지가 남지 않는다.

## 그래프 자극 (stimulus)

토론 그래프는 자극마다 한 번 실행된다. 자극은 아래 **4종**이고 `agent_runs.stimulus`·턴 매니저 결정의 `stimulus`·State 스냅샷 `active_runs[].stimulus`가 같은 값을 쓴다. `timeout`(api가 직접 채움)·`room_quiet`는 없다.

| type | 발생 | 함께 오는 값 | run_id |
|---|---|---|---|
| `message` | 지원자 room 메시지 저장 뒤(의도 분류는 메일박스가 이 자극을 처리할 때) | message_id | api가 새로 발급(uuid4) |
| `phase_started` | 세션 시작·단계 전환 커밋 뒤 `advance_phase`가 넣음 | phase | `uuid5(session_id, "phase:"+phase)` |
| `idle` | api 스케줄러. 토론 단계에서 침묵 초대 조건([product/scenario.md](../product/scenario.md) 3.1)이 참이고 그 단계 `invite_delivered`가 false | phase | enqueue마다 새로 발급(uuid4) |
| `card_due` | api 스케줄러. 카드 예정 시각(단계 시작 + `offset_min`, 최소 20초) 도달 뒤 그 카드가 아직 `delivered`가 아님 | phase, card_id | enqueue마다 새로 발급(uuid4) |

형식: `Stimulus = {type, phase, generation, message_id?, card_id?}`. `message` 자극의 `generation`은 **그 메시지 저장 트랜잭션이 올린 값**이다(enqueue 시점에 다시 읽지 않는다. 그사이 다른 메시지가 저장됐으면 이 자극은 낡은 자극이 된다). 다른 자극의 `generation`은 enqueue 시점의 `sessions.generation`. `message` 자극의 `phase`는 그 메시지의 `messages.phase`.

- **다시 넣기(재평가)**: 스케줄러는 5초 틱마다 조건을 다시 본다. 침묵 초대가 아직 전달되지 않았으면(`invite_delivered` false) `idle`을, 카드가 아직 `delivered`가 아니면 `card_due`를 다시 넣는다(카드는 아래 상한 안에서만). 같은 단계의 `idle`, 같은 card_id의 `card_due`가 이미 큐에 있거나 실행 중이면 넣지 않는다. 지원자가 입력 중(`typing` 유효)이면 그 틱에는 넣지 않는다.
- `card_due`는 담당 자리 전원이 delivered될 때까지 enqueue마다 **새 uuid4 run_id**로 다시 넣는다(고정 run_id 없음). 앞 run이 `finished`여도 일부 화자가 버려졌으면 다음 `card_due`가 남은 화자를 이어서 넣는다. 같은 카드의 `card_due`는 마지막 run의 `queued_at` 뒤 30초 간격으로 **카드당 최대 3회**까지만 넣는다(`scheduler.py` `CARD_DUE_MAX_RUNS`). 상한에 닿으면 그 카드는 미전달로 남고 리포트 `cards[].delivered`가 false다. 카드 전달의 멱등은 `session_cards.delivered_seats`(자리 집합)와 관찰의 (session_id, card_id)로만 판단한다.
- 카드 트리거는 시각 기반 `card_due` 하나다(이벤트 트리거 없음). 예외로 페르소나 발언이 없는 `honesty_credit`은 기획안 단계 `phase_started` 실행에서 `record_observation`만 남긴다(`trigger_message_id` = 그 단계 시작 시스템 안내 메시지).
- **카드 진행 상태 `session_cards`**: 카드마다 `(session_id, card_id)` 한 행. `card_due` 첫 처리 때 행을 만들고, 문제 상황 카드(`flexibility_majority`·`listening_pressure`)는 그때 기준안을 계산해 `locked_option_id`·`locked_at`으로 잠근다 = "지원자의 현재 지지 안이 아닌 첫 시나리오 안"([product/scenario.md](../product/scenario.md) 순서). 그 시나리오 안의 행이 아직 없으면 잠금 트랜잭션에서 만든다(proposer = 그 카드의 첫 담당 자리, 곧바로 카드 발언으로 그 안을 밀기 때문). 같은 단계의 두 카드는 같은 기준안을 쓰고 한 번 잠그면 바꾸지 않는다. 예정 시각이 같은 카드들은 먼저 실행된 `card_due` run의 `cards` 노드가 예정 시각이 지난 미전달 카드를 함께 처리한다(뒤에 꺼낸 같은 시각 카드의 `card_due`는 `delivered_seats`에 이미 든 자리를 다시 말하게 하지 않는다). 카드 전에는 고집형도 입장을 밝히지 않는다.
- **전달 기록**: 카드 화자가 실제로 게시해야 전달이다. 메시지 하나가 여러 카드를 함께 전달할 수 있다(예: 같은 시각의 `flexibility_majority`·`listening_pressure`는 고집형의 첫 기준안 발언이 두 카드의 첫 발언). `POST /internal/sessions/{id}/messages`의 `card_ids`(카드 id 배열)에 든 **카드마다** api가 그 `session_cards` 행의 `delivered_seats`에 sender 자리를 더한다. 담당 자리([product/scenario.md](../product/scenario.md) 카드 `actor`를 `seats`로 대응) 전원이 들어가면 그 카드는 delivered이고, 아직이면 다음 run의 결정적 노드가 남은 화자를 이어서 넣으며 스케줄러도 `card_due`를 다시 넣는다. 카드 관찰(`record_observation`)은 **카드마다 1건**, 그 카드의 첫 카드 발언이 게시된 뒤 기록한다(`trigger_message_id` = 그 메시지. 공동 전달이면 두 관찰이 같은 메시지를 가리킨다).

### 세션 메일박스 (직렬화)

- api 프로세스에 세션마다 메일박스 하나(`app/mailbox.py`): asyncio Queue + 소비 Task 1개가 자극을 하나씩 직렬로 실행한다. 세션 Lock·`lock_held`·처리 집합(`handled`)은 없다. api는 단일 프로세스로 운영한다.
- 큐 밖의 지원자 메시지 처리는 저장·`sessions.generation += 1`(같은 트랜잭션)·`message` 자극 enqueue뿐이다. 단계 전환(`advance_phase`)도 +1. 그래프는 **각 게시 직전** generation을 다시 읽어 실행 시작 때 값과 다르면 남은 화자를 버린다(실행은 `finished`, `decision.dropped_seats`에 기록). `post_message`의 `expected_generation`이 api에서 같은 검사를 원자적으로 한 번 더 한다(다르면 409 `stale_generation`).
- `message` 자극은 꺼낸 순서대로 먼저 의도 분류와 안·입장 반영을 한다([contracts/internal.md](../contracts/internal.md) "의도 분류 출력 스키마", 저장된 `messages.phase` 기준, 낡은 자극도 생략하지 않음). 그다음 자극이 낡았으면 턴 선택·페르소나 발언 생성을 하지 않고 `agent_runs.status = skipped`로 닫는다: phase가 현재 단계와 다름, 또는 `message` 자극인데 generation이 현재 값보다 작음(뒤의 메시지 자극이 응답을 맡음). 다른 자극은 낡았으면 아무것도 하지 않고 skipped.
- 지원자 입력 중 대기(그래프 노드 `wait_typing`): 게시 직전 지원자 `typing`이 유효하면 최대 10초 기다린다. 그래도 유효하면 게시하지 않고 같은 자극을 새 run_id로 큐 뒤에 다시 넣고 이번 실행은 `deferred`.
- `advance_phase`는 `phase_started`를 큐에 넣기만 한다.
- 멱등은 `agent_runs.status`와 DB 멱등 키만 쓴다. 자극 저장: `agent_runs` 행을 `queued`로 만든 뒤 큐에 넣는다. 재시작하면 `queued`·`started` 행을 같은 run_id로 다시 넣는다. 실행 중 프로세스에서 `started`가 200초(역할별 타임아웃 합 + 여유)를 넘으면 `failed`로 닫고 같은 run_id로 다시 넣는다. 같은 run_id 재실행의 게시는 (session_id, run_id, seq) 멱등으로 중복되지 않는다. 합의 채우기는 칸 확정·공지·generation +1이 한 트랜잭션(위 "합의 채우기")이라 fill 커밋 뒤 재실행이 단계 변경으로 `skipped`돼도 미게시 공지가 남지 않는다.
- 비용: LLM 래퍼가 호출마다 `usage.cost_usd`(모델별 단가 표, 래퍼 한 곳)를 계산한다. 누계는 두 컬럼이다: `sessions.debate_cost_usd` = 세션 중 토론 LLM 비용(의도 분류·턴·페르소나·어시스턴트, 하드 상한용), `sessions.cost_usd` = 전체 관측값(토론 + 리포트 평가). 목표 $0.5/세션은 `cost_usd`로 관측만 하고, 토큰 수도 `agent_runs.usage`에서 관측만 한다(세션 중 응답을 끊지 않음). 판단은 api `services/budget.py` 한 곳이 DB에서 `debate_cost_usd`를 읽어 `cost_left = SESSION_COST_HARD_USD(기본 $2) − debate_cost_usd`로 계산하고 DebateState `cost_left`로 넘긴다. `cost_left ≤ 0`이면 페르소나는 LLM 없이 자리별 고정 안전 문구만 게시한다(의도 분류·턴 규칙은 그대로). 시간 초과·합의 채우기·제출은 그대로 진행한다.
- api/agent 경계 `SessionPorts`: `send_typing(seat, state)`, `get_generation()`, `candidate_typing()`, `enqueue(stimulus)`. 그래프는 이 포트로만 api 내부 상태에 닿는다.
