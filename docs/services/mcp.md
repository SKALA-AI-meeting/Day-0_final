# MCP 서버 (debate·rag·assessment)

세 서버 모두 Python FastMCP, Streamable HTTP. 도구 목록·입출력·오류 형식·허용 목록은 [contracts/mcp.md](../contracts/mcp.md) "MCP 도구"가 정본이다. MCP는 **debate-mcp(손)·rag-mcp(눈)·assessment-mcp(기록자)** 3개다(D11). scenario-mcp·웹 검색 MCP·가드 MCP는 두지 않는다(카탈로그는 debate-mcp로 충분, 웹 검색은 공정성·재현성 훼손, 출력 검사는 코드에서 무조건 실행).

## 공통

- DB 접근 없음(rag-mcp의 `material_chunks` 제외). api 내부 앱(`http://api:8001/internal/*`)을 httpx로 부르고 헤더 `X-Internal-Token`을 붙인다. 권한·상태 판단(단계, 합의, 마감, 자료 잠금)은 api가 하고 MCP는 전달만 한다.
- 각 도구는 `async def`로 정의한다(FastMCP 4.1.1은 sync 도구를 워커 스레드에서 실행하므로 httpx 비동기 클라이언트와 섞지 않는다). Pydantic 입력 모델, 한 문단 설명과 인자 설명, `{ok, data}` / `{ok: false, error: {code, message, retryable}}` 반환.
- 오류 매핑: 404 `not_found`, 403 `forbidden`, 409 `conflict`, 422 `invalid_input`, 423 `not_released`, 5xx·연결 실패 `upstream_unavailable`(retryable, 1회 재시도), 그 밖 `internal`. 타임아웃 10초. **409의 api 오류 코드(`no_consensus`·`slot_confirmed`·`phase_not_open`)는 `message`에 그대로** 담는다. 결정적 노드는 이 값으로 분기한다.
- 멱등 키(run_id·seq·source_message_id 등)는 그대로 전달하고 판단은 api에 맡긴다.
- `session_id`는 그래프가 주입한다(모델이 바꿀 수 없음).
- 각 서버 `/health`, 컨테이너 하나씩.
- 접속 env(클라이언트 쪽, 정본 [contracts/mcp.md](../contracts/mcp.md) "MCP 도구"): `DEBATE_MCP_URL`(`http://debate-mcp:9101/mcp`), `RAG_MCP_URL`(`http://rag-mcp:9102/mcp`), `ASSESSMENT_MCP_URL`(`http://assessment-mcp:9103/mcp`). 서버 쪽은 `API_INTERNAL_URL`·`INTERNAL_TOKEN`(assessment-mcp는 `ASSESSMENT_MCP_TOKEN`도).

## debate-mcp (`services/debate-mcp/`, `:9101/mcp`)

패키지 `debate_mcp`(`__main__`·`server`·`tools`·`client`·`config`).

| 도구 | 내부 API | 구분 | 메모 |
|---|---|---|---|
| get_debate_state | GET /debate_state + GET /sessions/{id} | 조회 | DebateState(현재 지지·칸·카드 진행·`invite_delivered`) + `seats: [{seat: candidate, display_name}, {seat, display_name, avatar_url}]`. 페르소나 유형 없음(내부 응답의 `persona_type`을 빼고 전달). 그래프 load가 부르고, 페르소나에게는 도구가 아니라 컨텍스트로 미리 넣는다(R5-12). 그때 `card_progress`·`cost_left`는 그래프가 뺀다. 페르소나에 도구로 바인딩하지 않는다 |
| get_transcript | GET /messages | 조회 | `[{id, sender, content, phase, is_invite, t_min}]`. meta·intent·origin 없음. limit 기본 30 |
| get_draft | GET /proposal | 조회 | 기획안 초안. 페르소나에는 proposal 단계에서만 바인딩. report-worker는 이 도구가 아니라 같은 내부 API로 제출본을 직접 읽는다 |
| post_message | POST /messages | 변경 | sender p1∣p2∣p3∣system. (run_id, seq) 멱등. `is_invite`면 api가 그 단계 `invite_delivered`를 켠다(두 번째 `conflict`). `card_ids`(배열)가 있으면 api가 카드마다 그 화자를 `delivered_seats`에 더한다(R5-4, 공동 전달은 두 카드 id) |
| propose_option | POST /options | 변경 | proposer p1∣p2∣p3. 시나리오 안은 `scenario_option_key`, 자유 안은 `source_message_id`로 멱등. 지원자가 같은 카탈로그 안을 먼저 말해 행이 있으면 그 행을 그대로 돌려준다(최초 제안자 보존, R5-6) |
| set_stance | POST /stances | 변경 | participant p1∣p2∣p3, support∣oppose, source_message_id = 그 입장을 말한 자기 메시지 |
| fill_slot | POST /slots/fill | 변경 | 입력 `{session_id, option_id, seat: p1∣p2∣p3, content, run_id}` → 출력 `{slot, phase, message}`. 합의 채우기 전용. 대상 단계는 option의 phase이고 `filled_by` 인자는 없다(`seat`가 공지 화자이자 `filled_by`, api가 `resolution: consensus`로 저장). `content`는 합의 공지 고정 문장이고 api가 칸 확정과 같은 트랜잭션에서 seq 5 메시지(`meta.kind=consensus_fill`)로 저장한다. 별도 `post_message`는 없다. 같은 run_id로 이미 확정됐으면 기존 칸과 공지를 그대로 돌려주고, 아니면 `conflict/phase_not_open` → `conflict/slot_confirmed` → `conflict/no_consensus` 순서로 검사한다. 409면 칸·공지 모두 저장되지 않는다. 시간 초과 채우기는 api가 그래프·MCP 없이 직접 한다(R5-2) |

