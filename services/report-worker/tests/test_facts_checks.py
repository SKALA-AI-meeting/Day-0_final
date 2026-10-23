"""평가 사실(결정 A1): 차원별 핵심·보조 장면 계산, 근거별 장면 id 검증과 재요청 문구, 모양 맞추기,
코드 요약 조립(결정 A2). LLM은 FakeLLM."""

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
from report_worker.scenes import build_scenes, facts_block

from tests.conftest import SCENARIOS, load
from tests.test_evaluate import dim, element_outputs, element_rubric, run, set_refs

RECORD = load("session_record")
RUBRIC = load("internal_rubric")
REFS = RefIndex(RECORD, load("internal_contributions"))
FORBIDDEN = load_forbidden(SCENARIOS, "debate")
SCENES = build_scenes(RECORD, RUBRIC)


def check(row: dict) -> str | None:
    return check_dimension(tidy(row), REFS, FORBIDDEN, SCENES[row["dimension_id"]])


def level_row(dimension_id: str, *evidence) -> dict:
    """evidence: "msg:x"(장면 id 없음 = core) 또는 ("msg:x", 장면 id)."""
    items = [{"ref": e[0], "scene": e[1]} if isinstance(e, tuple) else e for e in evidence]
    return {
        "dimension_id": dimension_id,
        "level": 3,
        "evidence": items,
        "rationale": "앵커 3에 맞음.",
        "summary_line": "상대 근거에 답함",
        "confidence": "medium",
    }


def no_record_row(dimension_id: str, reason: str) -> dict:
    return {
        "dimension_id": dimension_id,
        "level": None,
        "no_record_reason": reason,
        "evidence": [],
        "rationale": "판단하지 않음.",
        "confidence": None,
    }


# ---- 장면 ----------------------------------------------------------------------------------


def test_core_scene_follows_card_window_rule_by_order_and_phase():
    listening = SCENES["listen_engage"]
    assert listening.cards == ("listening_pressure",)
    # 카드 첫 발언(m-13)부터 문제 상황 단계 끝까지. 다음 단계 시작 안내(m-20)는 같은 25.4분이어도 밖
    assert listening.core.candidate_ids == ("m-15", "m-17", "m-18")
    assert "m-13" in listening.core.members and "m-20" not in listening.core.members
    assert "m-12" not in listening.core.members  # 카드 첫 발언 전
    # 수용: 문제 상황 칸 결정에서 자른다. 결정 전은 그 단계 메시지까지, 결정 뒤는 다음 단계부터
    assert SCENES["flex_openness"].core.candidate_ids == ("m-15", "m-17", "m-18")
    assert SCENES["flex_acceptance"].core.candidate_ids == ("m-22", "m-24", "m-27")
    assert len(SCENES["flex_decision_respect"].core.candidate_ids) == 6
    assert SCENES["flex_acceptance"].decision.path == "ai_consensus_fill"


def test_rounded_minutes_do_not_move_a_message_across_the_decision():
    record = copy.deepcopy(RECORD)
    for m in record["messages"]:
        if m["id"] == "m-22":
            m["t_min"] = 25.4  # 결정과 같은 분으로 반올림돼도 해결책 단계 메시지는 결정 뒤
    scenes = build_scenes(record, RUBRIC)
    assert "m-22" in scenes["flex_acceptance"].core.candidate_ids
    assert "m-22" not in scenes["flex_openness"].core.candidate_ids


def test_aux_scenes_come_from_rubric_data():
    pers = SCENES["pers_reasoning"]
    assert [s.scene_id for s in pers.aux] == ["problem_stage", "proposal_stage"]
    assert pers.get("problem_stage").candidate_ids == ("m-12", "m-15", "m-17", "m-18")
    accept = SCENES["flex_acceptance"]
    # 키워드 결정 뒤(문제 상황 단계)는 보조 장면, 키워드 단계 메시지는 어느 장면에도 없음
    assert accept.holding("m-15") == ["keyword_decided"]
    assert accept.holding("m-06") == []
    listening = SCENES["listen_engage"]
    assert listening.get("persuasion_cards").candidate_ids == ("m-06", "m-08", "m-24", "m-27")


def test_scene_without_core_cards_is_session_and_undelivered_card_is_missing():
    record = copy.deepcopy(RECORD)
    for o in record["observations"]:
        if o["card_id"] == "listening_pressure":
            o["delivered"] = False
    rubric = copy.deepcopy(RUBRIC)
    for element in rubric["elements"]:
        for d in element["dimensions"]:
            if d["dimension_id"] == "incl_safe_tone":
                d["core_cards"] = []
    scenes = build_scenes(record, rubric)
    candidates = [m["id"] for m in record["messages"] if m["sender"] == "candidate"]
    assert scenes["incl_safe_tone"].session_wide
    assert list(scenes["incl_safe_tone"].core.candidate_ids) == candidates
    assert not scenes["listen_engage"].core.exists


