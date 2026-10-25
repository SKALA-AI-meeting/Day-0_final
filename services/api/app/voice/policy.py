"""채용 건에 복사되는 음성 정책. 런타임에서 누락 정책을 env로 보충하지 않는다."""

import os

from app.voice.speech import voices_for


def pinned_policy() -> dict:
    seconds = int(os.getenv("VOICE_USER_TURN_SECONDS", "60"))
    if not 1 <= seconds <= 60:
        raise ValueError("VOICE_USER_TURN_SECONDS must be 1..60")
    think = int(os.getenv("VOICE_THINK_SECONDS", "5"))
    if not 0 <= think <= 60:
        raise ValueError("VOICE_THINK_SECONDS must be 0..60")
    hold = int(os.getenv("VOICE_TURN_HOLD_SECONDS", "20"))
    if not 5 <= hold <= 120:
        raise ValueError("VOICE_TURN_HOLD_SECONDS must be 5..120")
    confirm = int(os.getenv("VOICE_CONFIRM_SECONDS", "8"))
    if not 0 <= confirm <= 30:
        raise ValueError("VOICE_CONFIRM_SECONDS must be 0..30")
    forced_wait = int(os.getenv("VOICE_FORCED_WAIT_SECONDS", "30"))
    if not 0 <= forced_wait <= 120:
        raise ValueError("VOICE_FORCED_WAIT_SECONDS must be 0..120")
    provider = os.getenv("VOICE_PROVIDER", "azure")
    return {
        "version": {"azure": "azure-f0-v1", "openai": "openai-v1"}.get(provider, "fake-v1"),
        "provider": provider,
        "user_turn_seconds": seconds,
        "think_seconds": think,
        "turn_hold_seconds": hold,
        "confirm_seconds": confirm,
        "forced_wait_seconds": forced_wait,
        "voices": voices_for(provider),
        "locale": "ko-KR",
        # F0 무료 등급은 Azure만. OpenAI는 사용량 과금이라 예산을 정해 두지 않는다(한도는 quota.py)
        "sku": "F0" if provider == "azure" else None,
        "paid_budget_usd": None if provider == "openai" else 0,
    }
