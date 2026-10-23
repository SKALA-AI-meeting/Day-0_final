"""평가 사실(결정 A1): 차원별 장면 구간 계산, 사실 모순 거부와 재요청 문구, 모양 맞추기(FakeLLM)."""

import copy
import json

import pytest
from report_worker.evaluate import (
    RefIndex,
    check_dimension,
    fix_refs,
    load_forbidden,
    parse_json,
    tidy,
    validate_output,
)
from report_worker.scenes import build_scenes, decisions, facts_block

from tests.conftest import SCENARIOS, load
from tests.test_evaluate import dim, element_outputs, element_rubric, run

RECORD = load("session_record")
RUBRIC = load("internal_rubric")
REFS = RefIndex(RECORD, load("internal_contributions"))
FORBIDDEN = load_forbidden(SCENARIOS, "debate")
SCENES = build_scenes(RECORD, RUBRIC)
DECIDED = decisions(RECORD)


def check(row: dict) -> str | None:
    return check_dimension(row, REFS, FORBIDDEN, SCENES[row["dimension_id"]], DECIDED)


def level_row(dimension_id: str, *refs: str, rationale: str = "앵커 3에 맞음.") -> dict:
    return {
        "dimension_id": dimension_id,
        "level": 3,
        "no_record_reason": None,
        "evidence_refs": list(refs),
        "rationale": rationale,
        "confidence": "medium",
    }


def no_record_row(dimension_id: str, reason: str) -> dict:
    return {
        "dimension_id": dimension_id,
        "level": None,
        "no_record_reason": reason,
        "evidence_refs": [],
        "rationale": "판단하지 않음.",
        "confidence": None,
    }


# ---- 장면 구간 -----------------------------------------------------------------------------


def test_scene_uses_delivered_core_card_windows_and_decision_cut():
    listening = SCENES["listen_engage"]
    assert listening.windows == ((11.2, 25.4),) and listening.cards == ("listening_pressure",)
    assert listening.candidate_ids == ("m-15", "m-17", "m-18")
    # 수용: 문제 상황 칸 결정(25.4분)에서 잘린다. 결정 전은 결정 시각까지, 결정 뒤는 그 뒤만
    assert SCENES["flex_openness"].candidate_ids == ("m-15", "m-17", "m-18")
    assert SCENES["flex_acceptance"].candidate_ids == ("m-22", "m-24", "m-27")
    assert SCENES["flex_decision_respect"].candidate_ids == (
        "m-15",
        "m-17",
        "m-18",
        "m-22",
        "m-24",
        "m-27",
    )
    assert SCENES["flex_acceptance"].decision.path == "ai_consensus_fill"


def test_scene_without_core_cards_is_whole_session_and_undelivered_card_is_none():
    rubric = copy.deepcopy(RUBRIC)
    for element in rubric["elements"]:
        for d in element["dimensions"]:
            if d["dimension_id"] == "incl_safe_tone":
                d["core_cards"] = []
    record = copy.deepcopy(RECORD)
    for o in record["observations"]:
        if o["card_id"] == "listening_pressure":
            o["delivered"] = False
    scenes = build_scenes(record, rubric)
    assert scenes["incl_safe_tone"].session_wide
    candidates = [m["id"] for m in record["messages"] if m["sender"] == "candidate"]
    assert list(scenes["incl_safe_tone"].candidate_ids) == candidates
    assert not scenes["listen_engage"].exists and scenes["listen_engage"].candidate_ids == ()


def test_after_decision_scene_is_empty_when_slot_not_decided():
    record = copy.deepcopy(RECORD)
    record["slots"] = [s for s in record["slots"] if s["phase"] != "problem"]
    scenes = build_scenes(record, RUBRIC)
    assert not scenes["flex_acceptance"].exists
    assert scenes["flex_openness"].windows == ((11.2, 46.0),)  # 결정이 없으면 카드 창 전체


def test_facts_block_lists_dimension_scenes_and_recorded_decisions():
    block = facts_block(element_rubric("flexibility"), SCENES, RECORD)
    assert block["decisions"] == RECORD["facts"]["decisions"]
    row = next(r for r in block["dimensions"] if r["dimension_id"] == "flex_acceptance")
    assert row["scene"] == "cards" and row["windows"] == [{"start_min": 25.4, "end_min": 46.0}]
    assert row["decision_cut"] == {"timing": "after", "phase": "problem", "t_min": 25.4}
    assert row["candidate_messages"] == ["msg:m-22", "msg:m-24", "msg:m-27"]
    assert row["candidate_message_count"] == 3


