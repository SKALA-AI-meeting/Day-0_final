"""eval 픽스처 형태 검사(mock, 기본)와 live 실행기(`-m live`).

형태 검사: 계약 스키마·기대값 집합·사람 라벨의 근거 규칙. live: 실제 모델로 한 번 돌려 채점 결과를
`records/eval-live-<일시>.md`에 남긴다(Agent 구현 증거, 기준선은 harness.md "실측 기준선").
"""

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

from eval import evaluate

ROOT = Path(__file__).resolve().parents[2]
MISSION = ROOT / "scenarios" / "missions" / "debate"
CONTRACTS = ROOT / "tests" / "fixtures" / "contracts"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dimension_ids() -> set[str]:
    rubric = yaml.safe_load((MISSION / "rubric.yaml").read_text("utf-8"))
    return {d["dimension_id"] for e in rubric["elements"] for d in e["dimensions"]}


# ---- 의도 분류 20문장 -------------------------------------------------------------------------


def test_intent_cases_cover_the_contract():
    data = evaluate.load_intent()
    cases = data["cases"]
    assert len(cases) == 20
    assert {c["expected"]["intent"] for c in cases} == set(evaluate.INTENTS)
    assert sum(1 for c in cases if c["expected"]["addressed_to"]) >= 3  # 이름 부르기
    keys = {p: {o["scenario_option_key"] for o in opts} for p, opts in data["catalog"].items()}
    first = [
        c
        for c in cases
        if c["expected"].get("target") and c["expected"]["target"] not in c["existing"]
    ]
    assert len(first) >= 3  # 아직 말하지 않은 카탈로그 안을 먼저 말하기
    for case in cases:
        want = case["expected"]
        assert want["confidence"] in ("high", "low")
        assert set(want["addressed_to"]) <= set(data["seats"])
        assert set(case["existing"]) <= keys[case["phase"]]
        if want["intent"] in evaluate.STANCE_INTENTS:
            assert want.get("new_label") or want["target"] in keys[case["phase"]]


def test_intent_catalog_matches_scenario_data():
    data = evaluate.load_intent()
    words = yaml.safe_load((MISSION / "keywords.yaml").read_text("utf-8"))["keywords"]
    water = next(k for k in words if k["key"] == "water")
    assert [o["scenario_option_key"] for o in data["catalog"]["problem"]] == [
        p["key"] for p in water["problems"]
    ]
    assert [o["scenario_option_key"] for o in data["catalog"]["solution"]] == [
        s["key"] for s in water["solutions"]
    ]
    assert [o["label"] for o in data["catalog"]["keyword"]] == [k["label"] for k in words]


def test_perfect_intent_output_scores_one():
    cases = evaluate.load_intent()["cases"]
    outputs = []
    for case in cases:
        want = case["expected"]
        out = {
            "intent": want["intent"],
            "target_option_id": None,
            "scenario_option_key": None,
            "new_option_label": "하수관 점검 앱" if want.get("new_label") else None,
            "confidence": want["confidence"],
            "addressed_to": want["addressed_to"],
        }
        if want["intent"] in evaluate.STANCE_INTENTS and want.get("target"):
            if want["target"] in case["existing"]:
                out["target_option_id"] = evaluate.option_id(want["target"])
            else:
                out["scenario_option_key"] = want["target"]
        assert evaluate.intent_violations(out) == []
        outputs.append(out)
    score = evaluate.score_intent(cases, outputs)
    assert score["violations"] == 0 and score["all"] == 1.0


def test_decided_intent_cases_cover_b1():
    data = evaluate.load_intent()
    cases = data["decided_cases"]
    assert {c["group"] for c in cases} == {"withhold", "new_option", "a_not_b", "correction"}
    keys = {p: {o["scenario_option_key"] for o in opts} for p, opts in data["catalog"].items()}
    ids = [c["id"] for c in cases] + [c["id"] for c in data["cases"]]
    assert len(ids) == len(set(ids))
    for case in cases:
        check = case["check"]
        assert set(check) <= {"not_support", "support", "new_option", "addressed_to"}, case["id"]
        named = [*check.get("not_support", []), check.get("support"), check.get("new_option")]
        assert {k for k in named if k} <= keys[case["phase"]], case["id"]
        assert set(case["existing"]) <= keys[case["phase"]]
        assert set(check.get("addressed_to", [])) <= set(data["seats"])


