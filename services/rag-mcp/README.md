# rag-mcp

키워드별 참고 자료를 검색·조회·색인하는 MCP 서버. 키워드가 확정되기 전에는 자료가 열리지 않는다.

## 한눈에

| 항목 | 내용 |
|---|---|
| 역할 | 확정 키워드의 자료 청크 검색(search_materials), 열린 자료 전문 조회(get_material), 미션 자료 색인(reindex_materials) |
| 사용 기술 | Python 3.12, fastmcp(Streamable HTTP), asyncpg + pgvector, sentence-transformers(BGE-M3), uvicorn |
| 포트 | 9102 (`/mcp`, 헬스 `/health`) |
| 필요한 env | `DATABASE_URL` `API_INTERNAL_URL` `INTERNAL_TOKEN` `SCENARIOS_DIR` `EMBEDDING_MODEL` `HF_HOME`(컨테이너 모델 캐시) |
| 의존 서비스 | postgres(`material_chunks`, pgvector), api 내부 앱(잠금·확정 키워드·catalog_version 판단, 자료 원문) |

호출자: 페르소나·어시스턴트(`search_materials`·`get_material`). `reindex_materials`는 에이전트에 노출하지 않고 `scripts/seed.py`가 부른다.

## 구현된 기능

| 기능 | 코드 | PRD |
|---|---|---|
| 확정 키워드 자료만 검색(코사인 유사도 상위 top_k), 확정 전 `not_released` | `rag_mcp/tools.py`, `store.py` | [F-15, F-16](../../PRD.md) |
| 열린 자료 전문 조회(api의 catalog_version 원문) | `rag_mcp/tools.py` | [F-15](../../PRD.md) |
| 카탈로그 해시 + 청크 규칙 버전(`index_version`)별 청크 색인(줄·문장 경계, 상한 400자, 앞 청크 끝 단위 겹침), 같은 키는 덮어쓰고 다른 키는 유지. 현재 키가 없으면 같은 카탈로그의 옛 키 색인으로 검색(먼저 만든 채용 건) | `rag_mcp/materials.py`, `store.py` | [F-06](../../PRD.md) 일부 |
| BGE-M3 임베딩(시작 때 1회 로드) | `rag_mcp/embedder.py` | - |

자료 원문(`materials` 행)은 `scripts/seed.py`가 같은 catalog_version으로 넣고, rag-mcp는 `material_chunks`만 쓴다. api 오류 매핑·재시도는 `rag_mcp/client.py`. 기능별 검증 테스트는 [`docs/features.md`](../../docs/features.md)에 있다.

## 실행

저장소 루트에서 실행한다. 포트는 9102 고정이라 컨테이너와 로컬 중 하나만 띄운다.

**컨테이너로 쓸 때(기본)**: `make infra-up`이 postgres·rabbitmq와 함께 rag-mcp를 `127.0.0.1:9102`에 올린다. 컨테이너는 내부 API를 `http://host.docker.internal:8001`(로컬 api)로 부른다.

```
make infra-up                                                   # postgres 5432, rabbitmq 5672, rag-mcp 9102
make seed                                                       # 자료 원문 시드 후 reindex_materials로 색인
```

**코드를 고치며 로컬로 띄울 때**: infra-up이 올린 rag-mcp 컨테이너를 먼저 내린다. 로컬 env는 `infra/.env.local`(`DATABASE_URL`·`API_INTERNAL_URL`을 localhost로, `HF_HOME`은 쓰기 가능한 로컬 폴더. 항목은 [configuration.md](../../docs/guides/configuration.md) "로컬 실행용 `infra/.env.local`").

```
make infra-up
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml --env-file infra/.env stop rag-mcp
uv run --directory services/rag-mcp python -m rag_mcp           # 9102. 시작 시 BGE-M3 1회 로드(첫 실행은 약 2.3GB 다운로드, HF_HOME 캐시)
make seed
```

## MCP 도구

<!-- gen:mcp-tools -->
> 생성 블록이다. 코드를 바꾸면 `make docs-gen`으로 갱신한다.

