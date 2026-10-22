# 하네스 8요소

음성 토론의 현재 구현은 [음성 구현](voice.md)을 따른다.

## 음성 하네스 경계

Instructions·Tools·Context의 기존 페르소나/모델/MCP 경계를 유지한다. State·Memory에는 voice_states·speech_jobs·speech_quotas를 추가한다. Agent Loop는 녹음·전사·준비 예약 중 메일박스 실행을 보류하고 실제 재생 완료 장벽으로 발언권을 연다. typing은 음성 보호 수단이 아니다. Guardrails는 최종 저장 본문만 TTS에 전달하고 확정 전사·phase·generation·epoch를 검증한다. Recovery는 캐시 재생·제한된 같은 파일 재전사·DB 복구를 사용한다. Observability는 facts.voice_usage와 작업별 시도·단위·청구 상태를 기록한다.

기존 토론 LLM 비용 상한은 유지한다. 음성은 Azure F0 확인과 별도 사전 quota 차감으로 유료 호출을 차단한다. 무료 순비용도 사용량은 차감하며 불확실 호출은 예약을 반환하지 않는다. 역할 LLM 5개·기본 모델·평가 척도는 변경하지 않는다.


구성은 [overview.md](overview.md), 노드·규칙은 [architecture/agents.md](agents.md), 도구 경계는 [contracts/mcp.md](../contracts/mcp.md) 도구 허용 목록이 정본이다.

