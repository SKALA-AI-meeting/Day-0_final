"""음성 완료 장벽·실제 저장 경로·원장 회귀. 클라우드를 호출하지 않는다."""

import base64
import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from app import scheduler
from app.models import Message, Session, SpeechJob, VoiceState
from app.routers.ws import handle_send
from app.services import debate, reports
from app.services.common import ServiceError
from app.voice import controller, quota
from app.voice.speech import FakeSpeech, pcm, silence
from sqlalchemy import select


@pytest.fixture
async def voice_ctx(ctx, monkeypatch):
    monkeypatch.setenv("VOICE_QUOTA_PERIOD", "test-period")
    ctx.settings = replace(ctx.settings, voice_enabled=True)
    ctx.speech = FakeSpeech("물에 관한 의견입니다")
    yield ctx
    ctx.settings = replace(ctx.settings, voice_enabled=False)


async def setup(api, ctx):
    sess = await api.new_session()
    async with ctx.session_factory() as db:
        state = await controller.row(db, sess.id)
        state.data = {**state.data, "epoch": "test"}
        await db.commit()
    return sess


async def snapshot(api, ctx, sess):
    r = await api.public.get(f"/sessions/{sess.id}/voice", headers=sess.auth)
    assert r.status_code == 200, r.text
    await ctx.drain_spawned()
    return r.json()


async def post(api, sess, suffix, body):
    return await api.public.post(
        f"/sessions/{sess.id}/voice/{suffix}", headers=sess.auth, json=body
    )


async def test_voice_playback_barrier_and_single_message(api, voice_ctx):
    ctx = voice_ctx
    sess = await setup(api, ctx)
    await api.post_ai(sess, "p1", "물 부족을 생각해 봐요")
    await api.post_ai(sess, "p3", "다른 의견도 있어요")
    await snapshot(api, ctx, sess)
    snap = await snapshot(api, ctx, sess)
    assert snap["state"] == "AI_SPEAKING"
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    assert (await post(api, sess, "capture", turn)).status_code == 409
    for item in snap["items"]:
        assert (
            await post(api, sess, "playback", {**turn, "job_id": item["id"], "outcome": "start"})
        ).status_code == 200
        assert (
            await post(api, sess, "playback", {**turn, "job_id": item["id"], "outcome": "ended"})
        ).status_code == 200
    snap = await snapshot(api, ctx, sess)
    assert snap["state"] == "USER_READY"
    start = (await post(api, sess, "capture", turn)).json()
    assert (
        controller.at(start["speech_deadline_at"]) - controller.at(start["speech_started_at"])
    ).total_seconds() == 60
    body = {
        **turn,
        "utterance_id": start["utterance_id"],
        "audio_base64": base64.b64encode(silence()).decode(),
        "stop_reason": "button",
    }
    assert (await post(api, sess, "audio", body)).status_code == 200
    assert (await post(api, sess, "audio", body)).status_code == 200
    await ctx.drain_spawned()
    snap = await snapshot(api, ctx, sess)
    assert snap["utterance"]["status"] == "ready"
    payload = {
        "client_msg_id": start["utterance_id"],
        "content": snap["utterance"]["transcript"],
        "voice_turn_id": turn["turn_id"],
        "epoch": "test",
    }
    await handle_send(ctx, sess.id, payload)
    await handle_send(ctx, sess.id, payload)
    await ctx.mailboxes.drain()
    async with ctx.session_factory() as db:
        mine = (
            await db.scalars(
                select(Message).where(Message.session_id == sess.id, Message.sender == "candidate")
            )
        ).all()
        assert len(mine) == 1
        assert mine[0].meta["source"] == "voice"
        assert mine[0].meta["edited"] is False  # 고치지 않은 인식 글


@pytest.mark.parametrize(
    "recognition,submitted",
    [
        ("물에 관한 의견입니다", "물에 관한 수정 의견입니다"),
        ("", "마이크 없이 직접 쓴 의견입니다"),
    ],
)
async def test_voice_edited_submit_keeps_recognition_and_turn_fence(
    api, voice_ctx, recognition, submitted
):
    sess = await setup(api, voice_ctx)
    voice_ctx.speech.transcript = recognition
    snap = await snapshot(api, voice_ctx, sess)
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    start = (await post(api, sess, "capture", turn)).json()
    assert (
        await post(
            api,
            sess,
            "audio",
            {
                **turn,
                "utterance_id": start["utterance_id"],
                "audio_base64": base64.b64encode(silence()).decode(),
                "stop_reason": "button",
            },
        )
    ).status_code == 200
    await voice_ctx.drain_spawned()
    snap = await snapshot(api, voice_ctx, sess)
    assert snap["utterance"]["status"] == ("ready" if recognition else "empty")
    # 수정하는 동안 보일 제출 마감: ready는 녹음 마감 + 처리 여유, empty는 내 차례 끝
    # (둘 다 단계 마감 이내)
    async with voice_ctx.session_factory() as db:
        session = await db.get(Session, sess.id)
        limit = min(session.phase_deadline_at, session.ends_at)
    expected = (
        controller.at(start["speech_deadline_at"])
        + timedelta(seconds=controller.PROCESSING_GRACE_SECONDS)
        if recognition
        else controller.at(snap["turn_until_at"])
    )
    assert controller.at(snap["utterance"]["submit_deadline_at"]) == min(limit, expected)
    payload = {
        "client_msg_id": start["utterance_id"],
        "content": submitted,
        "voice_turn_id": turn["turn_id"],
        "epoch": turn["epoch"],
    }
    async with voice_ctx.session_factory() as db:
        session = await db.get(Session, sess.id)
        with pytest.raises(ServiceError) as wrong_turn:
            await controller.submit(
                db, session, {**payload, "voice_turn_id": str(uuid.uuid4())}, voice_ctx.clock.now()
            )
        assert wrong_turn.value.code == "voice_turn_conflict"
        with pytest.raises(ServiceError) as wrong_utterance:
            await controller.submit(
                db, session, {**payload, "client_msg_id": str(uuid.uuid4())}, voice_ctx.clock.now()
            )
        assert wrong_utterance.value.code == "voice_payload_mismatch"
        await db.rollback()
    await handle_send(voice_ctx, sess.id, payload)
    async with voice_ctx.session_factory() as db:
        mine = (
            await db.scalars(
                select(Message).where(Message.session_id == sess.id, Message.sender == "candidate")
            )
        ).all()
        assert len(mine) == 1
        assert mine[0].content == submitted
        assert mine[0].meta["stt_transcript"] == recognition
        assert mine[0].meta["edited"] is True
        # 관리자 전체 기록: 본문은 제출 글, 고친 경우에만 원래 인식 글을 함께 준다
        entry = next(
            e
            for e in await reports.record(db, await db.get(Session, sess.id))
            if e["ref"]["id"] == str(mine[0].id)
        )
        assert entry["summary"] == submitted
        assert entry["stt_transcript"] == recognition


