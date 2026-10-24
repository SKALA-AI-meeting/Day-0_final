"""스케줄러 틱(가짜 시계): 마감 임박 안내, card_due 재enqueue·기준안 잠금·공동 전달, 복구·outbox."""

import asyncio
import uuid
from datetime import datetime, timedelta

from app import scheduler
from app.models import Message, Option, SessionCard
from app.queue import GENERATE
from kit import high, to_phase
from sqlalchemy import select


async def _card(ctx, sess, card_id):
    async with ctx.session_factory() as db:
        return await db.get(SessionCard, (sess.id, card_id))


async def test_time_warning_once_before_deadline(api, ctx, clock):
    sess = await api.new_session(minutes=60)
    clock.advance(minutes=8, seconds=1)  # 키워드 마감 10분, L/30 = 2분 전
    await scheduler.tick(ctx)
    await scheduler.tick(ctx)
    async with ctx.session_factory() as db:
        warnings = (
            await db.scalars(
                select(Message).where(Message.session_id == sess.id, Message.sender == "system")
            )
        ).all()
    kinds = [m.meta.get("kind") for m in warnings]
    assert kinds.count("time_warning") == 1
    assert "남았어요" in next(m.content for m in warnings if m.meta.get("kind") == "time_warning")


async def test_card_due_reenqueued_with_new_run_until_delivered(api, ctx, clock, runner):
    sess = await api.new_session(minutes=60)
    clock.advance(minutes=3, seconds=1)  # persuasion_keyword offset L/20
    await scheduler.tick(ctx)
    await ctx.mailboxes.drain()
    clock.advance(seconds=31)
    await scheduler.tick(ctx)  # 앞 run이 finished여도 미전달이면 다시 넣는다
    await ctx.mailboxes.drain()
    runs = [(s.card_id, r) for _, s, r in runner.calls if s.type == "card_due"]
    assert [c for c, _ in runs] == ["persuasion_keyword", "persuasion_keyword"]
    assert runs[0][1] != runs[1][1]

    await api.post_ai(sess, "p3", "저는 바람이 더 낫다고 봐요", card_ids=["persuasion_keyword"])
    card = await _card(ctx, sess, "persuasion_keyword")
    assert card.delivered_seats == ["p3"]
    await scheduler.tick(ctx)
    await ctx.mailboxes.drain()
    assert sum(1 for _, s, _ in runner.calls if s.type == "card_due") == 2


async def test_card_due_not_duplicated_while_pending(api, ctx, clock, runner):
    await api.new_session(minutes=60)
    release = asyncio.Event()

    async def hold(ctx_, session_id, stimulus, run_id):
        if stimulus.type == "card_due":
            await release.wait()

    runner.hook = hold
    clock.advance(minutes=3, seconds=1)
    await scheduler.tick(ctx)
    await asyncio.sleep(0.05)
    await scheduler.tick(ctx)
    release.set()
    await ctx.mailboxes.drain()
    assert sum(1 for _, s, _ in runner.calls if s.type == "card_due") == 1


