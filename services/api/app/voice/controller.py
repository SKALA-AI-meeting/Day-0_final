"""DB 기반 Voice Turn Controller. 실행 완료와 실제 재생 확인을 구분한다."""

import hashlib
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.masking import mask
from app.models import AgentRun, Message, SpeechJob, VoiceState
from app.services.common import ServiceError, conflict, iso
from app.voice import quota
from app.voice.speech import pcm

HOLD = {"USER_READY", "USER_SPEAKING", "TRANSCRIBING", "PROCESSING"}


def at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def row(db, session_id):
    await db.execute(
        insert(VoiceState).values(session_id=session_id, data={}).on_conflict_do_nothing()
    )
    return await db.scalar(
        select(VoiceState).where(VoiceState.session_id == session_id).with_for_update()
    )


def validate(session, state, turn_id, epoch, now):
    if (
        session.status != "in_progress"
        or now >= session.phase_deadline_at
        or now >= session.ends_at
    ):
        raise conflict("voice_turn_expired")
    if (
        state.get("turn_id") != turn_id
        or state.get("epoch") != epoch
        or state.get("generation") != session.generation
        or state.get("phase") != session.phase
    ):
        raise conflict("voice_turn_conflict")


async def sync(db, session, position, now):
    state = await row(db, session.id)
    data = dict(state.data)
    if (
        not data
        or data.get("generation") != session.generation
        or data.get("phase") != session.phase
    ):
        data = {
            "turn_id": str(uuid.uuid4()),
            "generation": session.generation,
            "phase": session.phase,
            "state": "AI_GENERATING",
            "epoch": data.get("epoch"),
            "ready_at": iso(now),
        }
    if session.status != "in_progress" or now >= session.ends_at:
        data.update(blocked_reason="session_ending")
        state.data = data
        return {**data, "items": [], "server_now": iso(now)}, []
    if (
        data["state"] == "USER_READY"
        and not data.get("release_ready")
        and now < at(data["ready_at"]) + timedelta(seconds=10)
    ):
        state.data = data
        return {**data, "items": [], "server_now": iso(now)}, []
    if data["state"] in ("USER_SPEAKING", "TRANSCRIBING", "PROCESSING"):
        # 녹음 한도 뒤 업로드·전사에 최대 100초만 허용한다. 늦은 결과는 fence로 거부.
        if now > at(data["speech_deadline_at"]) + timedelta(seconds=100):
            data.update(
                state="AI_GENERATING", utterance_id=None, blocked_reason="voice_interrupted"
            )
        state.data = data
        return {**data, "items": [], "server_now": iso(now)}, []
    runs = (
        await db.scalars(
            select(AgentRun).where(
                AgentRun.session_id == session.id, AgentRun.status.in_(["queued", "started"])
            )
        )
    ).all()
    messages = (
        await db.scalars(
            select(Message)
            .where(
                Message.session_id == session.id,
                Message.channel == "room",
                Message.sender.in_(["p1", "p2", "p3"]),
            )
            .order_by(Message.created_at, Message.seq)
        )
    ).all()
    jobs, pending = [], []
    for message in messages:
        jid = uuid.uuid5(message.id, "tts")
        job = await db.get(SpeechJob, jid)
        if message.phase != session.phase and message.meta.get("kind") not in (
            "consensus_fill",
            "timeout_fill",
        ):
            continue
        if job is None:
            job = SpeechJob(
                id=jid,
                session_id=session.id,
                kind="tts",
                created_at=now,
                expires_at=now + timedelta(hours=24),
                data={
                    "status": "queued",
                    "attempts": 0,
                    "message_id": str(message.id),
                    "body_hash": hashlib.sha256(message.content.encode()).hexdigest(),
                    "text": message.content,
                    "voice": position.scenario["voice_policy"]["voices"][message.sender],
                    "seat": message.sender,
                    "card_ids": message.card_ids,
                },
            )
            db.add(job)
        if job.data["status"] in ("heard", "canceled"):
            continue
        if job.data["status"] == "processing" and now > at(job.data["started_at"]) + timedelta(
            seconds=120
        ):
            job.data = {**job.data, "status": "error", "error": "voice_timeout_unknown"}
        if job.data["status"] == "queued":
            pending.append(job.id)
        jobs.append(job)
    items = [
        {
            "id": str(j.id),
            "seat": j.data["seat"],
            "status": j.data["status"],
            "duration_ms": j.data.get("duration_ms"),
            "error": j.data.get("error"),
        }
        for j in jobs
    ]
    if (runs or jobs) and data["state"] == "USER_READY":
        data = {
            "turn_id": str(uuid.uuid4()),
            "generation": session.generation,
            "phase": session.phase,
            "state": "AI_GENERATING",
            "epoch": data.get("epoch"),
            "ready_at": iso(now),
        }
    if runs or any(j.data["status"] in ("queued", "processing") for j in jobs):
        data["state"] = "AI_GENERATING"
    elif jobs:
        data["state"] = "AI_SPEAKING"
    else:
        if data["state"] != "USER_READY":
            data["ready_at"] = iso(now)
        data["state"] = "USER_READY"
    if data["state"] == "USER_READY" and now > at(data["ready_at"]) + timedelta(seconds=10):
        # 시작하지 않은 예약은 카드/침묵 실행을 위해 반납한다.
        data["release_ready"] = True
    state.data = data
    return {**data, "items": items, "server_now": iso(now)}, pending


