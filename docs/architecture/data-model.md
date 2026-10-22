# 데이터 모델

PostgreSQL 16 + pgvector. DDL은 `infra/db/schema.sql`. 모든 id는 UUID, 시각은 timestamptz. 계약(필드 이름·상태값)은 [contracts/README.md](../contracts/README.md)와 같다.

요소·카드·페르소나 유형·평가표 차원(`dimension_id`)은 **text id로만 저장**하고 목록과 뜻은 [product/rubric.md](../product/rubric.md)·[product/scenario.md](../product/scenario.md)가 정본이다. 채용 건 생성 때 그 시점의 정의가 `positions.scenario`에 복사되므로 이후 카탈로그가 바뀌어도 진행 중 채용 건은 영향받지 않는다.

참여자 값(`participant`): `candidate`, `p1`, `p2`, `p3`(AI 동료 자리). 메시지 sender에만 `system`·`assistant`가 더 있다(칸 `filled_by`에는 `system` 없음). 단계 값(`phase`): `keyword`, `problem`, `solution`, `proposal`(sessions.phase에만 `completed` 추가).

## 테이블

### 계정·채용 건

| 테이블 | 핵심 컬럼 | 비고 |
|---|---|---|
| companies | id, name | |
| admins | id, company_id, email(unique), password_hash | bcrypt |
| positions | id, company_id, name, mission_id text default 'debate', session_minutes int default 60 (60∣15, CHECK), catalog_version text, scenario jsonb, created_at | **요소 선택 컬럼 없음**(모든 채용 건이 5요소·카드 6장). `scenario`는 생성 때 조립·복사: `seats`([{seat: p1∣p2∣p3, persona_type, name, avatar}], 고정 3명: p1 고집형·p2 소극형·p3 비판형. `avatar`는 카탈로그 `personas.yaml`의 `avatar`(에셋 `scenarios/missions/<id>/assets/avatars/`의 파일 경로)를 조립 때 고정한 값이고 API의 `avatar_url`이 이 값으로 만들어진다), `keywords`(후보 목록), 키워드별 단계 고정 안(자리별 `scenario_option_key`·라벨), 카드 6장(시각 트리거 `offset_min`(`session_minutes` 비례 환산, 최소 20초)·담당·리포트 템플릿 문장), 평가표(BARS 척도: 5요소 19차원·수준별 앵커, 척도 버전), 자리별 프롬프트·공통 규칙·금지 문구 패턴·고정 안전 문구, 평가 프롬프트(`prompts/evaluator/*.md`)와 평가 금지 패턴, 단계 예산(10·18·18·14 비율)·침묵 초대·마감 임박 설정, 시간 초과·합의 채우기 고정 문장, 키워드별 자료 목록(본문은 `materials` 테이블의 같은 `catalog_version`). 형식은 [product/scenario.md](../product/scenario.md). 수정·삭제 없음 |
| invites | id, position_id, alias, email, token_hash, expires_at, used_at, cancelled_at, previous_session_id, reissued_at null, superseded_at null | 토큰 원문은 링크에만. 별칭 유일성은 부분 unique `(position_id, alias) WHERE superseded_at IS NULL`. **재발급**(`/reissue`)은 같은 행의 token_hash·expires_at만 바꾸고 `reissued_at` 기록. **재응시**(새 초대 + previous_session_id)는 이전 초대에 `superseded_at`을 찍어 같은 별칭을 다시 쓸 수 있게 한다 |

### 세션과 토론 상태

