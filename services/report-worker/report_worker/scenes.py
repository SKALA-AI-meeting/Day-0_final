"""차원별 장면 구간과 그 안의 지원자 메시지(결정 A1). 기록과 척도만으로 계산하는 순수 함수.

정본은 `docs/product/rubric.md` 2.1·2.3·7.3이다.

- 핵심 카드가 없는 차원은 세션 전체가 장면이다.
- 핵심 카드가 있는 차원은 담당 동료 전원이 전달한(`delivered`) 핵심 카드의 관찰 창
  (`observations[].window`)이 장면 구간이다. 하나도 없으면 장면이 없다(핵심 카드 미발생).
- `decision_timing`이 있는 차원은 핵심 카드 단계의 칸 결정 시각에서 구간을 자른다: `before`는
  결정 시각까지, `after`는 결정 뒤만. 칸이 정해지지 않았으면 `after` 구간은 없다.

평가 프롬프트의 `[사실]` 블록과 워커 검증(`evaluate.check_dimension`)이 같은 값을 쓴다.
"""

from dataclasses import dataclass, field
from typing import Any

PATHS = {
    "candidate_confirm": "지원자 확정",
    "ai_consensus_fill": "AI 동료 합의 채움",
    "timeout": "시간 초과",
}
PHASE_NAMES = {"keyword": "키워드", "problem": "문제 상황", "solution": "해결책"}


@dataclass(frozen=True)
class Decision:
    phase: str
    t_min: float
    path: str

    def describe(self) -> str:
        name = PHASE_NAMES.get(self.phase, self.phase)
        return f"{name} 칸 결정({self.t_min}분, {PATHS.get(self.path, self.path)})"


@dataclass(frozen=True)
class Scene:
    dimension_id: str
    session_wide: bool
    windows: tuple[tuple[float, float], ...]
    cards: tuple[str, ...]  # 발동(전원 전달)한 핵심 카드
    timing: str | None  # before ∣ after ∣ None
    decision: Decision | None  # timing이 있을 때 핵심 카드 단계의 결정
    candidate_ids: tuple[str, ...] = field(default=())

    @property
    def exists(self) -> bool:
        return self.session_wide or bool(self.windows)

    def contains(self, t_min: float) -> bool:
        if self.session_wide:
            return True
        return any(self._inside(t_min, start, end) for start, end in self.windows)

    def _inside(self, t: float, start: float, end: float) -> bool:
        if not start <= t <= end:
            return False
        if self.decision is None:
            return True
        # 결정과 같은 시각의 메시지는 결정 전으로 본다(사실 decided_before와 같은 기준)
        if self.timing == "after":
            return t > self.decision.t_min
        if self.timing == "before":
            return t <= self.decision.t_min
        return True

    def describe(self) -> str:
        if self.session_wide:
            return "세션 전체"
        if not self.windows:
            return "장면 없음"
        spans = ", ".join(f"{s}~{e}분" for s, e in self.windows)
        cut = ""
        if self.decision is not None:
            side = "뒤" if self.timing == "after" else "전"
            cut = f", {self.decision.describe()} {side}"
        return f"{spans}({'·'.join(self.cards)}{cut})"


def decisions(record: dict[str, Any]) -> dict[str, Decision]:
    """확정된 칸의 결정. 경로는 resolution·filled_by로 정한다(api `facts.decision_path`와
    같은 규칙)."""
    out = {}
    for slot in record.get("slots", []):
        if slot.get("t_min") is None or not slot.get("filled_by"):
            continue
        if slot.get("resolution") == "timeout":
            path = "timeout"
        elif slot.get("filled_by") == "candidate":
            path = "candidate_confirm"
        else:
            path = "ai_consensus_fill"
        out[slot["phase"]] = Decision(slot["phase"], slot["t_min"], path)
    return out


def build_scenes(record: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Scene]:
    """척도의 모든 차원에 대한 장면."""
    stage_of = {c["card_id"]: c.get("stage_id") for c in rubric.get("cards", [])}
    fired = {
        o["card_id"]: o["window"]
        for o in record.get("observations", [])
        if o.get("delivered") and o.get("window")
    }
    decided = decisions(record)
    candidate = [m for m in record.get("messages", []) if m.get("sender") == "candidate"]
    scenes = {}
    for element in rubric.get("elements", []):
        for dimension in element.get("dimensions", []):
            core = list(dimension.get("core_cards") or [])
            timing = dimension.get("decision_timing")
            decision = None
            if timing and core:
                decision = decided.get(stage_of.get(core[0]) or "")
            cards = tuple(c for c in core if c in fired)
            windows = []
            for card in cards:
                start, end = fired[card]["start_min"], fired[card]["end_min"]
                if timing == "after":
                    if decision is None:
                        continue
                    start = max(start, decision.t_min)
                elif timing == "before" and decision is not None:
                    end = min(end, decision.t_min)
                if start <= end:
                    windows.append((start, end))
            scene = Scene(
                dimension_id=dimension["dimension_id"],
                session_wide=not core,
                windows=tuple(windows),
                cards=cards,
                timing=timing,
                decision=decision,
            )
            ids = tuple(m["id"] for m in candidate if scene.exists and scene.contains(m["t_min"]))
            scenes[dimension["dimension_id"]] = Scene(**{**scene.__dict__, "candidate_ids": ids})
    return scenes


def after_any_decision(record_decisions: dict[str, Decision], t_min: float) -> bool:
    return any(d.t_min < t_min for d in record_decisions.values())


def facts_block(
    element_rubric: dict[str, Any], scenes: dict[str, Scene], record: dict[str, Any]
) -> dict[str, Any]:
    """평가 프롬프트 `[사실]` 블록: 결정 기록과 이 요소 차원별 장면 구간·구간 안 지원자 메시지."""
    facts = record.get("facts") or {}
    rows = []
    for dimension in element_rubric["dimensions"]:
        scene = scenes[dimension["dimension_id"]]
        rows.append(
            {
                "dimension_id": scene.dimension_id,
                "scene": "session"
                if scene.session_wide
                else ("cards" if scene.windows else "none"),
                "windows": [{"start_min": s, "end_min": e} for s, e in scene.windows],
                "fired_core_cards": list(scene.cards),
                "decision_cut": None
                if scene.decision is None
                else {
                    "timing": scene.timing,
                    "phase": scene.decision.phase,
                    "t_min": scene.decision.t_min,
                },
                "candidate_messages": [f"msg:{i}" for i in scene.candidate_ids],
                "candidate_message_count": len(scene.candidate_ids),
            }
        )
    recorded = facts.get("decisions")
    if recorded is None:  # 사실이 없는 기록(픽스처 등): 칸 기록에서 시각·경로만
        recorded = [
            {"phase": d.phase, "t_min": d.t_min, "path": d.path} for d in decisions(record).values()
        ]
    return {"decisions": recorded, "dimensions": rows}
