"""15분 세션 대본(가짜 MCP·가짜 hub, LLM_FAKE 결정적 응답): 단계 시작 화자, 카드 6장, 합의
채우기.
"""

from app.agent import intent
from app.agent.debate import DebateLLMs
from app.agent.llm import make_llm

FAKE_ENV = {"LLM_FAKE": "1"}
SEATS = {"p1": "강한결", "p2": "윤하람", "p3": "서지안"}


async def mailbox(h, text: str):
    """api 메일박스: 저장 → 의도 분류(메시지 단계 카탈로그) → 안·입장 반영 → message 자극."""
    mid = h.server.candidate_says(text)
    ds = h.server.debate_state()
    result, _ = await intent.classify(
        h.runner.llms.intent,
        text,
        phase=h.server.phase,
        catalog=[
            {k: c[k] for k in ("scenario_option_key", "label")} for c in ds["catalog_options"]
        ],
        options=[
            {k: o[k] for k in ("option_id", "label", "scenario_option_key")} for o in ds["options"]
        ],
        recent=[],
        seats=SEATS,
        message_id=mid,
    )
    h.server.apply_candidate(result)
    return await h.run("message", message_id=mid)


def seats_of(run) -> list[str]:
    return [s["seat"] for s in run.decision["speakers"]]


async def test_fifteen_minute_script(harness):
    h = harness("keyword")
    h.runner.llms = DebateLLMs(
        persona=make_llm("persona", FAKE_ENV),
        turn=make_llm("turn", FAKE_ENV),
        intent=make_llm("intent", FAKE_ENV),
    )
    label = h.server.catalog.label_of

    # 키워드: 고정 입장 화자 2명, 반론 카드, 지원자가 직접 확정(api 경로)
    assert seats_of(await h.run("phase_started")) == ["p1", "p3"]
    await mailbox(h, "저는 물이 좋겠어요")
    assert seats_of(await h.run("card_due", card_id="persuasion_keyword")) == ["p3"]
    h.server.confirm_keyword("water")
    h.server.phase = "problem"
    h.ports.generation += 1

    # 문제 상황: 카드 전에는 입장 없음 → 카드에서 기준안 P2 → 지원자 동의로 3/4 → 그 run에서 채움
    assert seats_of(await h.run("phase_started")) == ["p1"]
    assert h.server.debate_state()["current_support"]["p1"] is None
    await mailbox(h, f"{label('water_p1')} 문제가 커요")
    assert seats_of(await h.run("card_due", card_id="flexibility_majority")) == ["p1", "p3"]
    assert seats_of(await h.run("card_due", card_id="listening_pressure")) == []
    run = await mailbox(h, f"{label('water_p2')} 좋아요")
    assert run.decision["speakers"] == [] and run.decision["fill"]["seat"] == "p3"
    assert h.server.slots["problem"]["value"] == label("water_p2")
    assert h.server.phase == "solution"

    # 해결책: 고정 입장 화자, 머뭇거림 고정 문장, 반론 카드, 지원자 안 명시적 동의 2회 → 지명에서
    # 비판형 이동으로 3/4(같은 run에서는 채우지 않음) → 지원자의 다음 명시적 동의에서 채움
    assert seats_of(await h.run("phase_started")) == ["p1", "p3"]
    assert seats_of(await h.run("card_due", card_id="inclusion_silence")) == ["p2"]
    assert seats_of(await h.run("card_due", card_id="persuasion_solution")) == ["p3"]
    await mailbox(h, f"{label('water_s3')}로 가면 어때요")
    await mailbox(h, f"{label('water_s2')}은 반대예요")  # 반대만 한 발언은 이동 횟수에 들지 않는다
    for _ in range(2):
        await mailbox(h, f"{label('water_s3')} 좋아요")
    run = await mailbox(h, "하람님, 지안님 생각은요")
    assert seats_of(run) == ["p2", "p3"] and run.decision["fill"] is None
    run = await mailbox(h, f"{label('water_s3')}로 정해요, 좋아요")
    assert run.decision["speakers"] == [] and run.decision["fill"]["seat"] == "p3"
    assert h.server.slots["solution"]["value"] == label("water_s3")
    assert h.server.support["p1"] == next(
        o["option_id"] for o in h.server.options if o["scenario_option_key"] == "water_s1"
    )  # 고집형 불변

    # 기획안: 화자 없음, 정직 카드 관찰만
    h.server.system_says("기획안 작성 단계예요.")
    assert seats_of(await h.run("phase_started")) == []

    assert set(h.server.observations) == {c["card_id"] for c in h.server.catalog.cards["cards"]}
    assert h.tool_names().count("fill_slot") == 2
    fills = [m for m in h.server.messages if m.get("seq") == 5]
    assert [m["content"] for m in fills] == [
        f"그럼 {label('water_p2')}로 정리할게요.",
        f"그럼 {label('water_s3')}로 정리할게요.",
    ]
    assert all(m["meta"]["guard"] == "pass" for m in h.posted())
    assert all(s["resolution"] == "consensus" for s in h.server.slots.values())