async def test_voice_too_long_submit_is_rejected_and_turn_kept(api, voice_ctx):
    """받아 적은 글을 길게 고쳐도 서버가 상한을 넘는 발언은 받지 않고, 줄여 다시 보내면 들어간다."""
    sess = await setup(api, voice_ctx)
    snap = await snapshot(api, voice_ctx, sess)
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    start = (await post(api, sess, "capture", turn)).json()
    audio = {
        **turn,
        "utterance_id": start["utterance_id"],
        "audio_base64": base64.b64encode(silence()).decode(),
        "stop_reason": "button",
    }
    assert (await post(api, sess, "audio", audio)).status_code == 200
    await voice_ctx.drain_spawned()
    await snapshot(api, voice_ctx, sess)
    payload = {
        "client_msg_id": start["utterance_id"],
        "voice_turn_id": turn["turn_id"],
        "epoch": turn["epoch"],
    }
    mark = api.mark(sess)
    long = "가" * (debate.CANDIDATE_MESSAGE_MAX_CHARS + 1)
    await handle_send(voice_ctx, sess.id, {**payload, "content": long})
    errors = [f for f in api.frames_since(sess, mark) if f["type"] == "error"]
    assert [e["payload"]["code"] for e in errors] == ["invalid_input"]
    snap = await snapshot(api, voice_ctx, sess)
    assert snap["utterance"]["status"] == "ready"  # 발언권을 소비하지 않음
    fits = "나" * debate.CANDIDATE_MESSAGE_MAX_CHARS
    await handle_send(voice_ctx, sess.id, {**payload, "content": fits})
    async with voice_ctx.session_factory() as db:
        mine = (
            await db.scalars(
                select(Message).where(Message.session_id == sess.id, Message.sender == "candidate")
            )
        ).all()
        assert [m.content for m in mine] == [fits]


async def test_voice_heard_ids_follow_playback_end(api, voice_ctx):
    """동료 발언은 재생이 끝난 것만 heard_message_ids에 들어간다(재생 중에는 자막만 보임)."""
    ctx = voice_ctx
    sess = await setup(api, ctx)
    first = await api.post_ai(sess, "p1", "물 부족을 생각해 봐요")
    await api.post_ai(sess, "p3", "다른 의견도 있어요")
    await snapshot(api, ctx, sess)
    snap = await snapshot(api, ctx, sess)
    assert snap["heard_message_ids"] == []
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    item = snap["items"][0]
    assert item["message_id"] == first["id"]
    r = await post(api, sess, "playback", {**turn, "job_id": item["id"], "outcome": "start"})
    assert r.status_code == 200
    snap = await snapshot(api, ctx, sess)
    assert snap["heard_message_ids"] == []
    r = await post(api, sess, "playback", {**turn, "job_id": item["id"], "outcome": "ended"})
    assert r.status_code == 200
    snap = await snapshot(api, ctx, sess)
    assert snap["heard_message_ids"] == [first["id"]]


async def test_voice_plays_first_ready_while_next_is_made(api, voice_ctx):
    """맨 앞 발언이 준비되면 뒤 발언을 만드는 중이어도 재생하고, 다 들을 때까지 내 차례는 없다."""
    ctx = voice_ctx
    sess = await setup(api, ctx)
    await api.post_ai(sess, "p1", "물 부족을 생각해 봐요")
    snap = await snapshot(api, ctx, sess)  # p1 합성까지 끝냄
    await api.post_ai(sess, "p3", "다른 의견도 있어요")
    r = await api.public.get(f"/sessions/{sess.id}/voice", headers=sess.auth)
    snap = r.json()
    assert [i["status"] for i in snap["items"]] == ["ready", "queued"]
    assert snap["state"] == "AI_SPEAKING"
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    first = snap["items"][0]["id"]
    for outcome in ("start", "ended"):
        body = {**turn, "job_id": first, "outcome": outcome}
        assert (await post(api, sess, "playback", body)).status_code == 200
    await ctx.drain_spawned()
    snap = await snapshot(api, ctx, sess)
    assert snap["state"] == "AI_SPEAKING"  # 둘째 발언 차례, 아직 내 차례 아님
    assert len(snap["items"]) == 1


