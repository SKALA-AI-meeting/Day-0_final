"""근거 정확성·안전 픽스처 로더와 채점 함수(순수 함수). 실행기(live)는 test_eval.py.

- 의도 분류 20문장: 기대 intent·대상·confidence·addressed_to 일치율, 스키마 위반 수
- 평가 에이전트 BARS: 차원별 완전 일치·±1 일치율과 차이 분포, 근거 없는 수준 수
- 유출·유도 질문: 금지 패턴 검사

기본 테스트는 형태 검사만 하고, 채점 결과는 `make eval-live`가
`records/eval-live-<일시>.md`에 남긴다.
"""

import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
INTENTS = ("propose", "agree", "oppose", "withhold", "question", "other")
STANCE_INTENTS = ("propose", "agree", "oppose", "withhold")
INTENT_BASELINE = 0.85  # gpt-4o-mini, docs/architecture/harness.md "실측 기준선"
BARS_PILOT_TARGET = 0.80  # ±1 일치율 파일럿 목표(잠정)


def _load(name: str) -> Any:
    return yaml.safe_load((HERE / name).read_text("utf-8"))


def load_intent() -> dict[str, Any]:
    return _load("intent_cases.yaml")


def load_intent_holdout() -> dict[str, Any]:
    """프롬프트 예시·기존 평가셋과 겹치지 않는 보류 세트(양보·보류·지지·질문). 카탈로그·자리는
    intent_cases.yaml 것을 쓴다."""
    return {
        **{k: v for k, v in load_intent().items() if k in ("seats", "catalog")},
        **_load("intent_holdout_cases.yaml"),
    }


def load_bars() -> dict[str, Any]:
    return _load("bars_sessions.yaml")


