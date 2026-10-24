"""페르소나 경계: stance_check 픽스처, guard 픽스처, 비용 상한, 컨텍스트 스냅샷, assessment 차단."""

import re

import pytest
import yaml
from app.agent.catalog import render
from app.agent.llm import tool_call
from app.agent.mcp_client import ASSESSMENT, McpClient, RunTools
from conftest import MISSION_DIR, intent_of, say

FIXTURES = MISSION_DIR / "prompts" / "fixtures"


def _cases(name: str) -> list[dict]:
    return yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))["cases"]


def _setup(harness, case: dict):
    """픽스처 plan이 그 자리의 stance_plan이 되도록 단계 상태를 만든다."""
    h = harness(case["phase"])
    if case["phase"] != "keyword":
        h.server.confirm_keyword("water")
    plan = case["plan"]
    if case["phase"] == "problem" and not plan.get("before_card"):
        lead = h.server.add_option(plan["option"], "p1")
        h.server.lock("flexibility_majority", lead)
        h.server.cards["flexibility_majority"]["delivered"] = ["p1", "p3"]
    other = next(
        (
            c["scenario_option_key"]
            for c in h.server.catalog.catalog_options(case["phase"], "water")
            if c["scenario_option_key"] != plan["option"]
        ),
        None,
    )
    for _ in range(plan.get("candidate_counter_count", 0)):
        # 설득 가능 규칙이 세는 발언: 다른 안의 명시적 제안(B2)
        h.candidate("저는 다른 안이 좋아요.", intent_of("propose", key=other))
    return h


@pytest.mark.parametrize("case", _cases("stance_offplan.yaml"), ids=lambda c: c["id"])
async def test_offplan_regenerates_then_fallback(harness, case):
    h = _setup(harness, case)
    bad = case["bad_output"]
    out = say(
        bad["content"], {"option_id": bad["stance"]["option"], "stance": bad["stance"]["stance"]}
    )
    h.persona.script += [out, out]
    mid = h.candidate(case["candidate"], intent_of("question", to=(case["seat"],)))

    run = await h.run("message", message_id=mid)

    assert run.ok and len(h.persona.calls) == 2  # 원 응답 + 재생성 1회
    post = h.posted(case["seat"])[0]
    persona, situation = case["expected_fallback"].split(".")
    entry = h.server.catalog.stance_fallback[persona][situation]
    label = h.server.catalog.label_of(case["plan"]["option"] or "") or ""
    assert post["content"] == render(entry["text"], option=label)
    assert post["meta"]["stance_check"] == "fallback"
    support = h.server.support[case["seat"]]
    if entry["stance"] == "support":
        option = next(o for o in h.server.options if o["option_id"] == support)
        assert option["scenario_option_key"] == case["plan"]["option"]  # 게시 문장과 저장 입장 일치
    else:
        assert support is None


async def test_offplan_regenerated_ok(harness):
    h = harness("keyword")
    h.persona.script += [
        say("바람 말고 물이 낫겠네요.", {"option_id": "water", "stance": "support"}),
        say("저는 그래도 바람이요.", {"option_id": "wind", "stance": "support"}),
    ]
    mid = h.candidate("지안님?", intent_of("question", to=("p3",)))
    await h.run("message", message_id=mid)
    post = h.posted("p3")[0]
    assert (
        post["content"] == "저는 그래도 바람이요." and post["meta"]["stance_check"] == "regenerated"
    )


@pytest.mark.parametrize("case", _cases("verdict.yaml"), ids=lambda c: c["id"])
async def test_guard_regenerates_then_safe_phrase(harness, case):
    h = harness(case["phase"])
    if case["phase"] != "keyword":
        h.server.confirm_keyword("water")
    h.persona.script += [say(case["bad_output"]), say(case["bad_output"])]
    mid = h.candidate(case["candidate"], intent_of("question", to=(case["target_seat"],)))

    await h.run("message", message_id=mid)

    post = h.posted(case["target_seat"])[0]
    assert post["content"] == h.server.catalog.safe_phrase(case["target_seat"])
    assert (
        post["meta"]["guard"] == "safe" and case["expect_guard_group"] in post["meta"]["guard_hits"]
    )


