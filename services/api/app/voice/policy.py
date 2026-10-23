"""채용 건에 복사되는 음성 정책. 런타임에서 누락 정책을 env로 보충하지 않는다."""

import os

from app.voice.speech import VOICES


def pinned_policy() -> dict:
    seconds = int(os.getenv("VOICE_USER_TURN_SECONDS", "60"))
    if not 1 <= seconds <= 60:
        raise ValueError("VOICE_USER_TURN_SECONDS must be 1..60")
    think = int(os.getenv("VOICE_THINK_SECONDS", "30"))
    if not 0 <= think <= 60:
        raise ValueError("VOICE_THINK_SECONDS must be 0..60")
    return {
        "version": "azure-f0-v1",
        "provider": os.getenv("VOICE_PROVIDER", "azure"),
        "user_turn_seconds": seconds,
        "think_seconds": think,
        "voices": VOICES.copy(),
        "locale": "ko-KR",
        "sku": "F0",
        "paid_budget_usd": 0,
    }
