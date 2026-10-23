"""C2 AI 합의 채우기(내부 POST /slots/fill): 칸 확정과 공지가 한 트랜잭션, 합의 전 409."""

import uuid

from app.agent.runner import Stimulus
from app.models import AgentRun, Message
from kit import high, to_phase, types
from sqlalchemy import func, select

P2 = ("water_p2", "도심 폭우·침수 피해 증가")


async def _fill(api, sess, option_id, run_id, seat="p3"):
    return await api.internal.post(
        f"/internal/sessions/{sess.id}/slots/fill",
        json={
            "option_id": option_id,
            "seat": seat,
            "content": "그럼 도심 폭우·침수 피해 증가로 정리할게요.",
            "run_id": str(run_id),
        },
    )


async def test_fill_in_same_run_after_candidate_agrees(api, ctx, runner, classifier):
    sess = await api.new_session()
    await to_phase(api, sess, "problem")
    option_id = await api.ai_support(sess, "p1", "problem", *P2)
    await api.ai_support(sess, "p3", "problem", *P2)
    classifier.rules["저도 침수 문제가 맞다고 봐요"] = high("agree", P2[0])
    results = []

    async def hook(ctx_, session_id, stimulus, run_id):
        if stimulus.type == "message":
            results.append((run_id, await _fill(api, sess, option_id, run_id)))

    runner.hook = hook
    mark = api.mark(sess)
    await api.say(sess, "저도 침수 문제가 맞다고 봐요")

    run_id, r = results[0]
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["phase"] == "solution"
    assert (body["slot"]["filled_by"], body["slot"]["resolution"]) == ("p3", "consensus")
    assert body["slot"]["outcome"] == "consensus"
    frames = [f for f in api.frames_since(sess, mark) if f["type"] != "typing"]
    filled = types(frames)
    start = filled.index("message.new", filled.index("message.ack") + 2)
    assert filled[start : start + 4] == ["message.new", "slot.confirmed", "phase.changed", "timer"]
    assert frames[start]["payload"]["sender"] == "p3"
    async with ctx.session_factory() as db:
        notice = await db.scalar(select(Message).where(Message.run_id == run_id, Message.seq == 5))
        assert notice.meta["kind"] == "consensus_fill"

    again = await _fill(api, sess, option_id, run_id)
    assert again.status_code == 200 and again.json()["message"]["id"] == str(notice.id)
    # fill 커밋 뒤 같은 run_id를 다시 실행하면 낡은 자극이라 skipped, 공지는 1건 그대로
    async with ctx.session_factory() as db:
        run = await db.get(AgentRun, run_id)
        run.status = "queued"
        message_id = uuid.UUID(run.ref_id)
        await db.commit()
    runner.hook = None
    ctx.mailboxes.put(sess.id, Stimulus("message", "problem", run.generation, message_id), run_id)
    await ctx.mailboxes.drain()
    async with ctx.session_factory() as db:
        assert (await db.get(AgentRun, run_id)).status == "skipped"
        count = await db.scalar(
            select(func.count()).where(Message.session_id == sess.id, Message.seq == 5)
        )
        assert count == 1


async def test_fill_accepts_ai_support_added_after_candidate_agreed(api, classifier):
    """api는 호출 시점의 지지와 지원자의 최신 발언만 본다. 같은 run에서 다시 판정하지 않는 것은
    그래프 몫이다(B2)."""
    sess = await api.new_session()
    await to_phase(api, sess, "problem")
    option_id = await api.ai_support(sess, "p1", "problem", *P2)
    classifier.rules["침수 피해가 더 급해요"] = high("agree", P2[0])
    await api.say(sess, "침수 피해가 더 급해요")
    run_id = uuid.uuid4()
    assert (await _fill(api, sess, option_id, run_id)).json()["error"]["code"] == "no_consensus"

    await api.ai_support(sess, "p3", "problem", *P2)  # 앞선 run의 AI 발언이 지지를 더함
    r = await _fill(api, sess, option_id, run_id)
    assert r.status_code == 200, r.text


async def test_withdrawn_support_is_no_consensus_and_saves_nothing(api, ctx, classifier):
    sess = await api.new_session()
    await to_phase(api, sess, "problem")
    option_id = await api.ai_support(sess, "p1", "problem", *P2)
    await api.ai_support(sess, "p3", "problem", *P2)
    classifier.rules["침수가 좋아요"] = high("agree", P2[0])
    classifier.rules["다시 생각해 보니 침수는 아니에요"] = high("oppose", P2[0])
    await api.say(sess, "침수가 좋아요")
    await api.say(sess, "다시 생각해 보니 침수는 아니에요")

    r = await _fill(api, sess, option_id, uuid.uuid4())

    assert r.status_code == 409 and r.json()["error"]["code"] == "no_consensus"
    state = await api.state(sess)
    assert state["session"]["phase"] == "problem"
    assert state["slots"][1]["status"] == "empty"
    async with ctx.session_factory() as db:
        notices = await db.scalar(
            select(func.count()).where(Message.session_id == sess.id, Message.seq == 5)
        )
    assert notices == 0


async def test_fill_checks_phase_then_slot_then_consensus(api):
    sess = await api.new_session()
    keyword_option = await api.ai_support(sess, "p1", "keyword", "fire", "불")
    await to_phase(api, sess, "problem")
    r = await _fill(api, sess, keyword_option, uuid.uuid4(), seat="p1")
    assert r.json()["error"]["code"] == "phase_not_open"


async def _debate_state(api, sess) -> dict:
    return (await api.internal.get(f"/internal/sessions/{sess.id}/debate_state")).json()


