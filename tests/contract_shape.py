"""공용 계약 픽스처(tests/fixtures/contracts/)의 형태 정의와 검사.

`docs/contracts/README.md`의 필드를 작은 형태 언어로 옮긴 SHAPES가 검사 기준이다.
픽스처는 SHAPES로 검사하고(`tests/test_contract_fixtures.py`), 생산자(api·MCP·워커) 테스트는
같은 형태로 실제 응답을 검사한다(`assert_contract`). 표준 라이브러리만 쓰고, 각 서비스 테스트가
파일 경로로 import한다.

형태 언어:
- `str`·`int`·`bool`·`NUM`(int 또는 float): 값의 종류
- `Opt(x)`: null 허용, `Enum(...)`: 정해진 값 중 하나
- `[x]`: 원소가 모두 x인 목록, `{키: x}`: 키 집합이 정확히 같은 객체. 키 끝의 `?`는 없어도 되는 키
- `Map(x)`: 키가 자유로운 객체(값은 x), `ANY`: 무엇이든, `Union(...)`: 하나라도 맞으면 통과
- `Tagged(키, {값: 형태})`: 키 값(예: WS `type`)으로 형태를 고름
- `Checked(x, 함수)`: x 형태 검사 뒤 값 사이 규칙(함수가 오류 문장을 돌려주면 실패)을 본다
"""

import json
from pathlib import Path
from typing import Any

CONTRACTS = Path(__file__).resolve().parent / "fixtures" / "contracts"


# ---- 형태 언어 --------------------------------------------------------------------------------


class Opt:
    def __init__(self, spec: Any) -> None:
        self.spec = spec


class Enum:
    def __init__(self, *values: Any) -> None:
        self.values = set(values)


class Map:
    def __init__(self, spec: Any) -> None:
        self.spec = spec


class Union:
    def __init__(self, *specs: Any) -> None:
        self.specs = specs


class Tagged:
    def __init__(self, key: str, specs: dict[str, Any]) -> None:
        self.key, self.specs = key, specs


class Checked:
    def __init__(self, spec: Any, check: Any) -> None:
        self.spec, self.check = spec, check


ANY = object()
NUM = (int, float)


def validate(value: Any, spec: Any, path: str = "$") -> None:
    """value가 spec 형태가 아니면 AssertionError(경로 포함)."""
    if spec is ANY:
        return
    if isinstance(spec, Opt):
        if value is not None:
            validate(value, spec.spec, path)
        return
    assert value is not None, f"{path}: required value is null"
    if isinstance(spec, Enum):
        # bool은 int의 하위 타입이라 1 == True로 통과하지 않게 타입도 맞춘다
        assert any(value == v and type(value) is type(v) for v in spec.values), (
            f"{path}: {value!r} not in {sorted(map(str, spec.values))}"
        )
    elif isinstance(spec, Union):
        errors = []
        for option in spec.specs:
            try:
                validate(value, option, path)
                return
            except AssertionError as error:
                errors.append(str(error))
        raise AssertionError(f"{path}: no union branch matched: {errors}")
    elif isinstance(spec, Checked):
        validate(value, spec.spec, path)
        error = spec.check(value)
        assert error is None, f"{path}: {error}"
    elif isinstance(spec, Tagged):
        assert isinstance(value, dict), f"{path}: expected object"
        tag = value.get(spec.key)
        assert tag in spec.specs, f"{path}.{spec.key}: unknown {tag!r}"
        validate(value, spec.specs[tag], path)
    elif isinstance(spec, Map):
        assert isinstance(value, dict), f"{path}: expected object"
        for key, item in value.items():
            validate(item, spec.spec, f"{path}.{key}")
    elif isinstance(spec, dict):
        assert isinstance(value, dict), f"{path}: expected object, got {type(value).__name__}"
        required = {k for k in spec if not k.endswith("?")}
        allowed = {k.rstrip("?") for k in spec}
        missing, extra = required - set(value), set(value) - allowed
        assert not missing and not extra, (
            f"{path}: missing {sorted(missing)}, extra {sorted(extra)}"
        )
        for key, sub in spec.items():
            name = key.rstrip("?")
            if name in value:
                validate(value[name], sub, f"{path}.{name}")
    elif isinstance(spec, list):
        assert isinstance(value, list), f"{path}: expected list, got {type(value).__name__}"
        for index, item in enumerate(value):
            validate(item, spec[0], f"{path}[{index}]")
    elif spec is NUM:
        assert isinstance(value, NUM) and not isinstance(value, bool), f"{path}: expected number"
    elif spec is int:
        assert isinstance(value, int) and not isinstance(value, bool), f"{path}: expected int"
    else:
        assert isinstance(value, spec), (
            f"{path}: expected {spec.__name__}, got {type(value).__name__}"
        )


# ---- 어휘 ------------------------------------------------------------------------------------

