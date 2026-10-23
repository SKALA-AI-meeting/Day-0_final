"""완료 기준: 도구 목록·설명·async, 검색이 확정 키워드·같은 버전 청크만, 키워드 확정 전 차단,
본문은 api 버전별 원문, 재색인(버전 키·이전 버전 유지), 오류 매핑·재시도.
"""

import inspect
import json
import shutil

import httpx
from rag_mcp import client, materials, store, tools

from tests.conftest import REPO_ROOT, SCENARIOS, call, make_settings

TOOLS = {"search_materials", "get_material", "reindex_materials"}
VERSION = materials.catalog_version(SCENARIOS)
ALL = materials.load_materials(SCENARIOS)


def content(material_id: str, scenarios=SCENARIOS) -> str:
    return next(
        m.content for m in materials.load_materials(scenarios) if m.material_id == material_id
    )


def session_materials(keyword: str | None, version: str = VERSION):
    """내부 GET /internal/sessions/{id}/materials 응답. keyword=None이면 확정 전(잠김)."""
    listed = [
        {"material_id": m.material_id, "title": m.title} for m in ALL if m.keyword_key == keyword
    ]
    body = {"catalog_version": version, "locked": keyword is None, "keyword_key": keyword,
            "materials": listed}  # fmt: skip
    return lambda r: httpx.Response(200, json=body)


async def test_tools_listed_async_with_descriptions(mcp_client):
    listed = await mcp_client.list_tools()
    assert {t.name for t in listed} == TOOLS
    for t in listed:
        assert t.description and len(t.description) > 10
        for name, prop in t.input_schema["properties"].items():
            assert prop.get("description"), (t.name, name)
    # S3: FastMCP는 sync 도구를 워커 스레드에서 돌리므로 모든 도구가 async def
    assert all(inspect.iscoroutinefunction(getattr(tools, name)) for name in TOOLS)


async def test_search_returns_confirmed_keyword_chunks_only(mcp_client, api, db):
    indexed = await call(mcp_client, "reindex_materials", {})
    assert indexed["ok"] is True
    target = materials.chunk(content("water_flood_cases"))[0]
    fire_text = materials.chunk(content("fire_stats"))[0]

    api.handler = session_materials("water")
    out = await call(mcp_client, "search_materials", {"query": target, "session_id": "s1"})
    assert out["ok"] is True
    top = out["data"][0]
    assert (top["material_id"], top["title"], top["chunk"]) == (
        "water_flood_cases",
        "도심 침수 사례",
        target,
    )
    assert top["score"] > 0.99
    assert api.requests[0].url.path == "/internal/sessions/s1/materials"
    assert api.requests[0].headers["x-internal-token"] == "test-token"

    other = await call(mcp_client, "search_materials", {"query": fire_text, "session_id": "s1"})
    assert all(r["material_id"].startswith("water_") for r in other["data"])


async def test_blocked_before_keyword_confirmed(mcp_client, api):
    api.handler = session_materials(None)
    out = await call(mcp_client, "search_materials", {"query": "침수", "session_id": "s1"})
    assert out["ok"] is False and out["error"]["code"] == "not_released"

    api.handler = lambda r: httpx.Response(
        423, json={"error": {"code": "not_released", "message": "m"}}
    )
    got = await call(mcp_client, "get_material", {"material_id": "water_stats", "session_id": "s1"})
    assert got["error"]["code"] == "not_released"


async def test_get_material_returns_api_versioned_source(mcp_client, api):
    fixture = REPO_ROOT / "tests" / "fixtures" / "contracts" / "internal_material.json"
    contract_material = json.loads(fixture.read_text(encoding="utf-8"))
    api.handler = lambda r: httpx.Response(200, json=contract_material)
    got = await call(
        mcp_client, "get_material", {"material_id": "water_flood_cases", "session_id": "s1"}
    )
    assert got == {"ok": True, "data": contract_material}
    assert api.requests[0].url.path == "/internal/sessions/s1/materials/water_flood_cases"


async def test_search_without_index_for_version_is_upstream_unavailable(mcp_client, api, db):
    await call(mcp_client, "reindex_materials", {})
    api.handler = session_materials("water", version="not-indexed")
    out = await call(mcp_client, "search_materials", {"query": "침수", "session_id": "s1"})
    assert out["error"] == {
        "code": "upstream_unavailable",
        "message": out["error"]["message"],
        "retryable": True,
    }


