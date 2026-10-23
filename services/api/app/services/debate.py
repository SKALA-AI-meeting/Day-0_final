"""토론 상태: 지원자 메시지 저장, 의도 분류 반영, 안·입장, 합의 판정, DebateState, 칸 확정·합의
채우기.

- 큐 밖(지원자 메시지): 저장 + generation +1 + agent_runs queued를 한 커밋. 의도 분류는 메일박스가
  `classify_and_apply`로 저장된 messages.phase 기준으로 한다(낡은 자극도 반드시, message_id당 1회).
- 칸 확정은 `status = empty`인 행만 바꾸는 조건부 갱신이라 동시 확정은 먼저 커밋한 쪽만 이긴다.
- 확정 경로에 LLM은 없다(옵션 키·정규화 라벨 일치만).
"""

import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runner import Stimulus
from app.masking import mask
from app.models import (
    Message,
    MessageIntent,
    Observation,
    Option,
    Position,
    Session,
    SessionCard,
    Slot,
    Stance,
)
from app.services import budget, consensus
from app.services import scenario as sc
from app.services.clock import Clock
from app.services.common import (
    Effects,
    conflict,
    invalid,
    iso,
    not_found,
    serialize_message,
    slot_full,
    slot_outcome,
    slot_public,
    t_min,
)
from app.services.phases import advance_phase, keyword_key, queue_run, save_message

SEATS = ("p1", "p2", "p3")
CONSENSUS_FILL_SEQ = 5
Classifier = Callable[..., Awaitable[tuple[dict[str, Any], bool]]]


# ---- 조회 ----------------------------------------------------------------------------------------


async def phase_options(db: AsyncSession, session_id: uuid.UUID, phase: str) -> list[Option]:
    rows = await db.scalars(
        select(Option)
        .where(Option.session_id == session_id, Option.phase == phase)
        .order_by(Option.created_at)
    )
    return list(rows)


async def phase_stances(db: AsyncSession, session_id: uuid.UUID, phase: str) -> list[Stance]:
    rows = await db.scalars(
        select(Stance)
        .where(Stance.session_id == session_id, Stance.phase == phase)
        .order_by(Stance.created_at)
    )
    return list(rows)


async def locked_lead(db: AsyncSession, session_id: uuid.UUID) -> uuid.UUID | None:
    return await db.scalar(
        select(SessionCard.locked_option_id).where(
            SessionCard.session_id == session_id,
            SessionCard.card_id.in_(("flexibility_majority", "listening_pressure")),
            SessionCard.locked_option_id.is_not(None),
        )
    )


def default_option_key(scenario: dict, phase: str, kw: str | None) -> str | None:
    """단계 기준안 키(문제 상황은 잠긴 기준안이 없을 때): keyword=default_keyword, problem=P1,
    solution=S1."""
    if phase == "keyword":
        return scenario["default_keyword"]
    options = sc.catalog_options(scenario, phase, kw)
    return options[0]["scenario_option_key"] if options else None


async def judge_phase(
    db: AsyncSession, session: Session, scenario: dict, phase: str
) -> tuple[dict[str, Any], list[Option]]:
    options = await phase_options(db, session.id, phase)
    stances = await phase_stances(db, session.id, phase)
    default_id = await locked_lead(db, session.id) if phase == "problem" else None
    if default_id is None:
        key = default_option_key(scenario, phase, await keyword_key(db, session.id))
        default_id = next((o.id for o in options if o.scenario_option_key == key), None)
    confirmed = await candidate_confirmed(db, session.id, phase)
    result = consensus.judge(
        [{"id": o.id, "proposer": o.proposer, "created_at": o.created_at} for o in options],
        [
            {
                "participant": s.participant,
                "option_id": s.option_id,
                "stance": s.stance,
                "created_at": s.created_at,
            }
            for s in stances
        ],
        default_id,
        confirmed,
    )
    return result, options


