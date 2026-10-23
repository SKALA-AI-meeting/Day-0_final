"""기록 상태(결정 A3: 장면 없음·반응 없음·판단 보류, 선결정)와 지원자 안 채택 규칙(결정 A5).
LLM은 FakeLLM."""

import copy

from report_worker.evaluate import (
    ADOPTED_RATIONALE,
    PREEMPTED_MARK,
    PREEMPTED_RATIONALE,
    RefIndex,
    check_dimension,
    load_forbidden,
    tidy,
)
from report_worker.report import aggregate
from report_worker.scenes import build_scenes, facts_block

from tests.conftest import SCENARIOS, load
from tests.test_evaluate import RECORD, RUBRIC, dim, element_outputs, element_rubric, run, set_refs

FORBIDDEN = load_forbidden(SCENARIOS, "debate")
PROBLEM_CARDS = ["flexibility_majority", "listening_pressure"]


def _decision(record: dict, phase: str, **changes) -> None:
    for row in record["facts"]["decisions"]:
        if row["phase"] == phase:
            row.update(changes)


def preempted_problem() -> dict:
    """지원자가 문제 상황 카드 예정 시각 전에 칸을 확정해 두 카드가 생기지 않은 기록."""
    record = copy.deepcopy(RECORD)
    record["observations"] = [
        o for o in record["observations"] if o["card_id"] not in PROBLEM_CARDS
    ]
    _decision(record, "problem", preempted_cards=PROBLEM_CARDS)
    return record


def adopted_problem(record: dict | None = None) -> dict:
    """문제 상황에서 지원자 안이 채택된 기록(사실 decisions)."""
    record = copy.deepcopy(record or RECORD)
    _decision(record, "problem", candidate_backed=True, candidate_option_adopted=True)
    return record


def no_record(row: dict, reason: str) -> None:
    row.update(level=None, no_record_reason=reason, evidence=[], confidence=None)


def old_copy() -> dict:
    """선결정·상태가 생기기 전에 복사된 척도."""
    rubric = copy.deepcopy(RUBRIC)
    rubric.pop("record_states")
    rubric["no_record_reasons"] = [
        {"code": r["code"], "label": r["label"]}
        for r in rubric["no_record_reasons"]
        if r["code"] != "preempted"
    ]
    return rubric


# ---- 선결정 --------------------------------------------------------------------------------


async def test_preempted_core_card_turns_not_fired_and_no_activity_into_preempted():
    out = copy.deepcopy(element_outputs()["flexibility"])
    no_record(dim(out, "flex_openness"), "event_not_fired")
    no_record(dim(out, "flex_decision_respect"), "no_activity")
    # flex_acceptance는 핵심 장면 근거로 수준을 냄 → 핵심 장면이 없어 변환 → 선결정
    rows, summary, llm, usage = await run("flexibility", out, record=preempted_problem())
    assert len(llm.calls) == 1 and usage["element_retries"] == 0  # 거부가 아니라 사실로 바꿈
    for dimension_id in ("flex_openness", "flex_decision_respect", "flex_acceptance"):
        row = rows[dimension_id]
        assert row["level"] is None and row["confidence"] is None
        assert row["no_record_reason"] == "preempted", dimension_id
        assert row["rationale"].endswith(PREEMPTED_MARK)
    # 상태별 요약: 선결정은 장면 없음, 반응 없음으로 세지 않는다
    assert "장면 없음: 다른 안 재검토(선결정 · 카드 전에 지원자가 칸을 확정)" in summary["summary"]
    assert "반응 없음" not in summary["summary"]


async def test_not_preempted_card_stays_event_not_fired_and_model_preempted_is_checked():
    record = copy.deepcopy(preempted_problem())
    _decision(record, "problem", preempted_cards=[])  # 카드가 생기지 않았지만 선결정은 아님
    out = copy.deepcopy(element_outputs()["flexibility"])
    no_record(dim(out, "flex_openness"), "event_not_fired")
    rows, _, _, _ = await run("flexibility", out, record=record)
    assert rows["flex_openness"]["no_record_reason"] == "event_not_fired"
    # 모델이 낸 preempted는 사실과 맞을 때만 받는다
    row = {
        "dimension_id": "flex_openness",
        "result": "preempted",
        "evidence": [],
        "rationale": "카드 전에 확정함.",
        "confidence": None,
    }
    for source, ok in ((record, False), (preempted_problem(), True)):
        refs = RefIndex(source, load("internal_contributions"))
        scenes = build_scenes(source, RUBRIC)
        error = check_dimension(tidy(row), refs, FORBIDDEN, scenes["flex_openness"])
        assert (error is None) == ok, error