TS = str  # ISO 8601 UTC
PHASE = Enum("keyword", "problem", "solution", "proposal")
SLOT_PHASE = Enum("keyword", "problem", "solution")
SEAT = Enum("p1", "p2", "p3")
PARTICIPANT = Enum("candidate", "p1", "p2", "p3")
SENDER = Enum("candidate", "p1", "p2", "p3", "system")
AI_SENDER = Enum("p1", "p2", "p3", "system")  # 내부 게시·MCP post_message
PERSONA = Enum("stubborn", "quiet", "critic")
SESSION_STATUS = Enum(
    "invited",
    "cancelled",
    "in_progress",
    "completed",
    "report_ready",
    "approving",
    "reviewed",
    "failed",
)
MINUTES = Enum(60, 15)
ELEMENT = Enum("persuasion", "listening", "inclusion", "flexibility", "honesty")
CARD = Enum(
    "persuasion_keyword",
    "flexibility_majority",
    "listening_pressure",
    "inclusion_silence",
    "persuasion_solution",
    "honesty_credit",
)
DIMENSION_IDS = (
    "pers_reasoning",
    "pers_counter_handling",
    "pers_comparison",
    "pers_convergence",
    "listen_engage",
    "listen_understand",
    "listen_acknowledge",
    "listen_civility",
    "incl_notice_invite",
    "incl_follow_through",
    "incl_shared_decision",
    "incl_safe_tone",
    "flex_openness",
    "flex_decision_respect",
    "flex_acceptance",
    "flex_forward",
    "hon_credit_record",
    "hon_source_in_talk",
    "hon_process_truth",
)
DIMENSION = Enum(*DIMENSION_IDS)
NO_RECORD = Enum(
    "no_activity", "evaluate_failed", "not_applicable", "event_not_fired", "auto_submitted_blank"
)
LEVEL = Enum(1, 2, 3, 4, 5)
CONFIDENCE = Enum("high", "medium", "low")
RESOLUTION = Enum("consensus", "timeout")
OUTCOME = Enum("consensus", "candidate_decision", "timeout")  # 표시용 칸 결과
STIMULUS = Enum("message", "phase_started", "idle", "card_due")
INTENT = Enum("propose", "agree", "oppose", "question", "other")
ORIGIN = Enum("spontaneous", "invited")
FAILED_STAGE = Enum("session", "report")
RECORD_TYPE = Enum(
    "message",
    "slot",
    "phase",
    "assistant_call",
    "material_view",
    "proposal_submit",
    "connection_gap",
)
SECTIONS = {
    "keyword": str,
    "problem": str,
    "solution": str,
    "rationale": str,
    "expected_effect": str,
    "idea_credit": str,
}
GAP = {"from_t_min": NUM, "to_t_min": Opt(NUM)}
MATERIAL_ITEM = {"material_id": str, "title": str}
SUPPORT = [{"option_id": str, "supporters": [PARTICIPANT]}]
SEAT_CANDIDATE = {"seat": Enum("candidate"), "display_name": str}
SEAT_PUBLIC = Union(SEAT_CANDIDATE, {"seat": SEAT, "display_name": str, "avatar_url": str})
SEAT_TYPED = Union(
    SEAT_CANDIDATE, {"seat": SEAT, "display_name": str, "avatar_url": str, "persona_type": PERSONA}
)

# ---- 공용 객체 -------------------------------------------------------------------------------

MESSAGE = {
    "id": str,
    "sender": SENDER,
    "content": str,
    "phase": PHASE,
    "is_invite": bool,
    "t_min": NUM,
    "created_at": TS,
}
INTENT_OUTPUT = {
    "intent": INTENT,
    "target_option_id": Opt(str),
    "scenario_option_key": Opt(str),
    "new_option_label": Opt(str),
    "confidence": Enum("high", "low"),
    "addressed_to": [SEAT],
}
INTERNAL_MESSAGE = {
    **MESSAGE,
    "card_ids": [CARD],
    "meta": Map(ANY),
    "origin": Opt(ORIGIN),
    "intent": Opt(INTENT_OUTPUT),
}
_SLOT_PUBLIC = {
    "phase": SLOT_PHASE,
    "status": Enum("empty", "confirmed"),
    "value": Opt(str),
    "filled_by": Opt(PARTICIPANT),
    "resolution": Opt(RESOLUTION),
    "outcome": Opt(OUTCOME),
    "t_min": Opt(NUM),
}
_SLOT_FULL = {
    "phase": SLOT_PHASE,
    "status": Enum("empty", "confirmed"),
    "value": Opt(str),
    "option_id": Opt(str),
    "filled_by": Opt(PARTICIPANT),
    "resolution": Opt(RESOLUTION),
    "outcome": Opt(OUTCOME),
    "support": Opt(SUPPORT),
    "confirmed_at": Opt(TS),
    "t_min": Opt(NUM),
}


def _confirmed_slot(slot: dict) -> str | None:
    """confirmed 칸은 value·filled_by·resolution·outcome·t_min이 있어야 한다."""
    if slot["status"] != "confirmed":
        return None
    empty = [
        k for k in ("value", "filled_by", "resolution", "outcome", "t_min") if slot.get(k) is None
    ]
    return f"confirmed slot missing {empty}" if empty else None