| 테이블 | 핵심 컬럼 | 비고 |
|---|---|---|
| sessions | id, invite_id(unique), status, phase, display_name text null, consented_at, started_at, ends_at, phase_started_at, phase_deadline_at, generation int default 0, invite_delivered_phases text[] default '{}', submitted_at, completed_at, ended_at, llm_failures int, debate_cost_usd numeric default 0, cost_usd numeric default 0, report_enqueued_at, approve_enqueued_at, eval_round int default 1, failed_stage, failed_reason | 초대와 동시에 생성(status invited, phase null). `display_name`은 지원자 표시 이름(동의 때 입력, 1~20자, 비우면 초대 별칭으로 저장, 시작 뒤 불변). 지원자 화면·AI 동료 호칭·디브리핑이 쓰고 관리자 화면은 별칭이 기본. status: invited·cancelled·in_progress·completed·report_ready·approving·reviewed·failed. phase: keyword·problem·solution·proposal·completed. `phase_deadline_at`은 누적 고정 마감([contracts/README.md](../contracts/README.md)), 스케줄러가 `now ≥ phase_deadline_at`이고 칸이 empty인 토론 단계를 **api `services/timeout.py`가 직접 시간 초과 채우기**한다. `generation`은 지원자 메시지 저장과 단계 전환마다 +1(그래프가 낡은 화자를 버리는 기준, [architecture/session-lifecycle.md](session-lifecycle.md) "세션 메일박스"). 카드 예정 시각(`phase_started_at` + 카드 `offset_min`)은 저장하지 않고 스케줄러가 계산한다. `invite_delivered_phases`는 침묵 초대가 **실제로 게시된** 단계(단계당 1회, 미전달이면 스케줄러가 idle을 다시 넣음). `debate_cost_usd`는 세션 중 토론 LLM 비용 누계(의도 분류·턴·페르소나·어시스턴트, 리포트 제외)로 하드 상한 판단에만 쓴다: `services/budget.py`가 읽어 `cost_left = SESSION_COST_HARD_USD − debate_cost_usd`를 DebateState로 넘긴다. `cost_usd`는 토론 + 리포트 평가 전체 비용 누계(관측). `eval_round`는 평가 실행 회차([contracts/internal.md](../contracts/internal.md) EvaluationSave run_id 규칙). submitted_at = 기획안 제출본 고정 시각(자동 제출 포함), completed_at은 같은 트랜잭션. **`ended_at`** = completed·failed(session)·cancelled로 바뀐 시각(디브리핑 기한·삭제 예정일 기준). 토큰 수는 `agent_runs.usage`에서 관측만 |
| messages | id, session_id, channel(room∣assistant), sender(candidate∣p1∣p2∣p3∣system∣assistant), phase, content, run_id, seq, client_msg_id, origin(spontaneous∣invited∣null), is_invite bool default false, card_ids text[] default '{}', cited_material_ids text[], meta jsonb, created_at | AI·시스템은 (session_id, run_id, seq) unique, 지원자는 (session_id, client_msg_id) unique. **origin는 지원자 room 메시지만**: 같은 단계에서 지원자를 부른 초대 메시지(`is_invite`) 뒤 첫 지원자 발언이면 `invited`, 나머지는 `spontaneous`. `is_invite`는 침묵 초대 발언. `card_ids`는 그 AI 발언이 전달한 카드 id 배열(한 발언이 두 카드의 첫 발언일 수 있음. 관리자 기록용, 지원자 API에는 나가지 않음). meta에 토큰·지연·모델·비용(`cost_usd`)·guard 결과·`stance_check`(`pass`∣`regenerated`∣`fallback`)·`kind`(`timeout_fill`∣`consensus_fill`∣`safe_phrase`∣null). `consensus_fill` 공지는 `fill_slot` 트랜잭션에서 칸 확정과 함께 저장된다(seq 5) |
| message_intents | message_id(pk), session_id, phase, intent(propose∣agree∣oppose∣question∣other), target_option_id null, scenario_option_key text null, new_option_label null, confidence(high∣low), addressed_to text[], fallback bool, model, latency_ms, created_at | 지원자 room 메시지마다 실시간 의도 분류 1행. `phase`는 그 메시지의 `messages.phase`(분류 입력·안·입장 저장 대상 단계, 현재 단계가 아님). 세션 메일박스가 `message` 자극을 꺼낼 때 순서대로 만들고, 자극이 낡아 응답을 건너뛰어도 분류·안·입장 반영은 한다([architecture/session-lifecycle.md](session-lifecycle.md) "세션 메일박스"). 입력에 그 단계 카탈로그 안 전체가 들어가 `scenario_option_key`로 아직 행이 없는 시나리오 안을 가리킬 수 있다. 실패면 intent=other·confidence=low·fallback=true. stances는 confidence=high일 때만 만든다. 토론 진행용. 평가 에이전트에는 참고 입력일 뿐 수준을 직접 정하지 않음 |
| options | id, session_id, phase, label, proposer(participant), scenario_option_key text null, source_message_id, created_at | 단계별 후보 안. **처음 말한 순간 한 번만** 만들어지므로 proposer가 최초 제안자(`get_contributions` 근거). 시나리오 안은 (session_id, phase, scenario_option_key) unique, 자유 안은 (session_id, phase, source_message_id) unique. 지원자가 시나리오 안을 먼저 말하면(의도 분류 `scenario_option_key`) 그 키로 행을 만들고 proposer=candidate, 이후 AI의 같은 키 제안은 기존 행을 돌려준다. 키워드 버튼 확정으로 만든 행, 카드 기준안 잠금으로 만든 행(proposer = 카드 첫 담당 자리), 시간 초과 기본 안(기준안 행이 없을 때, 공지 자리가 proposer)은 source_message_id null 허용 |
| stances | id, session_id, phase, participant, option_id, stance(support∣oppose), source_message_id, run_id null, created_at | append-only 입장 이력. 지원자는 의도 분류(confidence high만)가, AI는 결정적 노드가 자기 메시지에 연결해 쓴다. 현재 지지는 참여자당 하나 = 참여자별 단계의 마지막 support 행의 안. 다른 안 support는 이전 지지를 대체하고, 그 뒤 같은 안에 oppose가 있으면 철회(null). (session_id, source_message_id, participant, option_id) unique |
| slots | (session_id, phase) pk, status(empty∣confirmed), value null, option_id null, filled_by(candidate∣p1∣p2∣p3) null, resolution(consensus∣timeout) null, support jsonb null, confirmed_at, run_id null | start에서 keyword·problem·solution 3행(empty) 생성. 초안 컬럼 없음. `value`·`option_id`·`filled_by`·`resolution`·`support`(확정 순간 지지 현황 `[{option_id, supporters}]`)는 확정 때 한 번 쓰고 바꾸지 않음. 지원자 확정의 option_id는 결정적 연결(옵션 키·정규화 라벨 일치)만, 아니면 null |
| proposals | session_id(pk), sections jsonb, revision int, updated_at, submitted jsonb null, submitted_at null, auto_submitted bool, submit_blank_sections text[] null, blank_warning_shown bool null | 기획안 초안과 제출본. sections = {keyword, problem, solution, rationale, expected_effect, idea_credit}(모두 문자열, 필드 id는 [product/scenario.md](../product/scenario.md) 기획안 템플릿). start에서 빈 초안 생성, proposal 단계 시작 때 빈 앞 3칸을 확정 칸 값으로 채움. 제출 때 sections를 `submitted`에 복사(불변). `auto_submitted`는 `ends_at` 자동 제출이면 true(WS `session.ended{reason: timeout}`). `submit_blank_sections`·`blank_warning_shown`은 제출 전 null, 제출 때 한 번 씀(빈 칸 없으면 `[]`, 경고값은 수동 생략 false·자동 제출 null, [contracts/api.md](../contracts/api.md) 제출) |
| agent_runs | run_id(pk), session_id, graph(debate∣assistant), stimulus(message∣phase_started∣idle∣card_due) null, ref_id null, generation int null, status(queued∣started∣finished∣deferred∣skipped∣failed), decision jsonb, usage jsonb, queued_at, started_at, finished_at | 자극 실행 기록이자 메일박스의 영속 사본. `ref_id`는 자극 대상(message면 message_id, card_due면 card_id). 상태값의 실행 완료는 `finished` 하나다. `generation`은 `message` 자극이면 그 메시지 저장이 올린 값, 다른 자극은 enqueue 때 값. `queued`로 만들고 큐에 넣는다. 재시작하면 `queued`·`started`를 같은 run_id로 다시 넣고, 실행 중 `started` 200초 초과는 `failed`로 닫고 같은 run_id로 다시 넣는다. `deferred`(지원자 입력 중이라 미게시, 자극은 새 run_id로 재큐)·`skipped`(낡은 자극). `decision`은 턴 매니저 결정 스키마([contracts/README.md](../contracts/README.md)), `usage`는 llm_calls·retries·tokens_in/out·latency_ms·tool_calls·cost_usd. /state의 active_runs 원천 |
| connection_gaps | id, session_id, disconnected_at, reconnected_at | in_progress 중 WS 연결 없던 구간. 사실 지표·확신도 참고 |
| material_views | id, session_id, material_id text, viewed_at | 자료 탭 열람 기록. 평가하지 않는 사실 |
| assistant_calls | id, session_id, request_message_id, response_message_id, cited_material_ids text[], tool_calls jsonb, created_at | AI 활용 사실 기록(평가하지 않음). 세션 60회 한도. `tool_calls`는 tool-calling 루프의 도구 호출 목록 `[{name, query?, material_id?}]`(요청당 최대 3) |
| debrief_surveys | session_id(pk), answers jsonb([{q, value}]), free_comment text null, created_at | 종료 설문 2문항(natural·fair, 1~5) + 선택 자유 의견 `free_comment`(500자) |