def test_after_decision_core_is_missing_when_slot_not_decided_and_old_copy_has_no_cut():
    record = copy.deepcopy(RECORD)
    record["slots"] = [s for s in record["slots"] if s["phase"] != "problem"]
    scenes = build_scenes(record, RUBRIC)
    assert not scenes["flex_acceptance"].core.exists
    assert len(scenes["flex_openness"].core.candidate_ids) == 6  # 결정이 없으면 카드 창 전체
    old = copy.deepcopy(RUBRIC)  # 이 값이 생기기 전에 복사된 척도
    for element in old["elements"]:
        for d in element["dimensions"]:
            d.pop("decision_timing", None)
            d.pop("aux_scenes", None)
    scenes = build_scenes(RECORD, old)
    assert (
        len(scenes["flex_acceptance"].core.candidate_ids) == 6
        and scenes["flex_acceptance"].aux == ()
    )


def test_facts_block_lists_scenes_per_dimension():
    block = facts_block(element_rubric("flexibility"), SCENES, RECORD)
    assert block["decisions"] == RECORD["facts"]["decisions"]
    row = next(r for r in block["dimensions"] if r["dimension_id"] == "flex_acceptance")
    assert row["decision_cut"] == {
        "timing": "after",
        "phase": "problem",
        "t_min": 25.4,
        "path": "ai_consensus_fill",
    }
    assert [s["id"] for s in row["scenes"]] == ["core", "keyword_decided", "proposal_stage"]
    assert row["scenes"][0]["candidate_messages"] == ["msg:m-22", "msg:m-24", "msg:m-27"]


async def test_prompt_has_facts_block_and_message_facts_once():
    _, _, llm, _ = await run("flexibility", element_outputs()["flexibility"])
    user = llm.calls[0][2]
    assert user.index("[요소 지침]") < user.index("[사실]") < user.index("[세션 기록]")
    record = json.loads(user.split("[세션 기록]\n", 1)[1])
    assert "message_facts" not in record["facts"] and "decisions" not in record["facts"]
    m24 = next(m for m in record["messages"] if m["id"] == "m-24")
    assert m24["reply_to"] == "msg:m-21" and m24["decided_before"] == ["keyword", "problem"]


# ---- 사실 모순 거부 ------------------------------------------------------------------------


def test_no_activity_and_event_not_fired_rejected_when_core_scene_has_them():
    error = check(no_record_row("listen_engage", "no_activity"))
    assert error.startswith("activity_present:") and "msg:m-15" in error
    assert check(no_record_row("listen_engage", "event_not_fired")).startswith("card_fired:")
    assert check(no_record_row("listen_engage", "not_applicable")) is None


def test_peer_message_as_evidence_is_rejected():
    error = check(level_row("listen_engage", "msg:m-15", "msg:m-16"))
    assert error.startswith("peer_evidence:") and "msg:m-16(p1)" in error


def test_evidence_scene_tag_is_checked_against_membership_not_words():
    # 장면 id 없는 근거 = 핵심 장면. 핵심 밖 메시지는 rationale 문구와 상관없이 거부
    row = level_row("listen_engage", "msg:m-15", "msg:m-31")
    row["rationale"] = "보조 장면도 같은 방향."
    error = check(row)
    assert error.startswith("out_of_scene:") and "msg:m-31" in error and "session" in error
    # 그 메시지가 드는 보조 장면 id를 달면 통과
    assert check(level_row("listen_engage", "msg:m-15", ("msg:m-31", "session"))) is None
    # 들지 않는 보조 장면 id는 거부, 이 차원에 없는 id도 거부
    bad = level_row("listen_engage", "msg:m-15", ("msg:m-31", "persuasion_cards"))
    assert check(bad).startswith("out_of_scene:")
    assert check(level_row("listen_engage", ("msg:m-15", "keyword_stage"))).startswith(
        "unknown_scene:"
    )


def test_after_decision_core_rejects_evidence_before_decision():
    error = check(level_row("flex_acceptance", "msg:m-18", "msg:m-22"))
    assert error.startswith("before_decision:")
    assert "문제 상황 칸 결정(25.4분, AI 동료 합의 채움)" in error and "msg:m-22" in error
    # 결정 전 메시지는 보조 장면(키워드 결정 뒤)으로 달 때만 쓸 수 있다
    aux = level_row("flex_acceptance", ("msg:m-18", "keyword_decided"), "msg:m-22")
    assert check(aux) is None
    # 어떤 결정보다도 앞선 키워드 단계 메시지는 어느 장면에도 들지 않는다
    early = level_row("flex_acceptance", ("msg:m-06", "keyword_decided"), "msg:m-22")
    assert check(early).startswith("out_of_scene:")


def test_before_decision_core_excludes_later_messages():
    assert check(level_row("flex_openness", "msg:m-17")) is None
    assert check(level_row("flex_openness", "msg:m-22")).startswith("out_of_scene:")
    assert check(level_row("flex_openness", ("msg:m-22", "solution_stage"))) is None