async def spoken_message_ids(db, session_id) -> list[str]:
    """재생이 시작된 동료 발언의 메시지 id. 화면은 이 발언만 글로 보여 준다(들은 범위, V5).

    0.75초마다 조회되므로 음성 파일(audio)은 읽지 않고 id만 꺼낸다.
    """
    rows = await db.scalars(
        select(SpeechJob.data["message_id"].astext).where(
            SpeechJob.session_id == session_id,
            SpeechJob.kind == "tts",
            SpeechJob.data.has_key("playback_started_at"),
        )
    )
    return list(rows)


async def blocked(ctx, session_id):
    if not ctx.settings.voice_enabled:
        return False
    from app.models import Session

    async with ctx.session_factory() as db:
        state = await db.get(VoiceState, session_id)
        session = await db.get(Session, session_id)
        return bool(
            state
            and session.status == "in_progress"
            and state.data.get("generation") == session.generation
            and state.data.get("state") in HOLD
            and not state.data.get("release_ready")
            and ctx.clock.now() < session.phase_deadline_at
            and (
                not state.data.get("speech_deadline_at")
                or ctx.clock.now() < at(state.data["speech_deadline_at"]) + timedelta(seconds=100)
            )
        )


async def capture(db, session, position, body, now):
    state = await row(db, session.id)
    data = dict(state.data)
    validate(session, data, body.turn_id, body.epoch, now)
    if data.get("utterance_id") and data["state"] == "USER_SPEAKING":
        return data
    if data["state"] != "USER_READY":
        raise conflict("voice_turn_conflict")
    if (
        getattr(body, "restart", False)
        and data.get("blocked_reason")
        and (
            data.get("capture_count", 0) >= 2
            or (data.get("speech_deadline_at") and at(data["speech_deadline_at"]) <= now)
        )
    ):
        data = {
            "turn_id": str(uuid.uuid4()),
            "generation": session.generation,
            "phase": session.phase,
            "state": "USER_READY",
            "epoch": data.get("epoch"),
            "ready_at": iso(now),
        }
    if data.get("capture_count", 0) >= 2:
        raise conflict("voice_retry_exhausted")
    jid = uuid.uuid4()
    deadline = min(
        now + timedelta(seconds=position.scenario["voice_policy"]["user_turn_seconds"]),
        session.phase_deadline_at,
        session.ends_at,
    )
    if data.get("speech_deadline_at"):
        deadline = min(deadline, at(data["speech_deadline_at"]))
    if deadline <= now:
        raise conflict("voice_turn_expired")
    data.update(
        state="USER_SPEAKING",
        utterance_id=str(jid),
        speech_started_at=iso(now),
        speech_deadline_at=iso(deadline),
        release_ready=False,
        blocked_reason=None,
        capture_count=data.get("capture_count", 0) + 1,
    )
    db.add(
        SpeechJob(
            id=jid,
            session_id=session.id,
            kind="stt",
            created_at=now,
            expires_at=now + timedelta(hours=24),
            data={
                "status": "recording",
                "attempts": 0,
                "phase": session.phase,
                "generation": session.generation,
                "turn_id": body.turn_id,
                "deadline": iso(deadline),
            },
        )
    )
    state.data = data
    return data


