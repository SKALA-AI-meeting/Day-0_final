# 보안·권한 경계

인증 토큰의 종류와 생명주기는 [계약 README](../contracts/README.md) "인증 생명주기", 도구 단위 허용 목록은 [contracts/mcp.md](../contracts/mcp.md) "도구 허용 목록"이 정본이다. 이 문서는 주체별 경계와 그 이유를 적는다.

## 권한 경계

| 주체 | debate-mcp | rag-mcp | assessment-mcp | 비고 |
|---|---|---|---|---|
| 진행 매니저 결정적 노드(load·cards·apply·fill) | 전부 | — | `record_observation` | 코드가 직접 호출 |
| 진행 매니저 LLM(turn) | — | — | — | 도구 없음. 주입된 상태만 본다 |
| 페르소나 respond | 조회 2개(`get_transcript`·`get_draft`, `get_debate_state`는 바인딩하지 않고 load가 걸러 주입) | `search_materials`·`get_material` | **불가** | 변경은 결정적 노드만. 평가 기준을 보고 행동을 맞추지 못하게 |
| 어시스턴트 | — | `search_materials`·`get_material` | — | 모델 도구 호출 루프, 요청당 최대 3회 |
| report-worker | — | — | `get_rubric`·`get_session_record`·`get_transcript_span`·`get_contributions`·`save_evaluation` | 코드가 직접 호출, 평가 에이전트 LLM에는 도구 없음(입력 묶음 주입). 리포트 저장은 내부 REST |

- session_id는 그래프가 주입하고 모델이 바꿀 수 없다.
- assessment-mcp는 `X-Assessment-Token` 헤더(env `ASSESSMENT_MCP_TOKEN`)가 있어야 응답한다. 토큰은 api의 결정적 노드 모듈과 워커에만 주고 페르소나 노드의 MCP 클라이언트 설정에는 넣지 않는다(허용 목록과 이중 차단).
- 페르소나 컨텍스트에 넣지 않는 것: 평가 척도·평가 결과·관찰·카드 이름·요소 이름·`card_progress`·`cost_left`, 다른 자리의 성향 이름, 아직 오지 않은 카드, 어시스턴트 대화, 열리지 않은 자료, 지원자의 기획안 초안(요청받아 `get_draft`를 부른 경우 제외).
- 권한·상태 판단(단계, 합의, 마감, 자료 잠금)은 api가 최종 판단한다. MCP는 전달만 한다.

## assessment 접근 차단

페르소나가 평가 기준을 보고 행동을 맞추지 못하게 이중으로 막는다.
1. **허용 목록**: 페르소나 respond에 바인딩하는 도구 목록에 assessment 도구가 없다. 목록 밖 호출은 그래프가 거부하고 `last_error`에 남긴다.
2. **클라이언트·토큰 분리**: `app/agent/mcp_client`는 debate·rag 클라이언트와 assessment 클라이언트를 따로 만든다. assessment 클라이언트(토큰 포함)는 `cards`·`apply` 노드 모듈에만 주입하고 페르소나 노드 설정에는 넣지 않는다. 토큰 없는 호출은 서버가 `forbidden`.

## 기업 간 격리

관리자 API의 모든 조회는 JWT의 company_id로 positions를 거쳐 필터한다. 내부 API는 별도 포트(8001)에서만 열리고 session_id로만 접근한다. assessment-mcp는 별도 토큰을 요구한다.

## 비밀값과 보관

- API 키·`JWT_SECRET`·`INTERNAL_TOKEN`·`ASSESSMENT_MCP_TOKEN`은 `infra/.env`, 로컬 앱 실행용 `infra/.env.local`, 프로필로 생성되는 `infra/.env.resolved-*`(모두 git 무시)에만 둔다. 예시는 `infra/.env.example`이고 문서·로그·커밋·이슈에 값을 쓰지 않는다.
- 내부 API(8001)는 compose 네트워크 안에서만 열리고 `X-Internal-Token`이 있어야 한다. 테스트 API(`/internal/test/*`)는 `ENABLE_TEST_API=1`일 때만 열린다.
- 초대 토큰 원문은 링크에만 있고 DB에는 해시만 둔다. 세션 데이터는 종료 90일 뒤 지운다([data-model.md](data-model.md) "보관과 삭제").
