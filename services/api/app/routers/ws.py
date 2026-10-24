"""WebSocket /ws/sessions/{id}?token=<세션 JWT>. 서버 프레임은 hub의 단일 송신 큐로만 나간다.

message.send: in_progress 확인 → 마스킹·저장·generation +1·agent_runs queued 한 커밋 →
message.ack(의도 분류 전) → message.new → message 자극 enqueue. 같은 client_msg_id는 기존 메시지로
ack.
typing: 지원자 입력 중(5초 갱신·6초 만료), 저장하지 않는다.
연결 없는 구간은 connection_gaps에 기록한다.
"""

import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.auth import decode_token
from app.masking import mask
from app.models import ConnectionGap, Message
from app.services import debate
from app.services.common import ServiceError, load_session, serialize_message
from app.services.context import AppContext

router = APIRouter()


async def handle_send(ctx: AppContext, session_id: uuid.UUID, payload: dict[str, Any]) -> None:
    client_msg_id = str(payload.get("client_msg_id") or "")
    content = str(payload.get("content") or "")
    if not client_msg_id or not content.strip():
        await ctx.hub.send(session_id, "error", {"code": "invalid_input", "message": "empty"})
        return
    async with ctx.ordered(session_id):
        try:
            async with ctx.session_factory() as db:
                session = await load_session(db, session_id, lock=True)
                voice_job = None
                if ctx.settings.voice_enabled:
                    from app.voice.controller import submit

                    existing = await db.scalar(
                        select(Message).where(
                            Message.session_id == session_id, Message.client_msg_id == client_msg_id
                        )
                    )
                    if existing is None:
                        voice_job = await submit(db, session, payload, ctx.clock.now())
                    elif existing.content != mask(content.strip()):
                        raise ServiceError(409, "voice_payload_mismatch")
                message, run_id, stimulus = await debate.save_candidate_message(
                    db, session, client_msg_id, content, ctx.clock
                )
                if voice_job:
                    voice_job.data = {**voice_job.data, "message_id": str(message.id)}
                    message.meta = {
                        **message.meta,
                        "source": "voice",
                        "utterance_id": str(voice_job.id),
                    }
                await db.commit()
                serialized = serialize_message(session, message)
                generation = session.generation
        except ServiceError as exc:
            code = (
                exc.code
                if exc.code.startswith("voice_") or exc.code in ("session_ending", "invalid_input")
                else "conflict"
            )
            await ctx.hub.send(session_id, "error", {"code": code, "message": exc.message})
            return
        ack = {
            "client_msg_id": client_msg_id,
            "message_id": serialized["id"],
            "run_id": str(run_id) if run_id else None,
        }
        await ctx.hub.send(session_id, "message.ack", ack)
        if stimulus is None:
            return
        ctx.set_generation(session_id, generation)
        await ctx.hub.send(session_id, "message.new", serialized)
        ctx.mailboxes.put(session_id, stimulus, run_id)


@router.websocket("/ws/sessions/{session_id}")
async def socket(websocket: WebSocket, session_id: uuid.UUID) -> None:
    ctx: AppContext = websocket.app.state.ctx
    # 거부도 accept 뒤에 close한다. accept 전 close는 uvicorn이 HTTP 403으로 바꿔 브라우저가
    # 1006만 받으므로 web의 4401(→resume)·4403(정지) 분기가 동작하지 않는다
    reject = _reject_code(websocket.query_params.get("token", ""), session_id)
    await websocket.accept()
    if reject is not None:
        await websocket.close(code=reject)
        return
    await ctx.hub.connect(session_id, websocket)
    if ctx.settings.voice_enabled:
        from app.voice.controller import row

        async with ctx.ordered(session_id):
            async with ctx.session_factory() as db:
                voice = await row(db, session_id)
                data = {**voice.data, "epoch": str(uuid.uuid4())}
                if data.get("state") == "USER_SPEAKING":
                    data.update(state="USER_READY", blocked_reason="voice_interrupted")
                voice.data = data
                await db.commit()
    await _mark_reconnected(ctx, session_id)
    try:
        while True:
            frame = await websocket.receive_json()
            if not ctx.hub.is_current(session_id, websocket):
                break
            kind, payload = frame.get("type"), frame.get("payload") or {}
            if kind == "message.send":
                await handle_send(ctx, session_id, payload)
            elif kind == "typing":
                if not ctx.settings.voice_enabled:
                    ctx.hub.candidate_typing(session_id, payload.get("state", "stop"))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if ctx.hub.disconnect(session_id, websocket):
            await ctx.mark_disconnected(session_id)


def _reject_code(token: str, session_id: uuid.UUID) -> int | None:
    """토큰 문제 4401(resume 후 재연결), 다른 세션 4403(재연결 안 함), 통과면 None."""
    try:
        principal = decode_token(token)
    except Exception:
        return 4401
    if principal.role != "candidate":
        return 4401
    if principal.subject != session_id:
        return 4403
    return None


async def _mark_reconnected(ctx: AppContext, session_id: uuid.UUID) -> None:
    async with ctx.session_factory() as db:
        gap = await db.scalar(
            select(ConnectionGap).where(
                ConnectionGap.session_id == session_id, ConnectionGap.reconnected_at.is_(None)
            )
        )
        if gap is not None:
            gap.reconnected_at = ctx.clock.now()
            await db.commit()