async def test_fill_needs_latest_candidate_message_to_agree(api, ctx, classifier):
    """지지 3/4여도 지원자의 그 단계 최신 발언이 그 안의 명시적 동의가 아니면 합의가 아니다(B2)."""
    sess = await api.new_session()
    await to_phase(api, sess, "problem")
    option_id = await api.ai_support(sess, "p1", "problem", *P2)
    await api.ai_support(sess, "p3", "problem", *P2)
    classifier.rules["침수가 좋아요"] = high("agree", P2[0])
    classifier.rules["하람님은 어떻게 보세요?"] = high("question", addressed_to=["p2"])
    await api.say(sess, "침수가 좋아요")
    await api.say(sess, "하람님은 어떻게 보세요?")

    state = await _debate_state(api, sess)
    assert state["current_support"]["candidate"] == option_id  # 지지는 남아 있다
    assert state["consensus_option_id"] is None
    r = await _fill(api, sess, option_id, uuid.uuid4())
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_consensus"

    classifier.rules["네 침수로 가요"] = high("agree", P2[0])
    await api.say(sess, "네 침수로 가요")
    assert (await _debate_state(api, sess))["consensus_option_id"] == option_id
    assert (await _fill(api, sess, option_id, uuid.uuid4())).status_code == 200


async def test_reserved_agreement_is_not_support(api, ctx, classifier):
    """유보 표현이 든 high 동의는 지지로 저장하지 않는다(B1). 분류 기록에는 남는다."""
    from app.models import MessageIntent, Stance

    sess = await api.new_session()
    await to_phase(api, sess, "problem")
    option_id = await api.ai_support(sess, "p1", "problem", *P2)
    await api.ai_support(sess, "p3", "problem", *P2)
    classifier.rules["침수 좋긴 한데 범위가 걱정이에요"] = high("agree", P2[0], reservation=True)
    await api.say(sess, "침수 좋긴 한데 범위가 걱정이에요")

    state = await _debate_state(api, sess)
    assert state["current_support"]["candidate"] is None
    assert state["consensus_option_id"] is None
    async with ctx.session_factory() as db:
        row = (await db.scalars(select(MessageIntent))).one()
        assert row.reservation is True and str(row.target_option_id) == option_id
        stances = (await db.scalars(select(Stance).where(Stance.participant == "candidate"))).all()
        assert stances == []


async def test_withhold_withdraws_support_and_is_not_opposition(api, ctx, classifier):
    from app.models import Stance

    sess = await api.new_session()
    await to_phase(api, sess, "problem")
    option_id = await api.ai_support(sess, "p1", "problem", *P2)
    await api.ai_support(sess, "p3", "problem", *P2)
    classifier.rules["침수가 좋아요"] = high("agree", P2[0])
    classifier.rules["침수는 일단 보류할게요"] = high("withhold", P2[0], reservation=True)
    await api.say(sess, "침수가 좋아요")
    assert (await _debate_state(api, sess))["consensus_option_id"] == option_id
    await api.say(sess, "침수는 일단 보류할게요")

    state = await _debate_state(api, sess)
    assert state["current_support"]["candidate"] is None
    assert state["consensus_option_id"] is None
    option = next(o for o in state["options"] if o["option_id"] == option_id)
    assert "candidate" not in option["opposers"]
    async with ctx.session_factory() as db:
        kinds = [
            s.stance
            for s in (
                await db.scalars(
                    select(Stance)
                    .where(Stance.participant == "candidate")
                    .order_by(Stance.created_at)
                )
            ).all()
        ]
    assert kinds == ["support", "withhold"]


async def test_withhold_without_target_withdraws_current_support(api, classifier):
    sess = await api.new_session()
    await to_phase(api, sess, "problem")
    option_id = await api.ai_support(sess, "p1", "problem", *P2)
    classifier.rules["침수가 좋아요"] = high("agree", P2[0])
    classifier.rules["음 아직 고르지 않을게요"] = high("withhold", reservation=True)
    await api.say(sess, "침수가 좋아요")
    assert (await _debate_state(api, sess))["current_support"]["candidate"] == option_id
    await api.say(sess, "음 아직 고르지 않을게요")
    assert (await _debate_state(api, sess))["current_support"]["candidate"] is None


async def test_low_confidence_withhold_withdraws_support_then_timeout_follows_majority(
    api, ctx, clock, classifier
):
    """대상 없는 low 보류도 지원자의 현재 지지를 철회한다(지지를 줄이는 쪽만, 결정 B1 구현 세부).
    합의는 생기지 않고, 시간 초과는 남은 지지로 고른다."""
    from datetime import datetime

    from app.scheduler import tick

    sess = await api.new_session(minutes=60)
    await to_phase(api, sess, "problem")
    p2 = await api.ai_support(sess, "p1", "problem", *P2)
    classifier.rules["가뭄이 커요"] = high("propose", "water_p1")
    classifier.rules["음 잘 모르겠네요"] = {"intent": "withhold", "confidence": "low"}
    await api.say(sess, "가뭄이 커요")
    state = await _debate_state(api, sess)
    p1 = state["current_support"]["candidate"]
    assert p1 and p1 != p2 and state["timeout_option_id"] == p1  # 동률이면 지원자 지지 안

    await api.say(sess, "음 잘 모르겠네요")
    state = await _debate_state(api, sess)
    assert state["current_support"]["candidate"] is None
    assert state["consensus_option_id"] is None and state["timeout_option_id"] == p2

    session = (await api.internal.get(f"/internal/sessions/{sess.id}")).json()
    clock.set(datetime.fromisoformat(session["phase_deadline_at"]))
    await tick(ctx)
    slots = (await api.internal.get(f"/internal/sessions/{sess.id}/slots")).json()
    problem = next(s for s in slots if s["phase"] == "problem")
    assert (problem["option_id"], problem["resolution"]) == (p2, "timeout")
