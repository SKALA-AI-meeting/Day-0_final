"""단언. 번호는 docs/guides/testing.md e2e "단언" 번호와 같다. 러너가 모은 bundle(dict)만 본다.

check(bundle) -> [{id, name, ok, failures, info?}]. 실패는 문장으로 남겨 summary.md에 그대로 쓴다.
0은 러너 step 오류, R은 restart 대본 단언이다. 시간 값은 서버가 준 ISO 시각끼리 비교하고(가짜 시계로
당긴 초는 bundle["advances"]로 보정), 벽시계는 지연 측정(9)에만 쓴다.
"""

import math
import re
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

from e2e import catalog
from e2e.client import parse_time

GUARD_PATTERNS = (
    Path(__file__).resolve().parents[2]
    / "scenarios"
    / "missions"
    / "debate"
    / "prompts"
    / "shared"
    / "guard_patterns.yaml"
)
# 평가 텍스트 금지 패턴은 카탈로그 데이터(워커 검증과 같은 목록)가 정본이다
FORBIDDEN = re.compile(
    "|".join(yaml.safe_load(GUARD_PATTERNS.read_text("utf-8"))["evaluator_forbidden"]["patterns"])
)
EVIDENCE_REF = re.compile(r"^(msg|slot|proposal|contribution):\S+$")
TOTAL_FIELDS = {"total", "total_score", "score", "sum", "overall", "grand_total"}
SEATS = {"candidate", "p1", "p2", "p3"}
NO_RECORD = {
    "no_activity",
    "evaluate_failed",
    "not_applicable",
    "event_not_fired",
    "auto_submitted_blank",
}
FACT_KEYS = {
    "message_count",
    "spontaneous_count",
    "invited_count",
    "spontaneous_ratio",
    "silence_invites_received",
    "first_message_sec",
    "response_latency_median_sec",
    "avg_message_chars",
    "intent_counts",
    "name_mentions",
    "slot_records",
    "own_options_proposed",
    "own_options_adopted",
    "pressure_responded",
    "invite_latency_sec",
    "quiet_spoke",
    "own_option_rejected",
    "messages_after_rejection",
    "ai_adopted_units",
    "credit_field_filled",
    "assistant_calls",
    "material_views",
    "connection_gaps",
}
CONTRIBUTION_KEYS = {
    "phase",
    "option_id",
    "label",
    "first_proposer",
    "credited_in_proposal",
    "credit_quote",
}
PREEMPTION = (
    Path(__file__).resolve().parents[2]
    / "scenarios"
    / "missions"
    / "debate"
    / "prompts"
    / "fixtures"
    / "preemption.yaml"
)
PHASE_CHANGE_SECONDS = 15
TYPING_GRACE_SECONDS = 0.25  # 입력 시작 직전에 이미 전송 중이던 프레임은 위반이 아니다
LATENCY_TARGET_SEC = 8
INTENT_TARGET_SEC = 3
COST_TARGET_USD = 0.5


# ---- 공용 -------------------------------------------------------------------------------------


def frames(b: dict[str, Any], *types: str) -> list[dict[str, Any]]:
    return [f for f in b["frames"] if not types or f["type"] in types]


def messages(b: dict[str, Any]) -> list[dict[str, Any]]:
    return b.get("messages") or []


def slots_by_phase(b: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["phase"]: s for s in b.get("slots") or []}


def shifted_before(b: dict[str, Any], wall: float) -> float:
    """그 시각(벽시계) 전에 가짜 시계로 당긴 초 합."""
    return sum(a["seconds"] for a in b["advances"] if a["at"] <= wall)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(pct / 100 * len(ordered)) - 1)]


def half_up_mean(levels: list[int]) -> int | None:
    return math.floor(sum(levels) / len(levels) + 0.5) if levels else None


def card_actor_ids(card_id: str) -> set[str]:
    return {catalog.seat_of(kind) for kind in _as_list(catalog.cards()[card_id]["actor"])}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value] if value else []


def phase_starts(b: dict[str, Any]) -> dict[str, float]:
    """단계 시작 t_min (phase.changed 기준)."""
    return {f["payload"]["phase"]: f["payload"]["t_min"] for f in frames(b, "phase.changed")}


