"""DB 기반 Voice Turn Controller. 실행 완료와 실제 재생 확인을 구분한다."""

import hashlib
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.masking import mask
from app.models import AgentRun, Message, SpeechJob, VoiceState
from app.services.common import ServiceError, conflict, iso
from app.voice import live, quota
from app.voice.speech import pcm

log = logging.getLogger(__name__)
HOLD = {"USER_READY", "USER_SPEAKING", "TRANSCRIBING", "PROCESSING"}
# 생각 시간이 끝난 뒤 화면이 녹음을 시작하기까지 기다려 주는 여유. 지나면 차례를 반납한다.
READY_GRACE_SECONDS = 10


def at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def think_seconds(position) -> int:
    """채용 건에 고정된 생각 시간. 이 값이 없던 예전 채용 건은 0(바로 녹음)."""
    return position.scenario["voice_policy"].get("think_seconds", 0)


def confirm_seconds(position) -> int:
    """받아 적은 글을 보여 주고 자동 제출하기까지의 시간. 예전 채용 건은 0(바로 제출)."""
    return position.scenario["voice_policy"].get("confirm_seconds", 0)


def name_phrases(position) -> list[str]:
    """받아 적기 구문 목록: 채용 건에 고정된 동료 이름(성 포함·성 뺀 이름)과 "님" 호칭.

    이름을 틀리게 적으면(예: 윤하람→윤아람) 호명 감지가 놓치므로 받아 적는 단계에서 맞춘다.
    """
    phrases: list[str] = []
    for seat in position.scenario.get("seats", []):
        name = seat["name"]
        given = name[1:] if len(name) >= 3 else name
        phrases += [name, given, f"{name}님", f"{given}님"]
    return phrases


def manual_start(position) -> bool:
    """일반 차례에서 지원자가 직접 [발언하기]로 녹음을 시작하는지(개정 V17). 예전 채용 건은 자동."""
    return "turn_hold_seconds" in position.scenario["voice_policy"]


def ready_hold(position, data: dict | None = None) -> timedelta:
    """내 차례(USER_READY)를 동료 발언·카드에 반납하지 않고 지켜 주는 시간.

    손들기·강제 차례는 자동 시작 대기(생각 시간) + 여유,
    일반 차례는 지원자가 직접 누를 때까지 기다리는 유지 시간(turn_hold_seconds)이다.
    """
    priority = (data or {}).get("priority")
    if priority not in ("hand", "forced") and manual_start(position):
        return timedelta(seconds=position.scenario["voice_policy"]["turn_hold_seconds"])
    return timedelta(seconds=think_seconds(position) + READY_GRACE_SECONDS)


def auto_start_at(position, data: dict) -> str | None:
    """화면이 녹음을 자동으로 시작할 시각. 일반 차례(직접 누름)면 없음."""
    ready = at(data["ready_at"])
    priority = data.get("priority")
    if priority in ("hand", "forced") or not manual_start(position):
        return iso(ready + timedelta(seconds=think_seconds(position)))
    return None


# 녹음이 끝난 뒤 받아 적기·확인·제출에 남겨 두는 여유(초). 마감에 맞춰 잘린 녹음도
# 파일이 마감 전에 도착해 그 단계 발언이 되게 하고, 강제 차례도 이만큼 일찍 발동한다
SUBMIT_MARGIN_SECONDS = 10


def speech_cutoff(session, data: dict) -> datetime:
    """녹음이 아무리 길어도 넘지 못하는 시각.

    단계 마감·세션 종료보다 제출 여유만큼 앞, 그리고 이 차례에 이미 받은 발언 마감(V4).
    """
    margin = timedelta(seconds=SUBMIT_MARGIN_SECONDS)
    cutoff = min(session.phase_deadline_at, session.ends_at) - margin
    if data.get("speech_deadline_at"):
        cutoff = min(cutoff, at(data["speech_deadline_at"]))
    return cutoff


def speech_limit_seconds(session, position, data: dict, now) -> int | None:
    """내 차례에서 녹음을 시작하면 말할 수 있는 초. 발언 한도보다 짧을 때만(마감이 가까움) 준다."""
    turn = position.scenario["voice_policy"]["user_turn_seconds"]
    auto = auto_start_at(position, data)
    start = max(now, at(auto)) if auto else now
    left = int((speech_cutoff(session, data) - start).total_seconds())
    return max(left, 0) if left < turn else None


NOTICE_KINDS = ("consensus_fill", "timeout_fill")
DEBATE_PHASES = ("keyword", "problem", "solution")


def forced_lead(position) -> timedelta:
    """단계 마감 몇 초 전에 강제 차례를 줄지.

    지금 동료 발언이 끝나기를 기다리는 시간(forced_wait_seconds, 예전 채용 건은 0)
    + 자동 시작 대기(생각 시간) + 발언 한도 + 여유.
    """
    policy = position.scenario["voice_policy"]
    return timedelta(
        seconds=policy.get("forced_wait_seconds", 0)
        + think_seconds(position)
        + policy["user_turn_seconds"]
        + SUBMIT_MARGIN_SECONDS
    )


