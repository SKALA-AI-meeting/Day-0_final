# api (FastAPI)

음성 토론의 상태·전사·오류·비용은 [음성 구현](../architecture/voice.md)을 따른다.

인증·REST·WebSocket·세션 시계·메일박스·DB 쓰기를 맡는 한 프로세스다(공개 8000 + 내부 8001). 실행·테스트 명령은 `services/api/README.md`, 결정 근거(R1~R7 등)는 [decisions.md](../decisions.md). 경로·필드·오류 코드는 [contracts/README.md](../contracts/README.md), 테이블은 [architecture/data-model.md](../architecture/data-model.md), 단계·칸·카드 규칙은 [product/scenario.md](../product/scenario.md), 차원 id·수준 앵커·기록 없음 사유·사실 지표는 [product/rubric.md](../product/rubric.md)(BARS)가 정본이다. 칸 결과는 `resolution: consensus | timeout`, 채운 사람은 자리 id `filled_by: candidate | p1 | p2 | p3`으로 쓴다(시간 초과도 공지한 자리 id, `system` 값은 없다).

## 구조

```
services/api/app/
  main.py, run.py        FastAPI 앱 2개(공개 8000: /admin,/s,/sessions,/ws,/assets · 내부 8001: /internal). 한 asyncio 루프에서 hub·메일박스·스케줄러 공유
  config.py, db.py       env, SQLAlchemy async
  models/                테이블 모델([architecture/data-model.md](../architecture/data-model.md))
  auth.py                JWT, 역할 의존성, bcrypt, 초대 토큰 해시
  hub.py                 세션별 WS 연결, 세션별 단일 송신 큐(seq 부여), typing 상태(만료 6초)
  mailbox.py             세션별 메일박스: asyncio Task 1개가 자극 큐를 직렬 소비(R5-1)
  ports.py               SessionPorts: api/agent 경계(typing 전송·generation 조회·자극 enqueue)
  queue.py               RabbitMQ publish(persistent, publisher confirm)
  masking.py             전화번호·이메일 마스킹
  scheduler.py           5초 루프
  services/
    session.py           SessionService: 상태·단계 전이의 유일한 소유자
    scenario.py          미션 카탈로그 로드(catalog_version), 고정 자리 3·카드 6장 조립, positions.scenario 복사(평가 프롬프트·금지 패턴 포함), 자료 원문 시드
    consensus.py         합의 판정·시간 초과 선택 함수(아래). api fill 검사와 DebateState가 같은 함수
    timeout.py           단계 마감 결정적 채우기(R5-2, 그래프 없음)
    debate.py            메시지 저장·generation 증가, 의도 분류 반영(안·입장), DebateState 계산, 칸 확정·fill
    proposal.py          기획안 조회·디바운스 저장(revision 검사)·제출
    facts.py             사실 지표 계산([product/rubric.md](../product/rubric.md) 6장), record 타임라인
    budget.py            세션 LLM 비용 누적(`debate_cost_usd`·`cost_usd`)·하드 상한, DebateState `cost_left` 계산
    phases.py            단계 전환(start·advance_phase)과 시스템·AI 메시지 저장, run 기록(queue_run)
    context.py           공개·내부 앱이 함께 쓰는 객체(hub·메일박스·runner·큐·DB·시계) 조립
    views.py             지원자 쪽 읽기 모델: State 스냅샷, 자료, 디브리핑, 참여자(페르소나 유형 비노출)
    reports.py           평가 저장(save_evaluation)·리포트 draft/final·담당자 검토·조회 병합·전체 기록
    admin.py             채용 건 조립 호출·초대·재응시·지원자 목록
    purge.py             보관 기한(ended_at + 90일) 삭제, 스케줄러가 호출
    clock.py             시계 주입(가짜 시계 테스트)
    common.py            오류, 커밋 뒤 효과(WS·자극·큐), 시각 변환, 직렬화
    test_sessions.py     테스트 API: 단계 시드 재생·시간 당기기(`ENABLE_TEST_API=1`일 때만)
  agent/                 debate/ · assistant/ · intent/ · guard/ · llm/ · mcp_client/ · runner.py ([architecture/agents.md](../architecture/agents.md))
  routers/               admin.py · candidate.py · ws.py · internal.py
```

## 인증

[contracts/README.md](../contracts/README.md) 인증 생명주기 표가 정본. 의존성 `require_admin`(company_id 필터), `require_candidate(session_id 일치)`, `require_internal`(X-Internal-Token). 내부 앱은 8001에 따로 띄우고 compose에서 공개하지 않는다. assessment-mcp는 자기 토큰(`X-Assessment-Token`)을 따로 검사하고, api 내부 API는 다른 MCP와 같이 X-Internal-Token으로 부른다.

- 입장 `GET /s/{token}` 응답에 `session_id`와 초대 이름(표시 이름 기본값)을 함께 낸다. `POST /s/{token}/consent`·`POST /s/{token}/resume` 응답에도 `session_id`를 낸다(R5-19). 새 브라우저에서 완료 링크로 들어와도 `session_id`로 `/sessions/{id}/debrief`를 부를 수 있다.
- 시작 전(`invited`, 동의 후) `GET /sessions/{id}/state`를 허용한다. 대기 화면의 팀 소개용으로 참여자 이름·아바타(`participants`)와 세션 길이·단계 예산만 채우고 메시지·칸·기획안은 비어 있다(R5-19).

## 채용 건 조립 (`services/scenario.py`)

`POST /admin/positions {name, session_minutes?, mission_id?}` 때 한 번 계산해 `positions.scenario`에 복사·고정한다([product/scenario.md](../product/scenario.md) 6장). **요소 선택은 MVP에 없다**(R2): 모든 채용 건이 5요소·카드 6장·차원 19개를 쓴다.

