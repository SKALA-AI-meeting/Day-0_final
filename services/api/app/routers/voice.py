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
from app.voice import controller, live, quota

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


class HandIn(TurnIn):
    raised: bool = True
    during_message_id: str | None = None
    offset_ms: int | None = Field(default=None, ge=0)


class StreamIn(TurnIn):
    utterance_id: str
    seq: int = Field(ge=0, le=1000)
    pcm_base64: str = Field(max_length=200000)  # 조각 하나(약 0.5초, 최대 4초 남짓)


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
                # 자동 녹음 시각(손들기·강제는 생각 시간 뒤). 일반 차례는 직접 눌러 없음
                auto = controller.auto_start_at(position, snap)
                if auto:
                    snap["think_deadline_at"] = auto
                hold = controller.ready_hold(position, snap)
                snap["turn_until_at"] = iso(controller.at(snap["ready_at"]) + hold)
                # 마감이 가까워 발언 한도(60초)를 다 못 쓰면 녹음 전에 알린다
                limit = controller.speech_limit_seconds(session, position, snap, ctx.clock.now())
                if limit is not None:
                    snap["speech_limit_seconds"] = limit
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
                    if job.data["status"] == "ready" and job.data.get("ready_at"):
                        wait = timedelta(seconds=controller.confirm_seconds(position))
                        ready = controller.at(job.data["ready_at"])
                        snap["utterance"]["confirm_deadline_at"] = iso(ready + wait)
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


@router.post("/sessions/{session_id}/voice/rerecord")
async def rerecord(session_id: uuid.UUID, body: RetryIn, ctx: Ctx, _: Candidate):
    """받아 적은 글을 제출하지 않고 버린 뒤 같은 차례에서 한 번 다시 녹음한다."""
    async with ctx.ordered(session_id):
        async with ctx.session_factory() as db:
            session, _, _ = await load_context(db, session_id, lock=True)
            data = await controller.rerecord(db, session, body, ctx.clock.now())
            await db.commit()
            return data


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
                live.discard(str(job.id))
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
            job = await db.get(SpeechJob, body.job_id)
            if job is None or job.session_id != session_id or job.kind != "tts":
                raise ServiceError(404, "not_found")
            if job.data["status"] == "heard" and body.outcome == "ended":
                return {"ok": True}
            if (
                body.outcome == "ended"
                and job.data["status"] == "ready"
                and job.data.get("playback_started_at")
            ):
                # 재생을 시작한 발언이 끝까지 재생되면 그사이 단계가 바뀌었어도 들은 것으로 남긴다
                job.data = {**job.data, "status": "heard", "heard_at": iso(ctx.clock.now())}
                await db.commit()
                return {"ok": True}
            snap, _pending = await controller.sync(db, session, position, ctx.clock.now())
            controller.validate(session, snap, body.turn_id, body.epoch, ctx.clock.now())
            if not snap["items"] or snap["items"][0]["id"] != str(job.id):
                raise conflict("voice_turn_conflict")
            data = dict(job.data)
            if body.outcome == "start":
                if data["status"] != "ready":
                    raise conflict("voice_turn_conflict")
                data["playback_started_at"] = controller.iso(ctx.clock.now())
            elif body.outcome == "ended":
                raise conflict("voice_turn_conflict")  # 재생을 시작하지 않은 발언
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


@router.post("/sessions/{session_id}/voice/hand")
async def hand(session_id: uuid.UUID, body: HandIn, ctx: Ctx, _: Candidate):
    """손들기/손 내리기. 지금 동료 발언이 끝나면 보호 발언만 듣고 내 차례가 된다."""
    async with ctx.ordered(session_id):
        async with ctx.session_factory() as db:
            session, _, _ = await load_context(db, session_id, lock=True)
            data = await controller.raise_hand(db, session, body, ctx.clock.now())
            await db.commit()
            return data


@router.post("/sessions/{session_id}/voice/stream")
async def stream(session_id: uuid.UUID, body: StreamIn, ctx: Ctx, _: Candidate):
    """녹음 중 소리 조각(PCM16 mono 16kHz 원본, base64)을 받아 말하는 동안 받아 적기를 진행한다."""
    try:
        chunk = base64.b64decode(body.pcm_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ServiceError(422, "voice_audio_invalid") from exc
    async with ctx.ordered(session_id):
        async with ctx.session_factory() as db:
            session, _, _ = await load_context(db, session_id)
            state = await controller.row(db, session_id)
            controller.validate(session, state.data, body.turn_id, body.epoch, ctx.clock.now())
            if (
                state.data.get("state") != "USER_SPEAKING"
                or state.data.get("utterance_id") != body.utterance_id
            ):
                raise conflict("voice_turn_conflict")
            job = await db.get(SpeechJob, uuid.UUID(body.utterance_id))
            phrases = job.data.get("phrases", []) if job else []
            await db.commit()
    seg = live.open_segmenter(body.utterance_id, str(session_id), ctx.speech, phrases)
    seg.feed(body.seq, chunk)
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