async def candidate_confirmed(
    db: AsyncSession, session_id: uuid.UUID, phase: str
) -> uuid.UUID | None:
    """그 단계 지원자의 최신 토론방 발언이 지지로 세는 발언이면 그 대상 안(B2).

    지지로 세는 발언 = high인 propose·agree이고 대상 안에 보류·반대·조건 표현이 없음. 최신 발언이
    아직 분류되지 않았거나 다른 의도면 None이라 AI 합의 채움이 성립하지 않는다.
    """
    latest = await db.scalar(
        select(Message.id)
        .where(
            Message.session_id == session_id,
            Message.sender == "candidate",
            Message.channel == "room",
            Message.phase == phase,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    if latest is None:
        return None
    found = await db.get(MessageIntent, latest)
    if (
        found is None
        or found.intent not in ("propose", "agree")
        or found.confidence != "high"
        or found.reservation
    ):
        return None
    return found.target_option_id


def support_snapshot(options: list[Option], result: dict[str, Any]) -> list[dict[str, Any]]:
    return consensus.support_snapshot(
        [{"id": o.id, "created_at": o.created_at} for o in options], result
    )


def serialize_option(o: Option, existing: bool = False) -> dict[str, Any]:
    return {
        "option_id": str(o.id),
        "phase": o.phase,
        "label": o.label,
        "proposer": o.proposer,
        "scenario_option_key": o.scenario_option_key,
        "created_at": iso(o.created_at),
        "existing": existing,
    }


async def get_or_create_option(
    db: AsyncSession,
    session_id: uuid.UUID,
    phase: str,
    *,
    label: str,
    proposer: str,
    now: datetime,
    key: str | None = None,
    source_message_id: uuid.UUID | None = None,
) -> tuple[Option, bool]:
    """시나리오 안은 (session, phase, key), 자유 안은 (session, phase, source_message_id) 멱등.

    반환 (행, existing). 기존 행이면 proposer를 바꾸지 않는다(최초 제안자 보존).
    """
    values = dict(
        session_id=session_id,
        phase=phase,
        label=label,
        proposer=proposer,
        scenario_option_key=key,
        source_message_id=source_message_id,
        created_at=now,
    )
    if key is not None:
        stmt = (
            insert(Option)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["session_id", "phase", "scenario_option_key"],
                index_where=text("scenario_option_key IS NOT NULL"),
            )
        )
        where = Option.scenario_option_key == key
    else:
        stmt = (
            insert(Option)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["session_id", "phase", "source_message_id"],
                index_where=text("scenario_option_key IS NULL AND source_message_id IS NOT NULL"),
            )
        )
        where = Option.source_message_id == source_message_id
    row = await db.scalar(stmt.returning(Option))
    if row is not None:
        return row, False
    existing = await db.scalar(
        select(Option).where(Option.session_id == session_id, Option.phase == phase, where)
    )
    return existing, True


async def add_stance(
    db: AsyncSession,
    session: Session,
    *,
    phase: str,
    participant: str,
    option_id: uuid.UUID,
    stance: str,
    source_message_id: uuid.UUID,
    now: datetime,
    run_id: uuid.UUID | None = None,
) -> uuid.UUID:
    row = await db.scalar(
        insert(Stance)
        .values(
            session_id=session.id,
            phase=phase,
            participant=participant,
            option_id=option_id,
            stance=stance,
            source_message_id=source_message_id,
            run_id=run_id,
            created_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["session_id", "source_message_id", "participant", "option_id"]
        )
        .returning(Stance.id)
    )
    if row is not None:
        return row
    return await db.scalar(
        select(Stance.id).where(
            Stance.session_id == session.id,
            Stance.source_message_id == source_message_id,
            Stance.participant == participant,
            Stance.option_id == option_id,
        )
    )


# ---- 지원자 메시지 -------------------------------------------------------------------------------