async def test_voice_normal_turn_waits_for_press(api, voice_ctx, clock):
    """일반 내 차례는 자동 녹음 없이 지원자가 누를 때까지 유지 시간(기본 20초) 동안 지킨다."""
    sess = await setup(api, voice_ctx)
    snap = await snapshot(api, voice_ctx, sess)
    assert snap["state"] == "USER_READY"
    assert "think_deadline_at" not in snap  # 자동 시작 없음
    assert "speech_limit_seconds" not in snap  # 마감이 멀면 60초 그대로
    hold = controller.at(snap["turn_until_at"]) - controller.at(snap["ready_at"])
    assert hold.total_seconds() == 20
    clock.advance(seconds=19)
    snap = await snapshot(api, voice_ctx, sess)
    assert not snap.get("release_ready")
    clock.advance(seconds=2)
    assert not await controller.blocked(voice_ctx, sess.id)  # GET 없이도 대기 실행을 푼다
    snap = await snapshot(api, voice_ctx, sess)
    assert snap["release_ready"] is True
    # 실제 handoff 전에는 아직 지원자가 발언을 시작할 수 있다.
    assert (
        await post(api, sess, "capture", {"turn_id": snap["turn_id"], "epoch": snap["epoch"]})
    ).status_code == 200


async def test_two_hold_expiries_resume_colleagues_in_one_phase(api, voice_ctx, clock):
    sess = await setup(api, voice_ctx)
    first = await snapshot(api, voice_ctx, sess)
    clock.advance(seconds=21)
    await scheduler.tick_session(voice_ctx, sess.id)
    await voice_ctx.mailboxes.drain()
    assert voice_ctx.runner.calls[-1][1].type == "idle"
    snap = await snapshot(api, voice_ctx, sess)
    assert snap["turn_id"] != first["turn_id"]
    # RecordingRunner는 발언을 게시하지 않으므로 빈 AI 차례 뒤 새 USER_READY가 된다.
    assert snap["state"] == "USER_READY"
    async with voice_ctx.session_factory() as db:
        session = await db.get(Session, sess.id)
        session.invite_delivered_phases = [*session.invite_delivered_phases, session.phase]
        await db.commit()
    clock.advance(seconds=21)
    await scheduler.tick_session(voice_ctx, sess.id)
    await voice_ctx.mailboxes.drain()
    assert voice_ctx.runner.calls[-1][1].type == "turn_elapsed"
    assert (await snapshot(api, voice_ctx, sess))["turn_id"] != snap["turn_id"]


async def test_hold_handoff_ignores_retry_and_stale_voice(api, voice_ctx, clock):
    sess = await setup(api, voice_ctx)
    snap = await snapshot(api, voice_ctx, sess)
    async with voice_ctx.session_factory() as db:
        session = await db.get(Session, sess.id)
        session.invite_delivered_phases = [*session.invite_delivered_phases, session.phase]
        await db.commit()
    clock.advance(seconds=19)
    async with voice_ctx.session_factory() as db:
        voice = await db.get(VoiceState, sess.id)
        voice.data = {**snap, "blocked_reason": "empty", "release_ready": False}
        await db.commit()
    await scheduler.tick_session(voice_ctx, sess.id)
    await voice_ctx.mailboxes.drain()
    assert not [c for c in voice_ctx.runner.calls if c[1].type == "turn_elapsed"]

    clock.advance(seconds=2)
    await scheduler.tick_session(voice_ctx, sess.id)
    await voice_ctx.mailboxes.drain()
    assert voice_ctx.runner.calls[-1][1].type == "turn_elapsed"

    for changes in (
        {"generation": snap["generation"] - 1, "release_ready": True},
        {"phase": "problem", "release_ready": True},
    ):
        async with voice_ctx.session_factory() as db:
            voice = await db.get(VoiceState, sess.id)
            voice.data = {**snap, **changes}
            await db.commit()
        before = len(voice_ctx.runner.calls)
        await scheduler.tick_session(voice_ctx, sess.id)
        await voice_ctx.mailboxes.drain()
        assert len(voice_ctx.runner.calls) == before


async def test_two_empty_stt_results_handoff_after_retry_hold(api, voice_ctx, clock):
    sess = await setup(api, voice_ctx)
    voice_ctx.speech.transcript = ""
    async with voice_ctx.session_factory() as db:
        session = await db.get(Session, sess.id)
        session.invite_delivered_phases = [*session.invite_delivered_phases, session.phase]
        await db.commit()

    snap = await snapshot(api, voice_ctx, sess)
    for attempt in range(2):
        turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
        response = await post(api, sess, "capture", {**turn, "restart": attempt > 0})
        assert response.status_code == 200, response.text
        body = {
            **turn,
            "utterance_id": response.json()["utterance_id"],
            "audio_base64": base64.b64encode(silence()).decode(),
            "stop_reason": "button",
        }
        assert (await post(api, sess, "audio", body)).status_code == 200
        await voice_ctx.drain_spawned()
        snap = await snapshot(api, voice_ctx, sess)
        assert snap["blocked_reason"] == "empty"
        assert snap["state"] == "USER_READY"

    assert snap["capture_count"] == 2
    exhausted = await post(api, sess, "capture", turn)
    assert exhausted.status_code == 409
    assert exhausted.json()["error"]["code"] == "voice_retry_exhausted"
    clock.advance(seconds=19)
    await scheduler.tick_session(voice_ctx, sess.id)
    await voice_ctx.mailboxes.drain()
    assert not [c for c in voice_ctx.runner.calls if c[1].type == "turn_elapsed"]
    clock.advance(seconds=2)
    await scheduler.tick_session(voice_ctx, sess.id)
    await voice_ctx.mailboxes.drain()
    assert voice_ctx.runner.calls[-1][1].type == "turn_elapsed"


