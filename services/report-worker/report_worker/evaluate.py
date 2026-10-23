"""평가 에이전트: 요소별 LLM 호출, 워커 검증, 요소 단위 1회 재요청, 확신도 clamp, 요소 수준·요약.

규칙 정본은 `docs/product/rubric.md` 2.2·2.4·7.2·7.3과 `docs/contracts/internal.md`
EvaluationSave·검증 경계다.

- 요소마다 별도 호출(5회). 입력은 그 요소의 척도·지침, 코드가 계산한 사실(`[사실]`: 결정 기록과
  차원별 장면 구간·구간 안 지원자 메시지, 메시지별 답한 대상·붙여넣기 의심)과 세션 기록 전체뿐이고
  다른 요소의 결과는 넣지 않는다(후광 방지).
- 형식 오류(파싱·스키마·차원 누락·중복·없는 차원), 근거 부재(수준이 있는데 같은 세션의 실제 행인
  지원자 근거가 최소 개수 미만), 금지어, 사실 모순(장면 구간에 지원자 메시지가 있는데
  `no_activity`, 핵심 카드가 발동했는데 `event_not_fired`, 지원자가 아닌 메시지를 근거로 씀, 근거에
  단 장면 id(핵심 `core`·척도의 보조 장면)에 그 메시지가 들지 않음, 결정 후 핵심 장면에 결정 전
  근거)이 하나라도 있으면 그 요소 호출
  전체를 실패 사유(어긴 사실 포함)와 함께 1회 다시 요청한다. 재요청 결과에서도 실패한 차원만
  `evaluate_failed`로 두고 나머지는 재요청 결과를 쓴다. 워커는 수준·근거를 고쳐 쓰지 않는다.
  값의 뜻이 하나뿐인 모양 차이(기록 없음 행의 confidence·근거 null, 숫자 문자열 수준 등)는 검증
  전에 맞춘다.
- 확신도 상한 위반은 거부하지 않고 상한으로 깎는다(clamp).
- 핵심 장면이 없는데(핵심 카드 미발생 등) 보조 장면 id를 단 근거 없이 수준을 낸 차원은 거부하지
  않고 `event_not_fired`로 바꾼다(rubric.md 2.4: 부재로만 낮게 보지 않는다). 담당 동료 일부만 전달한
  카드(`delivered=false`)는 발동으로 세지 않는다.
- 선결정(결정 A3): 핵심 카드가 지원자 선확정으로 생기지 않은 차원(`scenes` `preempted`)의
  `event_not_fired`·`no_activity`(위 변환 포함)는 `preempted`로 바꾼다. 장면이 생기지 않았으니
  반응 없음이 아니다. 척도 복사본에 `preempted` 사유가 없으면(이전 채용 건) 바꾸지 않는다.
- 지원자 안 채택(결정 A5): 결정 뒤를 보는 차원인데 지원자 안이 채택돼 결정 뒤 장면이 없으면
  (`scenes` `fixed_reason`) 모델 출력과 상관없이 워커가 기록 없음 행을 쓰고 재요청 사유에 넣지
  않는다.
- 요소 요약(summary)은 LLM을 다시 부르지 않고 최종 차원 행의 `summary_line`과 기록 없음 차원의
  상태(장면 없음·반응 없음·판단 보류)·사유로 코드가 조립한다(결정 A2·A3).
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
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any

import yaml

from report_worker.llm import LLM, complete_tracked
from report_worker.scenes import CORE, DimensionScenes, build_scenes, facts_block

log = logging.getLogger("report_worker.evaluate")

ELEMENTS = ("persuasion", "listening", "inclusion", "flexibility", "honesty")
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
# 모델이 쓸 수 있는 기록 없음 사유. evaluate_failed는 워커만 쓴다. preempted는 워커가 사실로
# 정하지만 모델이 낸 값이 사실과 맞으면 받는다(맞지 않으면 사실 모순)
MODEL_REASONS = frozenset(
    {"no_activity", "not_applicable", "event_not_fired", "auto_submitted_blank", "preempted"}
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
AUX_CAPPED_MARK = " (보조 장면 근거만이라 수준 상한 4 적용)"
NOT_FIRED_MARK = " (핵심 장면 없음·보조 장면 근거 없음으로 이벤트 미발생 처리)"
PREEMPTED_MARK = " (지원자가 카드 예정 시각 전에 칸을 확정해 선결정 처리)"
ADOPTED_RATIONALE = "지원자 안이 채택돼 결정 뒤 설계된 장면이 성립하지 않음"
PREEMPTED_RATIONALE = "지원자가 카드 예정 시각 전에 칸을 확정해 설계된 장면이 생기지 않음"
# 상태가 없는 이전 척도 복사본의 기록 없음 묶음 이름
NO_RECORD_GROUP = "기록 없음"
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
        self.messages = {m["id"]: m for m in record.get("messages", [])}
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
    """뜻이 하나뿐인 참조 모양만 고친다: 접두 없이 메시지 id만 쓴 `m-15`는 `msg:m-15`로, 숫자만
    쓴 `msg:15`는 세션에 `m-15`가 있을 때만 `msg:m-15`로(`evidence[].ref`·`evidence_refs` 모두).
    """
    rows = dimension_rows(output)
    if rows is None:
        return output
    for row in rows:
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("evidence_refs"), list):
            row["evidence_refs"] = [_fix_ref(ref, refs) for ref in row["evidence_refs"]]
        if isinstance(row.get("evidence"), list):
            row["evidence"] = [
                {**item, "ref": _fix_ref(item.get("ref"), refs)}
                if isinstance(item, dict)
                else _fix_ref(item, refs)
                for item in row["evidence"]
            ]
    return {"dimensions": rows}


def _fix_ref(ref: Any, refs: RefIndex) -> Any:
    if not isinstance(ref, str):
        return ref
    if ref in refs.message_sender:
        return f"msg:{ref}"
    if (
        re.fullmatch(r"msg:\d+", ref)
        and f"m-{ref[4:]}" in refs.message_sender
        and ref[4:] not in refs.message_sender
    ):
        return f"msg:m-{ref[4:]}"
    return ref


# ---- 검증 (rubric.md 7.3) ---------------------------------------------------------------------


def tidy(row: Any) -> Any:
    """뜻이 하나뿐인 모양 차이만 맞춘다(내용 규칙은 그대로).

    - 근거: `evidence: [{ref, scene}]`를 `evidence_refs`(문자열)와 `evidence_scenes`({ref: 장면
      id})로 나눈다. 장면을 적지 않은 근거(문자열 하나, 또는 `evidence_refs`)는 핵심 장면(`core`)
    - 기록 없음 행의 confidence·근거 null, "3"·3.0 같은 수준, 대문자 확신도, 빈 문자열 사유
    수준과 기록 없음 사유가 둘 다 있으면 뜻이 둘이라 고치지 않는다(형식 오류로 재요청).
    """
    if not isinstance(row, dict):
        return row
    row = dict(row)
    row.pop("context_refs", None)  # 장면을 만든 동료 발언 표시(근거가 아니고 저장하지 않음)
    if "result" in row and "level" not in row and "no_record_reason" not in row:
        # 출력 형식: 수준(1~5)과 기록 없음 사유를 한 칸 `result`에 낸다
        result = row.pop("result")
        if isinstance(result, str) and not result.strip().isdigit():
            row["level"], row["no_record_reason"] = None, result.strip()
        else:
            row["level"] = result
    row.setdefault("no_record_reason", None)
    evidence = row.pop("evidence", None)
    if isinstance(evidence, list):
        refs, scenes = [], {}
        for item in evidence:
            if isinstance(item, dict):
                ref = item.get("ref")
                scene = item.get("scene")
                if isinstance(ref, str) and isinstance(scene, str) and scene.strip():
                    scenes[ref] = scene.strip()
                refs.append(ref)
            else:
                refs.append(item)
        row["evidence_refs"] = refs
        row["evidence_scenes"] = scenes
    level = row.get("level")
    if isinstance(level, str) and level.strip().isdigit():
        row["level"] = int(level.strip())
    elif isinstance(level, float) and level.is_integer():
        row["level"] = int(level)
    if row.get("no_record_reason") in ("", "null", "none", "None"):
        row["no_record_reason"] = None
    if isinstance(row.get("confidence"), str):
        row["confidence"] = row["confidence"].strip().lower() or None
    if row.get("evidence_refs") is None:
        row["evidence_refs"] = []
    elif isinstance(row["evidence_refs"], str):
        row["evidence_refs"] = [row["evidence_refs"]]
    row.setdefault("evidence_scenes", {})
    if row.get("level") is None and row.get("no_record_reason") is not None:
        row["confidence"] = None
    return row


def _refs_text(ids: list[str] | tuple[str, ...], limit: int = 8) -> str:
    shown = [f"msg:{i}" for i in ids[:limit]]
    return ", ".join(shown) + (f" 외 {len(ids) - limit}개" if len(ids) > limit else "")


def scene_of(row: dict[str, Any], ref: str) -> str:
    """근거 하나에 붙은 장면 id. 적지 않았으면 핵심 장면."""
    return row.get("evidence_scenes", {}).get(ref, CORE)


def candidate_msgs(row: dict[str, Any], refs: "RefIndex") -> list[str]:
    return [
        r
        for r in row["evidence_refs"]
        if r.startswith("msg:") and refs.message_sender.get(r[4:]) == "candidate"
    ]


def check_facts(row: dict[str, Any], refs: "RefIndex", dim: DimensionScenes | None) -> str | None:
    """기록된 사실과 모순되는 판단(결정 A1). 저장 순서·단계·보낸 자리·장면 id만 보고 문장 뜻은
    추측하지 않는다."""
    level, reason = row["level"], row["no_record_reason"]
    if dim is None:
        return None
    core = dim.core
    if level is None:
        if reason == "no_activity" and core.candidate_ids:
            return (
                f"activity_present: 사실 — 이 차원의 핵심 장면({core.label})에 지원자 메시지 "
                f"{len(core.candidate_ids)}개({_refs_text(core.candidate_ids)})가 있다. "
                "no_activity는 쓸 수 없다. 그 메시지로 수준을 판단하거나, 메시지가 있어도 "
                "이 차원이 볼 장면·전제가 없으면 not_applicable과 그 이유를 쓴다"
            )
        if reason == "preempted" and not dim.preempted:
            return (
                "not_preempted: 사실 — 이 차원의 핵심 카드는 선결정되지 않았다. preempted는 쓸 수 "
                "없다. 사실에 맞는 다른 사유를 쓰거나 장면 안 행동으로 판단한다"
            )
        if reason == "event_not_fired" and core.exists:
            return (
                f"card_fired: 사실 — 이 차원의 핵심 장면이 있다({core.label}). "
                "event_not_fired는 쓸 수 없다. 장면 안 행동으로 판단하거나 no_activity·"
                "not_applicable 중 사실에 맞는 사유를 쓴다"
            )
        return None
    msgs = [r[4:] for r in row["evidence_refs"] if r.startswith("msg:")]
    peers = [i for i in msgs if refs.message_sender.get(i) != "candidate"]
    if peers:
        who = ", ".join(f"msg:{i}({refs.message_sender.get(i)})" for i in peers)
        hint = (
            f" 이 차원 핵심 장면의 지원자 메시지: {_refs_text(core.candidate_ids)}."
            if (core.candidate_ids)
            else ""
        )
        return (
            f"peer_evidence: 사실 — {who}는 지원자 메시지가 아니다. 그 참조만 evidence에서 "
            "context_refs로 옮기고 result·rationale은 그대로 둔다(남은 지원자 근거가 1개여도 "
            "된다. 수준 4·5에 근거가 1개면 워커가 수준 3으로 둔다)."
            f"{hint} 남은 지원자 근거가 하나도 없을 때만 기록 없음 사유를 쓴다"
        )
    tags = {f"msg:{i}": scene_of(row, f"msg:{i}") for i in msgs}
    field_tags = {  # 보조 장면 id를 단 기획안 필드 근거(장면 id가 없으면 기존처럼 핵심)
        r: scene_of(row, r)
        for r in row["evidence_refs"]
        if r.startswith("proposal:") and scene_of(row, r) != CORE
    }
    unknown = sorted({t for t in [*tags.values(), *field_tags.values()] if dim.get(t) is None})
    if unknown:
        return (
            f"unknown_scene: 장면 id {unknown}는 이 차원에 없다. [사실]의 이 차원 scenes id"
            f"({', '.join(dim.ids)}) 중 하나를 근거마다 단다"
        )
    for ref, tag in field_tags.items():
        field = ref.split(":", 1)[1]
        if field not in dim.get(tag).fields:
            allowed = [f"{x.scene_id}: {sorted(x.fields)}" for x in dim.aux if x.fields]
            return (
                f"out_of_scene: 사실 — {ref}는 장면 {tag}가 근거로 받는 기획안 필드가 아니다. "
                f"기획안 필드를 받는 보조 장면: {', '.join(allowed) or '없음'}"
            )
    if not core.exists and not field_tags and all(t == CORE for t in tags.values()):
        return None  # 핵심 장면 없이 핵심 장면 근거만: not_fired가 event_not_fired로 바꾼다
    for ref, tag in tags.items():
        message_id = ref[4:]
        scene = dim.get(tag)
        if scene.exists and scene.contains(message_id):
            continue
        phase = refs.messages[message_id]["phase"]
        if tag == CORE and dim.is_before_decision(phase):
            after = _refs_text(core.candidate_ids) or "없음"
            return (
                f"before_decision: 사실 — {ref}는 {dim.decision.describe()} 전 메시지다. 이 "
                f"차원의 핵심 장면은 결정 뒤 행동만 본다. 결정 뒤 핵심 장면의 지원자 메시지: "
                f"{after}. 결정 뒤 메시지가 없으면 no_activity, 결정 뒤 볼 장면이 없으면 "
                "not_applicable. 보조 장면에 드는 메시지면 그 보조 장면 id를 단다"
            )
        holding = dim.holding(message_id)
        where = (
            f"이 메시지가 드는 장면: {', '.join(holding)}"
            if holding
            else ("이 메시지는 이 차원의 어느 장면에도 들지 않는다")
        )
        return (
            f"out_of_scene: 사실 — {ref}는 장면 {tag}({scene.label})에 들지 않는다. {where}. "
            "근거마다 그 메시지가 드는 장면 id를 달거나, 장면에 드는 메시지로 판단한다"
        )
    return None


def check_dimension(
    row: Any,
    refs: "RefIndex",
    forbidden: list[re.Pattern],
    dim: DimensionScenes | None = None,
) -> str | None:
    """차원 한 행의 실패 사유. 통과하면 None. dim이 없으면 사실 모순 검사는 하지 않는다."""
    if not isinstance(row, dict):
        return "format: 차원 행이 객체가 아님"
    dimension_id = row.get("dimension_id")
    level = row.get("level")
    reason = row.get("no_record_reason")
    evidence = row.get("evidence_refs")
    rationale = row.get("rationale")
    confidence = row.get("confidence")
    line = row.get("summary_line")
    if not isinstance(evidence, list) or not all(isinstance(r, str) for r in evidence):
        return "format: evidence의 ref는 문자열이어야 함"
    if not isinstance(rationale, str) or not rationale.strip():
        return "format: rationale이 비어 있음"
    if line is not None and not isinstance(line, str):
        return "format: summary_line은 문자열이어야 함"
    if has_forbidden(rationale, forbidden) or (line and has_forbidden(line, forbidden)):
        return "forbidden: rationale·summary_line에 금지어(합격·순위·추천·점수 표현)를 쓰지 않는다"
    bad = [r for r in evidence if not refs.exists(r)]
    if bad:
        return f"ref_not_in_session: 참조 {bad}가 세션에 없다. 세션 기록의 ref 문자열만 쓴다"
    if level is not None and reason is not None:
        return (
            "format: level과 no_record_reason은 둘 중 하나만 쓴다 — 수준이면 "
            "no_record_reason을 빼고, 기록 없음이면 level·confidence를 null로 둔다"
        )
    if level is None:
        if reason not in MODEL_REASONS:
            return f"format: 기록 없음 사유가 올바르지 않음 {reason!r}"
        if confidence is not None:
            return "format: 기록 없음이면 confidence는 null"
        return check_facts(row, refs, dim)
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 5:
        return f"format: level은 1~5 정수 또는 null {level!r}"
    if confidence not in CONFIDENCE_ORDER:
        return f"format: confidence 값이 올바르지 않음 {confidence!r}"
    fact = check_facts(row, refs, dim)
    if fact:
        return fact
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
    valid: dict[str, dict[str, Any]]  # dimension_id → 통과한 행(내부 키 포함)
    errors: dict[str, str]  # dimension_id 또는 "_format" → 사유
    raw: dict[str, Any] = dc_field(default_factory=dict)  # dimension_id → 모델이 낸 행 그대로


def dimension_rows(output: Any) -> list[Any] | None:
    """출력의 차원 행 목록. `{dimensions: [...]}`가 기본이고, 행 목록만 낸 출력과
    `{dimensions: {<dimension_id>: 행}}`처럼 뜻이 하나인 모양은 같은 목록으로 읽는다."""
    if isinstance(output, list):
        return output
    if not isinstance(output, dict):
        return None
    rows = output.get("dimensions")
    if isinstance(rows, dict):
        return [
            {"dimension_id": key, **value} if isinstance(value, dict) else value
            for key, value in rows.items()
        ]
    return rows if isinstance(rows, list) else None


def validate_output(
    output: Any,
    dimension_ids: list[str],
    refs: RefIndex,
    forbidden: list[re.Pattern],
    scenes: dict[str, DimensionScenes] | None = None,
) -> Validation:
    result = Validation({}, {})
    rows = dimension_rows(output)
    if rows is None:
        result.errors["_format"] = "format: 출력이 {dimensions: [...]} 형식이 아님"
        return result
    seen: dict[str, int] = {}
    for raw in rows:
        row = tidy(raw)
        dimension_id = row.get("dimension_id") if isinstance(row, dict) else None
        if dimension_id not in dimension_ids:
            result.errors["_format"] = f"format: 척도에 없는 차원 {dimension_id!r}"
            continue
        result.raw[dimension_id] = raw
        seen[dimension_id] = seen.get(dimension_id, 0) + 1
        error = check_dimension(row, refs, forbidden, (scenes or {}).get(dimension_id))
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


CONTRACT_KEYS = (
    "dimension_id",
    "level",
    "no_record_reason",
    "evidence_refs",
    "rationale",
    "confidence",
)


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    """저장 형식 키 + 워커 내부 키(근거별 장면 `evidence_scenes`, 요약 조각 `summary_line`)."""
    line = row.get("summary_line")
    return {
        "dimension_id": row["dimension_id"],
        "level": row.get("level"),
        "no_record_reason": row.get("no_record_reason"),
        "evidence_refs": list(row.get("evidence_refs") or []),
        "rationale": row["rationale"],
        "confidence": row.get("confidence"),
        "evidence_scenes": dict(row.get("evidence_scenes") or {}),
        "summary_line": line.strip() if isinstance(line, str) and line.strip() else None,
    }


def contract_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in CONTRACT_KEYS}


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
        "evidence_scenes": {},
        "summary_line": None,
    }


def fixed_reason(dim: DimensionScenes | None, codes: set[str]) -> str | None:
    """워커가 정하는 기록 없음 사유(결정 A5). 척도 복사본에 없는 사유는 not_applicable로."""
    reason = dim.fixed_reason if dim is not None else None
    if reason == "preempted" and reason not in codes:
        return "not_applicable"
    return reason


def fixed_row(dimension_id: str, reason: str) -> dict[str, Any]:
    rationale = PREEMPTED_RATIONALE if reason == "preempted" else ADOPTED_RATIONALE
    return {**failed_row(dimension_id), "no_record_reason": reason, "rationale": rationale}


def preempt(row: dict[str, Any], dim: DimensionScenes | None, codes: set[str]) -> dict[str, Any]:
    """선결정 차원의 event_not_fired·no_activity를 preempted로(결정 A3). 장면이 생기지 않았으니
    반응 없음도 아니다."""
    if (
        dim is None
        or not dim.preempted
        or "preempted" not in codes
        or row["no_record_reason"] not in ("event_not_fired", "no_activity")
    ):
        return row
    return {**row, "no_record_reason": "preempted", "rationale": row["rationale"] + PREEMPTED_MARK}


# ---- 확신도 상한 (rubric.md 2.4) ---------------------------------------------------------------


def scene_refs(row: dict[str, Any], refs: RefIndex) -> list[str]:
    """장면이 정해지는 근거: 지원자 메시지와, 보조 장면 id를 단 기획안 필드."""
    tagged_fields = [
        r for r in row["evidence_refs"] if r.startswith("proposal:") and scene_of(row, r) != CORE
    ]
    return candidate_msgs(row, refs) + tagged_fields


def aux_only(row: dict[str, Any], refs: RefIndex) -> bool:
    """장면이 정해지는 근거가 있고 모두 보조 장면 id가 달렸다."""
    scoped = scene_refs(row, refs)
    return bool(scoped) and all(scene_of(row, r) != CORE for r in scoped)


def confidence_cap(
    dimension: dict[str, Any],
    units: int,
    element_cards: list[str],
    fired_cards: set[str],
    dim: DimensionScenes | None = None,
    support_only: bool = False,
) -> str:
    cap = "high"
    if units <= 1:
        cap = "medium"  # 근거 단위 1개
    if len(element_cards) >= 2 and sum(c in fired_cards for c in element_cards) == 1:
        cap = _lower(cap, "medium")  # 카드가 둘 이상인 요소(설득)에서 하나만 발동
    core = dimension.get("core_cards") or []
    core_missing = dim is not None and not dim.core.exists
    if (core and not any(c in fired_cards for c in core)) or core_missing or support_only:
        cap = _lower(cap, "low")  # 핵심 장면 없음, 또는 보조 장면 근거만
    return cap


def not_fired(row: dict[str, Any], dim: DimensionScenes | None, refs: RefIndex) -> bool:
    """핵심 장면이 없는데(핵심 카드 미발생·결정 뒤 없음) 보조 장면 근거 없이 수준을 낸 행."""
    if row["level"] is None or dim is None or dim.core.exists:
        return False
    return not any(scene_of(row, r) != CORE for r in scene_refs(row, refs))


def _lower(a: str, b: str) -> str:
    return a if CONFIDENCE_ORDER[a] <= CONFIDENCE_ORDER[b] else b


def clamp(row: dict[str, Any], cap: str) -> dict[str, Any]:
    """상한보다 높은 확신도만 상한으로 바꾼다. rationale은 그대로."""
    if row["level"] is None or row["confidence"] is None:
        return row
    return {**row, "confidence": _lower(row["confidence"], cap)}


# ---- 요소 수준·요약 ----------------------------------------------------------------------------


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


def first_sentence(rationale: str) -> str:
    """판단 이유의 첫 문장(워커가 붙인 상한·변환 표시는 뺀다). summary_line이 없을 때 쓴다."""
    text = rationale
    for mark in (CAPPED_MARK, AUX_CAPPED_MARK, NOT_FIRED_MARK, PREEMPTED_MARK):
        text = text.replace(mark, "")
    text = text.strip()
    return re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0] if text else ""


def build_summary(
    element_rubric: dict[str, Any],
    rows: list[dict[str, Any]],
    labels: dict[str, str],
    forbidden: list[re.Pattern],
    states: dict[str, str] | None = None,
) -> str:
    """요소 요약을 최종 차원 행에서 결정적으로 조립한다(결정 A2). 수준이 있는 차원은 수준이 높은
    순서로 그 행의 `summary_line`(없으면 그 행 판단 이유의 첫 문장), 기록 없음 차원은 상태별로
    이름과 사유 표시(결정 A3).

    states는 사유 코드 → 상태 이름(`rubric.yaml` record_states, 상태 순서대로). 없으면(이전 척도
    복사본) "기록 없음" 하나로 묶는다. 판단 보류 상태는 사유 문장이 상태 이름과 같은 말이라 이름만
    쓴다.
    """
    if all(r["no_record_reason"] == "evaluate_failed" for r in rows):
        return FAILED_SUMMARY
    states = states or {}
    names = {
        d["dimension_id"]: d.get("name") or d["dimension_id"] for d in element_rubric["dimensions"]
    }
    order = {d["dimension_id"]: i for i, d in enumerate(element_rubric["dimensions"])}
    leveled = sorted(
        (r for r in rows if r["level"] is not None),
        key=lambda r: (-r["level"], order[r["dimension_id"]]),
    )
    parts = []
    for row in leveled:
        line = (row.get("summary_line") or first_sentence(row["rationale"])).strip().rstrip(".")
        parts.append(f"{line}." if line else f"{names[row['dimension_id']]} 수준 {row['level']}.")
    groups: dict[str, list[str]] = {name: [] for name in dict.fromkeys(states.values())}
    for r in rows:
        if r["level"] is not None:
            continue
        reason = r["no_record_reason"]
        group = states.get(reason, NO_RECORD_GROUP)
        name = names[r["dimension_id"]]
        shown = (
            name
            if reason == "evaluate_failed" and reason in states
            else (f"{name}({labels.get(reason, reason)})")
        )
        groups.setdefault(group, []).append(shown)
    for group, items in groups.items():
        if items:
            parts.append(f"{group}: " + ", ".join(items) + ".")
    summary = " ".join(parts)
    if has_forbidden(summary, forbidden):  # 행마다 검사했으므로 겹쳐 생긴 경우만
        return FAILED_SUMMARY
    return summary


# ---- 호출 ---------------------------------------------------------------------------------------


def user_prompt(
    element_rubric: dict[str, Any],
    no_record_reasons: list[dict[str, Any]],
    guide: str,
    record: dict[str, Any],
    facts: dict[str, Any],
) -> str:
    rubric_block = {**element_rubric, "no_record_reasons": no_record_reasons}
    return "\n\n".join(
        [
            f"[요소] {element_rubric['element']}",
            "[요소 척도]\n" + json.dumps(rubric_block, ensure_ascii=False),
            "[요소 지침]\n" + guide,
            "[사실]\n" + json.dumps(facts, ensure_ascii=False),
            "[세션 기록]\n" + json.dumps(with_refs(record), ensure_ascii=False),
        ]
    )


EVALUATOR_FACT_KEYS = ("decisions", "message_facts")  # [사실]·메시지 행으로 옮겨 보이는 사실


def with_refs(record: dict[str, Any]) -> dict[str, Any]:
    """근거로 달 수 있는 행마다 `ref`(계약 근거 4종 문자열)를 붙여 모델이 그대로 베끼게 한다.

    메시지는 지원자 메시지에만 `ref`가 있다(AI 동료·시스템 메시지는 근거가 될 수 없다).

    지원자 메시지에는 메시지별 사실(답한 대상 `reply_to`, 먼저 정해진 칸 `decided_before`, 붙여넣기
    의심 `paste_suspect`)을 붙이고, 같은 값을 `facts`에서 빼 입력이 두 번 들어가지 않게 한다.
    """
    out = {**record}
    facts = record.get("facts") or {}
    per_message = {f["message_id"]: f for f in facts.get("message_facts") or []}
    messages = []
    for m in record.get("messages", []):
        row = {**m, "ref": f"msg:{m['id']}"} if m["sender"] == "candidate" else {**m}
        fact = per_message.get(m["id"])
        if fact is not None:
            row["reply_to"] = f"msg:{fact['reply_to']}" if fact.get("reply_to") else None
            row["decided_before"] = fact.get("decided_before") or []
            row["paste_suspect"] = fact.get("paste_suspect")
        messages.append(row)
    out["messages"] = messages
    out["facts"] = {k: v for k, v in facts.items() if k not in EVALUATOR_FACT_KEYS}
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


def scene_fact(dim: DimensionScenes | None) -> str:
    """재요청 사유 뒤에 붙이는 그 차원의 장면 사실 한 줄."""
    if dim is None:
        return ""
    parts = []
    for scene in (dim.core, *dim.aux):
        ids = _refs_text(scene.candidate_ids) if scene.candidate_ids else "없음"
        state = ids if scene.exists else "장면 없음"
        parts.append(f"{scene.scene_id}: {state}")
    banned = []
    if dim.core.candidate_ids:
        banned.append("no_activity")
    if dim.core.exists:
        banned.append("event_not_fired")
    tail = f". 이 차원에 {'·'.join(banned)}는 쓸 수 없다" if banned else ""
    return f" [사실: 장면별 지원자 메시지 — {'; '.join(parts)}{tail}]"


def retry_block(
    errors: dict[str, str],
    scenes: dict[str, DimensionScenes] | None = None,
    previous: dict[str, Any] | None = None,
) -> str:
    """차원별 거부 사유 + 그 차원의 장면 사실 + 직전에 낸 그 행. 문제가 없는 차원은 그대로 낸다."""
    lines = []
    for key, reason in errors.items():
        lines.append(f"- {key}: {reason}{scene_fact((scenes or {}).get(key))}")
        if previous and key in previous:
            lines.append("  직전 행: " + json.dumps(previous[key], ensure_ascii=False))
    return (
        "\n\n[재요청]\n직전 출력에 아래 문제가 있었습니다. 문제가 된 차원은 직전 행에서 그 문제만 "
        "[사실]에 맞게 고치고(문제와 상관없는 판단은 바꾸지 않는다), 문제가 없는 차원은 직전 "
        "판단 그대로 두어 같은 출력 형식으로 이 요소의 차원 전부를 JSON 하나로 내 주세요.\n"
        + "\n".join(lines)
    )


def parse_json(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    # 앞뒤에 설명 문장이 붙은 출력: 처음 '{'부터 마지막 '}'까지가 JSON 하나면 그것을 쓴다
    first, last = cleaned.find("{"), cleaned.rfind("}")
    if 0 <= first < last:
        try:
            return json.loads(cleaned[first : last + 1])
        except ValueError:
            return None
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

    @property
    def scenes(self) -> dict[str, DimensionScenes]:
        return build_scenes(self.record, self.rubric)

    @property
    def reason_codes(self) -> set[str]:
        """채용 건 척도 복사본의 기록 없음 사유 코드(이전 복사본에는 preempted가 없다)."""
        return {r["code"] for r in self.rubric.get("no_record_reasons", [])}

    @property
    def reason_states(self) -> dict[str, str]:
        """사유 코드 → 상태 이름(record_states 순서). 이전 복사본에는 없어 빈 dict."""
        names = {s["state"]: s["label"] for s in self.rubric.get("record_states") or []}
        order = {state: i for i, state in enumerate(names)}
        reasons = [r for r in self.rubric.get("no_record_reasons", []) if r.get("state") in names]
        reasons.sort(key=lambda r: order[r["state"]])
        return {r["code"]: names[r["state"]] for r in reasons}

    def element_cards(self, element: str) -> list[str]:
        return [c["card_id"] for c in self.rubric.get("cards", []) if c.get("element") == element]


async def evaluate_element(
    llm: LLM, ctx: EvalContext, element_rubric: dict[str, Any], usage: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """요소 하나: (차원 행 목록, 요소 요약). 전송 오류는 그대로 올린다."""
    element = element_rubric["element"]
    dimensions = element_rubric["dimensions"]
    dimension_ids = [d["dimension_id"] for d in dimensions]
    scenes = ctx.scenes
    user = user_prompt(
        element_rubric,
        ctx.rubric.get("no_record_reasons", []),
        ctx.prompts.elements[element],
        ctx.record,
        facts_block(element_rubric, scenes, ctx.record),
    )

    # 지원자 안 채택으로 결정 뒤 장면이 없는 차원은 워커가 정하므로 그 출력 오류로 재요청하지 않는다
    fixed = {
        i: reason
        for i in dimension_ids
        if (reason := fixed_reason(scenes.get(i), ctx.reason_codes)) is not None
    }

    def check(text: str) -> Validation:
        output = fix_refs(parse_json(text), ctx.refs)
        result = validate_output(output, dimension_ids, ctx.refs, ctx.forbidden, scenes)
        for dimension_id in fixed:
            result.errors.pop(dimension_id, None)
        return result

    checked = check(await complete_tracked(llm, ctx.prompts.common, user, usage))
    if checked.errors:
        usage["element_retries"] += 1
        retry = user + retry_block(checked.errors, scenes, checked.raw)
        first = checked
        checked = check(await complete_tracked(llm, ctx.prompts.common, retry, usage))
        # 재요청에서 새로 실패한 차원은 첫 출력에서 검증을 통과한 행을 쓴다(통과 행끼리만 고른다)
        for dimension_id, row in first.valid.items():
            if dimension_id not in checked.valid:
                checked.valid[dimension_id] = row
    rows = []
    for dimension in dimensions:
        dimension_id = dimension["dimension_id"]
        dim = scenes.get(dimension_id)
        if dimension_id in fixed:
            log.info("fixed dimension=%s reason=%s", dimension_id, fixed[dimension_id])
            rows.append(fixed_row(dimension_id, fixed[dimension_id]))
            continue
        row = checked.valid.get(dimension_id)
        if row is None:
            code = reason_code(checked.errors.get(dimension_id) or checked.errors.get("_format"))
            log.info("evaluate_failed dimension=%s reason=%s", dimension_id, code)
            rows.append(failed_row(dimension_id, code))
            continue
        if not_fired(row, dim, ctx.refs):
            log.info("event_not_fired dimension=%s (no auxiliary scene evidence)", dimension_id)
            row = {
                **row,
                "level": None,
                "no_record_reason": "event_not_fired",
                "confidence": None,
                "rationale": row["rationale"] + NOT_FIRED_MARK,
            }
        if row["level"] is None:
            rows.append(preempt(row, dim, ctx.reason_codes))
            continue
        if row["level"] == 5 and aux_only(row, ctx.refs):  # 보조 장면만으로 5를 주지 않는다
            row = {**row, "level": 4, "rationale": row["rationale"] + AUX_CAPPED_MARK}
        units = ctx.refs.counted_units(row["evidence_refs"], dimension_id)
        if row["level"] is not None and row["level"] >= 4 and units < 2:
            row = {**row, "level": 3, "rationale": row["rationale"] + CAPPED_MARK}
        cap = confidence_cap(
            dimension,
            units,
            ctx.element_cards(element),
            ctx.fired_cards,
            dim,
            support_only=aux_only(row, ctx.refs),
        )
        rows.append(clamp(row, cap))
    labels = {r["code"]: r["label"] for r in ctx.rubric.get("no_record_reasons", [])}
    summary = build_summary(element_rubric, rows, labels, ctx.forbidden, ctx.reason_states)
    final = [contract_row(r) for r in rows]
    return final, element_summary(element, final, summary)


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