SLOT_PUBLIC = Checked(_SLOT_PUBLIC, _confirmed_slot)
SLOT_FULL = Checked(_SLOT_FULL, _confirmed_slot)
PROPOSAL = {"sections": SECTIONS, "revision": int, "updated_at": Opt(TS), "submitted_at": Opt(TS)}
MATERIALS = {"locked": bool, "keyword": Opt(str), "materials": [MATERIAL_ITEM]}
STATE = {
    "session": {
        "id": str,
        "alias": str,
        "status": SESSION_STATUS,
        "phase": Opt(PHASE),
        "started_at": Opt(TS),
        "ends_at": Opt(TS),
        "session_minutes": MINUTES,
        "phase_started_at": Opt(TS),
        "phase_deadline_at": Opt(TS),
        "phase_remaining_seconds": Opt(int),
        "session_remaining_seconds": Opt(int),
        "office_start": str,
        "office_end": str,
        "completed_at": Opt(TS),
    },
    "keyword_options": [{"option_key": str, "label": str}],
    "phases": [{"phase": PHASE, "budget_min": NUM, "deadline_t_min": NUM}],
    "participants": [
        Union(
            {"id": Enum("candidate"), "display_name": str},
            {"id": SEAT, "display_name": str, "avatar_url": str},
        )
    ],
    "slots": [SLOT_PUBLIC],
    "messages": [MESSAGE],
    "materials": MATERIALS,
    "proposal": PROPOSAL,
    "assistant": [
        {
            "call_id": str,
            "request": str,
            "response": str,
            "cited_material_ids": [str],
            "t_min": NUM,
        }
    ],
    "typing": [SEAT],
    "active_runs": [{"run_id": str, "stimulus": STIMULUS}],
    "connection_gaps": [GAP],
    "ws_seq": int,
}
ENTRY = {
    "session_id": str,
    "company_name": str,
    "duration_minutes": MINUTES,
    "status": SESSION_STATUS,
    "default_display_name": str,
    "elements_names": [str],
    "entry_notice": str,
    "contact_email": str,
    "debrief_token?": str,
    "failed_stage?": FAILED_STAGE,
}
SESSION_TOKEN = {"session_token": str, "session_id": str}
WINDOW = {"start_min": NUM, "end_min": NUM}
SURVEY_QUESTIONS = {
    "submitted": bool,
    "questions": [{"q": str, "text": str, "kind": Enum("scale", "text")}],
}
DEBRIEF = {
    "intro_notice": str,
    "elements?": [{"element": ELEMENT, "name": str, "how_we_see": str}],
    "cards?": [{"card_id": CARD, "element": ELEMENT, "name": str, "description": str}],
    "seats?": [
        Union(
            SEAT_CANDIDATE,
            {
                "seat": SEAT,
                "display_name": str,
                "avatar_url": str,
                "persona_type": PERSONA,
                "persona_description": str,
            },
        )
    ],
    "my_messages?": [{"id": str, "content": str, "phase": SLOT_PHASE, "t_min": NUM}],
    "scoring_notice": str,
    "appeal_contact": str,
    "connection_gaps": [GAP],
    "retention_notice": str,
    "contact_email": str,
    "proposal?": Opt({"sections": SECTIONS}),
    "view_until": TS,
    "survey": SURVEY_QUESTIONS,
    "failed_stage?": FAILED_STAGE,
}
INVITE_RESPONSE = {"session_id": str, "invite_url": str, "expires_at": TS, "notice_text": str}
# 지원자 목록 행에서 계약 표가 `키?`로 둔 필드(없거나 null)
CANDIDATE_OPTIONAL = {
    "phase",
    "started_at",
    "ends_at",
    "ended_at",
    "last_activity_at",
    "report_ready_at",
    "purge_at",
    "failed_stage",
    "failed_reason",
    "next_action",
    "previous_session_id",
    "reissued_at",
    "elements",
    "low_confidence_count",
    "level_source",
}
_CANDIDATE = {
    "session_id": str,
    "alias": str,
    "email": str,
    "status": SESSION_STATUS,
    "display_status": Enum(*SESSION_STATUS.values, "expired"),
    "display_group": Enum("waiting", "in_progress", "needs_review", "done", "problem"),
    "phase": Opt(Enum(*PHASE.values, "completed")),
    "invited_at": TS,
    "started_at": Opt(TS),
    "ends_at": Opt(TS),
    "ended_at": Opt(TS),
    "last_activity_at": Opt(TS),
    "message_count": int,
    "report_ready_at": Opt(TS),
    "purge_at": Opt(TS),
    "failed_stage": Opt(FAILED_STAGE),
    "failed_reason": Opt(str),
    "next_action": Opt(str),
    "previous_session_id": Opt(str),
    "reissued_at": Opt(TS),
    "elements": Opt([{"element": ELEMENT, "level": Opt(LEVEL), "adjusted": bool}]),
    "low_confidence_count": Opt(int),
    "level_source": Opt(Enum("auto", "reviewer")),
}
CANDIDATE_ROW = {(k + "?" if k in CANDIDATE_OPTIONAL else k): v for k, v in _CANDIDATE.items()}
RECORD_REF = {"type": RECORD_TYPE, "id": str, "t_min": NUM}
CONTEXT_MSG = {"id": str, "sender": SENDER, "content": str, "t_min": NUM}
CONTRIBUTION_OPTION = {
    "option_id": str,
    "phase": SLOT_PHASE,
    "label": str,
    "first_proposer": PARTICIPANT,
    "source_message_id": Opt(str),
    "t_min": NUM,
    "adopted": bool,
}
_EVIDENCE_HEAD = {"ref": str, "kind": str, "counted": bool}
EVIDENCE = Tagged(
    "kind",
    {
        "message": {
            **_EVIDENCE_HEAD,
            "speaker": SENDER,
            "phase": PHASE,
            "t_min": NUM,
            "origin": Opt(ORIGIN),
            "quote": str,
            "context": {"before": [CONTEXT_MSG], "after": [CONTEXT_MSG]},
            "record_ref": RECORD_REF,
        },
        "slot": {
            **_EVIDENCE_HEAD,
            "phase": SLOT_PHASE,
            "value": str,
            "resolution": RESOLUTION,
            "filled_by": PARTICIPANT,
            "support": SUPPORT,
            "t_min": NUM,
            "record_ref": RECORD_REF,
        },
        "proposal": {
            **_EVIDENCE_HEAD,
            "field": Enum(*SECTIONS),
            "quote": str,
            "t_min": NUM,
            "record_ref": RECORD_REF,
        },
        "contribution": {**_EVIDENCE_HEAD, **CONTRIBUTION_OPTION, "record_ref": RECORD_REF},
    },
)


