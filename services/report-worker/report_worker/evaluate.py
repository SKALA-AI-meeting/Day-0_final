"""평가 에이전트: 요소별 LLM 호출, 워커 검증, 요소 단위 1회 재요청, 확신도 clamp, 요소 수준.

규칙 정본은 `docs/product/rubric.md` 2.2·2.4·7.3과 `docs/contracts/internal.md` EvaluationSave·검증
경계다.

- 요소마다 별도 호출(5회). 입력은 그 요소의 척도·지침과 세션 기록 전체뿐이고 다른 요소의 결과는
  넣지 않는다(후광 방지).
- 형식 오류(파싱·스키마·차원 누락·중복·없는 차원), 근거 부재(수준이 있는데 같은 세션의 실제 행인
  지원자 근거가 최소 개수 미만), 금지어가 하나라도 있으면 그 요소 호출 전체를 실패 사유와 함께
  1회 다시 요청한다. 재요청 결과에서도 실패한 차원만 `evaluate_failed`로 두고 나머지는 재요청
  결과를 쓴다. 워커는 수준·근거를 고쳐 쓰지 않는다.
- 확신도 상한 위반은 거부하지 않고 상한으로 깎는다(clamp).
- 핵심 카드가 하나도 발동하지 않았는데 `rationale`에 "보조 장면" 표시 없이 수준을 낸 차원은 거부하지
  않고 `event_not_fired`로 바꾼다(rubric.md 2.4: 부재로만 낮게 보지 않는다). 담당 동료 일부만 전달한
  카드(`delivered=false`)는 발동으로 세지 않는다.
- 검증으로 차원 행(evaluate_failed, 수준 상한 3, event_not_fired)이 모델 출력과 달라진 요소만 최종
  차원 행으로 summary를 1회 다시 쓰게 한다. 실패하면 고정 문장. 입력에 다른 요소 결과는 없다.
- LLM 전송 오류·타임아웃은 여기서 잡지 않는다(저장 없이 큐 재시도).
검증·clamp·요소 수준은 순수 함수라 단위 테스트한다.
"""

import hashlib
import importlib.util
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from report_worker.llm import LLM, complete_tracked

log = logging.getLogger("report_worker.evaluate")

ELEMENTS = ("persuasion", "listening", "inclusion", "flexibility", "honesty")
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
# 모델이 쓸 수 있는 기록 없음 사유. evaluate_failed는 워커만 쓴다
MODEL_REASONS = frozenset(
    {"no_activity", "not_applicable", "event_not_fired", "auto_submitted_blank"}
)
CREDIT_DIMENSION = "hon_credit_record"  # contribution 참조도 근거 개수에 드는 예외(rubric.md 3.5)
FAILED_SUMMARY = (
    "근거가 충분한 판단이 나오지 않아 요약을 쓰지 않았습니다. 원문 기록 확인을 권장합니다"
)
FAILED_RATIONALE = (
    "근거가 충분한 판단이 나오지 않아 수준을 매기지 않았습니다. "
    "원문 기록 확인을 권장합니다. (사유: {code})"
)
CAPPED_MARK = " (근거 1개로 수준 상한 3 적용)"
SUPPORT_MARK = "보조 장면"
NOT_FIRED_MARK = " (핵심 카드 미발생·보조 장면 표시 없음으로 이벤트 미발생 처리)"
REWRITE_FALLBACK = (
    "일부 차원 판단이 검증 단계에서 바뀌어 요약을 다시 쓰지 못했습니다. "
    "차원별 판단을 확인해 주세요."
)
REF_PATTERN = re.compile(r"^(msg|slot|proposal|contribution):(.+)$")


# ---- 프롬프트·금지어 (채용 건 복사본) -------------------------------------------------------

EVALUATOR_FILES = ("evaluator/common.md", *(f"evaluator/{e}.md" for e in ELEMENTS))
GUARD_FILE = "guard_patterns.yaml"