async def save_candidate_message(
    db: AsyncSession, session: Session, client_msg_id: str, content: str, clock: Clock
) -> tuple[Message, uuid.UUID | None, Stimulus | None]:
    """큐 밖 처리: 저장·generation +1·agent_runs queued (한 커밋은 호출자).

    같은 client_msg_id 재전송은 (기존 메시지, 기존 run_id, None).
    """
    existing = await db.scalar(
        select(Message).where(
            Message.session_id == session.id, Message.client_msg_id == client_msg_id
        )
    )
    if existing is not None:
        from app.models import AgentRun

        run_id = await db.scalar(
            select(AgentRun.run_id).where(
                AgentRun.session_id == session.id, AgentRun.ref_id == str(existing.id)
            )
        )
        return existing, run_id, None
    if session.status != "in_progress":
        raise conflict("session_ending", "session is not in progress")
    content = mask(content.strip())
    if not content:
        raise invalid("invalid_input", "empty message")
    now = clock.now()
    origin = await _origin(db, session)
    message = await db.scalar(
        insert(Message)
        .values(
            session_id=session.id,
            channel="room",
            sender="candidate",
            phase=session.phase,
            content=content,
            client_msg_id=client_msg_id,
            origin=origin,
            created_at=now,
        )
        .returning(Message)
    )
    session.generation += 1
    stimulus = Stimulus(
        type="message",
        phase=session.phase,
        generation=session.generation,
        message_id=message.id,
    )
    run_id = uuid.uuid4()
    await queue_run(db, session, stimulus, run_id, clock.now())
    return message, run_id, stimulus


async def _origin(db: AsyncSession, session: Session) -> str:
    """같은 단계에서 지원자를 부른 초대 메시지 뒤 첫 지원자 발언이면 invited."""
    invite_at = await db.scalar(
        select(func.max(Message.created_at)).where(
            Message.session_id == session.id,
            Message.phase == session.phase,
            Message.is_invite.is_(True),
        )
    )
    if invite_at is None:
        return "spontaneous"
    after = await db.scalar(
        select(func.count()).where(
            Message.session_id == session.id,
            Message.sender == "candidate",
            Message.channel == "room",
            Message.created_at > invite_at,
        )
    )
    return "invited" if after == 0 else "spontaneous"


