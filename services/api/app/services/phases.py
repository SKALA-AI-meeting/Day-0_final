"""단계 전환(시작·advance_phase)과 시스템·AI 메시지 저장. 상태·단계 변경은 이 모듈과 session.py만
한다.

advance_phase는 칸 확정과 같은 트랜잭션에서 호출되고(호출자가 이미 세션 행을 잠갔다), WS 프레임과
phase_started 자극은 Effects에 쌓아 커밋 뒤에 보낸다. 프레임
순서(docs/architecture/session-lifecycle.md):
(공지 message.new) → slot.confirmed → phase.changed → timer → (materials.opened) → 시스템 안내
message.new.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runner import Stimulus
from app.models import AgentRun, Message, Option, Position, Proposal, Session, Slot
from app.services import scenario as sc
from app.services.clock import Clock
from app.services.common import Effects, iso, serialize_message, t_min


async def save_message(
    db: AsyncSession,
    session: Session,
    *,
    sender: str,
    content: str,
    run_id: uuid.UUID,
    seq: int,
    now: datetime,
    phase: str | None = None,
    meta: dict[str, Any] | None = None,
    is_invite: bool = False,
    card_ids: list[str] | None = None,
    cited_material_ids: list[str] | None = None,
) -> tuple[Message, bool]:
    """AI·시스템 room 메시지. (session_id, run_id, seq) 멱등: (메시지, 새로 만듦)."""
    row = await db.scalar(
        insert(Message)
        .values(
            session_id=session.id,
            channel="room",
            sender=sender,
            phase=phase or session.phase,
            content=content,
            run_id=run_id,
            seq=seq,
            is_invite=is_invite,
            card_ids=card_ids or [],
            cited_material_ids=cited_material_ids or [],
            meta=meta or {},
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["session_id", "run_id", "seq"])
        .returning(Message)
    )
    if row is not None:
        return row, True
    existing = await db.scalar(
        select(Message).where(
            Message.session_id == session.id, Message.run_id == run_id, Message.seq == seq
        )
    )
    return existing, False


async def queue_run(
    db: AsyncSession,
    session: Session,
    stimulus: Stimulus,
    run_id: uuid.UUID,
    now: datetime,
    ref_id: str | None = None,
) -> bool:
    """agent_runs 행을 queued로 만든다(있으면 그대로). 자극 enqueue는 커밋 뒤 Effects로."""
    row = await db.scalar(
        insert(AgentRun)
        .values(
            run_id=run_id,
            session_id=session.id,
            graph="debate",
            stimulus=stimulus.type,
            # phase_started·idle은 재시작 때 자극을 되살리려고 단계를 ref_id에 둔다
            ref_id=ref_id if ref_id is not None else (stimulus.ref_id or stimulus.phase),
            generation=stimulus.generation,
            status="queued",
            queued_at=now,
        )
        .on_conflict_do_nothing(index_elements=["run_id"])
        .returning(AgentRun.run_id)
    )
    return row is not None


def timer_payload(session: Session, now: datetime) -> dict[str, Any]:
    return {
        "phase": session.phase,
        "phase_deadline_at": iso(session.phase_deadline_at),
        "phase_remaining_seconds": max(int((session.phase_deadline_at - now).total_seconds()), 0),
        "session_remaining_seconds": max(int((session.ends_at - now).total_seconds()), 0),
    }


async def keyword_key(db: AsyncSession, session_id: uuid.UUID) -> str | None:
    """확정된 키워드 칸의 옵션 키(자료·문제·해결책 카탈로그 선택 기준)."""
    return await db.scalar(
        select(Option.scenario_option_key)
        .join(Slot, Slot.option_id == Option.id)
        .where(Slot.session_id == session_id, Slot.phase == "keyword", Slot.status == "confirmed")
    )


async def enter_phase(
    db: AsyncSession,
    session: Session,
    position: Position,
    new_phase: str,
    reason: str,
    fx: Effects,
    clock: Clock,
) -> None:
    """phase·마감·generation을 바꾸고 전환 프레임·시스템 안내·phase_started를 쌓는다."""
    scenario = position.scenario
    previous = session.phase
    now = clock.now()
    session.phase = new_phase
    session.phase_started_at = now
    session.phase_deadline_at = (
        session.ends_at
        if new_phase == "proposal"
        else sc.phase_deadline(scenario, session.started_at, new_phase)
    )
    session.generation += 1
    fx.generation = session.generation
    fx.emit(
        "phase.changed",
        {
            "phase": new_phase,
            "previous_phase": previous,
            "reason": reason,
            "phase_started_at": iso(now),
            "phase_deadline_at": iso(session.phase_deadline_at),
            "t_min": t_min(session, now) or 0.0,
        },
    )
    fx.emit("timer", timer_payload(session, now))
    kw = await keyword_key(db, session.id)
    if previous == "keyword" and kw:
        info = scenario["keywords"][kw]
        fx.emit("materials.opened", {"keyword": info["label"], "materials": info["materials"]})
    if new_phase == "proposal":
        await _prefill_proposal(db, session)
    if reason == "start":
        await _greet(db, session, scenario, fx, clock)
    remaining = sc.remaining_text((session.phase_deadline_at - now).total_seconds())
    keywords = "·".join(o["label"] for o in sc.keyword_options(scenario))
    content = scenario["phrases"]["stage_start"][new_phase].format(
        keywords=keywords, remaining=remaining
    )
    message, _ = await save_message(
        db,
        session,
        sender="system",
        content=content,
        run_id=uuid.uuid5(session.id, "stage_start:" + new_phase),
        seq=0,
        now=clock.now(),
        meta={"kind": "stage_start"},
    )
    fx.emit("message.new", serialize_message(session, message))
    stimulus = Stimulus(type="phase_started", phase=new_phase, generation=session.generation)
    run_id = uuid.uuid5(session.id, "phase:" + new_phase)
    if await queue_run(db, session, stimulus, run_id, clock.now()):
        fx.stimuli.append((stimulus, run_id))


GREETING_SEATS = ("p1", "p2", "p3")


async def _greet(
    db: AsyncSession, session: Session, scenario: dict[str, Any], fx: Effects, clock: Clock
) -> None:
    """세션 시작 인사: 첫 단계 안내 앞에 AI 동료 셋이 고정 문구(채용 건 복사본
    phrases.greeting)로 한 마디씩 게시한다. LLM을 거치지 않아 모든 지원자가 같은 인사를 받는다.
    발언 수·첫 발언 집계에서는 빠진다(meta.kind greeting, debate.py). 인사 문구가 없는 옛 채용 건은
    건너뛴다."""
    lines = (scenario.get("phrases") or {}).get("greeting") or {}
    if not lines:
        return
    names = {s["seat"]: s["name"] for s in scenario.get("seats", [])}
    candidate = session.display_name or "지원자"
    run_id = uuid.uuid5(session.id, "greeting")
    for seq, seat in enumerate(GREETING_SEATS):
        if not lines.get(seat):
            continue
        content = lines[seat].format(name=names.get(seat, seat), candidate=candidate)
        message, created = await save_message(
            db,
            session,
            sender=seat,
            content=content,
            run_id=run_id,
            seq=seq,
            now=clock.now(),
            meta={"kind": "greeting"},
        )
        if created:
            fx.emit("message.new", serialize_message(session, message))


async def advance_phase(
    db: AsyncSession, session: Session, position: Position, reason: str, fx: Effects, clock: Clock
) -> None:
    await enter_phase(db, session, position, sc.next_phase(session.phase), reason, fx, clock)


async def _prefill_proposal(db: AsyncSession, session: Session) -> None:
    """proposal 진입 때 기획안 앞 3칸 중 빈 칸을 확정 칸 값으로 채운다(revision+1)."""
    proposal = await db.get(Proposal, session.id, with_for_update=True)
    slots = (await db.scalars(select(Slot).where(Slot.session_id == session.id))).all()
    sections = dict(proposal.sections)
    changed = False
    for slot in slots:
        if slot.value and not sections.get(slot.phase, "").strip():
            sections[slot.phase] = slot.value
            changed = True
    if changed:
        proposal.sections = sections
        proposal.revision += 1