### 관찰·분석·리포트

| 테이블 | 핵심 컬럼 | 비고 |
|---|---|---|
| session_cards | session_id, card_id text, locked_option_id null, delivered_seats jsonb default '[]', locked_at null | **카드 진행 상태**. PK(session_id, card_id). `card_due` 첫 처리 때 행 생성. 문제 상황 카드는 그때 기준안을 `locked_option_id`·`locked_at`으로 잠금(지원자 현재 지지 안이 아닌 첫 시나리오 안, 이후 불변, 시간 초과 기준안으로도 씀). 내부 `POST /messages`의 `card_ids`에 든 카드마다 sender 자리를 `delivered_seats`에 추가(공동 전달). 담당 자리 전원이면 delivered(아니면 다음 run이 남은 화자를 이어서 말하고 스케줄러가 `card_due`를 다시 넣음). DebateState `card_progress`의 원천 |
| observations | id, session_id, card_id text, element text, phase, fired_at, trigger_message_id null, run_id, created_at | **카드 관찰 기록**(턴 매니저 결정적 노드 → assessment-mcp `record_observation`). 카드의 **첫 카드 발언이 게시된 뒤** 기록하고 `trigger_message_id` = 그 메시지, `fired_at` = 그 시각. `honesty_credit`은 기획안 단계 `phase_started` 실행에서 기록하고 `trigger_message_id` = 단계 시작 시스템 안내. (session_id, card_id) unique. 관찰 창은 저장하지 않고 카드의 `window` 규칙([product/scenario.md](../product/scenario.md))으로 워커가 계산. 레이블·수준은 두지 않음 |
| evaluation_runs | session_id, run_id, rubric_version, prompt_versions jsonb (`{common: 해시, <element>: 해시}`), model, elements jsonb (ElementSummary 5행), has_failed bool (`evaluate_failed` 차원이 하나라도 있으면 true), created_at | 평가 실행 1행. PK(session_id, run_id). `evaluations` 차원 행과 **한 트랜잭션**으로 저장(`save_evaluation`). 같은 `(session_id, run_id)` 재저장은 기존 결과를 돌려줌(재시도 이어하기·리포트 재생성 재사용). 요소 요약의 정본이고 draft는 여기서 읽는다(체크포인트 의존 없음) |
| evaluations | id, session_id, run_id, dimension_id text, level int null (1~5), no_record_reason text null (no_activity∣evaluate_failed∣not_applicable∣event_not_fired∣auto_submitted_blank), evidence_refs jsonb (문자열 배열, 형식은 [contracts/api.md](../contracts/api.md) "근거 참조"), rationale text, confidence text null (high∣medium∣low), created_at | 세션 후 **평가 에이전트**가 차원마다 낸 판단 1행(형식은 contracts의 `DimensionEvaluation`). (session_id, run_id) → evaluation_runs FK. `level`과 `no_record_reason`은 둘 중 하나만(CHECK), 기록 없음이면 `confidence` null. UNIQUE(session_id, dimension_id, run_id). 버전·모델은 evaluation_runs에 |
| reports | session_id(pk), stage(draft∣final), content jsonb, content_hash, eval_run_id, generated_at, reviewed_at, attempts, usage jsonb, reviewer_name | 리포트 본문(`content.elements[]`·`dimensions[]`·`facts`·`contribution_check`·`seats`·`slots`·`cards`·`debrief`, [contracts/README.md](../contracts/README.md)). `eval_run_id`는 이 리포트가 읽은 `evaluation_runs.run_id`. **총점·합계 컬럼 없음**. 지원자 목록의 요소별 수준 칩·low 차원 수는 `content.elements[]`·`dimensions[]`에 조정을 병합해 계산. content는 불변이고 **final은 draft content 그대로(해시 동일)**, 조정은 조회 때 병합 |
| rubric_adjustments | id, session_id, dimension_id text, level int null (1~5), no_record_reason text null, memo text NOT NULL, adjusted_by(admin_id), reviewer_name, adjusted_at | 담당자 **차원 단위** 조정(append-only, 같은 차원은 마지막 행이 유효). `level`과 `no_record_reason`은 둘 중 하나만. GET report 때 병합해 `adjusted_level`·`adjust_memo`와 요소 수준 재계산에 쓴다 |
| report_notes | id, session_id, text, reviewer_name, admin_id, created_at | 검토 완료 뒤 메모(값 불변) |