- 지원자의 안·입장은 api가 의도 분류로 직접 저장하므로 도구 입력에 `candidate`는 없다.

## rag-mcp (`services/rag-mcp/`, `:9102/mcp`)

| 도구 | 구분 | 메모 |
|---|---|---|
| search_materials | 조회 | `{session_id, query, top_k?=3}` → `[{material_id, title, chunk, score}]`. 내부 GET /materials로 잠금·확정 키워드(`keyword_key`)·catalog_version을 확인하고 같은 버전·같은 키워드 청크만 코사인 유사도 상위 k. 잠겨 있으면 `not_released`. 해당 버전 청크가 없으면 `upstream_unavailable`(재색인 필요) |
| get_material | 조회 | `{session_id, material_id}` → 본문. 열린 자료가 아니면 `not_released`. 본문은 채용 건 `catalog_version`으로 DB에 시드된 원문(내부 API 경유, R5-18)이라 검색 청크와 같은 버전이다 |
| reindex_materials | 변경(관리용) | `catalog_version`별로 시드된 자료 원문(원천은 `scenarios/missions/<id>/materials/<keyword>/*.md`)을 400자 청크로 나눠 BGE-M3(1024차원) 임베딩 → `material_chunks(material_id, keyword_key, chunk_no, content, embedding, model, catalog_version)`. 키 (catalog_version, material_id, chunk_no). 이전 버전 청크는 남겨 진행 중 채용 건이 자기 버전으로 검색. 에이전트에 노출하지 않음 |

- catalog_version은 api와 같은 함수(`scenarios/version.py`, 미션 폴더 전체 해시).
- 모델 로드는 시작 시 1회. 컨테이너 메모리 3GB.

## assessment-mcp (`services/assessment-mcp/`, `:9103/mcp`)

평가 입력 조회와 평가 결과 기록 전용(D18·D19 개정). 모든 요청에 `X-Assessment-Token`(env `ASSESSMENT_MCP_TOKEN`)이 있어야 하고 없거나 다르면 `forbidden`. 정확한 입출력과 내부 API 경로는 [contracts/mcp.md](../contracts/mcp.md) "MCP 도구"가 정본이다(아래 내부 API 열은 대응 관계 메모).

| 도구 | 내부 API | 구분 | 호출자 | 메모 |
|---|---|---|---|---|
| record_observation | POST /observations | 변경 | 진행 매니저 결정적 노드 `apply`(첫 카드 발언 게시 직후)·`cards`(발언 없는 `honesty_credit`), 코드 호출 | 카드 발동 기록. 트리거는 시각 기반 `card_due`뿐이고(R5-4) 첫 카드 발언이 실제 게시된 뒤에만 카드마다 기록한다(공동 전달 메시지는 카드마다 1건). 입력은 계약대로 `{session_id, card_id, phase, trigger_message_id?, run_id}`. (session_id, card_id) 멱등 |
| get_rubric | GET /rubric | 조회 | 워커 collect | 세션 `positions.scenario`에 복사된 척도: 5요소 차원 19개의 id·정의·수준 1~5 앵커·기록 없음 사유·카드 창, `rubric_version` |
| get_session_record | GET /sessions/{id}, /messages(전체, 페이지 반복), /slots, /proposal, /observations, /facts, /contributions | 조회 | 워커 collect | 평가 입력 묶음: 대화 전체(intent·origin 포함)·칸 기록(지지 현황 스냅샷)·기획안 제출본(`auto_submitted`)·카드 발생 observations·사실 지표·Contributions. 전용 엔드포인트 없이 기존 내부 GET을 모아 SessionRecord로 조립. completed 이후만(아니면 `conflict`) |
| get_transcript_span | GET /transcript_span | 조회 | 워커 draft | 근거 메시지의 앞뒤 맥락(기본 앞 5·뒤 3). **리포트 표시용**이며 평가 판단 입력이 아니다 |
| get_contributions | GET /contributions | 조회 | 워커 collect | 안별 최초 제안자·채택 여부(`contribution_check` 원천) |
| save_evaluation | POST /internal/sessions/{id}/evaluations | 변경 | 워커 evaluate(코드 호출) | `{session_id, run_id, rubric_version, prompt_versions, model, dimensions[], elements[]}`(`prompt_versions`는 평가 프롬프트 파일별 개별 해시 맵, 형식 정본 [contracts/README.md](../contracts/README.md), R5-14). **멱등 키 `(session_id, run_id)`**: 같은 키 재호출은 저장하지 않고 `existing=true`와 기존 값. api가 차원 행과 `evaluation_runs` 한 행(요소 요약 `elements` 포함)을 한 트랜잭션으로 저장한다. 형식 위반은 `invalid_input` |

