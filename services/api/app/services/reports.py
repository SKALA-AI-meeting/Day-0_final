"""평가 저장(save_evaluation)·리포트 draft/final·담당자 검토·리포트 조회 병합·전체 기록.

api는 평가 형식과 세션 소속만 검증한다(R5-14). 판단 규칙(근거 수·확신도 상한)은 워커 몫이다.
저장된 reports.content는 불변이고 final은 draft content 그대로(해시 동일). 조정은 GET 때 병합한다.
"""

import hashlib
import json
import math
import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import queue
from app.models import (
    AssistantCall,
    ConnectionGap,
    DebriefSurvey,
    Evaluation,
    EvaluationRun,
    Invite,
    MaterialView,
    Message,
    Option,
    Position,
    Proposal,
    Report,
    ReportNote,
    RubricAdjustment,
    Session,
    Slot,
)
from app.services import budget
from app.services.common import Effects, conflict, invalid, iso, purge_at, slot_outcome, t_min
from app.services.views import gaps, seats_typed

COMPLETED_PLUS = ("completed", "report_ready", "approving", "reviewed", "failed")
_REF = re.compile(r"^(msg|slot|proposal|contribution):(.+)$")


def content_hash(content: dict[str, Any]) -> str:
    raw = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def rubric_dimensions(scenario: dict) -> dict[str, dict]:
    return {
        d["dimension_id"]: {**d, "element": e["element"]}
        for e in scenario["rubric"]["elements"]
        for d in e["dimensions"]
    }


def no_record_codes(scenario: dict) -> set[str]:
    return {r["code"] for r in scenario["rubric"]["no_record_reasons"]}


def rubric_view(scenario: dict) -> dict[str, Any]:
    rubric = scenario["rubric"]
    return {
        "rubric_version": rubric["rubric_version"],
        "elements": [
            {
                "element": e["element"],
                "name": e["name"],
                "dimensions": [
                    {
                        "dimension_id": d["dimension_id"],
                        "name": d["name"],
                        "definition": d["definition"],
                        "core_cards": d.get("core_cards", []),
                        # 결정 전후 구분·보조 장면 구간. 이 값이 생기기 전에 복사된 척도에는 없다
                        **{k: d[k] for k in ("decision_timing", "aux_scenes") if k in d},
                        "levels": [
                            {
                                "level": lv["level"],
                                "anchor": lv["anchor"],
                                "reviewer_hint": lv.get("reviewer_hint", ""),
                            }
                            for lv in d["levels"]
                        ],
                    }
                    for d in e["dimensions"]
                ],
            }
            for e in rubric["elements"]
        ],
        # state(기록 상태, 결정 A3)는 이 값이 생기기 전에 복사된 척도에는 없다
        "no_record_reasons": [
            {
                "code": r["code"],
                "label": r["label"],
                **({"state": r["state"]} if "state" in r else {}),
            }
            for r in rubric["no_record_reasons"]
        ],
        **({"record_states": rubric["record_states"]} if "record_states" in rubric else {}),
        "cards": [
            # window는 cards.yaml 값 그대로(관찰 창, 평가 입력 경계가 아님)
            {
                "card_id": c["card_id"],
                "element": c["element"],
                "stage_id": c["stage_id"],
                "window": c["window"],
            }
            for c in scenario["cards"]
        ],
    }


# ---- 평가 저장 -----------------------------------------------------------------------------------


async def _validate_refs(db: AsyncSession, session: Session, refs: list[Any], template: list[str]):
    for ref in refs:
        match = _REF.match(ref) if isinstance(ref, str) else None
        if not match:
            raise invalid("invalid_input", f"bad evidence ref: {ref}")
        kind, value = match.groups()
        ok = False
        try:
            if kind == "msg":
                m = await db.get(Message, uuid.UUID(value))
                ok = m is not None and m.session_id == session.id and m.channel == "room"
            elif kind == "slot":
                s = await db.get(Slot, (session.id, value))
                ok = s is not None and s.status == "confirmed"
            elif kind == "proposal":
                ok = value in template
            elif kind == "contribution":
                o = await db.get(Option, uuid.UUID(value))
                ok = o is not None and o.session_id == session.id
        except ValueError:
            ok = False
        if not ok:
            raise invalid("invalid_input", f"evidence ref not in session: {ref}")


