"""음성 완료 장벽·실제 저장 경로·원장 회귀. 클라우드를 호출하지 않는다."""

import base64
import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from app.models import Message, Session
from app.routers.ws import handle_send
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


async def test_voice_think_time_holds_turn(api, voice_ctx, clock):
    """내 차례에는 생각 시간(기본 30초) + 여유 10초 동안 차례를 반납하지 않는다."""
    sess = await setup(api, voice_ctx)
    snap = await snapshot(api, voice_ctx, sess)
    assert snap["state"] == "USER_READY"
    think = controller.at(snap["think_deadline_at"]) - controller.at(snap["ready_at"])
    assert think.total_seconds() == 30
    clock.advance(seconds=35)
    snap = await snapshot(api, voice_ctx, sess)
    assert not snap.get("release_ready")
    clock.advance(seconds=6)
    snap = await snapshot(api, voice_ctx, sess)
    assert snap["release_ready"] is True


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
    """받아 적은 글은 10초 확인 뒤 자동 제출 기준을 주고, 다시 녹음은 한 번만 새 60초로 허용한다."""
    ctx = voice_ctx
    sess = await setup(api, ctx)
    snap = await snapshot(api, ctx, sess)
    first, snap = await record_once(api, ctx, sess, snap)
    u = snap["utterance"]
    assert u["status"] == "ready"
    # 채용 건의 동료 이름이 받아 적기 구문 목록으로 넘어간다(이름 오인식 → 호명 누락 방지)
    assert {"윤하람", "하람님", "한결님", "지안님"} <= set(ctx.speech.phrases)
    wait = (controller.at(u["confirm_deadline_at"]) - ctx.clock.now()).total_seconds()
    assert 9.9 < wait <= 10  # 테스트 시계는 호출마다 마이크로초씩 흐른다
    turn = {"turn_id": snap["turn_id"], "epoch": snap["epoch"]}
    r = await post(api, sess, "rerecord", {**turn, "job_id": u["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "USER_READY"
    clock.advance(seconds=50)  # 앞 녹음 마감과 관계없이 60초를 새로 받는다
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