def phase_ends(b: dict[str, Any]) -> dict[str, float]:
    """단계 끝 t_min = 다음 단계 시작, proposal은 session.ended 시각."""
    starts = phase_starts(b)
    ends: dict[str, float] = {}
    names = [p for p in catalog.PHASES if p in starts]
    for current, nxt in zip(names, names[1:], strict=False):
        ends[current] = starts[nxt]
    ended = (b.get("session") or {}).get("completed_at")
    if "proposal" in starts and ended and b.get("session", {}).get("t_min_now") is not None:
        ends["proposal"] = b["session"]["t_min_now"]
    return ends


# ---- 0 러너 -----------------------------------------------------------------------------------


def assert_0_steps(b: dict[str, Any]) -> list[str]:
    return [f"step 오류: {e}" for e in b["step_errors"]]


# ---- 1 단계 -----------------------------------------------------------------------------------


def assert_1_phases(b: dict[str, Any]) -> list[str]:
    out: list[str] = []
    changed = frames(b, "phase.changed")
    order = [
        "keyword",
        *(f["payload"]["phase"] for f in changed if f["payload"]["reason"] != "start"),
    ]
    if order != list(catalog.PHASES):
        out.append(f"단계 순서가 {list(catalog.PHASES)}가 아님: {order}")
    if not frames(b, "session.ended"):
        out.append("session.ended 프레임 없음")
    slots = slots_by_phase(b)
    for phase in catalog.PHASES[:3]:
        slot = slots.get(phase)
        if not slot or slot["status"] != "confirmed":
            out.append(f"{phase} 칸이 confirmed가 아님")
            continue
        if slot["filled_by"] not in SEATS or slot["resolution"] not in ("consensus", "timeout"):
            out.append(
                f"{phase} 칸 값 이상: filled_by={slot['filled_by']} resolution={slot['resolution']}"
            )
        for key, want in (b["script"].get("expect", {}).get("slots", {}).get(phase) or {}).items():
            if slot.get(key) != want:
                out.append(f"{phase} 칸 {key}={slot.get(key)!r}, 기대 {want!r}")
    started = parse_time(b["started"]["started_at"])
    minutes = b["minutes"]
    deadlines = catalog.phase_deadlines(minutes)
    previous = None
    for frame in changed:
        p = frame["payload"]
        shift = shifted_before(b, frame["_at"])
        expected = started + timedelta(minutes=deadlines[p["phase"]]) - timedelta(seconds=shift)
        got = parse_time(p["phase_deadline_at"])
        if abs((got - expected).total_seconds()) > 2:
            out.append(
                f"{p['phase']} 마감이 누적 고정 값과 다름: "
                f"{got.isoformat()} vs {expected.isoformat()}"
            )
        if p["reason"] == "timeout" and previous is not None:
            old = parse_time(previous["payload"]["phase_deadline_at"])
            old -= timedelta(
                seconds=shifted_before(b, frame["_at"]) - shifted_before(b, previous["_at"])
            )
            gap = (parse_time(p["phase_started_at"]) - old).total_seconds()
            if not -1 <= gap <= PHASE_CHANGE_SECONDS:
                out.append(f"마감 → phase.changed {gap:.1f}초 (≤ {PHASE_CHANGE_SECONDS})")
        previous = frame
    before = b.get("materials_before") or {}
    if not before.get("locked") or before.get("materials"):
        out.append("keyword 확정 전 자료가 잠겨 있지 않음")
    opened = frames(b, "materials.opened")
    keyword_confirm = next(
        (f for f in frames(b, "slot.confirmed") if f["payload"]["phase"] == "keyword"), None
    )
    if not opened:
        out.append("materials.opened 없음")
    elif keyword_confirm and b["frames"].index(opened[0]) < b["frames"].index(keyword_confirm):
        out.append("materials.opened가 keyword 확정보다 먼저 옴")
    pre = (b.get("states") or {}).get("pre_start") or {}
    if pre.get("session", {}).get("status") != "invited" or not pre.get("participants"):
        out.append("시작 전 /state에 invited 상태·참여자가 없음")
    return out