async def save_evaluation(
    db: AsyncSession, session: Session, position: Position, body: dict[str, Any], now: datetime
) -> dict[str, Any]:
    if session.status not in COMPLETED_PLUS:
        raise conflict("conflict", "session not completed")
    run_id = uuid.UUID(str(body["run_id"]))
    found = await db.get(EvaluationRun, (session.id, run_id))
    if found is not None:
        saved = len(
            (
                await db.scalars(
                    select(Evaluation.id).where(
                        Evaluation.session_id == session.id, Evaluation.run_id == run_id
                    )
                )
            ).all()
        )
        return {"run_id": str(run_id), "existing": True, "saved": saved}
    scenario = position.scenario
    known = rubric_dimensions(scenario)
    codes = no_record_codes(scenario)
    dims = body.get("dimensions") or []
    ids = [d.get("dimension_id") for d in dims]
    if len(ids) != len(set(ids)) or set(ids) != set(known):
        raise invalid("invalid_input", "need each rubric dimension exactly once")
    for d in dims:
        level, reason = d.get("level"), d.get("no_record_reason")
        if (level is None) == (reason is None):
            raise invalid("invalid_input", f"{d['dimension_id']}: level xor no_record_reason")
        if level is not None and (not isinstance(level, int) or not 1 <= level <= 5):
            raise invalid("invalid_input", f"{d['dimension_id']}: level out of range")
        if reason is not None and (reason not in codes or d.get("confidence") is not None):
            raise invalid("invalid_input", f"{d['dimension_id']}: bad no_record_reason")
        if d.get("confidence") not in (None, "high", "medium", "low"):
            raise invalid("invalid_input", f"{d['dimension_id']}: bad confidence")
        await _validate_refs(
            db, session, d.get("evidence_refs") or [], scenario["proposal_template"]
        )
    elements = body.get("elements") or []
    if sorted(e.get("element") for e in elements) != sorted(scenario["elements"]):
        raise invalid("invalid_input", "need each element exactly once")
    db.add(
        EvaluationRun(
            session_id=session.id,
            run_id=run_id,
            rubric_version=body["rubric_version"],
            prompt_versions=body["prompt_versions"],
            model=body["model"],
            elements=elements,
            has_failed=any(d.get("no_record_reason") == "evaluate_failed" for d in dims),
            created_at=now,
        )
    )
    await db.flush()
    for d in dims:
        db.add(
            Evaluation(
                session_id=session.id,
                run_id=run_id,
                dimension_id=d["dimension_id"],
                level=d.get("level"),
                no_record_reason=d.get("no_record_reason"),
                evidence_refs=d.get("evidence_refs") or [],
                rationale=d.get("rationale") or "",
                confidence=d.get("confidence"),
                created_at=now,
            )
        )
    return {"run_id": str(run_id), "existing": False, "saved": len(dims)}


async def get_evaluations(
    db: AsyncSession, session: Session, run_id: uuid.UUID | None
) -> dict[str, Any] | None:
    query = select(EvaluationRun).where(EvaluationRun.session_id == session.id)
    if run_id is not None:
        query = query.where(EvaluationRun.run_id == run_id)
    run = await db.scalar(query.order_by(EvaluationRun.created_at.desc()).limit(1))
    if run is None:
        return None
    rows = (
        await db.scalars(
            select(Evaluation).where(
                Evaluation.session_id == session.id, Evaluation.run_id == run.run_id
            )
        )
    ).all()
    return {
        "run_id": str(run.run_id),
        "rubric_version": run.rubric_version,
        "prompt_versions": run.prompt_versions,
        "model": run.model,
        "elements": run.elements,
        "dimensions": [
            {
                "dimension_id": r.dimension_id,
                "level": r.level,
                "no_record_reason": r.no_record_reason,
                "evidence_refs": r.evidence_refs,
                "rationale": r.rationale,
                "confidence": r.confidence,
            }
            for r in rows
        ],
        "has_failed": run.has_failed,
        "created_at": iso(run.created_at),
    }


