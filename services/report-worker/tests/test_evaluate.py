"""워커 검증(rubric.md 7.3)·요소 단위 1회 재요청·확신도 clamp·요소 수준 단위 테스트(FakeLLM)."""

import copy

import pytest
from report_worker.evaluate import (
    CAPPED_MARK,
    FAILED_SUMMARY,
    NOT_FIRED_MARK,
    EvalContext,
    RefIndex,
    element_level,
    evaluate_element,
    load_forbidden,
    load_prompts,
    validate_output,
)
from report_worker.llm import empty_usage

from tests.conftest import SCENARIOS, FakeLLM, element_outputs, load

RECORD = load("session_record")
RUBRIC = load("internal_rubric")
CONTRIBUTIONS = load("internal_contributions")
FORBIDDEN = load_forbidden(SCENARIOS, "debate")


def ctx(record=RECORD, rubric=RUBRIC) -> EvalContext:
    return EvalContext(
        record=record,
        rubric=rubric,
        refs=RefIndex(record, CONTRIBUTIONS),
        prompts=load_prompts(SCENARIOS, "debate"),
        forbidden=FORBIDDEN,
    )


def element_rubric(element: str, rubric=RUBRIC) -> dict:
    return next(e for e in rubric["elements"] if e["element"] == element)


def set_refs(row: dict, refs: list) -> None:
    """판단 호출 출력의 근거를 바꾼다. 문자열만 주면 장면 id 없는 근거(핵심 장면)."""
    row["evidence"] = list(refs)


def evidence_refs(row: dict) -> list:
    return [e["ref"] if isinstance(e, dict) else e for e in row["evidence"]]


def tag_scene(output: dict, scene: str) -> None:
    """지원자 메시지 근거마다 장면 id를 단다."""
    for row in output["dimensions"]:
        row["evidence"] = [
            {"ref": e["ref"], "scene": scene} if e["ref"].startswith("msg:") else e
            for e in row["evidence"]
        ]


def dim(output: dict, dimension_id: str) -> dict:
    return next(d for d in output["dimensions"] if d["dimension_id"] == dimension_id)


async def run(element: str, *outputs, record=RECORD, rubric=RUBRIC):
    llm = FakeLLM()
    llm.script[element] = list(outputs)
    usage = empty_usage(llm.model)
    rows, summary = await evaluate_element(
        llm, ctx(record, rubric), element_rubric(element, rubric), usage
    )
    return {r["dimension_id"]: r for r in rows}, summary, llm, usage


def test_fixture_outputs_pass_validation():
    refs = RefIndex(RECORD, CONTRIBUTIONS)
    for element, output in element_outputs().items():
        ids = [d["dimension_id"] for d in element_rubric(element)["dimensions"]]
        checked = validate_output(output, ids, refs, FORBIDDEN)
        assert checked.errors == {}, element
        assert set(checked.valid) == set(ids)


def _no_evidence(o):
    set_refs(dim(o, "listen_understand"), [])


def _level4_one_unit(o):
    set_refs(dim(o, "listen_engage"), ["msg:m-15"])  # 지원자 근거 단위 1개


def _context_only(o):
    set_refs(dim(o, "listen_understand"), ["msg:m-13", "msg:m-16"])  # AI 메시지만


def _other_session(o):
    set_refs(dim(o, "listen_understand"), ["msg:other-session-m-99"])


def _level_and_reason(o):
    dim(o, "listen_understand")["no_record_reason"] = "no_activity"


def _missing(o):
    o["dimensions"] = [d for d in o["dimensions"] if d["dimension_id"] != "listen_understand"]


def _duplicate(o):
    o["dimensions"].append(copy.deepcopy(dim(o, "listen_understand")))


def _forbidden(o):
    dim(o, "listen_understand")["rationale"] = "총점이 높아 추천합니다."


BROKEN = {
    "listen_understand": [
        _no_evidence,
        _context_only,
        _other_session,
        _level_and_reason,
        _missing,
        _duplicate,
        _forbidden,
    ],
}
CASES = [(d, f) for d, fs in BROKEN.items() for f in fs]


@pytest.mark.parametrize(("dimension_id", "breaker"), CASES, ids=[f.__name__ for _, f in CASES])
async def test_invalid_dimension_retries_element_once_then_evaluate_failed(dimension_id, breaker):
    bad = copy.deepcopy(element_outputs()["listening"])
    breaker(bad)
    rows, summary, llm, usage = await run("listening", bad, copy.deepcopy(bad))
    assert len(llm.calls) == 2  # 요소 단위 1회 재요청(요약은 코드가 조립)
    assert "[재요청]" in llm.calls[1][2] and dimension_id in llm.calls[1][2]
    assert usage["element_retries"] == 1
    assert rows[dimension_id]["level"] is None
    assert rows[dimension_id]["no_record_reason"] == "evaluate_failed"
    assert rows[dimension_id]["confidence"] is None
    others = [r for d, r in rows.items() if d != dimension_id]
    assert all(r["no_record_reason"] != "evaluate_failed" for r in others)  # 나머지는 재요청 결과
    assert summary["dimension_count"] == 4 and summary["no_record_count"] == 1
    assert "기록 없음: 이해 확인(판단 보류" in summary["summary"]