async def test_next_ready_is_protected_after_turn_elapsed(api, voice_ctx, clock):
    sess = await setup(api, voice_ctx)
    first = await snapshot(api, voice_ctx, sess)
    async with voice_ctx.session_factory() as db:
        session = await db.get(Session, sess.id)
        session.invite_delivered_phases = [*session.invite_delivered_phases, session.phase]
        await db.commit()
    clock.advance(seconds=21)
    await scheduler.tick_session(voice_ctx, sess.id)
    await voice_ctx.mailboxes.drain()
    assert voice_ctx.runner.calls[-1][1].type == "turn_elapsed"

    next_turn = await snapshot(api, voice_ctx, sess)
    assert next_turn["state"] == "USER_READY"
    assert next_turn["turn_id"] != first["turn_id"]
    assert next_turn["blocked_reason"] is None
    assert not next_turn.get("release_ready")
    assert await controller.blocked(voice_ctx, sess.id)
    await api.post_ai(sess, "p3", "카드 발언", card_ids=["persuasion_keyword"])
    assert (await snapshot(api, voice_ctx, sess))["state"] == "USER_READY"


async def test_stt_failure_retry_gets_fresh_hold_after_original_expires(api, voice_ctx, clock):
    sess = await setup(api, voice_ctx)
    snap = await snapshot(api, voice_ctx, sess)
    start, snap = await record_once(api, voice_ctx, sess, snap)
    clock.advance(seconds=21)
    async with voice_ctx.session_factory() as db:
        job = await db.get(SpeechJob, uuid.UUID(start["utterance_id"]))
        job.data = {**job.data, "status": "empty"}
        await db.commit()
    retry = await snapshot(api, voice_ctx, sess)
    assert retry["state"] == "USER_READY"
    assert retry["blocked_reason"] == "empty"
    assert controller.at(retry["ready_at"]) > controller.at(start["ready_at"])
    turn = {"turn_id": retry["turn_id"], "epoch": retry["epoch"], "restart": True}
    response = await post(api, sess, "capture", turn)
    assert response.status_code == 200, response.text
    assert response.json()["utterance_id"] != start["utterance_id"]


async def test_proposal_voice_does_not_offer_a_turn(api, voice_ctx):
    sess = await setup(api, voice_ctx)
    async with voice_ctx.session_factory() as db:
        session = await db.get(Session, sess.id)
        session.phase = "proposal"
        session.generation += 1
        await db.commit()
    snap = await snapshot(api, voice_ctx, sess)
    assert snap["state"] != "USER_READY"
    assert "turn_until_at" not in snap
    assert (
        await post(api, sess, "capture", {"turn_id": snap["turn_id"], "epoch": snap["epoch"]})
    ).status_code == 409


async def test_solution_turns_then_consensus_closes_microphone(api, voice_ctx, clock):
    """카드 뒤 12초 간격의 두 발언은 가능하고, 해결책 확정 뒤에는 새 턴이 없다."""
    ctx = voice_ctx
    sess = await setup(api, ctx)
    async with ctx.session_factory() as db:
        session = await db.get(Session, sess.id)
        session.phase = "solution"
        session.generation += 1
        await db.commit()
    await api.post_ai(sess, "p3", "다른 해결책도 생각해 봐요", card_ids=["persuasion_solution"])
    await snapshot(api, ctx, sess)  # 첫 조회에서 합성을 예약한다.
    snap = await snapshot(api, ctx, sess)
    item = snap["items"][0]
    assert item["status"] == "ready"
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"], "job_id": item["id"]}
    for outcome in ("start", "ended"):
        assert (await post(api, sess, "playback", {**turn, "outcome": outcome})).status_code == 200

    for index in range(2):
        snap = await snapshot(api, ctx, sess)
        assert snap["state"] == "USER_READY"
        assert snap["phase"] == "solution"
        assert not snap.get("release_ready")
        start, snap = await record_once(api, ctx, sess, snap)
        await handle_send(
            ctx,
            sess.id,
            {
                "client_msg_id": start["utterance_id"],
                "content": snap["utterance"]["transcript"],
                "voice_turn_id": snap["turn_id"],
                "epoch": snap["epoch"],
            },
        )
        await ctx.mailboxes.drain()
        if index == 0:
            clock.advance(seconds=12)

    response = await api.confirm(sess, "solution", value="빗물 저금통·투수 포장 같은 생활 인프라")
    assert response.status_code == 200, response.text
    snap = await snapshot(api, ctx, sess)
    assert snap["phase"] == "proposal"
    assert snap["state"] != "USER_READY"
    assert "turn_until_at" not in snap
    assert (
        await post(api, sess, "capture", {"turn_id": snap["turn_id"], "epoch": snap["epoch"]})
    ).status_code == 409


async def test_proposal_plays_queued_previous_phase_utterance(api, voice_ctx):
    sess = await setup(api, voice_ctx)
    message = await api.post_ai(sess, "p1", "앞 단계에서 정리한 내용이에요")
    await snapshot(api, voice_ctx, sess)
    async with voice_ctx.session_factory() as db:
        session = await db.get(Session, sess.id)
        session.phase = "proposal"
        session.generation += 1
        await db.commit()
    snap = await snapshot(api, voice_ctx, sess)
    assert snap["items"][0]["message_id"] == message["id"]
    body = {"turn_id": snap["turn_id"], "epoch": snap["epoch"], "job_id": snap["items"][0]["id"]}
    for outcome in ("start", "ended"):
        assert (await post(api, sess, "playback", {**body, "outcome": outcome})).status_code == 200
    assert message["id"] in (await snapshot(api, voice_ctx, sess))["heard_message_ids"]


