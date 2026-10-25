"""받아 적기·읽기 공급자 분리와 로컬 Whisper. 외부 호출·모델 적재 없음."""

from types import SimpleNamespace

import pytest
from app.services.common import ServiceError
from app.voice import policy, quota, speech
from app.voice.speech import (
    GEMINI_VOICES,
    GeminiSpeech,
    SplitSpeech,
    WhisperSpeech,
    provider_label,
    silence,
)


@pytest.fixture
def split_env(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "gemini")
    monkeypatch.setenv("VOICE_STT_PROVIDER", "whisper")
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "gemini")
    monkeypatch.setenv("VOICE_QUOTA_PERIOD", "auto")


def test_split_provider_policy_and_ledger(split_env):
    sp = speech.provider()
    assert isinstance(sp, SplitSpeech)
    assert isinstance(sp.stt, WhisperSpeech) and isinstance(sp.tts, GeminiSpeech)
    assert provider_label() == "whisper+gemini"
    pinned = policy.pinned_policy()
    # 목소리는 읽기 공급자, 예산은 과금 공급자(Gemini)가 끼어 있어 정하지 않는다
    assert pinned["provider"] == "whisper+gemini" and pinned["version"] == "whisper+gemini-v1"
    assert pinned["voices"] == GEMINI_VOICES
    assert pinned["sku"] is None and pinned["paid_budget_usd"] is None
    from datetime import UTC, datetime

    assert quota.scope(datetime(2026, 10, 25, tzinfo=UTC)) == "whisper+gemini:2026-10"


def test_same_provider_keeps_single_adapter(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "gemini")
    monkeypatch.delenv("VOICE_STT_PROVIDER", raising=False)
    monkeypatch.delenv("VOICE_TTS_PROVIDER", raising=False)
    assert isinstance(speech.provider(), GeminiSpeech)
    assert provider_label() == "gemini"


def test_whisper_cannot_read_aloud(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "gemini")
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "whisper")
    with pytest.raises(ServiceError):
        speech.provider()


async def test_split_routes_calls_to_each_side():
    calls = []

    class Side:
        def __init__(self, name):
            self.name = name

        async def verify(self):
            calls.append((self.name, "verify"))

        async def synthesize(self, text, voice):
            calls.append((self.name, "tts"))
            return b"wav"

        async def transcribe(self, audio, phrases=()):
            calls.append((self.name, "stt"))
            return "글"

    sp = SplitSpeech(Side("stt"), Side("tts"))
    await sp.verify()
    assert await sp.synthesize("안녕", "Kore") == b"wav"
    assert await sp.transcribe(silence(0.1)) == "글"
    assert calls == [("stt", "verify"), ("tts", "verify"), ("tts", "tts"), ("stt", "stt")]


async def test_whisper_transcribes_pcm_with_names_prompt(monkeypatch):
    seen = {}

    class Model:
        def transcribe(self, audio, **kwargs):
            seen.update(kwargs, samples=len(audio), dtype=str(audio.dtype))
            return iter([SimpleNamespace(text=" 저는 "), SimpleNamespace(text="물이요")]), None

    monkeypatch.setattr(WhisperSpeech, "_model", lambda self: Model())
    text = await WhisperSpeech().transcribe(silence(0.5), ["강한결", "윤하람"])
    assert text == "저는 물이요"
    assert seen["samples"] == 8000 and seen["dtype"] == "float32"
    assert seen["language"] == "ko" and "강한결, 윤하람" in seen["initial_prompt"]


async def test_whisper_rejects_bad_audio_and_load_failure(monkeypatch):
    with pytest.raises(ServiceError):
        await WhisperSpeech().transcribe(b"not wav")

    def broken(self):
        raise RuntimeError("download failed")

    monkeypatch.setattr(WhisperSpeech, "_model", broken)
    with pytest.raises(ServiceError) as error:
        await WhisperSpeech().verify()
    assert error.value.code == "voice_unavailable"
    with pytest.raises(ServiceError):
        await WhisperSpeech().synthesize("안녕", "Kore")
