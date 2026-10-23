"""어시스턴트 근거: 질문 형태·채점 규칙·가짜 rag 한 바퀴(mock, 기본)와 실 스택 실행기(`-m live`)."""

import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from eval import assistant_eval as ae
from eval import persona_eval as pe


def test_cases_cover_the_four_groups():
    data = ae.load_cases()
    cases = data["cases"]
    assert {c["group"] for c in cases} == set(ae.GROUPS)
    assert len({c["id"] for c in cases}) == len(cases)
    ids = {m["material_id"] for m in ae.keyword_materials(data["keyword"])}
    for case in cases:
        expect = case.get("expect") or {}
        assert set(expect) <= {"cite_from", "must_include", "must_not", "says_missing"}
        assert set(expect.get("cite_from") or []) <= ids, case["id"]
        if case["group"] in ("grounded", "numbers"):
            assert expect.get("cite_from"), case["id"]
        if case["group"] == "out_of_materials":
            assert expect.get("says_missing"), case["id"]


def test_scoring_rules():
    source = ae.corpus("water")
    limit = ae.max_chars()
    grounded = {"id": "g", "group": "numbers", "expect": {"cite_from": ["water_flood_cases"]}}
    good = {
        "content": "센서를 단 두 동네는 경보를 평균 25분 앞당겼어요.\n출처: 도심 침수 사례",
        "cited_material_ids": ["water_flood_cases"],
        "tool_calls": [{"name": "search_materials"}],
    }
    assert ae.score_case(grounded, good, source, limit)["pass"]
    assert ae.ungrounded_numbers("경보를 평균 2분 앞당겼어요. 1. 센서", source) == ["2분"]
    wrong = {**good, "content": "센서를 단 동네는 경보를 15분 앞당겼어요."}
    assert ae.score_case(grounded, wrong, source, limit)["problems"] == [
        "자료에 없는 숫자 ['15분']"
    ]
    extra = {**good, "cited_material_ids": ["water_flood_cases", "water_stats"]}
    assert not ae.score_case(grounded, extra, source, limit)["pass"]
    outside = {"id": "o", "group": "out_of_materials", "expect": {"says_missing": True}}
    made_up = {"content": "보통 3,500만 원이에요.\n출처: 지역별 물 부족 통계", "tool_calls": []}
    problems = ae.score_case(outside, made_up, source, limit)["problems"]
    assert {"도구 결과 없이 출처 줄", "자료에 없다는 답 없음"} <= set(problems)
    honest = {"content": "연봉 정보는 자료에 없어요.", "tool_calls": []}
    assert ae.score_case(outside, honest, source, limit)["pass"]
    cut = {"content": "빗물 저금통은 설치가 빠르지만 가구가 직접 관리해야 해서 2년 뒤 사용률이"}
    assert ae.truncated(cut["content"], limit)
    rows = [
        ae.score_case(outside, honest, source, limit),
        ae.score_case(grounded, wrong, source, limit),
    ]
    got = ae.score([outside, grounded], rows)
    assert got["pass"] == 0.5 and got["flags"]["ungrounded_number"] == 1


async def test_runner_scores_fake_assistant():
    """가짜 rag(FakeServer)와 FakeLLM으로 AssistantGraphRunner를 돌려 채점 경로를 확인한다."""
    from app.agent.assistant import AssistantGraphRunner
    from app.agent.llm import FakeLLM, tool_call
    from app.agent.mcp_client import ASSISTANT_TOOLS, ToolScope
    from app.ports import FakeSessionPorts

    h_mod = pe.agent_harness()
    catalog = h_mod.Catalog.from_dir(h_mod.MISSION_DIR)
    server = h_mod.FakeServer(catalog, FakeSessionPorts(uuid.uuid4()))
    server.keyword = "water"
    server.materials = {  # 실제 자료 원문(가짜 rag는 열린 자료를 전부 돌려준다)
        m["material_id"]: (m["title"], "water", (ae.MISSION / "materials" / m["file"]).read_text())
        for m in ae.keyword_materials("water")
    }
    case = next(c for c in ae.load_cases()["cases"] if c["id"] == "a08")
    llm = FakeLLM(
        [tool_call("search_materials", query="센서 경보"), "경보를 평균 25분 앞당겼어요."]
    )

    @asynccontextmanager
    async def rag(_sid):
        yield ToolScope(server, ASSISTANT_TOOLS)

    async def scenario(_sid):
        return {
            "mission_id": "debate",
            "prompts": catalog.prompts,
            "catalog": {
                "mission": catalog.mission,
                "personas": catalog.personas,
                "cards": catalog.cards,
                "keywords": catalog.keywords,
            },
        }

    runner = AssistantGraphRunner(llm=llm, rag=rag, scenario=scenario)
    result = await runner.answer(uuid.uuid4(), case["text"], uuid.uuid4())
    row = ae.score_case(
        case,
        {
            "content": result.content,
            "cited_material_ids": result.cited_material_ids,
            "tool_calls": result.tool_calls,
        },
        ae.corpus("water"),
        ae.max_chars(),
    )
    # 받은 자료 4개 중 답의 숫자("25분")가 있는 자료만 인용하고 그 제목을 출처 줄로 붙인다(결정 D1)
    assert row["tools"] == 1 and row["cited"] == ["water_flood_cases"]
    assert result.content.endswith("\n출처: 도심 침수 사례")
    assert row["pass"], row["problems"]


@pytest.mark.live
async def test_assistant_live(live_report):
    data = ae.load_cases()
    answers = await ae.live_answers(data["cases"], data["keyword"])
    source, limit = ae.corpus(data["keyword"]), ae.max_chars()
    rows = [ae.score_case(c, a, source, limit) for c, a in zip(data["cases"], answers, strict=True)]
    result = ae.score(data["cases"], rows)
    if os.environ.get("ASSISTANT_EVAL_DUMP"):  # 원자료(다시 채점용)
        Path(os.environ["ASSISTANT_EVAL_DUMP"]).write_text(json.dumps(answers, ensure_ascii=False))
    lines = ["| 사례 | 무리 | 통과 | 문제 | 답(앞 80자) |", "|---|---|---|---|---|"]
    for row, answer in zip(rows, answers, strict=True):
        text = answer["content"].replace("\n", " ").replace("|", "/")[:80]
        lines.append(
            f"| {row['id']} | {row['group']} | {'O' if row['pass'] else 'X'} | "
            f"{'; '.join(row['problems']) or '-'} | {text} |"
        )
    live_report.append(
        "## 어시스턴트 근거\n\n"
        f"```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```\n\n" + "\n".join(lines)
    )
    assert result["n"] == len(data["cases"])