async def record_once(api, ctx, sess, snap):
    """발언권을 받아 무음 파일을 올리고 받아 적기까지 마친 상태를 돌려준다."""
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    start = (await post(api, sess, "capture", turn)).json()
    body = {
        **turn,
        "utterance_id": start["utterance_id"],
        "audio_base64": base64.b64encode(silence()).decode(),
        "stop_reason": "button",
    }
    assert (await post(api, sess, "audio", body)).status_code == 200
    await ctx.drain_spawned()
    return start, await snapshot(api, ctx, sess)


async def test_voice_rerecord_once_with_fresh_time(api, voice_ctx, clock):
    """받아 적은 글은 3초 확인 뒤 자동 제출 기준을 주고, 다시 녹음은 한 번만 새 60초로 허용한다."""
    ctx = voice_ctx
    sess = await setup(api, ctx)
    snap = await snapshot(api, ctx, sess)
    first, snap = await record_once(api, ctx, sess, snap)
    u = snap["utterance"]
    assert u["status"] == "ready"
    # 채용 건의 동료 이름이 받아 적기 구문 목록으로 넘어간다(이름 오인식 → 호명 누락 방지)
    assert {"윤하람", "하람님", "한결님", "지안님"} <= set(ctx.speech.phrases)
    wait = (controller.at(u["confirm_deadline_at"]) - ctx.clock.now()).total_seconds()
    assert 7.9 < wait <= 8  # 기본 확인 8초. 테스트 시계는 호출마다 마이크로초씩 흐른다
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    r = await post(api, sess, "rerecord", {**turn, "job_id": u["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "USER_READY"
    clock.advance(seconds=50)  # 실제 handoff 전에는 유지 시간이 지나도 다시 녹음할 수 있다
    second, snap = await record_once(api, ctx, sess, r.json())
    started = controller.at(second["speech_started_at"])
    assert (controller.at(second["speech_deadline_at"]) - started).total_seconds() == 60
    assert second["utterance_id"] != first["utterance_id"]
    r = await post(api, sess, "rerecord", {**turn, "job_id": snap["utterance"]["id"]})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "voice_retry_exhausted"


async def test_voice_zero_speakers_stale_and_audio_validation(api, voice_ctx, clock):
    sess = await setup(api, voice_ctx)
    snap = await snapshot(api, voice_ctx, sess)
    assert snap["state"] == "USER_READY" and snap["items"] == []
    turn = {"turn_id": snap["turn_id"], "epoch": "test"}
    start = (await post(api, sess, "capture", turn)).json()
    assert (
        await post(
            api,
            sess,
            "audio",
            {
                **turn,
                "utterance_id": start["utterance_id"],
                "audio_base64": "AAAA",
                "stop_reason": "timeout",
            },
        )
    ).status_code == 422
    async with voice_ctx.session_factory() as db:
        s = await db.get(Session, sess.id)
        s.phase_deadline_at = voice_ctx.clock.now() - timedelta(seconds=1)
        await db.commit()
    assert (
        await post(
            api,
            sess,
            "audio",
            {
                **turn,
                "utterance_id": start["utterance_id"],
                "audio_base64": base64.b64encode(silence()).decode(),
                "stop_reason": "timeout",
            },
        )
    ).status_code == 409


async def test_voice_quota_refuses_before_provider_and_other_session(api, voice_ctx):
    sess = await setup(api, voice_ctx)
    async with voice_ctx.session_factory() as db:
        s = await db.get(Session, sess.id)
        with pytest.raises(ServiceError) as error:
            await quota.consume(db, s, "stt", 100000, voice_ctx.clock.now())
        assert error.value.code == "voice_quota_exhausted"
    assert not voice_ctx.speech.calls
    r = await api.public.get(f"/sessions/{uuid.uuid4()}/voice", headers=sess.auth)
    assert r.status_code == 403


def test_pcm_rejects_truncated_and_long_audio():
    assert pcm(silence(60))[1] == 60
    with pytest.raises(ServiceError):
        pcm(silence(61))
    with pytest.raises(ServiceError):
        pcm(silence()[:-20])


async def test_azure_paid_sku_rejected_before_synthesis(monkeypatch):
    from types import SimpleNamespace

    import azure.identity
    import httpx
    from app.voice.speech import VOICES, AzureSpeech

    class Credential:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_token(self, *args):
            return SimpleNamespace(token="fake-access")

    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", Credential)
    monkeypatch.setenv("AZURE_SPEECH_KEY", "fake-key")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "koreacentral")
    monkeypatch.setenv(
        "AZURE_SPEECH_RESOURCE_ID",
        "/subscriptions/test/resourceGroups/test/providers/Microsoft.CognitiveServices/accounts/test",
    )
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"key1": "fake-key"}
            if request.url.path.endswith("listKeys")
            else {"sku": {"name": "S0"}, "location": "koreacentral"},
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    with pytest.raises(ServiceError) as error:
        await AzureSpeech().synthesize("안녕하세요", VOICES["p1"])
    assert error.value.code == "voice_unavailable"
    assert len(requests) == 2
    assert all(r.url.host == "management.azure.com" for r in requests)
    assert len(set(VOICES.values())) == 3


async def test_azure_fast_transcription_request(monkeypatch):
    """받아 적기는 F0 확인 뒤 빠른 받아 적기 한 번 호출로 끝나고 이름 구문 목록을 함께 보낸다."""
    import json
    from types import SimpleNamespace

    import azure.identity
    import httpx
    from app.voice.speech import FAST_TRANSCRIPTION_API, AzureSpeech

    class Credential:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_token(self, *args):
            return SimpleNamespace(token="fake-access")

    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", Credential)
    monkeypatch.setenv("AZURE_SPEECH_KEY", "fake-key")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "koreacentral")
    monkeypatch.setenv(
        "AZURE_SPEECH_RESOURCE_ID",
        "/subscriptions/test/resourceGroups/test/providers/Microsoft.CognitiveServices/accounts/test",
    )
    requests = []
    fast_status = 200

    def respond(request):
        requests.append(request)
        if request.url.host == "management.azure.com":
            if request.url.path.endswith("listKeys"):
                return httpx.Response(200, json={"key1": "fake-key"})
            return httpx.Response(200, json={"sku": {"name": "F0"}, "location": "koreacentral"})
        if fast_status != 200:
            return httpx.Response(fast_status, json={"error": {"code": "NotSupported"}})
        return httpx.Response(200, json={"combinedPhrases": [{"text": "하람님 의견은요?"}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    text = await AzureSpeech().transcribe(silence(), ["윤하람", "하람님"])
    assert text == "하람님 의견은요?"
    stt = requests[-1]
    assert stt.url.host == "koreacentral.api.cognitive.microsoft.com"
    assert stt.url.path == "/speechtotext/transcriptions:transcribe"
    assert stt.url.params["api-version"] == FAST_TRANSCRIPTION_API
    body = stt.content.decode("utf-8", "replace")
    definition = json.loads(
        body.split('name="definition"')[1].split("\r\n\r\n", 1)[1].split("\r\n--")[0]
    )
    assert definition == {"locales": ["ko-KR"], "phraseList": {"phrases": ["윤하람", "하람님"]}}
    # 빠른 받아 적기가 거절되면 같은 파일을 연속 인식(예비 경로)으로 받아 적는다
    fast_status = 403
    fallback = []

    async def continuous(self, frames, seconds, phrases):
        fallback.append(list(phrases))
        return "연속 인식 결과"

    monkeypatch.setattr(AzureSpeech, "_transcribe_continuous", continuous)
    assert await AzureSpeech().transcribe(silence(), ["윤하람"]) == "연속 인식 결과"
    assert fallback == [["윤하람"]]


def tone(seconds: float, loud: bool = True) -> bytes:
    """PCM16 mono 16kHz 원본 소리. loud=False면 무음(말이 멈춘 곳)."""
    import array
    import math

    n = int(seconds * 16000)
    amp = 8000 if loud else 0
    return array.array("h", (int(amp * math.sin(i / 8)) for i in range(n))).tobytes()


class SegmentSpeech(FakeSpeech):
    """소리가 있으면 "소리", 무음이면 "무음"을 돌려주는 가짜 받아 적기(조각 경로 확인용)."""

    def __init__(self):
        super().__init__()
        self.stt_calls: list[float] = []

    async def transcribe(self, audio, phrases=()):
        frames, seconds = pcm(audio)
        self.stt_calls.append(round(seconds, 2))
        return "소리" if any(frames) else "무음"


def test_live_cut_lands_in_pause(monkeypatch):
    from app.voice import live

    frames = tone(7) + tone(0.5, loud=False) + tone(3)
    cut = live.quietest_cut(frames, 6 * 32000, len(frames), 0.3)
    assert 7 * 32000 <= cut <= 7.5 * 32000


async def test_live_segments_then_tail_in_order(monkeypatch):
    """10초가 쌓이면 쉬는 곳에서 잘라 미리 보내고, 끝나면 꼬리만 보낸 뒤 순서대로 잇는다."""
    from app.voice import live

    monkeypatch.setenv("VOICE_SEGMENT_SECONDS", "10")
    speech = SegmentSpeech()
    seg = live.Segmenter("s", speech, ["하람님"])
    audio = tone(7) + tone(0.5, loud=False) + tone(4.5)
    for seq, i in enumerate(range(0, len(audio), 16000)):
        seg.feed(seq, audio[i : i + 16000])
    assert len(seg.tasks) == 1  # 10초가 되는 순간 한 번 잘림
    text = await seg.finish(12)
    assert text == "소리 소리"
    assert 7 <= speech.stt_calls[0] <= 7.5 and sum(speech.stt_calls) == 12


async def test_live_gap_or_length_mismatch_falls_back():
    from app.voice import live

    seg = live.Segmenter("s", SegmentSpeech(), [])
    seg.feed(0, tone(0.5))
    seg.feed(2, tone(0.5))  # 1번 조각이 빠짐
    with pytest.raises(live.Fallback):
        await seg.finish(1)
    seg = live.Segmenter("s", SegmentSpeech(), [])
    seg.feed(0, tone(0.5))
    with pytest.raises(live.Fallback):
        await seg.finish(5)  # 완성 파일은 5초인데 0.5초만 받음


async def test_voice_stream_chunks_used_for_transcript(api, voice_ctx):
    """녹음 중에 보낸 조각으로 받아 적고, 완성 파일 전체는 다시 받아 적지 않는다."""
    ctx = voice_ctx
    speech = SegmentSpeech()
    ctx.speech = speech
    sess = await setup(api, ctx)
    snap = await snapshot(api, ctx, sess)
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    start = (await post(api, sess, "capture", turn)).json()
    audio = tone(3)
    for seq, i in enumerate(range(0, len(audio), 16000)):
        body = {
            **turn,
            "utterance_id": start["utterance_id"],
            "seq": seq,
            "pcm_base64": base64.b64encode(audio[i : i + 16000]).decode(),
        }
        assert (await post(api, sess, "stream", body)).status_code == 200
    # 완성 파일은 같은 길이의 무음: 조각 경로를 쓰면 "소리", 파일 전체로 받아 적으면 "무음"
    body = {
        **turn,
        "utterance_id": start["utterance_id"],
        "audio_base64": base64.b64encode(silence(3)).decode(),
        "stop_reason": "button",
    }
    assert (await post(api, sess, "audio", body)).status_code == 200
    await ctx.drain_spawned()
    snap = await snapshot(api, ctx, sess)
    assert snap["utterance"]["transcript"] == "소리"
    assert speech.stt_calls == [3.0]


async def ready_two(api, ctx, sess, card_second: bool = False):
    """동료 발언 두 개(p1, p3)를 합성까지 끝낸 재생 대기 상태."""
    first = await api.post_ai(sess, "p1", "물 부족을 생각해 봐요")
    extra = {"card_ids": ["listening_pressure"]} if card_second else {}
    second = await api.post_ai(sess, "p3", "다른 의견도 있어요", seq=2, **extra)
    await snapshot(api, ctx, sess)
    snap = await snapshot(api, ctx, sess)
    assert snap["state"] == "AI_SPEAKING"
    return first, second, snap


async def test_voice_playback_end_after_phase_change_counts_as_heard(api, voice_ctx):
    """재생 중에 단계가 바뀌어도, 끝까지 재생된 발언은 들은 것으로 기록하고 거절하지 않는다."""
    ctx = voice_ctx
    sess = await setup(api, ctx)
    first, _second, snap = await ready_two(api, ctx, sess)
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    playing = snap["items"][0]["id"]
    body = {**turn, "job_id": playing, "outcome": "start"}
    assert (await post(api, sess, "playback", body)).status_code == 200
    async with ctx.session_factory() as db:  # 재생 중 시간 초과로 다음 단계가 시작됨
        s = await db.get(Session, sess.id)
        s.phase, s.generation = "problem", s.generation + 1
        await db.commit()
    body = {**turn, "job_id": playing, "outcome": "ended"}
    assert (await post(api, sess, "playback", body)).status_code == 200
    async with ctx.session_factory() as db:
        assert (await db.get(SpeechJob, uuid.UUID(playing))).data["status"] == "heard"
    snap = await snapshot(api, ctx, sess)
    assert first["id"] in snap["heard_message_ids"]


async def test_voice_hand_raise_takes_turn_after_current(api, voice_ctx):
    """손들면 지금 발언은 끝까지 듣고, 줄 선 발언은 취소된 뒤 내 차례(생각 시간 뒤 자동 녹음)."""
    ctx = voice_ctx
    sess = await setup(api, ctx)
    first, _second, snap = await ready_two(api, ctx, sess)
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    playing = snap["items"][0]["id"]
    body = {**turn, "job_id": playing, "outcome": "start"}
    assert (await post(api, sess, "playback", body)).status_code == 200
    hand = {**turn, "during_message_id": first["id"], "offset_ms": 1200}
    assert (await post(api, sess, "hand", hand)).status_code == 200
    snap = await snapshot(api, ctx, sess)
    assert snap["state"] == "AI_SPEAKING"  # 지금 발언은 끊지 않는다
    body = {**turn, "job_id": playing, "outcome": "ended"}
    assert (await post(api, sess, "playback", body)).status_code == 200
    snap = await snapshot(api, ctx, sess)
    assert snap["state"] == "USER_READY"
    assert snap["priority"] == "hand"
    assert snap["items"] == []
    assert snap["heard_message_ids"] == [first["id"]]
    wait = controller.at(snap["think_deadline_at"]) - controller.at(snap["ready_at"])
    assert wait.total_seconds() == 5  # 손들기도 생각 시간(기본 5초) 뒤 자동 녹음
    start, _ = await record_once(api, ctx, sess, snap)
    async with ctx.session_factory() as db:
        job = await db.get(SpeechJob, uuid.UUID(start["utterance_id"]))
        assert job.data["turn_kind"] == "hand"
        assert job.data["hand"]["during_message_id"] == first["id"]
        assert job.data["hand"]["offset_ms"] == 1200


async def test_voice_hand_raise_keeps_card_utterance(api, voice_ctx):
    """손들어도 카드 발언(평가 장면)은 건너뛰지 않고 들은 뒤에 내 차례가 된다."""
    ctx = voice_ctx
    sess = await setup(api, ctx)
    _first, second, snap = await ready_two(api, ctx, sess, card_second=True)
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    assert (await post(api, sess, "hand", turn)).status_code == 200
    snap = await snapshot(api, ctx, sess)
    assert snap["state"] == "AI_SPEAKING"
    assert [i["message_id"] for i in snap["items"]] == [second["id"]]


async def test_voice_hand_turn_not_stolen_and_stale_not_replayed(api, voice_ctx):
    """손들기 차례는 새 동료 발언이 빼앗지 못하고, 내 말 뒤에 그 전 발언을 재생하지 않는다."""
    ctx = voice_ctx
    sess = await setup(api, ctx)
    _first, _second, snap = await ready_two(api, ctx, sess)
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    assert (await post(api, sess, "hand", turn)).status_code == 200
    snap = await snapshot(api, ctx, sess)
    assert snap["state"] == "USER_READY"
    late = await api.post_ai(sess, "p2", "저도 하나 있긴 한데요", seq=3)
    snap = await snapshot(api, ctx, sess)
    assert snap["state"] == "USER_READY"  # 새 발언이 차례를 빼앗지 않는다
    start, snap = await record_once(api, ctx, sess, snap)
    payload = {
        "client_msg_id": start["utterance_id"],
        "content": snap["utterance"]["transcript"],
        "voice_turn_id": snap["turn_id"],
        "epoch": snap["epoch"],
    }
    await handle_send(ctx, sess.id, payload)
    await ctx.mailboxes.drain()
    await snapshot(api, ctx, sess)
    snap = await snapshot(api, ctx, sess)
    assert late["id"] not in [i["message_id"] for i in snap["items"]]
    async with ctx.session_factory() as db:
        job = await db.get(SpeechJob, uuid.uuid5(uuid.UUID(late["id"]), "tts"))
        assert job.data["status"] == "canceled"
        assert job.data["error"] == "superseded"


async def near_deadline(ctx, sess, seconds: float):
    async with ctx.session_factory() as db:
        s = await db.get(Session, sess.id)
        s.phase_deadline_at = ctx.clock.now() + timedelta(seconds=seconds)
        await db.commit()


async def test_voice_forced_turn_before_phase_ends(api, voice_ctx):
    """이 단계 지원자 발언이 없고 마감까지 105초(30+5+60+10) 이하면 동료 발언을 넘기고 강제한다."""
    ctx = voice_ctx
    sess = await setup(api, ctx)
    await ready_two(api, ctx, sess)
    await near_deadline(ctx, sess, 110)
    snap = await snapshot(api, ctx, sess)
    assert not snap.get("priority")  # 아직 105초보다 많이 남음
    await near_deadline(ctx, sess, 100)
    snap = await snapshot(api, ctx, sess)
    assert snap["state"] == "USER_READY"
    assert snap["priority"] == "forced"
    assert snap["items"] == []
    start, _ = await record_once(api, ctx, sess, snap)
    async with ctx.session_factory() as db:
        job = await db.get(SpeechJob, uuid.UUID(start["utterance_id"]))
        assert job.data["turn_kind"] == "forced"


async def test_voice_short_turn_near_deadline_is_announced(api, voice_ctx):
    """마감이 가까워 60초를 못 쓰면 녹음 전에 말할 수 있는 초를 알리고 녹음도 그만큼 준다(V4)."""
    ctx = voice_ctx
    sess = await setup(api, ctx)
    await near_deadline(ctx, sess, 30)
    snap = await snapshot(api, ctx, sess)
    assert snap["priority"] == "forced"
    assert snap["speech_limit_seconds"] in (14, 15)  # 생각 5초 뒤 시작, 마감 10초 전까지
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    start = (await post(api, sess, "capture", turn)).json()
    given = controller.at(start["speech_deadline_at"]) - controller.at(start["speech_started_at"])
    assert given.total_seconds() <= 30


async def test_voice_recording_cut_by_deadline_still_submits(api, voice_ctx, clock):
    """마감 직전 녹음은 마감 10초 전에 끝나서, 녹음 파일이 마감 전에 도착해 그 단계 발언이 된다."""
    ctx = voice_ctx
    sess = await setup(api, ctx)
    await near_deadline(ctx, sess, 30)
    snap = await snapshot(api, ctx, sess)
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    start = (await post(api, sess, "capture", turn)).json()
    async with ctx.session_factory() as db:
        phase_deadline = (await db.get(Session, sess.id)).phase_deadline_at
    end = controller.at(start["speech_deadline_at"])
    assert phase_deadline - end >= timedelta(seconds=10)  # 받아 적기·확인·제출 여유
    # 녹음 시간이 끝나 자동으로 멈추고, 파일은 조금 늦게(2초 뒤) 도착한다
    clock.advance(seconds=(end - clock.now()).total_seconds() + 2)
    body = {
        **turn,
        "utterance_id": start["utterance_id"],
        "audio_base64": base64.b64encode(silence()).decode(),
        "stop_reason": "timeout",
    }
    assert (await post(api, sess, "audio", body)).status_code == 200


async def test_voice_no_forced_turn_after_candidate_spoke(api, voice_ctx):
    """이 단계에 이미 말했으면(일반 차례든 손들기든) 마감이 가까워도 강제하지 않는다."""
    ctx = voice_ctx
    sess = await setup(api, ctx)
    snap = await snapshot(api, ctx, sess)
    start, snap = await record_once(api, ctx, sess, snap)
    payload = {
        "client_msg_id": start["utterance_id"],
        "content": snap["utterance"]["transcript"],
        "voice_turn_id": snap["turn_id"],
        "epoch": snap["epoch"],
    }
    await handle_send(ctx, sess.id, payload)
    await ctx.mailboxes.drain()
    await near_deadline(ctx, sess, 70)
    await api.post_ai(sess, "p1", "그럼 이건 어때요", seq=4)
    await snapshot(api, ctx, sess)
    snap = await snapshot(api, ctx, sess)
    assert snap["state"] == "AI_SPEAKING"
    assert not snap.get("priority")


def test_quota_scope_auto_uses_utc_month(monkeypatch):
    from datetime import UTC, datetime

    monkeypatch.setenv("AZURE_SPEECH_RESOURCE_ID", "res")
    monkeypatch.setenv("VOICE_QUOTA_PERIOD", "auto")
    jan = quota.scope(datetime(2026, 1, 31, 23, 59, tzinfo=UTC))
    feb = quota.scope(datetime(2026, 2, 1, 0, 0, tzinfo=UTC))
    assert (jan, feb) == ("res:2026-01", "res:2026-02")
    monkeypatch.setenv("VOICE_QUOTA_PERIOD", "fixed-id")
    assert quota.scope(datetime(2026, 2, 1, tzinfo=UTC)) == "res:fixed-id"
    monkeypatch.setenv("VOICE_QUOTA_PERIOD", "")
    with pytest.raises(ServiceError):
        quota.scope(datetime(2026, 2, 1, tzinfo=UTC))
