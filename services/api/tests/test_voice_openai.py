"""OpenAI 음성 공급자(VOICE_PROVIDER=openai): 요청 형식·키 규칙·정책. OpenAI를 호출하지 않는다."""

import io
import json
import wave

import httpx
import pytest
from app.agent.llm.roles import speech_key
from app.services.common import ServiceError
from app.voice import policy, quota, speech
from app.voice.speech import OPENAI_VOICES, OpenAISpeech, silence


def mock_openai(monkeypatch, respond):
    """httpx.AsyncClient를 가짜 전송으로 바꾸고 보낸 요청을 모은다"""
    requests = []

    def handler(request):
        requests.append(request)
        return respond(request)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return requests


def test_speech_models_use_restricted_key_allowlist():
    env = {"OPENAI_API_KEY": "restricted"}
    assert speech_key("gpt-4o-mini-transcribe", env) == "restricted"
    assert speech_key("gpt-4o-mini-tts", env) == "restricted"
    assert speech_key("whisper-1", env) == "restricted"
    with pytest.raises(ValueError, match="allowlist"):
        speech_key("gpt-4o-mini", env)  # LLM 모델은 음성 목록이 아니다
    with pytest.raises(ValueError, match="OPENAI_API_KEY is required"):
        speech_key("gpt-4o-mini-tts", {})


def test_provider_and_policy_follow_voice_provider(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "openai")
    monkeypatch.setenv("VOICE_QUOTA_PERIOD", "auto")
    assert isinstance(speech.provider(), OpenAISpeech)
    pinned = policy.pinned_policy()
    assert pinned["provider"] == "openai" and pinned["version"] == "openai-v1"
    assert pinned["voices"] == OPENAI_VOICES and len(set(OPENAI_VOICES.values())) == 3
    assert pinned["sku"] is None and pinned["paid_budget_usd"] is None
    from datetime import UTC, datetime

    assert quota.scope(datetime(2026, 10, 25, tzinfo=UTC)) == "openai:2026-10"


async def test_openai_verify_needs_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("app.agent.llm.roles._env", lambda env: env or {})
    with pytest.raises(ServiceError) as error:
        await OpenAISpeech().verify()
    assert error.value.code == "voice_unavailable"


async def test_openai_transcription_request(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    requests = mock_openai(monkeypatch, lambda r: httpx.Response(200, json={"text": " 물이요 "}))
    text = await OpenAISpeech().transcribe(silence(0.5), ["강한결", "윤하람"])
    assert text == "물이요"
    (req,) = requests
    assert req.url.path == "/v1/audio/transcriptions"
    assert req.headers["Authorization"] == "Bearer fake-key"
    body = req.content.decode("utf-8", "ignore")
    assert 'name="model"' in body and "gpt-4o-mini-transcribe" in body
    assert 'name="language"' in body and "강한결, 윤하람" in body
    assert 'filename="speech.wav"' in body


async def test_openai_transcription_rejects_bad_audio(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    requests = mock_openai(monkeypatch, lambda r: httpx.Response(200, json={"text": "x"}))
    with pytest.raises(ServiceError):
        await OpenAISpeech().transcribe(b"not wav")
    assert requests == []


async def test_openai_speech_returns_wav_with_real_length(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    raw = b"\x01\x00" * 24000  # 1초 분량 24kHz PCM16
    requests = mock_openai(monkeypatch, lambda r: httpx.Response(200, content=raw))
    audio = await OpenAISpeech().synthesize("안녕하세요", OPENAI_VOICES["p1"])
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24000)
        assert wav.getnframes() == 24000
    (req,) = requests
    sent = json.loads(req.content)
    assert req.url.path == "/v1/audio/speech"
    assert sent["voice"] == "ash" and sent["response_format"] == "pcm"
    assert sent["model"] == "gpt-4o-mini-tts" and "instructions" in sent


async def test_openai_speech_rejects_unpinned_voice(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    requests = mock_openai(monkeypatch, lambda r: httpx.Response(200, content=b""))
    with pytest.raises(ServiceError) as error:
        await OpenAISpeech().synthesize("안녕하세요", "ko-KR-InJoonNeural")
    assert error.value.code == "voice_policy_mismatch" and requests == []


async def test_openai_http_error_surfaces(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    mock_openai(monkeypatch, lambda r: httpx.Response(403, json={"error": {"message": "no"}}))
    with pytest.raises(httpx.HTTPStatusError):
        await OpenAISpeech().transcribe(silence(0.2))
