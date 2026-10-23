"""단계 마감 시간 초과 채우기(R5-2). 그래프·LLM·메일박스를 거치지 않고 api가 결정적으로 직접 한다.

스케줄러가 `now ≥ phase_deadline_at`이고 칸이 empty인 토론 단계에서 부른다. 한 트랜잭션으로:
timeout_option_id·timeout_rule(필요하면 기준안 행 생성, proposer = system) → 적용 규칙별 고정
문장을 시스템 메시지로 저장 → 칸 confirmed(resolution=timeout, filled_by=system, timeout_rule) →
advance_phase. 멱등 키 (session, phase).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Option, Position, Session, Slot
from app.services import scenario as sc
from app.services.clock import Clock
from app.services.common import Effects, serialize_message
from app.services.debate import (
    confirm,
    default_option_key,
    get_or_create_option,
    judge_phase,
    slot_event,
    support_snapshot,
)
from app.services.phases import advance_phase, keyword_key, save_message


async def fill(
    db: AsyncSession, session: Session, position: Position, clock: Clock, fx: Effects
) -> bool:
    """현재 단계 칸을 시간 초과로 채운다. 이미 confirmed거나 토론 단계가 아니면 False."""
    phase = session.phase
    if phase not in sc.DEBATE_PHASES:
        return False
    slot = await db.scalar(select(Slot).where(Slot.session_id == session.id, Slot.phase == phase))
    if slot is None or slot.status != "empty":
        return False
    scenario = position.scenario
    result, options = await judge_phase(db, session, scenario, phase)
    option_id, rule = result["timeout_option_id"], result["timeout_rule"]
    # 지지 현황은 선택에 쓴 안들로 남긴다(기준안을 새로 만들면 빈 목록 = 나온 안 없음)
    support = support_snapshot(options, result)
    if option_id is None:
        # 안이 하나도 없으면 기준안 행을 이 트랜잭션에서 만든다. 아무도 내지 않은 안이라 제안자는
        # system이다(지지·최초 제안·아이디어 기여로 세지 않는다)
        kw = await keyword_key(db, session.id)
        key = default_option_key(scenario, phase, kw)
        label = next(
            c["label"]
            for c in sc.catalog_options(scenario, phase, kw)
            if c["scenario_option_key"] == key
        )
        option, _ = await get_or_create_option(
            db, session.id, phase, label=label, proposer="system", now=clock.now(), key=key
        )
        option_id = option.id
    option = await db.get(Option, option_id)
    run_id = uuid.uuid5(session.id, "timeout:" + phase)
    message, _ = await save_message(
        db,
        session,
        sender="system",
        content=sc.phrase(
            scenario,
            "timeout_fill",
            variant=rule,
            option=option.label,
            candidate=session.display_name or "지원자",
        ),
        run_id=run_id,
        seq=0,
        now=clock.now(),
        meta={"kind": "timeout_fill", "timeout_rule": rule},
    )
    slot = await confirm(
        db,
        session,
        phase,
        value=option.label,
        option_id=option.id,
        filled_by="system",
        resolution="timeout",
        timeout_rule=rule,
        support=support,
        now=clock.now(),
        run_id=run_id,
    )
    fx.emit("message.new", serialize_message(session, message))
    fx.emit("slot.confirmed", slot_event(session, slot))
    await advance_phase(db, session, position, "timeout", fx, clock)
    return True
