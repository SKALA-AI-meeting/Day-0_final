# debate-mcp

토론 상태를 읽고 쓰는 MCP 서버. api 내부 REST를 얇게 감싸 토론 그래프의 도구로 노출한다.

## 한눈에

| 항목 | 내용 |
|---|---|
| 역할 | 토론 상태·대화·기획안 초안 조회(get_debate_state·get_transcript·get_draft)와 메시지 게시·안 제안·입장·칸 채우기(post_message·propose_option·set_stance·fill_slot) |
| 사용 기술 | Python 3.12, fastmcp(Streamable HTTP), httpx, uvicorn |
| 포트 | 9101 (`/mcp`, 헬스 `/health`) |
| 필요한 env | `API_INTERNAL_URL` `INTERNAL_TOKEN` |
| 의존 서비스 | api 내부 앱(8001). DB·큐에는 직접 연결하지 않는다 |

호출자: 토론 그래프(`services/api`). 페르소나 에이전트는 조회 도구와 rag-mcp 도구만 쓰고, 변경 도구(post_message·propose_option·set_stance·fill_slot)는 그래프의 결정적 노드가 호출한다. 허용 목록은 api의 `agent/mcp_client`가 고정한다.

## 구현된 기능

| 기능 | 코드 | PRD |
|---|---|---|
| 토론 상태·대화 조회(페르소나 유형은 빼고 전달) | `debate_mcp/tools.py` | [F-21, F-22](../../PRD.md) |
| 동료 메시지 게시, 안 제안·입장 기록 | `debate_mcp/tools.py` | [F-12, F-26, F-27](../../PRD.md) |
| 합의된 칸 채우기(합의 전에는 api가 거부) | `debate_mcp/tools.py` | [F-26](../../PRD.md) |
| api 호출·재시도·오류 매핑(5xx·연결 실패 1회 재시도, 409 코드 그대로 전달) | `debate_mcp/client.py` | - |

기능별 검증 테스트는 [`docs/features.md`](../../docs/features.md)에 있다.

## 실행

저장소 루트에서 실행한다. api 내부 앱(8001)이 먼저 떠 있어야 도구 호출이 성공한다.

```
uv run --directory services/debate-mcp python -m debate_mcp     # 9101
```

로컬 실행 env는 [configuration.md](../../docs/guides/configuration.md) "로컬 실행용 `infra/.env.local`"을 따른다. 컨테이너는 compose가 `API_INTERNAL_URL=http://api:8001`을 준다.

## MCP 도구

<!-- gen:mcp-tools -->
> 생성 블록이다. 코드를 바꾸면 `make docs-gen`으로 갱신한다.

| 도구 | 하는 일 | 인자 |
|---|---|---|
| `get_debate_state` | 현재 단계의 토론 상태(DebateState)와 참여자 자리 목록을 조회한다. | `session_id` |
| `get_transcript` | 토론방 최근 메시지(기본 30개)를 시간순으로 조회한다. | `session_id`, `since_t_min`, `phase`, `limit` |
| `get_draft` | 기획안 초안(sections 6칸·revision·updated_at)을 조회한다. | `session_id` |
| `post_message` | AI 동료(p1·p2·p3) 또는 시스템 메시지를 토론방에 게시한다. | `session_id`, `sender`, `content`, `run_id`, `seq`, `expected_generation`, `is_invite`, `card_ids`, `cited_material_ids`, `meta` |
| `propose_option` | AI 동료가 말한 안을 토론 안 목록에 등록한다. | `session_id`, `phase`, `label`, `proposer`, `source_message_id`, `run_id`, `scenario_option_key` |
| `set_stance` | AI 동료의 안에 대한 지지·반대 입장을 기록한다. | `session_id`, `participant`, `option_id`, `stance`, `source_message_id`, `run_id` |
| `fill_slot` | 합의된 안으로 현재 단계의 칸을 채우고 공지 메시지를 남긴다(합의 채우기 전용). | `session_id`, `option_id`, `seat`, `content`, `run_id` |
<!-- /gen:mcp-tools -->

반환은 `{ok, data}` 또는 `{ok:false, error:{code, message, retryable}}`다. 409의 api 오류 코드(`no_consensus`·`stale_generation`·`slot_confirmed`·`phase_not_open`)는 `{code: conflict, message: <api 코드>}`로 그대로 전달한다. 도구 계약(입력·출력·오류 코드)은 [`docs/contracts/mcp.md`](../../docs/contracts/mcp.md)에 있다.

## 테스트

pytest 단위 테스트다. api는 httpx MockTransport로 mock하고, 도구 출력은 `tests/contract_shape.py`의 `MCP_TOOLS` 형태로 검사한다. DB·네트워크·LLM이 필요 없다.

```
uv run --directory services/debate-mcp pytest -q    # 이 서비스만
make test                                           # 전체
```

<!-- gen:tests -->
> 생성 블록이다. 코드를 바꾸면 `make docs-gen`으로 갱신한다.

총 15개.

<details><summary><code>tests/test_health.py</code> (1)</summary>

- `test_health`

</details>

<details><summary><code>tests/test_tools.py</code> (14)</summary>

- `test_seven_tools_listed_with_descriptions`
- `test_all_tools_async`
- `test_tools_follow_contract`
- `test_get_debate_state_merges_seats_without_persona_type`
- `test_get_transcript_passes_filters_and_strips_internal_fields`
- `test_conflict_codes_passed_through_in_message`
- `test_error_codes_mapped`
- `test_5xx_retried_once_then_upstream_unavailable`
- `test_connection_failure_retried_once_then_upstream_unavailable`
- `test_retry_succeeds_after_one_5xx`
- `test_non_json_success_body_becomes_upstream_unavailable`
- `test_idempotency_keys_forwarded_unchanged`
- `test_optional_fields_omitted_when_absent`
- `test_invalid_input_returned_as_error_result`

</details>
<!-- /gen:tests -->

## 관련 문서

- [`docs/services/mcp.md`](../../docs/services/mcp.md): MCP 서버 3개의 구성
- [`docs/contracts/mcp.md`](../../docs/contracts/mcp.md): 도구 계약
- [`docs/architecture/security.md`](../../docs/architecture/security.md): 도구 허용 목록과 토큰 경계
- [`docs/guides/configuration.md`](../../docs/guides/configuration.md): env
