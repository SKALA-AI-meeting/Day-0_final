"""로컬 읽기 공급자 Supertonic과 자리별 목소리. 모델 내려받기·적재 없음(live 제외)."""

import io
import os
import wave
from datetime import UTC, datetime

import numpy as np
import pytest
from app.services.common import ServiceError
from app.voice import policy, quota, speech
from app.voice.speech import (
    GEMINI_VOICES,
    SUPERTONIC_VOICES,
    VOICES,
    SplitSpeech,
    SupertonicSpeech,
    WhisperSpeech,
    provider_label,
)


@pytest.fixture
def local_env(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "gemini")
    monkeypatch.setenv("VOICE_STT_PROVIDER", "whisper")
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "supertonic")
    monkeypatch.setenv("VOICE_QUOTA_PERIOD", "auto")


def test_local_split_policy_and_ledger(local_env):
    sp = speech.provider()
    assert isinstance(sp, SplitSpeech)
    assert isinstance(sp.stt, WhisperSpeech) and isinstance(sp.tts, SupertonicSpeech)
    assert provider_label() == "whisper+supertonic"
    pinned = policy.pinned_policy()
    # 둘 다 로컬이라 과금 공급자가 없다: 예산 0, F0 표시 없음, 목소리는 Supertonic
    assert pinned["provider"] == "whisper+supertonic"
    assert pinned["version"] == "whisper+supertonic-v1"
    assert pinned["voices"] == SUPERTONIC_VOICES
    assert pinned["sku"] is None and pinned["paid_budget_usd"] == 0
    assert quota.scope(datetime(2026, 10, 25, tzinfo=UTC)) == "whisper+supertonic:2026-10"


def test_supertonic_cannot_transcribe(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "gemini")
    monkeypatch.setenv("VOICE_STT_PROVIDER", "supertonic")
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "gemini")
    with pytest.raises(ServiceError):
        speech.provider()


def test_seat_voices_match_profile_gender():
    # p1 강한결 남성, p2 윤하람·p3 서지안 여성(프로필 사진)
    assert SUPERTONIC_VOICES == {"p1": "M1", "p2": "F1", "p3": "F2"}
    assert GEMINI_VOICES["p3"] == "Leda" and VOICES["p3"] == "ko-KR-JiMinNeural"
    for voices in (SUPERTONIC_VOICES, GEMINI_VOICES, VOICES):
        assert len(set(voices.values())) == 3


async def test_supertonic_returns_pcm16_wav(monkeypatch):
    seen = {}

    class Engine:
        sample_rate = 44100

        def synthesize(self, text, **kwargs):
            seen.update(kwargs, text=text)
            wav = np.full((1, 4410), 0.5, np.float32)
            wav[0, 0] = 2.0  # 범위를 넘는 표본은 잘라 낸다
            return wav, np.array([0.1])

    styles = {v: f"style-{v}" for v in SUPERTONIC_VOICES.values()}
    monkeypatch.setattr(SupertonicSpeech, "_engine", lambda self: (Engine(), styles))
    monkeypatch.setenv("SUPERTONIC_STEPS", "")  # 빈 값은 기본값(5)
    audio = await SupertonicSpeech().synthesize("저는 물이요.", SUPERTONIC_VOICES["p3"])
    with wave.open(io.BytesIO(audio), "rb") as src:
        assert (src.getnchannels(), src.getsampwidth(), src.getframerate()) == (1, 2, 44100)
        assert src.getnframes() == 4410
        frames = np.frombuffer(src.readframes(4410), "<i2")
    assert frames[0] == 32767 and frames[1] == 16383
    assert seen["voice_style"] == "style-F2" and seen["lang"] == "ko"
    assert seen["total_steps"] == 5 and seen["text"] == "저는 물이요."


async def test_supertonic_rejects_other_voice_and_load_failure(monkeypatch):
    with pytest.raises(ServiceError) as error:
        await SupertonicSpeech().synthesize("안녕", "Kore")
    assert error.value.code == "voice_policy_mismatch"

    def broken(self):
        raise RuntimeError("download failed")

    monkeypatch.setattr(SupertonicSpeech, "_engine", broken)
    with pytest.raises(ServiceError) as error:
        await SupertonicSpeech().verify()
    assert error.value.code == "voice_unavailable"
    with pytest.raises(ServiceError):
        await SupertonicSpeech().transcribe(b"")


@pytest.mark.live
async def test_supertonic_live_synthesizes_korean(monkeypatch):
    """실제 모델(첫 실행은 내려받기). SUPERTONIC_CACHE가 없으면 사용자 캐시에 둔다."""
    monkeypatch.setenv(
        "SUPERTONIC_CACHE",
        os.getenv("SUPERTONIC_CACHE") or os.path.expanduser("~/.cache/supertonic3"),
    )
    audio = await SupertonicSpeech().synthesize("안녕하세요, 반갑습니다.", "F1")
    with wave.open(io.BytesIO(audio), "rb") as src:
        assert src.getnframes() / src.getframerate() > 0.5
