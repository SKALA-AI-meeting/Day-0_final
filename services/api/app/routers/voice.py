"""세션 인증을 적용한 음성 제어·완성 파일 전사·비공개 재생 자산."""

import base64
import binascii
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth import Principal, require_candidate
from app.models import SpeechJob
from app.services.common import ServiceError, conflict, iso, load_context
from app.services.context import AppContext, get_context
from app.voice import controller, quota

router = APIRouter()
Ctx = Annotated[AppContext, Depends(get_context)]
Candidate = Annotated[Principal, Depends(require_candidate)]


class TurnIn(BaseModel):
    turn_id: str
    epoch: str
    restart: bool = False


class PlaybackIn(TurnIn):
    job_id: uuid.UUID
    outcome: str = Field(pattern="^(start|ended|error|retry)$")


class RetryIn(TurnIn):
    job_id: uuid.UUID


class AudioIn(TurnIn):
    utterance_id: str
    audio_base64: str = Field(max_length=2600000)
    stop_reason: str = Field(pattern="^(button|timeout|disconnect)$")


@router.get("/sessions/{session_id}/voice")
async def voice_state(session_id: uuid.UUID, ctx: Ctx, _: Candidate):
    """음성 턴·완료 장벽·전사·제출 상태 조회와 재연결 복구."""
    async with ctx.ordered(session_id):
        async with ctx.session_factory() as db:
            session, position, _ = await load_context(db, session_id, lock=True)
            if not position.scenario.get("voice_policy"):
                raise conflict("position_outdated")
            if session.status == "in_progress":
                await quota.admission(db, session, ctx.clock.now())
            snap, pending = await controller.sync(db, session, position, ctx.clock.now())
            snap["heard_message_ids"] = await controller.heard_message_ids(db, session_id)
            if snap.get("state") == "USER_READY" and snap.get("ready_at"):
                think = timedelta(seconds=controller.think_seconds(position))
                snap["think_deadline_at"] = iso(controller.at(snap["ready_at"]) + think)
            if snap.get("utterance_id"):
                job = await db.get(SpeechJob, uuid.UUID(snap["utterance_id"]))
                if job:
                    snap["utterance"] = {
                        "id": str(job.id),
                        **{
                            k: job.data.get(k)
                            for k in ("status", "transcript", "message_id", "error")
                        },
                    }
                    if job.data["status"] == "ready":
                        state = await controller.row(db, session_id)
                        state.data = {**state.data, "state": "PROCESSING"}
                        snap["state"] = "PROCESSING"
                    if job.data["status"] in ("empty", "error"):
                        state = await controller.row(db, session_id)
                        state.data = {
                            **state.data,
                            "state": "USER_READY",
                            "blocked_reason": job.data["status"],
                            "release_ready": True,
                        }
                        snap.update(state="USER_READY", blocked_reason=job.data["status"])
            await db.commit()
    for jid in pending:
        ctx.spawn(controller.run_job(ctx, jid))
    return snap


@router.post("/sessions/{session_id}/voice/retry")
async def retry_stt(session_id: uuid.UUID, body: RetryIn, ctx: Ctx, _: Candidate):
    """보존된 완성 파일의 STT를 같은 발언 ID로 최대 1회 재시도."""
    async with ctx.ordered(session_id):
        async with ctx.session_factory() as db:
            session, _, _ = await load_context(db, session_id, lock=True)
            state = await controller.row(db, session_id)
            controller.validate(session, state.data, body.turn_id, body.epoch, ctx.clock.now())
            job = await db.get(SpeechJob, body.job_id)
            if (
                not job
                or job.session_id != session_id
                or job.kind != "stt"
                or not job.audio
                or job.data.get("status") != "error"
                or job.data.get("attempts", 0) >= 2
                or state.data.get("utterance_id") != str(job.id)
            ):
                raise conflict("voice_retry_exhausted")
            job.data = {**job.data, "status": "queued", "error": None}
            state.data = {**state.data, "state": "TRANSCRIBING", "blocked_reason": None}
            await db.commit()
    ctx.spawn(controller.run_job(ctx, job.id))
    return {"ok": True}


@router.post("/sessions/{session_id}/voice/capture")
async def capture(session_id: uuid.UUID, body: TurnIn, ctx: Ctx, _: Candidate):
    """AI 실행·재생 장벽을 다시 확인하고 최대 60초 발언권 발급."""
    async with ctx.ordered(session_id):
        async with ctx.session_factory() as db:
            session, position, _ = await load_context(db, session_id, lock=True)
            await controller.sync(db, session, position, ctx.clock.now())
            result = await controller.capture(db, session, position, body, ctx.clock.now())
            await db.commit()
            return result