@dataclass(frozen=True)
class Prompts:
    common: str
    elements: dict[str, str]
    versions: dict[str, str]  # {common: 해시, <element>: 해시}


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _read_files(scenarios_dir: Path, mission_id: str) -> dict[str, str]:
    """현재 카탈로그 파일을 프롬프트 키로 읽는다(키 규칙은 scenarios/version.py `prompt_files`)."""
    root = Path(scenarios_dir)
    spec = importlib.util.spec_from_file_location("scenarios_version", root / "version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    files = module.prompt_files(root / "missions" / mission_id / "prompts", copied_only=True)
    return {n: files[n].read_text(encoding="utf-8") for n in (*EVALUATOR_FILES, GUARD_FILE)}


def _prompts(texts: dict[str, str]) -> Prompts:
    common = texts["evaluator/common.md"]
    elements = {e: texts[f"evaluator/{e}.md"] for e in ELEMENTS}
    versions = {"common": _digest(common), **{e: _digest(t) for e, t in elements.items()}}
    return Prompts(common, elements, versions)


def _forbidden(guard_yaml: str) -> list[re.Pattern]:
    data = yaml.safe_load(guard_yaml)
    return [re.compile(p) for p in data["evaluator_forbidden"]["patterns"]]


class PinnedCopyMissing(Exception):
    """채용 건 복사본에 평가 프롬프트·금지 패턴이 없다. 재시도해도 같으므로 바로 실패 처리한다."""


def load_prompts(scenarios_dir: Path, mission_id: str) -> Prompts:
    """현재 카탈로그 파일(eval·테스트용). 평가 실행은 `pinned_catalog`만 쓴다."""
    return _prompts(_read_files(scenarios_dir, mission_id))


def load_forbidden(scenarios_dir: Path, mission_id: str) -> list[re.Pattern]:
    return _forbidden(_read_files(scenarios_dir, mission_id)[GUARD_FILE])


def pinned_catalog(
    scenario: dict[str, Any], position_id: str | None
) -> tuple[Prompts, list[re.Pattern]]:
    """채용 건 생성 때 `positions.scenario.prompts`에 복사된 평가 프롬프트·금지 패턴.

    같은 채용 건의 지원자는 같은 프롬프트로 평가된다. 복사본에 하나라도 빠졌으면 현재 파일로
    채우지 않고 `PinnedCopyMissing`을 올린다.
    """
    texts = scenario.get("prompts") or {}
    missing = [n for n in (*EVALUATOR_FILES, GUARD_FILE) if not texts.get(n)]
    if not missing and "evaluator_forbidden" not in (yaml.safe_load(texts[GUARD_FILE]) or {}):
        missing = [f"{GUARD_FILE}:evaluator_forbidden"]
    if missing:
        raise PinnedCopyMissing(f"position {position_id} scenario copy lacks {', '.join(missing)}")
    return _prompts(texts), _forbidden(texts[GUARD_FILE])


def has_forbidden(text: str, forbidden: list[re.Pattern]) -> bool:
    return any(p.search(text) for p in forbidden)


# ---- 근거 참조 ---------------------------------------------------------------------------------


class RefIndex:
    """같은 세션의 실제 행과 근거 개수 규칙(rubric.md 2.2, 3.5 예외)."""

    def __init__(self, record: dict[str, Any], contributions: dict[str, Any]) -> None:
        self.message_sender = {m["id"]: m["sender"] for m in record.get("messages", [])}
        self.slot_filled_by = {s["phase"]: s.get("filled_by") for s in record.get("slots", [])}
        sections = ((record.get("proposal") or {}).get("sections")) or {}
        self.proposal_fields = set(sections)
        self.option_ids = {o["option_id"] for o in contributions.get("options", [])}

    def exists(self, ref: Any) -> bool:
        if not isinstance(ref, str):
            return False
        match = REF_PATTERN.match(ref)
        if not match:
            return False
        kind, key = match.groups()
        if kind == "msg":
            return key in self.message_sender
        if kind == "slot":
            return key in self.slot_filled_by
        if kind == "proposal":
            return key in self.proposal_fields
        return key in self.option_ids

    def counted(self, ref: str, dimension_id: str) -> bool:
        """지원자 행동 단위만 센다. AI·시스템 메시지와 contribution은 맥락(정직 기여 기록 예외)."""
        kind, key = ref.split(":", 1)
        if kind == "msg":
            return self.message_sender.get(key) == "candidate"
        if kind == "slot":
            return self.slot_filled_by.get(key) == "candidate"
        if kind == "proposal":
            return True
        return dimension_id == CREDIT_DIMENSION

    def counted_units(self, refs: list[str], dimension_id: str) -> int:
        return len({r for r in refs if self.exists(r) and self.counted(r, dimension_id)})


def fix_refs(output: Any, refs: RefIndex) -> Any:
    """모델이 `msg:15`로 낸 숫자 id를 세션에 `m-15`가 있을 때만 `msg:m-15`로 고친다."""
    if not isinstance(output, dict) or not isinstance(output.get("dimensions"), list):
        return output
    for row in output["dimensions"]:
        evidence = row.get("evidence_refs") if isinstance(row, dict) else None
        if not isinstance(evidence, list):
            continue
        row["evidence_refs"] = [
            f"msg:m-{ref[4:]}"
            if isinstance(ref, str)
            and re.fullmatch(r"msg:\d+", ref)
            and f"m-{ref[4:]}" in refs.message_sender
            and ref[4:] not in refs.message_sender
            else ref
            for ref in evidence
        ]
    return output


# ---- 검증 (rubric.md 7.3) ---------------------------------------------------------------------


def check_dimension(row: Any, refs: RefIndex, forbidden: list[re.Pattern]) -> str | None:
    """차원 한 행의 실패 사유. 통과하면 None."""
    if not isinstance(row, dict):
        return "format: 차원 행이 객체가 아님"
    dimension_id = row.get("dimension_id")
    level = row.get("level")
    reason = row.get("no_record_reason")
    evidence = row.get("evidence_refs")
    rationale = row.get("rationale")
    confidence = row.get("confidence")
    if not isinstance(evidence, list) or not all(isinstance(r, str) for r in evidence):
        return "format: evidence_refs는 문자열 배열이어야 함"
    if not isinstance(rationale, str) or not rationale.strip():
        return "format: rationale이 비어 있음"
    if has_forbidden(rationale, forbidden):
        return "forbidden: rationale에 금지어(합격·순위·추천·점수 표현)를 쓰지 않는다"
    bad = [r for r in evidence if not refs.exists(r)]
    if bad:
        return f"ref_not_in_session: 참조 {bad}가 세션에 없다. 세션 기록의 ref 문자열만 쓴다"
    if level is None:
        if reason not in MODEL_REASONS:
            return f"format: 기록 없음 사유가 올바르지 않음 {reason!r}"
        if confidence is not None:
            return "format: 기록 없음이면 confidence는 null"
        return None
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 5:
        return f"format: level은 1~5 정수 또는 null {level!r}"
    if reason is not None:
        return "format: level과 no_record_reason은 둘 중 하나만 쓴다"
    if confidence not in CONFIDENCE_ORDER:
        return f"format: confidence 값이 올바르지 않음 {confidence!r}"
    units = refs.counted_units(evidence, dimension_id)
    if units < 1:
        return (
            "no_candidate_evidence: 지원자 근거 없음 — 수준 대신 no_record_reason을 내거나 "
            "지원자 근거(지원자 msg·지원자가 확정한 slot·proposal)를 단다"
        )
    return (
        None  # 수준 4·5인데 지원자 근거 단위가 1개면 거부하지 않고 evaluate_element가 3으로 깎는다
    )


@dataclass
class Validation:
    valid: dict[str, dict[str, Any]]  # dimension_id → 통과한 행
    errors: dict[str, str]  # dimension_id 또는 "_format"·"summary" → 사유
    summary: str | None  # 통과한 summary


def validate_output(
    output: Any,
    dimension_ids: list[str],
    refs: RefIndex,
    forbidden: list[re.Pattern],
) -> Validation:
    result = Validation({}, {}, None)
    if not isinstance(output, dict) or not isinstance(output.get("dimensions"), list):
        result.errors["_format"] = "format: 출력이 {element, summary, dimensions[]} 형식이 아님"
        return result
    summary = output.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        result.errors["summary"] = "format: summary가 비어 있음"
    elif has_forbidden(summary, forbidden):
        result.errors["summary"] = (
            "forbidden: summary에 금지어(합격·순위·추천·점수 표현)를 쓰지 않는다"
        )
    else:
        result.summary = summary
    seen: dict[str, int] = {}
    for row in output["dimensions"]:
        dimension_id = row.get("dimension_id") if isinstance(row, dict) else None
        if dimension_id not in dimension_ids:
            result.errors["_format"] = f"format: 척도에 없는 차원 {dimension_id!r}"
            continue
        seen[dimension_id] = seen.get(dimension_id, 0) + 1
        error = check_dimension(row, refs, forbidden)
        if error:
            result.errors[dimension_id] = error
        else:
            result.valid[dimension_id] = normalize(row)
    for dimension_id in dimension_ids:
        if seen.get(dimension_id, 0) == 0:
            result.errors[dimension_id] = "missing: 차원 누락 — 모든 차원을 한 행씩 낸다"
        elif seen[dimension_id] > 1:
            result.errors[dimension_id] = "duplicate: 차원 중복 — 차원마다 한 행만 낸다"
            result.valid.pop(dimension_id, None)
    return result


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "dimension_id": row["dimension_id"],
        "level": row.get("level"),
        "no_record_reason": row.get("no_record_reason"),
        "evidence_refs": list(row.get("evidence_refs") or []),
        "rationale": row["rationale"],
        "confidence": row.get("confidence"),
    }