1. 미션 폴더 해시 → `catalog_version`(`scenarios/version.py`, rag-mcp 색인과 같은 함수).
2. 자리 3개는 **고정**(R2): `p1` 고집형 강한결, `p2` 소극형 윤하람, `p3` 비판형 서지안. `seats: [{seat, persona_type, name, avatar}]`로 저장(D13-3, `avatar`는 카탈로그 `personas.yaml`의 `avatar` 필드). 동조형·발산형과 요소 → 페르소나 결정 규칙은 MVP 이후 확장이다([product/scenario.md](../product/scenario.md)).
3. 카드 6장(카드별 `offset_min`을 `session_minutes`에 비례 환산, 최소 20초), 키워드 후보 4개와 키워드별 문제·해결책 고정 안(해결책은 "방향" 단위)·자료 목록, 자리별 프롬프트·공통 규칙·금지 문구 패턴·고정 문장(칸 채우기·시간 초과·안전 문구), 단계 예산(10·18·18·14 비율)·마감 임박·침묵 초대 설정, 척도(차원 19개·수준 앵커·`rubric_version`)와 평가 프롬프트·평가 금지 패턴을 복사. 워커는 이 복사본으로 평가하므로 카탈로그 파일이 바뀌어도 같은 채용 건의 평가 프롬프트는 같다.
4. 자료 원문 시드(R5-18): 카탈로그 `materials/<keyword>/*.md` 원문을 `catalog_version`별로 DB에 넣는다(이미 그 버전이 있으면 건너뜀). 목록·검색·본문 조회가 모두 같은 `catalog_version`을 쓴다. 카탈로그 파일을 바꿔도 기존 채용 건은 자기 버전 원문을 본다.

응답과 지원자 쪽 API는 자리 id·`display_name`·`avatar_url`만 내보내고 `persona_type`은 관리자 리포트·디브리핑·내부 API에서만 낸다.

## 이름과 프로필 이미지 (D13-3)

- 지원자 표시 이름: 동의 화면에서 입력(기본값 초대 별칭, 1~20자, 공백 정리·마스킹). `POST /s/{token}/consent {display_name?}`에 받아 `sessions.display_name`에 저장, 비우면 별칭. 시작 뒤에는 바꾸지 않는다. `/state.participants`의 candidate 이름, 페르소나 컨텍스트의 호칭("OO님"), 디브리핑이 이 값을 쓴다. 관리자 리포트·목록은 별칭을 기본으로 쓰고 표시 이름이 다르면 함께 보인다.
- AI 동료 프로필: 카탈로그 정적 에셋 `scenarios/missions/<id>/assets/avatars/`를 api 공개 앱이 `/assets/missions/<mission_id>/avatars/<file>`로 읽기 전용 서빙한다(StaticFiles, 캐시 헤더). 자리별 `avatar_url`은 조립 때 고정한 `avatar` 경로로 만든다. 파일 이름은 중립(`avatar-01.png` 등)이어야 하고 유형 이름을 쓰지 않는다(조립 때 검사, 유형 이름이 들어 있으면 조립 실패).
- 지금 이미지는 임시이며 교체는 에셋 파일과 `avatar` 경로만 바꾼다. 진행 중 채용 건은 조립 때 고정한 경로를 계속 쓰므로, 교체 시 파일을 덮어쓰지 않고 새 파일 이름으로 추가한다.
- 계약 필드(정본 [contracts/README.md](../contracts/README.md) 공통·[architecture/data-model.md](../architecture/data-model.md)): `/state.participants[]`·`GET /internal/sessions/{id}`·`get_debate_state.seats`·Report·debrief `seats[]`에 `display_name`·`avatar_url`(지원자는 `display_name`만), consent 요청 `display_name`, `sessions.display_name` 컬럼, `positions.scenario.seats[].avatar`.

## 세션 메일박스와 generation (R5-1)

세션 Lock·`lock_held`·`handled` 집합은 없다. 세션마다 **메일박스** 하나(asyncio Task 1개 + 자극 큐)가 그래프 자극을 들어온 순서대로 하나씩 처리한다.

- 큐 **밖**에서는 지원자 메시지 저장, 같은 트랜잭션의 `sessions.generation` +1, `message` 자극 enqueue만 한다. `message` 자극의 generation은 이 저장이 올린 값이다(enqueue 때 다시 읽지 않음). 그 뒤 더 새 메시지가 저장되면 이 자극은 턴·발언만 skipped가 된다.
- 메일박스는 `message` 자극마다 들어온 순서대로 **의도 분류와 안·지지 반영을 반드시 수행한다**. 분류와 반영은 저장된 `messages.phase`를 기준으로 한다. 자극이 낡았을 때(generation이 현재 값보다 작거나 메시지 단계가 현재 단계와 다름) 건너뛰는 것은 턴 선택·페르소나 발언 생성뿐이다(`agent_runs.status = skipped`). 그래서 연속 메시지에서도 앞 메시지의 안·입장·최초 제안 사실이 남는다.
- 그래프는 화자마다 게시 직전에 SessionPorts로 generation을 다시 읽고, 실행 시작 때와 다르면 남은 화자를 버린다(새 메시지 자극이 다시 정한다).
- `advance_phase`는 `phase_started` 자극을 큐에 넣기만 한다(직접 실행하지 않음).
- `card_due`를 꺼내면 그래프 실행 전에 한 트랜잭션에서 `session_cards` 행을 만들고, 문제 상황 카드(`flexibility_majority`·`listening_pressure`)면 기준안(지원자 현재 지지 안이 아닌 첫 시나리오 안, 행이 없으면 proposer = 첫 담당 자리로 생성)을 `locked_option_id`에 잠근다(한 번만). 그래프는 DebateState `card_progress[].locked_option_id`로 읽는다.
- 멱등은 `agent_runs.status`와 DB 멱등 키(메시지 (run_id, seq), 안, 입장, 칸 (session, phase), 관찰 (session, card_id))로만 한다. 이미 `finished`인 run_id는 다시 실행하지 않는다.
- 메일박스는 메모리이므로 api는 단일 프로세스로 운영한다. 재시작하면 스케줄러가 미처리 run과 마감을 다시 집는다.