@router.post("/sessions/{session_id}/voice/abort")
async def abort_capture(session_id: uuid.UUID, body: TurnIn, ctx: Ctx, _: Candidate):
    """복구 불가능한 장치·파일 오류를 기록하고 남은 발언권으로 재시도 허용."""
    async with ctx.ordered(session_id):
        async with ctx.session_factory() as db:
            session, _, _ = await load_context(db, session_id, lock=True)
            state = await controller.row(db, session_id)
            controller.validate(session, state.data, body.turn_id, body.epoch, ctx.clock.now())
            if state.data.get("state") != "USER_SPEAKING":
                raise conflict("voice_turn_conflict")
            jid = state.data.get("utterance_id")
            job = await db.get(SpeechJob, uuid.UUID(jid)) if jid else None
            if job:
                job.data = {**job.data, "status": "canceled", "error": "voice_recording_failed"}
                job.audio = None
            state.data = {
                **state.data,
                "state": "USER_READY",
                "blocked_reason": "voice_recording_failed",
                "release_ready": True,
            }
            await db.commit()
            return state.data


@router.post("/sessions/{session_id}/voice/playback")
async def playback(session_id: uuid.UUID, body: PlaybackIn, ctx: Ctx, _: Candidate):
    """현재 항목의 실제 재생 시작·종료·오류 확인. 캐시 재생은 재합성하지 않는다."""
    async with ctx.ordered(session_id):
        async with ctx.session_factory() as db:
            session, position, _ = await load_context(db, session_id, lock=True)
            snap, _pending = await controller.sync(db, session, position, ctx.clock.now())
            controller.validate(session, snap, body.turn_id, body.epoch, ctx.clock.now())
            job = await db.get(SpeechJob, body.job_id)
            if job is None or job.session_id != session_id or job.kind != "tts":
                raise ServiceError(404, "not_found")
            if job.data["status"] == "heard" and body.outcome == "ended":
                return {"ok": True}
            if not snap["items"] or snap["items"][0]["id"] != str(job.id):
                raise conflict("voice_turn_conflict")
            data = dict(job.data)
            if body.outcome == "start":
                if data["status"] != "ready":
                    raise conflict("voice_turn_conflict")
                data["playback_started_at"] = controller.iso(ctx.clock.now())
            elif body.outcome == "ended":
                if not data.get("playback_started_at") or data["status"] != "ready":
                    raise conflict("voice_turn_conflict")
                data.update(status="heard", heard_at=controller.iso(ctx.clock.now()))
            elif body.outcome == "error":
                data.update(status="error", error="voice_playback_failed")
            else:
                if data.get("attempts", 0) >= 2 and not job.audio:
                    raise conflict("voice_retry_exhausted")
                data.update(status="ready" if job.audio else "queued", error=None)
            job.data = data
            await db.commit()
            if data["status"] == "queued":
                ctx.spawn(controller.run_job(ctx, job.id))
            return {"ok": True}


@router.post("/sessions/{session_id}/voice/audio")
async def audio(session_id: uuid.UUID, body: AudioIn, ctx: Ctx, _: Candidate):
    """최종 PCM WAV를 멱등 접수하고 독립 STT 작업 예약."""
    try:
        raw = base64.b64decode(body.audio_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ServiceError(422, "voice_audio_invalid") from exc
    async with ctx.ordered(session_id):
        async with ctx.session_factory() as db:
            session, _, _ = await load_context(db, session_id, lock=True)
            job = await controller.accept_audio(db, session, body, raw, ctx.clock.now())
            await db.commit()
    ctx.spawn(controller.run_job(ctx, job.id))
    return {"utterance_id": str(job.id), "status": job.data["status"]}


@router.get("/sessions/{session_id}/voice/assets/{job_id}")
async def asset(session_id: uuid.UUID, job_id: uuid.UUID, ctx: Ctx, _: Candidate):
    """같은 세션의 만료 전 TTS WAV만 비공개 반환."""
    async with ctx.session_factory() as db:
        job = await db.scalar(
            select(SpeechJob).where(
                SpeechJob.id == job_id, SpeechJob.session_id == session_id, SpeechJob.kind == "tts"
            )
        )
        if not job or not job.audio or job.expires_at <= ctx.clock.now():
            raise ServiceError(404, "not_found")
        return Response(job.audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})