def _no_record(dim: dict) -> str | None:
    """level null ↔ no_record_reason 있음, 기록 없음이면 confidence null."""
    if dim["level"] is None:
        if dim.get("no_record_reason") is None:
            return "level null needs no_record_reason"
        if dim["confidence"] is not None:
            return "no-record dimension must have confidence null"
    elif dim.get("no_record_reason") is not None:
        return "level and no_record_reason are exclusive"
    return None


def _all_dimensions(dims: list) -> str | None:
    ids = [d["dimension_id"] for d in dims]
    ok = len(ids) == len(DIMENSION_IDS) and set(ids) == set(DIMENSION_IDS)
    return None if ok else f"need each of 19 dimensions exactly once, got {len(ids)}"


def _all_elements(elements: list) -> str | None:
    ids = [e["element"] for e in elements]
    ok = len(ids) == len(ELEMENT.values) and set(ids) == ELEMENT.values
    return None if ok else f"need each of 5 elements exactly once, got {len(ids)}"


DIMENSION_EVAL = Checked(
    {
        "dimension_id": DIMENSION,
        "level": Opt(LEVEL),
        "no_record_reason?": Opt(NO_RECORD),
        "evidence_refs": [str],
        "rationale": str,
        "confidence": Opt(CONFIDENCE),
    },
    _no_record,
)
ELEMENT_SUMMARY = {
    "element": ELEMENT,
    "level": Opt(LEVEL),
    "summary": str,
    "dimension_count": int,
    "no_record_count": int,
}
DIMENSIONS_19 = Checked([DIMENSION_EVAL], _all_dimensions)
ELEMENTS_5 = Checked([ELEMENT_SUMMARY], _all_elements)
PROMPT_VERSIONS = {"common": str, **{e: str for e in ELEMENT.values}}
FACTS = {
    "message_count": {
        "total": int,
        "keyword": int,
        "problem": int,
        "solution": int,
        "proposal": int,
    },
    "spontaneous_count": int,
    "invited_count": int,
    "spontaneous_ratio": NUM,
    "silence_invites_received": Map(int),
    "first_message_sec": Map(Opt(int)),
    "response_latency_median_sec": Opt(int),
    "avg_message_chars": int,
    "intent_counts": Map(int),
    "name_mentions": Map(int),
    "slot_records": [
        {
            "phase": SLOT_PHASE,
            "option_id": Opt(str),
            "filled_by": PARTICIPANT,
            "resolution": RESOLUTION,
            "support": SUPPORT,
            "t_min": NUM,
        }
    ],
    "own_options_proposed": int,
    "own_options_adopted": int,
    "pressure_responded": bool,
    "invite_latency_sec": Opt(int),
    "quiet_spoke": bool,
    "own_option_rejected": Opt(bool),
    "messages_after_rejection": int,
    "ai_adopted_units": [{"phase": SLOT_PHASE, "option_id": str, "proposer": SEAT}],
    "credit_field_filled": bool,
    "blank_sections_at_submit": [str],
    "blank_warning_acknowledged": Opt(bool),
    "assistant_calls": [{"call_id": str, "t_min": NUM, "cited_material_ids": [str]}],
    "material_views": [{"material_id": str, "t_min": NUM}],
    "connection_gaps": [GAP],
}
REPORT = {
    "session_id": str,
    "alias": str,
    "stage": Enum("draft", "final"),
    "position": {"name": str, "session_minutes": MINUTES},
    "seats": [SEAT_TYPED],
    "evaluation": {
        "run_id": str,
        "rubric_version": str,
        "prompt_versions": PROMPT_VERSIONS,
        "model": str,
    },
    "elements": Checked(
        [
            {
                **ELEMENT_SUMMARY,
                "name": str,
                "low_confidence_count": int,
                "adjusted_level": Opt(LEVEL),
            }
        ],
        _all_elements,
    ),
    "dimensions": Checked(
        [
            Checked(
                {
                    "element": ELEMENT,
                    "dimension_id": DIMENSION,
                    "name": str,
                    "level": Opt(LEVEL),
                    "no_record_reason": Opt(NO_RECORD),
                    "rationale": str,
                    "confidence": Opt(CONFIDENCE),
                    "reviewer_hint?": str,
                    "adjusted_level": Opt(LEVEL),
                    "adjusted_no_record_reason": Opt(NO_RECORD),
                    "adjust_memo": Opt(str),
                    "evidence_refs": [str],
                    "evidence": [EVIDENCE],
                },
                _no_record,
            )
        ],
        _all_dimensions,
    ),
    "facts": FACTS,
    "contribution_check": [
        {
            "phase": SLOT_PHASE,
            "option_id": str,
            "label": str,
            "first_proposer": SEAT,
            "credited_in_proposal": [PARTICIPANT],
            "credit_quote": Opt(str),
        }
    ],
    "slots": [
        {
            "phase": SLOT_PHASE,
            "value": str,
            "option_id": Opt(str),
            "filled_by": PARTICIPANT,
            "resolution": RESOLUTION,
            "outcome": OUTCOME,
            "t_min": NUM,
            "first_proposer": Opt(PARTICIPANT),
            "support": [{"option_id": str, "label": str, "supporters": [PARTICIPANT]}],
        }
    ],
    "cards": [
        {
            "card_id": CARD,
            "element": ELEMENT,
            "fired_at": Opt(NUM),
            "delivered": bool,
            "scene": str,
        }
    ],
    "debrief": {
        "cards": [
            {
                "card_id": CARD,
                "element": ELEMENT,
                "phase": PHASE,
                "fired": bool,
                "fired_t_min": Opt(NUM),
                "window": Opt(WINDOW),
            }
        ],
        "survey": Opt({"natural": LEVEL, "fair": LEVEL, "free_comment": Opt(str)}),
    },
    "proposal": Opt({"submitted_at": TS, "auto_submitted": bool, "sections": SECTIONS}),
    "ai_usage": {
        "used": bool,
        "events": [
            {
                "call_id": str,
                "t_min": NUM,
                "request_quote": str,
                "response_quote": str,
                "cited_material_ids": [str],
            }
        ],
    },
    "review": Opt({"reviewer_name": str, "reviewed_at": Opt(TS)}),
    "notes": [{"id": str, "text": str, "reviewer_name": str, "created_at": TS}],
    "connection_gaps": [GAP],
    "generated_at": TS,
    "reviewed_at": Opt(TS),
    "purge_at": Opt(TS),
}
OPTION = {
    "option_id": str,
    "phase": SLOT_PHASE,
    "label": str,
    "proposer": PARTICIPANT,
    "scenario_option_key": Opt(str),
    "created_at": TS,
    "existing": bool,
}
CARD_PROGRESS = [{"card_id": CARD, "locked_option_id": Opt(str), "delivered": [SEAT]}]
DEBATE_STATE = {
    "phase": PHASE,
    "generation": int,
    "phase_deadline_at": TS,
    "phase_remaining_seconds": int,
    "slot": Opt({"phase": SLOT_PHASE, "status": Enum("empty", "confirmed"), "value": Opt(str)}),
    "options": [
        {
            "option_id": str,
            "label": str,
            "proposer": PARTICIPANT,
            "scenario_option_key": Opt(str),
            "created_at": TS,
            "supporters": [PARTICIPANT],
            "opposers": [PARTICIPANT],
        }
    ],
    "catalog_options": [{"scenario_option_key": str, "label": str, "spoken": bool}],
    "current_support": {k: Opt(str) for k in ("candidate", "p1", "p2", "p3")},
    "consensus_option_id": Opt(str),
    "timeout_option_id": Opt(str),
    "candidate": {"spoke_in_phase": bool, "last_message_t_min": Opt(NUM), "typing": bool},
    "invite_delivered": bool,
    "last_room_message_t_min": Opt(NUM),
    "confirmed": [
        {
            "phase": SLOT_PHASE,
            "value": str,
            "option_id": Opt(str),
            "resolution": RESOLUTION,
            "filled_by": PARTICIPANT,
        }
    ],
    "card_progress": CARD_PROGRESS,
    "cost_left": NUM,
}
CONTRIBUTIONS = {
    "options": [CONTRIBUTION_OPTION],
    "slots": [
        {
            "phase": SLOT_PHASE,
            "option_id": Opt(str),
            "first_proposer": Opt(PARTICIPANT),
            "filled_by": PARTICIPANT,
            "resolution": RESOLUTION,
        }
    ],
}
RUBRIC = {
    "rubric_version": str,
    "elements": [
        {
            "element": ELEMENT,
            "name": str,
            "dimensions": [
                {
                    "dimension_id": DIMENSION,
                    "name": str,
                    "definition": str,
                    "core_cards": [CARD],
                    "levels": [{"level": LEVEL, "anchor": str, "reviewer_hint": str}],
                }
            ],
        }
    ],
    "no_record_reasons": [{"code": NO_RECORD, "label": str}],
    "cards": [
        {
            "card_id": CARD,
            "element": ELEMENT,
            "stage_id": PHASE,
            "window": {"start": Enum("stage_start", "card_message"), "end": str, "label": str},
        }
    ],
}
SESSION_RECORD = {
    "session": {
        "id": str,
        "elements": [ELEMENT],
        "session_minutes": MINUTES,
        "started_at": TS,
        "completed_at": TS,
        "connection_gaps": [GAP],
    },
    "seats": [Union(SEAT_CANDIDATE, {"seat": SEAT, "display_name": str, "persona_type": PERSONA})],
    "messages": [
        {
            "id": str,
            "sender": SENDER,
            "content": str,
            "phase": PHASE,
            "t_min": NUM,
            "origin": Opt(ORIGIN),
            "is_invite": bool,
            "card_ids": [CARD],
            "intent": Opt({"intent": INTENT, "target_option_id": Opt(str), "addressed_to": [SEAT]}),
        }
    ],
    "slots": [{k: v for k, v in _SLOT_FULL.items() if k not in ("status", "confirmed_at")}],
    "proposal": {"sections": SECTIONS, "submitted_at": TS, "auto_submitted": bool},
    "observations": [
        {
            "card_id": CARD,
            "element": ELEMENT,
            "phase": PHASE,
            "t_min": NUM,
            "trigger_message_id": Opt(str),
            "delivered": bool,
            "window": WINDOW,
        }
    ],
    "facts": FACTS,
    "contributions": CONTRIBUTIONS,
}
EVALUATION_SAVE = {
    "session_id": str,
    "run_id": str,
    "rubric_version": str,
    "prompt_versions": PROMPT_VERSIONS,
    "model": str,
    "dimensions": DIMENSIONS_19,
    "elements": ELEMENTS_5,
}
EVALUATIONS = {
    "run_id": str,
    "rubric_version": str,
    "prompt_versions": PROMPT_VERSIONS,
    "model": str,
    "elements": ELEMENTS_5,
    "dimensions": DIMENSIONS_19,
    "has_failed": bool,
    "created_at": TS,
}
OBSERVATION_CREATED = {"observation_id": str, "fired_at": TS, "t_min": NUM}
POST_MESSAGE_REQUEST = {
    "sender": AI_SENDER,
    "content": str,
    "run_id": str,
    "seq": int,
    "expected_generation?": int,
    "is_invite?": bool,
    "card_ids?": [CARD],
    "cited_material_ids?": [str],
    "meta?": Map(ANY),
}
OPTION_REQUEST = {
    "phase": SLOT_PHASE,
    "label": str,
    "proposer": SEAT,
    "scenario_option_key?": Opt(str),
    "source_message_id": str,
    "run_id": str,
}
STANCE_REQUEST = {
    "participant": SEAT,
    "option_id": str,
    "stance": Enum("support", "oppose"),
    "source_message_id": str,
    "run_id": str,
}
SLOT_FILL_REQUEST = {"option_id": str, "seat": SEAT, "content": str, "run_id": str}
SLOT_FILL = {"slot": SLOT_FULL, "message": MESSAGE, "phase": PHASE}
OBSERVATION_REQUEST = {
    "card_id": CARD,
    "phase": PHASE,
    "trigger_message_id?": Opt(str),
    "run_id": str,
}
INTERNAL_MATERIAL = {"material_id": str, "title": str, "content": str}
TRANSCRIPT_SPAN = {
    "target": INTERNAL_MESSAGE,
    "before": [INTERNAL_MESSAGE],
    "after": [INTERNAL_MESSAGE],
}
WS_SERVER = {
    "message.ack": {"client_msg_id": str, "message_id": str, "run_id": Opt(str)},
    "message.new": MESSAGE,
    "typing": {"participant": SEAT, "state": Enum("start", "stop")},
    "slot.confirmed": {
        "phase": SLOT_PHASE,
        "value": str,
        "filled_by": PARTICIPANT,
        "resolution": RESOLUTION,
        "outcome": OUTCOME,
        "t_min": NUM,
    },
    "phase.changed": {
        "phase": PHASE,
        "previous_phase": Opt(PHASE),
        "reason": Enum("start", "confirmed", "timeout"),
        "phase_started_at": TS,
        "phase_deadline_at": TS,
        "t_min": NUM,
    },
    "timer": {
        "phase": PHASE,
        "phase_deadline_at": TS,
        "phase_remaining_seconds": int,
        "session_remaining_seconds": int,
    },
    "materials.opened": {"keyword": str, "materials": [MATERIAL_ITEM]},
    "session.ended": {"reason": Enum("submitted", "timeout", "failed", "cancelled")},
    "session.replaced": {},
    "error": {"code": Enum("session_ending", "conflict", "invalid_input"), "message": str},
}
WS_FRAME = Tagged(
    "type", {t: {"type": str, "seq": int, "payload": p} for t, p in WS_SERVER.items()}
)
WS_CLIENT = Tagged(
    "type",
    {
        "message.send": {"type": str, "payload": {"client_msg_id": str, "content": str}},
        "typing": {"type": str, "payload": {"state": Enum("start", "stop")}},
    },
)
MCP_ERROR_CODE = Enum(
    "invalid_input",
    "not_found",
    "forbidden",
    "conflict",
    "not_released",
    "upstream_unavailable",
    "internal",
)
QUEUE_REPORT = {"session_id": str, "attempt": int, "eval_round": int}