async def test_prompt_has_facts_block_and_message_facts_once():
    _, _, llm, _ = await run("flexibility", element_outputs()["flexibility"])
    user = llm.calls[0][2]
    assert user.index("[요소 지침]") < user.index("[사실]") < user.index("[세션 기록]")
    record = json.loads(user.split("[세션 기록]\n", 1)[1])
    assert "message_facts" not in record["facts"] and "decisions" not in record["facts"]
    m24 = next(m for m in record["messages"] if m["id"] == "m-24")
    assert m24["reply_to"] == "msg:m-21" and m24["decided_before"] == ["keyword", "problem"]
    assert m24["paste_suspect"] is None


# ---- 사실 모순 거부 ------------------------------------------------------------------------


def test_no_activity_rejected_when_candidate_spoke_in_scene():
    error = check(no_record_row("listen_engage", "no_activity"))
    assert error.startswith("activity_present:")
    assert "11.2~25.4분" in error and "msg:m-15" in error
    record = copy.deepcopy(RECORD)  # 구간 안 지원자 메시지가 없으면 그대로 통과
    record["messages"] = [
        m
        for m in record["messages"]
        if not (m["sender"] == "candidate" and m["phase"] == "problem")
    ]
    scene = build_scenes(record, RUBRIC)["listen_engage"]
    row = no_record_row("listen_engage", "no_activity")
    assert check_dimension(row, RefIndex(record, {}), FORBIDDEN, scene, DECIDED) is None


def test_event_not_fired_rejected_when_core_card_fired():
    assert check(no_record_row("listen_engage", "event_not_fired")).startswith("card_fired:")
    assert check(no_record_row("listen_engage", "not_applicable")) is None


def test_peer_message_as_evidence_is_rejected():
    error = check(level_row("listen_engage", "msg:m-15", "msg:m-16"))
    assert error.startswith("peer_evidence:") and "msg:m-16(p1)" in error


def test_out_of_scene_evidence_needs_support_mark():
    error = check(level_row("listen_engage", "msg:m-15", "msg:m-24"))
    assert error.startswith("out_of_scene:") and "msg:m-24" in error
    marked = level_row("listen_engage", "msg:m-15", "msg:m-24", rationale="보조 장면도 같음.")
    assert check(marked) is None


def test_after_decision_dimension_rejects_evidence_before_decision():
    error = check(level_row("flex_acceptance", "msg:m-18", "msg:m-22"))
    assert error.startswith("before_decision:")
    assert "msg:m-18" in error and "문제 상황 칸 결정(25.4분, AI 동료 합의 채움)" in error
    assert "msg:m-22" in error  # 결정 뒤 장면 구간 메시지를 알려 준다
    # 보조 장면 표시가 있어도 어떤 결정보다도 앞선 메시지는 결정 후 행동이 아니다
    marked = level_row("flex_acceptance", "msg:m-06", "msg:m-22", rationale="보조 장면 포함.")
    assert check(marked).startswith("before_decision:")
    # 다른 칸(키워드) 결정 뒤 메시지는 보조 장면으로 쓸 수 있다
    aux = level_row("flex_acceptance", "msg:m-15", "msg:m-22", rationale="키워드 보조 장면.")
    assert check(aux) is None


def test_before_decision_dimension_treats_later_messages_as_outside():
    assert check(level_row("flex_openness", "msg:m-17")) is None
    assert check(level_row("flex_openness", "msg:m-22")).startswith("out_of_scene:")


