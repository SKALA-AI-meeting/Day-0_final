# MCP 도구

서버별 구현(전달·색인·인증)은 [MCP 서비스](../services/mcp.md), 페르소나의 assessment 차단 근거는 [보안·권한 경계](../architecture/security.md).

세 서버 모두 Python FastMCP, Streamable HTTP. 도구 함수는 모두 `async def`다(FastMCP는 동기 도구를 워커 스레드에서 실행한다). 클라이언트(api 그래프·report-worker)는 env로 접속한다: `DEBATE_MCP_URL`(기본 `http://debate-mcp:9101/mcp`), `RAG_MCP_URL`(기본 `http://rag-mcp:9102/mcp`), `ASSESSMENT_MCP_URL`(기본 `http://assessment-mcp:9103/mcp`, 토큰 `ASSESSMENT_MCP_TOKEN`). MCP 서버는 `API_INTERNAL_URL`(기본 `http://api:8001`)과 `INTERNAL_TOKEN`으로 내부 API를 부른다. 각 도구는 Pydantic 입력 모델과 한 문단 설명·인자 설명을 가진다. session_id는 그래프가 주입한다(모델이 바꿀 수 없음).

### 공통 응답 형식

```json
{"ok": true, "data": {...}}
{"ok": false, "error": {"code": "not_found", "message": "session not found", "retryable": false}}
```

코드: `invalid_input`, `not_found`, `forbidden`, `conflict`, `not_released`, `upstream_unavailable`(retryable), `internal`. api 매핑: 404 not_found, 403 forbidden, 409 conflict, 422 invalid_input, 423 not_released, 5xx·연결 실패 upstream_unavailable(1회 재시도, 타임아웃 10초). **409의 api 오류 코드(`no_consensus`·`stale_generation`·`slot_confirmed`·`phase_not_open`)는 MCP `message`에 그대로 담는다**(예: `{"code": "conflict", "message": "no_consensus"}`). 결정적 노드는 이 message로 분기한다.

### debate-mcp (`http://debate-mcp:9101/mcp`)

| 도구 | 입력 | 출력 data | 내부 API | 구분 |
|---|---|---|---|---|
| get_debate_state | {session_id} | DebateState + {seats: [{seat: "candidate", display_name}, {seat, display_name, avatar_url}]} (페르소나 유형 없음). 결정적 노드(load) 전용이고 페르소나에는 바인딩하지 않는다(페르소나는 load가 걸러 주입한 DebateState만 본다) | GET /debate_state, GET /sessions/{id} | 조회 |
| get_transcript | {session_id, since_t_min?, phase?, limit?=30} | [{id, sender, content, phase, is_invite, t_min, reply_to}] (`reply_to`는 AI 발언이 답한 메시지 id, `meta.reply_to`에서 꺼냄, 없으면 null. 그 밖의 meta·intent·origin 없음) | GET /messages | 조회 |
| get_draft | {session_id} | {sections, revision, updated_at} | GET /proposal | 조회 |
| post_message | {session_id, sender: p1∣p2∣p3∣system, content, run_id, seq, expected_generation?, is_invite?, card_ids?, cited_material_ids?, meta?} | Message. 409 `stale_generation`이면 게시 안 됨 | POST /messages | 변경 |
| propose_option | {session_id, phase, label, proposer: p1∣p2∣p3, scenario_option_key?, source_message_id, run_id} | Option(`existing` 포함) | POST /options | 변경 |
| set_stance | {session_id, participant: p1∣p2∣p3, option_id, stance: support∣oppose, source_message_id, run_id} | {stance_id} | POST /stances | 변경 |
| fill_slot | {session_id, option_id, seat: p1∣p2∣p3, content, run_id} | {slot, message, phase}. 합의 채우기 전용. 칸 확정·공지 메시지 저장(seq 5)·단계 전환을 api가 한 트랜잭션으로 한다. `resolution`은 api가 consensus로 정하고 인자로 받지 않는다. 같은 run_id 재호출은 기존 결과 | POST /slots/fill | 변경 |

- 지원자의 안·입장은 api 메일박스가 의도 분류로 직접 저장하므로 MCP 도구의 proposer·participant에 `candidate`는 없다.
- 그래프의 페르소나 게시는 항상 `expected_generation`(실행 시작 때 값)을 넣는다. 합의 채우기 공지는 `post_message`로 따로 게시하지 않는다. `fill` 노드의 `announce_fill` 분기가 공지 문장을 `fill_slot`의 `content`로 넘기고, api가 칸 확정과 같은 트랜잭션에서 저장한다. `fill_slot`이 409(`no_consensus`·`slot_confirmed`·`phase_not_open`)면 칸도 공지도 없이 run을 끝낸다.

### rag-mcp (`http://rag-mcp:9102/mcp`)