## SessionService

메서드: `create_for_invite`, `reissue`, `consent(display_name?)`, `start`, `advance_phase`, `submit(reason)`, `cancel`, `fail(stage, reason)`, `mark_report_ready`, `approve`, `mark_reviewed`, `regenerate`. 모든 전이는 현재 상태를 검사하고 멱등이다. 상태 머신은 [contracts/README.md](../contracts/README.md).

- `start`: `in_progress`, `phase=keyword`, `started_at`·`ends_at`·`phase_started_at`·`phase_deadline_at` 확정, 칸 3행(`empty`)·기획안 빈 초안 행 생성, 첫 시스템 안내(시나리오 `stage_start` 문구, 키워드 후보 4개 포함) 저장, `phase_started` enqueue.
- `advance_phase(reason)`: 칸 확정과 **같은 트랜잭션**에서 `phase`·`phase_started_at`·`phase_deadline_at`(누적 고정: `started_at + 예산 1..k의 합 × session_minutes/60`) 갱신, 시스템 안내 저장. keyword 확정이면 그 키워드 자료를 연다. proposal 진입이면 기획안 앞 3칸 중 빈 칸을 확정 칸 값으로 채운다(revision+1). 커밋 뒤 WS `slot.confirmed` → `phase.changed` → `timer` → (`materials.opened`) → `message.new`, 이어서 `phase_started` enqueue.
- `submit(reason: submitted∣timeout)`: 한 트랜잭션에서 기획안 `sections`를 `submitted`에 복사(불변), `submitted_at`, timeout이면 `auto_submitted=true`, `status=completed`·`phase=completed`·`completed_at`·`ended_at` → 큐 `report.generate` 발행(confirm 뒤 `report_enqueued_at`) → WS `session.ended{reason}`. 그 뒤 들어오는 내부 쓰기는 409 `conflict`이므로 진행 중 run의 아직 말하지 않은 화자는 게시되지 않고, 메일박스는 남은 자극을 버리고 멈춘다. 중간 상태는 없다.
- `cancel`·`fail(stage, reason)`: `ended_at`을 함께 기록한다(R5-21). `fail`은 `failed_stage`(session∣report)와 담당자 행동 안내. 세션 실패는 리포트를 만들지 않는다.
- `reissue`: 아래 "초대 재발급".
- `regenerate`: failed_stage=report인 세션만. 초안 전 실패(`failed_step` draft)는 남은 draft를 지우고 completed로 돌려 재발행, 확정 실패(`failed_step` finalize)는 approving으로 돌려 같은 `eval_round`의 `report.approve`를 재발행한다(정본 [contracts/api.md](../contracts/api.md)). 초안 전 실패 경로: 최신 `evaluation_runs.has_failed`가 false면 `sessions.eval_round`를 그대로 두어 워커가 저장 평가를 재사용하고, true면 `eval_round`를 +1 해 새 run_id(`uuid5(session_id, "evaluate:"+eval_round)`)로 다시 평가하게 한다(R5-14). 큐 본문은 `{session_id, attempt, eval_round}`. 리포트 그래프 thread_id는 `report:{session_id}:{eval_round}`라 새 회차는 이전 interrupt 체크포인트를 쓰지 않는다. 승인 때 발행하는 `report.approve` 본문에도 draft를 만든 `eval_round`를 넣는다.

## 지원자 메시지 처리 (`services/debate.py`)

1. WS `message.send{client_msg_id, content}` → in_progress 확인(아니면 오류 프레임 `session_ending`) → 마스킹 → 저장(`(session_id, client_msg_id)` 멱등, `phase`, `origin`: 같은 단계에서 지원자를 부른 초대 메시지(`is_invite`) 뒤 첫 지원자 발언이면 `invited`, 아니면 `spontaneous`)과 `sessions.generation` +1을 한 커밋 → `message.ack{run_id}`·`message.new` 브로드캐스트(ack은 의도 분류를 기다리지 않는다) → `agent_runs` 행 `queued` → `message` 자극 enqueue.
2. 메일박스가 그 자극을 꺼내면 낡았는지와 상관없이 먼저 **실시간 의도 분류**(`agent/intent`, `MODEL_INTENT`, 타임아웃 10초, 재시도 없음)를 한다. 입력은 메시지·**저장된 `messages.phase`**·그 단계의 **카탈로그 안 전체**(아직 발언되지 않은 것 포함, `scenario_option_key`와 라벨)와 그 단계의 이미 생긴 안 목록·직전 5개(R5-6). 메일박스가 밀린 사이 단계가 바뀌어도 현재 단계의 카탈로그로 분류하지 않는다. 출력은 `confidence: high|low`를 포함한다(R4·R5-22). `message_intents` 저장 → 토론 상태 반영:
   - `propose`: 대상이 카탈로그 안이면 그 `scenario_option_key`로 안 행을 찾거나 만든다(없으면 proposer=candidate, source_message_id=이 메시지). 이후 AI가 같은 안을 propose해도 같은 행을 쓰므로 최초 제안자가 보존된다. 카탈로그 밖이면 `new_option_label`로 자유 안을 만든다. 그다음 support.
   - `agree`+target이면 support, `oppose`+target이면 oppose. `question`·`other`는 입장 없음.
   - 실패·스키마 위반은 `intent=other`·`fallback=true`로 두고 진행.