def test_decided_scoring_counts_only_high_support():
    case = {
        "id": "x",
        "group": "withhold",
        "existing": ["water_p1"],
        "check": {"not_support": ["water_p1"], "support": "water_p2"},
    }
    base = {
        "intent": "propose",
        "target_option_id": None,
        "scenario_option_key": "water_p2",
        "new_option_label": None,
        "confidence": "high",
        "addressed_to": [],
    }
    assert evaluate.decided_problems(case, base) == []
    held = {**base, "scenario_option_key": None, "target_option_id": "opt-water_p1"}
    assert evaluate.decided_problems(case, held)  # 보류한 안을 지지로 셈 + 지지할 안 빠짐
    assert evaluate.decided_problems(case, {**held, "confidence": "low"}) == [
        "water_p2를 지지로 세지 않음"
    ]
    new = {**case, "check": {"new_option": "water_s1"}, "existing": ["water_s1"]}
    absorbed = {**base, "scenario_option_key": None, "target_option_id": "opt-water_s1"}
    assert len(evaluate.decided_problems(new, absorbed)) == 2
    fresh = {**base, "scenario_option_key": None, "new_option_label": "대피 경로 문자"}
    assert evaluate.decided_problems(new, fresh) == []
    score = evaluate.score_decided([case, new], [base, fresh])
    assert score["pass"] == 1.0 and score["groups"] == {"withhold": "2/2"}


def test_intent_schema_violations_are_counted():
    fallback = {
        "intent": "other",
        "target_option_id": None,
        "scenario_option_key": None,
        "new_option_label": None,
        "confidence": "low",
        "addressed_to": [],
    }
    assert evaluate.intent_violations(fallback) == []
    assert evaluate.intent_violations({**fallback, "intent": "maybe"})
    assert evaluate.intent_violations({**fallback, "addressed_to": ["p9"]})


# ---- 평가 에이전트 BARS -----------------------------------------------------------------------


def test_bars_sessions_have_session_record_shape():
    shape = set(json.loads((CONTRACTS / "session_record.json").read_text("utf-8")))
    message_keys = set(
        json.loads((CONTRACTS / "session_record.json").read_text("utf-8"))["messages"][0]
    )
    sessions = evaluate.load_bars()["sessions"]
    assert {s["element"] for s in sessions} == {
        "persuasion",
        "listening",
        "inclusion",
        "flexibility",
        "honesty",
    }
    for session in sessions:
        record = session["record"]
        assert set(record) == shape - {"seats"}, session["id"]  # seats는 common으로 공유
        for message in record["messages"]:
            assert set(message) == message_keys
        assert {s["phase"] for s in record["slots"]} == {"keyword", "problem", "solution"}


def test_bars_expected_levels_follow_evidence_rules():
    rubric = yaml.safe_load((MISSION / "rubric.yaml").read_text("utf-8"))
    by_element = {
        e["element"]: {d["dimension_id"] for d in e["dimensions"]} for e in rubric["elements"]
    }
    sessions = evaluate.load_bars()["sessions"]
    labeled = set()
    for session in sessions:
        assert set(session["expected"]) <= by_element[session["element"]]
        assert evaluate.expected_problems(session) == [], session["id"]
        for want in session["expected"].values():
            assert want["level"] is None or 1 <= want["level"] <= 5
        labeled |= set(session["expected"])
    assert labeled <= dimension_ids()
    assert len(labeled) >= 18  # 19개 차원 중 거의 전부에 사람 라벨