# ---- 2 턴 -------------------------------------------------------------------------------------


def _msg_by_id(b: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["id"]: m for m in messages(b)}


def assert_2_turns(b: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for run in b.get("runs") or []:
        speakers = (run.get("decision") or {}).get("speakers") or []
        if len(speakers) > 2:
            out.append(f"run {run['run_id']} 화자 {len(speakers)}명")
        if run["status"] in ("queued", "started"):
            out.append(f"run {run['run_id']} 상태가 {run['status']}로 남음")
    by_id = _msg_by_id(b)
    for frame in frames(b, "message.new"):
        p = frame["payload"]
        kind = (by_id.get(p["id"], {}).get("meta") or {}).get("kind")
        if p["sender"] in ("p1", "p2", "p3") and kind not in ("consensus_fill", "timeout_fill"):
            for start, stop in b["typing_intervals"]:
                if start + TYPING_GRACE_SECONDS <= frame["_at"] <= stop:
                    out.append(f"지원자 입력 중에 {p['sender']} 게시 ({p['id']})")
    for phase, slot in slots_by_phase(b).items():
        if slot["filled_by"] in ("p1", "p2", "p3") and slot["resolution"] == "consensus":
            top = max(
                (s for s in slot.get("support") or [] if s["option_id"] == slot["option_id"]),
                key=lambda s: len(s["supporters"]),
                default=None,
            )
            if not top or "candidate" not in top["supporters"] or len(top["supporters"]) < 3:
                out.append(f"{phase} AI 채우기인데 합의(지원자 포함 3/4)가 아님: {top}")
    critic = catalog.seat_of("critic")
    invites: dict[str, int] = {}
    for m in messages(b):
        if m.get("is_invite"):
            invites[m["phase"]] = invites.get(m["phase"], 0) + 1
            if m["sender"] != critic:
                out.append(f"침묵 초대를 {m['sender']}가 함")
    out += [f"{ph} 침묵 초대 {n}건" for ph, n in invites.items() if n > 1]
    out += _quiet_speech(b)
    for segment in _segments(b):
        seqs = [f["seq"] for f in segment]
        if any(nxt != cur + 1 for cur, nxt in zip(seqs, seqs[1:], strict=False)):
            out.append(f"WS seq 빠짐: {seqs}")
    return out


def _segments(b: dict[str, Any]) -> list[list[dict[str, Any]]]:
    starts = b["segments"] or [0]
    ends = [*starts[1:], len(b["frames"])]
    return [b["frames"][s:e] for s, e in zip(starts, ends, strict=True)]


def _quiet_speech(b: dict[str, Any]) -> list[str]:
    quiet = catalog.seat_of("quiet")
    names = {
        s["seat"]: s["display_name"]
        for s in (b.get("session") or {}).get("seats", [])
        if "seat" in s
    }
    out: list[str] = []
    last_candidate: dict[str, Any] | None = None
    for m in messages(b):
        if m["sender"] == "candidate":
            last_candidate = m
        if m["sender"] != quiet:
            continue
        kind = (m.get("meta") or {}).get("kind")
        if "inclusion_silence" in (m.get("card_ids") or []) or kind in (
            "consensus_fill",
            "timeout_fill",
        ):
            continue
        intent = (last_candidate or {}).get("intent") or {}
        called = quiet in (intent.get("addressed_to") or []) or (
            names.get(quiet) and names[quiet] in (last_candidate or {}).get("content", "")
        )
        whole_room = intent.get("intent") == "question" and not intent.get("addressed_to")
        if not (called or whole_room):
            out.append(f"소극형이 지명·전체 질문 없이 발언: {m['id']}")
    return out


# ---- 3 카드 -----------------------------------------------------------------------------------


def assert_3_cards(b: dict[str, Any]) -> list[str]:
    out: list[str] = []
    observations = b.get("observations") or []
    count: dict[str, int] = {}
    for o in observations:
        count[o["card_id"]] = count.get(o["card_id"], 0) + 1
    out += [f"{card} 관찰 {n}건 (카드당 1건)" for card, n in count.items() if n != 1]
    starts, ends = phase_starts(b), phase_ends(b)
    expected = b["script"].get("expect", {}).get("cards", [])
    by_card = {o["card_id"]: o for o in observations}
    for card_id in expected:
        o = by_card.get(card_id)
        if o is None:
            out.append(f"{card_id} 관찰 없음")
            continue
        phase = catalog.cards()[card_id]["stage_id"]
        offset = catalog.card_offset_min(card_id, b["minutes"])
        if offset is not None and phase in starts and o["t_min"] < starts[phase] + offset - 0.05:
            out.append(
                f"{card_id} 예정 시각 전에 발생: {o['t_min']} < {starts[phase] + offset:.2f}"
            )
        if not o["delivered"]:
            out.append(f"{card_id} 담당 화자 전원이 전달하지 않음")
    for card_id in catalog.cards():
        if card_id in by_card:
            continue
        phase = catalog.cards()[card_id]["stage_id"]
        offset = catalog.card_offset_min(card_id, b["minutes"]) or 0
        if phase in starts and phase in ends and ends[phase] > starts[phase] + offset + 0.05:
            out.append(f"{card_id} 미발생인데 단계가 예정 시각 뒤까지 이어짐")
    unfired = set(catalog.cards()) - set(by_card)
    report = b.get("draft")
    if report and unfired:
        core = {
            d["dimension_id"]: d
            for e in (b.get("rubric") or {}).get("elements", [])
            for d in e["dimensions"]
        }
        for dim in report["dimensions"]:
            cards = set(core.get(dim["dimension_id"], {}).get("core_cards") or [])
            if (
                cards
                and cards <= unfired
                and not (
                    dim["no_record_reason"] == "event_not_fired"
                    or "보조" in (dim.get("rationale") or "")
                )
            ):
                out.append(f"{dim['dimension_id']} 핵심 카드 미발생인데 사유·보조 장면 표시 없음")
    return out


# ---- 4 선점 금지 ------------------------------------------------------------------------------


def _first_quiet_invite(b: dict[str, Any], begin: float) -> float | None:
    """지원자가 소극형 자리를 처음 부른 메시지의 t_min(이름 '하람' 포함 또는 addressed_to)."""
    quiet = catalog.seat_of("quiet")
    times = [
        m["t_min"]
        for m in messages(b)
        if m["sender"] == "candidate"
        and m["t_min"] >= begin
        and (
            quiet in ((m.get("intent") or {}).get("addressed_to") or [])
            or "하람" in m.get("content", "")
        )
    ]
    return min(times, default=None)


def assert_4_preemption(b: dict[str, Any]) -> list[str]:
    out: list[str] = []
    starts, ends = phase_starts(b), phase_ends(b)
    obs = {o["card_id"]: o for o in b.get("observations") or []}
    for case in yaml.safe_load(PREEMPTION.read_text("utf-8"))["cases"]:
        card = catalog.cards()[case["card"]]
        seat = catalog.seat_of(case["speaker"])
        window = card["window"]
        if window["start"] == "card_message":
            if case["card"] not in obs:
                continue
            begin = obs[case["card"]]["t_min"]
        else:
            if card["stage_id"] not in starts:
                continue
            begin = starts[card["stage_id"]]
        end_phase = "proposal" if window["end"] == "submit" else window["end"].split(":", 1)[1]
        if end_phase not in ends:
            continue
        end = ends[end_phase]
        if case.get("until") == "candidate_invites_quiet":
            invited = _first_quiet_invite(b, begin)
            if invited is not None:
                end = min(end, invited)
        for pattern in case.get("must_not_regex") or []:
            regex = re.compile(pattern, re.IGNORECASE)
            for m in messages(b):
                if (
                    m["sender"] == seat
                    and begin <= m["t_min"] <= end
                    and regex.search(m["content"])
                ):
                    out.append(
                        f"{case['id']}: {seat} 발언이 금지 패턴 {pattern!r}에 걸림 ({m['id']})"
                    )
    return out


# ---- 5 리포트 ---------------------------------------------------------------------------------


def _record_refs(b: dict[str, Any]) -> set[tuple[str, str, float]]:
    return {(r["ref"]["type"], r["ref"]["id"], r["ref"]["t_min"]) for r in b.get("record") or []}


def _check_report(b: dict[str, Any], report: dict[str, Any], label: str) -> list[str]:
    out: list[str] = []
    rubric = b.get("rubric") or {}
    expected_ids = [d["dimension_id"] for e in rubric.get("elements", []) for d in e["dimensions"]]
    ids = [d["dimension_id"] for d in report["dimensions"]]
    if sorted(ids) != sorted(expected_ids) or len(ids) != len(set(ids)):
        out.append(f"{label}: 차원이 19개 한 행씩이 아님 ({len(ids)}행)")
    if sorted(e["element"] for e in report["elements"]) != sorted(
        e["element"] for e in rubric.get("elements", [])
    ):
        out.append(f"{label}: 요소 행이 5개 한 행씩이 아님")
    if TOTAL_FIELDS & set(report) or any(TOTAL_FIELDS & set(e) for e in report["elements"]):
        out.append(f"{label}: 총점·합계 필드가 있음")
    versions = (report.get("evaluation") or {}).get("prompt_versions") or {}
    want_versions = {"common", *(e["element"] for e in report["elements"])}
    if not want_versions <= set(versions):
        out.append(f"{label}: prompt_versions 누락 {sorted(want_versions - set(versions))}")
    if not FACT_KEYS <= set(report.get("facts") or {}):
        out.append(f"{label}: facts 지표 누락 {sorted(FACT_KEYS - set(report.get('facts') or {}))}")
    for row in report.get("contribution_check") or []:
        if set(row) != CONTRIBUTION_KEYS:
            out.append(f"{label}: contribution_check 형태 이상 {sorted(row)}")
    for element in report["elements"]:
        if FORBIDDEN.search(element.get("summary") or ""):
            out.append(f"{label}: {element['element']} summary 금지어")
        levels = [
            d["level"]
            for d in report["dimensions"]
            if d["element"] == element["element"] and d["level"] is not None
        ]
        if element["level"] != half_up_mean(levels):
            out.append(
                f"{label}: {element['element']} 수준 {element['level']} != "
                f"차원 평균 반올림 {half_up_mean(levels)}"
            )
    msgs = _msg_by_id(b)
    slots = slots_by_phase(b)
    sections = (b.get("proposal") or {}).get("sections") or {}
    option_ids = {o["option_id"] for o in (b.get("contributions") or {}).get("options", [])}
    records = _record_refs(b)
    for dim in report["dimensions"]:
        name = f"{label}: {dim['dimension_id']}"
        if FORBIDDEN.search(dim.get("rationale") or ""):
            out.append(f"{name} rationale 금지어")
        if dim["level"] is None:
            if dim.get("no_record_reason") not in NO_RECORD:
                out.append(f"{name} 기록 없음 사유 코드 이상: {dim.get('no_record_reason')}")
            if dim.get("confidence") is not None:
                out.append(f"{name} 기록 없음인데 confidence가 있음")
        else:
            if dim.get("confidence") not in ("high", "medium", "low"):
                out.append(f"{name} confidence 값 범위 밖: {dim.get('confidence')}")
            if dim.get("no_record_reason"):
                out.append(f"{name} level과 사유가 함께 있음")
        counted: set[str] = set()
        for ref in dim["evidence_refs"]:
            if not EVIDENCE_REF.match(ref):
                out.append(f"{name} 근거 형식 이상: {ref}")
                continue
            kind, _, key = ref.partition(":")
            if kind == "msg":
                if key not in msgs:
                    out.append(f"{name} 다른 세션/없는 메시지 {ref}")
                elif msgs[key]["sender"] == "candidate":
                    counted.add(ref)
            elif kind == "slot":
                if key not in slots:
                    out.append(f"{name} 없는 칸 {ref}")
                elif slots[key]["filled_by"] == "candidate":
                    counted.add(ref)
            elif kind == "proposal":
                if key not in sections:
                    out.append(f"{name} 없는 기획안 필드 {ref}")
                else:
                    counted.add(ref)
            elif kind == "contribution":
                if key not in option_ids and key not in {
                    r["phase"] for r in report.get("contribution_check") or []
                }:
                    out.append(f"{name} 없는 기여 행 {ref}")
                elif dim["dimension_id"] == "hon_credit_record":
                    counted.add(ref)
        if dim["level"] is not None:
            need = 2 if dim["level"] >= 4 else 1
            if len(counted) < need:
                out.append(
                    f"{name} 수준 {dim['level']}인데 개수에 드는 근거 {len(counted)}개 (≥ {need})"
                )
        if len(dim.get("evidence") or []) != len(dim["evidence_refs"]):
            out.append(f"{name} evidence와 evidence_refs 개수 다름")
        for item in dim.get("evidence") or []:
            ctx = item.get("context") or {}
            if len(ctx.get("before") or []) > 5 or len(ctx.get("after") or []) > 3:
                out.append(f"{name} 근거 맥락이 앞 5·뒤 3을 넘음")
            ref = item.get("record_ref") or {}
            if item.get("kind") in ("message", "slot") and b.get("record"):
                if (ref.get("type"), ref.get("id"), ref.get("t_min")) not in records:
                    out.append(f"{name} record_ref가 /record의 ref와 다름: {ref}")
    for dim_id, want in (b["script"].get("expect", {}).get("dimensions") or {}).items():
        dim = next((d for d in report["dimensions"] if d["dimension_id"] == dim_id), None)
        if dim is None:
            continue
        missing = [r for r in want.get("evidence", []) if r not in dim["evidence_refs"]]
        if missing:
            out.append(f"{label}: {dim_id} 근거에 {missing}가 없음")
        if "no_record_reason" in want and dim["no_record_reason"] != want["no_record_reason"]:
            out.append(
                f"{label}: {dim_id} 사유 {dim['no_record_reason']} != {want['no_record_reason']}"
            )
        if "level" in want and b["llm_fake"] and dim["level"] != want["level"]:
            out.append(f"{label}: {dim_id} 수준 {dim['level']} != {want['level']}")
    return out


def _contribution_expectations(b: dict[str, Any], report: dict[str, Any]) -> list[str]:
    out = []
    for phase, want in (b["script"].get("expect", {}).get("contribution") or {}).items():
        row = next((r for r in report.get("contribution_check") or [] if r["phase"] == phase), None)
        if row is None:
            out.append(f"contribution_check에 {phase} 행 없음")
            continue
        if row["first_proposer"] != want["first_proposer"]:
            out.append(f"{phase} 최초 제안자 {row['first_proposer']} != {want['first_proposer']}")
        credited = want["first_proposer"] in (row["credited_in_proposal"] or [])
        if credited != want["credited"]:
            out.append(f"{phase} 기획안 표기 {credited} != {want['credited']}")
    observed = {o["card_id"] for o in b.get("observations") or []}
    out += [
        f"{card} 관찰 없음"
        for card in b["script"].get("expect", {}).get("observations", [])
        if card not in observed
    ]
    return out


def _assistant_citations(b: dict[str, Any]) -> list[str]:
    """대본에 assistant step이 있으면 어시스턴트가 확정 키워드의 자료를 1개 이상 인용했는지."""
    if not any("assistant" in step for step in b["script"]["steps"]):
        return []
    calls = b.get("assistant_calls") or []
    if not calls:
        return ["assistant step이 있는데 assistant_calls가 없음"]
    keyword_materials = {
        m["material_id"]
        for m in ((b["states"].get("final") or {}).get("materials") or {}).get("materials", [])
    }
    cited = {m for c in calls for m in c.get("cited_material_ids") or []}
    if not cited:
        return ["어시스턴트가 자료를 하나도 인용하지 못함(rag 색인 버전 확인)"]
    outside = cited - keyword_materials
    return [f"인용한 자료가 확정 키워드의 자료가 아님: {sorted(outside)}"] if outside else []


def assert_5_report(b: dict[str, Any]) -> list[str]:
    if not b.get("draft"):
        return ["draft 리포트 없음"]
    out = _assistant_citations(b)
    out += _check_report(b, b["draft"], "draft")
    out += _contribution_expectations(b, b["draft"])
    if b.get("final"):
        out += _check_report(b, b["final"], "final")
    return out


# ---- 6 검토 -----------------------------------------------------------------------------------


def _content_part(report: dict[str, Any]) -> dict[str, Any]:
    """저장된 content만 남긴다(조회 때 api가 병합하는 조정·검토 필드 제외)."""
    elements = [{k: v for k, v in e.items() if k != "adjusted_level"} for e in report["elements"]]
    dims = [
        {
            k: v
            for k, v in d.items()
            if k not in ("adjusted_level", "adjusted_no_record_reason", "adjust_memo")
        }
        for d in report["dimensions"]
    ]
    keep = {
        k: report.get(k) for k in ("evaluation", "facts", "contribution_check", "slots", "cards")
    }
    keep["debrief_cards"] = (report.get("debrief") or {}).get("cards")
    return {**keep, "elements": elements, "dimensions": dims}


def assert_6_review(b: dict[str, Any]) -> list[str]:
    review = b.get("review") or {}
    out = []
    for key, want in (
        ("no_memo", 422),
        ("no_record_without_reason", 422),
        ("approve", 200),
        ("readjust", 409),
        ("same_body", 200),
        ("notes", 200),
    ):
        if review.get(key) != want:
            out.append(f"검토 {key}: {review.get(key)} (기대 {want})")
    merged, final, draft = b.get("report"), b.get("final"), b.get("draft")
    if not (merged and final and draft):
        return [*out, "final 리포트 없음"]
    dim = next(
        (d for d in merged["dimensions"] if d["dimension_id"] == review["dimension_id"]), None
    )
    spec = b["script"]["review"]["adjust"]
    if not dim or dim["adjusted_level"] != spec["level"] or dim["adjust_memo"] != spec["memo"]:
        out.append("조회 시 adjusted_level·adjust_memo 병합이 조정과 다름")
    elif dim:
        element = next(e for e in merged["elements"] if e["element"] == dim["element"])
        levels = [
            d["adjusted_level"] if d["adjusted_level"] is not None else d["level"]
            for d in merged["dimensions"]
            if d["element"] == dim["element"]
        ]
        if element["adjusted_level"] != half_up_mean([v for v in levels if v is not None]):
            out.append(
                f"요소 수준 재계산 {element['adjusted_level']} != "
                f"{half_up_mean([v for v in levels if v is not None])}"
            )
    if _content_part(draft) != _content_part(final):
        out.append("draft와 final의 content가 다름(해시 불일치)")
    if (draft.get("evaluation") or {}).get("run_id") != (final.get("evaluation") or {}).get(
        "run_id"
    ):
        out.append("final이 draft와 다른 평가 실행(같은 thread 재개가 아님)")
    return out


# ---- 7 timeout_all ---------------------------------------------------------------------------


def assert_7_timeout(b: dict[str, Any]) -> list[str]:
    out = []
    slots = slots_by_phase(b)
    for phase in catalog.PHASES[:3]:
        slot = slots.get(phase) or {}
        if slot.get("resolution") != "timeout" or slot.get("filled_by") not in ("p1", "p2", "p3"):
            out.append(
                f"{phase} 칸이 timeout/자리 id가 아님: "
                f"{slot.get('resolution')}/{slot.get('filled_by')}"
            )
    proposal = b.get("proposal") or {}
    if not proposal.get("auto_submitted"):
        out.append("자동 제출(auto_submitted)이 아님")
    for phase in catalog.PHASES[:3]:
        if (proposal.get("sections") or {}).get(phase) != (slots.get(phase) or {}).get("value"):
            out.append(f"기획안 {phase} 칸이 확정 값과 다름")
    ended = frames(b, "session.ended")
    if not ended or ended[0]["payload"]["reason"] != "timeout":
        out.append("session.ended reason이 timeout이 아님")
    if not b.get("draft"):
        out.append("자동 제출 뒤 리포트 생성 안 됨")
    return out


# ---- 9 비용·지연 -----------------------------------------------------------------------------


def measure_9(b: dict[str, Any]) -> dict[str, Any]:
    delays = []
    acks = frames(b, "message.ack")
    for ack in acks:
        nxt = next(
            (
                f
                for f in b["frames"][b["frames"].index(ack) + 1 :]
                if f["type"] == "message.new" and f["payload"]["sender"] in ("p1", "p2", "p3")
            ),
            None,
        )
        if nxt:
            delays.append(nxt["_at"] - ack["_at"])
    intent = [ms / 1000 for ms in b.get("intent_latency_ms") or []]
    cost = (b.get("costs") or {}).get("cost_usd")
    return {
        "cost_usd": cost,
        "cost_target": COST_TARGET_USD,
        "first_persona_p95_sec": percentile(delays, 95),
        "first_persona_target": LATENCY_TARGET_SEC,
        "intent_p95_sec": percentile(intent, 95),
        "intent_target": INTENT_TARGET_SEC,
    }


def assert_9_cost_latency(b: dict[str, Any]) -> list[str]:
    info = measure_9(b)
    b["measure"] = info
    out = []
    if b.get("costs") is not None and b.get("runs") and info["cost_usd"] is None:
        out.append("세션 비용(sessions.cost_usd)을 읽지 못함")
    if info["first_persona_p95_sec"] is None:
        out.append("지원자 메시지 → 첫 페르소나 게시 지연을 잴 수 없음")
    return out


# ---- R restart --------------------------------------------------------------------------------


def assert_r_restart(b: dict[str, Any]) -> list[str]:
    out = []
    limit = b["script"].get("expect", {}).get("restart", {}).get("reconnect_within_seconds", 90)
    if not b["restarts"]:
        return ["restart step이 실행되지 않음"]
    for r in b["restarts"]:
        if not r["reconnected"] or r["seconds"] > limit:
            out.append(f"재시작 뒤 {limit}초 안에 재연결 못 함 ({r['seconds']}초)")
        lost = set(r["ids_before"]) - set(r["ids_after"])
        if lost:
            out.append(f"재시작 뒤 /state에서 메시지 {sorted(lost)}가 사라짐")
    if not frames(b, "session.ended"):
        out.append("재시작 뒤 세션이 끝까지 가지 못함")
    seen = set()
    for m in messages(b):
        key = (m["sender"], m["content"], m["phase"], m.get("t_min"))
        if key in seen:
            out.append(f"중복 메시지: {m['id']}")
        seen.add(key)
    labels = set()
    for o in (b.get("contributions") or {}).get("options", []):
        key = (o["phase"], o["label"])
        if key in labels:
            out.append(f"중복 안: {key}")
        labels.add(key)
    return out


# ---- 진입점 -----------------------------------------------------------------------------------

NAMES = {
    "0": "러너 step",
    "1": "단계",
    "2": "턴",
    "3": "카드",
    "4": "선점 금지",
    "5": "리포트",
    "6": "검토",
    "7": "timeout_all",
    "9": "비용·지연",
    "R": "restart",
}


def check(b: dict[str, Any]) -> list[dict[str, Any]]:
    plan: list[tuple[str, Callable[[dict[str, Any]], list[str]]]] = [
        ("0", assert_0_steps),
        ("1", assert_1_phases),
        ("2", assert_2_turns),
        ("3", assert_3_cards),
        ("4", assert_4_preemption),
        ("5", assert_5_report),
    ]
    if "review" in b["script"]:
        plan.append(("6", assert_6_review))
    if b["name"] == "timeout_all":
        plan.append(("7", assert_7_timeout))
    plan.append(("9", assert_9_cost_latency))
    if b["name"] == "restart":
        plan.append(("R", assert_r_restart))
    results = []
    for number, fn in plan:
        try:
            failures = fn(b)
        except Exception as error:  # noqa: BLE001 - 단언 코드 오류도 실패로 남긴다
            failures = [f"단언 실행 오류: {error!r}"]
        results.append(
            {"id": number, "name": NAMES[number], "ok": not failures, "failures": failures}
        )
    return results


def passed(results: list[dict[str, Any]]) -> bool:
    return all(r["ok"] for r in results)