async def test_level4_with_one_candidate_unit_is_capped_to_3_medium_not_rejected():
    out = copy.deepcopy(element_outputs()["listening"])
    _level4_one_unit(out)
    row = dim(out, "listen_engage")
    row["level"], row["confidence"] = 4, "high"
    rows, _, llm, usage = await run("listening", out)
    assert len(llm.calls) == 1 and usage["element_retries"] == 0  # 상한 적용(거부 아님)
    capped = rows["listen_engage"]
    assert capped["level"] == 3 and capped["confidence"] == "medium"
    assert capped["rationale"].endswith(CAPPED_MARK)
    assert capped["evidence_refs"] == ["msg:m-15"]


async def test_retry_prompt_names_dimension_and_reject_reason():
    bad = copy.deepcopy(element_outputs()["listening"])
    _no_evidence(bad)
    _, _, llm, _ = await run("listening", bad, copy.deepcopy(bad))
    retry = llm.calls[1][2].split("[재요청]", 1)[1]
    assert "listen_understand: no_candidate_evidence: 지원자 근거 없음" in retry
    assert "no_record_reason" in retry


async def test_failed_row_rationale_has_guidance_and_reason_code():
    bad = copy.deepcopy(element_outputs()["listening"])
    _other_session(bad)
    rows, _, _, _ = await run("listening", bad, copy.deepcopy(bad))
    assert rows["listen_understand"]["rationale"] == (
        "근거가 충분한 판단이 나오지 않아 수준을 매기지 않았습니다. "
        "원문 기록 확인을 권장합니다. (사유: ref_not_in_session)"
    )


async def test_retry_result_replaces_first_output():
    bad = copy.deepcopy(element_outputs()["listening"])
    _no_evidence(bad)
    good = element_outputs()["listening"]
    rows, summary, llm, _ = await run("listening", bad, good)
    assert len(llm.calls) == 2  # 첫 출력·재요청
    assert all(r["no_record_reason"] is None for r in rows.values())
    assert dim(good, "listen_understand")["summary_line"] in summary["summary"]


async def test_valid_output_calls_once_and_summary_is_assembled_from_rows():
    out = element_outputs()["listening"]
    rows, summary, llm, usage = await run("listening", out)
    assert len(llm.calls) == 1 and usage["element_retries"] == 0
    # 수준이 높은 차원부터 각 행의 summary_line(행 순서 유지), 다른 내용 없음
    lines = [dim(out, d)["summary_line"] for d in ("listen_engage", "listen_acknowledge")]
    assert summary["summary"].startswith(f"{lines[0]}. {lines[1]}.")
    assert summary["summary"].endswith(dim(out, "listen_understand")["summary_line"] + ".")
    assert summary["level"] == 4  # (4+3+4+4)/4 = 3.75 → 4


async def test_confidence_over_cap_is_clamped_not_rejected():
    out = copy.deepcopy(element_outputs()["listening"])
    dim(out, "listen_understand")["confidence"] = "high"  # 근거 1개 → 최대 medium
    rows, _, llm, usage = await run("listening", out)
    assert usage["element_retries"] == 0
    assert rows["listen_understand"]["confidence"] == "medium"
    assert rows["listen_understand"]["level"] == 3
    assert rows["listen_understand"]["rationale"] == dim(out, "listen_understand")["rationale"]


async def test_core_card_not_fired_caps_low_and_one_persuasion_card_caps_medium():
    record = copy.deepcopy(RECORD)
    record["observations"] = [
        o for o in record["observations"] if o["card_id"] != "persuasion_solution"
    ]
    record["observations"] = [
        o for o in record["observations"] if o["card_id"] != "listening_pressure"
    ]
    out = copy.deepcopy(element_outputs()["listening"])
    tag_scene(out, "session")  # 보조 장면(세션 전체) 근거로 판단
    listening, _, _, _ = await run("listening", out, record=record)
    assert listening["listen_engage"]["level"] == 4
    assert listening["listen_engage"]["confidence"] == "low"
    persuasion, _, _, _ = await run("persuasion", element_outputs()["persuasion"], record=record)
    # 핵심 1 + 보조 1, 근거 단위 2개: 설득 카드 하나만 발동해 최대 medium
    assert persuasion["pers_reasoning"]["confidence"] == "medium"
    # 지원자 메시지 근거가 모두 보조 장면이면 최대 low
    assert persuasion["pers_convergence"]["confidence"] == "low"