async def test_reindex_idempotent_and_keeps_previous_version(
    mcp_client, api, db, database_url, tmp_path
):
    scenarios = tmp_path / "scenarios"
    shutil.copytree(SCENARIOS, scenarios, ignore=shutil.ignore_patterns("e2e", "__pycache__"))
    api.configure(make_settings(database_url, scenarios))

    first = await call(mcp_client, "reindex_materials", {})
    again = await call(mcp_client, "reindex_materials", {})
    assert first == again
    assert first["data"]["dim"] == 1024 and first["data"]["model"] == "fake-1024"
    old = first["data"]["index_version"]
    assert old == materials.index_version(first["data"]["catalog_version"])
    count = "SELECT count(*) FROM material_chunks WHERE catalog_version = $1"
    assert await db.fetchval(count, old) == first["data"]["indexed"]
    keys = {
        r["keyword_key"] for r in await db.fetch("SELECT DISTINCT keyword_key FROM material_chunks")
    }
    assert keys == {"water", "fire", "tree", "wind"}

    doc = scenarios / "missions" / "debate" / "materials" / "water" / "water_stats.md"
    doc.write_text(doc.read_text(encoding="utf-8") + "\n- 추가 문단\n", encoding="utf-8")
    second = await call(mcp_client, "reindex_materials", {})
    new = second["data"]["index_version"]
    assert new != old
    assert await db.fetchval(count, old) == first["data"]["indexed"]
    assert await db.fetchval(count, new) == second["data"]["indexed"]


async def test_error_codes_mapped_and_4xx_not_retried(mcp_client, api):
    cases = {401: "forbidden", 403: "forbidden", 404: "not_found", 410: "conflict"}
    for status, code in cases.items():
        api.handler = lambda r, s=status: httpx.Response(
            s, json={"error": {"code": "x", "message": "m"}}
        )
        out = await call(
            mcp_client, "get_material", {"material_id": "water_stats", "session_id": "s1"}
        )
        assert out["ok"] is False and out["error"]["code"] == code
    assert len(api.requests) == len(cases)


async def test_invalid_input_returned_as_error_result(mcp_client, api):
    out = await call(
        mcp_client, "search_materials", {"query": "침수", "session_id": "s1", "top_k": 0}
    )
    assert out["ok"] is False and out["error"]["code"] == "invalid_input"
    assert api.requests == []


async def test_connection_failure_retried_once_then_upstream_unavailable(mcp_client, api):
    def refuse(request):
        raise httpx.ConnectError("down")

    api.handler = refuse
    out = await call(mcp_client, "search_materials", {"query": "침수", "session_id": "s1"})
    assert out["error"]["code"] == "upstream_unavailable" and out["error"]["retryable"] is True
    assert len(api.requests) == 2


async def test_old_position_falls_back_to_legacy_index_of_its_catalog(mcp_client, api, db):
    """청크 규칙 버전 전에 만든 채용 건: 현재 키 색인이 없으면 같은 카탈로그의 옛 키(접미사 없음)
    색인을 쓰고, 현재 키 색인이 생기면 그것을 쓴다. 다른 카탈로그의 색인만 있으면 재색인 요구."""
    import numpy as np

    legacy = [
        store.Chunk(
            "water_flood_cases", "water", 0, "옛 규칙 청크", np.ones(1024, dtype=np.float32)
        )
    ]
    await store.replace_version(await _url(db), VERSION, "fake-1024", legacy)
    api.handler = session_materials("water")
    out = await call(mcp_client, "search_materials", {"query": "침수", "session_id": "s1"})
    assert out["ok"] is True and [r["chunk"] for r in out["data"]] == ["옛 규칙 청크"]

    indexed = await call(mcp_client, "reindex_materials", {})
    assert indexed["data"]["index_version"] == materials.index_version(VERSION)
    out = await call(mcp_client, "search_materials", {"query": "침수", "session_id": "s1"})
    assert out["ok"] is True and "옛 규칙 청크" not in [r["chunk"] for r in out["data"]]

    api.handler = session_materials("water", version="other-catalog")
    out = await call(mcp_client, "search_materials", {"query": "침수", "session_id": "s1"})
    assert out["ok"] is False and out["error"]["code"] == "upstream_unavailable"
    assert "reindex" in out["error"]["message"]


async def _url(db) -> str:
    """db 픽스처가 연결한 임시 DB의 주소(도구 설정과 같은 값)."""
    return client.current_settings().database_url