3. 자극이 낡지 않았으면 이어서 토론 그래프 `stimulus=message` 실행(AgentRunner). 낡았으면 그래프를 부르지 않고 `skipped`로 닫는다(뒤의 메시지 자극이 함께 처리). 그래프 결과의 게시·안·입장·칸 채우기는 debate-mcp → 내부 API로 들어온다.

**typing (R5-9)**: 지원자 클라이언트는 입력 중 5초마다 WS `typing{state: start}`를 다시 보낸다. hub는 저장하지 않고 메모리에 마지막 갱신 시각만 두며 6초 동안 갱신이 없으면 만료(stop)로 본다. DebateState의 `candidate.typing`과 그래프의 끼어들기 대기가 이 값을 읽는다. 페르소나 typing은 그래프가 SessionPorts로 hub에 보낸다(`typing{participant, state}`), `/state.typing`은 hub 상태.

**WS 순서 (R5-10)**: 모든 서버 프레임은 세션별 단일 송신 큐를 거치며 봉투에 세션 단위 `seq`(1씩 증가)를 붙인다. 클라이언트는 `seq`가 비면 `/state`를 다시 읽는다. 다른 경로에서 소켓에 직접 쓰지 않는다.

## 합의 판정과 시간 초과 선택 (`services/consensus.py`)

순수 함수 `judge(options, stances, default_option_id=None)`(`services/consensus.py`)가 합의 안·시간 초과 안·참여자별 현재 지지를 함께 돌려준다. 지원자 입장은 의도 분류가 `confidence: high`일 때만 `stances` 행이 되므로 입력은 안과 입장 이력뿐이다. 수치·우선순위는 [product/scenario.md](../product/scenario.md)가 정본이고 계약은 입력과 결과만 고정한다.

- 현재 지지 = **참여자당 하나**. 다른 안을 support하면 이전 지지를 대체하고, 같은 안에 대한 후속 `oppose`는 그 지지를 철회한다(R5-7). 지원자 입장은 `confidence: high`인 `agree`·`propose`만 지지로 센다(R4).
- consensus: 지원자 포함 4명 중 3명 이상 지지. 성립 즉시 판정한다(합의 대기 시간 없음, R4).
- timeout: 지지자 최다 → 동률이면 지원자 지지 안 → 먼저 제안된 안 → 없으면 단계 기준안(키워드는 `default_keyword`, 문제 상황은 잠긴 기준안). 기준안 행이 없으면 채우기 전에 만든다(proposer는 채우는 자리).
- 같은 함수가 DebateState와 내부 `POST /slots/fill` 검사에 쓰인다. 그래서 AI는 합의 전에 칸을 채우지 못한다(409 `no_consensus`).

## 칸 (`confirm`·`fill`·시간 초과)

칸 상태는 `empty | confirmed` 두 개다(R5-5). 지원자 초안 저장(`PUT slots`)·`filled` 상태·WS `slot.filled`는 없다. 지원자가 칸에 입력 중인 값은 화면에만 있고 확정할 때 한 번 보낸다.

- `POST /sessions/{id}/slots/{phase}/confirm{value}`: 현재 단계만. keyword는 후보 4개의 옵션 키로만 확정(422 `invalid_keyword`). problem·solution은 **확정 경로에 LLM이 없다**: 의도 분류가 만든 안 목록과 정규화 문자열 일치, 또는 카탈로그 키와 일치하면 그 `option_id`, 아니면 `option_id=null`(새 안을 만들지 않음). `filled_by=candidate`, `resolution=consensus`, 그 순간 지지 현황 스냅샷(`support`) 저장 → `advance_phase("confirmed")`. 같은 값 재요청은 200 기존 결과, 다르면 409 `slot_confirmed`.
- 내부 `POST /internal/sessions/{id}/slots/fill{option_id, seat, content, run_id}` → `{slot, phase, message}`(debate-mcp `fill_slot`): 그래프의 합의 채우기 전용. 경로에 단계가 없고 대상 단계는 option의 phase다. `filled_by` 인자는 없고 `seat`가 공지 화자이자 `filled_by`다(`resolution=consensus`). 판정 순서: 같은 run_id로 이미 확정됐으면 기존 칸과 공지를 그대로 돌려준다. 아니면 409 `phase_not_open` → `slot_confirmed` → `no_consensus` 순서로 검사한다. 통과하면 **한 트랜잭션**에서 칸 confirmed → 공지 메시지 저장(sender=seat, `content`, (run_id, seq 5), `meta.kind=consensus_fill`) → `advance_phase("confirmed")`(generation +1). 커밋 뒤 WS는 `message.new`(공지) → `slot.confirmed` → `phase.changed` → `timer` 순서로 보낸다(이어서 시스템 안내 `message.new`). 409면 칸·공지 모두 저장하지 않는다. 시간 초과 채우기는 그래프가 하지 않는다(아래).
- 모든 확정은 `status = empty`인 행만 바꾸는 조건부 갱신이다. 지원자 확정·합의 채우기·시간 초과가 겹치면 먼저 커밋한 쪽만 이기고 나머지는 409 `slot_confirmed`.

