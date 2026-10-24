"""사실 지표(rubric.md 6장). 평가 결과 없이 기록만으로 규칙 계산한다. 사실만으로 수준을 정하지
않는다.

평가자 사실(`decisions`·`message_facts`, 결정 A1)은 SessionRecord 모양의 dict만 받는 순수 함수
`evaluator_facts`가 만든다. 평가셋 실행기(tests/eval)도 같은 함수로 픽스처 사실을 채운다.
"""

import re
import statistics
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AssistantCall,
    MaterialView,
    Message,
    MessageIntent,
    Observation,
    Option,
    Position,
    Proposal,
    Session,
    SessionCard,
    Slot,
    Stance,
)
from app.services import scenario as sc
from app.services.common import t_min
from app.services.views import gaps

INTENTS = ("propose", "agree", "oppose", "withhold", "question", "other")
AI_SEATS = ("p1", "p2", "p3")
# 붙여넣기 의심: 지원자 메시지의 글자 5-gram 중 앞선 어시스턴트 답에도 있는 비율. 짧은 메시지는
# 우연히 겹치므로 공백을 뺀 5-gram이 20개 이상인 메시지만 본다
PASTE_GRAM = 5
PASTE_MIN_GRAMS = 20
PASTE_OVERLAP = 0.6


# ---- 평가자 사실 (결정 A1, 순수 함수) ---------------------------------------------------------


def decision_path(slot: dict[str, Any]) -> str:
    """칸이 정해진 경로: 시간 초과 / 지원자 확정 / AI 동료 합의 채움."""
    if slot.get("resolution") == "timeout":
        return "timeout"
    return "candidate_confirm" if slot.get("filled_by") == "candidate" else "ai_consensus_fill"


def _earlier(a: dict[str, Any], b: dict[str, Any]) -> bool | None:
    """a가 b보다 먼저인지. 둘 다 `at`(세션 시작부터의 초, 반올림 없음)이 있으면 그 값, 아니면
    `t_min`으로 비교한다. 비교할 값이 없으면 None."""
    for key in ("at", "t_min"):
        if a.get(key) is not None and b.get(key) is not None:
            return a[key] < b[key]
    return None