async def classify_and_apply(
    db: AsyncSession,
    session: Session,
    position: Position,
    message: Message,
    classify: Classifier,
    clock: Clock,
) -> MessageIntent:
    """의도 분류(그 메시지의 messages.phase와 그 단계 카탈로그) → message_intents → 안·입장."""
    found = await db.get(MessageIntent, message.id)
    if found is not None:
        return found
    scenario = position.scenario
    phase = message.phase
    kw = await keyword_key(db, session.id)
    catalog = sc.catalog_options(scenario, phase, kw)
    debate_phase = phase in sc.DEBATE_PHASES
    options = await phase_options(db, session.id, phase) if debate_phase else []
    recent_rows = await db.scalars(
        select(Message)
        .where(
            Message.session_id == session.id,
            Message.channel == "room",
            Message.created_at < message.created_at,
        )
        .order_by(Message.created_at.desc())
        .limit(5)
    )
    recent = [{"sender": m.sender, "content": m.content} for m in reversed(list(recent_rows))]
    started = clock.now()
    try:
        result, used_fallback = await classify(
            message.content,
            phase=phase,
            catalog=catalog,
            options=[
                {
                    "option_id": str(o.id),
                    "label": o.label,
                    "scenario_option_key": o.scenario_option_key,
                }
                for o in options
            ],
            recent=recent,
            seats=sc.seat_names(scenario),
            message_id=str(message.id),
            instructions=scenario.get("prompts", {}).get("intent.md"),
        )
    except Exception:
        from app.agent import intent as intent_module

        result, used_fallback = intent_module.fallback(), True
    by_id = {str(o.id): o for o in options}
    by_key = {o.scenario_option_key: o for o in options if o.scenario_option_key}
    catalog_labels = {c["scenario_option_key"]: c["label"] for c in catalog}
    intent = result.get("intent", "other")
    confidence = result.get("confidence", "low")
    reservation = bool(result.get("reservation"))
    # 지지로 세는 발언: high인 propose·agree이고 대상 안에 보류·반대·조건 표현이 없음(B1)
    explicit = confidence == "high" and not reservation
    target = by_id.get(str(result.get("target_option_id")))
    key = result.get("scenario_option_key")
    if key not in catalog_labels:
        key = None
    if target is None and key in by_key:
        target = by_key[key]
    now = clock.now()
    if debate_phase and intent == "propose" and target is None:
        if key is not None:
            target, _ = await get_or_create_option(
                db,
                session.id,
                phase,
                label=catalog_labels[key],
                proposer="candidate",
                now=now,
                key=key,
                source_message_id=message.id,
            )
        elif result.get("new_option_label"):
            target, _ = await get_or_create_option(
                db,
                session.id,
                phase,
                label=str(result["new_option_label"])[:40],
                proposer="candidate",
                now=now,
                source_message_id=message.id,
            )
    elif debate_phase and target is None and key is not None and intent in ("agree", "oppose"):
        # 아직 행이 없는 카탈로그 안에 동의·반대: 지지는 지지로 세는 발언일 때만, 반대는 행이
        # 없으면 의미 없음
        if intent == "agree" and explicit:
            target, _ = await get_or_create_option(
                db,
                session.id,
                phase,
                label=catalog_labels[key],
                proposer="candidate",
                now=now,
                key=key,
                source_message_id=message.id,
            )
    withdrawn = None
    if debate_phase and intent == "withhold":
        # 보류·기권은 그 안 지지를 철회한다. 대상 없이 판단을 미루면 지금 지지하는 안을 철회한다.
        # 지지를 줄이는 쪽이라 confidence와 상관없이 반영한다
        withdrawn = target
        if withdrawn is None and key is None:
            result_now, _ = await judge_phase(db, session, scenario, phase)
            current = result_now["current_support"].get("candidate")
            withdrawn = next((o for o in options if o.id == current), None)
    row = MessageIntent(
        message_id=message.id,
        session_id=session.id,
        phase=phase,
        intent=intent,
        target_option_id=target.id if target else None,
        scenario_option_key=key,
        new_option_label=result.get("new_option_label"),
        confidence=confidence,
        reservation=reservation,
        addressed_to=[s for s in result.get("addressed_to") or [] if s in SEATS],
        excluded=[s for s in result.get("excluded") or [] if s in SEATS],
        fallback=used_fallback,
        latency_ms=int((clock.now() - started).total_seconds() * 1000),
        created_at=clock.now(),
    )
    db.add(row)
    stance, stance_option = None, target
    if intent in ("propose", "agree") and explicit:
        stance = "support"
    elif intent == "oppose" and confidence == "high":
        stance = "oppose"
    elif intent == "withhold":
        stance, stance_option = "withhold", withdrawn
    if debate_phase and stance and stance_option is not None:
        await add_stance(
            db,
            session,
            phase=phase,
            participant="candidate",
            option_id=stance_option.id,
            stance=stance,
            source_message_id=message.id,
            now=clock.now(),
        )
    await db.flush()
    return row


# ---- 내부 쓰기(debate-mcp) -----------------------------------------------------------------------


def require_writable(session: Session) -> None:
    if session.status != "in_progress":
        raise conflict("conflict", "session is not in progress")


async def post_internal_message(
    db: AsyncSession, session: Session, body: dict[str, Any], clock: Clock, fx: Effects
) -> Message:
    """POST /internal/sessions/{id}/messages. (run_id, seq) 멱등이 generation 검사보다 먼저."""
    run_id = uuid.UUID(str(body["run_id"]))
    existing = await db.scalar(
        select(Message).where(
            Message.session_id == session.id,
            Message.run_id == run_id,
            Message.seq == body["seq"],
        )
    )
    if existing is not None:
        return existing
    require_writable(session)
    expected = body.get("expected_generation")
    if expected is not None and expected != session.generation:
        raise conflict("stale_generation", "generation changed")
    is_invite = bool(body.get("is_invite"))
    if is_invite:
        if session.phase in session.invite_delivered_phases:
            raise conflict("conflict", "invite already delivered in this phase")
        session.invite_delivered_phases = [*session.invite_delivered_phases, session.phase]
    card_ids = list(dict.fromkeys(body.get("card_ids") or []))
    sender = body["sender"]
    for card_id in card_ids:
        await deliver_card(db, session.id, card_id, sender)
    message, _ = await save_message(
        db,
        session,
        sender=sender,
        content=body["content"],
        run_id=run_id,
        seq=body["seq"],
        now=clock.now(),
        meta=body.get("meta") or {},
        is_invite=is_invite,
        card_ids=card_ids,
        cited_material_ids=body.get("cited_material_ids") or [],
    )
    fx.emit("message.new", serialize_message(session, message))
    return message


