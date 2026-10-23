"""P0 최소 흐름: 지원자 메시지 → 의도 분류 → turn(지명) → respond → guard → stance_check → apply
→ typing.
"""

import uuid

from app.agent import intent
from conftest import say


def _addressed(target: str, to: list[str], kind: str = "oppose") -> dict:
    return {
        "intent": kind,
        "target_option_id": None,
        "scenario_option_key": target,
        "new_option_label": None,
        "confidence": "high",
        "reservation": kind == "oppose",
        "addressed_to": to,
        "excluded": [],
    }


async def test_named_seat_responds_with_typing_and_stance(harness):
    h = harness(
        "keyword",
        persona=[
            say("근데 바람이 더 시급하지 않을까요?", {"option_id": "wind", "stance": "support"})
        ],
    )
    mid = h.server.candidate_says("지안님, 바람은 별로예요. 물이 낫지 않아요?")
    result, used = await intent.classify(
        FakeIntent(_addressed("water", ["p3"], "propose")),
        "지안님, 바람은 별로예요. 물이 낫지 않아요?",
        phase="keyword",
        catalog=h.server.catalog.catalog_options("keyword", None),
        options=[],
        recent=[],
        seats={"p1": "강한결", "p2": "윤하람", "p3": "서지안"},
        message_id=uuid.UUID(mid),
    )
    assert not used and result["addressed_to"] == ["p3"]

    run = await h.run("message", message_id=mid)

    assert run.ok and run.status == "finished"
    assert run.decision["speakers"][0]["seat"] == "p3"
    assert run.decision["llm_used"] is False
    posted = h.posted()
    assert [m["sender"] for m in posted] == ["p3"]
    assert posted[0]["seq"] == 1 and posted[0]["meta"]["guard"] == "pass"
    assert ("p3", "start") in h.ports.typing_sent and h.ports.typing_sent[-1] == ("p3", "stop")
    # 계획 안(바람)은 아직 행이 없으므로 propose_option → set_stance
    names = h.tool_names()
    assert names.index("post_message") < names.index("propose_option") < names.index("set_stance")
    wind = next(o for o in h.server.options if o["scenario_option_key"] == "wind")
    assert h.server.support["p3"] == wind["option_id"]
    assert run.usage["llm_calls"] >= 1 and "cost_usd" in run.usage
    # 페르소나 입력: 지원자 표시 이름, 다른 자리 이름
    assert "김지원님" in h.snapshots[0]["text"] and "강한결" in h.snapshots[0]["text"]


async def test_stale_phase_is_skipped(harness):
    h = harness("keyword")
    mid = h.server.candidate_says("안녕하세요")
    h.server.phase = "problem"
    from app.agent.runner import Stimulus

    run = await h.runner.run(
        h.session_id,
        Stimulus(
            type="message",
            phase="keyword",
            generation=h.ports.generation,
            message_id=uuid.UUID(mid),
        ),
        uuid.uuid4(),
    )
    assert run.status == "skipped" and not h.posted()


async def test_debate_state_unavailable_fails_run(harness):
    h = harness("keyword")
    h.server.fail_tools.add("get_debate_state")
    run = await h.run("phase_started")
    assert not run.ok and run.status == "failed"
    # upstream_unavailable 1회 재시도
    assert h.tool_names().count("get_debate_state") == 2


class FakeIntent:
    """complete_json만 있는 JsonLLM(Protocol 확인용)."""

    def __init__(self, out: dict) -> None:
        self.out = out

    async def complete_json(self, role, system, user, schema, *, timeout, temperature):
        return dict(self.out)