# MCP 도구별 (입력, 출력 data). 픽스처의 입력·출력이 "<이름>.json" 문자열이면 그 픽스처를 쓴다.
MCP_TOOLS: dict[str, dict[str, tuple[Any, Any]]] = {
    "debate": {
        "get_debate_state": ({"session_id": str}, {**DEBATE_STATE, "seats": [SEAT_PUBLIC]}),
        "get_transcript": (
            {"session_id": str, "since_t_min?": NUM, "phase?": PHASE, "limit?": int},
            [{k: v for k, v in MESSAGE.items() if k != "created_at"}],
        ),
        "get_draft": (
            {"session_id": str},
            {"sections": SECTIONS, "revision": int, "updated_at": TS},
        ),
        "post_message": ({"session_id": str, **POST_MESSAGE_REQUEST}, MESSAGE),
        "propose_option": ({"session_id": str, **OPTION_REQUEST}, OPTION),
        "set_stance": ({"session_id": str, **STANCE_REQUEST}, {"stance_id": str}),
        "fill_slot": ({"session_id": str, **SLOT_FILL_REQUEST}, SLOT_FILL),
    },
    "rag": {
        "search_materials": (
            {"session_id": str, "query": str, "top_k?": int},
            [{"material_id": str, "title": str, "chunk": str, "score": NUM}],
        ),
        "get_material": ({"session_id": str, "material_id": str}, INTERNAL_MATERIAL),
        "reindex_materials": (
            {"mission_id?": str},
            {"indexed": int, "model": str, "dim": int, "catalog_version": str},
        ),
    },
    "assessment": {
        "record_observation": ({"session_id": str, **OBSERVATION_REQUEST}, OBSERVATION_CREATED),
        "get_rubric": ({"session_id": str}, RUBRIC),
        "get_session_record": ({"session_id": str}, SESSION_RECORD),
        "get_transcript_span": (
            {"session_id": str, "message_id": str, "before?": int, "after?": int},
            TRANSCRIPT_SPAN,
        ),
        "get_contributions": ({"session_id": str}, CONTRIBUTIONS),
        "save_evaluation": (EVALUATION_SAVE, {"run_id": str, "existing": bool, "saved": int}),
    },
}