| 도구 | 하는 일 | 인자 |
|---|---|---|
| `search_materials` | 확정된 키워드의 참고 자료에서 질문과 가까운 문단을 찾는다. | `query`, `session_id`, `top_k` |
| `get_material` | 열린 참고 자료 하나의 전문을 읽는다. | `material_id`, `session_id` |
| `reindex_materials` | 미션의 현재 카탈로그 버전으로 자료를 줄·문장 경계 청크(상한 400자)로 다시 색인한다(관리용). | `mission_id` |
<!-- /gen:mcp-tools -->

| 도구 | 오류 동작 |
|---|---|
| search_materials | api 내부 `GET /internal/sessions/{id}/materials`의 잠금·확정 키워드·catalog_version으로 거른 `material_chunks`만 검색. 키워드 확정 전이면 `not_released`, 해당 버전 색인이 없으면 `upstream_unavailable` |
| get_material | 열리지 않은 자료는 `not_released` |
| reindex_materials | `{mission_id?}`(기본 debate). `{indexed, model, dim, catalog_version}` 반환. 에이전트에 노출하지 않음 |

도구 계약은 [`docs/contracts/mcp.md`](../../docs/contracts/mcp.md)에 있다.

## 테스트

pytest 단위 테스트다. 기본은 가짜 임베딩과 임시 DB(`rag_test_*`)를 쓰며 `make infra-up`의 postgres가 필요하다(없으면 DB 테스트를 건너뜀). `live` 표시는 실제 BGE-M3(CPU)를 로드하는 테스트로 `make test`에서 빠진다. 테스트용 DB 주소는 `TEST_DATABASE_URL`.

```
uv run --directory services/rag-mcp pytest -q               # 이 서비스만
uv run --directory services/rag-mcp pytest -q -m live       # 실제 BGE-M3
make test                                                   # 전체
```

<!-- gen:tests -->
> 생성 블록이다. 코드를 바꾸면 `make docs-gen`으로 갱신한다.

총 18개(live 1개 포함, `make test`는 live를 뺀다).

<details><summary><code>tests/test_chunk.py</code> (6)</summary>

- `test_every_line_of_every_material_stays_whole`
- `test_number_is_not_split_across_chunks`
- `test_overlap_repeats_last_unit_of_previous_chunk`
- `test_long_line_splits_at_sentence_then_space_boundaries`
- `test_blank_lines_are_separators_only`
- `test_index_version_folds_chunker_version`

</details>

<details><summary><code>tests/test_health.py</code> (1)</summary>

- `test_health`

</details>

<details><summary><code>tests/test_live.py</code> (1)</summary>

- `test_bge_m3_embeds_1024_normalized_and_ranks_related_text_higher` (live)

</details>

<details><summary><code>tests/test_tools.py</code> (10)</summary>

- `test_tools_listed_async_with_descriptions`
- `test_search_returns_confirmed_keyword_chunks_only`
- `test_blocked_before_keyword_confirmed`
- `test_get_material_returns_api_versioned_source`
- `test_search_without_index_for_version_is_upstream_unavailable`
- `test_reindex_idempotent_and_keeps_previous_version`
- `test_error_codes_mapped_and_4xx_not_retried`
- `test_invalid_input_returned_as_error_result`
- `test_connection_failure_retried_once_then_upstream_unavailable`
- `test_old_position_falls_back_to_legacy_index_of_its_catalog`

</details>
<!-- /gen:tests -->

## 관련 문서

- [`docs/services/mcp.md`](../../docs/services/mcp.md): MCP 서버 3개의 구성
- [`docs/contracts/mcp.md`](../../docs/contracts/mcp.md): 도구 계약
- [`docs/architecture/data-model.md`](../../docs/architecture/data-model.md): materials·material_chunks
- [`docs/guides/scenario-changes.md`](../../docs/guides/scenario-changes.md): 자료를 바꾼 뒤 시드·재색인
- [`docs/guides/configuration.md`](../../docs/guides/configuration.md): env
