"""지원자 REST(/s, /sessions, 포트 8000). 계약: docs/contracts/api.md "지원자 REST"·"인증
생명주기"."""

import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.auth import (
    Bearer,
    Principal,
    _principal,
    debrief_token,
    hash_invite_token,
    require_candidate,
    require_debrief_reader,
    session_token,
)
from app.masking import mask
from app.models import (
    AssistantCall,
    Company,
    DebriefSurvey,
    Invite,
    MaterialView,
    Message,
    Position,
    Proposal,
    Session,
)
from app.services import budget, debate, views
from app.services import proposal as proposal_service
from app.services import session as session_service
from app.services.common import (
    ENDED_STATUSES,
    Effects,
    ServiceError,
    conflict,
    invalid,
    load_context,
    not_found,
)
from app.services.context import AppContext, get_context

router = APIRouter()
Ctx = Annotated[AppContext, Depends(get_context)]
Candidate = Annotated[Principal, Depends(require_candidate)]
Reader = Annotated[Principal, Depends(require_debrief_reader)]


def entry_notice(minutes: int, contact: str) -> str:
    return (
        f"{minutes}분 동안 이어서 진행되며 중간 휴식이 없습니다. PC를 권장합니다. "
        "AI 동료 3명과 음성으로 키워드 → 문제 상황 → 해결책을 정하고, "
        "마이크 발언은 Azure Speech에서 전사하며 최종 텍스트를 기록합니다. "
        "원음은 임시 처리 후 삭제합니다. "
        "기획안은 혼자 작성해 제출합니다. "
        "토론 결과가 내 제안과 다를 수 있고, 안의 질이 아니라 과정의 태도를 봅니다. "
        "연결이 끊겨도 시간은 흐릅니다. 외부 도구는 막지 않지만 안의 AI 어시스턴트 요청·응답은 "
        f"사실로 기록되며, AI가 평가 수준을 매기지 않습니다. 문의: {contact}"
    )


async def _by_token(db, token: str) -> tuple[Session, Invite, Position]:
    invite = await db.scalar(select(Invite).where(Invite.token_hash == hash_invite_token(token)))
    if invite is None:
        raise not_found("unknown invite")
    session = await db.scalar(select(Session).where(Session.invite_id == invite.id))
    position = await db.get(Position, invite.position_id)
    return session, invite, position


def _check_open(ctx: AppContext, session: Session, invite: Invite) -> None:
    if session.status == "cancelled" or invite.cancelled_at is not None:
        raise ServiceError(410, "cancelled", "invite cancelled")
    if session.status == "invited" and ctx.clock.now() >= invite.expires_at:
        raise ServiceError(410, "expired", "invite expired")


@router.get("/s/{token}")
async def entry(token: str, ctx: Ctx) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, invite, position = await _by_token(db, token)
        _check_open(ctx, session, invite)
        company = await db.get(Company, position.company_id)
        contact = ctx.settings.contact_email
        body = {
            "session_id": str(session.id),
            "company_name": company.name,
            "duration_minutes": position.session_minutes,
            "status": session.status,
            "default_display_name": invite.alias,
            "elements_names": [e["name"] for e in position.scenario["element_info"]],
            "entry_notice": entry_notice(position.session_minutes, contact),
            "contact_email": contact,
        }
        if session.status in ENDED_STATUSES and session.ended_at is not None:
            body["debrief_token"] = debrief_token(session.id, views.debrief_until(session))
        if session.status == "failed" and session.failed_stage:
            body["failed_stage"] = session.failed_stage
        return body


class ConsentIn(BaseModel):
    display_name: str | None = None


def clean_display_name(value: str | None, alias: str) -> str:
    name = re.sub(r"\s+", " ", (value or "")).strip()
    if not name:
        return alias
    name = mask(name)
    if len(name) > 20:
        raise invalid("invalid_input", "display_name must be 1-20 characters")
    return name


@router.post("/s/{token}/consent")
async def consent(token: str, body: ConsentIn, ctx: Ctx) -> dict[str, str]:
    async with ctx.session_factory() as db:
        session, invite, position = await _by_token(db, token)
        _check_open(ctx, session, invite)
        session_service.consent(
            session, clean_display_name(body.display_name, invite.alias), ctx.clock.now()
        )
        await db.commit()
        return {
            "session_token": session_token(session.id, position.session_minutes),
            "session_id": str(session.id),
        }


@router.post("/s/{token}/resume")
async def resume(token: str, ctx: Ctx) -> dict[str, str]:
    async with ctx.session_factory() as db:
        session, invite, position = await _by_token(db, token)
        if session.status != "in_progress":
            raise conflict("conflict", "session is not in progress")
        return {
            "session_token": session_token(session.id, position.session_minutes),
            "session_id": str(session.id),
        }


