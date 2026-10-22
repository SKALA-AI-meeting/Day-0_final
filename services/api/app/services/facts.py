"""사실 지표(rubric.md 6장). 평가 결과 없이 기록만으로 규칙 계산한다. 사실만으로 수준을 정하지
않는다."""

import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AssistantCall,
    MaterialView,
    Message,
    MessageIntent,
    Option,
    Position,
    Proposal,
    Session,
    Slot,
    Stance,
)
from app.services import scenario as sc
from app.services.common import t_min
from app.services.views import gaps

INTENTS = ("propose", "agree", "oppose", "question", "other")


async def compute(db: AsyncSession, session: Session, position: Position) -> dict[str, Any]:
    scenario = position.scenario
    names = sc.seat_names(scenario)
    seat_by_type = {s["persona_type"]: s["seat"] for s in scenario["seats"]}
    room = (
        await db.scalars(
            select(Message)
            .where(Message.session_id == session.id, Message.channel == "room")
            .order_by(Message.created_at)
        )
    ).all()
    mine = [m for m in room if m.sender == "candidate"]
    intents = {
        i.message_id: i
        for i in (
            await db.scalars(select(MessageIntent).where(MessageIntent.session_id == session.id))
        ).all()
    }
    options = (await db.scalars(select(Option).where(Option.session_id == session.id))).all()
    slots = (await db.scalars(select(Slot).where(Slot.session_id == session.id))).all()
    confirmed = [s for s in slots if s.status == "confirmed"]
    adopted_ids = {s.option_id for s in confirmed if s.option_id}
    by_option = {o.id: o for o in options}

    counts = {p: 0 for p in sc.PHASES}
    for m in mine:
        counts[m.phase] = counts.get(m.phase, 0) + 1
    spontaneous = sum(1 for m in mine if m.origin == "spontaneous")
    invited = sum(1 for m in mine if m.origin == "invited")

    phase_start: dict[str, Any] = {}
    for m in room:
        if m.sender == "system" and (m.meta or {}).get("kind") == "stage_start":
            phase_start.setdefault(m.phase, m.created_at)
    first_sec: dict[str, int | None] = {}
    for phase in sc.DEBATE_PHASES:
        first = next((m for m in mine if m.phase == phase), None)
        start = phase_start.get(phase)
        first_sec[phase] = (
            int((first.created_at - start).total_seconds()) if first and start else None
        )
    latencies = []
    for prev, cur in zip(room, room[1:], strict=False):
        if cur.sender == "candidate" and prev.sender in names:
            latencies.append((cur.created_at - prev.created_at).total_seconds())

    mentions = {seat: 0 for seat in names}
    for m in mine:
        addressed = set(intents[m.id].addressed_to) if m.id in intents else set()
        for seat, name in names.items():
            if name in m.content or seat in addressed:
                mentions[seat] += 1

    intent_counts = {k: 0 for k in INTENTS}
    for i in intents.values():
        intent_counts[i.intent] += 1

    own = [o for o in options if o.proposer == "candidate"]
    stubborn = seat_by_type.get("stubborn")
    quiet = seat_by_type.get("quiet")
    stubborn_options = {o.id for o in options if o.proposer == stubborn}
    pressure = any(
        m.phase == "problem"
        and (
            names.get(stubborn, "#") in m.content
            or (m.id in intents and intents[m.id].target_option_id in stubborn_options)
            or (m.id in intents and stubborn in intents[m.id].addressed_to)
        )
        for m in mine
    )
    hesitation = next((m for m in room if "inclusion_silence" in (m.card_ids or [])), None)
    invite_latency = None
    if hesitation is not None:
        reply = next(
            (
                m
                for m in mine
                if m.created_at > hesitation.created_at
                and m.id in intents
                and quiet in intents[m.id].addressed_to
            ),
            None,
        )
        if reply is not None:
            invite_latency = int((reply.created_at - hesitation.created_at).total_seconds())
    quiet_messages = {m.id for m in room if m.sender == quiet and m.phase == "solution"}
    quiet_support = await db.scalar(
        select(Stance.id).where(
            Stance.session_id == session.id,
            Stance.participant == quiet,
            Stance.stance == "support",
            Stance.phase == "solution",
        )
    )
    quiet_spoke = bool(quiet_support) or any(o.source_message_id in quiet_messages for o in options)

    problem_slot = next((s for s in confirmed if s.phase == "problem"), None)
    own_problem = [o for o in own if o.phase == "problem"]
    rejected = None
    if own_problem:
        rejected = problem_slot is not None and problem_slot.option_id not in {
            o.id for o in own_problem
        }
    after_rejection = 0
    if problem_slot is not None:
        after_rejection = sum(
            1 for m in mine if m.created_at > problem_slot.confirmed_at and m.phase == "solution"
        )
    proposal = await db.get(Proposal, session.id)
    sections = (proposal.submitted or proposal.sections) if proposal else {}
    credit = (sections.get("idea_credit") or "").strip()
    calls = (
        await db.scalars(
            select(AssistantCall)
            .where(AssistantCall.session_id == session.id)
            .order_by(AssistantCall.created_at)
        )
    ).all()
    views = (
        await db.scalars(
            select(MaterialView)
            .where(MaterialView.session_id == session.id)
            .order_by(MaterialView.viewed_at)
        )
    ).all()
    silence: dict[str, int] = {p: 0 for p in sc.DEBATE_PHASES}
    for m in room:
        if m.is_invite and m.phase in silence:
            silence[m.phase] += 1
    total = len(mine)
    result = {
        "message_count": {"total": total, **{p: counts.get(p, 0) for p in sc.PHASES}},
        "spontaneous_count": spontaneous,
        "invited_count": invited,
        "spontaneous_ratio": round(spontaneous / total, 2) if total else 0.0,
        "silence_invites_received": silence,
        "first_message_sec": first_sec,
        "response_latency_median_sec": int(statistics.median(latencies)) if latencies else None,
        "avg_message_chars": int(sum(len(m.content) for m in mine) / total) if total else 0,
        "intent_counts": intent_counts,
        "name_mentions": mentions,
        "slot_records": [
            {
                "phase": s.phase,
                "option_id": str(s.option_id) if s.option_id else None,
                "filled_by": s.filled_by,
                "resolution": s.resolution,
                "support": s.support or [],
                "t_min": t_min(session, s.confirmed_at) or 0.0,
            }
            for s in sorted(confirmed, key=lambda s: sc.PHASES.index(s.phase))
        ],
        "own_options_proposed": len(own),
        "own_options_adopted": sum(1 for o in own if o.id in adopted_ids),
        "pressure_responded": pressure,
        "invite_latency_sec": invite_latency,
        "quiet_spoke": quiet_spoke,
        "own_option_rejected": rejected,
        "messages_after_rejection": after_rejection,
        "ai_adopted_units": [
            {
                "phase": by_option[oid].phase,
                "option_id": str(oid),
                "proposer": by_option[oid].proposer,
            }
            for oid in adopted_ids
            if oid in by_option and by_option[oid].proposer in ("p1", "p2", "p3")
        ],
        "credit_field_filled": bool(credit) and credit != "없음",
        "blank_sections_at_submit": (proposal.submit_blank_sections or []) if proposal else [],
        "blank_warning_acknowledged": proposal.blank_warning_shown if proposal else None,
        "assistant_calls": [
            {
                "call_id": str(c.id),
                "t_min": t_min(session, c.created_at) or 0.0,
                "cited_material_ids": list(c.cited_material_ids),
            }
            for c in calls
        ],
        "material_views": [
            {"material_id": v.material_id, "t_min": t_min(session, v.viewed_at) or 0.0}
            for v in views
        ],
        "connection_gaps": await gaps(db, session),
    }

    from app.models import SpeechJob, VoiceState

    if await db.get(VoiceState, session.id):
        jobs = (await db.scalars(select(SpeechJob).where(SpeechJob.session_id == session.id))).all()
        result["voice_usage"] = {
            "tts_characters": sum(
                sum(a["quantity"] for a in j.data.get("usage_attempts", []))
                for j in jobs
                if j.kind == "tts"
            ),
            "stt_seconds": sum(
                sum(a["quantity"] for a in j.data.get("usage_attempts", []))
                for j in jobs
                if j.kind == "stt"
            ),
            "unheard_messages": [
                j.data.get("message_id")
                for j in jobs
                if j.kind == "tts" and j.data.get("status") != "heard"
            ],
            "technical_failures": sum(
                j.data.get("status") in ("error", "canceled", "empty") for j in jobs
            ),
            "billing_unknown": sum(j.data.get("billing_status") == "unknown" for j in jobs),
            "net_cost_usd": 0,
            "notice": (
                "음성 전달·전사 장애와 처리 지연은 지원자 행동의 근거가 아니다. "
                "음색·발음·속도는 판단하지 않는다."
            ),
        }
    return result