# ---- draft / final -------------------------------------------------------------------------------


async def save_draft(db: AsyncSession, session: Session, body: dict[str, Any], now: datetime):
    if session.status in ("report_ready", "approving", "reviewed"):
        return {"status": "report_ready"}
    if session.status != "completed":
        raise conflict("conflict", f"cannot save draft from {session.status}")
    content = body["content"]
    run_id = (content.get("evaluation") or {}).get("run_id")
    report = await db.get(Report, session.id)
    if report is None:
        report = Report(session_id=session.id, stage="draft", content=content, content_hash="")
        db.add(report)
    report.stage = "draft"
    report.content = content
    report.content_hash = content_hash(content)
    report.eval_run_id = uuid.UUID(run_id) if run_id else None
    report.generated_at = now
    report.usage = body.get("usage") or {}
    await budget.add_report_cost(db, session.id, report.usage)
    session.status = "report_ready"
    return {"status": "report_ready"}


async def save_final(db: AsyncSession, session: Session, body: dict[str, Any], now: datetime):
    report = await db.get(Report, session.id)
    if session.status == "reviewed" and report is not None:
        return {"status": "reviewed", "reviewed_at": iso(report.reviewed_at)}
    if session.status != "approving" or report is None:
        raise conflict("conflict", f"cannot finalize from {session.status}")
    if content_hash(body["content"]) != report.content_hash:
        raise conflict("draft_mismatch", "final content differs from draft")
    report.stage = "final"
    report.reviewed_at = report.reviewed_at or now
    session.status = "reviewed"
    return {"status": "reviewed", "reviewed_at": iso(report.reviewed_at)}


def report_failed(session: Session, error: str, now: datetime, fx: Effects) -> dict[str, str]:
    from app.services import session as session_service

    if session.status in ("completed", "report_ready", "approving"):
        session_service.fail(session, "report", error[:500], now, fx)
    return {"status": "failed"}


# ---- 검토 ----------------------------------------------------------------------------------------


def _review_body(adjustments: list[dict], reviewer: str) -> dict[str, Any]:
    return {"reviewer_name": reviewer, "adjustments": adjustments}


async def review(
    db: AsyncSession,
    session: Session,
    position: Position,
    admin_id: uuid.UUID,
    body: dict[str, Any],
    now: datetime,
    fx: Effects,
) -> dict[str, str]:
    """차원 단위 조정 + 승인 한 트랜잭션. 판정 순서: 상태(409) → 검증(422)."""
    report = await db.get(Report, session.id)
    adjustments = body.get("adjustments") or []
    reviewer = (body.get("reviewer_name") or "").strip()
    if session.status in ("approving", "reviewed") and report is not None:
        stored = await _stored_review(db, session)
        try:
            incoming = _review_body(_normalize_adjustments(adjustments), reviewer)
        except (KeyError, AttributeError):
            incoming = None
        if stored == incoming:
            return {"status": "approving"}
        raise conflict("already_reviewed", "already reviewed with a different body")
    if report is None or session.status != "report_ready":
        raise conflict("conflict", "report not ready")
    known = rubric_dimensions(position.scenario)
    codes = no_record_codes(position.scenario)
    if not reviewer or len(reviewer) > 40:
        raise invalid("invalid_input", "reviewer_name required")
    seen = set()
    for a in adjustments:
        if not isinstance(a, dict):
            raise invalid("invalid_input", "each adjustment must be an object")
        dim = a.get("dimension_id")
        if dim not in known:
            raise invalid("invalid_dimension", f"unknown dimension {dim}")
        if dim in seen:
            raise invalid("duplicate_dimension", f"duplicate dimension {dim}")
        seen.add(dim)
        if not (a.get("memo") or "").strip():
            raise invalid("memo_required", f"memo required for {dim}")
        level, reason = a.get("level"), a.get("no_record_reason")
        if level is not None and (not isinstance(level, int) or not 1 <= level <= 5):
            raise invalid("invalid_input", "level out of range")
        if (level is None) == (reason is None) or (reason is not None and reason not in codes):
            raise invalid("invalid_input", "level xor valid no_record_reason")
    for a in adjustments:
        db.add(
            RubricAdjustment(
                session_id=session.id,
                dimension_id=a["dimension_id"],
                level=a.get("level"),
                no_record_reason=a.get("no_record_reason"),
                memo=a["memo"].strip(),
                adjusted_by=admin_id,
                reviewer_name=reviewer,
                adjusted_at=now,
            )
        )
    report.reviewer_name = reviewer
    report.reviewed_at = now  # 검토 완료(승인) 시각. final은 이 값을 그대로 둔다
    session.status = "approving"
    session.approve_enqueued_at = None
    fx.publish.append(queue.APPROVE)
    return {"status": "approving"}