@router.post("/s/{token}/start")
async def start_session(token: str, ctx: Ctx, creds: Bearer) -> dict[str, Any]:
    principal = _principal(creds)
    if ctx.settings.voice_enabled:
        await ctx.speech.verify()
    fx = Effects()
    # 세션 id는 후보자 토큰의 subject(아니면 아래에서 403)
    async with ctx.ordered(principal.subject):
        async with ctx.session_factory() as db:
            session, invite, position = await _by_token(db, token)
            if principal.role != "candidate" or principal.subject != session.id:
                raise ServiceError(403, "forbidden", "other session")
            session = await db.get(Session, session.id, with_for_update=True)
            if session.status == "invited":
                _check_open(ctx, session, invite)
                if session.consented_at is None:
                    raise conflict("conflict", "consent first")
                await session_service.start(db, session, position, ctx.clock, fx)
                if ctx.settings.voice_enabled:
                    from app.voice import quota

                    if not position.scenario.get("voice_policy"):
                        raise conflict("position_outdated", "새 음성 채용 건으로 초대해 주세요")
                    from app.voice.policy import pinned_policy

                    if position.scenario["voice_policy"]["provider"] != pinned_policy()["provider"]:
                        raise conflict("voice_policy_mismatch")
                    await quota.admission(db, session, ctx.clock.now())
                    from app.voice.controller import row

                    await row(db, session.id)
                invite.used_at = ctx.clock.now()
                await db.commit()
            elif session.status != "in_progress":
                raise conflict("conflict", f"cannot start from {session.status}")
            result = {
                "session_id": str(session.id),
                "started_at": _iso(session.started_at),
                "ends_at": _iso(session.ends_at),
                "phase": "keyword",
            }
        ctx.emit(session.id, fx)
    await ctx.publish(session.id, fx)
    return result


def _iso(value):
    from app.services.common import iso

    return iso(value)


@router.get("/sessions/{session_id}/state")
async def state(session_id: uuid.UUID, ctx: Ctx, _: Candidate) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, position, invite = await load_context(db, session_id)
        if session.status == "invited" and session.consented_at is None:
            raise conflict("conflict", "consent first")
        return await views.state(
            db,
            session,
            position,
            invite,
            ctx.clock.now(),
            ctx.hub.typing_seats(session_id),
            ctx.hub.last_seq(session_id),
        )


class ConfirmIn(BaseModel):
    value: str | None = None
    option_key: str | None = None


@router.post("/sessions/{session_id}/slots/{phase}/confirm")
async def confirm(
    session_id: uuid.UUID, phase: str, body: ConfirmIn, ctx: Ctx, _: Candidate
) -> dict[str, Any]:
    fx = Effects()
    async with ctx.ordered(session_id):
        async with ctx.session_factory() as db:
            session, position, _invite = await load_context(db, session_id, lock=True)
            if ctx.settings.voice_enabled:
                from app.models import VoiceState

                voice = await db.get(VoiceState, session_id)
                if voice and voice.data.get("state") in (
                    "USER_SPEAKING",
                    "TRANSCRIBING",
                    "PROCESSING",
                ):
                    raise conflict("voice_turn_conflict", "발언 제출을 마친 뒤 정해 주세요")
            if phase not in ("keyword", "problem", "solution"):
                raise conflict("phase_not_open", "unknown phase")
            result = await debate.confirm_slot(
                db, session, position, phase, body.value, body.option_key, ctx.clock, fx
            )
            await db.commit()
        ctx.emit(session_id, fx)
    await ctx.publish(session_id, fx)
    return result


@router.get("/sessions/{session_id}/materials")
async def materials(session_id: uuid.UUID, ctx: Ctx, _: Candidate) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, position, _invite = await load_context(db, session_id)
        return await views.materials(db, session, position)


@router.get("/sessions/{session_id}/materials/{material_id}")
async def material(
    session_id: uuid.UUID, material_id: str, ctx: Ctx, _: Candidate
) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, position, _invite = await load_context(db, session_id)
        row = await views.material(db, session, position, material_id)
        if row is None:
            raise ServiceError(423, "not_released", "material not released")
        db.add(
            MaterialView(session_id=session.id, material_id=material_id, viewed_at=ctx.clock.now())
        )
        await db.commit()
        return {"material_id": row.material_id, "title": row.title, "content": row.content}


class AssistantIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