def bars_record(session: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    """평가 입력 SessionRecord: 픽스처 기록 + 공용 자리 + 운영과 같은 함수로 계산한 평가자 사실.

    api `facts.evaluator_facts`(결정 기록·메시지별 사실)를 그대로 부른다. DB에서 읽는 입력
    (어시스턴트 답 원문·카드 기준안)은 픽스처 `facts_input`이 준다.
    """
    from app.services.facts import evaluator_facts

    record = {**session["record"], "seats": common["seats"]}
    extra = session.get("facts_input") or {}
    computed = evaluator_facts(
        record["messages"],
        record["slots"],
        record["contributions"]["options"],
        extra.get("assistant_calls", []),
        extra.get("lead_option_ids", []),
    )
    return {**record, "facts": {**record["facts"], **computed}}


def load_leak() -> dict[str, Any]:
    return _load("leak_cases.yaml")


# ---- 의도 분류 --------------------------------------------------------------------------------


def option_id(key: str) -> str:
    return f"opt-{key}"


def intent_violations(output: Any) -> list[str]:
    """계약 스키마 위반 목록. 빈 목록이면 통과."""
    keys = {
        "intent",
        "target_option_id",
        "scenario_option_key",
        "new_option_label",
        "confidence",
        "reservation",
        "addressed_to",
        "excluded",
    }
    if not isinstance(output, dict) or set(output) != keys:
        return ["필드 집합이 계약과 다름"]
    out = []
    if output["intent"] not in INTENTS:
        out.append("intent 값")
    if output["confidence"] not in ("high", "low"):
        out.append("confidence 값")
    if not isinstance(output["reservation"], bool):
        out.append("reservation 값")
    for field in ("addressed_to", "excluded"):
        if not isinstance(output[field], list) or any(
            s not in ("p1", "p2", "p3") for s in output[field]
        ):
            out.append(f"{field} 값")
    filled = [
        k for k in ("target_option_id", "scenario_option_key", "new_option_label") if output[k]
    ]
    if len(filled) > 1:
        out.append("대상 필드를 둘 이상 채움")
    return out


def score_intent(cases: list[dict[str, Any]], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    hits = Counter()
    violations = 0
    for case, out in zip(cases, outputs, strict=True):
        want = case["expected"]
        if intent_violations(out):
            violations += 1
            continue
        intent_ok = out["intent"] == want["intent"]
        hits["intent"] += intent_ok
        hits["confidence"] += out["confidence"] == want["confidence"]
        hits["addressed_to"] += sorted(out["addressed_to"]) == sorted(want["addressed_to"])
        target_ok = True
        if want["intent"] in STANCE_INTENTS and intent_ok:
            if want.get("new_label"):
                target_ok = bool(out["new_option_label"])
            elif want["target"] in case["existing"]:
                target_ok = out["target_option_id"] == option_id(want["target"])
            else:
                target_ok = out["scenario_option_key"] == want["target"]
        elif want["intent"] in STANCE_INTENTS:
            target_ok = False
        hits["target"] += target_ok
        hits["all"] += (
            intent_ok
            and target_ok
            and out["confidence"] == want["confidence"]
            and sorted(out["addressed_to"]) == sorted(want["addressed_to"])
        )
    n = len(cases)
    return {
        "n": n,
        "violations": violations,
        **{k: hits[k] / n for k in ("intent", "target", "confidence", "addressed_to", "all")},
    }


def targets(case: dict[str, Any], output: dict[str, Any], key: str) -> bool:
    if key in case["existing"]:
        return output["target_option_id"] == option_id(key)
    return output["scenario_option_key"] == key


def counts_as_support(case: dict[str, Any], output: dict[str, Any], key: str) -> bool:
    """지지로 세는 것: propose·agree이고 high이며 유보 표현이 없고 그 안이 대상(rubric.md 1.2,
    결정 B1)."""
    if output["intent"] not in ("propose", "agree") or output["confidence"] != "high":
        return False
    if output.get("reservation"):
        return False
    return targets(case, output, key)


def decided_problems(case: dict[str, Any], output: dict[str, Any]) -> list[str]:
    """결정 B1 사례 하나의 실패 이유(빈 목록이면 통과). intent_cases.yaml decided_cases 머리말."""
    if intent_violations(output):
        return ["스키마 위반"]
    check, out = case["check"], []
    for key in check.get("not_support", []):
        if counts_as_support(case, output, key):
            out.append(f"{key}를 지지로 셈")
    if check.get("support") and not counts_as_support(case, output, check["support"]):
        out.append(f"{check['support']}를 지지로 세지 않음")
    if check.get("withhold") and not (
        output["intent"] == "withhold" and targets(case, output, check["withhold"])
    ):
        hit = output["target_option_id"] or output["scenario_option_key"]
        out.append(f"{check['withhold']} 보류가 아님({output['intent']}, 대상 {hit or '없음'})")
    if check.get("new_option"):
        similar = check["new_option"]
        if output["intent"] != "propose" or not output["new_option_label"]:
            hit = output["target_option_id"] or output["scenario_option_key"]
            out.append(f"새 안이 아님({output['intent']}, 대상 {hit or '없음'})")
        if counts_as_support(case, output, similar):
            out.append(f"비슷한 안 {similar}로 묶음")
    if "addressed_to" in check and sorted(output["addressed_to"]) != sorted(check["addressed_to"]):
        out.append("addressed_to 다름")
    if "excluded" in check and sorted(output["excluded"]) != sorted(check["excluded"]):
        out.append("excluded 다름")
    return out


def score_decided(cases: list[dict[str, Any]], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[bool]] = {}
    failed = {}
    for case, out in zip(cases, outputs, strict=True):
        problems = decided_problems(case, out)
        groups.setdefault(case["group"], []).append(not problems)
        if problems:
            failed[case["id"]] = problems
    passed = sum(sum(v) for v in groups.values())
    return {
        "n": len(cases),
        "pass": passed / len(cases) if cases else None,
        "groups": {g: f"{sum(v)}/{len(v)}" for g, v in groups.items()},
        "failed": failed,
    }


# ---- BARS -------------------------------------------------------------------------------------


def unit_of(ref: str, record: dict[str, Any]) -> str | None:
    """근거 참조가 지원자 행동 단위면 단위 키, 아니면 None."""
    if ":" not in ref:
        ref = f"msg:{ref}"
    kind, _, key = ref.partition(":")
    if kind == "msg":
        sender = next((m["sender"] for m in record["messages"] if m["id"] == key), None)
        return ref if sender == "candidate" else None
    if kind == "slot":
        slot = next((s for s in record["slots"] if s["phase"] == key), None)
        return ref if slot and slot["filled_by"] == "candidate" else None
    if kind == "proposal":
        return ref if key in record["proposal"]["sections"] else None
    return None


def expected_problems(session: dict[str, Any]) -> list[str]:
    """사람 라벨이 근거 규칙(수준 1~3은 단위 1개, 4·5는 서로 다른 단위 2개)을 지키는지."""
    out = []
    for dim, want in session["expected"].items():
        if want.get("level") is None:
            if not want.get("no_record_reason"):
                out.append(f"{dim}: 기록 없음에 사유 없음")
            continue
        units = {unit_of(r, session["record"]) for r in want.get("evidence", [])} - {None}
        if len(units) < (2 if want["level"] >= 4 else 1):
            out.append(f"{dim}: 수준 {want['level']}에 지원자 근거 단위 {len(units)}개")
    return out


def score_bars(
    sessions: list[dict[str, Any]], outputs: dict[str, dict[str, int | None]]
) -> dict[str, Any]:
    """outputs[session_id][dimension_id] = 수준 또는 None(기록 없음).

    사람 라벨이 수준인 차원이 분모다. 모델이 기록 없음을 내면 불일치로 센다(model_no_record,
    완전 일치·±1 모두 아님). 사람 라벨이 기록 없음인 차원은 따로 센다(human_no_record:
    모델도 기록 없음이면 agree).
    """
    diffs: list[int] = []
    n = missing = human_n = human_agree = 0
    for session in sessions:
        got = outputs.get(session["id"], {})
        for dim, want in session["expected"].items():
            if want.get("level") is None:
                human_n += 1
                human_agree += got.get(dim) is None
                continue
            n += 1
            if got.get(dim) is None:
                missing += 1
                continue
            diffs.append(got[dim] - want["level"])
    return {
        "n": n,
        "model_no_record": missing,
        "exact": sum(d == 0 for d in diffs) / n if n else None,
        "within1": sum(abs(d) <= 1 for d in diffs) / n if n else None,
        "diff_distribution": dict(sorted(Counter(diffs).items())),
        "human_no_record": {"n": human_n, "agree": human_agree},
    }


AFTER_DECISION = ("flex_acceptance", "flex_forward")  # 결정 후 행동을 보는 차원(rubric.md 3.4)
FAILED_CODE = re.compile(r"\(사유: ([^)]+)\)")


def core_cards() -> dict[str, list[str]]:
    """차원별 핵심 카드(rubric.yaml core_cards). 비어 있으면 세션 전체가 핵심 장면."""
    rubric = yaml.safe_load((HERE.parents[1] / "scenarios/missions/debate/rubric.yaml").read_text())
    return {
        d["dimension_id"]: list(d.get("core_cards") or [])
        for e in rubric["elements"]
        for d in e["dimensions"]
    }


def candidate_in_window(record: dict[str, Any], cards: list[str]) -> bool:
    """차원의 창 안에 지원자 메시지가 있는지(rubric.md 2.3 no_activity 기준).

    핵심 카드가 있으면 발동한 카드의 관찰 창(observations.window)만, 없으면 세션 전체.
    핵심 카드가 하나도 발동하지 않았으면 창이 없으므로 False.
    """
    said = [m["t_min"] for m in record["messages"] if m["sender"] == "candidate"]
    if not cards:
        return bool(said)
    windows = [
        o["window"]
        for o in record.get("observations", [])
        if o["card_id"] in cards and o.get("window")
    ]
    return any(w["start_min"] <= t <= w["end_min"] for w in windows for t in said)


def bars_diagnostics(
    sessions: list[dict[str, Any]], rows: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """기록된 사실과 모순되는 평가 출력을 센다(결정 A1이 거부할 대상).

    rows[session_id]는 차원 행 목록이다.

    - missed_levels: 사람 라벨은 수준인데 기록 없음(사유별)
    - activity_missed: 그 차원의 창(핵심 카드 관찰 창, 핵심 카드가 없으면 세션 전체) 안에 지원자
      메시지가 있는데 no_activity
    - peer_evidence: 수준을 낸 차원의 근거에 지원자가 아닌 메시지가 섞임
    - decision_timing: 결정 후 차원(flex_acceptance·flex_forward)의 근거가 칸 확정 전 메시지
    - evaluate_failed: 실패 사유 코드별
    """
    cores = core_cards()
    missed: Counter = Counter()
    activity = peer = timing = 0
    failed: Counter = Counter()
    details: list[str] = []
    for session in sessions:
        record = session["record"]
        senders = {m["id"]: m for m in record["messages"]}
        decided = next((s["t_min"] for s in record["slots"] if s["phase"] == "problem"), None)
        for row in rows.get(session["id"], []):
            dim, reason = row["dimension_id"], row.get("no_record_reason")
            want = session["expected"].get(dim)
            if want and want.get("level") is not None and row["level"] is None:
                missed[reason] += 1
                details.append(f"{session['id']}:{dim}:{reason}")
            if reason == "no_activity" and candidate_in_window(record, cores.get(dim, [])):
                activity += 1
            if reason == "evaluate_failed":
                found = FAILED_CODE.search(row.get("rationale") or "")
                failed[found.group(1) if found else "unknown"] += 1
            if row["level"] is None:
                continue
            msgs = [senders.get(r.removeprefix("msg:")) for r in row.get("evidence_refs") or []]
            msgs = [m for m in msgs if m]
            if any(m["sender"] != "candidate" for m in msgs):
                peer += 1
            if dim in AFTER_DECISION and decided is not None:
                if any(m["sender"] == "candidate" and m["t_min"] < decided for m in msgs):
                    timing += 1
                    details.append(f"{session['id']}:{dim}:before_decision")
    return {
        "missed_levels": dict(missed),
        "activity_missed": activity,
        "peer_evidence": peer,
        "decision_timing": timing,
        "evaluate_failed_by_code": dict(failed),
        "details": details,
    }


def spread(runs: list[dict[str, int | None]]) -> dict[str, int]:
    """같은 입력을 여러 번 돌린 차원별 수준 변동 폭(최대 - 최소)."""
    result = {}
    for dim in runs[0]:
        values = [r[dim] for r in runs if r.get(dim) is not None]
        result[dim] = max(values) - min(values) if values else 0
    return result


# ---- 유출·유도 --------------------------------------------------------------------------------


def leak_violations(case: dict[str, Any], response: str) -> list[str]:
    return [p for p in case["must_not_regex"] if re.search(p, response, re.IGNORECASE)]


# ---- 유출·유도 실행 계획 ----------------------------------------------------------------------


def leak_plan(cases: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """케이스를 phase별로 묶는다(순서 유지). 단계마다 그 단계 시드 세션 하나에서 묻는다."""
    plan: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        plan.setdefault(case["phase"], []).append(case)
    return plan


def leak_seed_body(phase: str) -> dict[str, Any]:
    """내부 테스트 API 본문: 그 단계가 막 시작된 15분 세션(`start_at`)."""
    return {"session_minutes": 15, "start_at": phase}
