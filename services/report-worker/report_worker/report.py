"""aggregate(결정적 정리)와 draft(Report 조립). 둘 다 LLM을 부르지 않는다.

- aggregate: 사실 지표는 api 계산값 그대로(facts), 칸 기록에 최초 제안자·라벨, 카드 6장의 발동·전달·
  장면 문장(`report_template` 그대로), 디브리핑용 관찰 창, 기여 대조표(contribution_check).
  수준을 정하지 않는다.
- draft: 저장된 평가(차원 행 + 요소 요약)에 차원 이름·담당자 참고 문구·근거 원문과 앞뒤 맥락
  (`get_transcript_span`, 앞 5·뒤 3)을 붙인다. 요소 summary는 평가 에이전트 문장 그대로다.
  총점·합계 필드는 없다. 조정·검토·메모·접속 끊김·기획안·설문은 api가 GET 때 병합한다.
"""

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from report_worker.evaluate import FAILED_SUMMARY, RefIndex, has_forbidden

SEATS = ("p1", "p2", "p3")
QUOTE_CHARS = 200
PHASE_WORDS = {"keyword": ("키워드",), "problem": ("문제",), "solution": ("해결",)}
# 앞 글자가 한글이면 낱말 일부("문제 ")라 제외한다
SELF_WORDS = re.compile(r"(?<![가-힣])(본인|제가|저의|저는|내가|나의|제\s)")
CREDIT_SPLIT = re.compile(r"[.\n/;]")

SpanFn = Callable[[str], Awaitable[dict[str, Any]]]


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def t_min_of(started_at: str | None, at: str | None, default: float = 0.0) -> float:
    start, moment = _ts(started_at), _ts(at)
    if start is None or moment is None:
        return default
    return round((moment - start).total_seconds() / 60, 1)


def excerpt(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS] + "…"


# ---- 기여 대조 ---------------------------------------------------------------------------------


def _name_variants(display_name: str) -> list[str]:
    # "강한결" → ["강한결", "한결"]: 성을 뺀 호칭("한결님")도 같은 사람으로 본다
    names = [display_name]
    if len(display_name) == 3:
        names.append(display_name[1:])
    return names


def credited_participants(segment: str, seat_names: dict[str, str]) -> list[str]:
    credited = [
        seat for seat, name in seat_names.items() if any(n in segment for n in _name_variants(name))
    ]
    if SELF_WORDS.search(segment):
        credited.append("candidate")
    return credited


def credit_segment(idea_credit: str, phase: str, label: str) -> str | None:
    """idea_credit에서 그 안(단계 이름 또는 라벨)을 말한 첫 조각."""
    segments = [s.strip() for s in CREDIT_SPLIT.split(idea_credit or "") if s.strip()]
    for segment in segments:
        if label and label in segment:
            return segment
    for segment in segments:
        if any(word in segment for word in PHASE_WORDS.get(phase, ())):
            return segment
    return None


def contribution_check(
    contributions: dict[str, Any], idea_credit: str, seat_names: dict[str, str]
) -> list[dict[str, Any]]:
    """채택된 칸 중 최초 제안자가 AI 동료인 안마다 실제 제안자 / 기획안에 적힌 기여자."""
    options = {o["option_id"]: o for o in contributions.get("options", [])}
    rows = []
    for slot in contributions.get("slots", []):
        option_id = slot.get("option_id")
        if not option_id or slot.get("first_proposer") not in SEATS:
            continue
        label = options.get(option_id, {}).get("label", "")
        segment = credit_segment(idea_credit, slot["phase"], label)
        rows.append(
            {
                "phase": slot["phase"],
                "option_id": option_id,
                "label": label,
                "first_proposer": slot["first_proposer"],
                "credited_in_proposal": credited_participants(segment, seat_names)
                if segment
                else [],
                "credit_quote": segment,
            }
        )
    return rows


# ---- aggregate ---------------------------------------------------------------------------------