async def ensure_card(db: AsyncSession, session_id: uuid.UUID, card_id: str) -> SessionCard:
    await db.execute(
        insert(SessionCard)
        .values(session_id=session_id, card_id=card_id)
        .on_conflict_do_nothing(index_elements=["session_id", "card_id"])
    )
    return await db.scalar(
        select(SessionCard)
        .where(SessionCard.session_id == session_id, SessionCard.card_id == card_id)
        .with_for_update()
    )


async def deliver_card(db: AsyncSession, session_id: uuid.UUID, card_id: str, seat: str) -> None:
    if seat not in SEATS:
        return
    row = await ensure_card(db, session_id, card_id)
    if seat not in row.delivered_seats:
        row.delivered_seats = [*row.delivered_seats, seat]


async def lock_lead_option(
    db: AsyncSession, session: Session, position: Position, card_id: str, clock: Clock
) -> SessionCard:
    """card_due 첫 처리: session_cards 행 생성, 문제 상황 카드면 기준안 잠금(이후 불변).

    기준안 = 지원자의 현재 지지 안이 아닌 첫 시나리오 안(P1→P2→P3). 행이 없으면 만든다
    (proposer = 카드 첫 담당 자리). 같은 단계의 두 카드는 같은 기준안을 쓴다.
    """
    scenario = position.scenario
    row = await ensure_card(db, session.id, card_id)
    card_def = sc.card(scenario, card_id)
    if card_def["stage_id"] != scenario["lead_option"]["stage_id"] or row.locked_option_id:
        return row
    lead = await locked_lead(db, session.id)
    if lead is None:
        result, options = await judge_phase(db, session, scenario, "problem")
        candidate_support = result["current_support"]["candidate"]
        by_id = {o.id: o for o in options}
        supported_key = by_id[candidate_support].scenario_option_key if candidate_support else None
        kw = await keyword_key(db, session.id)
        catalog = sc.catalog_options(scenario, "problem", kw)
        choice = next(c for c in catalog if c["scenario_option_key"] != supported_key)
        seats = sc.card_seats(scenario, card_def)
        option, _ = await get_or_create_option(
            db,
            session.id,
            "problem",
            label=choice["label"],
            proposer=seats[0] if seats else "p1",
            now=clock.now(),
            key=choice["scenario_option_key"],
        )
        lead = option.id
    row.locked_option_id = lead
    row.locked_at = clock.now()
    return row


async def internal_option(
    db: AsyncSession, session: Session, body: dict[str, Any], clock: Clock
) -> dict[str, Any]:
    require_writable(session)
    if body["phase"] != session.phase:
        raise conflict("phase_not_open", "not the current phase")
    source = uuid.UUID(str(body["source_message_id"])) if body.get("source_message_id") else None
    option, existing = await get_or_create_option(
        db,
        session.id,
        body["phase"],
        label=body["label"],
        proposer=body["proposer"],
        now=clock.now(),
        key=body.get("scenario_option_key"),
        source_message_id=source,
    )
    return serialize_option(option, existing)


async def internal_stance(
    db: AsyncSession, session: Session, body: dict[str, Any], clock: Clock
) -> dict[str, Any]:
    require_writable(session)
    option = await db.get(Option, uuid.UUID(str(body["option_id"])))
    if option is None or option.session_id != session.id:
        raise not_found("option not found")
    if option.phase != session.phase:
        raise conflict("phase_not_open", "not the current phase")
    stance_id = await add_stance(
        db,
        session,
        phase=option.phase,
        participant=body["participant"],
        option_id=option.id,
        stance=body["stance"],
        source_message_id=uuid.UUID(str(body["source_message_id"])),
        run_id=uuid.UUID(str(body["run_id"])) if body.get("run_id") else None,
        now=clock.now(),
    )
    return {"stance_id": str(stance_id)}


# ---- 칸 확정 -------------------------------------------------------------------------------------


def normalize(value: str) -> str:
    value = re.sub(r"[^\w\s]", "", value.strip().lower())
    return re.sub(r"\s+", " ", value).strip()


