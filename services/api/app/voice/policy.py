"""채용 건에 복사되는 음성 정책. 런타임에서 누락 정책을 env로 보충하지 않는다.

예전 채용 건에 남은 think_seconds(자동 녹음 대기)는 더 읽지 않는다. 모든 차례는 직접 눌러 시작한다.
"""

import os

from app.voice.speech import VOICES


def pinned_policy() -> dict:
    seconds = int(os.getenv("VOICE_USER_TURN_SECONDS", "60"))
    if not 1 <= seconds <= 60:
        raise ValueError("VOICE_USER_TURN_SECONDS must be 1..60")
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
        "version": "azure-f0-v1" if provider == "azure" else "fake-v1",
        "provider": provider,
        "user_turn_seconds": seconds,
        "turn_hold_seconds": hold,
        "confirm_seconds": confirm,
        "forced_wait_seconds": forced_wait,
        "voices": VOICES.copy(),
        "locale": "ko-KR",
        "sku": "F0",
        "paid_budget_usd": 0,
    }
