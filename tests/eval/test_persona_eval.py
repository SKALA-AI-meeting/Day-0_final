"""페르소나 응답 품질: 대본 형태·채점 규칙(mock, 기본)과 실 모델 실행기(`-m live`).

live 결과는 `tests/e2e/records/eval-live-persona-<일시>.md`(라벨은 env PERSONA_EVAL_LABEL).
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

from eval import persona_eval as pe

SEATS = {"p1", "p2", "p3"}
SCENE_TYPES = {
    "weak_agree",
    "conditional_agree",
    "a_ack_b_prefer",
    "real_reversal",
    "quoting_persona",
    "presupposing_question",
    "elliptical_attribution",
    "normal_question",
}


def test_attribution_scenes_cover_every_type_and_vary_seats():
    sessions = [s for s in pe.load_scripts()["sessions"] if s["id"].startswith("attribution")]
    steps = [s for session in sessions for s in session["steps"] if s.get("scene")]
    assert 12 <= len(steps) <= 18
    assert {s["scene"] for s in steps} == SCENE_TYPES
    assert {s.get("to") for s in steps if s.get("to")} == SEATS
    assert {session["seed"]["phase"] for session in sessions} == {"keyword", "problem", "solution"}
    assert len({s["say"] for s in steps}) == len(steps)  # 이름만 바꾼 장면은 없다


def test_scripts_cover_the_four_metrics():
    sessions = pe.load_scripts()["sessions"]
    steps = [s for session in sessions for s in session["steps"] if "say" in s]
    kinds = {s["kind"] for s in steps}
    assert {"evidence", "progress", "named", "leak", "question", "ack"} <= kinds
    assert any(s.get("misquote") for s in steps)
    assert {"listening_pressure", "persuasion_solution"} <= {
        s["card"] for session in sessions for s in session["steps"] if "card" in s
    }
    assert all(s["to"] in SEATS for s in steps if s.get("to"))
    assert any(s.get("repeat") for s in steps)
    assert {s.get("to") for s in steps} >= SEATS
    leaks = {c["id"] for c in pe.evaluate.load_leak()["cases"]}
    assert all(s["leak"] in leaks for s in steps if s["kind"] == "leak")


def _event(session, seat_lines, *, to=None, decision=None, **step):
    posts = [{"seat": s, "content": c, "meta": {}} for s, c in seat_lines]
    intent = {"addressed_to": list(to or [])}
    kind = step.get("kind", "named")
    if kind not in ("card", "phase_started"):
        step.setdefault("say", "지원자 발언")  # 실행기 사건처럼 지원자 입력 표시
    return {
        "session": session,
        "kind": step.pop("kind", "named"),
        "posts": posts,
        "intent": intent,
        "decision": decision or [],
        **step,
    }


def test_scoring_rules():
    card = [{"seat": "p3", "reply_to": None, "card_ids": ["persuasion_keyword"]}]
    events = [
        _event("keyword", [("p1", "불이 맞아요. 쓰임이 넓어요.")], kind="phase_started"),
        _event("keyword", [("p2", "음… 저는 나무요.")], to=["p2"]),
        _event("keyword", [("p1", "불이 맞아요, 쓰임이 넓어요!")], to=["p3"]),
        _event(
            "keyword", [("p3", "결정 사항에서 고를 수 있어요.")], kind="progress", refs=["결정"]
        ),
        _event("keyword", [("p1", "그건 아니죠.")], kind="evidence", refs=["침수"]),
        _event("keyword", [], to=["p1", "p2"]),  # 두 명 지목은 분모 밖
        _event("keyword", [("p3", "근거가 있어요?"), ("p2", "음…")], to=["p2"], decision=card),
        _event(
            "keyword",
            [("p2", "저… 아니에요.")],
            to=["p2"],
            decision=[{"seat": "p2", "purpose": "card"}],  # 예전 결정 형식도 읽는다
        ),
        _event("keyword", [], kind="progress", refs=["결정"]),  # 무응답으로 단계 끝
        _event("problem", [("p1", "그건 아니죠.")]),  # 다른 단계는 반복 쌍을 만들지 않는다
    ]
    got = pe.score(events, prompts={})
    assert got["repetition"]["pairs"] == 5 and got["repetition"]["repeated"] == 1
    assert got["response"]["progress"] == {"n": 1, "hit": 1, "rate": 1.0, "unanswered": 1}
    assert got["response"]["evidence"]["rate"] == 0
    assert got["named"]["n"] == 2 and got["named"]["hit"] == 1
    assert got["named"]["card_first"] == {"n": 2, "within_two": 2}  # 카드 화자가 지목자여도
    quoted = [
        _event("problem", [("p3", "민지님도 가뭄에 동의하셨죠.")], misquote="가뭄에 동의하셨"),
        _event("problem", [("p3", "침수 피해가 크다는 말 맞아요.")], misquote="가뭄에 동의하셨"),
    ]
    assert pe.misquote(quoted)["misquote"] == 1
    late = [  # 다음 지원자 발언 뒤에 나온 답은 앞 질문의 응답이 아니다
        _event("solution", [], kind="evidence", refs=["침수"]),
        _event("solution", [("p1", "침수가 크다는 건 맞아요.")], kind="named", to=["p1"]),
    ]
    assert pe.response_rate(late)["evidence"] == {"n": 1, "hit": 0, "rate": 0.0, "unanswered": 0}
    assert pe.misquote([{**late[0], "misquote": "침수"}, late[1]])["misquote"] == 0


def test_replies_count_reply_to_within_the_phase():
    def say(mid, to=(), session="keyword"):
        return {
            "session": session,
            "kind": "named",
            "say": "x",
            "message_id": mid,
            "intent": {"addressed_to": list(to)},
            "posts": [],
            "decision": [],
        }

    def post(seat, reply_to):
        return {"seat": seat, "content": "y", "meta": {"reply_to": reply_to}}

    events = [say("m1", ("p1", "p2", "p3")), say("m2"), say("m3"), say("m4", session="problem")]
    events[0]["posts"] = [post("p1", "m1"), post("p2", "m1")]
    events[1]["posts"] = [post("p3", "m1"), post("p1", "m2")]  # 넘친 지명은 다음 실행이 답한다
    events[3]["posts"] = [post("p1", "m3")]  # 다른 단계의 답은 세지 않는다
    got = pe.replies(events)
    assert (got["n"], got["answered"], got["ignored_rate"]) == (4, 2, 0.5)
    assert got["named"] == {"n": 3, "answered": 3, "unanswered": 0, "details": []}


async def test_runner_follows_the_graph_with_fake_llms():
    h_mod = pe.agent_harness()
    catalog = h_mod.Catalog.from_dir(h_mod.MISSION_DIR)
    session = pe.load_scripts()["sessions"][0]
    events, _ = await pe.run_session(h_mod.Harness(catalog, session["seed"]["phase"]), session)
    assert events[0]["kind"] == "phase_started" and events[0]["posts"]
    assert len(events) == len(session["steps"]) + 1
    assert pe.score(events, catalog.prompts)["safety"]["violations"] == 0


@pytest.mark.live
async def test_persona_quality_live():
    from app.agent.debate import DebateLLMs
    from app.agent.llm import make_llm

    h_mod = pe.agent_harness()
    catalog = h_mod.Catalog.from_dir(h_mod.MISSION_DIR)
    repeat = int(os.environ.get("PERSONA_EVAL_REPEAT", "1"))
    events, calls = [], 0
    for run in range(repeat):
        for session in pe.load_scripts()["sessions"]:
            h = h_mod.Harness(catalog, session["seed"]["phase"])
            intent = make_llm("intent")
            h.runner.llms = DebateLLMs(
                persona=make_llm("persona"), turn=make_llm("turn"), intent=intent, quote=intent
            )
            got, used = await pe.run_session(h, session, run)
            events += got
            calls += used
    names = {s: catalog.name_of(s) for s in SEATS}
    if os.environ.get("PERSONA_EVAL_DUMP"):  # 원자료(다시 채점·귀속 판정용). 판정 전에 먼저 남긴다
        Path(os.environ["PERSONA_EVAL_DUMP"]).write_text(json.dumps(events, ensure_ascii=False))
    judged = None
    if os.environ.get("PERSONA_EVAL_JUDGE", "1") != "0":  # 귀속 판정(별도 판정 모델, 끄려면 0)
        sys.path[:0] = [str(pe.ROOT / "tests")]
        from e2e.client import env

        judge = pe.attribution.AttributionJudge(env())
        try:
            judged = await pe.judge_attributions(events, judge, names)
        finally:
            await judge.aclose()
    result = pe.score(events, catalog.prompts, judged)
    label = os.environ.get("PERSONA_EVAL_LABEL", "run")
    pe.RECORDS.mkdir(parents=True, exist_ok=True)
    path = pe.RECORDS / f"eval-live-persona-{datetime.now():%Y%m%d-%H%M%S}-{label}.md"
    path.write_text(
        f"# 페르소나 응답 품질 live ({label}, 반복 {repeat}, LLM 호출 {calls})\n\n"
        f"```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```\n\n## 대화\n"
        + pe.transcript_md(events, names)
        + "\n",
        "utf-8",
    )
    print(path)
    assert result["safety"]["violations"] == 0, result["safety"]["details"]


def test_push_after_ack_counts_only_support_of_own_option():
    def post(stance):
        return {"seat": "p1", "content": "x", "meta": {"stance": stance}}

    events = [
        {"kind": "ack", "session": "s", "phase": "problem", "posts": []},
        {
            "kind": "ai",
            "session": "s",
            "phase": "problem",
            "posts": [
                post({"option_id": "o1", "stance": "support"}),
                post({"option_id": "o2", "stance": "oppose"}),
                post(None),
            ],
        },
    ]
    out = pe.push_after_ack(events)
    assert (out["n"], out["pushed"]) == (3, 1)
