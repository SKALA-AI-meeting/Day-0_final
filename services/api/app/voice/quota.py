"""전용 F0 월 사용량과 시작 예약. 불확실한 호출은 사용량을 반환하지 않는다."""

import math
import os
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models import Session, SpeechQuota
from app.services.common import ServiceError

CAPS = {"tts": 400000, "stt": 14400}


def scope(now) -> str:
    # 경계에 두 달을 쓸 수 있는 문제를 피하려고 자원별 quota reset은 명시적으로 설정한다.
    period = os.getenv("VOICE_QUOTA_PERIOD", "")
    if not period:
        raise ServiceError(503, "voice_quota_unverified", "VOICE_QUOTA_PERIOD 확인이 필요합니다")
    return os.getenv("AZURE_SPEECH_RESOURCE_ID", "fake") + ":" + period


async def ledger(db, now):
    key = scope(now)
    await db.execute(insert(SpeechQuota).values(scope=key, data={}).on_conflict_do_nothing())
    return await db.scalar(select(SpeechQuota).where(SpeechQuota.scope == key).with_for_update())


async def admission(db, session, now):
    row = await ledger(db, now)
    data = dict(row.data)
    reservations = dict(data.get("reservations", {}))
    # 종료 세션의 미사용 예약만 반환한다. 사용량·unknown 호출은 유지한다.
    for sid in list(reservations):
        old = await db.get(Session, uuid.UUID(sid))
        if old is None or old.status not in ("invited", "in_progress"):
            reservations.pop(sid)
    key = str(session.id)
    if key not in reservations:
        if reservations and os.getenv("VOICE_PROVIDER", "azure") != "fake":
            raise ServiceError(409, "voice_capacity", "음성 시연은 한 번에 한 세션만 진행합니다")
        limits = (
            {"tts": 3000, "stt": 300}
            if session.ends_at and (session.ends_at - session.started_at).total_seconds() <= 900
            else {"tts": 12000, "stt": 1200}
        )
        for kind in CAPS:
            reserved = sum(b.get(kind, 0) for b in reservations.values())
            if data.get(kind, 0) + reserved + limits[kind] > CAPS[kind]:
                raise ServiceError(409, "voice_quota_exhausted", "월 음성 한도가 부족합니다")
        reservations[key] = {**limits, "tts_calls": 0, "stt_calls": 0}
    row.data = {**data, "reservations": reservations}


async def consume(db, session, kind, amount, now):
    row = await ledger(db, now)
    data = dict(row.data)
    reservations = dict(data.get("reservations", {}))
    budget = dict(reservations.get(str(session.id), {}))
    amount = math.ceil(amount)
    if amount <= 0 or budget.get(kind, 0) < amount:
        raise ServiceError(409, "voice_quota_exhausted", "음성 사용 한도를 소진했습니다")
    calls = kind + "_calls"
    maximum = 100 if kind == "tts" else 40
    if budget.get(calls, 0) >= maximum:
        raise ServiceError(409, "voice_quota_exhausted", "음성 호출 한도를 소진했습니다")
    times = [t for t in data.get("tts_times", []) if now.timestamp() - t < 60]
    if kind == "tts" and os.getenv("VOICE_PROVIDER", "azure") != "fake":
        if len(times) >= 15:
            raise ServiceError(
                429, "voice_rate_limited", "음성 생성 요청이 많습니다. 잠시 뒤 재시도하세요"
            )
        times.append(now.timestamp())
    budget[kind] -= amount
    budget[calls] = budget.get(calls, 0) + 1
    reservations[str(session.id)] = budget
    row.data = {
        **data,
        kind: data.get(kind, 0) + amount,
        "reservations": reservations,
        "tts_times": times,
    }