async def confirm(
    db: AsyncSession,
    session: Session,
    phase: str,
    *,
    value: str,
    option_id: uuid.UUID | None,
    filled_by: str,
    resolution: str,
    support: list[dict],
    now: datetime,
    run_id: uuid.UUID | None = None,
) -> Slot:
    """empty인 행만 바꾸는 조건부 갱신. 지면 409 slot_confirmed."""
    row = await db.scalar(
        update(Slot)
        .where(Slot.session_id == session.id, Slot.phase == phase, Slot.status == "empty")
        .values(
            status="confirmed",
            value=value,
            option_id=option_id,
            filled_by=filled_by,
            resolution=resolution,
            support=support,
            confirmed_at=now,
            run_id=run_id,
        )
        .returning(Slot)
        .execution_options(synchronize_session=False, populate_existing=True)
    )
    if row is None:
        raise conflict("slot_confirmed", "slot already confirmed")
    return row


async def confirm_slot(
    db: AsyncSession,
    session: Session,
    position: Position,
    phase: str,
    value: str | None,
    option_key: str | None,
    clock: Clock,
    fx: Effects,
) -> dict[str, Any]:
    """지원자 확정 POST /sessions/{id}/slots/{phase}/confirm → {slot, phase}."""
    scenario = position.scenario
    if session.status != "in_progress":
        raise conflict("session_ending", "session is not in progress")
    slot = await db.scalar(select(Slot).where(Slot.session_id == session.id, Slot.phase == phase))
    if slot is not None and slot.status == "confirmed":
        same = (phase == "keyword" and _keyword_label(scenario, option_key) == slot.value) or (
            phase != "keyword" and value is not None and value.strip() == slot.value
        )
        if same:
            return {"slot": slot_public(session, slot), "phase": session.phase}
        raise conflict("slot_confirmed", "slot already confirmed")
    if phase != session.phase or slot is None:
        raise conflict("phase_not_open", "not the current phase")
    now = clock.now()
    if phase == "keyword":
        label = _keyword_label(scenario, option_key)
        if label is None:
            raise invalid("invalid_keyword", "keyword must be one of the candidates")
        option, _ = await get_or_create_option(
            db, session.id, "keyword", label=label, proposer="candidate", now=now, key=option_key
        )
        value, option_id = label, option.id
    else:
        value = (value or "").strip()
        if not value:
            raise invalid("invalid_input", "value is required")
        options = await phase_options(db, session.id, phase)
        match = next(
            (o for o in options if option_key and o.scenario_option_key == option_key), None
        )
        if match is None:
            target = normalize(value)
            match = next((o for o in options if normalize(o.label) == target), None)
        option_id = match.id if match else None
    result, options = await judge_phase(db, session, scenario, phase)
    slot = await confirm(
        db,
        session,
        phase,
        value=value,
        option_id=option_id,
        filled_by="candidate",
        resolution="consensus",
        support=support_snapshot(options, result),
        now=clock.now(),
    )
    fx.emit("slot.confirmed", slot_event(session, slot))
    await advance_phase(db, session, position, "confirmed", fx, clock)
    return {"slot": slot_public(session, slot), "phase": session.phase}


def _keyword_label(scenario: dict, option_key: str | None) -> str | None:
    if option_key is None or option_key not in scenario["keywords"]:
        return None
    return scenario["keywords"][option_key]["label"]


def slot_event(session: Session, slot: Slot) -> dict[str, Any]:
    return {
        "phase": slot.phase,
        "value": slot.value,
        "filled_by": slot.filled_by,
        "resolution": slot.resolution,
        "outcome": slot_outcome(slot),
        "t_min": t_min(session, slot.confirmed_at) or 0.0,
    }


