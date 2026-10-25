"""Gemini 음성 공급자(VOICE_PROVIDER=gemini): 요청·응답 형식, 키 규칙, 정책. 실제 호출 없음."""

import base64
import io
import json
import wave

import httpx
import pytest
from app.services.common import ServiceError
from app.voice import policy, quota, speech
from app.voice.speech import GEMINI_VOICES, GeminiSpeech, silence, wav_from_pcm


def mock_gemini(monkeypatch, respond):
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


def audio_reply(data: bytes, mime: str):
    part = {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [part]}}]})


def test_provider_and_policy_follow_voice_provider(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "gemini")
    monkeypatch.setenv("VOICE_QUOTA_PERIOD", "auto")
    assert isinstance(speech.provider(), GeminiSpeech)
    pinned = policy.pinned_policy()
    assert pinned["version"] == "gemini-v1" and pinned["voices"] == GEMINI_VOICES
    assert pinned["sku"] is None and pinned["paid_budget_usd"] is None
    assert len(set(GEMINI_VOICES.values())) == 3
    from datetime import UTC, datetime

    assert quota.scope(datetime(2026, 10, 25, tzinfo=UTC)) == "gemini:2026-10"


async def test_gemini_verify_needs_gemini_key_and_model(monkeypatch):
    monkeypatch.setattr("app.agent.llm.roles._env", lambda env: env or {})
    with pytest.raises(ServiceError) as error:
        await GeminiSpeech().verify()
    assert error.value.code == "voice_unavailable"
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr("app.agent.llm.roles._env", lambda env: {"GEMINI_API_KEY": "fake-key"})
    await GeminiSpeech().verify()
    monkeypatch.setenv(
        "MODEL_STT", "gpt-4o-mini-transcribe"
    )  # Gemini 공급자에 다른 회사 모델은 안 된다
    with pytest.raises(ServiceError):
        await GeminiSpeech().verify()


async def test_gemini_transcription_request(monkeypatch):
    monkeypatch.setattr("app.agent.llm.roles._env", lambda env: {"GEMINI_API_KEY": "fake-key"})
    body = {"candidates": [{"content": {"parts": [{"text": ' "물이요" '}]}}]}
    requests = mock_gemini(monkeypatch, lambda r: httpx.Response(200, json=body))
    audio = silence(0.5)
    assert await GeminiSpeech().transcribe(audio, ["강한결"]) == "물이요"
    (req,) = requests
    assert req.url.path == "/v1beta/models/gemini-3.5-flash-lite:generateContent"
    assert req.headers["x-goog-api-key"] == "fake-key"
    sent = json.loads(req.content)
    prompt, media = sent["contents"][0]["parts"]
    assert "그대로 받아 적어라" in prompt["text"] and "강한결" in prompt["text"]
    assert base64.b64decode(media["inline_data"]["data"]) == audio
    assert sent["generationConfig"]["temperature"] == 0


async def test_gemini_transcription_without_candidates_is_empty(monkeypatch):
    monkeypatch.setattr("app.agent.llm.roles._env", lambda env: {"GEMINI_API_KEY": "fake-key"})
    mock_gemini(monkeypatch, lambda r: httpx.Response(200, json={"candidates": []}))
    assert await GeminiSpeech().transcribe(silence(0.2)) == ""


@pytest.mark.parametrize("wav_reply", [False, True])
async def test_gemini_speech_returns_wav(monkeypatch, wav_reply):
    monkeypatch.setattr("app.agent.llm.roles._env", lambda env: {"GEMINI_API_KEY": "fake-key"})
    raw = b"\x01\x00" * 24000  # 1초 분량 24kHz PCM16
    data, mime = (
        (wav_from_pcm(raw, 24000), "audio/wav")
        if wav_reply
        else (raw, "audio/L16;codec=pcm;rate=24000")
    )
    requests = mock_gemini(monkeypatch, lambda r: audio_reply(data, mime))
    audio = await GeminiSpeech().synthesize("안녕하세요", GEMINI_VOICES["p2"])
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert (wav.getframerate(), wav.getnframes()) == (24000, 24000)
    sent = json.loads(requests[0].content)
    # 기본은 가벼운 3.8 TTS이고, 지시문을 소리 내 읽는 모델이라 본문만 보낸다
    assert requests[0].url.path == "/v1beta/models/gemini-3.8-flash-lite-tts:generateContent"
    assert sent["contents"][0]["parts"][0]["text"] == "안녕하세요"
    config = sent["generationConfig"]
    assert config["responseModalities"] == ["AUDIO"]
    assert config["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore"


async def test_gemini_speech_rejects_unpinned_voice(monkeypatch):
    monkeypatch.setattr("app.agent.llm.roles._env", lambda env: {"GEMINI_API_KEY": "fake-key"})
    requests = mock_gemini(monkeypatch, lambda r: httpx.Response(200, json={}))
    with pytest.raises(ServiceError) as error:
        await GeminiSpeech().synthesize("안녕하세요", "ash")
    assert error.value.code == "voice_policy_mismatch" and requests == []


async def test_gemini_style_instruction_only_for_models_that_follow_it(monkeypatch):
    monkeypatch.setattr("app.agent.llm.roles._env", lambda env: {"GEMINI_API_KEY": "fake-key"})
    monkeypatch.setenv("MODEL_TTS", "gemini-2.5-flash-preview-tts")
    raw = b"\x01\x00" * 2400
    requests = mock_gemini(
        monkeypatch, lambda r: audio_reply(raw, "audio/L16;codec=pcm;rate=24000")
    )
    await GeminiSpeech().synthesize("안녕하세요", GEMINI_VOICES["p1"])
    text = json.loads(requests[0].content)["contents"][0]["parts"][0]["text"]
    assert text.endswith("안녕하세요") and "차분" in text
