"""음성 모드 설정과 공급자 초기화 경계. 외부 서비스는 호출하지 않는다."""

from dataclasses import replace

import pytest
from app.config import get_settings
from app.services.context import AppContext


def test_voice_off_requires_test_api(monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "0")
    monkeypatch.setenv("ENABLE_TEST_API", "0")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="VOICE_ENABLED=0 requires ENABLE_TEST_API=1"):
        get_settings()
    monkeypatch.setenv("ENABLE_TEST_API", "1")
    get_settings.cache_clear()
    assert get_settings().voice_enabled is False
    monkeypatch.setenv("VOICE_ENABLED", "false")
    get_settings.cache_clear()
    assert get_settings().voice_enabled is False
    get_settings.cache_clear()


def test_voice_off_does_not_construct_speech(monkeypatch, ctx):
    from app.voice import speech

    def forbidden():
        raise AssertionError("speech provider constructed")

    monkeypatch.setattr(speech, "provider", forbidden)
    settings = replace(ctx.settings, voice_enabled=False)
    copy = AppContext(
        settings=settings,
        session_factory=ctx.session_factory,
        clock=ctx.clock,
        publisher=ctx.publisher,
        runner=ctx.runner,
        classify=ctx.classify,
    )
    assert copy.speech is None