async def fill_slot(
    db: AsyncSession,
    session: Session,
    position: Position,
    body: dict[str, Any],
    clock: Clock,
    fx: Effects,
) -> dict[str, Any]:
    """내부 POST /slots/fill: 합의 재검사 → 칸 confirmed → 공지(seq 5) → advance_phase, 한
    트랜잭션."""
    run_id = uuid.UUID(str(body["run_id"]))
    done = await db.scalar(
        select(Slot).where(
            Slot.session_id == session.id, Slot.run_id == run_id, Slot.status == "confirmed"
        )
    )
    if done is not None:
        message = await db.scalar(
            select(Message).where(
                Message.session_id == session.id,
                Message.run_id == run_id,
                Message.seq == CONSENSUS_FILL_SEQ,
            )
        )
        return {
            "slot": slot_full(session, done),
            "message": serialize_message(session, message),
            "phase": session.phase,
        }
    option = await db.get(Option, uuid.UUID(str(body["option_id"])))
    if option is None or option.session_id != session.id:
        raise not_found("option not found")
    require_writable(session)
    if option.phase != session.phase:
        raise conflict("phase_not_open", "not the current phase")
    slot = await db.scalar(
        select(Slot).where(Slot.session_id == session.id, Slot.phase == option.phase)
    )
    if slot.status == "confirmed":
        raise conflict("slot_confirmed", "slot already confirmed")
    result, options = await judge_phase(db, session, position.scenario, option.phase)
    if result["consensus_option_id"] != option.id:
        raise conflict("no_consensus", "option is not the consensus")
    seat = body["seat"]
    slot = await confirm(
        db,
        session,
        option.phase,
        value=option.label,
        option_id=option.id,
        filled_by=seat,
        resolution="consensus",
        support=support_snapshot(options, result),
        now=clock.now(),
        run_id=run_id,
    )
    message, _ = await save_message(
        db,
        session,
        sender=seat,
        content=body["content"],
        run_id=run_id,
        seq=CONSENSUS_FILL_SEQ,
        now=clock.now(),
        meta={"kind": "consensus_fill"},
    )
    fx.emit("message.new", serialize_message(session, message))
    fx.emit("slot.confirmed", slot_event(session, slot))
    await advance_phase(db, session, position, "confirmed", fx, clock)
    return {
        "slot": slot_full(session, slot),
        "message": serialize_message(session, message),
        "phase": session.phase,
    }


# ---- DebateState ---------------------------------------------------------------------------------