async def _stored_review(db: AsyncSession, session: Session) -> dict[str, Any]:
    rows = (
        await db.scalars(
            select(RubricAdjustment)
            .where(RubricAdjustment.session_id == session.id)
            .order_by(RubricAdjustment.adjusted_at)
        )
    ).all()
    report = await db.get(Report, session.id)
    adjustments = []
    for r in rows:
        item = {"dimension_id": r.dimension_id, "level": r.level, "memo": r.memo}
        if r.no_record_reason is not None:
            item["no_record_reason"] = r.no_record_reason
        adjustments.append(item)
    return _review_body(adjustments, report.reviewer_name or "")


def _normalize_adjustments(items: list[dict]) -> list[dict]:
    out = []
    for a in items:
        item = {
            "dimension_id": a["dimension_id"],
            "level": a.get("level"),
            "memo": a["memo"].strip(),
        }
        if a.get("no_record_reason") is not None:
            item["no_record_reason"] = a["no_record_reason"]
        out.append(item)
    return out


def round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


async def merged_report(
    db: AsyncSession, session: Session, position: Position, invite: Invite
) -> dict[str, Any] | None:
    report = await db.get(Report, session.id)
    if report is None:
        return None
    content = json.loads(json.dumps(report.content))
    # 식별 필드는 워커가 넣지만 빠졌으면 api가 채운다(자리 유형은 관리자 리포트에만 나간다)
    content.setdefault("session_id", str(session.id))
    content.setdefault("alias", invite.alias)
    content.setdefault(
        "position", {"name": position.name, "session_minutes": position.session_minutes}
    )
    content.setdefault("seats", seats_typed(position.scenario, session, invite))
    adjustments = (
        await db.scalars(
            select(RubricAdjustment)
            .where(RubricAdjustment.session_id == session.id)
            .order_by(RubricAdjustment.adjusted_at)
        )
    ).all()
    latest = {a.dimension_id: a for a in adjustments}
    for dim in content.get("dimensions", []):
        adj = latest.get(dim["dimension_id"])
        dim["adjusted_level"] = adj.level if adj else None
        dim["adjusted_no_record_reason"] = adj.no_record_reason if adj else None
        dim["adjust_memo"] = adj.memo if adj else None
    for element in content.get("elements", []):
        element["adjusted_level"] = None
        dims = [d for d in content.get("dimensions", []) if d["element"] == element["element"]]
        if any(d["dimension_id"] in latest for d in dims):
            levels = [
                (latest[d["dimension_id"]].level if d["dimension_id"] in latest else d["level"])
                for d in dims
            ]
            levels = [lv for lv in levels if lv is not None]
            element["adjusted_level"] = round_half_up(sum(levels) / len(levels)) if levels else None
    notes = (
        await db.scalars(
            select(ReportNote)
            .where(ReportNote.session_id == session.id)
            .order_by(ReportNote.created_at)
        )
    ).all()
    proposal = await db.get(Proposal, session.id)
    survey = await db.get(DebriefSurvey, session.id)
    survey_value = None
    if survey is not None:
        answers = {a["q"]: a["value"] for a in survey.answers}
        survey_value = {
            "natural": answers.get("natural"),
            "fair": answers.get("fair"),
            "free_comment": survey.free_comment,
        }
    content.setdefault("debrief", {})["survey"] = survey_value
    content["review"] = (
        {"reviewer_name": report.reviewer_name, "reviewed_at": iso(report.reviewed_at)}
        if session.status in ("approving", "reviewed")
        else None
    )
    content["notes"] = [
        {
            "id": str(n.id),
            "text": n.text,
            "reviewer_name": n.reviewer_name,
            "created_at": iso(n.created_at),
        }
        for n in notes
    ]
    content["connection_gaps"] = await gaps(db, session)
    content["purge_at"] = purge_at(session)
    content["reviewed_at"] = iso(report.reviewed_at)
    content["stage"] = report.stage
    content["proposal"] = (
        {
            "submitted_at": iso(proposal.submitted_at),
            "auto_submitted": proposal.auto_submitted,
            "sections": proposal.submitted,
        }
        if proposal and proposal.submitted
        else None
    )
    return content


