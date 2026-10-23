"""차원별 장면(핵심·보조)과 그 안의 지원자 메시지(결정 A1). 기록과 척도만으로 계산하는 순수 함수.

정본은 `docs/product/rubric.md` 2.1·2.5·7.3이다. 장면에 드는지는 반올림한 분이 아니라 메시지의
저장 순서와 단계(`messages.phase`)로 정한다. 칸 확정은 단계를 넘기므로 어떤 칸의 결정 뒤 메시지는
그 단계보다 뒤 단계의 메시지다.

- 핵심 장면(`core`): 핵심 카드가 없으면 세션 전체. 있으면 담당 동료 전원이 전달한(`delivered`)
  핵심 카드의 관찰 창(카드 `window` 규칙: 시작 = 단계 시작 ∣ 카드 첫 발언, 끝 = 그 단계 끝 ∣
  제출). 하나도 없으면 핵심 장면이 없다(핵심 카드 미발생).
- `decision_timing`이 있으면 핵심 카드 단계의 칸 결정에서 자른다: `before`는 그 단계 메시지까지,
  `after`는 그 칸이 정해진 뒤(다음 단계부터)만. 칸이 정해지지 않았으면 `after` 핵심 장면은 없다.
  척도 복사본에 이 값이 없으면(이전 채용 건) 자르지 않는다.
- 보조 장면: 척도 `aux_scenes`(관찰 장면 표의 보조 행을 단계·카드 창으로 옮긴 데이터).

평가 프롬프트의 `[사실]` 블록과 워커 검증(`evaluate.check_facts`)이 같은 값을 쓴다.
"""

from dataclasses import dataclass, replace
from typing import Any

PHASES = ("keyword", "problem", "solution", "proposal")
ORDER = {p: i for i, p in enumerate(PHASES)}
CORE = "core"
PATHS = {
    "candidate_confirm": "지원자 확정",
    "ai_consensus_fill": "AI 동료 합의 채움",
    "timeout": "시간 초과",
}
PHASE_NAMES = {
    "keyword": "키워드",
    "problem": "문제 상황",
    "solution": "해결책",
    "proposal": "기획안",
}


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
    scene_id: str
    label: str
    exists: bool
    members: frozenset[str]  # 장면에 드는 메시지 id(보낸 자리 무관)
    candidate_ids: tuple[str, ...]  # 그중 지원자 메시지(저장 순서)
    fields: frozenset[str] = frozenset()  # 이 장면 근거로 달 수 있는 기획안 필드

    def contains(self, message_id: str) -> bool:
        return message_id in self.members


@dataclass(frozen=True)
class DimensionScenes:
    dimension_id: str
    session_wide: bool
    cards: tuple[str, ...]  # 발동(전원 전달)한 핵심 카드
    timing: str | None  # before ∣ after ∣ None
    decision: Decision | None  # timing이 있을 때 핵심 카드 단계의 결정
    core: Scene
    aux: tuple[Scene, ...]

    def get(self, scene_id: str) -> Scene | None:
        if scene_id == CORE:
            return self.core
        return next((s for s in self.aux if s.scene_id == scene_id), None)

    @property
    def ids(self) -> list[str]:
        return [CORE, *(s.scene_id for s in self.aux)]

    def holding(self, message_id: str) -> list[str]:
        """이 메시지가 드는 장면 id(핵심 먼저)."""
        scenes = [self.core, *self.aux]
        return [s.scene_id for s in scenes if s.exists and s.contains(message_id)]

    def is_before_decision(self, phase: str) -> bool:
        """결정 뒤를 보는 차원에서 이 단계 메시지가 결정 전인지."""
        return (
            self.timing == "after"
            and self.decision is not None
            and ORDER.get(phase, 0) <= ORDER[self.decision.phase]
        )


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


def _card_members(
    messages: list[dict[str, Any]], observation: dict[str, Any], rule: dict[str, Any] | None
) -> set[str]:
    """카드 관찰 창(cards.yaml `window`)에 드는 메시지. 저장 순서와 단계로만 가른다."""
    rule = rule or {}
    stage = observation["phase"]
    start = 0
    if rule.get("start") == "card_message":
        index = next(
            (i for i, m in enumerate(messages) if m["id"] == observation.get("trigger_message_id")),
            None,
        )
        if index is not None:
            start = index
        else:  # 첫 발언 기록이 없으면 단계 시작부터
            start = next((i for i, m in enumerate(messages) if m["phase"] == stage), len(messages))
    end_rule = str(rule.get("end") or f"stage_end:{stage}")
    end = "proposal" if end_rule == "submit" else end_rule.removeprefix("stage_end:")
    return {
        m["id"]
        for i, m in enumerate(messages)
        if i >= start and ORDER[stage] <= ORDER.get(m["phase"], 0) <= ORDER.get(end, 3)
    }