async def debate_state(
    db: AsyncSession, session: Session, position: Position, now: datetime, candidate_typing: bool
) -> dict[str, Any]:
    scenario = position.scenario
    phase = session.phase
    debate_phase = phase in sc.DEBATE_PHASES
    result, options = await judge_phase(db, session, scenario, phase) if debate_phase else ({}, [])
    kw = await keyword_key(db, session.id)
    catalog = sc.catalog_options(scenario, phase, kw) if debate_phase else []
    spoken = {o.scenario_option_key for o in options}
    slots = (
        await db.scalars(select(Slot).where(Slot.session_id == session.id).order_by(Slot.phase))
    ).all()
    slot = next((s for s in slots if s.phase == phase), None)
    cand_msgs = (
        await db.scalars(
            select(Message.created_at).where(
                Message.session_id == session.id,
                Message.sender == "candidate",
                Message.channel == "room",
                Message.phase == phase,
            )
        )
    ).all()
    last_room = await db.scalar(
        select(func.max(Message.created_at)).where(
            Message.session_id == session.id, Message.channel == "room"
        )
    )
    cards = (
        await db.scalars(select(SessionCard).where(SessionCard.session_id == session.id))
    ).all()
    # 카드의 첫 발언(관찰 trigger)과 그 뒤 AI 자리별 발언 수(대화 창과 무관한 전체 기록).
    # 진행 매니저가 이 수로 압박 발언 횟수를 센다
    triggers = {
        card_id: message_id
        for card_id, message_id in (
            await db.execute(
                select(Observation.card_id, Observation.trigger_message_id).where(
                    Observation.session_id == session.id
                )
            )
        ).all()
    }
    spoken_since = {
        card_id: await _spoken_since(db, session.id, message_id)
        for card_id, message_id in triggers.items()
    }
    current = result.get("current_support", {p: None for p in consensus.PARTICIPANTS})
    order = {p: i for i, p in enumerate(sc.PHASES)}
    # 이 단계 지원자 메시지의 저장된 의도(진행 매니저 설득 가능 규칙 횟수, 프로세스 메모리에 기대지
    # 않음)와 AI 자리별 이 단계 첫 발언 시각
    candidate_intents = (
        (
            await db.execute(
                select(MessageIntent, Message.created_at)
                .join(Message, Message.id == MessageIntent.message_id)
                .where(
                    MessageIntent.session_id == session.id,
                    Message.phase == phase,
                    Message.sender == "candidate",
                    Message.channel == "room",
                )
                .order_by(Message.created_at)
            )
        ).all()
        if debate_phase
        else []
    )
    first_spoke = dict(
        (
            await db.execute(
                select(Message.sender, func.min(Message.created_at))
                .where(
                    Message.session_id == session.id,
                    Message.phase == phase,
                    Message.channel == "room",
                    Message.sender.in_(SEATS),
                )
                .group_by(Message.sender)
            )
        ).all()
    )
    return {
        "phase": phase,
        "generation": session.generation,
        "phase_deadline_at": iso(session.phase_deadline_at),
        "phase_remaining_seconds": max(int((session.phase_deadline_at - now).total_seconds()), 0),
        "slot": None
        if slot is None
        else {"phase": slot.phase, "status": slot.status, "value": slot.value},
        "options": [
            {
                "option_id": str(o.id),
                "label": o.label,
                "proposer": o.proposer,
                "scenario_option_key": o.scenario_option_key,
                "created_at": iso(o.created_at),
                "supporters": result["supporters"].get(o.id, []),
                "opposers": result["opposers"].get(o.id, []),
            }
            for o in options
        ],
        "catalog_options": [{**c, "spoken": c["scenario_option_key"] in spoken} for c in catalog],
        "current_support": {p: (str(v) if v else None) for p, v in current.items()},
        "consensus_option_id": _str(result.get("consensus_option_id")),
        "timeout_option_id": _str(result.get("timeout_option_id")),
        "candidate": {
            "spoke_in_phase": bool(cand_msgs),
            "last_message_t_min": t_min(session, max(cand_msgs)) if cand_msgs else None,
            "typing": candidate_typing,
        },
        "invite_delivered": phase in session.invite_delivered_phases,
        "last_room_message_t_min": t_min(session, last_room),
        "confirmed": [
            {
                "phase": s.phase,
                "value": s.value,
                "option_id": _str(s.option_id),
                "resolution": s.resolution,
                "filled_by": s.filled_by,
            }
            for s in sorted(slots, key=lambda s: order[s.phase])
            if s.status == "confirmed"
        ],
        "card_progress": [
            {
                "card_id": c.card_id,
                "locked_option_id": _str(c.locked_option_id),
                "delivered": list(c.delivered_seats),
                "trigger_message_id": _str(triggers.get(c.card_id)),
                "spoken_since_trigger": spoken_since.get(c.card_id) or {},
            }
            for c in cards
        ],
        "cost_left": budget.cost_left(session),
        "candidate_intents": [
            {
                "message_id": str(i.message_id),
                "created_at": iso(created),
                "intent": i.intent,
                "target_option_id": _str(i.target_option_id),
                "scenario_option_key": i.scenario_option_key,
                "new_option_label": i.new_option_label,
                "confidence": i.confidence,
                "reservation": i.reservation,
            }
            for i, created in candidate_intents
        ],
        "first_spoke_at": {seat: iso(first_spoke.get(seat)) for seat in SEATS},
    }


async def _spoken_since(
    db: AsyncSession, session_id: uuid.UUID, message_id: uuid.UUID | None
) -> dict[str, int]:
    """trigger 메시지(포함)부터 같은 단계에서 AI 자리별 토론방 발언 수."""
    if message_id is None:
        return {}
    trigger = await db.get(Message, message_id)
    if trigger is None:
        return {}
    rows = await db.execute(
        select(Message.sender, func.count())
        .where(
            Message.session_id == session_id,
            Message.channel == "room",
            Message.phase == trigger.phase,
            Message.sender.in_(("p1", "p2", "p3")),
            Message.created_at >= trigger.created_at,
        )
        .group_by(Message.sender)
    )
    return {sender: int(n) for sender, n in rows.all()}


def _str(value: Any) -> str | None:
    return str(value) if value is not None else None