| 도구 | 입력 | 출력 data | 구분 |
|---|---|---|---|
| search_materials | {session_id, query, top_k?=3} | [{material_id, title, chunk, score}]. 내부 GET /materials로 잠금·키워드·catalog_version 확인 후 열린 자료의 같은 버전 청크만. 잠겨 있으면 `not_released` | 조회 |
| get_material | {session_id, material_id} | {material_id, title, content}. 내부 GET /materials/{material_id}(같은 catalog_version 본문). 열린 자료가 아니면 `not_released` | 조회 |
| reindex_materials | {mission_id?} | {indexed, model, dim, catalog_version, index_version}. 현재 카탈로그 자료를 줄·문장 경계 청크(상한 400자, 앞 청크 끝 단위 겹침)로 `material_chunks`에 색인(관리용, 이전 키 행은 지우지 않음). 저장 키 `index_version` = catalog_version + 청크 규칙 버전(`-c<n>`)이고 search_materials도 같은 키로 찾는다. 청크 규칙이 바뀌면 재색인 전까지 검색이 `upstream_unavailable`(reindex). 원문 `materials` 행은 `scripts/seed.py`가 같은 버전으로 시드. 에이전트에 노출하지 않음 | 변경 |

### assessment-mcp (`http://assessment-mcp:9103/mcp`, X-Assessment-Token 필수)

| 도구 | 입력 | 출력 data | 내부 API | 구분 |
|---|---|---|---|---|
| record_observation | {session_id, card_id, phase, trigger_message_id?, run_id} | {observation_id, fired_at, t_min}. 카드 첫 발언 게시 **뒤에** 한 번 | POST /observations | 변경 |
| get_rubric | {session_id} | {rubric_version, elements(차원·수준 앵커), no_record_reasons, cards} | GET /rubric | 조회 |
| get_session_record | {session_id} | SessionRecord(아래). 평가 입력 묶음 | GET /sessions/{id}, /messages(전체, 페이지 반복), /slots, /proposal, /observations, /facts, /contributions | 조회 |
| get_transcript_span | {session_id, message_id, before?=5, after?=3} | {target, before, after} (intent·origin 포함). 리포트 근거의 앞뒤 맥락 표시용 | GET /transcript_span | 조회 |
| get_contributions | {session_id} | Contributions | GET /contributions | 조회 |
| save_evaluation | EvaluationSave(아래) | {run_id, existing, saved} | POST /evaluations | 변경 |

토큰이 없거나 다르면 `forbidden`.

### 도구 허용 목록

| 주체 | 허용 도구 | 호출 방식 |
|---|---|---|
| 진행 매니저 결정적 노드(load·cards·apply·fill) | debate-mcp 전부, assessment-mcp `record_observation` | 코드가 직접 호출 |
| 진행 매니저 turn(LLM) | 없음 | 주입된 DebateState만 |
| 페르소나 respond | `get_transcript`, `get_draft`(proposal 단계에서만 바인딩), `search_materials`, `get_material` | 모델 도구 호출, 응답당 3회. `get_debate_state`는 바인딩하지 않는다(DebateState는 load가 `card_progress`·`cost_left`를 걸러 컨텍스트에 주입). 최근 대화도 미리 들어가므로 `get_transcript`는 보통 부르지 않는다 |
| 어시스턴트 | `search_materials`, `get_material` | 모델 tool-calling 루프, 요청당 최대 3회, 답에는 실제로 쓴 자료 id만(코드가 제목·숫자·구간 겹침으로 판정) |
| report-worker | `get_rubric`, `get_session_record`, `get_transcript_span`, `get_contributions`, `save_evaluation` | 코드가 직접 호출(평가 에이전트 LLM에는 도구를 바인딩하지 않고 입력 묶음을 주입). 평가 결과 재조회(`GET /evaluations`)·리포트 저장은 내부 REST |

페르소나 respond의 출력은 구조화 `{content, proposes?: {scenario_option_key∣label}, stance?: {option_id, stance}}`이고, 결정적 `apply`가 guard 통과 뒤 `post_message` → `propose_option`/`set_stance` 순으로 반영한다(source_message_id = 방금 게시한 메시지). 카드 발언이면 `post_message`에 `card_ids`를 넣고(전달 기록, 공동 전달이면 여러 개), 그중 첫 발언인 카드마다 이어서 `record_observation`. 자리의 고정 안과 입장 변경 허용 범위는 시나리오 규칙(`stance_plan`)으로 노드 `stance_check`가 **게시 전에** 검사한다: 계획 밖 입장(자기 계획 안에 `oppose`하거나 문장으로 반대하는 것 포함, 또는 문장이 계획과 다른 입장을 말함)이면 응답 전체를 1회 재생성하고, 그래도 어긋나면 계획과 맞는 자리별 고정 문구(프롬프트 `stance_fallback`)로 바꿔 게시한다. 결과는 `messages.meta.stance_check`(`pass`∣`regenerated`∣`fallback`)에 남긴다. 게시 문장과 기록 입장은 항상 일치한다(문장만 게시하고 입장을 버리는 경우는 없다).