### 에이전트·검색 저장소

| 테이블 | 핵심 컬럼 | 비고 |
|---|---|---|
| checkpoints 등 | LangGraph PostgresSaver 기본 테이블 | report-worker만 thread `report:{id}:{eval_round}`로 쓴다(평가 회차마다 새 thread). api의 토론 그래프·어시스턴트는 체크포인터를 쓰지 않는다 |
| materials | (catalog_version, material_id) pk, keyword_key, title, content(Markdown), created_at | 자료 **원문의 버전별 사본**. `scripts/seed.py`가 카탈로그 자료를 그 catalog_version으로 시드(이전 버전 행은 지우지 않음). 목록·본문 조회는 api가 채용 건 `catalog_version`으로 여기서 읽는다(카탈로그 파일 직접 읽기 없음) |
| material_chunks | id, material_id, keyword_key, chunk_no, content, embedding vector(1024), model, catalog_version | rag-mcp만 쓴다. (catalog_version, material_id, chunk_no) unique. 검색은 세션의 확정 키워드(`keyword_key`)와 catalog_version으로 필터. 원문은 같은 버전의 `materials` 행 |

## 쓰기 주체

| 테이블 | 쓰는 경로 |
|---|---|
| messages(candidate) | api(WS 수신, 큐 밖 저장·generation +1·enqueue) |
| message_intents, 지원자 stances·options | api 메일박스(`message` 자극 처리 때 의도 분류) |
| messages(p1~p3·system), AI options·stances | debate-mcp → 내부 API (턴 매니저 결정적 노드). 합의 채우기 공지는 `post_message`가 아니라 `fill_slot`(내부 `slots/fill`) 트랜잭션이 칸과 함께 저장. 시간 초과 공지 메시지와 그 기준안 행만 api 스케줄러가 직접 |
| sessions.generation·invite_delivered_phases·debate_cost_usd·cost_usd | api(메시지 저장·advance_phase·내부 messages·LLM 래퍼 usage 누적) |
| session_cards | api(`card_due` 처리 시 생성·잠금, 내부 messages의 `card_ids`로 전달 추가) |
| agent_runs | api 메일박스 |
| materials | `scripts/seed.py` |
| slots | 지원자 REST(confirm), debate-mcp `fill_slot`(합의 채우기), api 스케줄러(시간 초과 채우기) |
| proposals | 지원자 REST, api(proposal 시작 채우기·제출) |
| observations | assessment-mcp `record_observation` (턴 매니저 결정적 노드) |
| evaluation_runs, evaluations | assessment-mcp `save_evaluation` (report-worker 평가 에이전트), 한 트랜잭션 |
| rubric_adjustments | review 트랜잭션(api) |
| reports | 내부 report/draft·final (report-worker), review 트랜잭션(api) |