# ---- 픽스처 이름 → 형태 ------------------------------------------------------------------------

SHAPES: dict[str, Any] = {
    # 지원자 REST
    "entry": ENTRY,
    "entry_completed": ENTRY,
    "consent_request": {"display_name?": Opt(str)},
    "consent": SESSION_TOKEN,
    "resume": SESSION_TOKEN,
    "start": {"session_id": str, "started_at": TS, "ends_at": TS, "phase": Enum("keyword")},
    "state_pre_start": STATE,
    "state_keyword": STATE,
    "state_problem": STATE,
    "state_solution": STATE,
    "state_proposal": STATE,
    "message": MESSAGE,
    "slot_confirm_request": {"value": str, "option_key?": Opt(str)},
    "slot_confirm": {"slot": SLOT_PUBLIC, "phase": PHASE},
    "materials_locked": MATERIALS,
    "materials": MATERIALS,
    "material": INTERNAL_MATERIAL,
    "assistant_request": {"prompt": str},
    "assistant_response": {
        "call_id": str,
        "message_id": str,
        "content": str,
        "cited_material_ids": [str],
        "tool_calls": [{"name": str, "query?": str, "material_id?": str}],
    },
    "proposal": PROPOSAL,
    "proposal_put_request": {"sections": SECTIONS, "revision": int},
    "proposal_put": {"revision": int, "updated_at": TS},
    "proposal_submit": {"status": Enum("completed"), "completed_at": TS, "submitted_at": TS},
    "debrief": DEBRIEF,
    "debrief_failed": DEBRIEF,
    "debrief_survey_request": {
        "answers": [{"q": Enum("natural", "fair"), "value": LEVEL}],
        "free_comment?": Opt(str),
    },
    "debrief_survey": {"submitted": bool},
    "error": {"error": {"code": str, "message": str}},
    # WebSocket
    "ws_events": [WS_FRAME],
    "ws_client_frames": [WS_CLIENT],
    "ws_error": WS_FRAME,
    "ws_session_replaced": WS_FRAME,
    # 관리자 REST
    "admin_auth_token": {"token": str},
    "admin_catalog": {
        "elements": [{"id": ELEMENT, "name": str, "meaning": str, "how_we_see": str}],
        "session_minutes_options": [MINUTES],
        "default_session_minutes": MINUTES,
        "candidate_notice_template": str,
        "candidate_faq": [{"q": str, "a": str}],
    },
    "admin_positions": [
        {
            "id": str,
            "name": str,
            "session_minutes": MINUTES,
            "candidate_count": int,
            "created_at": TS,
        }
    ],
    "admin_position_request": {"name": str, "session_minutes?": MINUTES, "mission_id?": str},
    "admin_position_created": {
        "id": str,
        "name": str,
        "session_minutes": MINUTES,
        "seats": [{"seat": SEAT, "name": str, "persona_type": PERSONA, "avatar_url": str}],
        "keywords": [str],
        "catalog_version": str,
    },
    "admin_candidates": [CANDIDATE_ROW],
    "admin_invite_request": {"alias?": str, "email": str, "previous_session_id?": str},
    "admin_invite": INVITE_RESPONSE,
    "admin_reissue": INVITE_RESPONSE,
    "admin_cancel": {"status": Enum("cancelled")},
    "admin_report_regenerate": {"status": Enum("completed")},
    "admin_review_request": {
        "adjustments": [
            {
                "dimension_id": DIMENSION,
                "level": Opt(LEVEL),
                "no_record_reason?": NO_RECORD,
                "memo": str,
            }
        ],
        "reviewer_name": str,
    },
    "admin_review_response": {"status": Enum("approving")},
    "admin_report_notes_request": {"text": str, "reviewer_name": str},
    "admin_report_notes": {
        "notes": [{"id": str, "text": str, "reviewer_name": str, "created_at": TS}]
    },
    "admin_record": [
        {"t_min": NUM, "type": RECORD_TYPE, "by": SENDER, "summary": str, "ref": RECORD_REF}
    ],
    "report": REPORT,
    # 내부 REST
    "internal_session": {
        "id": str,
        "status": SESSION_STATUS,
        "phase": Opt(Enum(*PHASE.values, "completed")),
        "alias": str,
        "display_name": Opt(str),
        "position": {
            "id": str,
            "name": str,
            "mission_id": str,
            "session_minutes": MINUTES,
            "scenario": Map(ANY),
            "catalog_version": str,
        },
        "seats": [SEAT_TYPED],
        "started_at": Opt(TS),
        "ends_at": Opt(TS),
        "phase_started_at": Opt(TS),
        "phase_deadline_at": Opt(TS),
        "completed_at": Opt(TS),
        "ended_at": Opt(TS),
        "t_min_now": Opt(NUM),
        "generation": int,
        "invite_delivered_phases": [SLOT_PHASE],
        "card_progress": CARD_PROGRESS,
        "connection_gaps": [GAP],
    },
    "internal_debate_state": DEBATE_STATE,
    "internal_messages": [INTERNAL_MESSAGE],
    "internal_message_post_request": POST_MESSAGE_REQUEST,
    "internal_message_post": MESSAGE,
    "internal_option_request": OPTION_REQUEST,
    "internal_option": OPTION,
    "internal_stance_request": STANCE_REQUEST,
    "internal_stance": {"stance_id": str},
    "internal_slot_fill_request": SLOT_FILL_REQUEST,
    "internal_slot_fill": SLOT_FILL,
    "internal_proposal": {**PROPOSAL, "auto_submitted": bool},
    "internal_materials": {
        "catalog_version": str,
        "locked": bool,
        "keyword_key": Opt(str),
        "materials": [MATERIAL_ITEM],
    },
    "internal_material": INTERNAL_MATERIAL,
    "internal_observation_request": OBSERVATION_REQUEST,
    "internal_observation_created": OBSERVATION_CREATED,
    "internal_observations": [
        {
            "observation_id": str,
            "card_id": CARD,
            "element": ELEMENT,
            "phase": PHASE,
            "fired_at": TS,
            "t_min": NUM,
            "trigger_message_id": Opt(str),
            "delivered": bool,
        }
    ],
    "internal_transcript_span": TRANSCRIPT_SPAN,
    "internal_contributions": CONTRIBUTIONS,
    "internal_rubric": RUBRIC,
    "session_record": SESSION_RECORD,
    "evaluation_save": EVALUATION_SAVE,
    "internal_evaluations_saved": {"run_id": str, "existing": bool, "saved": int},
    "internal_evaluations": EVALUATIONS,
    "internal_facts": FACTS,
    "internal_slots": [SLOT_FULL],
    "internal_assistant_calls": [
        {
            "id": str,
            "request_message_id": str,
            "response_message_id": str,
            "request": str,
            "response": str,
            "cited_material_ids": [str],
            "created_at": TS,
        }
    ],
    "internal_report_draft": {"status": Enum("report_ready")},
    "internal_report_final": {"status": Enum("reviewed"), "reviewed_at": TS},
    "internal_report_failed": {"status": Enum("failed")},
    "internal_test_session_request": {
        "session_minutes": MINUTES,
        "alias?": str,
        "company_id?": str,
        "admin_email?": str,
        "start_at?": Enum("entry", "keyword", "problem", "solution", "proposal", "ended"),
        "stage?": str,
    },
    "internal_test_session": {
        "session_id": str,
        "token": str,
        "invite_url": str,
        "phase": Opt(Enum(*PHASE.values, "completed")),
        "session_token?": str,
        "debrief_token?": str,
        "admin_report_url?": str,
    },
    # 의도 분류
    "intent_classification": INTENT_OUTPUT,
    "intent_classification_fallback": INTENT_OUTPUT,
    # MCP·큐 (mcp_tools.json은 validate_mcp_tools가 따로 검사)
    "mcp_ok": {"ok": Enum(True), "data": ANY},
    "mcp_error": {
        "ok": Enum(False),
        "error": {"code": MCP_ERROR_CODE, "message": str, "retryable": bool},
    },
    "queue_messages": {
        "report.generate": QUEUE_REPORT,
        "report.approve": QUEUE_REPORT,
        "report.dlq": {**QUEUE_REPORT, "error": str},
    },
}


