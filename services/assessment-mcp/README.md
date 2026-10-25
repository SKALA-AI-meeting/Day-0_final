# assessment-mcp

평가 기록자 MCP 서버. 평가 에이전트가 읽을 세션 기록을 조립하고 관찰·평가를 저장한다. 페르소나 에이전트는 이 서버에 접근하지 못한다.

## 한눈에

| 항목 | 내용 |
|---|---|
| 역할 | 세션 기록(SessionRecord)·척도·근거 앞뒤 대화·기여 조회와 관찰·평가 저장 |
| 사용 기술 | Python 3.12, fastmcp(Streamable HTTP), httpx, uvicorn |
| 포트 | 9103 (`/mcp`, 헬스 `/health`) |
| 필요한 env | `API_INTERNAL_URL` `INTERNAL_TOKEN` `ASSESSMENT_MCP_TOKEN` |
| 의존 서비스 | api 내부 앱(8001). DB·큐에는 직접 연결하지 않는다 |

호출자: api의 결정적 노드(관찰 기록)와 report-worker(평가). 모든 도구 호출은 `X-Assessment-Token`이 `ASSESSMENT_MCP_TOKEN`과 같아야 하고, 아니면 api를 부르지 않고 `forbidden`이다(서버 토큰이 비어 있으면 전부 거부). 페르소나 에이전트는 허용 목록과 토큰 양쪽에서 막힌다.

## 구현된 기능

| 기능 | 코드 | PRD |
|---|---|---|
| 관찰 기록(멱등 키 그대로 전달) | `assessment_mcp/tools.py` | [F-30](../../PRD.md) |
| 평가 저장(멱등, 형태 검증) | `assessment_mcp/tools.py` | [F-33](../../PRD.md) |
| SessionRecord 조립: 내부 GET(세션·메시지 페이지 반복·칸·기획안·관찰·사실 지표·기여·척도)을 모아 관찰 창을 척도 규칙과 칸 확정 시각으로 계산. completed 전이면 `conflict` | `assessment_mcp/tools.py` | [F-33, F-37](../../PRD.md) |
| 근거 메시지 앞 5·뒤 3 대화 조회, 기여 대조 자료 | `assessment_mcp/tools.py` | [F-35](../../PRD.md) |
| 토큰 검사, api 호출·오류 매핑 | `assessment_mcp/server.py`, `client.py` | - |

기능별 검증 테스트는 [`docs/features.md`](../../docs/features.md)에 있다.

## 실행

저장소 루트에서 실행한다. api 내부 앱(8001)이 먼저 떠 있어야 한다. 로컬 실행 env는 [configuration.md](../../docs/guides/configuration.md) "로컬 실행용 `infra/.env.local`"을 따른다.

```
uv run --directory services/assessment-mcp python -m assessment_mcp    # 9103
```

## MCP 도구

<!-- gen:mcp-tools -->
> 생성 블록이다. 코드를 바꾸면 `make docs-gen`으로 갱신한다.

| 도구 | 하는 일 | 인자 |
|---|---|---|
| `record_observation` | 카드 발동을 기록한다. | `session_id`, `card_id`, `phase`, `run_id`, `trigger_message_id` |
| `save_evaluation` | 평가 결과(차원 행·요소 요약·실행 메타)를 저장한다. | `session_id`, `run_id`, `rubric_version`, `prompt_versions`, `model`, `dimensions`, `elements` |
| `get_rubric` | 세션 채용 건에 복사된 평가 척도를 조회한다. | `session_id` |
| `get_transcript_span` | 근거 메시지와 그 앞뒤 맥락(기본 앞 5·뒤 3)을 조회한다. | `session_id`, `message_id`, `before`, `after` |
| `get_contributions` | 안별 최초 제안자·채택 여부와 칸별 최초 제안자·채운 자리를 조회한다(정직 대조 근거). | `session_id` |
| `get_session_record` | 평가 입력 묶음(SessionRecord)을 조립한다. | `session_id` |
<!-- /gen:mcp-tools -->

변경 도구(record_observation·save_evaluation)는 멱등 키를 그대로 전달하고, 조회 도구(get_rubric·get_session_record·get_transcript_span·get_contributions)는 부작용이 없다. 도구 계약은 [`docs/contracts/mcp.md`](../../docs/contracts/mcp.md)에 있다.

## 테스트

pytest 단위 테스트다. api는 httpx MockTransport로 mock하고, 출력은 `tests/contract_shape.py`의 `MCP_TOOLS` 형태로 검사한다. DB·네트워크·LLM이 필요 없다.

```
uv run --directory services/assessment-mcp pytest -q    # 이 서비스만
make test                                               # 전체
```

<!-- gen:tests -->
> 생성 블록이다. 코드를 바꾸면 `make docs-gen`으로 갱신한다.

총 20개.

<details><summary><code>tests/test_health.py</code> (1)</summary>

- `test_health`

</details>

<details><summary><code>tests/test_http_token.py</code> (3)</summary>

- `test_token_header_checked_over_http` ×3

</details>

<details><summary><code>tests/test_tools.py</code> (16)</summary>

- `test_six_tools_listed_with_descriptions`
- `test_all_tools_async`
- `test_tools_follow_contract`
- `test_missing_or_wrong_token_is_forbidden_without_api_call`
- `test_empty_server_token_rejects_everything`
- `test_save_evaluation_idempotent_pass_through`
- `test_save_evaluation_api_validation_error_is_invalid_input`
- `test_save_evaluation_bad_shape_rejected_before_api`
- `test_record_observation_idempotency_pass_through`
- `test_transcript_span_defaults_before_5_after_3`
- `test_session_record_assembled_in_contract_shape`
- `test_session_record_pages_messages_until_short_page`
- `test_session_record_before_completed_is_conflict`
- `test_session_record_upstream_error_propagates`
- `test_error_mapping_and_retry`
- `test_session_record_failed_session_is_conflict`

</details>
<!-- /gen:tests -->

## 관련 문서

- [`docs/services/mcp.md`](../../docs/services/mcp.md): MCP 서버 3개의 구성
- [`docs/contracts/mcp.md`](../../docs/contracts/mcp.md): 도구 계약
- [`docs/contracts/internal.md`](../../docs/contracts/internal.md): 평가 저장 등 내부 REST
- [`docs/product/rubric.md`](../../docs/product/rubric.md): 척도와 검증 규칙
- [`docs/architecture/security.md`](../../docs/architecture/security.md): 토큰 경계