| 요소 | 구현 위치 | 수치·규칙 | 확인 방법 |
|---|---|---|---|
| Instructions | `scenarios/missions/debate/prompts/`: `common_rules.md`(동등한 팀원, 짧게, 시간은 단계 남은 시간으로 말함(오피스 시각을 말하지 않음), 기능 설명만 하고 평가 대상 행동을 유도하지 않음, 평가 질문 안내문), `persona_<type>.md`×3(고집형·소극형·비판형 고정, 말투·행동 규칙·must_not. 동조형·발산형은 MVP 이후 확장), `stance_fallback`(자리·입장 계획별 고정 문구), `turn_manager.md`, `intent.md`, `assistant.md`, `evaluator/common.md`(평가 에이전트 공통: 판단 순서·근거 규칙·편향 방지·출력 형식·합격·순위·총점 표현 금지, [product/rubric.md](../product/rubric.md) 2장) + `evaluator/<element>.md`(요소별 특수 지침), `guard_patterns.yaml`, `safe_<type>.md`(안전 문구, 비용 상한 뒤 고정 문구 겸용), 시간 초과·합의 채우기 고정 문장. 카드 brief·선점 금지([product/scenario.md](../product/scenario.md) 4.2) | 프롬프트·패턴은 catalog_version 해시에 포함되고 본문이 `positions.scenario`에 복사돼 채용 건에 고정. 실행은 복사본만 읽는다(평가 프롬프트·금지 패턴도 채용 건 생성 때 복사된다. 복사본이 없으면 현재 파일로 채우지 않고 시작·평가를 실패시킨다) | 프롬프트 리뷰, 유출·선점·판정 유도 픽스처, 평가 프롬프트 버전 맵(`prompt_versions` = `{common: evaluator/common.md 해시, <element>: evaluator/<element>.md 해시}`)이 `evaluation_runs`에 저장되는지 |
| Tools | MCP 클라이언트 3개(`langchain-mcp-adapters`), 서버 도구 함수는 모두 `async def`(FastMCP는 동기 도구를 워커 스레드에서 실행), 노드별 허용 목록(contracts). 페르소나 respond = debate 조회 2개(`get_transcript`·`get_draft`, `get_draft`는 proposal만. `get_debate_state`는 바인딩하지 않음) + rag 2개, 어시스턴트 = rag 2개(tool-calling 루프, 요청당 최대 3회, 인용 포함), 결정적 노드 = debate 전부(`fill_slot`은 합의 채우기 전용이고 공지 문장을 `content`로 함께 넘겨 칸 확정과 한 트랜잭션으로 저장) + assessment `record_observation`, 워커 = assessment 5개(`get_rubric`·`get_session_record`·`get_transcript_span`·`get_contributions`·`save_evaluation`, 코드가 직접 호출, 평가 에이전트 LLM에는 도구 바인딩 없음). session_id는 그래프가 주입 | 응답당 도구 3회, 검색 top 3, 대화 조회 30개, 도구 결과 2k자 절단. **페르소나는 assessment 접근 불가**(허용 목록 + 클라이언트·토큰 분리의 이중 차단) | 허용 밖 도구 호출 거부 테스트, 페르소나 MCP 설정에 assessment URL·토큰 없음 |
| Context | `load`가 만든 자리별 컨텍스트(걸러 낸 DebateState를 미리 주입해 조회 도구 왕복을 줄임): 자기 성향 프롬프트, 이번 목적·대상 안, 자기 `stance_plan`, 자기 카드 brief, 자리 기억(최근 대화 중 자기 발언 3개), 최근 대화 30개, 지원자 표시 이름(호칭 "OO님")·다른 자리 이름, 단계 남은 시간, 확정 칸 값, 열린 자료 청크. 턴 매니저 LLM = DebateState 요약 + 최근 10개 + 자리별 성향 한 줄. 평가 에이전트 = 그 요소의 척도(차원·앵커) + `get_session_record` 전체(세션 대화 전체·칸 기록·기획안 제출본·카드 발생·사실 지표·Contributions), **다른 요소의 평가 결과는 넣지 않음**. **어느 페르소나에도 넣지 않는 것**: 평가 척도·평가 결과·관찰·카드 이름·요소 이름·`card_progress`·`cost_left`, 다른 자리의 성향 이름, 아직 오지 않은 카드, 어시스턴트 대화, 열리지 않은 자료, 지원자 기획안 초안(요청받아 `get_draft`를 부른 경우 제외) | 입력 범위는 최근 대화 30개와 자기 발언 3개로 정한다(토큰 한도 검사·요약 접기는 없다). 평가 에이전트는 대화를 접거나 창으로 자르지 않는다(세션 전체, 요소당 1호출) | 컨텍스트 스냅샷 검사, 유출 질문 픽스처 |
| State·Memory | 토론 그래프는 체크포인터 없이 실행한다. 토론 상태(안·입장·칸·`session_cards`·`generation`)는 DB가 원천이고 매 실행 `load`가 다시 읽는다. 자리 기억은 최근 대화 중 자기 발언 3개(밀었던 안·설득 이동·압박 횟수는 DB의 입장 이력과 메시지에서 계산). 어시스턴트 대화 이력은 세션별 프로세스 메모리. LangGraph PostgresSaver는 report-worker만: thread `report:{id}:{eval_round}`(새 평가 회차는 새 thread라 이전 interrupt 체크포인트를 재사용하지 않고, 같은 회차 큐 재시도만 체크포인트에서 재개) | 자극 큐는 메모리(`mailbox.py`)이고 영속 사본은 `agent_runs`(queued·started). 재시작 복구는 DB 기록으로 다시 실행하는 방식이다(그래프 중간 재개 없음) | 재시작 복구 단위 테스트, P1 restart 대본 |
| Agent Loop | 세션 메일박스(`mailbox.py`, asyncio Task 1개가 자극 4종 `message`·`phase_started`·`idle`·`card_due`를 직렬 소비, 락 없음). 턴 매니저: 자극당 화자 0~2명, 둘째 화자 1~3초 간격, 턴 LLM은 규칙 결과 0명인 `message` 자극만. `wait_typing`: 지원자 입력 중(5초 갱신, 6초 만료)이면 최대 10초 대기, 그래도 입력 중이면 미게시·재큐(`deferred`). 게시 직전 `generation`이 바뀌었으면 남은 화자 버림(409 `stale_generation`으로 이중 확인). recursion_limit 20. 침묵 초대 단계당 1회(실제 게시 기준, 미전달이면 스케줄러가 idle 재큐). 압박 발언 최대 3회. 설득 이동 단계당 자리별 1회. 어시스턴트 60회/세션. 비용(`services/budget.py`): 목표 $0.5/세션은 `sessions.cost_usd`로 관측, 토큰은 관측만, 하드 상한 $2는 토론 비용만(`cost_left = SESSION_COST_HARD_USD − sessions.debate_cost_usd ≤ 0`, 리포트 비용 제외) | 상한 뒤 페르소나는 LLM 없이 고정 안전 문구만, 합의 채우기 공지(`fill_slot` 트랜잭션, LLM 없음)·시간 초과·카드 진행은 계속. 한도는 세션 단위 | 가짜 시계·가짜 hub 단위 테스트, `sessions.debate_cost_usd` |
| Guardrails | `guard` 노드(코드에서 무조건 실행): 점수·합격·순위·평가·연출 언급, 지원자 판정 표현, 결론 대리 선언, 성향 이름, 미래 카드 암시 → 재생성 1회 → 자리별 안전 문구. 400자 상한. 입력 마스킹(전화번호·이메일). `stance_check` 노드: 응답 입장이 `stance_plan` 밖(자기 계획 안에 반대하는 것 포함)이거나 문장이 계획과 다른 입장을 말하면 전체 재생성 1회 → `stance_fallback` 고정 문구(게시 문장과 기록 입장 항상 일치, `messages.meta.stance_check`). api 결정적 검사: **AI는 합의 전에 칸을 못 채움**(409 `no_consensus`), 시간 초과 채우기는 api `services/timeout.py`만(그래프 경로 없음), 침묵 초대 단계당 1회(409), 낡은 게시 차단(409 `stale_generation`). 지원자 확정의 안 연결은 옵션 키·정규화 라벨 일치만(LLM 없음). 행동 유도 금지(F9). 평가 검증 경계([contracts/internal.md](../contracts/internal.md) EvaluationSave): api는 형식·세션 소속, 워커는 **근거 없는 수준 금지**(근거 최소 개수)·금지어(합격·불합격·순위·추천·점수)·요소 level 계산, 확신도 상한은 clamp. 사람 승인: 리포트 그래프 interrupt → 검토 완료(차원 단위 조정, 메모·`reviewer_name` 필수)가 같은 thread 재개 | 패턴 목록은 카탈로그 데이터. 승인 전 final 저장은 api가 409로 차단. 토큰 스트리밍 없음(검사 뒤 게시) | guard·stance_check 단위 테스트, 선점·판정·결론 유도 픽스처, 합의 전 fill 409 테스트, 평가 검증 단위 테스트, 승인 e2e |
| Recovery | LLM 래퍼: 역할별 타임아웃·재시도(아래 "역할별 모델" 표), 재시도 간격 1·3초 | 페르소나 발언 실패 → 그 발언 생략, `llm_failures+1`(연속 3회 → 세션 failed, 성공 시 0). turn LLM 실패 → 규칙 결과만. 의도 분류 실패 → `other`(fallback=true). 카드 화자 미전달 → 다음 run이 이어서 말함(`session_cards.delivered_seats`, 전원 전달 전까지 `card_due`를 새 run_id로 재큐, 카드당 최대 3회·30초 간격). MCP upstream 1회 재시도. 평가: 형식 오류·근거 부재 → 그 요소 1회 재요청 후 실패 차원 `evaluate_failed`, LLM 전송·타임아웃 → 저장 없이 큐 재시도(같은 `(session_id, run_id)` 저장분은 재사용, `evaluate_failed`가 있으면 재생성 때 새 run_id). 워커 실패 → 큐 재시도 3회·DLQ·`report_failed`. 재시작 → 미처리 마감(api 직접 채우기)·자동 제출·`agent_runs` queued·started 재큐(실행 중 200초 초과도)·발행 재처리(멱등 키). WS seq 빠짐 → 클라이언트 `/state` 재조회 | 강제 실패 주입 테스트(큐 DLQ·LLM timeout), 재시작 복구 단위 테스트 |
| Observability | LangSmith trace(LLM 호출 metadata `agent_run_id`·tags. 그래프 config에 `run_id` 키를 쓰지 않음), `agent_runs.decision`(턴 매니저 결정 스키마), `agent_runs.usage`(llm_calls, retries, tokens_in/out, latency_ms, tool_calls, cost_usd), `sessions.debate_cost_usd`·`sessions.cost_usd`, `message_intents`(latency_ms, fallback, model), `messages.meta`(토큰·지연·모델·비용·guard 결과·`stance_check`), `reports.usage`, `evaluation_runs`의 `rubric_version`·`prompt_versions`·`model`·`run_id`, `evaluations.confidence`, `assistant_calls.tool_calls`, 구조화 로그에 run_id | 세션 비용 = `sessions.cost_usd`(agent_runs.usage + reports.usage의 `cost_usd`). 응답 지연 = 지원자 message.ack 시각과 첫 페르소나 message.new 시각 차 | trace 링크, e2e summary의 비용·지연·턴 결정 표 |

