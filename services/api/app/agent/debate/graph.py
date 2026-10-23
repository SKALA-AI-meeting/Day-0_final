"""토론 그래프(docs/architecture/agents.md "토론 그래프"): 진행 매니저 + 페르소나 3.

```
load → cards → turn ─┬─ (화자마다) respond → guard → stance_check → wait_typing → apply ─┐
                     │                                                               ├→ fill → END
                     └─ 화자 0명 ────────────────────────────────────────────────────────┘
```

typing start는 respond 시작, stop은 apply 끝(또는 버림·미룸)에서 보낸다. 실행은 api 메일박스가
세션마다
직렬로 하므로 그래프 안에 Lock이 없다. 토론 상태(안·입장·칸·카드 진행·generation)의 원천은 DB이고
그래프는 load로 다시 읽는다. 모든 변경은 run_id를 갖고 api 멱등 키가 중복을 막는다.
"""

import asyncio
import logging
import random
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from app.agent import intent as intent_mod
from app.agent import persona
from app.agent.catalog import DEBATE_PHASES, Catalog, render
from app.agent.guard import GuardResult, guard_from_prompts
from app.agent.llm import BaseLLM, LLMUnavailable, Trace, Usage, parse_json_object
from app.agent.manager import rules
from app.agent.manager.rules import PlanContext, StancePlan
from app.agent.mcp_client import RunTools, error_code, error_message
from app.ports import SessionPorts

log = logging.getLogger(__name__)

TRANSCRIPT_LIMIT = 30
TURN_RECENT = 10
TYPING_WAIT_SECONDS = 10.0
TYPING_POLL_SECONDS = 0.5
SPEAKER_GAP_SECONDS = (1.0, 3.0)
FILL_SEQ = 5

Sleep = Callable[[float], Awaitable[None]]


@dataclass
class DebateLLMs:
    """역할별 래퍼(roles.py make_llm). intent는 stance_check 문장 판정·의도 재분류에 쓴다."""

    persona: BaseLLM
    turn: BaseLLM
    intent: BaseLLM | None = None


@dataclass
class DebateDeps:
    """실행마다 바뀌는 의존성(Runtime context)."""

    catalog: Catalog
    tools: RunTools
    ports: SessionPorts
    llms: DebateLLMs
    usage: Usage = field(default_factory=Usage)
    sleep: Sleep = asyncio.sleep
    typing_wait: float = TYPING_WAIT_SECONDS
    typing_poll: float = TYPING_POLL_SECONDS
    speaker_gap: tuple[float, float] = SPEAKER_GAP_SECONDS
    snapshots: list[dict[str, Any]] | None = None  # 테스트용 페르소나 입력 기록

    def trace(self, state: "DebateState", node: str) -> Trace:
        return Trace(state["run_id"], state["session_id"], node)


class DebateState(TypedDict, total=False):
    session_id: str
    run_id: str
    stimulus: dict[str, Any]
    phase: str
    generation: int
    ds: dict[str, Any]
    transcript: list[dict[str, Any]]
    names: dict[str, str]
    intent: dict[str, Any] | None
    brief: list[dict[str, Any]]
    lead: dict[str, Any] | None
    decision: dict[str, Any]
    idx: int
    plan: dict[str, Any] | None
    draft: dict[str, Any] | None
    status: str  # finished | deferred | skipped | failed
    posted: list[dict[str, Any]]
    observed: list[str]
    fallback_used: bool
    last_error: str | None
    llm_failed: bool
    notice: str | None  # 첫 화자 발언 앞에 붙일 정해진 안내(fixed_phrases notice_triggers)


# ---- 도움 함수 ------------------------------------------------------------------------------
def _data(result: dict[str, Any]) -> Any:
    return result.get("data") if result.get("ok") else None


def _speakers(state: DebateState) -> list[dict[str, Any]]:
    return (state.get("decision") or {}).get("speakers") or []


def _current(state: DebateState) -> dict[str, Any]:
    return _speakers(state)[state.get("idx", 0)]


def _plan_context(state: DebateState, deps: DebateDeps) -> PlanContext:
    ds = state["ds"]
    progress = rules.card_progress(ds)
    lock_card = (deps.catalog.mission.get("lead_option") or {}).get(
        "lock_card", "flexibility_majority"
    )
    brief_cards = {c for item in state.get("brief") or [] for c in item["card_ids"]}
    card_active = lock_card in progress or lock_card in brief_cards
    ctx = PlanContext(ds=ds, catalog=deps.catalog, card_active=card_active, lead=state.get("lead"))
    critic = deps.catalog.seat_of("critic")
    if critic:
        key, declared = rules._base_key(critic, state["phase"], ctx)
        targets = {v for v in (key,) if v}
        option = rules.option_by_key(ds, key)
        if option:
            targets.add(option["option_id"])
        if ctx.lead and key == ctx.lead.get("key") and ctx.lead.get("option_id"):
            targets.add(ctx.lead["option_id"])
        if declared and targets:
            rule = deps.catalog.persona(critic).get("persuadable") or {}
            ctx.counter_count = rules.counter_count(
                _phase_intents(state, critic), targets, rule if isinstance(rule, dict) else {}
            )
    return ctx