## 세션 상태

상태·단계 전이와 재시작 복구는 [session-lifecycle.md](session-lifecycle.md)가 정본이다. 상태·단계 변경은 api `SessionService` 한 곳만 한다.

## 보관과 삭제 (90일)

세션 종료 시각 `sessions.ended_at`(completed·failed(session)·cancelled 모두 설정) 후 90일. 삭제 예정일 `purge_at = ended_at + 90일`은 관리자 목록·리포트에 표시한다. api 스케줄러가 틱마다 `services/purge.py`의 `purge_expired`로 `ended_at + 90일 ≤ now`인 세션을 세션 단위로 지운다. 끝나지 않은 세션(invited·in_progress)은 대상이 아니다.

| 지우는 것 | 남기는 것 |
|---|---|
| messages, message_intents, options, stances, slots, session_cards, proposals, agent_runs, connection_gaps, material_views, assistant_calls, debrief_surveys, observations, evaluation_runs, evaluations, reports, rubric_adjustments(조정 메모 포함), report_notes, checkpoints(그 세션의 thread, 실제로는 `report:` thread), sessions 행, invites의 email·alias·token_hash | positions(채용 건 정의와 지원자 수), companies, admins, materials·material_chunks(세션 무관) |

LangSmith는 프로젝트 보존 기간을 90일로 둔다.

## 기업 간 격리와 스키마 변경

기업 간 격리 규칙은 [security.md](security.md). 새 DB 초기화와 스키마를 바꾼 뒤 로컬 DB를 다시 만드는 절차는 [guides/database.md](../guides/database.md).


## 음성 상태·작업·사용량

`voice_states(session_id PK/FK CASCADE, data jsonb)`는 턴·generation·epoch·발언권·마감의 원천이다. `speech_jobs(id PK,session_id FK CASCADE,kind,data jsonb,audio bytea,created_at,expires_at)`는 최종 본문 해시·voice·작업 상태·시도별 사용량·전사·재생 확인을 보존한다. PCM 원음/TTS bytes는 임시이고 성공 전사·세션 종료·24시간 만료 정책으로 비운다. `speech_quotas(scope PK,data jsonb)`는 자원/기간별 월 사용량·세션 예약·호출 속도를 행 잠금으로 직렬화한다. scope는 세션 삭제에 종속되지 않는다.

모델과 DDL은 일치해야 한다. 로컬 볼륨은 DB 가이드에 따라 별도 재생성하며 공유 개발 스택을 자동 초기화하지 않는다.