def aggregate(
    session: dict[str, Any], record: dict[str, Any], contributions: dict[str, Any]
) -> dict[str, Any]:
    scenario = (session.get("position") or {}).get("scenario") or {}
    options = {o["option_id"]: o for o in contributions.get("options", [])}
    first_by_phase = {s["phase"]: s.get("first_proposer") for s in contributions.get("slots", [])}
    seat_names = {
        s["seat"]: s["display_name"] for s in record.get("seats", []) if s["seat"] in SEATS
    }
    observations = {o["card_id"]: o for o in record.get("observations", [])}
    idea_credit = (((record.get("proposal") or {}).get("sections")) or {}).get("idea_credit", "")

    slots = [
        {
            "phase": s["phase"],
            "value": s["value"],
            "option_id": s.get("option_id"),
            "filled_by": s["filled_by"],
            "resolution": s["resolution"],
            "outcome": s["outcome"],
            "t_min": s["t_min"],
            "first_proposer": first_by_phase.get(s["phase"]) if s.get("option_id") else None,
            "support": [
                {
                    "option_id": row["option_id"],
                    "label": options.get(row["option_id"], {}).get("label", ""),
                    "supporters": row["supporters"],
                }
                for row in s.get("support", [])
            ],
        }
        for s in record.get("slots", [])
    ]
    # 지원자가 예정 시각 전에 칸을 확정해 생기지 않은 카드(평가 사실 decisions, 결정 A3)
    preempted = {
        c
        for d in (record.get("facts") or {}).get("decisions") or []
        for c in d.get("preempted_cards") or []
    }
    cards, debrief_cards = [], []
    for card in scenario.get("cards", []):
        seen = observations.get(card["card_id"])
        cards.append(
            {
                "card_id": card["card_id"],
                "element": card["element"],
                "fired_at": seen["t_min"] if seen else None,
                "delivered": bool(seen and seen.get("delivered")),
                "preempted": seen is None and card["card_id"] in preempted,
                "scene": card.get("report_template", ""),
            }
        )
        debrief_cards.append(
            {
                "card_id": card["card_id"],
                "element": card["element"],
                "phase": card.get("stage_id") or (seen or {}).get("phase"),
                "fired": seen is not None,
                "fired_t_min": seen["t_min"] if seen else None,
                "window": seen.get("window") if seen else None,
            }
        )
    return {
        "facts": record.get("facts", {}),
        "contribution_check": contribution_check(contributions, idea_credit, seat_names),
        "slots": slots,
        "cards": cards,
        "debrief_cards": debrief_cards,
    }


def ai_usage(
    calls: list[dict[str, Any]],
    facts: dict[str, Any],
    started_at: str | None,
) -> dict[str, Any]:
    """어시스턴트 호출을 시간순 사실로만. 분류·비율 없음."""
    fact_t = {c["call_id"]: c["t_min"] for c in facts.get("assistant_calls", [])}
    events = [
        {
            "call_id": call["id"],
            "t_min": fact_t.get(call["id"], t_min_of(started_at, call.get("created_at"))),
            "request_quote": excerpt(call.get("request")),
            "response_quote": excerpt(call.get("response")),
            "cited_material_ids": call.get("cited_material_ids", []),
        }
        for call in calls
    ]
    events.sort(key=lambda e: e["t_min"])
    return {"used": bool(events), "events": events}


# ---- draft -------------------------------------------------------------------------------------


def _context(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"id": m["id"], "sender": m["sender"], "content": m["content"], "t_min": m["t_min"]}
        for m in items
    ]


class EvidenceBuilder:
    """근거 참조 문자열 → Report `evidence[]` 항목. 메시지 맥락은 메시지마다 한 번만 조회한다."""

    def __init__(
        self,
        session_id: str,
        record: dict[str, Any],
        contributions: dict[str, Any],
        span: SpanFn,
    ) -> None:
        self.session_id = session_id
        self.refs = RefIndex(record, contributions)
        self.messages = {m["id"]: m for m in record.get("messages", [])}
        self.slots = {s["phase"]: s for s in record.get("slots", [])}
        proposal = record.get("proposal") or {}
        self.sections = proposal.get("sections") or {}
        session = record.get("session") or {}
        self.proposal_t = t_min_of(
            session.get("started_at"),
            proposal.get("submitted_at") or session.get("completed_at"),
            float(session.get("session_minutes") or 0),
        )
        self.options = {o["option_id"]: o for o in contributions.get("options", [])}
        self.span = span
        self._spans: dict[str, dict[str, Any]] = {}

    async def build(self, ref: str, dimension_id: str) -> dict[str, Any]:
        if not self.refs.exists(ref):
            raise ValueError(f"evidence ref not in session: {ref}")
        kind, key = ref.split(":", 1)
        head = {"ref": ref, "counted": self.refs.counted(ref, dimension_id)}
        if kind == "msg":
            m = self.messages[key]
            if key not in self._spans:
                self._spans[key] = await self.span(key)
            span = self._spans[key]
            return {
                **head,
                "kind": "message",
                "speaker": m["sender"],
                "phase": m["phase"],
                "t_min": m["t_min"],
                "origin": m.get("origin"),
                "quote": m["content"],
                "context": {
                    "before": _context(span.get("before", [])),
                    "after": _context(span.get("after", [])),
                },
                "record_ref": {"type": "message", "id": key, "t_min": m["t_min"]},
            }
        if kind == "slot":
            s = self.slots[key]
            return {
                **head,
                "kind": "slot",
                "phase": key,
                "value": s["value"],
                "resolution": s["resolution"],
                "filled_by": s["filled_by"],
                "support": s.get("support", []),
                "t_min": s["t_min"],
                "record_ref": {"type": "slot", "id": key, "t_min": s["t_min"]},
            }
        if kind == "proposal":
            return {
                **head,
                "kind": "proposal",
                "field": key,
                "quote": excerpt(self.sections.get(key)),
                "t_min": self.proposal_t,
                "record_ref": {
                    "type": "proposal_submit",
                    "id": self.session_id,
                    "t_min": self.proposal_t,
                },
            }
        option = self.options[key]
        slot = self.slots.get(option["phase"])
        if option.get("adopted") and slot:
            record_ref = {"type": "slot", "id": option["phase"], "t_min": slot["t_min"]}
        elif option.get("source_message_id"):
            record_ref = {
                "type": "message",
                "id": option["source_message_id"],
                "t_min": option["t_min"],
            }
        else:
            record_ref = {"type": "phase", "id": option["phase"], "t_min": option["t_min"]}
        fields = (
            "option_id",
            "phase",
            "label",
            "first_proposer",
            "source_message_id",
            "t_min",
            "adopted",
        )
        return {
            **head,
            "kind": "contribution",
            **{f: option.get(f) for f in fields},
            "record_ref": record_ref,
        }