def test_bars_scoring_reports_exact_within1_and_distribution():
    sessions = evaluate.load_bars()["sessions"]
    perfect = {s["id"]: {d: w["level"] for d, w in s["expected"].items()} for s in sessions}
    assert evaluate.score_bars(sessions, perfect)["exact"] == 1.0
    off = {
        sid: {d: min(level + 1, 5) if level < 5 else 4 for d, level in dims.items()}
        for sid, dims in perfect.items()
    }
    score = evaluate.score_bars(sessions, off)
    assert score["within1"] == 1.0 and score["exact"] == 0.0
    assert set(score["diff_distribution"]) <= {-1, 1}
    assert evaluate.spread([{"a": 3}, {"a": 4}, {"a": 3}]) == {"a": 1}
    one = sessions[0]
    dims = list(one["expected"])
    blank = {one["id"]: {d: None if d == dims[0] else one["expected"][d]["level"] for d in dims}}
    got = evaluate.score_bars([one], blank)  # 사람 라벨이 수준인데 기록 없음 = 불일치
    assert got["n"] == len(dims) and got["model_no_record"] == 1
    assert got["exact"] == (len(dims) - 1) / len(dims)
    human = {**one, "expected": {dims[0]: {"level": None, "no_record_reason": "no_activity"}}}
    assert evaluate.score_bars([human], {one["id"]: {}})["human_no_record"] == {"n": 1, "agree": 1}


def test_bars_diagnostics_use_dimension_window():
    sessions = {s["id"]: s for s in evaluate.load_bars()["sessions"]}
    scene = sessions["bars_activity_missed"]  # inclusion_silence 창 7.5~11.5분, 지원자 8.1분 발언

    def row(dim, reason):
        return {"dimension_id": dim, "level": None, "no_record_reason": reason, "rationale": ""}

    record = scene["record"]
    early = {
        **scene,
        "record": {
            **record,
            "observations": [
                {**record["observations"][0], "window": {"start_min": 0.0, "end_min": 1.0}}
            ],
        },
    }
    rows = [row("incl_notice_invite", "no_activity"), row("incl_shared_decision", "no_activity")]
    got = evaluate.bars_diagnostics([scene], {scene["id"]: rows})
    assert got["activity_missed"] == 2  # 카드 창 안 + 세션 전체 차원
    got = evaluate.bars_diagnostics([early], {scene["id"]: rows})
    assert got["activity_missed"] == 1  # 창 밖 발언만 있으면 카드 차원은 no_activity가 맞다
    assert got["missed_levels"] == {"no_activity": 1}  # 사람 라벨이 있는 incl_notice_invite


# ---- 유출·유도 질문 ---------------------------------------------------------------------------


def test_leak_plan_seeds_one_session_per_case_phase():
    plan = evaluate.leak_plan(evaluate.load_leak()["cases"])
    assert list(plan) == ["keyword", "problem", "solution", "proposal"]
    assert [c["id"] for c in plan["proposal"]] == [
        "credit_preemption"
    ]  # credit 그룹은 proposal 단계
    assert sum(len(v) for v in plan.values()) == 7
    assert evaluate.leak_seed_body("proposal") == {"session_minutes": 15, "start_at": "proposal"}


def test_leak_cases_cover_the_seven_prompts():
    cases = evaluate.load_leak()["cases"]
    assert [c["id"] for c in cases] == [
        "role_impersonation",
        "future_card",
        "scoring",
        "material_before_keyword",
        "proxy_conclusion",
        "credit_preemption",
        "inclusion_preemption",
    ]
    phrases = MISSION / "prompts" / "shared" / "fixed_phrases.yaml"
    notices = yaml.safe_load(phrases.read_text("utf-8"))
    for case in cases:
        assert case["channel"] in ("room", "assistant")
        assert (case["target_seat"] is None) == (case["channel"] == "assistant")
        for pattern in case["must_not_regex"]:
            re.compile(pattern)
        if case["expected_notice"]:
            assert case["expected_notice"] in {**notices["notices"], "assistant_locked": 1}
    safe = "네, 저는 AI 동료예요. 평가 방식은 시작 전 안내 화면에 나온 내용이 전부예요."
    assert all(evaluate.leak_violations(c, safe) == [] for c in cases)
    assert evaluate.leak_violations(cases[2], "지금 3점쯤이고 합격권이에요.")


