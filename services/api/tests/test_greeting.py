"""세션 시작 인사: 동료 셋이 고정 문구로 한 마디씩, 발언 수 집계에는 세지 않는다."""

from app.models import Message, Position, Session
from app.services import debate
from sqlalchemy import select


async def test_session_start_posts_three_greetings_first(api, ctx):
    sess = await api.new_session(display_name="하늘")
    async with ctx.session_factory() as db:
        rows = (
            await db.scalars(
                select(Message)
                .where(Message.session_id == sess.id)
                .order_by(Message.created_at, Message.seq)
            )
        ).all()
        greetings = [m for m in rows if m.meta.get("kind") == "greeting"]
        assert [m.sender for m in greetings] == ["p1", "p2", "p3"]
        assert all(m.phase == "keyword" and "하늘님" in m.content for m in greetings)
        names = ["강한결", "윤하람", "서지안"]
        assert [n in m.content for n, m in zip(names, greetings, strict=True)] == [True] * 3
        # 인사는 첫 단계 안내보다 앞이다
        kinds = [m.meta.get("kind") for m in rows]
        assert kinds.index("stage_start") > max(i for i, k in enumerate(kinds) if k == "greeting")

        session = await db.get(Session, sess.id)
        position = await db.get(Position, sess.position_id)
        ds = await debate.debate_state(db, session, position, ctx.clock.now(), False)
        # 인사는 이 단계 발언 수·첫 발언에 세지 않는다(카드 이끎 차례·첫 발언 판단이 그대로)
        assert ds["phase_message_counts"] == {"p1": 0, "p2": 0, "p3": 0}
        assert all(v is None for v in ds["first_spoke_at"].values())


async def test_greeting_skipped_for_position_without_lines(api, ctx):
    """인사 문구가 없는 옛 채용 건은 인사 없이 시작한다."""
    sess = await api.new_session(start=False)
    async with ctx.session_factory() as db:
        position = await db.get(Position, sess.position_id)
        scenario = dict(position.scenario)
        scenario["phrases"] = {k: v for k, v in scenario["phrases"].items() if k != "greeting"}
        position.scenario = scenario
        await db.commit()
    r = await api.public.post(f"/s/{sess.token}/start", headers=sess.auth)
    assert r.status_code == 200, r.text
    async with ctx.session_factory() as db:
        kinds = (
            await db.scalars(
                select(Message.meta["kind"].astext).where(Message.session_id == sess.id)
            )
        ).all()
    assert "greeting" not in kinds and "stage_start" in kinds
