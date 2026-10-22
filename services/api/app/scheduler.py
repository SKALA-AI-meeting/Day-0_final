"""스케줄러(5초 틱). tick()은 주입된 시계로 한 번 조건을 보고 처리하므로 가짜 시계로 단위
테스트한다.

세션마다(in_progress):
  expiry   now ≥ ends_at → submit("timeout") (자동 제출, 큐 발행)
  deadline 토론 단계 now ≥ phase_deadline_at, 칸 empty → api 결정적 시간 초과 채우기(LLM·그래프
  없음)
  warning  마감 L/30 전 → 시스템 안내 1건((session, phase, "warning") 멱등)
  idle     침묵 초대 조건 참·그 단계 invite_delivered false·대기 중 idle 없음·지원자 입력 중 아님 →
  enqueue
  card_due 카드 예정 시각 도달·담당 전원 delivered 아님·같은 card_id 대기·실행 없음 → 새 run_id로
  enqueue(같은 카드는 마지막 run 이후 30초 간격, 최대 3회)
전역:
  runs     started가 200초 넘은 run → failed로 닫고 같은 run_id로 다시 넣기
  outbox   completed·approving인데 *_enqueued_at 비어 있으면 재발행
  purge    ended_at + 90일이 지난 세션 삭제(completed 이후·failed·cancelled)
재시작 복구는 recover(): 메일박스 queued·started 재큐 뒤 tick 한 번(마감·자동 제출·미발행 재처리).
"""

import asyncio
import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select, update

from app import queue
from app.agent.runner import Stimulus
from app.mailbox import pending_key
from app.models import AgentRun, Invite, Message, MessageIntent, Position, Session, SessionCard
from app.services import purge, timeout
from app.services import scenario as sc
from app.services import session as session_service
from app.services.common import Effects, serialize_message
from app.services.context import AppContext
from app.services.phases import save_message

log = logging.getLogger(__name__)
TICK_SECONDS = 5
RUN_TIMEOUT = timedelta(seconds=200)
CARD_DUE_RETRY_GAP = timedelta(seconds=30)
CARD_DUE_MAX_RUNS = 3


async def tick(ctx: AppContext) -> None:
    async with ctx.session_factory() as db:
        ids = (await db.scalars(select(Session.id).where(Session.status == "in_progress"))).all()
    for session_id in ids:
        try:
            await tick_session(ctx, session_id)
        except Exception:
            log.exception("scheduler tick failed for %s", session_id)
    await ctx.mailboxes.recover(stale_before=ctx.clock.now() - RUN_TIMEOUT)
    from app.models import SpeechJob

    async with ctx.session_factory() as db:
        await db.execute(
            update(SpeechJob)
            .where(
                (SpeechJob.expires_at <= ctx.clock.now())
                | SpeechJob.session_id.in_(
                    select(Session.id).where(Session.status != "in_progress")
                )
            )
            .values(audio=None)
        )
        await db.commit()
    async with ctx.session_factory() as db:
        await queue.republish_pending(db, ctx.publisher, ctx.clock.now())
    async with ctx.session_factory() as db:
        await purge.purge_expired(db, ctx.clock.now())


async def tick_session(ctx: AppContext, session_id: uuid.UUID) -> None:
    fx = Effects()
    async with ctx.ordered(session_id):
        async with ctx.session_factory() as db:
            session = await db.get(Session, session_id, with_for_update=True)
            if session is None or session.status != "in_progress":
                return
            invite = await db.get(Invite, session.invite_id)
            position = await db.get(Position, invite.position_id)
            now = ctx.clock.now()
            if now >= session.ends_at:
                await session_service.submit(
                    db, session, "timeout", now, fx, position.scenario["proposal_template"]
                )
            elif session.phase in sc.DEBATE_PHASES and now >= session.phase_deadline_at:
                await timeout.fill(db, session, position, ctx.clock, fx)
            elif session.phase in sc.DEBATE_PHASES:
                await _warning(db, session, position, now, fx)
            await db.commit()
            candidates: list[Stimulus] = []
            if (
                session.status == "in_progress"
                and session.phase in sc.DEBATE_PHASES
                and fx.generation is None
            ):
                if not ctx.hub.is_candidate_typing(session_id):
                    candidates = await _due_stimuli(db, ctx, session, position, now)
        ctx.emit(session_id, fx)
    await ctx.publish(session_id, fx)
    for stimulus in candidates:
        await ctx.mailboxes.enqueue(session_id, stimulus)