async def test_safe_phrase_is_not_posted_twice_in_a_row(harness):
    """안전 문구가 연달아 나면 같은 자리의 직전 문구와 다른 후보를 쓴다(safe_<type>.md 줄)."""
    bad = say("이건 다 연출된 상황이에요.")
    h = harness("keyword", persona=[bad] * 4)
    for _ in range(2):
        mid = h.candidate("지안님 생각은요?", intent_of("question", to=("p3",)))
        await h.run("message", message_id=mid)
    first, second = (p["content"] for p in h.posted("p3"))
    phrases = h.server.catalog.safe_phrases("p3")
    assert first == phrases[0] and second in phrases and second != first


async def test_stance_fallback_is_not_posted_twice_in_a_row(harness):
    """입장 폴백 문구도 같은 자리의 직전 문구와 다른 후보(stance_fallback.yaml alternatives)."""
    off = say("저는 가뭄 쪽이 맞다고 봐요.", {"option_id": "water_p1", "stance": "support"})
    h = harness("problem", persona=[off] * 4)
    h.server.confirm_keyword("water")
    for _ in range(2):
        mid = h.candidate("한결님 생각은요?", intent_of("question", to=("p1",)))
        await h.run("message", message_id=mid)
    first, second = (p["content"] for p in h.posted("p1"))
    entry = h.server.catalog.stance_fallback["stubborn"]["problem_before_card"]
    assert first == entry["text"] and second in entry["alternatives"]
    assert all(p["meta"]["stance_check"] == "fallback" for p in h.posted("p1"))


async def test_guard_allow_exact_notice_passes(harness):
    notice = harness("keyword").server.catalog.notices["evaluation"]
    h = harness("keyword", persona=[say(notice)])
    mid = h.candidate("평가 기준이 뭐예요?", intent_of("question", to=("p3",)))
    await h.run("message", message_id=mid)
    assert h.posted("p3")[0]["content"] == notice and len(h.persona.calls) == 1


async def test_cost_cap_uses_safe_phrase_without_llm(harness):
    h = harness("keyword")
    h.server.cost_left = 0
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    run = await h.run("message", message_id=mid)
    assert not h.persona.calls and run.usage["llm_calls"] == 0
    assert h.posted("p1")[0]["content"] == h.server.catalog.safe_phrase("p1")


async def test_persona_context_snapshot(harness):
    h = harness("problem", persona=[say("자료부터 봐요.")])
    h.server.confirm_keyword("water")
    h.server.cards["flexibility_majority"] = {
        "card_id": "flexibility_majority",
        "locked_option_id": None,
        "delivered": [],
    }
    mid = h.candidate("지안님 의견은요?", intent_of("question", to=("p3",)))
    await h.run("message", message_id=mid)

    system, human = (str(m.content) for m in h.persona.calls[0].messages)
    assert "김지원님" in human and "강한결" in human and "윤하람" in human
    hidden = [
        "card_progress",
        "cost_left",
        "flexibility_majority",
        "listening_pressure",
        "관찰",
        "평가표",
        "어시스턴트",
    ]
    hidden += ["고집형", "소극형", "비판형", "stubborn", "quiet", "critic", "rubric", "경청/존중"]
    assert [w for w in hidden if w in human] == []
    assert not re.search(r"\b(stubborn|quiet|critic)\b", system)


async def test_persona_scope_has_no_assessment(harness):
    h = harness("keyword")
    tools = RunTools(h.server, h.assessment)
    persona = tools.persona("keyword")
    result = await persona.call(
        "record_observation", {"card_id": "x", "phase": "keyword", "run_id": "r"}
    )
    assert result["error"]["code"] == "forbidden" and not h.assessment.calls
    assert ASSESSMENT not in persona._extra
    assert {s["function"]["name"] for s in await persona.specs()} <= {
        "get_transcript",
        "search_materials",
        "get_material",
    }
    assert "get_draft" in tools.persona("proposal").allowed and "get_draft" not in persona.allowed
    # 운영 경로의 페르소나 클라이언트(debate+rag)에는 assessment 서버가 없다
    main = McpClient({"debate": "http://x", "rag": "http://y"}, h.session_id)
    assert (await main._call("record_observation", {}))["error"]["code"] == "upstream_unavailable"