def reason_code(error: str | None) -> str:
    """검증 사유 문자열 앞의 코드. 없으면 format."""
    return error.split(":", 1)[0] if error and ":" in error else "format"


def failed_row(dimension_id: str, code: str = "format") -> dict[str, Any]:
    return {
        "dimension_id": dimension_id,
        "level": None,
        "no_record_reason": "evaluate_failed",
        "evidence_refs": [],
        "rationale": FAILED_RATIONALE.format(code=code),
        "confidence": None,
    }


# ---- 확신도 상한 (rubric.md 2.4) ---------------------------------------------------------------


def confidence_cap(
    dimension: dict[str, Any],
    units: int,
    element_cards: list[str],
    fired_cards: set[str],
) -> str:
    cap = "high"
    if units <= 1:
        cap = "medium"  # 근거 단위 1개
    if len(element_cards) >= 2 and sum(c in fired_cards for c in element_cards) == 1:
        cap = _lower(cap, "medium")  # 카드가 둘 이상인 요소(설득)에서 하나만 발동
    core = dimension.get("core_cards") or []
    if core and not any(c in fired_cards for c in core):
        cap = _lower(cap, "low")  # 핵심 카드 미발생, 보조 장면만
    return cap


def not_fired(row: dict[str, Any], dimension: dict[str, Any], fired_cards: set[str]) -> bool:
    """핵심 카드가 하나도 발동하지 않았는데 보조 장면 표시 없이 수준을 낸 행."""
    core = dimension.get("core_cards") or []
    return (
        row["level"] is not None
        and bool(core)
        and not any(c in fired_cards for c in core)
        and SUPPORT_MARK not in row["rationale"]
    )