def _phase_intents(state: DebateState, after_seat: str | None = None) -> list[dict[str, Any]]:
    """이 단계 지원자 메시지의 의도. 현재 자극 메시지 포함.

    원천은 DebateState `candidate_intents`(api가 저장한 message_intents)라 재시작·다른
    프로세스에서도 같다. after_seat를 주면 그 자리의 이 단계 첫 발언(`first_spoke_at`) 뒤 메시지만
    센다(입장을 밝히기 전 제안은 반론이 아님). 그 자리가 아직 말하지 않았으면 하나도 세지 않는다.
    """
    ds = state.get("ds") or {}
    since = (ds.get("first_spoke_at") or {}).get(after_seat) if after_seat else None
    if after_seat and since is None:
        return []
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in ds.get("candidate_intents") or []:
        seen.add(str(row.get("message_id")))
        if since is None or _when(row.get("created_at")) > _when(since):
            found.append(row)
    current_id = str((state.get("stimulus") or {}).get("message_id"))
    if state.get("intent") and current_id not in seen:
        found.append(state["intent"])
    return found


def _when(value: Any) -> datetime:
    """ISO 시각 비교용(문자열 비교는 소수 초 자리 수가 다르면 순서가 틀린다)."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def _plan_for(state: DebateState, deps: DebateDeps, seat: str) -> StancePlan:
    return rules.stance_plan(seat, state["phase"], _plan_context(state, deps))


def _card_text(state: DebateState, deps: DebateDeps, seat: str, card_ids: list[str]) -> str | None:
    texts = [rules.CARD_INSTRUCTIONS[c] for c in card_ids if c in rules.CARD_INSTRUCTIONS]
    if not card_ids and state["phase"] == "problem" and seat == deps.catalog.seat_of("stubborn"):
        card = rules.card_progress(state["ds"]).get("listening_pressure") or {}
        count = rules.pressure_count(card, seat)
        # 카드 첫 발언을 포함해 압박 발언은 최대 MAX_PRESSURE회(이번 발언이 그다음 회차)
        if card.get("delivered") and count < rules.MAX_PRESSURE:
            texts.append(rules.PRESSURE_FOLLOWUP)
    return " ".join(texts) or None


def _typing_off(deps: DebateDeps, seat: str) -> None:
    deps.ports.send_typing(seat, "stop")


def _drop_rest(state: DebateState, deps: DebateDeps, reason: str) -> dict[str, Any]:
    speakers = _speakers(state)
    idx = state.get("idx", 0)
    dropped = [s["seat"] for s in speakers[idx:]]
    if idx < len(speakers):
        _typing_off(deps, speakers[idx]["seat"])
    decision = {**state["decision"], "dropped_seats": dropped}
    log.info("drop speakers %s: %s", dropped, reason)
    return {"decision": decision, "idx": len(speakers), "status": "finished", "draft": None}


# ---- 노드 -----------------------------------------------------------------------------------
async def load(state: DebateState, runtime: Runtime[DebateDeps]) -> dict[str, Any]:
    deps = runtime.context
    stimulus = state["stimulus"]
    result = await deps.tools.code.call("get_debate_state", {})
    ds = _data(result)
    if not isinstance(ds, dict):
        return {"status": "failed", "last_error": f"get_debate_state: {error_code(result)}"}
    generation = deps.ports.get_generation()
    if ds.get("phase") != stimulus["phase"]:
        return {"status": "skipped", "ds": ds, "generation": generation}
    if stimulus["type"] == "message" and stimulus.get("generation", generation) < generation:
        return {"status": "skipped", "ds": ds, "generation": generation}
    found = _data(
        await deps.tools.code.call(
            "get_transcript", {"phase": stimulus["phase"], "limit": TRANSCRIPT_LIMIT}
        )
    )
    transcript = [m for m in found if isinstance(m, dict)] if isinstance(found, list) else []
    names = {s["seat"]: s.get("display_name") or s["seat"] for s in ds.get("seats") or []}
    for seat in ("p1", "p2", "p3"):
        names.setdefault(seat, deps.catalog.name_of(seat))
    names.setdefault("candidate", "지원자")
    update: dict[str, Any] = {
        "ds": ds,
        "generation": generation,
        "transcript": transcript,
        "names": names,
        "intent": None,
        "status": "finished",
        "posted": [],
        "observed": [],
        "fallback_used": False,
        "last_error": None,
        "llm_failed": False,
        "idx": 0,
        "draft": None,
        "plan": None,
        "notice": None,
    }
    if stimulus["type"] == "message" and stimulus.get("message_id"):
        update["intent"] = await _message_intent(stimulus, ds, transcript, names, deps)
        update["notice"] = _notice(stimulus, transcript, deps.catalog)
    return update


def _notice(
    stimulus: dict[str, Any], transcript: list[dict[str, Any]], catalog: Catalog
) -> str | None:
    """지원자 메시지가 notice_triggers 패턴에 걸리면 그 안내문(정하는 방법 질문 등)."""
    mid = str(stimulus["message_id"])
    text = next((str(m.get("content") or "") for m in transcript if str(m.get("id")) == mid), "")
    for key, patterns in (catalog.fixed.get("notice_triggers") or {}).items():
        if text and any(re.search(p, text, re.IGNORECASE) for p in patterns or []):
            return catalog.notices.get(key)
    return None


async def _message_intent(
    stimulus: dict[str, Any],
    ds: dict[str, Any],
    transcript: list[dict[str, Any]],
    names: dict[str, str],
    deps: DebateDeps,
) -> dict[str, Any] | None:
    """메일박스가 분류한 결과를 읽는다. 재시작 등으로 없으면 같은 입력으로 다시 분류한다(저장
    없음).
    """
    mid = str(stimulus["message_id"])
    cached = intent_mod.recall(mid)
    if cached is not None:
        return dict(cached)
    index = next((i for i, m in enumerate(transcript) if str(m.get("id")) == mid), None)
    if index is None or deps.llms.intent is None:
        return None
    message = transcript[index]
    result, _ = await intent_mod.classify(
        deps.llms.intent,
        str(message.get("content") or ""),
        phase=stimulus["phase"],
        catalog=[
            {"scenario_option_key": c["scenario_option_key"], "label": c["label"]}
            for c in ds.get("catalog_options") or []
        ],
        options=[
            {
                "option_id": o["option_id"],
                "label": o["label"],
                "scenario_option_key": o.get("scenario_option_key"),
            }
            for o in ds.get("options") or []
        ],
        recent=transcript[max(0, index - 5) : index],
        seats={k: v for k, v in names.items() if k != "candidate"},
        message_id=mid,
        instructions=deps.catalog.prompts.get("intent.md"),
    )
    return dict(result)


async def cards(state: DebateState, runtime: Runtime[DebateDeps]) -> dict[str, Any]:
    """이번 run의 카드 화자(card_brief)와 문제 상황 기준안. honesty_credit은 관찰만."""
    deps = runtime.context
    ds, stimulus = state["ds"], state["stimulus"]
    brief = rules.card_brief(stimulus, ds, deps.catalog)
    lead = rules.locked_lead(ds, deps.catalog)
    lock_card = (deps.catalog.mission.get("lead_option") or {}).get(
        "lock_card", "flexibility_majority"
    )
    if lead is None and lock_card in rules.card_progress(ds):
        lead = rules.held_lead(ds, deps.catalog)
    if lead is None and any(lock_card in item.card_ids for item in brief):
        # api가 card_due 처리에서 잠그지 않았으면 같은 규칙으로 계산해 쓴다(행은 apply의
        # propose_option)
        key = rules.lead_key(ds, deps.catalog)
        option = rules.option_by_key(ds, key)
        lead = {"option_id": (option or {}).get("option_id"), "key": key}
    observed = list(state.get("observed") or [])
    if stimulus["type"] == "phase_started" and stimulus["phase"] == "proposal":
        for card in deps.catalog.cards_in("proposal"):
            if card.get("actor_mode") != "none" or not rules.first_delivery(card["card_id"], ds):
                continue
            trigger = next(
                (
                    m.get("id")
                    for m in reversed(state.get("transcript") or [])
                    if m.get("sender") == "system"
                ),
                None,
            )
            result = await deps.tools.code.call(
                "record_observation",
                {
                    "card_id": card["card_id"],
                    "phase": "proposal",
                    "trigger_message_id": trigger,
                    "run_id": state["run_id"],
                },
            )
            if result.get("ok"):
                observed.append(card["card_id"])
    return {
        "brief": [{"seat": item.seat, "card_ids": item.card_ids} for item in brief],
        "lead": lead,
        "observed": observed,
    }


async def turn(state: DebateState, runtime: Runtime[DebateDeps]) -> dict[str, Any]:
    deps = runtime.context
    brief = [rules.CardSpeaker(i["seat"], i["card_ids"]) for i in state.get("brief") or []]
    decision = rules.rule_decision(
        state["stimulus"],
        state["ds"],
        deps.catalog,
        brief,
        state.get("intent"),
        state["transcript"],
    )
    if (
        not decision["speakers"]
        and decision["fill"] is None
        and state["stimulus"]["type"] == "message"
        and _cost_left(state) > 0
        and (
            state["phase"] != "proposal" or (state.get("intent") or {}).get("intent") == "question"
        )
    ):
        decision["llm_used"] = True
        # 지원자가 답하지 말라고 뺀 자리(의도 분류 excluded)는 턴 LLM이 골라도 넣지 않는다
        excluded = set(
            rules.excluded_seats(
                rules.message_text(state["stimulus"], state["transcript"]),
                (state.get("intent") or {}).get("excluded"),
                deps.catalog,
            )
        )
        decision["speakers"] = [
            s for s in await _turn_llm(state, deps, brief) if s["seat"] not in excluded
        ]
        decision["reason"] = decision.get("reason") or "턴 LLM 화자 선택"
    critic = deps.catalog.seat_of("critic")
    if state.get("notice") and not decision["speakers"] and decision["fill"] is None and critic:
        # 정하는 방법 질문은 답이 없으면 안 되므로 턴 LLM이 0명을 골라도 비판형이 안내한다
        decision["speakers"] = [rules.speaker(critic, "respond")]
        decision["reason"] = "정하는 방법 질문"
    return {"decision": decision, "idx": 0}


def _cost_left(state: DebateState) -> float:
    value = state["ds"].get("cost_left")
    return float(value) if isinstance(value, int | float) else 1.0


async def _turn_llm(
    state: DebateState, deps: DebateDeps, brief: list[rules.CardSpeaker]
) -> list[dict[str, Any]]:
    """규칙 6: 규칙 결과 0명 + message 자극일 때만. 실패하면 0명."""
    ds, names = state["ds"], state["names"]
    summary = persona.visible_state(ds)
    tones = [
        f"- {seat} {names.get(seat)}: {deps.catalog.persona(seat).get('tone', '')}"
        for seat in ("p1", "p2", "p3")
    ]
    bans = sorted(
        {
            b
            for seat in ("p1", "p2", "p3")
            for b in rules.preempt_bans(seat, state["phase"], ds, deps.catalog, brief)
        }
    )
    recent = state["transcript"][-TURN_RECENT:]
    user = "\n\n".join(
        [
            "[토론 상태 요약]\n"
            + str(
                {
                    "phase": summary.get("phase"),
                    "phase_remaining_seconds": summary.get("phase_remaining_seconds"),
                    "options": [
                        {k: o.get(k) for k in ("option_id", "label", "supporters", "opposers")}
                        for o in summary.get("options") or []
                    ],
                    "confirmed": summary.get("confirmed"),
                }
            ),
            "[최근 대화]\n" + "\n".join(f"{m.get('sender')}: {m.get('content')}" for m in recent),
            "[자리 성향]\n" + "\n".join(tones),
            "[선점 금지]\n" + ("\n".join(f"- {b}" for b in bans) or "- 없음"),
            "[지원자 메시지 분류]\n" + str(state.get("intent")),
        ]
    )
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        message = await deps.llms.turn.ainvoke(
            [SystemMessage(deps.catalog.prompts.get("turn_manager.md", "")), HumanMessage(user)],
            usage=deps.usage,
            trace=deps.trace(state, "turn"),
            json_mode=True,
        )
        out = parse_json_object(str(message.content))
    except (LLMUnavailable, ValueError) as exc:
        log.warning("turn llm failed: %r", exc)
        return []
    return rules.validate_turn_llm(out, deps.catalog, state["transcript"])


async def respond(state: DebateState, runtime: Runtime[DebateDeps]) -> dict[str, Any]:
    deps = runtime.context
    spk = _current(state)
    seat = spk["seat"]
    deps.ports.send_typing(seat, "start")
    plan = _plan_for(state, deps, seat)
    meta: dict[str, Any] = {"guard": "pass", "stance_check": "pass"}
    # 고정 문장 카드(머뭇거림 한 줄)는 LLM 없이
    for card_id in spk.get("card_ids") or []:
        line = (deps.catalog.card(card_id) or {}).get("fixed_line")
        if line:
            draft = persona.Draft(
                content=line, fixed=True, meta={**meta, "kind": "card_fixed_line"}
            )
            return {"plan": asdict(plan), "draft": asdict(draft)}
    mine = persona.my_lines(state["transcript"], seat)
    if _cost_left(state) <= 0:
        draft = persona.safe_draft(deps.catalog, seat, mine)
        draft.meta = {**meta, "cost_cap": True}
        return {"plan": asdict(plan), "draft": asdict(draft)}
    try:
        draft = await _generate(state, deps, spk, plan, None)
    except LLMUnavailable as exc:
        log.warning("persona %s llm unavailable: %s", seat, exc)
        return {
            "plan": asdict(plan),
            "draft": None,
            "fallback_used": True,
            "llm_failed": True,
            "last_error": f"persona_llm_unavailable:{seat}",
        }
    if persona.repeats(draft.content, mine):
        # 같은 문장 반복은 1회만 다시 쓰게 하고 다시 쓴 문장도 같은 검사를 한다(실패하거나 비거나
        # 여전히 반복이면 첫 초안을 그대로 쓴다)
        meta["repeat"] = "kept"
        try:
            again = await _generate(state, deps, spk, plan, persona.REPEAT_VIOLATION, _spent(draft))
        except LLMUnavailable:
            again = None
        if again is not None and again.content and not persona.repeats(again.content, mine):
            draft, meta["repeat"] = again, "regenerated"
        elif again is not None:
            draft.meta["tools_used"] = _spent(again)  # 버린 재생성이 쓴 도구도 예산에서 뺀다
    draft.meta = {**meta, "tools_used": _spent(draft)}
    return {"plan": asdict(plan), "draft": asdict(draft)}


async def _generate(
    state: DebateState,
    deps: DebateDeps,
    spk: dict[str, Any],
    plan: StancePlan,
    violation: str | None,
    spent: int = 0,
) -> persona.Draft:
    seat = spk["seat"]
    brief = [rules.CardSpeaker(i["seat"], i["card_ids"]) for i in state.get("brief") or []]
    messages = persona.build_messages(
        catalog=deps.catalog,
        seat=seat,
        purpose=spk["purpose"],
        plan=plan,
        ds=state["ds"],
        names=state["names"],
        transcript=state["transcript"],
        card_text=_card_text(state, deps, seat, spk.get("card_ids") or []),
        bans=rules.preempt_bans(seat, state["phase"], state["ds"], deps.catalog, brief),
        violation=violation,
        notice=_first_notice(state),
    )
    if deps.snapshots is not None:
        deps.snapshots.append({"seat": seat, "text": "\n".join(str(m.content) for m in messages)})
    draft, calls = await persona.generate(
        llm=deps.llms.persona,
        messages=messages,
        tools=deps.tools.persona(state["phase"]),
        usage=deps.usage,
        trace=deps.trace(state, f"respond:{seat}"),
        spent=spent,
    )
    draft.meta["tool_calls"] = calls
    draft.meta["tools_used"] = spent + len(calls)
    # 안내문을 붙일 자리는 붙인 뒤 400자 안에 들도록 본문을 먼저 자른다(검사한 본문 = 게시 본문)
    draft.content = draft.content.strip()[: _body_limit(state, deps)].rstrip()
    return draft


def _spent(draft: persona.Draft) -> int:
    """응답 하나(최초·반복·guard·stance 재생성)가 지금까지 쓴 도구 횟수(예산 공유)."""
    return int(draft.meta.get("tools_used") or 0)


def _first_notice(state: DebateState) -> str | None:
    return state.get("notice") if state.get("idx", 0) == 0 else None


def _body_limit(state: DebateState, deps: DebateDeps) -> int:
    limit = guard_from_prompts(deps.catalog.prompts).max_chars
    notice = _first_notice(state)
    return limit - len(notice) - 1 if notice else limit


def _restore(state: DebateState) -> tuple[persona.Draft, StancePlan]:
    draft = persona.Draft(**state["draft"])
    plan = StancePlan(**state["plan"])
    return draft, plan


def _guard_check(state: DebateState, deps: DebateDeps, text: str) -> GuardResult:
    """금지 패턴 검사. 지원자 표시 이름은 가리고 본다(지원자가 정한 이름이 패턴에 걸리지 않게).

    동료 이름은 카탈로그 데이터라 패턴이 그 이름을 직접 쓰므로(credit 그룹) 가리지 않는다.
    """
    checker = guard_from_prompts(deps.catalog.prompts)
    return checker.check(text, state["phase"], [state["names"].get("candidate") or ""])


async def guard(state: DebateState, runtime: Runtime[DebateDeps]) -> dict[str, Any]:
    """금지 패턴 → 같은 입력 재생성 1회 → 자리별 안전 문구. 400자 초과는 자른다."""
    if not state.get("draft"):
        return {}
    deps = runtime.context
    draft, plan = _restore(state)
    result = _guard_check(state, deps, draft.content)
    if result.ok or draft.fixed:
        draft.content = result.text
        return {"draft": asdict(draft)}
    spk = _current(state)
    meta = {**draft.meta, "guard": "regenerated", "guard_hits": list(result.hits)}
    try:
        violation = f"금지 표현({', '.join(result.hits)})"
        again = await _generate(state, deps, spk, plan, violation, _spent(draft))
    except LLMUnavailable:
        again = None
    second = _guard_check(state, deps, again.content) if again else None
    if again is None or second is None or not second.ok or not again.content:
        safe = persona.safe_draft(
            deps.catalog, spk["seat"], persona.my_lines(state["transcript"], spk["seat"])
        )
        safe.meta = {**meta, "guard": "safe"}
        return {"draft": asdict(safe)}
    again.content = second.text
    again.meta = {**meta, "tools_used": _spent(again)}
    return {"draft": asdict(again)}


async def _judge_sentence(
    state: DebateState, deps: DebateDeps, content: str
) -> dict[str, Any] | None:
    """게시 문장의 입장 판정(의도 분류 모듈, 같은 형식). 분류 LLM이 없으면 생략."""
    if deps.llms.intent is None or not content:
        return None
    ds = state["ds"]
    result, used_fallback = await intent_mod.classify(
        deps.llms.intent,
        content,
        phase=state["phase"],
        catalog=[
            {"scenario_option_key": c["scenario_option_key"], "label": c["label"]}
            for c in ds.get("catalog_options") or []
        ],
        options=[
            {
                "option_id": o["option_id"],
                "label": o["label"],
                "scenario_option_key": o.get("scenario_option_key"),
            }
            for o in ds.get("options") or []
        ],
        recent=state["transcript"][-5:],
        seats={k: v for k, v in state["names"].items() if k != "candidate"},
        instructions=deps.catalog.prompts.get("intent.md"),
    )
    return None if used_fallback else dict(result)


async def _violation(
    state: DebateState, deps: DebateDeps, draft: persona.Draft, plan: StancePlan
) -> tuple[str | None, dict | None]:
    found = persona.plan_violation(draft, plan, state["ds"])
    if found:
        return found, None
    judged = await _judge_sentence(state, deps, draft.content)
    found = persona.sentence_violation(judged, plan) or persona.move_unstated(
        draft, plan, state["ds"], judged
    )
    return found, judged


def _settle_stance(
    draft: persona.Draft, judged: dict[str, Any] | None, plan: StancePlan, ds: dict[str, Any]
) -> None:
    """문장이 계획 안 지지면 그 지지를 저장 입장으로 둔다. 이동이 아직 기록되지 않았으면 구조화
    stance가 다른 값(옛 안 oppose 등)이어도 계획 안 지지로 정정한다(입장은 코드가 정한다).
    """
    pending = persona.move_pending(plan, ds)
    proposed = persona.resolve_target(ds, (draft.proposes or {}).get("scenario_option_key"))
    if not (persona.sentence_supports_plan(judged, plan) or (pending and proposed & plan.targets)):
        return
    if draft.stance is None or pending:
        draft.stance = {"option_id": plan.option_id or plan.key, "stance": "support"}


async def stance_check(state: DebateState, runtime: Runtime[DebateDeps]) -> dict[str, Any]:
    """계획 밖 입장이면 응답 전체 1회 재생성 → 그래도 밖이면 stance_fallback(R5-8, S2)."""
    if not state.get("draft"):
        return {}
    deps = runtime.context
    draft, plan = _restore(state)
    if draft.fixed:
        return {}
    found, judged = await _violation(state, deps, draft, plan)
    if not found:
        _settle_stance(draft, judged, plan, state["ds"])
        return {"draft": asdict(draft)}
    spk = _current(state)
    meta = {**draft.meta, "stance_check": "regenerated", "stance_violation": found}
    try:
        again = await _generate(state, deps, spk, plan, f"계획 밖 입장({found})", _spent(draft))
    except LLMUnavailable:
        again = None
    if again is not None and _guard_check(state, deps, again.content).ok and again.content:
        second, judged = await _violation(state, deps, again, plan)
        if not second:
            _settle_stance(again, judged, plan, state["ds"])
            again.content = _guard_check(state, deps, again.content).text
            again.meta = {**meta, "tools_used": _spent(again)}
            return {"draft": asdict(again)}
    pending = persona.move_pending(plan, state["ds"])
    fixed = persona.stance_fallback(
        deps.catalog, spk["seat"], plan, pending, persona.my_lines(state["transcript"], spk["seat"])
    )
    fixed.meta = {**meta, "stance_check": "fallback"}
    return {"draft": asdict(fixed)}


async def wait_typing(state: DebateState, runtime: Runtime[DebateDeps]) -> dict[str, Any]:
    """게시 직전: generation이 바뀌었으면 남은 화자를 버린다. 지원자 입력 중이면 최대 10초 기다리고
    그래도 입력 중이면 게시하지 않고 같은 자극을 다시 넣는다(R5-9)."""
    deps = runtime.context
    if not state.get("draft"):
        return {}
    if deps.ports.get_generation() != state["generation"]:
        return _drop_rest(state, deps, "generation changed")
    waited = 0.0
    while deps.ports.candidate_typing() and waited < deps.typing_wait:
        await deps.sleep(deps.typing_poll)
        waited += deps.typing_poll
    if deps.ports.candidate_typing():
        _typing_off(deps, _current(state)["seat"])
        from app.agent.runner import Stimulus

        stim = state["stimulus"]
        deps.ports.enqueue(
            Stimulus(
                type=stim["type"],
                phase=stim["phase"],
                generation=stim["generation"],
                message_id=uuid.UUID(stim["message_id"]) if stim.get("message_id") else None,
                card_id=stim.get("card_id"),
            )
        )
        speakers = _speakers(state)
        return {
            "status": "deferred",
            "draft": None,
            "idx": len(speakers),
            "decision": {
                **state["decision"],
                "dropped_seats": [s["seat"] for s in speakers[state["idx"] :]],
            },
        }
    if deps.ports.get_generation() != state["generation"]:
        return _drop_rest(state, deps, "generation changed while waiting")
    return {}


async def apply(state: DebateState, runtime: Runtime[DebateDeps]) -> dict[str, Any]:
    """post_message(card_ids) → (처음 미는 안) propose_option → set_stance → 첫 카드 발언 뒤
    관찰.
    """
    deps = runtime.context
    idx = state.get("idx", 0)
    spk = _current(state)
    seat = spk["seat"]
    update: dict[str, Any] = {"idx": idx + 1, "draft": None}
    if not state.get("draft"):
        _typing_off(deps, seat)
        return update
    draft, plan = _restore(state)
    code = deps.tools.code
    card_ids = list(spk.get("card_ids") or [])
    meta = {
        **draft.meta,
        "model": getattr(deps.llms.persona, "model", None),
        "purpose": spk["purpose"],
    }
    meta.pop("tool_calls", None)
    content = draft.content
    if draft.meta.get("kind") != "card_fixed_line":
        # 안내문은 고정 문구라 검사 뒤에 붙인다(폴백·안전 문구여도 붙임, 카드 고정 한 줄은 제외)
        content = persona.with_notice(content, _first_notice(state))
    posted = await code.call(
        "post_message",
        {
            "sender": seat,
            "content": content,
            "run_id": state["run_id"],
            "seq": idx + 1,
            "expected_generation": state["generation"],
            "is_invite": spk["purpose"] == "invite",
            "card_ids": card_ids,
            "meta": meta,
        },
    )
    if not posted.get("ok"):
        if error_message(posted) == "stale_generation":
            return {**_drop_rest(state, deps, "stale_generation"), "idx": len(_speakers(state))}
        _typing_off(deps, seat)
        return {
            **update,
            "fallback_used": True,
            "last_error": f"post_message: {error_code(posted)} {error_message(posted)}",
        }
    message = posted["data"] or {}
    message_id = message.get("id")
    stance_written = await _write_stance(state, deps, seat, draft, plan, message_id)
    observed = list(state.get("observed") or [])
    for card_id in card_ids:
        if card_id in observed or not rules.first_delivery(card_id, state["ds"]):
            continue
        result = await code.call(
            "record_observation",
            {
                "card_id": card_id,
                "phase": state["phase"],
                "trigger_message_id": message_id,
                "run_id": state["run_id"],
            },
        )
        if result.get("ok"):
            observed.append(card_id)
    _typing_off(deps, seat)
    posted_list = [
        *(state.get("posted") or []),
        {"seat": seat, "message_id": message_id, "stance": stance_written, "card_ids": card_ids},
    ]
    # 같은 run의 다음 화자가 방금 게시한 발언을 최근 대화·자기 기억에서 보게 한다
    seen = {
        "id": message_id,
        "sender": seat,
        "content": message.get("content", content),
        "phase": message.get("phase", state["phase"]),
        "is_invite": message.get("is_invite", spk["purpose"] == "invite"),
        "t_min": message.get("t_min"),
    }
    transcript = [*(state.get("transcript") or []), seen][-TRANSCRIPT_LIMIT:]
    update.update({"posted": posted_list, "observed": observed, "transcript": transcript})
    if idx + 1 < len(_speakers(state)):
        low, high = deps.speaker_gap
        await deps.sleep(random.uniform(low, high))
    return update


async def _write_stance(
    state: DebateState,
    deps: DebateDeps,
    seat: str,
    draft: persona.Draft,
    plan: StancePlan,
    message_id: str | None,
) -> dict[str, Any] | None:
    """게시 문장이 표현한 입장만 기록한다(게시 문장과 기록 입장 일치)."""
    stance = draft.stance or {}
    kind = stance.get("stance")
    if not (kind or draft.proposes) or not plan.declared or message_id is None:
        return None
    code = deps.tools.code
    option_id = plan.option_id
    if option_id is None and plan.key:
        created = await code.call(
            "propose_option",
            {
                "phase": state["phase"],
                "label": plan.label or plan.key,
                "proposer": seat,
                "scenario_option_key": plan.key,
                "source_message_id": message_id,
                "run_id": state["run_id"],
            },
        )
        option_id = (_data(created) or {}).get("option_id")
    if option_id is None:
        return None
    kind = kind if kind in ("support", "oppose") else "support"
    if kind == "oppose":  # 계획 안 oppose는 stance_check가 막으므로 여기 오는 oppose는 다른 안 대상
        target = persona.resolve_target(state["ds"], stance.get("option_id"))
        other = next((t for t in target if rules.option_by_id(state["ds"], t)), None)
        if other is None:
            return None
        option_id = other
    await code.call(
        "set_stance",
        {
            "participant": seat,
            "option_id": option_id,
            "stance": kind,
            "source_message_id": message_id,
            "run_id": state["run_id"],
        },
    )
    return {"option_id": option_id, "stance": kind}


async def fill(state: DebateState, runtime: Runtime[DebateDeps]) -> dict[str, Any]:
    """합의 채우기 전용 분기: `fill_slot{option_id, seat, content, run_id}` 한 번(공지
    post_message 없음).

    채우는 것은 load 때 이미 합의가 있던 run(`decision.fill`)뿐이다. 이번 run의 AI 발언으로 지지가
    바뀌어도 같은 run에서 다시 판정하지 않고, 이후 run이 지원자의 최신 발언과 함께 다시 본다(B2).
    """
    deps = runtime.context
    if (
        state.get("status") in ("deferred", "failed", "skipped")
        or state["phase"] not in DEBATE_PHASES
    ):
        return {}
    decision = dict(state["decision"])
    target = decision.get("fill")
    ds = state["ds"]
    if not target:
        return {}
    option = rules.option_by_id(ds, target["option_id"]) or {}
    label = (
        option.get("label") or deps.catalog.label_of(option.get("scenario_option_key") or "") or ""
    )
    content = render(
        str(deps.catalog.fixed.get("consensus_fill", "그럼 {option}로 정리할게요.")), option=label
    )
    result = await deps.tools.code.call(
        "fill_slot",
        {
            "option_id": target["option_id"],
            "seat": target["seat"],
            "content": content,
            "run_id": state["run_id"],
        },
    )
    decision["fill"] = target
    if not result.get("ok"):
        log.info("fill_slot rejected: %s", error_message(result))
        decision["fill_result"] = error_message(result) or error_code(result)
    return {"decision": decision}


# ---- 분기 -----------------------------------------------------------------------------------
def after_load(state: DebateState) -> str:
    return END if state.get("status") in ("skipped", "failed") else "cards"


def after_turn(state: DebateState) -> str:
    return "respond" if _speakers(state) else "fill"


def after_wait(state: DebateState) -> str:
    if state.get("status") == "deferred" or state.get("idx", 0) >= len(_speakers(state)):
        return END
    return "apply"


def after_apply(state: DebateState) -> str:
    if state.get("status") == "deferred":
        return END
    if (state.get("decision") or {}).get("dropped_seats"):
        return END
    return "respond" if state.get("idx", 0) < len(_speakers(state)) else "fill"


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    builder = StateGraph(DebateState, context_schema=DebateDeps)
    for name, node in (
        ("load", load),
        ("cards", cards),
        ("turn", turn),
        ("respond", respond),
        ("guard", guard),
        ("stance_check", stance_check),
        ("wait_typing", wait_typing),
        ("apply", apply),
        ("fill", fill),
    ):
        builder.add_node(name, node)
    builder.add_edge(START, "load")
    builder.add_conditional_edges("load", after_load, ["cards", END])
    builder.add_edge("cards", "turn")
    builder.add_conditional_edges("turn", after_turn, ["respond", "fill"])
    builder.add_edge("respond", "guard")
    builder.add_edge("guard", "stance_check")
    builder.add_edge("stance_check", "wait_typing")
    builder.add_conditional_edges("wait_typing", after_wait, ["apply", END])
    builder.add_conditional_edges("apply", after_apply, ["respond", "fill", END])
    builder.add_edge("fill", END)
    return builder.compile(checkpointer=checkpointer)