async def needs_forced_turn(db, session, position, data, now) -> bool:
    """이 단계에 지원자 발언이 없고 마감이 가까우면 단계당 한 번 차례를 강제한다(개정 V16)."""
    if session.phase not in DEBATE_PHASES or data.get("forced_phase") == session.phase:
        return False
    if now < session.phase_deadline_at - forced_lead(position):
        return False
    spoke = await db.scalar(
        select(func.count())
        .select_from(Message)
        .where(
            Message.session_id == session.id,
            Message.channel == "room",
            Message.sender == "candidate",
            Message.phase == session.phase,
        )
    )
    return not spoke


def protected(job) -> bool:
    """손들기·지원자 발언으로도 건너뛰지 않는 발언: 카드 발언(평가 장면)과 합의·시간 초과 공지."""
    return bool(job.data.get("card_ids")) or bool(job.data.get("notice"))


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
        forced_phase = data.get("forced_phase") if data.get("phase") == session.phase else None
        data = {
            "turn_id": str(uuid.uuid4()),
            "generation": session.generation,
            "phase": session.phase,
            "state": "AI_GENERATING",
            "epoch": data.get("epoch"),
            "ready_at": iso(now),
            "forced_phase": forced_phase,
        }
    if session.status != "in_progress" or now >= session.ends_at:
        data.update(blocked_reason="session_ending")
        state.data = data
        return {**data, "items": [], "server_now": iso(now)}, []
    forced = await needs_forced_turn(db, session, position, data, now)
    if forced and data["state"] == "USER_READY" and not data.get("priority"):
        data.update(
            priority="forced", ready_at=iso(now), release_ready=False, forced_phase=session.phase
        )
    elif forced and data["state"] in ("AI_GENERATING", "AI_SPEAKING"):
        data["hand"] = {"raised_at": iso(now), "forced": True}
    if (
        data["state"] == "USER_READY"
        and not data.get("release_ready")
        and now < at(data["ready_at"]) + ready_hold(position, data)
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
    last_candidate = await db.scalar(
        select(func.max(Message.created_at)).where(
            Message.session_id == session.id,
            Message.channel == "room",
            Message.sender == "candidate",
        )
    )
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
                    "notice": message.meta.get("kind") in NOTICE_KINDS,
                },
            )
            db.add(job)
        if job.data["status"] in ("heard", "canceled"):
            continue
        if (
            last_candidate
            and message.created_at < last_candidate
            and not job.data.get("playback_started_at")
            and not protected(job)
        ):
            # 지원자가 이미 말했으니 그 전에 나온 안 들은 동료 발언은 흐름에 안 맞는다(손들기 등)
            job.data = {**job.data, "status": "canceled", "error": "superseded"}
            continue
        if job.data["status"] == "processing" and now > at(job.data["started_at"]) + timedelta(
            seconds=120
        ):
            job.data = {**job.data, "status": "error", "error": "voice_timeout_unknown"}
        if job.data["status"] == "queued":
            pending.append(job.id)
        jobs.append(job)
    hand = data.get("hand")
    if hand and data["state"] in ("AI_GENERATING", "AI_SPEAKING"):
        playing = (
            jobs and jobs[0].data["status"] == "ready" and jobs[0].data.get("playback_started_at")
        )
        if not playing:
            # 지금 말하는 동료가 없으면 보호 발언만 남기고 줄 서 있던 동료 발언을 취소한다
            for job in jobs:
                if not protected(job):
                    job.data = {**job.data, "status": "canceled", "error": "hand_raised"}
            jobs = [j for j in jobs if protected(j)]
            pending = [jid for jid in pending if any(j.id == jid for j in jobs)]
            if not jobs:
                data.pop("hand")
                kind = "forced" if hand.get("forced") else "hand"
                data.update(
                    state="USER_READY",
                    ready_at=iso(now),
                    priority=kind,
                    hand_turn=None if kind == "forced" else hand,
                    release_ready=False,
                    blocked_reason=None,
                )
                if kind == "forced":
                    data["forced_phase"] = session.phase
                # 손들기 차례는 실행 중인 에이전트가 있어도 바로 준다. 이후에는 위쪽의
                # "유지 시간 동안 내 차례 지키기"가 새 동료 발언이 빼앗지 못하게 막는다.
                state.data = data
                return {**data, "items": [], "server_now": iso(now)}, []
    items = [
        {
            "id": str(j.id),
            "message_id": j.data["message_id"],
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
            "forced_phase": data.get("forced_phase"),
        }
    if jobs and jobs[0].data["status"] == "ready":
        # 맨 앞 발언이 준비되면 뒤 발언·실행이 남아 있어도 먼저 재생한다(대기 최대 약 14초 단축).
        # 남은 발언이나 실행이 있는 동안은 아래 조건으로 내 차례(USER_READY)가 되지 않는다.
        data["state"] = "AI_SPEAKING"
    elif runs or any(j.data["status"] in ("queued", "processing") for j in jobs):
        data["state"] = "AI_GENERATING"
    elif jobs:
        data["state"] = "AI_SPEAKING"
    else:
        if data["state"] != "USER_READY":
            data["ready_at"] = iso(now)
        data["state"] = "USER_READY"
    if data["state"] == "USER_READY" and now > at(data["ready_at"]) + ready_hold(position, data):
        # 시작하지 않은 예약은 카드/침묵 실행을 위해 반납한다.
        data["release_ready"] = True
    state.data = data
    return {**data, "items": items, "server_now": iso(now)}, pending


async def heard_message_ids(db, session_id) -> list[str]:
    """재생이 끝난 동료 발언의 메시지 id. 화면은 이 발언만 대화 기록에 글로 남긴다(들은 범위, V5).
    재생 중인 발언은 화면이 자막으로 한 문장씩 보여 준다.

    0.75초마다 조회되므로 음성 파일(audio)은 읽지 않고 id만 꺼낸다.
    """
    rows = await db.scalars(
        select(SpeechJob.data["message_id"].astext).where(
            SpeechJob.session_id == session_id,
            SpeechJob.kind == "tts",
            SpeechJob.data["status"].astext == "heard",
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
        speech_cutoff(session, data),
    )
    if deadline <= now:
        raise conflict("voice_turn_expired")
    # 평가 담당이 "손들고 말함 / 내 차례에 말함 / 강제로 말함"을 구분할 수 있게 발언 기록에 남긴다
    turn_kind = data.pop("priority", None) or "turn"
    hand_turn = data.pop("hand_turn", None)
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
                "turn_kind": turn_kind,
                "hand": hand_turn,
                "phrases": name_phrases(position),
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


async def raise_hand(db, session, body, now):
    """동료가 말하거나 대답을 준비하는 동안 손들기/손 내리기. 지금 발언이 끝나면 내 차례가 된다."""
    state = await row(db, session.id)
    data = dict(state.data)
    validate(session, data, body.turn_id, body.epoch, now)
    if data.get("state") not in ("AI_GENERATING", "AI_SPEAKING"):
        raise conflict("voice_turn_conflict")
    if body.raised:
        # 누구 발언 중 몇 초에 들었는지(재생 중이 아니면 비움)는 화면이 알려 준다
        data["hand"] = {
            "raised_at": iso(now),
            "during_message_id": body.during_message_id,
            "offset_ms": body.offset_ms,
        }
    else:
        data.pop("hand", None)
    state.data = data
    return data


async def transcribe_stt(speech, job_id, audio: bytes, data: dict) -> str:
    """미리 받아 적은 조각이 있으면 이어 붙이고, 없거나 어긋나면 완성 파일 전체로 받아 적는다."""
    seg = live.pop(str(job_id))
    if seg is not None:
        try:
            return await seg.finish(data["seconds"])
        except live.Fallback as exc:
            log.warning("말하는 동안 받아 적기 대신 파일 전체로 받아 적음: %s", exc)
    return await speech.transcribe(audio, data.get("phrases", []))


async def rerecord(db, session, body, now):
    """받아 적은 글을 제출하지 않고 버린 뒤, 같은 차례에서 한 번만 새로 녹음하게 한다(60초 새로)."""
    state = await row(db, session.id)
    data = dict(state.data)
    validate(session, data, body.turn_id, body.epoch, now)
    if data.get("rerecorded"):
        raise conflict("voice_retry_exhausted")
    job = await db.get(SpeechJob, body.job_id)
    if (
        job is None
        or job.session_id != session.id
        or job.kind != "stt"
        or data.get("utterance_id") != str(job.id)
        or job.data.get("status") != "ready"
    ):
        raise conflict("voice_turn_conflict")
    job.data = {**job.data, "status": "canceled", "error": "voice_rerecorded"}
    live.discard(str(job.id))
    # 앞 녹음의 마감을 지워야 capture가 발언 시간을 새로 준다
    for key in ("utterance_id", "speech_started_at", "speech_deadline_at"):
        data.pop(key, None)
    data.update(
        state="USER_READY",
        ready_at=iso(now),
        rerecorded=True,
        capture_count=0,
        release_ready=False,
        blocked_reason=None,
    )
    state.data = data
    return data


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
            result = mask((await transcribe_stt(speech, job_id, audio, data)).strip())
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
                extra["ready_at"] = iso(ctx.clock.now())  # 확인 화면 자동 제출 시각의 기준
                job.audio = None
                if not result:
                    extra["status"] = "empty"
            job.data = {**job.data, **extra}
            await db.commit()