async def test_credit_record_counts_contribution_reference():
    out = copy.deepcopy(element_outputs()["honesty"])
    assert evidence_refs(dim(out, "hon_credit_record")) == [
        "proposal:idea_credit",
        "contribution:opt-pb-p2",
    ]
    rows, _, llm, usage = await run("honesty", out)
    assert usage["element_retries"] == 0 and rows["hon_credit_record"]["level"] == 4
    # 다른 차원에서는 contribution이 개수에 들지 않는다
    set_refs(dim(out, "hon_process_truth"), ["contribution:opt-pb-p2"])
    rows, _, llm, _ = await run("honesty", out, copy.deepcopy(out))
    assert rows["hon_process_truth"]["no_record_reason"] == "evaluate_failed"


async def test_forbidden_summary_line_retries_like_rationale():
    out = copy.deepcopy(element_outputs()["listening"])
    dim(out, "listen_engage")["summary_line"] = "합격 수준으로 응답함"
    rows, summary, llm, usage = await run("listening", out, copy.deepcopy(out))
    assert len(llm.calls) == 2 and usage["element_retries"] == 1
    assert rows["listen_engage"]["no_record_reason"] == "evaluate_failed"
    assert "합격" not in summary["summary"]


async def test_missing_summary_line_falls_back_to_first_rationale_sentence():
    out = copy.deepcopy(element_outputs()["listening"])
    row = dim(out, "listen_engage")
    del row["summary_line"]
    row["rationale"] = "압박마다 상대 근거를 받아 답함. 앵커 4에 맞음."
    _, summary, _, usage = await run("listening", out)
    assert usage["element_retries"] == 0
    assert summary["summary"].startswith("압박마다 상대 근거를 받아 답함.")
    assert "앵커 4" not in summary["summary"]


async def test_unparseable_twice_fails_every_dimension():
    rows, summary, llm, _ = await run("listening", "not json", "{broken")
    assert len(llm.calls) == 2  # 판단 1회 + 재요청 1회
    assert {r["no_record_reason"] for r in rows.values()} == {"evaluate_failed"}
    assert summary["level"] is None and summary["summary"] == FAILED_SUMMARY


def _without_listening_card():
    record = copy.deepcopy(RECORD)
    record["observations"] = [
        o for o in record["observations"] if o["card_id"] != "listening_pressure"
    ]
    return record


async def test_core_card_not_fired_without_support_mark_becomes_event_not_fired():
    out = copy.deepcopy(element_outputs()["listening"])
    rows, _, llm, usage = await run(
        "listening", out, {"summary": "다시 쓴 요약"}, record=_without_listening_card()
    )
    assert usage["element_retries"] == 0  # 거부가 아니라 변환
    engage = rows["listen_engage"]
    assert engage["level"] is None and engage["confidence"] is None
    assert engage["no_record_reason"] == "event_not_fired"
    assert engage["rationale"] == dim(out, "listen_engage")["rationale"] + NOT_FIRED_MARK


def _partial_listening_card():
    record = copy.deepcopy(RECORD)
    for o in record["observations"]:
        if o["card_id"] == "listening_pressure":
            o["delivered"] = False  # 담당 동료 일부만 카드 발언을 전달
    return record


async def test_partially_delivered_core_card_counts_as_not_fired():
    out = copy.deepcopy(element_outputs()["listening"])
    rows, _, _, usage = await run(
        "listening", out, {"summary": "다시 쓴 요약"}, record=_partial_listening_card()
    )
    assert usage["element_retries"] == 0
    engage = rows["listen_engage"]
    assert engage["level"] is None and engage["no_record_reason"] == "event_not_fired"
    assert engage["rationale"] == dim(out, "listen_engage")["rationale"] + NOT_FIRED_MARK


async def test_partially_delivered_core_card_with_aux_scene_evidence_caps_low():
    out = copy.deepcopy(element_outputs()["listening"])
    tag_scene(out, "session")  # 보조 장면(세션 전체) 근거로 판단
    rows, _, _, _ = await run("listening", out, record=_partial_listening_card())
    assert rows["listen_engage"]["level"] == 4
    assert rows["listen_engage"]["confidence"] == "low"


