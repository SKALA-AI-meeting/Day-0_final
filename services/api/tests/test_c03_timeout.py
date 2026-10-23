"""C3 시간 초과: 스케줄러가 그래프 없이 api에서 결정적으로 채운다. 선택 규칙을 칸에 기록하고 그
규칙의 문장을 시스템 메시지로 알린다(결정 B3·B4)."""

from datetime import datetime, timedelta

from app.models import Message, Option, Slot
from app.scheduler import tick
from kit import high, to_phase, types
from sqlalchemy import select

P1 = ("water_p1", "가뭄으로 생활용수 제한이 잦아짐")
P2 = ("water_p2", "도심 폭우·침수 피해 증가")


async def _deadline(api, sess) -> datetime:
    session = (await api.internal.get(f"/internal/sessions/{sess.id}")).json()
    return datetime.fromisoformat(session["phase_deadline_at"])


async def _slot(api, sess, phase: str) -> dict:
    slots = (await api.internal.get(f"/internal/sessions/{sess.id}/slots")).json()
    return next(s for s in slots if s["phase"] == phase)


async def test_timeout_fills_candidate_supported_option_with_system_notice(
    api, ctx, clock, runner, classifier
):
    """지지자가 더 많은 안이 있어도 지원자가 지지하는 안으로 채우고, 페르소나 자리가 아니라
    시스템 메시지로 지원자 지지 규칙의 문장을 게시한다."""
    sess = await api.new_session(minutes=60)
    await to_phase(api, sess, "problem")
    await api.ai_support(sess, "p1", "problem", *P2)
    await api.ai_support(sess, "p3", "problem", *P2)
    classifier.rules["가뭄 문제가 커요"] = high("propose", P1[0])
    await api.say(sess, "가뭄 문제가 커요")
    deadline = await _deadline(api, sess)

    clock.set(deadline - timedelta(seconds=10))
    mark = api.mark(sess)
    await tick(ctx)
    await ctx.mailboxes.drain()
    assert "slot.confirmed" not in types(api.frames_since(sess, mark))

    clock.set(deadline)
    calls = len(runner.calls)
    mark = api.mark(sess)
    await tick(ctx)
    await ctx.mailboxes.drain()

    frames = api.frames_since(sess, mark)
    assert types(frames)[:3] == ["message.new", "slot.confirmed", "phase.changed"]
    notice, slot = frames[0]["payload"], frames[1]["payload"]
    assert notice["sender"] == "system"
    assert notice["content"] == (
        f"시간 안에 정하지 못해 김지원님이 지지한 {P1[1]}으로 다음 단계로 넘어가요."
    )
    assert (slot["value"], slot["resolution"], slot["filled_by"], slot["outcome"]) == (
        P1[1],
        "timeout",
        "system",
        "timeout",
    )
    assert frames[2]["payload"]["reason"] == "timeout"
    assert [s.type for _, s, _ in runner.calls[calls:]] == ["phase_started"]

    stored = await _slot(api, sess, "problem")
    assert stored["timeout_rule"] == "candidate_support"
    async with ctx.session_factory() as db:
        row = await db.get(Slot, (sess.id, "problem"))
        assert (row.filled_by, row.timeout_rule) == ("system", "candidate_support")
        rows = (
            await db.scalars(
                select(Message).where(Message.session_id == sess.id, Message.phase == "problem")
            )
        ).all()
        message = next(m for m in rows if (m.meta or {}).get("kind") == "timeout_fill")
        assert message.sender == "system"
        assert message.meta == {"kind": "timeout_fill", "timeout_rule": "candidate_support"}


async def test_timeout_withheld_candidate_falls_to_most_support(api, ctx, clock, classifier):
    """지원자가 지지를 보류로 철회하면 최다 지지 규칙으로 고르고 그 규칙의 문장을 쓴다."""
    sess = await api.new_session(minutes=60)
    await to_phase(api, sess, "problem")
    await api.ai_support(sess, "p1", "problem", *P2)
    await api.ai_support(sess, "p3", "problem", *P2)
    classifier.rules["가뭄 문제가 커요"] = high("propose", P1[0])
    classifier.rules["가뭄은 일단 보류할게요"] = high("withhold", P1[0], reservation=True)
    await api.say(sess, "가뭄 문제가 커요")
    await api.say(sess, "가뭄은 일단 보류할게요")
    clock.set(await _deadline(api, sess))
    mark = api.mark(sess)
    await tick(ctx)

    notice = api.frames_since(sess, mark)[0]["payload"]
    assert (notice["sender"], notice["content"]) == (
        "system",
        f"시간 안에 정하지 못해 지금 지지가 가장 많은 {P2[1]}로 다음 단계로 넘어가요.",
    )
    stored = await _slot(api, sess, "problem")
    assert (stored["value"], stored["filled_by"], stored["timeout_rule"]) == (
        P2[1],
        "system",
        "most_support",
    )


async def test_timeout_tie_takes_earliest_option(api, ctx, clock):
    """지지자 최다가 동률이면 먼저 제안된 안이다."""
    sess = await api.new_session(minutes=60)
    await to_phase(api, sess, "problem")
    await api.ai_support(sess, "p3", "problem", *P2)
    clock.advance(seconds=5)
    await api.ai_support(sess, "p1", "problem", *P1)
    clock.set(await _deadline(api, sess))
    mark = api.mark(sess)
    await tick(ctx)

    notice = api.frames_since(sess, mark)[0]["payload"]
    assert notice["sender"] == "system" and f"먼저 나온 {P2[1]}로" in notice["content"]
    stored = await _slot(api, sess, "problem")
    assert (stored["value"], stored["timeout_rule"]) == (P2[1], "earliest")


async def test_timeout_without_options_uses_default_keyword(api, ctx, clock):
    """나온 안이 없으면 기준안 행을 system 제안으로 만들고 기준안 규칙을 기록한다."""
    sess = await api.new_session(minutes=15)
    clock.set(await _deadline(api, sess))
    mark = api.mark(sess)
    await tick(ctx)

    notice = api.frames_since(sess, mark)[0]["payload"]
    assert (notice["sender"], notice["content"]) == (
        "system",
        "시간 안에 정하지 못했고 나온 안이 없어 이 단계의 기본 안인 물로 다음 단계로 넘어가요.",
    )
    keyword = await _slot(api, sess, "keyword")
    assert (keyword["value"], keyword["resolution"], keyword["filled_by"]) == (
        "물",
        "timeout",
        "system",
    )
    assert (keyword["timeout_rule"], keyword["support"]) == ("default", [])
    async with ctx.session_factory() as db:
        option = await db.scalar(select(Option).where(Option.session_id == sess.id))
        assert (option.label, option.proposer) == ("물", "system")

    # 평가자 사실: 시간 초과 경로·system·적용 규칙. 아무도 내지 않은 기준안은 동료 아이디어가 아니다
    facts = (await api.internal.get(f"/internal/sessions/{sess.id}/facts")).json()
    decision = facts["decisions"][0]
    assert (decision["path"], decision["filled_by"], decision["timeout_rule"]) == (
        "timeout",
        "system",
        "default",
    )
    assert decision["candidate_option_adopted"] is False and facts["ai_adopted_units"] == []