**시간 초과 채우기 (`services/timeout.py`, R5-2)**: 그래프가 아니라 api가 결정적으로 직접 한다. 스케줄러가 마감 지난 `empty` 칸을 찾으면 `judge`의 `timeout_option_id`(필요하면 기준안 행 생성) → 시나리오 고정 문장(R7: "시간이 다 돼서 대화에서 가장 많이 모인 의견인 X로 할게요.", 이름만 치환)을 우선순위 자리 이름으로 게시(고정 자리에서는 서지안 `p3`, 우선순위 정본은 [product/scenario.md](../product/scenario.md)) → 칸 확정(`resolution=timeout`, `filled_by`=게시한 자리) → `advance_phase("timeout")`. LLM 없음, 멱등 키 `(session, phase)`.

## 기획안

- `GET/PUT /sessions/{id}/proposal`: in_progress 언제든 저장. `revision`이 다르면 409 `conflict`. 6칸 모두 문자열.
- `POST .../proposal/submit`: phase=proposal에서만(아니면 409 `phase_not_open`). `submit("submitted")`. 멱등.
- 제출 이후 쓰기(메시지·칸·기획안·어시스턴트)는 409 `session_ending`.

## 자료와 어시스턴트

- `GET /sessions/{id}/materials`: keyword 칸 확정 전 `locked=true`·빈 목록. 확정 키워드의 자료 목록은 `positions.scenario`의 키워드별 자료.
- `GET .../materials/{material_id}`: 열린 자료가 아니면 423 `not_released`. 본문은 채용 건 `catalog_version`으로 DB에 시드된 원문(R5-18). `material_views` 기록.
- 내부 `GET /internal/sessions/{id}/materials`(+본문): rag-mcp가 잠금·키워드·catalog_version을 확인하고 본문을 읽는 원천.
- `POST /sessions/{id}/assistant{prompt}`: 세션 60회 초과 429 `assistant_limit`. 요청·응답 메시지(channel=assistant)와 `assistant_calls`(도구 호출 내역 포함) 기록, 어시스턴트 실행(메일박스 밖, 토론 그래프와 독립). 어시스턴트는 Python 도구 호출 루프다([architecture/agents.md](../architecture/agents.md), R5-13).

## 비용 (`services/budget.py`, R5-11)

LLM 래퍼가 호출마다 낸 `usage.cost_usd`를 api가 두 컬럼에 누적한다. `sessions.debate_cost_usd`는 세션 중 토론 LLM 비용(하드 상한 판단용), `sessions.cost_usd`는 리포트까지 포함한 전체 관측값이다(목표 $0.5). 토큰 수는 `agent_runs.usage`에서 관측만 하고 세션 중 응답을 끊지 않는다. `budget.py`가 DB에서 `debate_cost_usd`를 읽어 `cost_left = SESSION_COST_HARD_USD(기본 $2) − debate_cost_usd`를 DebateState에 담는다. 0 이하면 그 세션의 페르소나는 LLM을 부르지 않고 자리별 고정 안전 문구만 게시한다. 의도 분류·어시스턴트는 그대로 둔다.

## 스케줄러 (5초)

```
deadline:  in_progress AND phase ∈ 토론 단계 AND now ≥ phase_deadline_at AND 칸 empty
           → timeout.fill(phase) (api 결정적, 그래프 자극 아님)
warning:   단계 마감 L/30 전 → 시스템 안내 1건(scenario time_warning 문구, 남은 시간 표기, LLM 없음, (session, phase, "warning") 멱등)
idle:      토론 단계에서 그 단계 `invite_delivered`가 false이고 침묵 초대 조건(scenario-model 3.1: 지원자 마지막 메시지 또는 단계 시작 후
           일정 시간, 또는 단계 기본 시간 절반이 지났는데 지원자 의견 없음)이 참 → stimulus=idle enqueue.
           같은 단계의 idle이 큐에 대기 중이면 넣지 않는다. 매 틱 조건을 다시 보므로 이전 idle이 초대 없이 끝났어도 다시 들어간다(R5-3)
card_due:  토론 단계에서 세션 카드의 예정 시각(phase_started_at + offset_min, 최소 20초) 도달
           AND 그 카드의 단계가 현재 단계 AND 담당 자리 전원이 delivered가 아님 → stimulus=card_due{card_id} (enqueue마다 새 uuid4 run_id).
           같은 card_id의 card_due가 큐에 있거나 실행 중이면 넣지 않는다. 같은 카드는 마지막 run 뒤 30초 간격·최대 3회까지만.
           멱등은 `session_cards.delivered_seats`로 판단
expiry:    in_progress AND now ≥ ends_at → submit("timeout")
runs:      agent_runs.status=started AND started_at < now-200s(역할별 타임아웃 합 + 여유) → 같은 run_id로 재실행
outbox:    completed AND report_enqueued_at null → report.generate 재발행 / approving AND approve_enqueued_at null → report.approve 재발행
purge:     ended_at + 90일 지난 세션 삭제(`services/purge.py` `purge_expired`, completed·failed·cancelled 모두, [architecture/data-model.md](../architecture/data-model.md) 보관 표, R5-21)
```

그래프 자극은 4종(`message`·`phase_started`·`idle`·`card_due`)이고 정본은 [architecture/session-lifecycle.md](../architecture/session-lifecycle.md) "그래프 자극"이다. `timeout`과 `room_quiet`는 없다(R5-2·R5-3). 카드 트리거는 시각 기반 `card_due` 하나뿐이다(이벤트 트리거 없음, R5-4). 침묵 초대 전달 여부는 단계별 `invite_delivered` 플래그로 추적한다(초대 메시지가 실제 게시될 때 api가 켬).

## 내부 API