def decision_facts(
    slots: list[dict[str, Any]],
    options: list[dict[str, Any]],
    lead_option_ids: Iterable[str] = (),
    card_schedule: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """확정 칸마다 결정 시각·경로·채운 자리·적용 규칙·확정 시점 지지 현황·지원자 안 채택 여부·
    선결정한 카드.

    slots는 SessionRecord `slots` 행(확정된 칸, 확정 시각 `at`(초)이 있으면 함께), options는
    Contributions `options` 행, lead_option_ids는 카드가 잠근 기준안
    (`session_cards.locked_option_id`),
    card_schedule은 카드 예정 시각 `{card_id, phase, at?, t_min, fired}`(`phase_starts` + 카드
    `offset_min`, `fired`는 관찰 기록이 있는지).
    `candidate_backed`: 정해진 안을 지원자가 처음 냈거나, 기준안이 아닌데 확정 시점에 지원자가
    지지함.
    `candidate_option_adopted`(rubric.md 3.4, 결정 A5): 지원자 안이고, AI 동료 합의 채움이거나
    지원자 확정이면 지원자 포함 지지 3명 이상. 시간 초과로 정해진 칸은 합의에 이르지 못한
    것이라 채택으로 보지 않는다. `timeout_rule`은 시간 초과 칸에 적용 규칙이 기록돼 있을 때만
    값이 있다.
    `preempted_cards`(결정 A3): 지원자가 직접 확정한 칸이고, 그 단계의 카드 중 발동하지 않은
    카드의 예정 시각보다 먼저 확정됨(그 카드는 단계가 끝나 생기지 않았다).
    """
    first = {o["option_id"]: o.get("first_proposer") for o in options}
    leads = set(lead_option_ids)
    schedule = list(card_schedule)
    order = {p: i for i, p in enumerate(sc.PHASES)}
    out = []
    for s in sorted(slots, key=lambda s: order.get(s["phase"], 99)):
        if s.get("t_min") is None:
            continue
        path = decision_path(s)
        support = s.get("support") or []
        chosen = s.get("option_id")
        backers = next(
            (g.get("supporters") or [] for g in support if g.get("option_id") == chosen), []
        )
        backed = first.get(chosen) == "candidate" or (
            "candidate" in backers and chosen not in leads
        )
        preempted = [
            c["card_id"]
            for c in schedule
            if path == "candidate_confirm"
            and c.get("phase") == s["phase"]
            and not c.get("fired")
            and _earlier(s, c)
        ]
        out.append(
            {
                "phase": s["phase"],
                "option_id": s.get("option_id"),
                "value": s.get("value"),
                "t_min": s["t_min"],
                "path": path,
                "filled_by": s.get("filled_by"),
                "timeout_rule": s.get("timeout_rule"),
                "support": support,
                "candidate_backed": backed,
                "candidate_option_adopted": backed
                and path != "timeout"
                and (path != "candidate_confirm" or len(backers) >= 3),
                "preempted_cards": preempted,
            }
        )
    return out


def _grams(text: str) -> set[str]:
    flat = re.sub(r"\s+", "", text or "").lower()
    return {flat[i : i + PASTE_GRAM] for i in range(len(flat) - PASTE_GRAM + 1)}


def paste_overlap(message: str, response: str) -> float:
    """지원자 메시지 글자 5-gram 중 어시스턴트 답에도 있는 비율. 짧은 메시지는 0."""
    grams = _grams(message)
    if len(grams) < PASTE_MIN_GRAMS:
        return 0.0
    return len(grams & _grams(response)) / len(grams)


def _when(row: dict[str, Any]) -> float | None:
    if row.get("at") is not None:
        return row["at"]
    return row.get("t_min")


def message_facts(
    messages: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """지원자 메시지마다 답한 대상·이미 정해진 칸·붙여넣기 의심.

    messages는 SessionRecord `messages`(저장 순서), calls는 `{call_id, t_min, response}`.
    순서는 반올림한 분이 아니라 저장 순서·단계로 정한다. 시각 비교가 필요한 붙여넣기만 `at`(세션
    시작부터의 초, 반올림 없음)이 있으면 그 값을, 없으면 `t_min`을 쓴다(메시지·호출 모두 같은 키).
    - `addressed_to`: 의도 분류가 기록한 부른 자리
    - `reply_to`: 부른 자리가 있으면 그 자리들의 직전 메시지, 없으면 직전 AI 동료 메시지
      (없으면 null)
    - `decided_before`: 이 메시지보다 먼저 확정된 칸의 단계. 칸 확정이 단계를 넘기므로 메시지의
      단계(`messages.phase`)보다 앞 단계의 확정 칸이다
    - `paste_suspect`: 이 메시지 전에 받은 어시스턴트 답과 글자가 많이 겹치면 `{call_id, overlap}`
    """
    order = {p: i for i, p in enumerate(sc.PHASES)}
    decided = [s["phase"] for s in slots if s.get("t_min") is not None and s.get("filled_by")]
    out = []
    for index, m in enumerate(messages):
        if m["sender"] != "candidate":
            continue
        addressed = list((m.get("intent") or {}).get("addressed_to") or [])
        targets = set(addressed) or set(AI_SEATS)
        reply = next((p["id"] for p in reversed(messages[:index]) if p["sender"] in targets), None)
        best = None
        for call in calls:
            if _when(call) is None or _when(call) >= _when(m):
                continue
            overlap = paste_overlap(m["content"], call.get("response") or "")
            if overlap >= PASTE_OVERLAP and (best is None or overlap > best["overlap"]):
                best = {"call_id": call["call_id"], "overlap": round(overlap, 2)}
        out.append(
            {
                "message_id": m["id"],
                "addressed_to": addressed,
                "reply_to": reply,
                "decided_before": sorted(
                    (p for p in decided if order[p] < order.get(m.get("phase"), 0)),
                    key=order.__getitem__,
                ),
                "paste_suspect": best,
            }
        )
    return out


def phase_starts(
    started_at: Any,
    confirmed_at: dict[str, Any],
    current_phase: str | None,
    current_started_at: Any,
) -> dict[str, Any]:
    """단계마다 실제 시작 시각. 카드 스케줄러가 읽는 `sessions.phase_started_at`과 같은 값을 쓴다.

    스케줄러는 지금 단계의 `phase_started_at`(단계 전환 때 `enter_phase`가 그 순간으로 둠)에
    카드 `offset_min`을 더해 카드 예정 시각을 정한다. 지나간 단계의 그 값은 남지 않으므로 같은
    전환 시각에서 다시 얻는다: 키워드는 세션 시작(`start`가 같은 순간 키워드 단계에 들어감), 그 뒤
    단계는 앞 단계 칸이 확정된 시각(칸 확정과 단계 전환이 한 요청 안에서 일어남), 지금 진행 중인
    단계는 `phase_started_at` 그대로. 단계 시작 안내 메시지의 저장 시각은 쓰지 않는다(전환과 별도
    저장이라 어긋날 수 있다). 테스트 API의 시간 당기기는 그 단계의 `phase_started_at`만 옮기므로
    지나간 단계에는 반영되지 않는다.
    """
    out: dict[str, Any] = {}
    if started_at is not None:
        out[sc.PHASES[0]] = started_at
    for before, phase in zip(sc.PHASES, sc.PHASES[1:], strict=False):
        if confirmed_at.get(before) is not None:
            out[phase] = confirmed_at[before]
    if current_phase and current_started_at is not None:
        out[current_phase] = current_started_at
    return out


def evaluator_facts(
    messages: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    options: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    lead_option_ids: Iterable[str] = (),
    card_schedule: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """`facts`에 더하는 평가자 사실 두 가지. 입력은 SessionRecord 모양 dict."""
    return {
        "decisions": decision_facts(slots, options, lead_option_ids, card_schedule),
        "message_facts": message_facts(messages, slots, calls),
    }


def _seconds(session: Session, at: Any) -> float | None:
    """세션 시작부터의 초(반올림 없음). 붙여넣기 판단의 앞뒤 비교용."""
    if at is None or session.started_at is None:
        return None
    return (at - session.started_at).total_seconds()


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
    responses = {
        m.id: m.content
        for m in (
            await db.scalars(
                select(Message).where(
                    Message.id.in_([c.response_message_id for c in calls if c.response_message_id])
                )
            )
        ).all()
    }
    record_slots = [
        {
            "phase": s.phase,
            "option_id": str(s.option_id) if s.option_id else None,
            "value": s.value,
            "filled_by": s.filled_by,
            "resolution": s.resolution,
            "timeout_rule": s.timeout_rule,
            "support": s.support or [],
            "t_min": t_min(session, s.confirmed_at),
            "at": _seconds(session, s.confirmed_at),
        }
        for s in confirmed
    ]
    fired_cards = set(
        (
            await db.scalars(
                select(Observation.card_id).where(Observation.session_id == session.id)
            )
        ).all()
    )
    starts = phase_starts(
        session.started_at,
        {s.phase: s.confirmed_at for s in confirmed},
        session.phase,
        session.phase_started_at,
    )
    card_schedule = []
    for card in scenario.get("cards", []):
        start = starts.get(card.get("stage_id"))
        if start is None or card.get("offset_min") is None or not card.get("actor"):
            continue
        due = start + timedelta(minutes=card["offset_min"])
        card_schedule.append(
            {
                "card_id": card["card_id"],
                "phase": card["stage_id"],
                "t_min": t_min(session, due),
                "at": _seconds(session, due),
                "fired": card["card_id"] in fired_cards,
            }
        )
    evaluator = evaluator_facts(
        [
            {
                "id": str(m.id),
                "sender": m.sender,
                "content": m.content,
                "phase": m.phase,
                "t_min": t_min(session, m.created_at) or 0.0,
                "at": _seconds(session, m.created_at),
                "intent": {"addressed_to": list(intents[m.id].addressed_to)}
                if m.id in intents
                else None,
            }
            for m in room
        ],
        record_slots,
        [{"option_id": str(o.id), "first_proposer": o.proposer} for o in options],
        [
            {
                "call_id": str(c.id),
                "t_min": t_min(session, c.created_at) or 0.0,
                "at": _seconds(session, c.created_at),
                "response": responses.get(c.response_message_id, ""),
            }
            for c in calls
        ],
        [
            str(lead)
            for lead in (
                await db.scalars(
                    select(SessionCard.locked_option_id).where(
                        SessionCard.session_id == session.id,
                        SessionCard.locked_option_id.is_not(None),
                    )
                )
            ).all()
        ],
        card_schedule,
    )
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
        **evaluator,
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