async def _warning(db, session: Session, position: Position, now, fx: Effects) -> None:
    scenario = position.scenario
    warn_at = session.phase_deadline_at - timedelta(minutes=scenario["time_warning_min"])
    if now < warn_at:
        return
    remaining = sc.remaining_text((session.phase_deadline_at - now).total_seconds())
    message, created = await save_message(
        db,
        session,
        sender="system",
        content=scenario["phrases"]["time_warning"].format(remaining=remaining),
        run_id=uuid.uuid5(session.id, "warning:" + session.phase),
        seq=0,
        now=now,
        meta={"kind": "time_warning"},
    )
    if created:
        fx.emit("message.new", serialize_message(session, message))


async def _due_stimuli(db, ctx: AppContext, session: Session, position: Position, now):
    scenario = position.scenario
    phase = session.phase
    out: list[Stimulus] = []
    idle = Stimulus("idle", phase, session.generation)
    if phase not in session.invite_delivered_phases and not ctx.mailboxes.has_pending(
        pending_key(session.id, idle)
    ):
        if await _silence(db, session, scenario, now):
            out.append(idle)
    delivered = {
        c.card_id: c.delivered_seats
        for c in (
            await db.scalars(select(SessionCard).where(SessionCard.session_id == session.id))
        ).all()
    }
    for card in scenario["cards"]:
        if card["stage_id"] != phase or card.get("offset_min") is None or not card.get("actor"):
            continue
        if now < session.phase_started_at + timedelta(minutes=card["offset_min"]):
            continue
        if len(delivered.get(card["card_id"], [])) >= sc.card_required_count(card):
            continue
        stimulus = Stimulus("card_due", phase, session.generation, card_id=card["card_id"])
        if ctx.mailboxes.has_pending(pending_key(session.id, stimulus)):
            continue
        if await _card_retry_allowed(db, session, card["card_id"], now):
            out.append(stimulus)
    return out


async def _card_retry_allowed(db, session: Session, card_id: str, now) -> bool:
    """같은 카드의 card_due는 마지막 run 이후 30초 간격, 카드(=단계)당 최대 3회까지만 넣는다.

    담당 화자가 끝내 게시하지 못해도 자극이 끝없이 쌓이지 않게 하는 상한이다.
    """
    count, last = (
        await db.execute(
            select(func.count(), func.max(AgentRun.queued_at)).where(
                AgentRun.session_id == session.id,
                AgentRun.stimulus == "card_due",
                AgentRun.ref_id == card_id,
            )
        )
    ).one()
    if count >= CARD_DUE_MAX_RUNS:
        log.info("card_due cap reached for %s %s", session.id, card_id)
        return False
    return last is None or now - last >= CARD_DUE_RETRY_GAP


async def _silence(db, session: Session, scenario: dict, now) -> bool:
    """docs/product/scenario.md 3.1: (a) 마지막 지원자 메시지(또는 단계 시작) 후 L/20 무발언, 또는
    (b) 단계 기본 시간 절반이 지났는데 이 단계 의견(propose·agree·oppose)이 없음."""
    rule = scenario["silence_invite"]
    last = await db.scalar(
        select(func.max(Message.created_at)).where(
            Message.session_id == session.id,
            Message.sender == "candidate",
            Message.channel == "room",
            Message.phase == session.phase,
        )
    )
    since = last or session.phase_started_at
    if now - since >= timedelta(minutes=rule["idle_min"]):
        return True
    budget = sc.stage(scenario, session.phase)["budget_min"]
    if now - session.phase_started_at >= timedelta(
        minutes=budget * rule["no_opinion_after_budget_ratio"]
    ):
        opinions = await db.scalar(
            select(func.count()).where(
                MessageIntent.session_id == session.id,
                MessageIntent.phase == session.phase,
                MessageIntent.intent.in_(rule["opinion_intents"]),
            )
        )
        return opinions == 0
    return False


async def recover(ctx: AppContext) -> None:
    """api 기동 때: 메일박스 queued·started 재큐 → 마감·자동 제출·미발행 재처리."""
    await ctx.mailboxes.recover()
    await tick(ctx)


async def run_forever(ctx: AppContext) -> None:
    await recover(ctx)
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            await tick(ctx)
        except Exception:
            log.exception("scheduler tick failed")