[contracts/internal.md](../contracts/internal.md) 내부 REST 표 그대로. 구현 메모:
- `POST /messages`: (run_id, seq) 멱등. `is_invite=true`면 그 단계 `invite_delivered=true`(두 번째는 409). `card_ids`(배열)가 있으면 배열의 카드마다 `session_cards.delivered_seats`에 sender 자리를 더한다(R5-4, 같은 자리 중복 없음). 한 발언이 두 카드의 첫 발언을 겸하면 두 카드 id를 함께 보낸다. sender p1~p3·system만.
- `POST /options`·`/stances`: 현재 단계만. AI의 안·입장은 게시한 자기 메시지(`source_message_id`)에 묶인다. 시나리오 안은 `(session_id, phase, scenario_option_key)`로 지원자가 먼저 만든 행을 그대로 돌려준다.
- `POST /observations`: 카드 발동 기록. 첫 카드 발언이 게시된 뒤에만 들어온다(`trigger_message_id` = 그 메시지). 공동 전달 메시지면 카드마다 1건씩 남는다. (session_id, card_id) 멱등.
- `GET /contributions`: 안 행의 proposer가 최초 제안자(안은 처음 말한 순간 한 번만 만든다).
- `GET /proposal`: in_progress면 현재 초안(debate-mcp `get_draft`), completed 이후면 제출본(`submitted`, `submitted_at`·`auto_submitted` 포함). report-worker가 직접 부른다.
- 평가 입력 묶음 전용 엔드포인트는 없다. assessment-mcp `get_session_record`가 기존 내부 GET(`/sessions/{id}`, `/messages` 페이지 반복, `/slots`, `/proposal`, `/observations`, `/facts`, `/contributions`)을 모아 SessionRecord를 만든다([contracts/README.md](../contracts/README.md)). completed 전 호출은 assessment-mcp가 `GET /sessions/{id}`의 status를 보고 `conflict`로 거절한다.
- `GET /transcript_span`: 근거 메시지 앞뒤 맥락(기본 앞 5·뒤 3). 리포트 표시용.
- `POST /internal/sessions/{id}/evaluations`(`save_evaluation`): completed 이후만. `(session_id, run_id)`가 이미 있으면 저장하지 않고 기존 값(`existing=true`). **api는 형식과 세션 소속만 검증한다**(R5-14): 차원 19개 밖 `dimension_id`, 차원 중복, `level`·`no_record_reason` 동시 존재, 사유 코드 밖 값, 다른 세션 행을 가리키는 `evidence_refs`는 422 `invalid_input`. 근거 수·확신도 상한 같은 판단 규칙은 워커가 저장 전에 처리한다(상한 위반은 거부가 아니라 워커가 깎음). 한 트랜잭션에서 차원마다 `evaluations` 행, 실행 단위로 `evaluation_runs` 한 행(session_id, run_id, rubric_version, `prompt_versions` jsonb(평가 프롬프트 파일별 개별 해시 맵, 형식은 [contracts/internal.md](../contracts/internal.md) EvaluationSave), model, `elements` jsonb, created_at)을 저장한다. 요소 요약은 이 행에서 다시 읽을 수 있다.
- `GET /evaluations`: 저장된 차원 행과 `evaluation_runs`의 `elements`를 함께 돌려준다(워커 재실행 시 요소 요약 복구).
- `POST /report/final`: draft content를 그대로 받는다(해시 동일, R5-15). 담당자 조정은 저장 content에 섞지 않고 조회 때 병합한다.
- `GET /facts`: `services/facts.py`가 [product/rubric.md](../product/rubric.md) 6장 지표를 규칙으로 계산.
- `GET /assistant_calls`: 행마다 요청·응답 원문(`request`·`response`)을 함께 준다(워커 `ai_usage` 인용). `GET /sessions/{id}`의 `position`에 `name`(`positions.name`)을 준다(Report `position.name`).
- `GET /rubric`: `positions.scenario`의 척도 복사본(5요소 차원 19개·앵커·사유 코드·카드 창, `rubric_version`).
- `GET /messages?tail=N`: 토론방 메시지 최근 N개를 오래된 순으로(debate-mcp `get_transcript`). limit·offset은 무시한다.

## 테스트 API (`ENABLE_TEST_API=1`, `services/test_sessions.py`)

e2e·단계별 테스트가 15분을 처음부터 기다리지 않게 하는 두 엔드포인트다([contracts/internal.md](../contracts/internal.md) 내부 REST). 꺼져 있으면 404.
- `POST /internal/test/sessions {start_at?, stage?}`: 관리자·채용 건·초대를 만들고, `start_at`이 entry가 아니면 단계 시드 `scenarios/e2e/stages/<이름>.yaml`(e2e 대본과 같은 문법)의 지원자 행동과 고정 AI 발언을 **SessionService·debate·proposal 서비스 함수로 재생**해 그 단계가 막 시작된 상태를 만든다. 가짜 시계를 시드 안에서만 쓰고 LLM·그래프를 부르지 않는다. SQL을 직접 넣지 않으므로 generation·seq·칸·안·입장·누적 마감·`session_cards`가 실제 흐름과 같은 규칙으로 쌓인다. 재생이 끝나면 시작 시각을 "지금 − 경과 시간"으로 맞춰 이후는 실제 시계와 스케줄러가 평소대로 진행한다. `ended`는 제출까지 재생하고 `report.generate`를 평소대로 발행한다.
- `POST /internal/test/sessions/{id}/advance {seconds | to}`: `sessions`의 `started_at`·`phase_started_at`·`phase_deadline_at`·`ends_at`을 같은 초만큼 앞당긴다(스키마 변경 없음). 카드 예정 시각은 시작 시각 기준 계산이라 함께 당겨진다. 시간 초과·카드·침묵 초대·자동 제출은 다음 스케줄러 틱이 평소 경로로 처리하므로 테스트 전용 분기가 없다. 메시지 `created_at`은 바꾸지 않는다.

