"""DebateState `open_mentions`: 그 단계 전체 기록에서 계산한 아직 답하지 않은 지명(결정 C1)."""

from app.models import Message
from kit import high
from sqlalchemy import select


async def _state(api, sess) -> dict:
    return (await api.internal.get(f"/internal/sessions/{sess.id}/debate_state")).json()


async def test_open_mention_survives_beyond_the_transcript_window(api, ctx, classifier):
    """지명이 최근 대화 30개 밖으로 밀려도 남고, 그 자리가 답하면(meta.reply_to) 닫힌다."""
    sess = await api.new_session()
    classifier.rules["한결님 어떻게 보세요?"] = high("question", addressed_to=["p1"])
    await api.say(sess, "한결님 어떻게 보세요?")
    for i in range(32):
        await api.say(sess, f"음 {i}")
    async with ctx.session_factory() as db:
        mention = await db.scalar(select(Message).where(Message.content == "한결님 어떻게 보세요?"))
    state = await _state(api, sess)
    assert state["open_mentions"] == [
        {
            "seat": "p1",
            "message_id": str(mention.id),
            "since": str(mention.id),
            "sender": "candidate",
            "content": "한결님 어떻게 보세요?",
        }
    ]
    await api.post_ai(sess, "p1", "불이 좋아요.", meta={"reply_to": str(mention.id)})
    assert (await _state(api, sess))["open_mentions"] == []