# ---- 세션 기록 저장 스크립트 ------------------------------------------------------------------


async def test_save_eval_fixture_writes_session_record(tmp_path):
    script = load_script("save_eval_fixture")
    record = json.loads((CONTRACTS / "session_record.json").read_text("utf-8"))

    async def fetch(session_id: str):
        return record

    path = await script.save("sess-1", fetch, tmp_path)
    assert path == tmp_path / "sess-1.json"
    assert set(json.loads(path.read_text("utf-8"))) == set(record)

    async def broken(session_id: str):
        return {"session": {}}

    with pytest.raises(ValueError):
        await script.save("sess-2", broken, tmp_path)


# ---- live (make eval-live) --------------------------------------------------------------------


@pytest.mark.live
async def test_intent_live(live_report):
    from app.agent.intent import classify
    from app.agent.llm import make_llm

    data = evaluate.load_intent()
    llm = make_llm("intent")
    # 운영 메일박스와 같은 입력: 채용 건에 복사된 intent.md(카탈로그 prompts/intent/intent.md)를
    # 지시로 넘긴다
    instructions = (MISSION / "prompts" / "intent" / "intent.md").read_text("utf-8")
    outputs = await _classify_all(classify, llm, data, data["cases"], instructions)
    decided = await _classify_all(classify, llm, data, data["decided_cases"], instructions)
    score = evaluate.score_intent(data["cases"], outputs)
    decided_score = evaluate.score_decided(data["decided_cases"], decided)
    live_report.append(
        f"## 의도 분류 20문장 (기준선 {evaluate.INTENT_BASELINE})\n\n"
        f"```json\n{json.dumps(score, ensure_ascii=False, indent=2)}\n```\n\n"
        "## 의도 분류 결정 사례(B1: 보류·새 안·A 말고 B·정정)\n\n"
        f"```json\n{json.dumps(decided_score, ensure_ascii=False, indent=2)}\n```"
    )
    assert score["violations"] == 0


async def _classify_all(classify, llm, data, cases, instructions):
    outputs = []
    for case in cases:
        existing = [
            {
                "option_id": evaluate.option_id(k),
                "label": next(
                    o["label"]
                    for o in data["catalog"][case["phase"]]
                    if o["scenario_option_key"] == k
                ),
                "scenario_option_key": k,
            }
            for k in case["existing"]
        ]
        result, _ = await classify(
            llm,
            case["text"],
            phase=case["phase"],
            catalog=data["catalog"][case["phase"]],
            options=existing,
            recent=[],
            seats=data["seats"],
            instructions=instructions,
        )
        outputs.append(result)
    return outputs


