"""내부 REST(/internal, 포트 8001, X-Internal-Token). docs/contracts/internal.md "내부 REST" 표
그대로.

내부 읽기는 상태 제한이 없다. 내부 쓰기는 in_progress에서만(아니면 409 conflict, 이미 저장된 키의
재전달은 기존 결과). evaluations·report/*는 completed 이후에만. 409의 error.code에는 세부 코드
(no_consensus·stale_generation·slot_confirmed·phase_not_open)를 그대로 넣는다.
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.auth import require_internal
from app.models import (
    AssistantCall,
    Message,
    MessageIntent,
    Observation,
    Option,
    Proposal,
    Session,
    SessionCard,
    Slot,
)
from app.services import debate, facts, reports, test_sessions, views
from app.services import proposal as proposal_service
from app.services import scenario as sc
from app.services.common import (
    ServiceError,
    conflict,
    iso,
    load_context,
    not_found,
    serialize_message,
    slot_full,
    t_min,
)
from app.services.context import AppContext, get_context

router = APIRouter(prefix="/internal", dependencies=[Depends(require_internal)])
Ctx = Annotated[AppContext, Depends(get_context)]
Seat = Literal["p1", "p2", "p3"]


def _card_progress(cards: list[SessionCard]) -> list[dict[str, Any]]:
    return [
        {
            "card_id": c.card_id,
            "locked_option_id": str(c.locked_option_id) if c.locked_option_id else None,
            "delivered": list(c.delivered_seats),
        }
        for c in cards
    ]


@router.get("/sessions/{session_id}")
async def get_session(session_id: uuid.UUID, ctx: Ctx) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, position, invite = await load_context(db, session_id)
        cards = (
            await db.scalars(select(SessionCard).where(SessionCard.session_id == session_id))
        ).all()
        return {
            "id": str(session.id),
            "status": session.status,
            "phase": session.phase,
            "alias": invite.alias,
            "display_name": session.display_name,
            "position": {
                "id": str(position.id),
                "name": position.name,
                "mission_id": position.mission_id,
                "session_minutes": position.session_minutes,
                "scenario": position.scenario,
                "catalog_version": position.catalog_version,
            },
            "seats": views.seats_typed(position.scenario, session, invite),
            "started_at": iso(session.started_at),
            "ends_at": iso(session.ends_at),
            "phase_started_at": iso(session.phase_started_at),
            "phase_deadline_at": iso(session.phase_deadline_at),
            "completed_at": iso(session.completed_at),
            "ended_at": iso(session.ended_at),
            "t_min_now": t_min(session, ctx.clock.now()),
            "generation": session.generation,
            "invite_delivered_phases": list(session.invite_delivered_phases),
            "card_progress": _card_progress(list(cards)),
            "connection_gaps": await views.gaps(db, session),
        }


@router.get("/sessions/{session_id}/debate_state")
async def debate_state(session_id: uuid.UUID, ctx: Ctx) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, position, _ = await load_context(db, session_id)
        if session.phase is None or session.phase == "completed":
            raise conflict("phase_not_open", "no debate phase")
        return await debate.debate_state(
            db, session, position, ctx.clock.now(), ctx.hub.is_candidate_typing(session_id)
        )


def _internal_message(session: Session, m: Message, intent: MessageIntent | None) -> dict:
    return {
        **serialize_message(session, m),
        "card_ids": list(m.card_ids),
        "meta": m.meta or {},
        "origin": m.origin,
        "intent": None
        if intent is None
        else {
            "intent": intent.intent,
            "target_option_id": str(intent.target_option_id) if intent.target_option_id else None,
            "scenario_option_key": intent.scenario_option_key,
            "new_option_label": intent.new_option_label,
            "confidence": intent.confidence,
            "addressed_to": list(intent.addressed_to),
        },
    }


async def _with_intents(db, session: Session, rows: list[Message]) -> list[dict]:
    ids = [m.id for m in rows]
    intents = (
        {
            i.message_id: i
            for i in (
                await db.scalars(select(MessageIntent).where(MessageIntent.message_id.in_(ids)))
            ).all()
        }
        if ids
        else {}
    )
    return [_internal_message(session, m, intents.get(m.id)) for m in rows]


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: uuid.UUID,
    ctx: Ctx,
    since_t_min: float | None = None,
    phase: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    tail: Annotated[int | None, Query(ge=1, le=1000)] = None,
) -> list[dict[str, Any]]:
    """토론방(room) 메시지만. tail=N이면 최근 N개를 오래된 순으로(limit·offset 무시)."""
    async with ctx.session_factory() as db:
        session, _, _ = await load_context(db, session_id)
        query = select(Message).where(Message.session_id == session_id, Message.channel == "room")
        if phase:
            query = query.where(Message.phase == phase)
        if since_t_min is not None and session.started_at is not None:
            from datetime import timedelta

            query = query.where(
                Message.created_at >= session.started_at + timedelta(minutes=since_t_min)
            )
        if tail is not None:
            rows = list(
                (
                    await db.scalars(
                        query.order_by(Message.created_at.desc(), Message.seq.desc()).limit(tail)
                    )
                ).all()
            )
            rows.reverse()
        else:
            rows = list(
                (
                    await db.scalars(
                        query.order_by(Message.created_at, Message.seq).limit(limit).offset(offset)
                    )
                ).all()
            )
        return await _with_intents(db, session, rows)


class PostMessageIn(BaseModel):
    sender: Literal["p1", "p2", "p3", "system"]
    content: str
    run_id: uuid.UUID
    seq: int
    expected_generation: int | None = None
    is_invite: bool = False
    card_ids: list[str] = []
    cited_material_ids: list[str] = []
    meta: dict[str, Any] = {}


async def _write(ctx: AppContext, session_id: uuid.UUID, fn) -> Any:
    from app.services.common import Effects

    fx = Effects()
    async with ctx.ordered(session_id):
        async with ctx.session_factory() as db:
            session, position, _ = await load_context(db, session_id, lock=True)
            result = await fn(db, session, position, fx)
            await db.commit()
        ctx.emit(session_id, fx)
    await ctx.publish(session_id, fx)
    return result


@router.post("/sessions/{session_id}/messages")
async def post_message(session_id: uuid.UUID, body: PostMessageIn, ctx: Ctx) -> dict[str, Any]:
    async def fn(db, session, position, fx):
        message = await debate.post_internal_message(
            db, session, body.model_dump(mode="json"), ctx.clock, fx
        )
        return serialize_message(session, message)

    return await _write(ctx, session_id, fn)


class OptionIn(BaseModel):
    phase: Literal["keyword", "problem", "solution"]
    label: str
    proposer: Seat
    scenario_option_key: str | None = None
    source_message_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None


@router.post("/sessions/{session_id}/options")
async def post_option(session_id: uuid.UUID, body: OptionIn, ctx: Ctx) -> dict[str, Any]:
    async def fn(db, session, position, fx):
        return await debate.internal_option(db, session, body.model_dump(mode="json"), ctx.clock)

    return await _write(ctx, session_id, fn)


class StanceIn(BaseModel):
    participant: Seat
    option_id: uuid.UUID
    stance: Literal["support", "oppose"]
    source_message_id: uuid.UUID
    run_id: uuid.UUID | None = None


@router.post("/sessions/{session_id}/stances")
async def post_stance(session_id: uuid.UUID, body: StanceIn, ctx: Ctx) -> dict[str, Any]:
    async def fn(db, session, position, fx):
        return await debate.internal_stance(db, session, body.model_dump(mode="json"), ctx.clock)

    return await _write(ctx, session_id, fn)


class FillIn(BaseModel):
    option_id: uuid.UUID
    seat: Seat
    content: str
    run_id: uuid.UUID


@router.post("/sessions/{session_id}/slots/fill")
async def fill(session_id: uuid.UUID, body: FillIn, ctx: Ctx) -> dict[str, Any]:
    async def fn(db, session, position, fx):
        return await debate.fill_slot(
            db, session, position, body.model_dump(mode="json"), ctx.clock, fx
        )

    return await _write(ctx, session_id, fn)


@router.get("/sessions/{session_id}/proposal")
async def get_proposal(session_id: uuid.UUID, ctx: Ctx) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, _, _ = await load_context(db, session_id)
        proposal = await db.get(Proposal, session_id)
        if proposal is None:
            raise not_found("proposal not found")
        return proposal_service.internal(proposal, session)


@router.get("/sessions/{session_id}/materials")
async def materials(session_id: uuid.UUID, ctx: Ctx) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, position, _ = await load_context(db, session_id)
        from app.services.phases import keyword_key

        kw = await keyword_key(db, session_id)
        listed = await views.materials(db, session, position)
        return {
            "catalog_version": position.catalog_version,
            "locked": listed["locked"],
            "keyword_key": kw,
            "materials": listed["materials"],
        }


@router.get("/sessions/{session_id}/materials/{material_id}")
async def material(session_id: uuid.UUID, material_id: str, ctx: Ctx) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, position, _ = await load_context(db, session_id)
        row = await views.material(db, session, position, material_id)
        if row is None:
            raise ServiceError(423, "not_released", "material not released")
        return {"material_id": row.material_id, "title": row.title, "content": row.content}


class ObservationIn(BaseModel):
    card_id: str
    phase: Literal["keyword", "problem", "solution", "proposal"]
    trigger_message_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None


@router.post("/sessions/{session_id}/observations")
async def post_observation(session_id: uuid.UUID, body: ObservationIn, ctx: Ctx) -> dict[str, Any]:
    async def fn(db, session, position, fx):
        found = await db.scalar(
            select(Observation).where(
                Observation.session_id == session_id, Observation.card_id == body.card_id
            )
        )
        if found is None:
            debate.require_writable(session)
            card = sc.card(position.scenario, body.card_id)
            fired_at = ctx.clock.now()
            if body.trigger_message_id:
                trigger = await db.get(Message, body.trigger_message_id)
                if trigger is not None and trigger.session_id == session_id:
                    fired_at = trigger.created_at
            found = Observation(
                session_id=session_id,
                card_id=body.card_id,
                element=card["element"],
                phase=body.phase,
                fired_at=fired_at,
                trigger_message_id=body.trigger_message_id,
                run_id=body.run_id,
                created_at=ctx.clock.now(),
            )
            db.add(found)
            await db.flush()
        return {
            "observation_id": str(found.id),
            "fired_at": iso(found.fired_at),
            "t_min": t_min(session, found.fired_at) or 0.0,
        }

    return await _write(ctx, session_id, fn)


async def observations_view(db, session: Session, scenario: dict) -> list[dict[str, Any]]:
    from app.models import SpeechJob, VoiceState

    voice = await db.get(VoiceState, session.id)
    heard = {}
    if voice:
        for job in (
            await db.scalars(
                select(SpeechJob).where(SpeechJob.session_id == session.id, SpeechJob.kind == "tts")
            )
        ).all():
            if job.data.get("status") == "heard":
                for card_id in job.data.get("card_ids", []):
                    heard.setdefault(card_id, set()).add(job.data["seat"])
    rows = (
        await db.scalars(
            select(Observation)
            .where(Observation.session_id == session.id)
            .order_by(Observation.fired_at)
        )
    ).all()
    cards = {
        c.card_id: c
        for c in (
            await db.scalars(select(SessionCard).where(SessionCard.session_id == session.id))
        ).all()
    }
    out = []
    for o in rows:
        card_def = sc.card(scenario, o.card_id)
        need = sc.card_required_count(card_def)
        delivered_seats = cards[o.card_id].delivered_seats if o.card_id in cards else []
        if voice and need:
            delivered_seats = list(heard.get(o.card_id, set()))
        out.append(
            {
                "observation_id": str(o.id),
                "card_id": o.card_id,
                "element": o.element,
                "phase": o.phase,
                "fired_at": iso(o.fired_at),
                "t_min": t_min(session, o.fired_at) or 0.0,
                "trigger_message_id": str(o.trigger_message_id) if o.trigger_message_id else None,
                "delivered": len(delivered_seats) >= need,
            }
        )
    return out


@router.get("/sessions/{session_id}/observations")
async def get_observations(session_id: uuid.UUID, ctx: Ctx) -> list[dict[str, Any]]:
    async with ctx.session_factory() as db:
        session, position, _ = await load_context(db, session_id)
        return await observations_view(db, session, position.scenario)


@router.get("/sessions/{session_id}/transcript_span")
async def transcript_span(
    session_id: uuid.UUID, message_id: uuid.UUID, ctx: Ctx, before: int = 5, after: int = 3
) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, _, _ = await load_context(db, session_id)
        target = await db.get(Message, message_id)
        if target is None or target.session_id != session_id or target.channel != "room":
            raise not_found("message not found")
        base = select(Message).where(Message.session_id == session_id, Message.channel == "room")
        prev = list(
            (
                await db.scalars(
                    base.where(Message.created_at < target.created_at)
                    .order_by(Message.created_at.desc())
                    .limit(before)
                )
            ).all()
        )
        prev.reverse()
        nxt = (
            await db.scalars(
                base.where(Message.created_at > target.created_at)
                .order_by(Message.created_at)
                .limit(after)
            )
        ).all()
        [t] = await _with_intents(db, session, [target])
        return {
            "target": t,
            "before": await _with_intents(db, session, prev),
            "after": await _with_intents(db, session, list(nxt)),
        }


async def contributions_view(db, session: Session) -> dict[str, Any]:
    options = (
        await db.scalars(
            select(Option).where(Option.session_id == session.id).order_by(Option.created_at)
        )
    ).all()
    slots = (await db.scalars(select(Slot).where(Slot.session_id == session.id))).all()
    adopted = {s.option_id for s in slots if s.status == "confirmed" and s.option_id}
    proposer = {o.id: o.proposer for o in options}
    return {
        "options": [
            {
                "option_id": str(o.id),
                "phase": o.phase,
                "label": o.label,
                "first_proposer": o.proposer,
                "source_message_id": str(o.source_message_id) if o.source_message_id else None,
                "t_min": t_min(session, o.created_at) or 0.0,
                "adopted": o.id in adopted,
            }
            for o in options
        ],
        "slots": [
            {
                "phase": s.phase,
                "option_id": str(s.option_id) if s.option_id else None,
                "first_proposer": proposer.get(s.option_id),
                "filled_by": s.filled_by,
                "resolution": s.resolution,
            }
            for s in sorted(slots, key=lambda s: sc.PHASES.index(s.phase))
            if s.status == "confirmed"
        ],
    }


@router.get("/sessions/{session_id}/contributions")
async def contributions(session_id: uuid.UUID, ctx: Ctx) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, _, _ = await load_context(db, session_id)
        return await contributions_view(db, session)


@router.get("/sessions/{session_id}/rubric")
async def rubric(session_id: uuid.UUID, ctx: Ctx) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        _, position, _ = await load_context(db, session_id)
        return reports.rubric_view(position.scenario)


@router.post("/sessions/{session_id}/evaluations")
async def save_evaluation(session_id: uuid.UUID, body: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, position, _ = await load_context(db, session_id, lock=True)
        for key in (
            "run_id",
            "rubric_version",
            "prompt_versions",
            "model",
            "dimensions",
            "elements",
        ):
            if key not in body:
                raise ServiceError(422, "invalid_input", f"missing {key}")
        try:
            result = await reports.save_evaluation(db, session, position, body, ctx.clock.now())
        except (ValueError, TypeError, KeyError) as exc:
            raise ServiceError(422, "invalid_input", str(exc)) from exc
        await db.commit()
        return result


@router.get("/sessions/{session_id}/evaluations")
async def get_evaluations(
    session_id: uuid.UUID, ctx: Ctx, run_id: uuid.UUID | None = None
) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, _, _ = await load_context(db, session_id)
        result = await reports.get_evaluations(db, session, run_id)
        if result is None:
            raise not_found("no evaluation")
        return result


@router.get("/sessions/{session_id}/facts")
async def get_facts(session_id: uuid.UUID, ctx: Ctx) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, position, _ = await load_context(db, session_id)
        return await facts.compute(db, session, position)


@router.get("/sessions/{session_id}/slots")
async def get_slots(session_id: uuid.UUID, ctx: Ctx) -> list[dict[str, Any]]:
    async with ctx.session_factory() as db:
        session, _, _ = await load_context(db, session_id)
        rows = (await db.scalars(select(Slot).where(Slot.session_id == session_id))).all()
        return [slot_full(session, s) for s in sorted(rows, key=lambda s: sc.PHASES.index(s.phase))]


@router.get("/sessions/{session_id}/assistant_calls")
async def assistant_calls(session_id: uuid.UUID, ctx: Ctx) -> list[dict[str, Any]]:
    """행마다 요청·응답 원문(request·response). 워커가 Report ai_usage 인용을 여기서 만든다."""
    async with ctx.session_factory() as db:
        await load_context(db, session_id)
        rows = (
            await db.scalars(
                select(AssistantCall)
                .where(AssistantCall.session_id == session_id)
                .order_by(AssistantCall.created_at)
            )
        ).all()
        ids = [c.request_message_id for c in rows] + [
            c.response_message_id for c in rows if c.response_message_id
        ]
        texts = (
            {
                m.id: m.content
                for m in (await db.scalars(select(Message).where(Message.id.in_(ids)))).all()
            }
            if ids
            else {}
        )
        return [
            {
                "id": str(c.id),
                "request_message_id": str(c.request_message_id),
                "response_message_id": str(c.response_message_id)
                if c.response_message_id
                else None,
                "request": texts.get(c.request_message_id, ""),
                "response": texts.get(c.response_message_id, "") if c.response_message_id else "",
                "cited_material_ids": list(c.cited_material_ids),
                "created_at": iso(c.created_at),
            }
            for c in rows
        ]


class DraftIn(BaseModel):
    content: dict[str, Any]
    usage: dict[str, Any] | None = None


@router.post("/sessions/{session_id}/report/draft")
async def report_draft(session_id: uuid.UUID, body: DraftIn, ctx: Ctx) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, _, _ = await load_context(db, session_id, lock=True)
        result = await reports.save_draft(db, session, body.model_dump(), ctx.clock.now())
        await db.commit()
        return result


class FinalIn(BaseModel):
    content: dict[str, Any]


@router.post("/sessions/{session_id}/report/final")
async def report_final(session_id: uuid.UUID, body: FinalIn, ctx: Ctx) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, _, _ = await load_context(db, session_id, lock=True)
        result = await reports.save_final(db, session, body.model_dump(), ctx.clock.now())
        await db.commit()
        return result


class FailedIn(BaseModel):
    error: str


@router.post("/sessions/{session_id}/report_failed")
async def report_failed(session_id: uuid.UUID, body: FailedIn, ctx: Ctx) -> dict[str, Any]:
    async def fn(db, session, position, fx):
        return reports.report_failed(session, body.error, ctx.clock.now(), fx)

    return await _write(ctx, session_id, fn)


class TestSessionIn(BaseModel):
    session_minutes: Literal[60, 15] = 15
    alias: str | None = None
    company_id: uuid.UUID | None = None
    admin_email: str | None = None
    start_at: Literal["entry", "keyword", "problem", "solution", "proposal", "ended"] = "entry"
    stage: str | None = None


def _require_test_api(ctx: Ctx) -> None:
    """의존성이라 본문 검증보다 먼저 돈다: 꺼져 있으면 본문과 상관없이 404."""
    if not ctx.settings.enable_test_api:
        raise not_found("test api disabled")


TestApi = [Depends(_require_test_api)]


@router.post("/test/sessions", dependencies=TestApi)
async def test_session(body: TestSessionIn, ctx: Ctx) -> dict[str, Any]:
    """단계 시드를 재생한 세션(ENABLE_TEST_API=1일 때만, 아니면 404)."""
    return await test_sessions.create(ctx, body.model_dump())


class AdvanceIn(BaseModel):
    seconds: int | None = None
    to: str | None = None


@router.post("/test/sessions/{session_id}/advance", dependencies=TestApi)
async def test_advance(session_id: uuid.UUID, body: AdvanceIn, ctx: Ctx) -> dict[str, Any]:
    """시간 당기기. 시간 초과·카드·침묵 초대·자동 제출은 다음 스케줄러 틱이 평소대로 처리한다."""
    return await test_sessions.advance(ctx, session_id, body.model_dump())


class FakeTranscriptIn(BaseModel):
    content: str


@router.post("/test/voice-transcript", dependencies=TestApi)
async def fake_transcript(body: FakeTranscriptIn, ctx: Ctx) -> dict[str, bool]:
    """FakeSpeech 전사 대본 설정. 내부 테스트 API·가짜 공급자에서만 허용."""
    from app.voice.speech import FakeSpeech

    if not isinstance(ctx.speech, FakeSpeech):
        raise not_found()
    ctx.speech.transcript = body.content
    return {"ok": True}