@pytest.mark.parametrize(
    ("dimension_id", "evidence", "code"),
    [
        ("listen_engage", ["msg:m-15", "msg:m-13"], "peer_evidence"),
        ("listen_engage", ["msg:m-15", "msg:m-31"], "out_of_scene"),
        ("flex_acceptance", ["msg:m-17", "msg:m-22"], "before_decision"),
    ],
)
async def test_fact_contradiction_retries_with_fact_then_evaluate_failed(
    dimension_id, evidence, code
):
    element = "listening" if dimension_id.startswith("listen") else "flexibility"
    bad = copy.deepcopy(element_outputs()[element])
    set_refs(dim(bad, dimension_id), evidence)
    rows, _, llm, usage = await run(element, bad, copy.deepcopy(bad))
    retry = llm.calls[1][2].split("[재요청]", 1)[1]
    assert f"{dimension_id}: {code}: 사실 — " in retry and "[사실]에 맞게" in retry
    assert "  직전 행: " in retry  # 그 차원의 직전 행을 보여 주고 그 문제만 고치게 한다
    assert "[사실: 장면별 지원자 메시지 — core: " in retry
    assert usage["element_retries"] == 1
    assert rows[dimension_id]["no_record_reason"] == "evaluate_failed"
    assert rows[dimension_id]["rationale"].endswith(f"(사유: {code})")


async def test_retry_keeps_rows_valid_in_first_output():
    first = copy.deepcopy(element_outputs()["listening"])
    dim(first, "listen_civility").update(
        level=None, no_record_reason="no_activity", evidence=[], confidence=None
    )
    second = element_outputs()["listening"]
    set_refs(dim(second, "listen_engage"), ["msg:m-13"])  # 재요청에서 새로 깨짐
    rows, _, llm, _ = await run("listening", first, second)
    retry = llm.calls[1][2].split("[재요청]", 1)[1]
    assert "listen_civility: activity_present" in retry
    assert "이 차원에 no_activity·event_not_fired는 쓸 수 없다]" in retry
    assert rows["listen_civility"]["level"] == 4  # 재요청 결과
    assert rows["listen_engage"]["evidence_refs"] == ["msg:m-15", "msg:m-17"]  # 첫 출력
    assert all(r["no_record_reason"] != "evaluate_failed" for r in rows.values())


async def test_core_missing_with_only_core_tagged_evidence_becomes_event_not_fired():
    record = copy.deepcopy(RECORD)
    record["observations"] = [
        o for o in record["observations"] if o["card_id"] != "listening_pressure"
    ]
    out = element_outputs()["listening"]
    rows, _, _, usage = await run("listening", out, record=record)
    assert usage["element_retries"] == 0
    assert rows["listen_engage"]["no_record_reason"] == "event_not_fired"


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
    shaped = tidy({"level": "3", "confidence": "Medium", "evidence": ["msg:m-15"]})
    assert shaped["level"] == 3 and shaped["confidence"] == "medium"
    assert shaped["evidence_refs"] == ["msg:m-15"] and shaped["evidence_scenes"] == {}
    assert tidy({"level": 4.0})["level"] == 4
    # 수준·기록 없음 사유를 한 칸(result)에 낸 출력
    assert (tidy({"result": 3})["level"], tidy({"result": 3})["no_record_reason"]) == (3, None)
    na = tidy({"result": "not_applicable", "confidence": None})
    assert (na["level"], na["no_record_reason"]) == (None, "not_applicable")
    # 수준과 사유가 둘 다 있으면 근거가 없어도 고치지 않는다(형식 오류로 재요청)
    both = {**no_record_row("listen_engage", "not_applicable"), "level": 2}
    assert check(both).startswith("format:")


def test_output_shapes_with_one_meaning_are_read():
    rows = [
        {**level_row("listen_engage", {"ref": "m-15", "scene": "core"})},
        {**level_row("listen_understand", "msg:m-17")},
    ]
    text = "다음과 같습니다.\n" + json.dumps({"dimensions": rows}, ensure_ascii=False) + "\n끝."
    output = fix_refs(parse_json(text), REFS)
    assert output["dimensions"][0]["evidence"][0]["ref"] == "msg:m-15"  # 접두 없는 id
    keyed = {"dimensions": {r["dimension_id"]: r for r in copy.deepcopy(rows)}}
    checked = validate_output(
        fix_refs(keyed, REFS), ["listen_engage", "listen_understand"], REFS, FORBIDDEN, SCENES
    )
    assert checked.errors == {} and set(checked.valid) == {"listen_engage", "listen_understand"}
    # 예전 형식 evidence_refs(문자열)도 핵심 장면 근거로 읽는다
    legacy = {k: v for k, v in level_row("listen_engage", "msg:m-15").items() if k != "evidence"}
    assert check({**legacy, "evidence_refs": ["msg:m-15"]}) is None
