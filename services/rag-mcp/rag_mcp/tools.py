"""rag-mcp 도구 3개(docs/contracts/mcp.md "rag-mcp"). 모두 `async def`(S3).

잠금(키워드 확정 전 차단)·확정 키워드·catalog_version은 api 내부
`GET /internal/sessions/{id}/materials`가 최종 판단하고 rag-mcp는 그 값으로 거르기만 한다.
본문은 api가 같은 catalog_version의 `materials` 행에서 돌려준다.
반환은 `{ok, data}` 또는 `{ok:false, error:{code, message, retryable}}`.
"""

from typing import Annotated, Any

import asyncpg
from pydantic import Field

from rag_mcp import client, materials, store
from rag_mcp.client import fail, ok
from rag_mcp.embedder import embed, get_embedder
from rag_mcp.server import mcp

SessionId = Annotated[str, Field(description="세션 ID. 그래프가 주입하며 모델이 바꾸지 않는다.")]
MaterialId = Annotated[str, Field(min_length=1, description="자료 ID(예: water_flood_cases)")]

# DB 연결 실패·쿼리 오류. 검색·색인은 DB 없이 진행할 수 없으므로 upstream_unavailable.
DB_ERRORS = (OSError, asyncpg.PostgresError)


@mcp.tool
async def search_materials(
    query: Annotated[str, Field(min_length=1, description="찾을 내용(자연어 질문이나 키워드)")],
    session_id: SessionId,
    top_k: Annotated[int, Field(ge=1, le=10, description="돌려줄 청크 수(기본 3)")] = 3,
) -> dict[str, Any]:
    """확정된 키워드의 참고 자료에서 질문과 가까운 문단을 찾는다.

    세션의 카탈로그 버전으로 색인된 청크 중 열린 자료만 코사인 유사도 순으로 돌려준다. 결과는
    [{material_id, title, chunk, score}]. 키워드 확정 전이면 not_released, 해당 버전 색인이 없으면
    upstream_unavailable.
    """
    listed = await client.call("GET", f"/internal/sessions/{session_id}/materials")
    if not listed["ok"]:
        return listed
    data = listed["data"]
    if data["locked"] or not data.get("keyword_key"):
        return fail("not_released", "materials open after the keyword is confirmed")
    titles = {m["material_id"]: m["title"] for m in data["materials"]}
    version = materials.index_version(data["catalog_version"])
    conf = client.current_settings()
    try:
        if not await store.has_version(conf.database_url, version):
            return fail("upstream_unavailable", f"no index {version}; reindex", True)
        if not titles:
            return ok([])
        vector = (await embed(get_embedder(conf.embedding_model), [query]))[0]
        rows = await store.search(
            conf.database_url, version, data["keyword_key"], list(titles), vector, top_k
        )
    except DB_ERRORS as exc:
        return fail("upstream_unavailable", f"database: {type(exc).__name__}", True)
    return ok(
        [
            {
                "material_id": r["material_id"],
                "title": titles[r["material_id"]],
                "chunk": r["content"],
                "score": round(float(r["score"]), 4),
            }
            for r in rows
        ]
    )


@mcp.tool
async def get_material(material_id: MaterialId, session_id: SessionId) -> dict[str, Any]:
    """열린 참고 자료 하나의 전문을 읽는다. 결과는 {material_id, title, content}.

    본문은 채용 건의 카탈로그 버전 원문이라 같은 채용 건의 지원자는 같은 자료를 본다. 키워드
    확정 전이거나 다른 키워드의 자료면 not_released, 없는 자료는 not_found.
    """
    return await client.call("GET", f"/internal/sessions/{session_id}/materials/{material_id}")


@mcp.tool
async def reindex_materials(
    mission_id: Annotated[
        str, Field(min_length=1, description="색인할 미션 ID(기본 debate)")
    ] = materials.DEFAULT_MISSION,
) -> dict[str, Any]:
    """미션의 현재 카탈로그 버전으로 자료를 줄·문장 경계 청크(상한 400자)로 다시 색인한다(관리용).

    색인 키는 카탈로그 버전 + 청크 규칙 버전(index_version)이다. 같은 키는 덮어써서 몇 번 불러도
    결과가 같고, 다른 키의 청크는 지우지 않아 진행 중인 채용 건이 자기 버전으로 계속 검색할 수
    있다. 청크 규칙이 바뀌면 키가 달라져 다시 색인하기 전까지 검색이 upstream_unavailable이다.
    결과는 {indexed: 색인된 청크 수, model, dim, catalog_version, index_version}. 에이전트에는
    노출하지 않는다.
    """
    conf = client.current_settings()
    try:
        catalog = materials.catalog_version(conf.scenarios_dir, mission_id)
    except FileNotFoundError:
        return fail("not_found", f"mission {mission_id} not found")
    version = materials.index_version(catalog)
    pieces = [
        (m.material_id, m.keyword_key, no, text)
        for m in materials.load_materials(conf.scenarios_dir, mission_id)
        for no, text in enumerate(materials.chunk(m.content))
    ]
    embedder = get_embedder(conf.embedding_model)
    vectors = await embed(embedder, [text for *_, text in pieces])
    chunks = [
        store.Chunk(material_id, keyword, no, text, vector)
        for (material_id, keyword, no, text), vector in zip(pieces, vectors, strict=True)
    ]
    try:
        await store.replace_version(conf.database_url, version, embedder.model, chunks)
    except DB_ERRORS as exc:
        return fail("upstream_unavailable", f"database: {type(exc).__name__}", True)
    return ok(
        {
            "indexed": len(chunks),
            "model": embedder.model,
            "dim": embedder.dim,
            "catalog_version": catalog,
            "index_version": version,
        }
    )