async def accept_audio(db, session, body, audio, now):
    state = await row(db, session.id)
    validate(session, state.data, body.turn_id, body.epoch, now)
    try:
        jid = uuid.UUID(body.utterance_id)
    except ValueError as exc:
        raise ServiceError(422, "voice_audio_invalid", "유효한 발언 ID가 필요합니다") from exc
    job = await db.get(SpeechJob, jid)
    if job is None or job.session_id != session.id or job.kind != "stt":
        raise conflict("voice_turn_conflict")
    digest = hashlib.sha256(audio).hexdigest()
    if job.data.get("audio_hash"):
        if job.data["audio_hash"] != digest:
            raise conflict("voice_payload_mismatch")
        return job
    if state.data.get("utterance_id") != str(job.id):
        raise conflict("voice_turn_conflict")
    _, seconds = pcm(audio)
    allowed = (at(job.data["deadline"]) - at(state.data["speech_started_at"])).total_seconds()
    if seconds > allowed + 0.25:
        raise ServiceError(422, "voice_audio_invalid", "발언 시간을 넘긴 파일입니다")
    job.audio = audio
    job.data = {
        **job.data,
        "audio_hash": digest,
        "seconds": seconds,
        "status": "queued",
        "stop_reason": body.stop_reason,
    }
    state.data = {**state.data, "state": "TRANSCRIBING"}
    return job


async def submit(db, session, payload, now):
    """기존 message.send 트랜잭션 안에서 전사·턴 fence와 단일 소비를 확인한다."""
    state = await row(db, session.id)
    validate(session, state.data, payload.get("voice_turn_id"), payload.get("epoch"), now)
    try:
        jid = uuid.UUID(str(payload.get("client_msg_id")))
    except ValueError as exc:
        raise conflict("voice_payload_mismatch") from exc
    job = await db.get(SpeechJob, jid)
    if (
        job is None
        or job.session_id != session.id
        or job.kind != "stt"
        or job.data.get("status") != "ready"
        or not job.data.get("transcript")
        or job.data["transcript"] != mask(str(payload.get("content", "")).strip())
        or state.data.get("utterance_id") != str(jid)
    ):
        raise conflict("voice_payload_mismatch")
    state.data = {**state.data, "state": "AI_GENERATING", "utterance_id": None}
    job.data = {**job.data, "status": "submitted"}
    return job


async def run_job(ctx, job_id):
    """공급자 호출 전 원장·상태를 커밋. timeout은 차감 유지, 명시적 재시도만 가능."""
    from app.models import Session

    async with ctx.session_factory() as db:
        job = await db.scalar(select(SpeechJob).where(SpeechJob.id == job_id).with_for_update())
        if job is None or job.data["status"] != "queued":
            return
        session = await db.get(Session, job.session_id)
        if session.status != "in_progress":
            job.data = {**job.data, "status": "canceled"}
            await db.commit()
            return
        data, audio, kind = dict(job.data), job.audio, job.kind
        quantity = len(data["text"]) if kind == "tts" else data["seconds"]
        try:
            await quota.consume(db, session, kind, quantity, ctx.clock.now())
        except ServiceError as exc:
            job.data = {**data, "status": "error", "error": exc.code}
            await db.commit()
            return
        job.data = {
            **data,
            "status": "processing",
            "started_at": iso(ctx.clock.now()),
            "attempts": data.get("attempts", 0) + 1,
            "quantity": quantity,
            "usage_attempts": [
                *data.get("usage_attempts", []),
                {"quantity": quantity, "at": iso(ctx.clock.now())},
            ],
            "net_cost_usd": 0,
            "billing_status": "reserved",
        }
        await db.commit()
    try:
        speech = ctx.speech
        if kind == "tts":
            result = await speech.synthesize(data["text"], data["voice"])
        else:
            result = mask((await speech.transcribe(audio)).strip())
        error = None
    except Exception:
        result, error = None, "voice_speech_failed"
    async with ctx.ordered(job.session_id):
        async with ctx.session_factory() as db:
            job = await db.scalar(select(SpeechJob).where(SpeechJob.id == job_id).with_for_update())
            if job is None or job.data["status"] != "processing":
                return
            session = await db.get(Session, job.session_id)
            if session.status != "in_progress" or job.expires_at <= ctx.clock.now():
                job.audio = None
                job.data = {**job.data, "status": "canceled"}
                await db.commit()
                return
            extra = {
                "status": "error" if error else "ready",
                "error": error,
                "billing_status": "unknown" if error else "free_f0",
                "net_cost_usd": None if error else 0,
            }
            if not error and kind == "tts":
                import io
                import wave

                with wave.open(io.BytesIO(result), "rb") as wav:
                    extra["duration_ms"] = round(wav.getnframes() / wav.getframerate() * 1000)
                job.audio = result
            elif not error:
                extra["transcript"] = result
                job.audio = None
                if not result:
                    extra["status"] = "empty"
            job.data = {**job.data, **extra}
            await db.commit()