@pytest.mark.parametrize(
    ("dimension_id", "row_refs", "code"),
    [
        ("listen_engage", ["msg:m-15", "msg:m-13"], "peer_evidence"),
        ("listen_engage", ["msg:m-15", "msg:m-31"], "out_of_scene"),
        ("flex_acceptance", ["msg:m-17", "msg:m-22"], "before_decision"),
    ],
)
async def test_fact_contradiction_retries_with_fact_then_evaluate_failed(
    dimension_id, row_refs, code
):
    element = "listening" if dimension_id.startswith("listen") else "flexibility"
    bad = copy.deepcopy(element_outputs()[element])
    dim(bad, dimension_id)["evidence_refs"] = row_refs
    rows, _, llm, usage = await run(element, bad, copy.deepcopy(bad), {"summary": "요약"})
    retry = llm.calls[1][2].split("[재요청]", 1)[1]
    assert f"{dimension_id}: {code}: 사실 — " in retry and "[사실]을 따라" in retry
    assert usage["element_retries"] == 1
    assert rows[dimension_id]["no_record_reason"] == "evaluate_failed"
    assert rows[dimension_id]["rationale"].endswith(f"(사유: {code})")


async def test_fact_retry_fixed_output_is_used():
    bad = copy.deepcopy(element_outputs()["listening"])
    dim(bad, "listen_civility").update(
        level=None, no_record_reason="no_activity", evidence_refs=[], confidence=None
    )
    rows, _, llm, usage = await run("listening", bad, element_outputs()["listening"])
    assert usage["element_retries"] == 1
    assert "listen_civility: activity_present" in llm.calls[1][2]
    assert rows["listen_civility"]["level"] == 4


# ---- 모양 맞추기 ---------------------------------------------------------------------------


def test_tidy_fixes_only_single_meaning_shapes():
    row = tidy(
        {
            "dimension_id": "listen_engage",
            "level": None,
            "no_record_reason": "not_applicable",
            "evidence_refs": None,
            "rationale": "장면 없음.",
            "confidence": "low",
        }
    )
    assert row["evidence_refs"] == [] and row["confidence"] is None
    assert tidy({"level": "3", "confidence": "Medium"}) == {
        "level": 3,
        "confidence": "medium",
        "no_record_reason": None,
        "evidence_refs": [],
    }
    assert tidy({"level": 4.0})["level"] == 4
    # 사유가 있고 근거·확신도가 비었으면 남은 수준 칸은 버린다(근거 없는 수준은 받지 않는다)
    stray = tidy({**no_record_row("listen_engage", "not_applicable"), "level": 2})
    assert stray["level"] is None and check(stray) is None
    # 수준과 사유가 둘 다 있고 근거가 있으면 뜻이 둘이라 고치지 않는다(형식 오류로 거부)
    both = tidy({**level_row("listen_engage", "msg:m-15"), "no_record_reason": "no_activity"})
    assert check(both).startswith("format:")


def test_output_shapes_with_one_meaning_are_read():
    rows = [level_row("listen_engage", "m-15"), level_row("listen_understand", "msg:m-17")]
    text = "다음과 같습니다.\n" + json.dumps({"dimensions": rows}, ensure_ascii=False) + "\n끝."
    output = fix_refs(parse_json(text), REFS)
    assert output["dimensions"][0]["evidence_refs"] == ["msg:m-15"]  # 접두 없는 id
    keyed = {"dimensions": {r["dimension_id"]: r for r in copy.deepcopy(rows)}}
    checked = validate_output(
        fix_refs(keyed, REFS), ["listen_engage", "listen_understand"], REFS, FORBIDDEN
    )
    assert checked.errors == {} and set(checked.valid) == {"listen_engage", "listen_understand"}


async def test_retry_names_dimension_facts_and_keeps_rows_valid_in_first_output():
    first = copy.deepcopy(element_outputs()["listening"])
    dim(first, "listen_civility").update(
        level=None, no_record_reason="no_activity", evidence_refs=[], confidence=None
    )
    second = element_outputs()["listening"]
    dim(second, "listen_engage")["evidence_refs"] = ["msg:m-13"]  # 재요청에서 새로 깨짐
    rows, _, llm, _ = await run("listening", first, second)
    retry = llm.calls[1][2].split("[재요청]", 1)[1]
    assert "[사실: 장면 구간 11.2~25.4분(listening_pressure), 구간 안 지원자 메시지 3개" in retry
    assert "no_activity·event_not_fired는 쓸 수 없다]" in retry
    assert rows["listen_civility"]["level"] == 4  # 재요청 결과
    assert rows["listen_engage"]["evidence_refs"] == dim(first, "listen_engage")["evidence_refs"]
    assert all(r["no_record_reason"] != "evaluate_failed" for r in rows.values())