# ---- 진입점 ----------------------------------------------------------------------------------


def load_contract(name: str) -> Any:
    return json.loads((CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))


def assert_contract(actual: Any, name: str, optional: Any = frozenset()) -> None:
    """생산자 응답이 그 픽스처 이름의 계약 형태인지 검사한다(optional은 옛 호출 호환용으로 무시)."""
    validate(actual, SHAPES[name], name)


def validate_mcp_tools(tools: dict[str, Any]) -> None:
    """mcp_tools.json: 서버·도구 목록이 계약과 같고 입력·출력이 형태에 맞는지."""
    assert set(tools) == set(MCP_TOOLS), f"mcp servers {sorted(tools)}"
    for server, specs in MCP_TOOLS.items():
        assert set(tools[server]) == set(specs), f"{server}: tools {sorted(tools[server])}"
        for tool, (in_spec, out_spec) in specs.items():
            case = tools[server][tool]
            assert set(case) == {"input", "output"}, f"{server}.{tool}: keys {sorted(case)}"
            for part, spec in (("input", in_spec), ("output", out_spec)):
                value = case[part]
                if isinstance(value, str) and value.endswith(".json"):
                    value = load_contract(value.removesuffix(".json"))
                validate(value, spec, f"mcp_tools.{server}.{tool}.{part}")