## 관리자

- `GET /admin/catalog`: 요소 5장(이름·뜻·"이렇게 봅니다", 읽기용 설명), 세션 길이 옵션. 선택 기능이 없으므로 `if_unselected`는 없다. 페르소나 유형·카드·차원 앵커는 내지 않는다.
- `POST /admin/positions`: 위 조립. session_minutes 60∣15(422 `invalid_input`, R3).
- `GET /admin/positions/{id}/candidates`: 상태 묶음·요소별 수준 칩(요소마다 최종 수준 1~5 또는 null, 담당자 조정 반영)·확신도 low 차원 수·출처(`auto`/`reviewer`)·시각·메시지 수·`purge_at`(= `ended_at` + 90일)·실패 안내. 합계·정렬 키·순위 없음.
- `POST /admin/positions/{id}/invites`: 신규 초대와 재응시(`previous_session_id`, 새 세션). 계약대로.
- `POST /admin/sessions/{id}/reissue`(R5-20): 같은 세션·같은 별칭을 유지하고 초대 토큰과 만료만 교체한다. 이전 토큰은 즉시 무효. 시작 전(`invited`)·만료 세션에서만, 그 외 409. 응답은 초대와 같은 `{session_id, invite_url, expires_at, notice_text}`. 재응시(새 세션)와 구분된다.
- `GET /admin/sessions/{id}/report`: 저장된 content에 `reviewer_name`·`review`·`notes`·`connection_gaps`·`purge_at`·`proposal`과 담당자 조정(`rubric_adjustments`)을 병합. content는 불변이고 final은 draft와 같은 content다(R5-15). 응답 구조(정본 [contracts/api.md](../contracts/api.md) Report):
  - `elements[]`: `element`, `level`(1~5∣null, 자동), `summary`, `dimension_count`, `no_record_count`, `adjusted_level?`(조정된 차원으로 다시 계산한 평균 반올림)
  - `dimensions[]`: `dimension_id`, `element`, `level`∣null, `no_record_reason?`, `rationale`, `confidence`(high∣medium∣low∣null), `adjusted_level?`, `adjusted_no_record_reason?`, `adjust_memo?`, `evidence[]`(참조 문자열, 종류, 원문 또는 기획안 필드 발췌, 오피스 시각, 단계, `record_ref`, 메시지면 앞 5·뒤 3 맥락, 맥락용 여부)
  - `facts`([product/rubric.md](../product/rubric.md) 6장), `contribution_check`(안별 실제 최초 제안자 / 기획안에 적힌 기여자), `seats[]`, `slots`, `cards[]`, `ai_usage`, `debrief{cards, survey}`(카드 발동·관찰 창은 워커, 설문은 api가 GET 때 병합)
  - **총점·합계 필드 없음.** 요소 수준끼리 더하거나 평균하지 않는다.
- `POST /admin/sessions/{id}/review {reviewer_name, adjustments: [{dimension_id, level: 1~5∣null, no_record_reason?, memo}]}`(R5-16): **차원 단위** 조정. 검증 오류는 모두 **422**(`invalid_input` 계열, 코드 이름은 계약 오류 코드 체계): `reviewer_name` 비어 있음, `memo` 비어 있음, `level=null`인데 `no_record_reason` 없음, 차원 목록 밖·중복 `dimension_id`, level 범위 밖. `rubric_adjustments`에 `(session_id, dimension_id, level, no_record_reason, memo, adjusted_by, adjusted_at)` 저장, 요소 `adjusted_level`은 조정 반영 차원 평균 반올림으로 다시 계산(자동 서술은 그대로). 조정 없이 승인만도 가능. 이미 approving·reviewed면 같은 본문 200, 다르면 409 `already_reviewed`(422보다 우선). 검토 완료 뒤 값 재조정 금지(메모 추가만). approving 커밋 → `report.approve{session_id, eval_round}` 발행(본문 정본 [contracts/queue.md](../contracts/queue.md) 큐 메시지).
- `GET /admin/sessions/{id}/record`: `message`·`slot`·`phase`·`assistant_call`·`material_view`·`proposal_submit`·`connection_gap` 타임라인. `ref`가 리포트 `record_ref`와 같은 값.
- 취소·regenerate·notes는 계약대로.

## 디브리핑

`GET /sessions/{id}/debrief`: 첫 문장은 고정 문구 "같은 채용 건의 모든 지원자가 같은 장면을 겪었고, 정답 없이 반응을 봅니다"(R1). 이어서 실제 발동한 카드(`observations`)마다 시나리오 디브리핑 문장, 평가 요소 이름과 "이렇게 봅니다" 한 줄(척도 세부·차원 앵커는 공개하지 않음), 지원자 본인 발언 원문(토론 단계마다 가장 긴 지원자 메시지 1개, 최대 3개, 결정적 선택, 읽기 전용 회고, R7), 자리별 `display_name`·`avatar_url`·성향 설명(지원자는 `display_name`만), 평가 결과 비공개 안내, 이의 제기 경로(문의 창구와 절차 한 줄, R1·R7), 끊김 구간, 보관 안내, 제출 기획안, 설문 상태(2문항 + 자유 의견 한 칸, R7). 세션 실패면 중단 안내만. 기한 = `ended_at` + 7일.

## 검증 항목