검증 책임(R5-14):

| 검증 | 주체 | 위반 시 |
|---|---|---|
| 스키마·차원 19개 안·차원 중복·`level`과 `no_record_reason` 중 하나만·사유 코드 | 워커(저장 전) + api(저장 시) | 워커: 요소 단위 1회 재요청 / api: 422 `invalid_input` |
| `evidence_refs`가 같은 세션의 실제 행 | 워커 + api | 워커: 근거 부재로 보고 요소 단위 재요청 / api: 422 |
| 수준이 있는데 지원자 근거 단위 0개 | 워커만 | 요소 단위 1회 재요청 → 남으면 그 차원 `evaluate_failed` |
| 수준 4·5인데 서로 다른 지원자 근거 단위 1개 | 워커만 | 거부하지 않고 수준 3·확신도 최대 medium으로 깎음 |
| 확신도 상한 | 워커만 | 거부하지 않고 상한으로 깎음(clamp) |
| 금지어(`rationale`·`summary`) | 워커만 | 요소 단위 1회 재요청 |

MCP는 판단하지 않고 그대로 전달한다.

패키지 구조는 debate-mcp와 같다(`assessment_mcp/{__main__, server, tools, client, config}.py`, `/health`, Dockerfile, pyproject·lockfile).

## 검증 항목

- MCP Inspector에서 세 서버의 도구 목록·설명 확인.
- 연결 테스트: 그래프 → debate-mcp `post_message` → api 저장 → WS `message.new` 1건, `fill_slot` 합의 전 `conflict`·message `no_consensus`(공지 메시지 0건), 성공 시 `slot.confirmed`·`message.new`(공지) 각 1건, 같은 (run_id, seq) 재호출 중복 없음, 침묵 초대 두 번째 `conflict`, `card_ids` 게시 뒤 DebateState의 카드마다 그 화자 `delivered`(두 카드 공동 전달 포함), 지원자가 먼저 만든 카탈로그 안에 `propose_option`하면 같은 option_id.
- rag: 키워드 확정 전 `search_materials`·`get_material` `not_released`, 확정 뒤 그 키워드 청크만 결과(다른 키워드 청크 0), 버전 청크 없음 `upstream_unavailable`, 카탈로그 파일을 바꿔도 기존 채용 건의 검색 청크와 `get_material` 본문이 같은 옛 버전, `reindex_materials` 뒤 키워드별 청크 수 기록.
- assessment: 토큰 없거나 다르면 `forbidden`, `record_observation` 멱등, `save_evaluation` 같은 `(session_id, run_id)` 재호출 `existing=true`(행 중복 없음, `evaluation_runs` 1행), 차원 목록 밖 `dimension_id`·사유 코드 밖 값 `invalid_input`, 확신도 상한 위반 본문은 api가 거부하지 않음, `get_session_record`가 completed 전 `conflict`, `get_transcript_span` 기본 앞 5·뒤 3.
- 허용 목록: 페르소나 노드에서 `record_observation`·`get_rubric`·`get_session_record` 호출 시도가 거부되고 페르소나 MCP 설정에 assessment URL·토큰이 없음. report-worker 클라이언트는 허용 5개 밖 도구(`record_observation`)를 호출하지 않는다.
- 공통: api 중단 시 `upstream_unavailable`(1회 재시도 뒤), 잘못된 입력 `invalid_input`, 세 서버의 모든 도구가 `async def`.