def assert_shape(actual: Any, expected: Any, path: str = "$") -> None:
    """두 JSON 값의 구조 비교(키 집합·값 종류). null은 어느 쪽이든 허용한다."""
    if actual is None or expected is None:
        return
    kinds = (bool, NUM, str, list, dict)
    actual_kind = next(k for k in kinds if isinstance(actual, k))
    expected_kind = next(k for k in kinds if isinstance(expected, k))
    assert actual_kind == expected_kind, f"{path}: {actual_kind} != {expected_kind}"
    if isinstance(expected, dict):
        assert set(actual) == set(expected), (
            f"{path}: missing {sorted(set(expected) - set(actual))}, "
            f"extra {sorted(set(actual) - set(expected))}"
        )
        for key in expected:
            assert_shape(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list) and expected:
        for index, item in enumerate(actual):
            assert_shape(item, expected[0], f"{path}[{index}]")


SHAPES["voice_state"] = {
    "turn_id": str,
    "epoch": Opt(str),
    "generation": int,
    "phase": str,
    "state": Enum(
        "AI_GENERATING", "AI_SPEAKING", "USER_READY", "USER_SPEAKING", "TRANSCRIBING", "PROCESSING"
    ),
    "ready_at": str,
    "server_now": str,
    "items": [
        {
            "id": str,
            "message_id": str,
            "seat": str,
            "status": str,
            "duration_ms": Opt(NUM),
            "error": Opt(str),
        }
    ],
    "blocked_reason?": Opt(str),
    "release_ready?": bool,
    "utterance_id?": Opt(str),
    "speech_started_at?": str,
    "speech_deadline_at?": str,
    "heard_message_ids?": [str],
    "think_deadline_at?": str,
    "turn_until_at?": str,
    "speech_limit_seconds?": int,
    "rerecorded?": bool,
    "priority?": Enum("hand", "forced"),
    "hand?": Opt(ANY),
    "hand_turn?": Opt(ANY),
    "utterance?": {
        "id": str,
        "status": str,
        "transcript": Opt(str),
        "message_id": Opt(str),
        "error": Opt(str),
        "confirm_deadline_at?": str,
    },
}

FACTS["voice_usage?"] = Map(ANY)
SHAPES["voice_state"]["capture_count?"] = int