@router.post("/sessions/{session_id}/assistant")
async def assistant(
    session_id: uuid.UUID, body: AssistantIn, ctx: Ctx, _: Candidate
) -> dict[str, Any]:
    """메일박스 밖에서 어시스턴트 실행. 요청·응답 메시지(channel=assistant)와 assistant_calls
    기록."""
    async with ctx.session_factory() as db:
        session, position, _invite = await load_context(db, session_id)
        if session.status != "in_progress":
            raise conflict("session_ending", "session is not in progress")
        limit = position.scenario["assistant"]["limit_per_session"]
        used = await db.scalar(select(func.count()).where(AssistantCall.session_id == session.id))
        if used >= limit:
            raise ServiceError(429, "assistant_limit", "assistant limit reached")
        request = Message(
            session_id=session.id,
            channel="assistant",
            sender="candidate",
            phase=session.phase,
            content=mask(body.prompt),
            created_at=ctx.clock.now(),
        )
        db.add(request)
        await db.commit()
    call_id = uuid.uuid4()
    answer = await ctx.assistant.answer(session_id, body.prompt, call_id)
    async with ctx.session_factory() as db:
        session = await db.get(Session, session_id)
        response = Message(
            session_id=session_id,
            channel="assistant",
            sender="assistant",
            phase=session.phase if session.phase != "completed" else None,
            content=answer.content,
            cited_material_ids=answer.cited_material_ids,
            meta={"usage": answer.usage},
            created_at=ctx.clock.now(),
        )
        db.add(response)
        await db.flush()
        db.add(
            AssistantCall(
                id=call_id,
                session_id=session_id,
                request_message_id=request.id,
                response_message_id=response.id,
                cited_material_ids=answer.cited_material_ids,
                tool_calls=answer.tool_calls,
                created_at=ctx.clock.now(),
            )
        )
        await budget.add_debate_cost(db, session_id, answer.usage)
        await db.commit()
    return {
        "call_id": str(call_id),
        "message_id": str(response.id),
        "content": answer.content,
        "cited_material_ids": answer.cited_material_ids,
        "tool_calls": answer.tool_calls,
    }


@router.get("/sessions/{session_id}/proposal")
async def get_proposal(session_id: uuid.UUID, ctx: Ctx, _: Reader) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        _s, position, _i = await load_context(db, session_id)
        proposal = await db.get(Proposal, session_id)
        return proposal_service.serialize(proposal, position.scenario["proposal_template"])


class ProposalIn(BaseModel):
    sections: dict[str, str]
    revision: int


@router.put("/sessions/{session_id}/proposal")
async def put_proposal(
    session_id: uuid.UUID, body: ProposalIn, ctx: Ctx, _: Candidate
) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, position, _i = await load_context(db, session_id)
        result = await proposal_service.put(
            db,
            session,
            body.sections,
            body.revision,
            position.scenario["proposal_template"],
            ctx.clock,
        )
        await db.commit()
        return result


class SubmitIn(BaseModel):
    blank_warning_shown: bool | None = None


@router.post("/sessions/{session_id}/proposal/submit")
async def submit(
    session_id: uuid.UUID, ctx: Ctx, _: Candidate, body: SubmitIn | None = None
) -> dict[str, Any]:
    fx = Effects()
    async with ctx.ordered(session_id):
        async with ctx.session_factory() as db:
            session, position, _i = await load_context(db, session_id, lock=True)
            result = await proposal_service.submit(
                db,
                session,
                position.scenario["proposal_template"],
                ctx.clock,
                fx,
                body.blank_warning_shown if body else None,
            )
            await db.commit()
        ctx.emit(session_id, fx)
    await ctx.publish(session_id, fx)
    return result


async def _debrief_session(db, ctx: AppContext, session_id: uuid.UUID):
    session, position, invite = await load_context(db, session_id)
    until = views.debrief_until(session)
    if session.status not in ENDED_STATUSES or until is None:
        raise conflict("conflict", "session not ended")
    if ctx.clock.now() > until:
        raise ServiceError(410, "expired", "debrief expired")
    return session, position, invite


@router.get("/sessions/{session_id}/debrief")
async def debrief(session_id: uuid.UUID, ctx: Ctx, _: Reader) -> dict[str, Any]:
    async with ctx.session_factory() as db:
        session, position, invite = await _debrief_session(db, ctx, session_id)
        return await views.debrief(db, session, position, invite, ctx.settings.contact_email)


class SurveyAnswer(BaseModel):
    q: str
    value: int = Field(ge=1, le=5)


class SurveyIn(BaseModel):
    answers: list[SurveyAnswer]
    free_comment: str | None = Field(default=None, max_length=500)


@router.post("/sessions/{session_id}/debrief/survey")
async def survey(session_id: uuid.UUID, body: SurveyIn, ctx: Ctx, _: Reader) -> dict[str, bool]:
    async with ctx.session_factory() as db:
        await _debrief_session(db, ctx, session_id)
        if await db.get(DebriefSurvey, session_id) is None:
            if {a.q for a in body.answers} - {"natural", "fair"}:
                raise invalid("invalid_input", "unknown survey question")
            db.add(
                DebriefSurvey(
                    session_id=session_id,
                    answers=[a.model_dump() for a in body.answers],
                    free_comment=body.free_comment,
                    created_at=ctx.clock.now(),
                )
            )
            await db.commit()
        return {"submitted": True}