- 기반: compose에서 api healthy, 스키마(options·stances·slots(`empty|confirmed`)·message_intents(`confidence`)·proposals·observations·evaluations·`evaluation_runs`·차원 단위 rubric_adjustments 새 형태, `sessions.display_name`·`generation`·`ended_at`, 버전별 자료 원문), SessionService 단위 테스트(전이 전부·멱등, 제출 즉시 completed·자동 제출 `auto_submitted`·시작 후 취소·실패와 취소의 `ended_at`·regenerate·reissue 토큰 교체).
- 조립: 자리 3개가 강한결·윤하람·서지안 고정, 카드 6장·차원 19개, `offset_min` 환산 최소 20초, `avatar` 파일 이름에 유형 이름이 없음, 자료 원문이 `catalog_version`별로 시드. 지원자 응답(`/state`, entry, consent, start)에 `persona_type`이 없다.
- 이름·입장: 입장·consent·resume 응답에 `session_id`, 입장 응답에 초대 이름, 시작 전 `/state`에 참여자 이름·아바타, consent의 `display_name`이 `/state.participants`·디브리핑에 쓰이고 비우면 별칭, `/assets/...` 이미지가 200.
- 메일박스: 가짜 시계·가짜 그래프로 자극이 직렬 처리됨, 메시지 저장 시 generation +1이고 그 자극의 generation이 저장 값, `message.ack`이 의도 분류 전에 나감, `agent_runs`가 `queued`로 저장된 뒤 enqueue, 단계 전환 뒤 꺼낸 이전 단계 메시지가 그 메시지 단계의 카탈로그로 분류·저장되고 턴·발언만 skipped, 게시 직전 generation이 바뀌면 남은 화자 버림, `phase_started`는 enqueue만, 같은 run_id의 finished run은 재실행 안 됨, 낡은 message 자극도 의도 분류·안·지지 반영은 되고 그래프만 skipped, 200초 넘은 started run 재실행.
- 단계·칸: 지원자 확정 → `slot.confirmed`·`phase.changed`·`timer` 순서와 keyword 확정 시 `materials.opened`, 마감 누적 이월, keyword 후보 밖 422, problem·solution 확정의 문자열·카탈로그 키 연결과 불일치 시 `option_id=null`(LLM 호출 0), AI fill의 합의 전 409 `no_consensus`(칸·공지 0건), AI fill 성공 시 칸 확정과 공지(seq 5, `consensus_fill`)가 같은 트랜잭션이고 WS가 `message.new` → `slot.confirmed` → `phase.changed` → `timer`, 같은 run_id 재호출은 기존 칸·공지, 409 검사 순서 `phase_not_open` → `slot_confirmed` → `no_consensus`, 동시 확정 시 하나만 성공, 마감 시 api 결정적 채우기(우선순위 자리 이름·R7 문구·LLM 0), proposal 진입 시 기획안 앞 3칸 채우기.
- 합의 판정 단위 테스트: 3/4 즉시 성립(대기 없음), confidence low인 지원자 agree 미집계, 다른 안 support가 이전 지지를 대체, 같은 안 후속 oppose가 지지 철회, 카탈로그 안을 지원자가 먼저 말하면 같은 option_id·proposer=candidate이고 이후 AI propose도 같은 행.
- 메시지·WS: ack·증분·재연결 후 `/state`, `origin`(초대 뒤 첫 발언만 invited), 의도 분류 → 안·입장 반영, 분류 실패 시 other, 모든 프레임에 연속 `seq`, typing 6초 만료.
- 시간(가짜 시계 단위 테스트 + 15분 실 스택 1~2개, R3): 단계 마감 → `phase.changed` 15초 안, 마감 임박 안내 1건, 침묵 초대 조건에서 `invite_delivered=false`인 동안 idle 재enqueue(대기 중이면 중복 없음), 카드 예정 시각에 card_due, 담당 전원 delivered 전까지 새 run_id로 재enqueue(큐·실행 중이면 중복 없음, 카드당 최대 3회·30초 간격), `ends_at` 자동 제출과 큐 발행.
- 자료·어시스턴트: 키워드 확정 전 목록 잠김·본문 423, 확정 뒤 그 키워드 자료만(채용 건 버전 원문), 열람 기록, 어시스턴트 60회 초과 429.
- 비용: `debate_cost_usd` 하드 상한 $2 초과 주입 시 페르소나가 고정 안전 문구만 게시, 세션 진행은 계속. 리포트 비용은 `cost_usd`에만 더해지고 `cost_left`에 영향 없음.
- 테스트 API: `start_at`마다(keyword·problem·solution·proposal·ended) 만든 세션의 `/state`가 그 단계 시작 상태이고 앞 단계 칸이 시드대로 confirmed, generation·seq가 연속, `advance{to: deadline}` 뒤 다음 틱에 시간 초과 채우기, `advance{to: "card:<id>"}` 뒤 card_due enqueue, `ENABLE_TEST_API` 없으면 404.
- 관리자·내부: 엔드포인트가 계약 예시대로, 기업 간 격리, review `reviewer_name`·차원 단위 조정·메모 필수·검증 오류 422·409 우선, 리포트 응답에 총점·합계 필드 없음, final content 해시 = draft, record의 `ref`와 리포트 `record_ref` 일치, evaluations `(session_id, run_id)` 멱등·형식 422·`evaluation_runs` 1행, observations 멱등, `card_ids` 두 개 메시지가 두 카드의 `delivered_seats`를 함께 갱신, reissue 후 옛 토큰 무효, 재시작 후 미처리 마감·제출·미발행 재처리, purge가 `ended_at` 기준으로 failed·cancelled도 지움.