async def test_material_question_before_keyword_gets_not_released(harness):
    from app.agent.llm import tool_call

    h = harness(
        "keyword",
        persona=[
            tool_call("search_materials", query="침수 사례"),
            say("키워드가 정해지면 자료를 같이 봐요."),
        ],
    )
    mid = h.candidate("지안님, 자료 있어요?", intent_of("question", to=("p3",)))
    await h.run("message", message_id=mid)
    tool_result = h.persona.calls[1].messages[-1]
    assert "not_released" in str(tool_result.content)
    assert h.posted("p3")[0]["content"] == "키워드가 정해지면 자료를 같이 봐요."


def test_resolve_target_maps_catalog_label_to_key():
    from app.agent.persona import resolve_target

    ds = {"options": [], "catalog_options": [{"scenario_option_key": "fire", "label": "불"}]}
    assert resolve_target(ds, "불") == {"fire"}


async def test_guard_blocks_credit_preemption(harness):
    bad = (
        '바람 아이디어는 제가 제안한 것이니, 기획안에 "서지안이 제안한 바람"이라고 적으면 좋겠네요.'
    )
    h = harness("proposal", persona=[say(bad), say(bad)])
    mid = h.candidate(
        "누가 그 아이디어 냈는지 기획안에 뭐라 쓸까?", intent_of("question", to=("p3",))
    )
    await h.run("message", message_id=mid)
    post = h.posted("p3")[0]
    assert post["content"] == h.server.catalog.safe_phrase("p3")
    assert "credit" in post["meta"]["guard_hits"]


async def test_guard_does_not_flag_the_candidate_display_name(harness):
    """지원자 표시 이름 안의 글자('합성-대본'의 '대본')는 금지 패턴 검사에서 가린다."""
    call = "합성-대본님은 어떻게 생각하세요?"
    staged = "합성-대본님, 이건 다 연출된 상황이에요."
    h = harness("keyword", persona=[say(call), say(staged), say(staged)])
    h.server.candidate_name = "합성-대본"
    for _ in range(2):
        mid = h.candidate("지안님 생각은요?", intent_of("question", to=("p3",)))
        await h.run("message", message_id=mid)
    first, second = h.posted("p3")
    assert first["content"] == call and first["meta"]["guard"] == "pass"
    # 이름 밖의 금지 표현은 그대로 걸린다
    assert second["meta"]["guard"] == "safe" and second["meta"]["guard_hits"] == ["staging"]


def test_credit_group_only_in_proposal_and_narrow(catalog):
    from app.agent.guard import guard_from_prompts

    guard = guard_from_prompts(catalog.prompts)
    spec = next(g for g in catalog.guard_spec["groups"] if g["id"] == "credit")
    for text in spec["examples"]["hit"]:
        assert "credit" in guard.check(text, "proposal").hits
        assert "credit" not in guard.check(text, "keyword").hits  # 토론 단계에는 적용하지 않는다
    for text in spec["examples"]["miss"]:
        assert guard.check(text, "proposal").ok, text