def _lower(a: str, b: str) -> str:
    return a if CONFIDENCE_ORDER[a] <= CONFIDENCE_ORDER[b] else b


def clamp(row: dict[str, Any], cap: str) -> dict[str, Any]:
    """상한보다 높은 확신도만 상한으로 바꾼다. rationale은 그대로."""
    if row["level"] is None or row["confidence"] is None:
        return row
    return {**row, "confidence": _lower(row["confidence"], cap)}


# ---- 요소 수준 ---------------------------------------------------------------------------------


def element_level(rows: list[dict[str, Any]]) -> int | None:
    """수준이 있는 차원 평균을 반올림(.5 올림). 모두 기록 없음이면 None."""
    levels = [r["level"] for r in rows if r["level"] is not None]
    if not levels:
        return None
    return math.floor(sum(levels) / len(levels) + 0.5)


def element_summary(element: str, rows: list[dict[str, Any]], summary: str) -> dict[str, Any]:
    return {
        "element": element,
        "level": element_level(rows),
        "summary": summary,
        "dimension_count": len(rows),
        "no_record_count": sum(r["level"] is None for r in rows),
    }


# ---- 호출 ---------------------------------------------------------------------------------------


def user_prompt(
    element_rubric: dict[str, Any],
    no_record_reasons: list[dict[str, Any]],
    guide: str,
    record: dict[str, Any],
) -> str:
    rubric_block = {**element_rubric, "no_record_reasons": no_record_reasons}
    return "\n\n".join(
        [
            f"[요소] {element_rubric['element']}",
            "[요소 척도]\n" + json.dumps(rubric_block, ensure_ascii=False),
            "[요소 지침]\n" + guide,
            "[세션 기록]\n" + json.dumps(with_refs(record), ensure_ascii=False),
        ]
    )