실패 유형별 폴백 형태: 페르소나 발언 → 생략. guard 2회 실패 → 자리별 안전 문구. 입장 계획 위반 2회 → `stance_fallback`. 비용 상한 → 고정 안전 문구. 턴 선택 → 규칙만. 의도 분류 → other. 단계 마감 → 그래프 없이 api 직접 채우기(우선순위 자리 이름, 고정 문장). 평가 차원 → `evaluate_failed`(기록 없음 사유, 화면 "판단 보류 · 근거 부족 — 원문 기록 확인 권장"). 실시간 의도 분류 실패는 기존대로 `other`. 어시스턴트 → "잠시 후 다시 시도해 주세요".

## 역할별 모델

역할 5개는 env `MODEL_INTENT`·`MODEL_TURN`·`MODEL_PERSONA`·`MODEL_ASSISTANT`·`MODEL_EVALUATOR`(평가 에이전트)를 읽고 비어 있으면 `OPENAI_MODEL`(기본 gpt-4o-mini). 호출 한 번의 타임아웃·재시도·temperature는 역할 표(`services/api/app/agent/llm/roles.py` `ROLE_CONFIG`, 워커는 `report_worker/config.py`의 같은 값) 한 곳이 정본이다.

| 역할 | 타임아웃 | 재시도 | temperature | 실패 시 |
|---|---|---|---|---|
| 의도 분류 `intent` | 10초 | 0 | 0.0 | `other`(fallback) |
| 턴 매니저 `turn` | 15초 | 2 | 0.0 | 규칙 결과만(0명) |
| 페르소나 `persona` | 20초 | 2 | 0.7 | 그 발언 생략, `llm_failures+1` |
| 어시스턴트 `assistant` | 30초 | 2 | 0.3 | "잠시 후 다시 시도해 주세요" |
| 평가 에이전트 `evaluator` | 60초 | 2 | 0.1 | 저장 없이 예외 → 큐 재시도(attempt+1) |
 키는 모델 id로 고른다(gpt-4o-mini → 제한 키, 그 외 gpt-* → 개인 키, gemini-* → Gemini 키). 규칙은 공용 LLM 래퍼 한 곳. 프롬프트·규칙 수정으로 안 되는 역할만 사용자 결정으로 올린다(`docs/decisions.md`).

## 실측 기준선

| 항목 | 목표 | 최근 측정 |
|---|---|---|
| 의도 분류 정확도(20문장) | 0.85 | 0.90 (2026-10-22, gpt-4o-mini, `make eval-live`. 대상 안 0.85, 확신도 0.80, 호명 대상 1.0) |

2026-10-21 최소 경로(메시지 → 의도 분류 → 규칙 턴 → 페르소나 1명 발언 → 게시)를 gpt-4o-mini로 잰 값(문서·보고서용 기준선, 목표가 아님): p50/p95 — 의도 분류 1.0/1.2초, 페르소나 발언 1.2/1.4초, 턴 LLM 1.2/1.5초. 60분 세션 비용 추정 $0.08~0.11. 의도 분류 정확도 목표 0.85(`tests/eval/` 20문장). e2e summary의 지연·비용은 이 값과 나란히 적는다.

