"""페르소나 에이전트 respond: 컨텍스트 조립, 도구 루프, guard·stance_check 판정과 고정 문구.

docs/architecture/agents.md "페르소나 에이전트"·"계획 밖 입장", harness.md Context.
페르소나 입력에 넣지 않는 것: card_progress·cost_left·관찰·평가표·카드 이름·요소 이름·다른 자리 성향
이름·어시스턴트 대화. DebateState는 load가 걸러 미리 넣으므로 get_debate_state는 바인딩하지 않는다.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.agent.catalog import SEATS, Catalog, render
from app.agent.llm import BaseLLM, Trace, Usage, parse_json_object
from app.agent.manager.rules import StancePlan, option_by_id, option_by_key, support_of
from app.agent.mcp_client import ToolScope, for_model

log = logging.getLogger(__name__)

MAX_TOOL_CALLS = 3  # 응답당(harness.md Tools)
RECENT_MESSAGES = 30
MEMORY_MESSAGES = 3
# [상황] 줄 이름. 뜻으로 반박을 세지 않으므로 무엇을 세는지 이름에 그대로 적는다
REACTION_LABEL = "내 지난 발언에 답한 동료·나를 부른 지원자 메시지"
REPEAT_SIMILARITY = 0.8  # 내 최근 발언과 이 이상 같으면 반복(testing.md 반복률 임계와 같음)
REPEAT_VIOLATION = "[내 기억]의 내 말과 거의 같은 문장 반복. 새 근거를 하나 들거나 다른 표현으로"
PHASE_NAMES = {
    "keyword": "키워드",
    "problem": "문제 상황",
    "solution": "해결책",
    "proposal": "기획안 작성",
}
OUTPUT_RULE = (
    '출력은 JSON 객체 하나다: {"content": "게시할 1~3문장", "proposes": null 또는 '
    '{"scenario_option_key": "키", "label": "라벨"}, "stance": null 또는 '
    '{"option_id": "안 id 또는 키", "stance": "support" 또는 "oppose"}}. '
    "stance는 이번 발언이 어떤 안의 지지·반대를 새로 밝히거나 되풀이해 밝힐 때만 채운다. "
    "질문에 답하거나 묻거나 받아 주는 발언이면 null이다. "
    "proposes·stance에 쓸 수 있는 안은 [내 입장]의 내 안뿐이다."
)
# 페르소나 컨텍스트에서 빼는 DebateState 필드(docs/contracts/internal.md DebateState)
HIDDEN_STATE_KEYS = ("card_progress", "cost_left", "candidate_intents", "first_spoke_at")


@dataclass
class Draft:
    content: str
    proposes: dict[str, Any] | None = None
    stance: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    fixed: bool = False  # LLM 없이 만든 고정 문장


def visible_state(ds: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in ds.items() if k not in HIDDEN_STATE_KEYS}


def _remaining(seconds: Any) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "알 수 없음"
    if total >= 60:
        return f"약 {round(total / 60)}분"
    return f"{max(total, 0)}초"


def _name(seat: str, names: dict[str, str]) -> str:
    return names.get(seat, seat)


def build_messages(
    *,
    catalog: Catalog,
    seat: str,
    situation: dict[str, Any],
    plan: StancePlan,
    ds: dict[str, Any],
    names: dict[str, str],
    transcript: list[dict[str, Any]],
    card: dict[str, Any] | None,
    bans: list[str],
    violation: str | None = None,
    notice: str | None = None,
) -> list[BaseMessage]:
    """자리별 컨텍스트(common_rules.md "코드가 붙이는 블록" 순서)."""
    state = visible_state(ds)
    system = "\n\n".join(
        part.strip()
        for part in (
            catalog.prompts.get("common_rules.md", ""),
            catalog.persona_prompt(seat),
            OUTPUT_RULE,
        )
        if part.strip()
    )
    candidate = names.get("candidate", "지원자")
    lines = [f"- 지원자: {candidate}님"]
    for other in SEATS:
        marker = " (나)" if other == seat else ""
        lines.append(f"- {other}: {_name(other, names)}{marker}")
    blocks = ["[참여자]\n" + "\n".join(lines)]

    phase = state.get("phase", plan.phase)
    status = [
        f"- 단계: {PHASE_NAMES.get(phase, phase)}",
        f"- 단계 남은 시간: {_remaining(state.get('phase_remaining_seconds'))}",
    ]
    for slot in state.get("confirmed") or []:
        name = PHASE_NAMES.get(slot.get("phase"), slot.get("phase"))
        status.append(f"- 확정된 칸 {name}: {slot.get('value')}")
    for option in state.get("options") or []:
        supporters = ", ".join(_display(p, names) for p in option.get("supporters") or []) or "없음"
        opposers = ", ".join(_display(p, names) for p in option.get("opposers") or []) or "없음"
        key = option.get("scenario_option_key")
        status.append(
            f"- 안 {option['option_id']}{f' ({key})' if key else ''}: {option.get('label')}"
            f" / 지지: {supporters} / 반대: {opposers}"
        )
    unspoken = [c for c in state.get("catalog_options") or [] if not c.get("spoken")]
    if unspoken:
        status.append(
            "- 아직 나오지 않은 후보: "
            + ", ".join(f"{c['label']} ({c['scenario_option_key']})" for c in unspoken)
        )
    blocks.append("[토론 상태]\n" + "\n".join(status))

    blocks.append("[내 입장]\n" + stance_text(plan, ds))
    blocks.append("[상황]\n" + situation_text(situation, names))
    if notice:
        blocks.append(
            f'[정해진 안내]\n이번 발언 맨 앞에 코드가 "{notice}"를 붙인다. '
            "이 문장을 다시 쓰지 말고 이어질 한두 문장만 쓴다. 이전에 안내했다고 말하지 않는다."
        )
    if card:
        blocks.append("[카드 차례]\n" + card_text(card))
    if bans:
        blocks.append("[지킬 것]\n" + "\n".join(f"- {b}" for b in bans))
    mine = my_lines(transcript, seat)
    if mine:
        blocks.append("[내 기억]\n" + "\n".join(f"- 내가 한 말: {c}" for c in mine))
    recent = transcript[-RECENT_MESSAGES:]
    talk = "\n".join(f"{_display(m.get('sender'), names)}: {m.get('content')}" for m in recent)
    blocks.append("[최근 대화]\n" + (talk or "(아직 없음)"))
    if violation:
        blocks.append(
            f"[다시 쓰기]\n앞 응답은 쓸 수 없다: {violation}. 규칙에 맞게 처음부터 다시 쓴다."
        )
    return [SystemMessage(system), HumanMessage("\n\n".join(blocks))]


def stance_text(plan: StancePlan, ds: dict[str, Any]) -> str:
    """내 입장을 상태 사실로(지시 없음, C2). 무엇을 어떻게 말할지는 성향이 정한다."""
    if not plan.declared:
        return (
            "- 이 단계의 내 안: 아직 없음 (칸이 정해지기 전까지 어느 안도 내 지지로 기록되지 않음)"
        )
    ident = plan.option_id or plan.key
    recorded = support_of(ds, plan.seat) == plan.option_id if plan.option_id else False
    lines = [
        f"- 이 단계의 내 안: {plan.label} ({ident})",
        f"- 이 안 지지를 말로 밝혔는지: {'예 (기록됨)' if recorded else '아니요 (아직 기록 없음)'}",
        f"- 이 단계에서 지지를 옮길 수 있는지: {_yes(plan.can_move)}",
    ]
    return "\n".join(lines)


def card_text(card: dict[str, Any]) -> str:
    """카드 차례의 사실(코드)과 설계된 장면의 목적(cards.yaml `purpose`, 데이터)."""
    turn = "예" if card.get("turn") else "아니요 (앞 카드 발언을 이어가는 차례)"
    pressure = card.get("pressure")
    if pressure:
        turn += f" (이 단계 압박 발언 {pressure[0]}회째, 최대 {pressure[1]}회)"
    lines = [f"- 이번 발언이 내 카드 차례: {turn}"]
    for purpose in card.get("purposes") or []:
        lines.append(f"- 이 장면의 목적: {purpose}")
    return "\n".join(lines)


def situation_text(situation: dict[str, Any], names: dict[str, str]) -> str:
    """진행 매니저가 정한 상황 정보를 사실 문장으로(지시 없음, C1·C2)."""
    target = situation.get("reply_to")
    if target:
        sender = _display(target.get("sender"), names)
        lines = [f'- 답할 메시지: {sender}: "{target.get("content")}"']
        if target.get("sender") == "candidate":
            lines.append(
                f"- 이 메시지에서 지원자가 나를 이름으로 불렀는지: {_yes(situation.get('named'))}"
            )
    else:
        lines = ["- 답할 메시지: 없음"]
    lines.append(f"- 이번 발언이 내 카드 차례인지: {_yes(situation.get('card_turn'))}")
    if situation.get("invite"):
        lines.append("- 침묵 초대 차례: 예(지원자가 이번 단계에서 한동안 말이 없음)")
    reacted = situation.get("reactions")
    if reacted is None:
        lines.append(f"- {REACTION_LABEL}: 이 단계에서 아직 말하지 않음")
    else:
        who = ", ".join(_display(p, names) for p in reacted) if reacted else "아직 없음"
        lines.append(f"- {REACTION_LABEL}: {who}")
    toward = situation.get("toward_mine")
    if toward is not None:
        line = (
            "- 내가 입장을 밝힌 뒤 내 안을 두고 온 지원자 메시지: "
            f"반대 {toward['oppose']}개, 보류 {toward['withhold']}개, 질문 {toward['question']}개"
        )
        if toward.get("last"):
            line += f' (마지막: "{toward["last"]}")'
        lines.append(line)
    counters = situation.get("counters")
    if counters is not None:
        labels = ", ".join(counters.get("labels") or []) or "없음"
        lines.append(
            f"- 내가 입장을 밝힌 뒤 지원자가 다른 안을 지지한 메시지: {counters['n']}개 "
            f"(안: {labels})"
        )
    move = situation.get("move")
    if move:
        lines.append(
            f"- 내 지지가 옮겨졌는데 아직 말로 밝히지 않음: 예 ({move.get('from') or '없음'} → "
            f"{move.get('to')}, 지원자 안)"
        )
    return "\n".join(lines)


def _yes(value: Any) -> str:
    return "예" if value else "아니요"


def my_lines(transcript: list[dict[str, Any]], seat: str) -> list[str]:
    return [m["content"] for m in transcript if m.get("sender") == seat][-MEMORY_MESSAGES:]


def _normal(text: str) -> str:
    return re.sub(r"[\W_]+", "", text)


def repeats(content: str, mine: list[str]) -> bool:
    """내 최근 발언과 거의 같은 문장인지(정규화 문자열 유사도)."""
    text = _normal(content)
    return bool(text) and any(
        SequenceMatcher(None, text, _normal(m)).ratio() >= REPEAT_SIMILARITY for m in mine
    )


def with_notice(content: str, notice: str | None) -> str:
    if not notice or notice in content:
        return content
    return f"{notice} {content}".strip()


def _display(participant: Any, names: dict[str, str]) -> str:
    if participant == "candidate":
        return f"{names.get('candidate', '지원자')}(지원자)"
    if participant == "system":
        return "시스템"
    return names.get(str(participant), str(participant))


async def generate(
    *,
    llm: BaseLLM,
    messages: list[BaseMessage],
    tools: ToolScope | None,
    usage: Usage,
    trace: Trace,
    spent: int = 0,
) -> tuple[Draft, list[dict[str, Any]]]:
    """도구 루프(응답당 최대 3회) → 구조화 출력. LLMUnavailable은 호출자에게 올린다.

    spent는 같은 응답의 앞선 생성이 이미 쓴 도구 횟수다. 반환 (초안, 도구 호출 기록).
    """
    specs = await tools.specs() if tools is not None else []
    history = list(messages)
    calls: list[dict[str, Any]] = []
    while True:
        bind = specs if specs and spent + len(calls) < MAX_TOOL_CALLS else None
        message = await llm.ainvoke(history, usage=usage, trace=trace, tools=bind, json_mode=True)
        if message.tool_calls and bind:
            history.append(message)
            for call in message.tool_calls:
                if spent + len(calls) >= MAX_TOOL_CALLS:
                    result = {"ok": False, "error": {"code": "forbidden", "message": "tool limit"}}
                else:
                    result = await tools.call(call["name"], dict(call.get("args") or {}))
                    usage.tool_calls += 1
                calls.append(
                    {"name": call["name"], "args": call.get("args"), "ok": result.get("ok")}
                )
                history.append(ToolMessage(for_model(result), tool_call_id=call["id"]))
            continue
        return parse_draft(message), calls


def parse_draft(message: AIMessage) -> Draft:
    text = str(message.content or "").strip()
    try:
        data = parse_json_object(text)
    except ValueError:
        return Draft(content=text)
    proposes = data.get("proposes")
    stance = data.get("stance")
    return Draft(
        content=str(data.get("content") or "").strip(),
        proposes=proposes if isinstance(proposes, dict) and any(proposes.values()) else None,
        stance=stance if isinstance(stance, dict) and stance.get("stance") else None,
    )


# ---- stance_check ----------------------------------------------------------------------------
def resolve_target(ds: dict[str, Any], value: Any) -> set[str]:
    """출력이 가리키는 안을 {option_id, key} 집합으로(id·키·라벨 어느 것이든)."""
    if not isinstance(value, str) or not value:
        return set()
    option = option_by_id(ds, value) or option_by_key(ds, value)
    if option is None:
        option = next((o for o in ds.get("options") or [] if o.get("label") == value), None)
    if option is None:
        # 아직 행이 없는 카탈로그 안을 라벨로 가리킨 경우(실 모델이 proposes.label만 채움)
        found = next((c for c in ds.get("catalog_options") or [] if c.get("label") == value), None)
        return {found["scenario_option_key"]} if found else {value}
    return {v for v in (option["option_id"], option.get("scenario_option_key")) if v}


def plan_violation(draft: Draft, plan: StancePlan, ds: dict[str, Any]) -> str | None:
    """구조화 출력이 계획 밖인지(자기 계획 안 oppose 포함, agent-spike S2)."""
    proposes = draft.proposes or {}
    proposed = resolve_target(ds, proposes.get("scenario_option_key") or proposes.get("label"))
    stance = draft.stance or {}
    target = resolve_target(ds, stance.get("option_id"))
    kind = stance.get("stance")
    if not plan.declared:
        if proposed or kind:
            return "아직 입장을 밝히지 않아야 하는데 안을 지지·반대함"
        return None
    if proposed and not proposed & plan.targets:
        return f"계획 밖 안 제안({sorted(proposed)})"
    if kind == "support" and target and not target & plan.targets:
        return f"계획 밖 안 지지({sorted(target)})"
    if kind == "oppose" and (not target or target & plan.targets):
        return "자기 계획 안에 반대함"
    return None


def sentence_violation(judged: dict[str, Any] | None, plan: StancePlan) -> str | None:
    """문장 판정(의도 분류 형식)이 기록될 입장과 다른 **명시적 지지 이동**을 말하는지(C2).

    거부하는 것은 high이고 유보 표현이 없는 다른 안 지지(propose·agree)와 자기 안 철회(oppose·
    withhold)뿐이다. 유보가 든 동의("그 말도 맞지만…")·low·질문·되짚기는 통과한다. 게시 문장과
    저장 입장이 갈라지지 않게 구조화 stance가 자기 안 support여도 문장이 다른 안을 지지하거나 자기
    안을 철회하면 거부한다(충돌이면 재생성). 입장이 없는 상태(카드 전·기획안 단계)는 구조화 출력만
    본다(`plan_violation`): 안을 나열하는 중립 문장을 판정이 propose로 읽는 경우가 많다.
    """
    if not judged or judged.get("confidence") != "high" or not plan.declared:
        return None
    intent = judged.get("intent")
    target = {v for v in (judged.get("target_option_id"), judged.get("scenario_option_key")) if v}
    moves = intent in ("propose", "agree") and not judged.get("reservation")
    if moves and (judged.get("new_option_label") or (target and not target & plan.targets)):
        return "문장이 계획과 다른 안을 지지함"
    if intent in ("oppose", "withhold") and target & plan.targets:
        return "문장이 자기 계획 안을 철회함"
    return None


def move_pending(plan: StancePlan, ds: dict[str, Any]) -> bool:
    """지원자 안으로 옮긴 계획인데 그 자리 지지가 아직 그 안으로 기록되지 않았는지."""
    return bool(plan.moved and plan.option_id) and support_of(ds, plan.seat) != plan.option_id


def move_unstated(
    draft: Draft, plan: StancePlan, ds: dict[str, Any], judged: dict[str, Any] | None
) -> str | None:
    """지원자 안으로 옮긴 계획이 아직 기록되지 않았으면 이번 발언이 그 안 지지를 밝혀야 한다.

    옛 안 oppose만 내고 지지를 밝히지 않으면 이동이 저장되지 않아 합의가 나지 않는다(세션 3e987267).
    """
    if not move_pending(plan, ds):
        return None
    stance = draft.stance or {}
    proposed = resolve_target(ds, (draft.proposes or {}).get("scenario_option_key"))
    if (
        stance.get("stance") == "support"
        and resolve_target(ds, stance.get("option_id")) & plan.targets
    ):
        return None
    if proposed & plan.targets or sentence_supports_plan(judged, plan):
        return None
    return "지원자 안으로 옮겼는데 그 안 지지를 밝히지 않음"


def sentence_supports_plan(judged: dict[str, Any] | None, plan: StancePlan) -> bool:
    if not judged or judged.get("confidence") != "high" or not plan.declared:
        return False
    target = {v for v in (judged.get("target_option_id"), judged.get("scenario_option_key")) if v}
    return judged.get("intent") in ("propose", "agree") and bool(target & plan.targets)


def pick_fixed(lines: list[str], mine: list[str]) -> str:
    """고정 문구 후보 중 내 최근 발언과 겹치지 않는 첫 문구. 모두 겹치면 가장 오래전에 쓴 문구
    (같은 고정 문구를 연달아 게시하지 않게)."""
    fresh = [line for line in lines if not repeats(line, mine)]
    if fresh:
        return fresh[0]

    def last_used(line: str) -> int:
        return max((i for i, m in enumerate(mine) if repeats(line, [m])), default=-1)

    return min(lines, key=last_used)


def stance_fallback(
    catalog: Catalog,
    seat: str,
    plan: StancePlan,
    pending_move: bool = False,
    mine: list[str] | None = None,
) -> Draft:
    """계획과 맞는 자리별 고정 문구(stance_fallback.yaml)와 그 입장. 이동 직후면 moved 문구.

    항목의 `text`와 `alternatives` 중 내 최근 발언(mine)과 겹치지 않는 문구를 쓴다.
    """
    table = catalog.stance_fallback.get(catalog.type_of(seat)) or {}
    entry = (
        (pending_move and table.get("moved"))
        or table.get(plan.fallback_key)
        or table.get(plan.phase)
        or {}
    )
    if entry.get("text"):
        lines = [str(t) for t in (entry["text"], *(entry.get("alternatives") or [])) if t]
        lines = [render(t, option=plan.label or "") for t in lines]
    else:
        lines = catalog.safe_phrases(seat)
    text = pick_fixed(lines, mine or [])
    stance = None
    if entry.get("stance") == "support" and plan.declared:
        stance = {"option_id": plan.option_id or plan.key, "stance": "support"}
    proposes = None
    if stance and plan.option_id is None and plan.key:
        proposes = {"scenario_option_key": plan.key, "label": plan.label}
    return Draft(content=text, proposes=proposes, stance=stance, fixed=True)


def safe_draft(catalog: Catalog, seat: str, mine: list[str] | None = None) -> Draft:
    """자리별 안전 문구(safe_<type>.md 한 줄). 내 최근 발언과 겹치지 않는 줄을 고른다."""
    return Draft(content=pick_fixed(catalog.safe_phrases(seat), mine or []), fixed=True)


def to_json(draft: Draft) -> str:
    return json.dumps(
        {"content": draft.content, "proposes": draft.proposes, "stance": draft.stance},
        ensure_ascii=False,
    )