async def test_problem_cards_lock_lead_and_joint_message_delivers_both(api, ctx, clock, classifier):
    sess = await api.new_session(minutes=60)
    await to_phase(api, sess, "problem")
    classifier.rules["가뭄이 제일 커요"] = high("propose", "water_p1")
    await api.say(sess, "가뭄이 제일 커요")
    clock.advance(minutes=3, seconds=1)
    await scheduler.tick(ctx)
    await ctx.mailboxes.drain()

    flex = await _card(ctx, sess, "flexibility_majority")
    pressure = await _card(ctx, sess, "listening_pressure")
    async with ctx.session_factory() as db:
        lead = await db.get(Option, flex.locked_option_id)
    assert lead.scenario_option_key == "water_p2" and lead.proposer == "p1"
    assert pressure.locked_option_id == flex.locked_option_id
    state = (await api.internal.get(f"/internal/sessions/{sess.id}/debate_state")).json()
    progress = {c["card_id"]: c["locked_option_id"] for c in state["card_progress"]}
    assert progress["flexibility_majority"] == str(lead.id)  # C5: 그래프는 이 값을 읽는다

    classifier.rules["침수도 괜찮네요"] = high("agree", "water_p2")
    await api.say(sess, "침수도 괜찮네요")
    joint = await api.post_ai(
        sess,
        "p1",
        "침수 피해가 핵심이에요",
        card_ids=["flexibility_majority", "listening_pressure"],
    )
    assert (await _card(ctx, sess, "flexibility_majority")).delivered_seats == ["p1"]
    assert (await _card(ctx, sess, "listening_pressure")).delivered_seats == ["p1"]
    assert (await _card(ctx, sess, "flexibility_majority")).locked_option_id == lead.id

    clock.advance(seconds=31)
    calls = len(ctx.runner.calls)
    await scheduler.tick(ctx)
    await ctx.mailboxes.drain()
    due = [s.card_id for _, s, _ in ctx.runner.calls[calls:] if s.type == "card_due"]
    assert due == ["flexibility_majority"]  # 담당 p3가 남음

    # 압박 횟수 원천: 카드 첫 발언(포함)부터 AI 자리별 발언 수(대화 창과 무관)
    r = await api.internal.post(
        f"/internal/sessions/{sess.id}/observations",
        json={
            "card_id": "listening_pressure",
            "phase": "problem",
            "trigger_message_id": joint["id"],
            "run_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 200, r.text
    clock.advance(seconds=1)
    await api.post_ai(sess, "p1", "그래도 침수가 먼저예요")
    state = (await api.internal.get(f"/internal/sessions/{sess.id}/debate_state")).json()
    row = next(c for c in state["card_progress"] if c["card_id"] == "listening_pressure")
    assert row["trigger_message_id"] == joint["id"]
    assert row["spoken_since_trigger"] == {"p1": 2}


async def test_recover_fills_missed_deadline_and_republishes(api, ctx, clock):
    sess = await api.new_session(minutes=15)
    other = await api.new_session(minutes=15)
    await to_phase(api, other, "proposal")
    ctx.publisher.fail = True
    r = await api.public.post(f"/sessions/{other.id}/proposal/submit", headers=other.auth, json={})
    assert r.status_code == 200 and ctx.publisher.published == []
    ctx.publisher.fail = False
    session = (await api.internal.get(f"/internal/sessions/{sess.id}")).json()
    clock.set(datetime.fromisoformat(session["phase_deadline_at"]) + timedelta(seconds=30))

    await scheduler.recover(ctx)  # api 재기동 직후

    slots = (await api.internal.get(f"/internal/sessions/{sess.id}/slots")).json()
    assert slots[0]["resolution"] == "timeout"
    assert (
        GENERATE,
        {"session_id": str(other.id), "attempt": 1, "eval_round": 1},
    ) in ctx.publisher.published


async def test_card_due_retries_are_spaced_and_capped(api, ctx, clock, runner):
    await api.new_session(minutes=60)
    clock.advance(minutes=3, seconds=1)
    for _ in range(3):
        await scheduler.tick(ctx)
        await ctx.mailboxes.drain()
        await scheduler.tick(ctx)  # 30초가 안 지나면 다시 넣지 않는다
        await ctx.mailboxes.drain()
        clock.advance(seconds=31)
    await scheduler.tick(ctx)  # 3회를 넘으면 더 넣지 않는다
    await ctx.mailboxes.drain()
    assert runner.types().count("card_due") == 3


async def test_candidate_confirm_before_card_time_preempts_the_card(api, ctx, clock, runner):
    """지원자가 카드 예정 시각 전에 칸을 직접 확정하면 확정은 막히지 않고, 그 카드는 이후 발동하지
    않으며 사실 지표에 선결정(`preempted_cards`)으로 남는다(C3·A3)."""
    sess = await api.new_session(minutes=60)
    clock.advance(minutes=1)
    r = await api.confirm(sess, "keyword", value="물", option_key="water")
    assert r.status_code == 200, r.text
    clock.advance(minutes=2, seconds=30)  # 키워드 카드 예정 시각(3분)을 지남
    await scheduler.tick(ctx)
    await ctx.mailboxes.drain()
    assert [s.card_id for _, s, _ in runner.calls if s.type == "card_due"] == []
    assert await _card(ctx, sess, "persuasion_keyword") is None
    facts = (await api.internal.get(f"/internal/sessions/{sess.id}/facts")).json()
    keyword = next(d for d in facts["decisions"] if d["phase"] == "keyword")
    assert keyword["path"] == "candidate_confirm"
    assert keyword["preempted_cards"] == ["persuasion_keyword"]