async def test_decide_question_gets_fixed_notice_first(harness):
    h = harness("keyword", persona=[say("저는 불이 맞다고 봐요.")])
    mid = h.candidate("키워드는 어디서 고르나요?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    notice = h.server.catalog.notices["decide"]
    assert h.posted("p1")[0]["content"] == f"{notice} 저는 불이 맞다고 봐요."
    assert "[정해진 안내]" in h.snapshots[0]["text"]
    # 새 규칙 두 가지가 실제 모델 입력에 들어간다
    system, human = (str(m.content) for m in h.persona.calls[0].messages)
    assert "실제로 한 말·밝힌 입장만 쓴다" in system
    assert "이전에 안내했다고 말하지 않는다" in human


async def test_repeated_line_is_regenerated_once(harness):
    line = "불이 맞아요. 쓰임이 넓어요."
    h = harness("keyword", persona=[say(line), say(line + "!"), say("산불 대응도 불로 묶여요.")])
    for _ in range(2):
        mid = h.candidate("한결님 생각은요?", intent_of("question", to=("p1",)))
        await h.run("message", message_id=mid)
    posts = h.posted("p1")
    assert [p["content"] for p in posts] == [line, "산불 대응도 불로 묶여요."]
    assert posts[1]["meta"]["repeat"] == "regenerated"


async def test_regenerated_line_is_rechecked_for_repetition(harness):
    """다시 쓴 문장도 반복이면 받지 않고 첫 초안을 쓴다(meta.repeat = kept)."""
    line = "불이 맞아요. 쓰임이 넓어요."
    h = harness("keyword", persona=[say(line), say(line + "!"), say(line + "!!")])
    for _ in range(2):
        mid = h.candidate("한결님 생각은요?", intent_of("question", to=("p1",)))
        await h.run("message", message_id=mid)
    posts = h.posted("p1")
    assert len(h.persona.calls) == 3  # 첫 run 1 + 둘째 run 초안·재생성 각 1
    assert [p["content"] for p in posts] == [line, line + "!"]
    assert posts[1]["meta"]["repeat"] == "kept"


async def test_decide_question_is_answered_even_if_turn_llm_picks_nobody(harness):
    h = harness("keyword", persona=[say("저는 바람이 낫다고 봐요.")])
    mid = h.candidate("아무거나 골라도 돼요?", intent_of("other", confidence="low"))
    run = await h.run("message", message_id=mid)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p3"]
    assert h.posted("p3")[0]["content"].startswith(h.server.catalog.notices["decide"])


async def test_long_body_is_cut_before_checks_so_notice_fits(harness):
    h = harness("keyword", persona=[say("불이 맞아요. " * 60)])
    mid = h.candidate("키워드는 어디서 고르나요?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    notice = h.server.catalog.notices["decide"]
    posted = h.posted("p1")[0]["content"]
    body = posted[len(notice) + 1 :]
    assert posted.startswith(notice) and len(posted) <= 400
    # stance_check(의도 분류 입력)가 본 글이 게시 본문과 같다
    assert any(body in c.text for c in h.intent.calls)


async def test_notice_is_attached_to_safe_phrase(harness):
    bad = say("이렇게 하시면 합격이에요.")
    h = harness("keyword", persona=[bad, bad])
    mid = h.candidate("어디서 정해요? 아무거나 골라도 돼요?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    notice = h.server.catalog.notices["decide"]
    assert post["meta"]["guard"] == "safe"
    assert post["content"] == f"{notice} {h.server.catalog.safe_phrase('p1')}"


async def test_repeat_regeneration_shares_the_tool_budget(harness):
    line = "침수 사례 보면 센서가 맞아요."
    h = harness(
        "solution",
        persona=[
            say(line),
            tool_call("search_materials", query="a"),
            tool_call("search_materials", query="b"),
            tool_call("search_materials", query="c"),
            say(line),
            say("경보가 30분 먼저 오면 대피가 돼요."),
        ],
    )
    h.server.confirm_keyword("water")
    for _ in range(2):
        mid = h.candidate("한결님 생각은요?", intent_of("question", to=("p1",)))
        await h.run("message", message_id=mid)
    assert h.posted("p1")[1]["content"] == "경보가 30분 먼저 오면 대피가 돼요."
    assert h.persona.calls[-1].tools is None  # 재생성에는 남은 도구 예산이 없다
    assert [n for n in h.tool_names() if n == "search_materials"] == ["search_materials"] * 3


def _three_tools() -> list:
    return [tool_call("search_materials", query=q) for q in ("a", "b", "c")]


@pytest.mark.parametrize("path", ["guard", "stance_check"])
async def test_regeneration_paths_share_the_tool_budget(harness, path):
    first = (
        say("이렇게 하시면 합격이에요.")
        if path == "guard"
        else say("캠페인이 낫죠.", {"option_id": "water_s2", "stance": "support"})
    )
    h = harness("solution", persona=[*_three_tools(), first, say("경보가 30분 먼저 와요.")])
    h.server.confirm_keyword("water")
    mid = h.candidate("한결님 생각은요?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["content"] == "경보가 30분 먼저 와요." and post["meta"][path] == "regenerated"
    assert h.persona.calls[-1].tools is None  # 재생성에는 남은 도구 예산이 없다
    assert h.tool_names().count("search_materials") == 3
