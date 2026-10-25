"""세션 메일박스(가짜 hub·가짜 runner): 직렬 소비, generation, 낡은 자극, 재실행·복구."""

import asyncio
import uuid
from datetime import timedelta

from app.agent.runner import Stimulus
from app.hub import CANDIDATE_TYPING_TTL
from app.models import AgentRun, MessageIntent, Option
from app.routers.ws import handle_send
from app.scheduler import tick
from kit import high, to_phase
from sqlalchemy import select


async def _runs(ctx, sess, stimulus=None):
    async with ctx.session_factory() as db:
        query = select(AgentRun).where(AgentRun.session_id == sess.id).order_by(AgentRun.queued_at)
        if stimulus:
            query = query.where(AgentRun.stimulus == stimulus)
        return list((await db.scalars(query)).all())


async def test_messages_run_serially_and_every_message_is_classified(api, ctx, runner, classifier):
    sess = await api.new_session()
    classifier.rules["물이요"] = high("propose", "water")
    classifier.rules["불도 괜찮아요"] = high("propose", "fire")
    acks_at_classify = []
    original = classifier.__call__

    async def watching(message, **kwargs):
        acks_at_classify.append(sum(f["type"] == "message.ack" for f in api.frames(sess)))
        return await original(message, **kwargs)

    ctx.classify = watching
    active, peak, release = [0], [0], asyncio.Event()

    async def hook(ctx_, session_id, stimulus, run_id):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await release.wait()
        active[0] -= 1

    runner.hook = hook
    for text in ("물이요", "불도 괜찮아요"):
        await handle_send(ctx, sess.id, {"client_msg_id": text, "content": text})
    await asyncio.sleep(0.05)
    release.set()
    await ctx.mailboxes.drain()

    runs = await _runs(ctx, sess, "message")
    assert [r.generation for r in runs] == [2, 3]  # 각 메시지 저장이 올린 값
    assert peak[0] == 1 and acks_at_classify[0] >= 1
    async with ctx.session_factory() as db:
        keys = set((await db.scalars(select(Option.scenario_option_key))).all())
        assert {"water", "fire"} <= keys  # 낡은 자극도 의도 분류·안 반영은 한다
        assert len((await db.scalars(select(MessageIntent))).all()) == 2


async def test_older_message_is_skipped_when_newer_saved(api, ctx, runner, classifier):
    sess = await api.new_session()
    release = asyncio.Event()

    async def hook(ctx_, session_id, stimulus, run_id):
        if stimulus.type == "phase_started":
            await release.wait()

    runner.hook = hook
    async with ctx.session_factory() as db:
        run = await db.get(AgentRun, uuid.uuid5(sess.id, "phase:keyword"))
        run.status = "queued"
        await db.commit()
    ctx.mailboxes.put(sess.id, Stimulus("phase_started", "keyword", 1), run.run_id)
    await asyncio.sleep(0.02)
    for text in ("첫 번째", "두 번째"):
        await handle_send(ctx, sess.id, {"client_msg_id": text, "content": text})
    release.set()
    await ctx.mailboxes.drain()
    statuses = [r.status for r in await _runs(ctx, sess, "message")]
    assert statuses == ["skipped", "finished"]
    assert len(classifier.calls) == 2


async def test_message_from_previous_phase_uses_its_own_phase_catalog(api, ctx, runner, classifier):
    sess = await api.new_session()
    classifier.rules["나무가 좋아요"] = high("propose", "tree")
    release = asyncio.Event()

    async def hook(ctx_, session_id, stimulus, run_id):
        await release.wait()

    runner.hook = hook
    async with ctx.session_factory() as db:
        run = await db.get(AgentRun, uuid.uuid5(sess.id, "phase:keyword"))
        run.status = "queued"
        await db.commit()
    ctx.mailboxes.put(sess.id, Stimulus("phase_started", "keyword", 1), run.run_id)
    await asyncio.sleep(0.02)
    await handle_send(ctx, sess.id, {"client_msg_id": "m1", "content": "나무가 좋아요"})
    r = await api.public.post(
        f"/sessions/{sess.id}/slots/keyword/confirm",
        json={"option_key": "water"},
        headers=sess.auth,
    )
    assert r.status_code == 200
    release.set()
    await ctx.mailboxes.drain()

    call = next(c for c in classifier.calls if c["message"] == "나무가 좋아요")
    assert call["phase"] == "keyword" and len(call["catalog"]) == 4
    async with ctx.session_factory() as db:
        tree = await db.scalar(select(Option).where(Option.scenario_option_key == "tree"))
        assert tree.phase == "keyword" and tree.proposer == "candidate"
    message_run = (await _runs(ctx, sess, "message"))[0]
    assert message_run.status == "skipped"