def with_refs(record: dict[str, Any]) -> dict[str, Any]:
    """근거로 달 수 있는 행마다 `ref`(계약 근거 4종 문자열)를 붙여 모델이 그대로 베끼게 한다."""
    out = {**record}
    out["messages"] = [{**m, "ref": f"msg:{m['id']}"} for m in record.get("messages", [])]
    out["slots"] = [{**s, "ref": f"slot:{s['phase']}"} for s in record.get("slots", [])]
    proposal = record.get("proposal")
    if proposal and proposal.get("sections"):
        refs = {field: f"proposal:{field}" for field in proposal["sections"]}
        out["proposal"] = {**proposal, "section_refs": refs}
    contributions = record.get("contributions")
    if contributions and contributions.get("options"):
        options = [{**o, "ref": f"contribution:{o['option_id']}"} for o in contributions["options"]]
        out["contributions"] = {**contributions, "options": options}
    return out


def retry_block(errors: dict[str, str]) -> str:
    lines = [f"- {key}: {reason}" for key, reason in errors.items()]
    return (
        "\n\n[재요청]\n직전 출력에 아래 문제가 있었습니다. "
        "같은 출력 형식으로 이 요소의 차원 전부를 다시 판단해 JSON 하나만 내 주세요.\n"
        + "\n".join(lines)
    )


def summary_prompt(
    element_rubric: dict[str, Any], rows: list[dict[str, Any]], labels: dict[str, str]
) -> str:
    """요약 재작성 입력: 이 요소의 최종 차원 행만(세션 기록·다른 요소 결과 없음)."""
    names = {d["dimension_id"]: d.get("name") for d in element_rubric["dimensions"]}
    final = [
        {
            "dimension_id": r["dimension_id"],
            "name": names.get(r["dimension_id"]),
            "level": r["level"],
            "no_record": labels.get(r["no_record_reason"], r["no_record_reason"])
            if r["no_record_reason"]
            else None,
            "rationale": r["rationale"],
        }
        for r in rows
    ]
    return "\n\n".join(
        [
            f"[요소] {element_rubric['element']}",
            "[요약 재작성]\n워커 검증으로 이 요소의 차원 판단이 바뀌었습니다. "
            '아래 최종 차원 행만 보고 공통 지침 "요약 재작성"대로 summary를 다시 써 주세요.',
            "[최종 차원]\n" + json.dumps(final, ensure_ascii=False),
        ]
    )