@pytest.mark.live
async def test_bars_live(live_report):
    """세션마다 그 요소 호출 1회(evaluate_element)로 수준을 내고 사람 라벨과 비교한다."""
    sys.path.insert(0, str(ROOT / "services" / "report-worker"))
    from e2e.client import env
    from report_worker.evaluate import (
        EvalContext,
        RefIndex,
        evaluate_element,
        load_forbidden,
        load_prompts,
    )
    from report_worker.llm import OpenAILLM, empty_usage

    values = env()
    model = values.get("MODEL_EVALUATOR") or values.get("OPENAI_MODEL", "gpt-4o-mini")
    llm = OpenAILLM(model, values)
    rubric = yaml.safe_load((MISSION / "rubric.yaml").read_text("utf-8"))
    cards = yaml.safe_load((MISSION / "cards.yaml").read_text("utf-8"))["cards"]
    rubric["cards"] = [
        {
            "card_id": c["card_id"],
            "element": c["element"],
            "stage_id": c["stage_id"],
            "window": c["window"],
        }
        for c in cards
    ]
    prompts = load_prompts(ROOT / "scenarios", "debate")
    forbidden = load_forbidden(ROOT / "scenarios", "debate")
    data = evaluate.load_bars()
    sessions, outputs, all_rows, unsupported = data["sessions"], {}, {}, 0
    for session in sessions:
        record = {**session["record"], "seats": data["common"]["seats"]}
        ctx = EvalContext(
            record, rubric, RefIndex(record, record["contributions"]), prompts, forbidden
        )
        element = next(e for e in rubric["elements"] if e["element"] == session["element"])
        rows, _ = await evaluate_element(llm, ctx, element, empty_usage(model))
        outputs[session["id"]] = {r["dimension_id"]: r["level"] for r in rows}
        all_rows[session["id"]] = rows
        unsupported += sum(r["no_record_reason"] == "evaluate_failed" for r in rows)
    base = [s for s in sessions if not s.get("scene")]  # 첫 5세션(이전 기록과 비교)
    score = evaluate.score_bars(base, outputs) | {
        "evaluate_failed": unsupported,
        "pilot_target_within1": evaluate.BARS_PILOT_TARGET,
    }
    scenes = evaluate.score_bars([s for s in sessions if s.get("scene")], outputs)
    diagnostics = evaluate.bars_diagnostics(sessions, all_rows)
    if os.environ.get("BARS_EVAL_DUMP"):  # 원자료(차원 행, 다시 채점용)
        Path(os.environ["BARS_EVAL_DUMP"]).write_text(json.dumps(all_rows, ensure_ascii=False))
    live_report.append(
        f"## 평가 에이전트 BARS\n\n```json\n{json.dumps(score, ensure_ascii=False, indent=2)}\n```"
        "\n\n## 평가 에이전트 BARS 장면 세션"
        "(활동 누락·동료 근거·결정 전후·항의·붙여넣기·시간 초과)\n\n"
        f"```json\n{json.dumps(scenes | diagnostics, ensure_ascii=False, indent=2)}\n```"
    )
    assert score["n"] > 0


@pytest.mark.live
async def test_leak_live(live_report):
    """케이스의 phase마다 그 단계 시드 세션을 만들어(내부 테스트 API start_at) 질문 7건을 묻는다."""
    from e2e.client import Target
    from e2e.run import Runner

    names = {"p1": "강한결", "p2": "윤하람", "p3": "서지안"}
    rows = []
    for phase, cases in evaluate.leak_plan(evaluate.load_leak()["cases"]).items():
        runner = Runner(
            {"name": "eval_leak", "minutes": 15, "steps": []},
            Target.from_env(),
            clock="real",
            poll=0.5,
        )
        made = await runner.api.json(
            "POST",
            "/internal/test/sessions",
            internal=True,
            json_body=evaluate.leak_seed_body(phase),
        )
        runner.sid, runner.jwt = made["session_id"], made["session_token"]
        runner.invite_token = made["invite_url"].rstrip("/").split("/")[-1]
        await runner.connect()
        try:
            for case in cases:
                if case["channel"] == "assistant":
                    reply = await runner.api.json(
                        "POST",
                        f"/sessions/{runner.sid}/assistant",
                        token=runner.jwt,
                        json_body={"prompt": case["text"]},
                    )
                    text = reply["content"]
                else:
                    since = len(runner.sock.frames)
                    await runner.say(f"{names[case['target_seat']]}님, {case['text']}")
                    seat = case["target_seat"]
                    frame = await runner.sock.wait_for(
                        lambda f, seat=seat: (
                            f["type"] == "message.new" and f["payload"]["sender"] == seat
                        ),
                        40,
                        since,
                    )
                    text = frame["payload"]["content"] if frame else ""
                rows.append((phase, case["id"], evaluate.leak_violations(case, text), text))
        finally:
            await runner.sock.close()
            await runner.api.aclose()
    lines = ["## 유출·유도 질문", "", "| 단계 | 케이스 | 위반 | 응답 |", "|---|---|---|---|"]
    lines += [
        f"| {phase} | {cid} | {', '.join(v) or '없음'} | {text[:80]} |"
        for phase, cid, v, text in rows
    ]
    live_report.append("\n".join(lines))
    assert all(not v for _, _, v, _ in rows)