async def test_post_with_old_generation_is_stale(api):
    sess = await api.new_session()
    session = (await api.internal.get(f"/internal/sessions/{sess.id}")).json()
    await api.say(sess, "안녕하세요")
    r = await api.internal.post(
        f"/internal/sessions/{sess.id}/messages",
        json={
            "sender": "p1",
            "content": "늦은 말",
            "run_id": str(uuid.uuid4()),
            "seq": 1,
            "expected_generation": session["generation"],
        },
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_generation"


async def test_finished_run_is_not_rerun_and_stale_started_run_is(api, ctx, clock, runner):
    sess = await api.new_session()
    await api.say(sess, "안녕하세요")
    run = (await _runs(ctx, sess, "message"))[0]
    stimulus = Stimulus("message", "keyword", run.generation, uuid.UUID(run.ref_id))
    count = len(runner.calls)
    ctx.mailboxes.put(sess.id, stimulus, run.run_id)
    await ctx.mailboxes.drain()
    assert len(runner.calls) == count

    async with ctx.session_factory() as db:
        row = await db.get(AgentRun, run.run_id)
        row.status, row.started_at = "started", clock.now()
        await db.commit()
    clock.advance(seconds=201)
    await tick(ctx)
    await ctx.mailboxes.drain()
    assert runner.calls[-1][2] == run.run_id
    assert (await _runs(ctx, sess, "message"))[0].status == "finished"


async def test_recover_requeues_queued_and_started_runs_with_same_id(api, ctx, runner):
    sess = await api.new_session()
    await api.say(sess, "안녕하세요")
    run = (await _runs(ctx, sess, "message"))[0]
    async with ctx.session_factory() as db:
        row = await db.get(AgentRun, run.run_id)
        row.status = "started"
        await db.commit()
    assert await ctx.mailboxes.recover() == 1
    await ctx.mailboxes.drain()
    assert runner.calls[-1][2] == run.run_id


async def test_ports_enqueue_makes_new_queued_run(api, ctx, runner):
    sess = await api.new_session()
    await to_phase(api, sess, "problem")
    ports = ctx.mailboxes.ports(sess.id)
    assert ports.get_generation() == 2
    ports.enqueue(Stimulus("card_due", "problem", 2, card_id="listening_pressure"))
    await ctx.mailboxes.drain()
    runs = await _runs(ctx, sess, "card_due")
    assert len(runs) == 1 and runs[0].status == "finished"


async def test_candidate_typing_expires_after_six_seconds(ctx, clock):
    sid = uuid.uuid4()
    ctx.hub.candidate_typing(sid, "start")
    clock.advance(seconds=5)
    assert ctx.hub.is_candidate_typing(sid)
    ctx.hub.candidate_typing(sid, "start")  # 5초마다 갱신
    clock.advance(seconds=5)
    assert ctx.hub.is_candidate_typing(sid)
    clock.advance(seconds=1, microseconds=1)
    assert not ctx.hub.is_candidate_typing(sid)
    assert CANDIDATE_TYPING_TTL == timedelta(seconds=6)


async def test_classifier_failure_is_other_and_low_confidence_adds_no_stance(api, ctx, classifier):
    from app.models import Stance

    sess = await api.new_session()
    classifier.rules["음 물도 괜찮고요"] = {
        "intent": "agree",
        "scenario_option_key": "water",
        "confidence": "low",
    }

    async def broken(message, **kwargs):
        if message == "터지는 메시지":
            raise TimeoutError("intent timeout")
        return await classifier(message, **kwargs)

    ctx.classify = broken
    await api.say(sess, "터지는 메시지")
    await api.say(sess, "음 물도 괜찮고요")
    messages = (await api.internal.get(f"/internal/sessions/{sess.id}/messages")).json()
    intents = [m["intent"] for m in messages if m["sender"] == "candidate"]
    assert intents[0]["intent"] == "other" and intents[0]["confidence"] == "low"
    assert intents[1]["confidence"] == "low"
    async with ctx.session_factory() as db:
        assert (await db.scalars(select(Stance))).all() == []


async def test_three_persona_llm_failures_fail_the_session(api, ctx, runner):
    from app.agent.runner import RunResult

    sess = await api.new_session()

    async def hook(ctx_, session_id, stimulus, run_id):
        return RunResult(ok=False, fallback_used=True, error="persona_llm_unavailable:p1")

    runner.hook = hook
    await api.say(sess, "하나")
    await api.say(sess, "둘")
    session = (await api.internal.get(f"/internal/sessions/{sess.id}")).json()
    assert session["status"] == "in_progress"
    mark = api.mark(sess)
    await api.say(sess, "셋")
    session = (await api.internal.get(f"/internal/sessions/{sess.id}")).json()
    assert session["status"] == "failed" and session["ended_at"]
    ended = [f["payload"] for f in api.frames_since(sess, mark) if f["type"] == "session.ended"]
    assert ended == [{"reason": "failed"}]


async def test_missing_scenario_copy_fails_session_without_running(api, ctx, runner):
    """진행 중 세션의 채용 건 복사본이 없으면 실행하지 않고 즉시 failed(session)로 끝낸다."""
    from app.models import Position

    sess = await api.new_session()
    calls = []

    async def hook(ctx_, session_id, stimulus, run_id):
        calls.append(stimulus.type)

    runner.hook = hook
    async with ctx.session_factory() as db:
        position = await db.get(Position, uuid.UUID(sess.position_id))
        position.scenario = {k: v for k, v in position.scenario.items() if k != "catalog"}
        await db.commit()
    mark = api.mark(sess)
    await api.say(sess, "물이 좋겠어요.")

    session = (await api.internal.get(f"/internal/sessions/{sess.id}")).json()
    assert session["status"] == "failed" and session["ended_at"]
    ended = [f["payload"] for f in api.frames_since(sess, mark) if f["type"] == "session.ended"]
    assert ended == [{"reason": "failed"}] and calls == []
    runs = await _runs(ctx, sess, "message")
    assert runs[-1].status == "failed"


async def test_intent_reservation_and_excluded_are_stored(api, classifier):
    sess = await api.new_session()
    classifier.rules["지안님 말고 하람님은요?"] = high(
        "question", addressed_to=["p2"], excluded=["p3"]
    )
    await api.say(sess, "지안님 말고 하람님은요?")
    messages = (await api.internal.get(f"/internal/sessions/{sess.id}/messages")).json()
    found = next(m["intent"] for m in messages if m["sender"] == "candidate")
    assert found["addressed_to"] == ["p2"] and found["excluded"] == ["p3"]
    assert found["reservation"] is False


async def test_text_message_over_limit_is_rejected(api, ctx):
    """텍스트 발언도 같은 글자 수 상한을 넘으면 저장하지 않고 invalid_input으로 알린다."""
    from app.models import Message
    from app.services.debate import CANDIDATE_MESSAGE_MAX_CHARS

    sess = await api.new_session()
    mark = api.mark(sess)
    await api.say(sess, "가" * (CANDIDATE_MESSAGE_MAX_CHARS + 1))
    frames = api.frames_since(sess, mark)
    assert [f["payload"]["code"] for f in frames if f["type"] == "error"] == ["invalid_input"]
    async with ctx.session_factory() as db:
        mine = (
            await db.scalars(
                select(Message).where(Message.session_id == sess.id, Message.sender == "candidate")
            )
        ).all()
        assert mine == []