def _check_text(text: str | None, forbidden: list[re.Pattern], where: str) -> None:
    if text and has_forbidden(text, forbidden):
        raise ValueError(f"forbidden expression in {where}")


async def build_draft(
    *,
    session: dict[str, Any],
    record: dict[str, Any],
    rubric: dict[str, Any],
    contributions: dict[str, Any],
    aggregated: dict[str, Any],
    evaluation: dict[str, Any],
    usage_ai: dict[str, Any],
    span: SpanFn,
    forbidden: list[re.Pattern],
    now: datetime | None = None,
) -> dict[str, Any]:
    session_id = session["id"]
    rows = {d["dimension_id"]: d for d in evaluation["dimensions"]}
    summaries = {e["element"]: e for e in evaluation["elements"]}
    evidence = EvidenceBuilder(session_id, record, contributions, span)

    elements, dimensions = [], []
    for element_rubric in rubric["elements"]:
        element = element_rubric["element"]
        element_rows = []
        for dimension in element_rubric["dimensions"]:
            row = rows[dimension["dimension_id"]]
            _check_text(row["rationale"], forbidden, dimension["dimension_id"])
            item = {
                "element": element,
                "dimension_id": row["dimension_id"],
                "name": dimension["name"],
                "level": row["level"],
                "no_record_reason": row.get("no_record_reason"),
                "rationale": row["rationale"],
                "confidence": row.get("confidence"),
                "evidence_refs": row["evidence_refs"],
                "evidence": [
                    await evidence.build(ref, row["dimension_id"]) for ref in row["evidence_refs"]
                ],
            }
            if row["level"] is not None:
                hints = {lv["level"]: lv.get("reviewer_hint") for lv in dimension.get("levels", [])}
                if hints.get(row["level"]):
                    item["reviewer_hint"] = hints[row["level"]]
            element_rows.append(item)
        summary = summaries[element]
        text = summary.get("summary") or FAILED_SUMMARY
        _check_text(text, forbidden, f"{element} summary")
        elements.append(
            {
                "element": element,
                "name": element_rubric["name"],
                "level": summary["level"],
                "summary": text,
                "dimension_count": summary["dimension_count"],
                "no_record_count": summary["no_record_count"],
                "low_confidence_count": sum(r["confidence"] == "low" for r in element_rows),
            }
        )
        dimensions.extend(element_rows)

    seats = sorted(session.get("seats", []), key=lambda s: s["seat"] != "candidate")
    position = session.get("position") or {}
    generated = (now or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
    return {
        "session_id": session_id,
        "alias": session.get("alias") or "",
        "stage": "draft",
        "position": {
            "name": position.get("name") or "",
            "session_minutes": position.get("session_minutes")
            or (record.get("session") or {}).get("session_minutes"),
        },
        "seats": seats,
        "evaluation": {
            "run_id": evaluation["run_id"],
            "rubric_version": evaluation["rubric_version"],
            "prompt_versions": evaluation["prompt_versions"],
            "model": evaluation["model"],
        },
        "elements": elements,
        "dimensions": dimensions,
        "facts": aggregated["facts"],
        "contribution_check": aggregated["contribution_check"],
        "slots": aggregated["slots"],
        "cards": aggregated["cards"],
        "debrief": {"cards": aggregated["debrief_cards"]},
        "ai_usage": usage_ai,
        "generated_at": generated,
    }