async def record(db: AsyncSession, session: Session) -> list[dict[str, Any]]:
    """전체 기록 타임라인. ref = 리포트 근거 record_ref와 같은 값."""
    items: list[dict[str, Any]] = []

    def add(at, type_, by, summary, ref_id, stt_transcript=None):
        tm = t_min(session, at) or 0.0
        items.append(
            {
                "t_min": tm,
                "type": type_,
                "by": by,
                "summary": summary,
                **({"stt_transcript": stt_transcript} if stt_transcript is not None else {}),
                "ref": {"type": type_, "id": str(ref_id), "t_min": tm},
            }
        )

    room = (
        await db.scalars(
            select(Message).where(Message.session_id == session.id, Message.channel == "room")
        )
    ).all()
    for m in room:
        original = (m.meta or {}).get("stt_transcript")
        add(
            m.created_at,
            "message",
            m.sender,
            m.content,
            m.id,
            original if original is not None and original != m.content else None,
        )
        if (m.meta or {}).get("kind") == "stage_start":
            add(m.created_at, "phase", "system", f"단계 시작: {m.phase}", m.phase)
    for s in (await db.scalars(select(Slot).where(Slot.session_id == session.id))).all():
        if s.status == "confirmed":
            by = s.filled_by
            add(s.confirmed_at, "slot", by, f"{s.phase}: {s.value} ({slot_outcome(s)})", s.phase)
    for c in (
        await db.scalars(select(AssistantCall).where(AssistantCall.session_id == session.id))
    ).all():
        add(c.created_at, "assistant_call", "candidate", "어시스턴트 요청", c.id)
    for v in (
        await db.scalars(select(MaterialView).where(MaterialView.session_id == session.id))
    ).all():
        add(v.viewed_at, "material_view", "candidate", v.material_id, v.id)
    proposal = await db.get(Proposal, session.id)
    if proposal and proposal.submitted_at:
        blanks = len(proposal.submit_blank_sections or [])
        if proposal.auto_submitted:
            summary = f"자동 제출 · 빈 칸 {blanks}개"
        else:
            warned = "경고 확인 후 제출" if proposal.blank_warning_shown else "경고 없이 제출"
            summary = f"제출 · 빈 칸 {blanks}개({warned})"
        add(proposal.submitted_at, "proposal_submit", "candidate", summary, session.id)
    for g in (
        await db.scalars(select(ConnectionGap).where(ConnectionGap.session_id == session.id))
    ).all():
        add(g.disconnected_at, "connection_gap", "system", "연결 끊김", g.id)
    items.sort(key=lambda i: i["t_min"])
    return items