async def test_summary_lists_no_record_rows_by_state_without_llm():
    # 경청: 핵심 카드 미발생 → event_not_fired로 바뀐 행은 요약에서 기록 없음으로만 보인다
    out = element_outputs()["listening"]
    _, summary, llm, _ = await run("listening", out, record=_without_listening_card())
    assert len(llm.calls) == 1
    assert summary["summary"] == (
        "기록 없음: 응답성(이벤트 미발생), 이해 확인(이벤트 미발생), "
        "타당성 인정(이벤트 미발생), 존중하는 반대(이벤트 미발생)."
    )
    assert not any(dim(out, d)["summary_line"] in summary["summary"] for d in ("listen_engage",))


def test_element_level_rounds_half_up_and_null_when_no_record():
    def rows(*levels):
        return [{"level": lv} for lv in levels]

    assert element_level(rows(4, 3, 4)) == 4
    assert element_level(rows(3, 4)) == 4  # 3.5 → 4
    assert element_level(rows(2, 3, None)) == 3  # 2.5 → 3, 기록 없음 제외
    assert element_level(rows(2, 2, 3)) == 2
    assert element_level(rows(None, None)) is None


async def test_numeric_msg_refs_are_fixed_when_session_has_m_prefixed_id():
    candidate = "m-15"  # 경청 장면 구간 안 지원자 메시지
    assert next(m for m in RECORD["messages"] if m["id"] == candidate)["sender"] == "candidate"
    output = copy.deepcopy(element_outputs()["listening"])
    row = output["dimensions"][0]
    row.update(level=3, no_record_reason=None, confidence="medium")
    set_refs(row, [f"msg:{candidate[2:]}"])
    rows, _, llm, usage = await run("listening", output)
    assert usage["element_retries"] == 0  # 재요청 없이 통과
    assert rows[row["dimension_id"]]["evidence_refs"] == [f"msg:{candidate}"]


async def test_fixed_numeric_ref_to_ai_message_does_not_count_as_evidence():
    ai = next(m["id"] for m in RECORD["messages"] if m["sender"] == "p1")
    output = copy.deepcopy(element_outputs()["listening"])
    row = output["dimensions"][0]
    row.update(level=3, no_record_reason=None, confidence="medium")
    set_refs(row, [f"msg:{ai[2:]}"])  # 고쳐져도 AI 메시지라 근거 단위가 아님
    rows, _, llm, _ = await run("listening", output, output)
    assert len(llm.calls) == 2  # 검증 실패 → 요소 재요청 1회
    assert rows[row["dimension_id"]]["no_record_reason"] == "evaluate_failed"


@pytest.mark.live
async def test_live_one_element_has_no_evaluate_failed():
    import os

    from report_worker.config import _load_env_files, evaluator_model
    from report_worker.llm import OpenAILLM

    _load_env_files()
    llm = OpenAILLM(evaluator_model(os.environ), os.environ)
    usage = empty_usage(llm.model)
    rows, _ = await evaluate_element(llm, ctx(), element_rubric("listening"), usage)
    assert not any(r["no_record_reason"] == "evaluate_failed" for r in rows)
    print("live llm_calls", usage["llm_calls"], "retries", usage["element_retries"])


def test_with_refs_exposes_all_four_reference_kinds():
    from report_worker.evaluate import with_refs

    record = {**RECORD, "contributions": CONTRIBUTIONS}
    shown = with_refs(record)
    for m in shown["messages"]:  # 지원자 메시지에만 ref(AI·시스템 메시지는 근거가 아니다)
        assert m.get("ref") == (f"msg:{m['id']}" if m["sender"] == "candidate" else None)
    assert all(s["ref"] == f"slot:{s['phase']}" for s in shown["slots"])
    assert shown["proposal"]["section_refs"]["idea_credit"] == "proposal:idea_credit"
    assert all(
        o["ref"] == f"contribution:{o['option_id']}" for o in shown["contributions"]["options"]
    )
    assert "ref" not in record["messages"][0]  # 원본은 바꾸지 않는다


@pytest.mark.live
async def test_live_honesty_credit_record_cites_idea_credit():
    import os

    from report_worker.config import _load_env_files, evaluator_model
    from report_worker.llm import OpenAILLM

    _load_env_files()
    llm = OpenAILLM(evaluator_model(os.environ), os.environ)
    usage = empty_usage(llm.model)
    record = {**RECORD, "contributions": CONTRIBUTIONS}
    rows, _ = await evaluate_element(llm, ctx(record), element_rubric("honesty"), usage)
    by_id = {r["dimension_id"]: r for r in rows}
    credit = by_id["hon_credit_record"]
    assert credit["no_record_reason"] is None
    assert "proposal:idea_credit" in credit["evidence_refs"]
    print(
        "live llm_calls", usage["llm_calls"], "retries", usage["element_retries"], credit["level"]
    )