def _scene(
    scene_id: str, label: str, exists: bool, members: set[str], messages: list[dict[str, Any]]
) -> Scene:
    ids = tuple(m["id"] for m in messages if exists and m["id"] in members)
    candidates = tuple(m["id"] for m in messages if m["sender"] == "candidate" and m["id"] in ids)
    return Scene(scene_id, label, exists, frozenset(ids), candidates)


def build_scenes(record: dict[str, Any], rubric: dict[str, Any]) -> dict[str, DimensionScenes]:
    """척도의 모든 차원에 대한 핵심·보조 장면."""
    messages = record.get("messages", [])
    rules = {c["card_id"]: c for c in rubric.get("cards", [])}
    fired = {
        o["card_id"]: _card_members(messages, o, (rules.get(o["card_id"]) or {}).get("window"))
        for o in record.get("observations", [])
        if o.get("delivered")
    }
    decided = decisions(record)
    everything = {m["id"] for m in messages}
    out = {}
    for element in rubric.get("elements", []):
        for dimension in element.get("dimensions", []):
            dimension_id = dimension["dimension_id"]
            core_cards = list(dimension.get("core_cards") or [])
            timing = dimension.get("decision_timing")
            stage = (rules.get(core_cards[0]) or {}).get("stage_id") if core_cards else None
            decision = decided.get(stage or "") if timing else None
            cards = tuple(c for c in core_cards if c in fired)
            if not core_cards:
                members, exists, label = everything, True, "세션 전체"
            else:
                members = set().union(*(fired[c] for c in cards)) if cards else set()
                label = "·".join(
                    f"{c} 카드 창({(rules.get(c) or {}).get('window', {}).get('label', '')})"
                    for c in core_cards
                )
                if timing == "before" and decision is not None:
                    members = {
                        i for i in members if ORDER[_phase(messages, i)] <= ORDER[decision.phase]
                    }
                    label += f", {decision.describe()}까지"
                elif timing == "after":
                    if decision is None:
                        members = set()
                        label += ", 그 칸이 정해지지 않아 결정 뒤가 없음"
                    else:
                        members = {
                            i for i in members if ORDER[_phase(messages, i)] > ORDER[decision.phase]
                        }
                        label += f", {decision.describe()} 뒤"
                # 장면은 카드가 발동했으면 있다(그 안에 메시지가 없어도). 결정 뒤를 보는데 칸이
                # 정해지지 않았으면 없다
                exists = bool(cards) and not (timing == "after" and decision is None)
            aux = []
            for spec in dimension.get("aux_scenes") or []:
                phases = set(spec.get("phases") or [])
                spec_members = {m["id"] for m in messages if m["phase"] in phases}
                for card in spec.get("cards") or []:
                    spec_members |= fired.get(card, set())
                spec_exists = bool(phases) or any(c in fired for c in spec.get("cards") or [])
                scene = _scene(spec["id"], spec["label"], spec_exists, spec_members, messages)
                aux.append(replace(scene, fields=frozenset(spec.get("fields") or [])))
            out[dimension_id] = DimensionScenes(
                dimension_id=dimension_id,
                session_wide=not core_cards,
                cards=cards,
                timing=timing,
                decision=decision,
                core=_scene(CORE, label, exists, members, messages),
                aux=tuple(aux),
            )
    return out


def _phase(messages: list[dict[str, Any]], message_id: str) -> str:
    return next(m["phase"] for m in messages if m["id"] == message_id)


def _scene_row(scene: Scene) -> dict[str, Any]:
    return {
        "id": scene.scene_id,
        "label": scene.label,
        "exists": scene.exists,
        "candidate_messages": [f"msg:{i}" for i in scene.candidate_ids],
        "candidate_message_count": len(scene.candidate_ids),
        **({"proposal_fields": sorted(scene.fields)} if scene.fields else {}),
    }


def facts_block(
    element_rubric: dict[str, Any], scenes: dict[str, DimensionScenes], record: dict[str, Any]
) -> dict[str, Any]:
    """평가 프롬프트 `[사실]` 블록: 결정 기록과 이 요소 차원별 핵심·보조 장면."""
    facts = record.get("facts") or {}
    rows = []
    for dimension in element_rubric["dimensions"]:
        dim = scenes[dimension["dimension_id"]]
        rows.append(
            {
                "dimension_id": dim.dimension_id,
                "fired_core_cards": list(dim.cards),
                "decision_cut": None
                if dim.decision is None
                else {
                    "timing": dim.timing,
                    "phase": dim.decision.phase,
                    "t_min": dim.decision.t_min,
                    "path": dim.decision.path,
                },
                "scenes": [_scene_row(dim.core), *(_scene_row(s) for s in dim.aux)],
            }
        )
    recorded = facts.get("decisions")
    if recorded is None:  # 사실이 없는 기록(픽스처 등): 칸 기록에서 시각·경로만
        recorded = [
            {"phase": d.phase, "t_min": d.t_min, "path": d.path} for d in decisions(record).values()
        ]
    return {"decisions": recorded, "dimensions": rows}