async def test_old_rubric_copy_keeps_event_not_fired_and_one_no_record_group():
    out = copy.deepcopy(element_outputs()["flexibility"])
    no_record(dim(out, "flex_openness"), "event_not_fired")
    rows, summary, _, _ = await run(
        "flexibility", out, record=preempted_problem(), rubric=old_copy()
    )
    assert rows["flex_openness"]["no_record_reason"] == "event_not_fired"
    assert "기록 없음: 다른 안 재검토(이벤트 미발생)" in summary["summary"]


def test_facts_block_marks_preempted_dimensions():
    record = preempted_problem()
    block = facts_block(element_rubric("listening"), build_scenes(record, RUBRIC), record)
    rows = {r["dimension_id"]: r for r in block["dimensions"]}
    assert rows["listen_engage"]["preempted"] is True
    assert rows["listen_engage"]["scenes"][0]["exists"] is False
    assert "선결정" in rows["listen_engage"]["scenes"][0]["label"]
    assert rows["listen_engage"]["fixed_result"] is None


def test_report_cards_mark_preempted_only_for_cards_that_did_not_fire():
    session = {
        "position": {
            "scenario": {
                "cards": [
                    {
                        "card_id": c,
                        "element": "listening",
                        "stage_id": "problem",
                        "report_template": "장면",
                    }
                    for c in [*PROBLEM_CARDS, "persuasion_solution"]
                ]
            }
        }
    }
    record = preempted_problem()
    cards = {c["card_id"]: c for c in aggregate(session, record, {"options": []})["cards"]}
    assert cards["flexibility_majority"]["preempted"] is True
    assert cards["flexibility_majority"]["fired_at"] is None
    assert cards["persuasion_solution"]["preempted"] is False  # 발동한 카드
    assert aggregate(session, RECORD, {"options": []})["cards"][0]["preempted"] is False


# ---- 지원자 안 채택(결정 A5) ----------------------------------------------------------------


async def test_adopted_candidate_option_fixes_only_after_decision_dimensions():
    out = copy.deepcopy(element_outputs()["flexibility"])
    set_refs(dim(out, "flex_acceptance"), ["msg:m-13"])  # 워커가 정하는 차원의 오류는 재요청 안 함
    rows, summary, llm, usage = await run("flexibility", out, record=adopted_problem())
    assert len(llm.calls) == 1 and usage["element_retries"] == 0
    for dimension_id in ("flex_acceptance", "flex_forward"):
        row = rows[dimension_id]
        assert row["level"] is None and row["no_record_reason"] == "not_applicable"
        assert row["rationale"] == ADOPTED_RATIONALE and row["evidence_refs"] == []
    # 결정 전 차원은 카드 장면으로 판단한 값 그대로
    for dimension_id in ("flex_openness", "flex_decision_respect"):
        assert rows[dimension_id]["level"] == dim(out, dimension_id)["level"]
    assert summary["summary"].endswith(
        "장면 없음: 결과 승복(해당 없음), 다음 단계 기여(해당 없음)."
    )


async def test_adopted_and_preempted_after_dimensions_are_preempted():
    record = adopted_problem(preempted_problem())
    rows, _, _, _ = await run("flexibility", element_outputs()["flexibility"], record=record)
    for dimension_id in ("flex_acceptance", "flex_forward"):
        assert rows[dimension_id]["no_record_reason"] == "preempted"
        assert rows[dimension_id]["rationale"] == PREEMPTED_RATIONALE
    rows, _, _, _ = await run(
        "flexibility", element_outputs()["flexibility"], record=record, rubric=old_copy()
    )
    assert rows["flex_acceptance"]["no_record_reason"] == "not_applicable"


def test_facts_block_gives_fixed_result_and_no_after_scene_when_adopted():
    record = adopted_problem()
    scenes = build_scenes(record, RUBRIC)
    block = facts_block(element_rubric("flexibility"), scenes, record)
    rows = {r["dimension_id"]: r for r in block["dimensions"]}
    assert rows["flex_acceptance"]["fixed_result"] == "not_applicable"
    assert rows["flex_acceptance"]["scenes"][0]["exists"] is False
    assert "지원자 안이 채택돼" in rows["flex_acceptance"]["scenes"][0]["label"]
    assert rows["flex_openness"]["fixed_result"] is None
    assert rows["flex_openness"]["scenes"][0]["exists"] is True
    # 시간 초과로 정해진 칸은 사실에서 채택이 아니다(api가 계산) → 결정 뒤 장면 그대로
    plain = {
        r["dimension_id"]: r
        for r in facts_block(element_rubric("flexibility"), build_scenes(RECORD, RUBRIC), RECORD)[
            "dimensions"
        ]
    }
    assert plain["flex_acceptance"]["fixed_result"] is None
    assert plain["flex_acceptance"]["scenes"][0]["exists"] is True