def parse_json(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except ValueError:
        return None


@dataclass(frozen=True)
class EvalContext:
    record: dict[str, Any]
    rubric: dict[str, Any]
    refs: RefIndex
    prompts: Prompts
    forbidden: list[re.Pattern]

    @property
    def fired_cards(self) -> set[str]:
        """담당 동료 전원이 전달한 카드만. 부분 전달은 핵심 장면이 성립하지 않아 미발생과 같다."""
        return {o["card_id"] for o in self.record.get("observations", []) if o.get("delivered")}

    def element_cards(self, element: str) -> list[str]:
        return [c["card_id"] for c in self.rubric.get("cards", []) if c.get("element") == element]


async def rewrite_summary(
    llm: LLM,
    ctx: EvalContext,
    element_rubric: dict[str, Any],
    rows: list[dict[str, Any]],
    usage: dict[str, Any],
) -> str:
    """최종 차원 행으로 summary만 1회 다시 쓴다. 형식·금지어 실패면 고정 문장."""
    labels = {r["code"]: r["label"] for r in ctx.rubric.get("no_record_reasons", [])}
    text = await complete_tracked(
        llm, ctx.prompts.common, summary_prompt(element_rubric, rows, labels), usage
    )
    out = parse_json(text)
    summary = out.get("summary") if isinstance(out, dict) else None
    if not isinstance(summary, str) or not summary.strip() or has_forbidden(summary, ctx.forbidden):
        log.info("summary_rewrite_failed element=%s", element_rubric["element"])
        return REWRITE_FALLBACK
    return summary


async def evaluate_element(
    llm: LLM, ctx: EvalContext, element_rubric: dict[str, Any], usage: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """요소 하나: (차원 행 목록, 요소 요약). 전송 오류는 그대로 올린다."""
    element = element_rubric["element"]
    dimensions = element_rubric["dimensions"]
    dimension_ids = [d["dimension_id"] for d in dimensions]
    user = user_prompt(
        element_rubric,
        ctx.rubric.get("no_record_reasons", []),
        ctx.prompts.elements[element],
        ctx.record,
    )
    text = await complete_tracked(llm, ctx.prompts.common, user, usage)
    checked = validate_output(
        fix_refs(parse_json(text), ctx.refs), dimension_ids, ctx.refs, ctx.forbidden
    )
    if checked.errors:
        usage["element_retries"] += 1
        text = await complete_tracked(
            llm, ctx.prompts.common, user + retry_block(checked.errors), usage
        )
        checked = validate_output(
            fix_refs(parse_json(text), ctx.refs), dimension_ids, ctx.refs, ctx.forbidden
        )
    rows = []
    changed = False  # 워커가 수준·기록 없음을 바꾼 차원이 있으면 모델 summary와 어긋날 수 있다
    for dimension in dimensions:
        dimension_id = dimension["dimension_id"]
        row = checked.valid.get(dimension_id)
        if row is None:
            code = reason_code(checked.errors.get(dimension_id) or checked.errors.get("_format"))
            log.info("evaluate_failed dimension=%s reason=%s", dimension_id, code)
            rows.append(failed_row(dimension_id, code))
            changed = True
            continue
        if not_fired(row, dimension, ctx.fired_cards):
            log.info("event_not_fired dimension=%s (no support mark)", dimension_id)
            rows.append(
                {
                    **row,
                    "level": None,
                    "no_record_reason": "event_not_fired",
                    "confidence": None,
                    "rationale": row["rationale"] + NOT_FIRED_MARK,
                }
            )
            changed = True
            continue
        units = ctx.refs.counted_units(row["evidence_refs"], dimension_id)
        if row["level"] is not None and row["level"] >= 4 and units < 2:
            row = {**row, "level": 3, "rationale": row["rationale"] + CAPPED_MARK}
            changed = True
        cap = confidence_cap(dimension, units, ctx.element_cards(element), ctx.fired_cards)
        rows.append(clamp(row, cap))
    if all(r["no_record_reason"] == "evaluate_failed" for r in rows):
        summary = FAILED_SUMMARY
    elif changed:
        summary = await rewrite_summary(llm, ctx, element_rubric, rows, usage)
    else:
        summary = checked.summary or FAILED_SUMMARY
    return rows, element_summary(element, rows, summary)


async def run_evaluation(llm: LLM, ctx: EvalContext, usage: dict[str, Any]) -> dict[str, Any]:
    """5요소를 순서대로 평가해 save_evaluation 본문(session_id·run_id 제외)을 만든다."""
    dimensions: list[dict[str, Any]] = []
    elements: list[dict[str, Any]] = []
    for element_rubric in ctx.rubric["elements"]:
        rows, summary = await evaluate_element(llm, ctx, element_rubric, usage)
        dimensions.extend(rows)
        elements.append(summary)
    return {
        "rubric_version": ctx.rubric["rubric_version"],
        "prompt_versions": ctx.prompts.versions,
        "model": llm.model,
        "dimensions": dimensions,
        "elements": elements,
    }
