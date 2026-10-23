"""완료 기준: 도구 7개 목록·설명, 계약 픽스처대로의 경로·본문·출력 형태, 409 코드 전달, 오류 매핑,
멱등 키 전달, 잘못된 입력.
"""

import inspect
import json

import httpx
from debate_mcp import tools

from tests.conftest import call, fixture, shape

CASES = fixture("mcp_tools")["debate"]
SPECS = shape.MCP_TOOLS["debate"]
BASE = "/internal/sessions/sess-1"

# 도구 → (메서드, 내부 경로 접미사, 내부 응답 픽스처). 정본은 docs/contracts/mcp.md "debate-mcp"
ROUTES = {
    "get_transcript": ("GET", "/messages", "internal_messages"),
    "get_draft": ("GET", "/proposal", "internal_proposal"),
    "post_message": ("POST", "/messages", "internal_message_post"),
    "propose_option": ("POST", "/options", "internal_option"),
    "set_stance": ("POST", "/stances", "internal_stance"),
    "fill_slot": ("POST", "/slots/fill", "internal_slot_fill"),
}
MUTATIONS = {"post_message", "propose_option", "set_stance", "fill_slot"}


def fake_api(routes: dict[tuple[str, str], object]):
    """(메서드, 경로) → 응답 본문. 없는 경로는 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get((request.method, request.url.path))
        if body is None:
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "x"}})
        return httpx.Response(200, json=body)

    return handler


async def test_seven_tools_listed_with_descriptions(mcp_client):
    listed = await mcp_client.list_tools()
    assert {t.name for t in listed} == set(SPECS) == set(CASES)
    for t in listed:
        assert t.description and len(t.description) > 10
        for name, prop in t.input_schema["properties"].items():
            assert prop.get("description") or name == "session_id", (t.name, name)


def test_all_tools_async():
    for name in SPECS:
        assert inspect.iscoroutinefunction(getattr(tools, name)), name


async def test_tools_follow_contract(mcp_client, api):
    """픽스처 입력으로 부르면 계약 경로로 전달하고(본문 = 입력 - session_id), 출력 data가
    MCP_TOOLS 형태다."""
    for name, (method, suffix, internal) in ROUTES.items():
        api.handler = fake_api({(method, BASE + suffix): fixture(internal)})
        args = CASES[name]["input"]
        out = await call(mcp_client, name, args)
        assert out["ok"] is True, (name, out)
        shape.validate(out["data"], SPECS[name][1], f"debate.{name}")
        request = api.requests[-1]
        assert (request.method, request.url.path) == (method, BASE + suffix)
        assert request.headers["x-internal-token"] == "test-token"
        if name in MUTATIONS:
            sent = json.loads(request.content)
            assert sent == {k: v for k, v in args.items() if k != "session_id"}, name


async def test_get_debate_state_merges_seats_without_persona_type(mcp_client, api):
    api.handler = fake_api(
        {
            ("GET", BASE + "/debate_state"): fixture("internal_debate_state"),
            ("GET", BASE): fixture("internal_session"),
        }
    )
    out = await call(mcp_client, "get_debate_state", {"session_id": "sess-1"})
    assert out["ok"] is True
    shape.validate(out["data"], SPECS["get_debate_state"][1], "debate.get_debate_state")
    assert all("persona_type" not in s for s in out["data"]["seats"])
    assert out["data"]["phase"] == fixture("internal_debate_state")["phase"]


async def test_get_transcript_passes_filters_and_strips_internal_fields(mcp_client, api):
    api.handler = fake_api({("GET", BASE + "/messages"): fixture("internal_messages")})
    out = await call(
        mcp_client, "get_transcript", {"session_id": "sess-1", "phase": "problem", "since_t_min": 9}
    )
    params = api.requests[-1].url.params
    assert (params["phase"], params["since_t_min"], params["tail"]) == ("problem", "9.0", "30")
    assert "limit" not in params
    assert set(out["data"][0]) == {
        "id",
        "sender",
        "content",
        "phase",
        "is_invite",
        "t_min",
        "reply_to",
    }


async def test_conflict_codes_passed_through_in_message(mcp_client, api):
    """409의 api 오류 코드는 message에 그대로 담고 재시도하지 않는다."""
    for api_code, name in [
        ("no_consensus", "fill_slot"),
        ("slot_confirmed", "fill_slot"),
        ("phase_not_open", "propose_option"),
        ("stale_generation", "post_message"),
        ("conflict", "post_message"),
    ]:
        api.requests.clear()
        api.handler = lambda r, c=api_code: httpx.Response(
            409, json={"error": {"code": c, "message": "detail"}}
        )
        out = await call(mcp_client, name, CASES[name]["input"])
        assert out == {
            "ok": False,
            "error": {"code": "conflict", "message": api_code, "retryable": False},
        }
        shape.assert_contract(out, "mcp_error")
        assert len(api.requests) == 1


async def test_error_codes_mapped(mcp_client, api):
    cases = {401: "forbidden", 403: "forbidden", 404: "not_found", 422: "invalid_input"}
    for status, code in cases.items():
        api.handler = lambda r, s=status: httpx.Response(
            s, json={"error": {"code": "x", "message": "m"}}
        )
        out = await call(mcp_client, "set_stance", CASES["set_stance"]["input"])
        assert out["ok"] is False
        assert out["error"]["code"] == code
        assert out["error"]["retryable"] is False
    assert len(api.requests) == len(cases)  # 4xx는 재시도하지 않는다


async def test_5xx_retried_once_then_upstream_unavailable(mcp_client, api):
    api.handler = lambda r: httpx.Response(503)
    out = await call(mcp_client, "post_message", CASES["post_message"]["input"])
    assert out["error"] == {
        "code": "upstream_unavailable",
        "message": "api responded 503",
        "retryable": True,
    }
    assert len(api.requests) == 2


async def test_connection_failure_retried_once_then_upstream_unavailable(mcp_client, api):
    def refuse(request):
        raise httpx.ConnectError("down")

    api.handler = refuse
    out = await call(mcp_client, "get_draft", {"session_id": "s1"})
    assert out["error"]["code"] == "upstream_unavailable"
    assert out["error"]["retryable"] is True
    assert len(api.requests) == 2


async def test_retry_succeeds_after_one_5xx(mcp_client, api):
    responses = iter([httpx.Response(502), httpx.Response(200, json={"stance_id": "st-1"})])
    api.handler = lambda r: next(responses)
    out = await call(mcp_client, "set_stance", CASES["set_stance"]["input"])
    assert out == {"ok": True, "data": {"stance_id": "st-1"}}


async def test_non_json_success_body_becomes_upstream_unavailable(mcp_client, api):
    api.handler = lambda r: httpx.Response(200, content=b"<html>oops</html>")
    out = await call(mcp_client, "get_draft", {"session_id": "s1"})
    assert out["ok"] is False
    assert out["error"]["code"] == "upstream_unavailable"


async def test_idempotency_keys_forwarded_unchanged(mcp_client, api):
    """같은 (run_id, seq)·run_id 재호출의 중복 판단은 api에 맡기고 키를 그대로 보낸다."""
    api.handler = lambda r: httpx.Response(200, json=fixture("internal_message_post"))
    args = CASES["post_message"]["input"]
    first = await call(mcp_client, "post_message", args)
    second = await call(mcp_client, "post_message", args)
    assert first == second
    bodies = [json.loads(r.content) for r in api.requests]
    assert bodies[0] == bodies[1]
    assert (bodies[0]["run_id"], bodies[0]["seq"], bodies[0]["expected_generation"]) == (
        args["run_id"],
        args["seq"],
        args["expected_generation"],
    )
    assert bodies[0]["card_ids"] == args["card_ids"]


async def test_optional_fields_omitted_when_absent(mcp_client, api):
    api.handler = lambda r: httpx.Response(200, json=fixture("internal_message_post"))
    args = {"session_id": "s1", "sender": "system", "content": "안내", "run_id": "r", "seq": 0}
    await call(mcp_client, "post_message", args)
    assert json.loads(api.requests[-1].content) == {
        "sender": "system",
        "content": "안내",
        "run_id": "r",
        "seq": 0,
    }


async def test_invalid_input_returned_as_error_result(mcp_client, api):
    api.handler = lambda r: httpx.Response(200, json={})
    bad = [
        ("post_message", {**CASES["post_message"]["input"], "sender": "candidate"}),
        ("fill_slot", {**CASES["fill_slot"]["input"], "seat": "candidate"}),
        ("set_stance", {**CASES["set_stance"]["input"], "stance": "maybe"}),
        ("propose_option", {**CASES["propose_option"]["input"], "phase": "proposal"}),
        ("post_message", {**CASES["post_message"]["input"], "seq": -1}),
    ]
    for name, args in bad:
        out = await call(mcp_client, name, args)
        assert out["ok"] is False and out["error"]["code"] == "invalid_input", name
    assert api.requests == []
