"""진행 매니저의 결정적 규칙(docs/architecture/agents.md "진행 매니저 규칙"·"카드별
실행"·"stance_plan").

LLM 없는 순수 함수만 둔다. 입력은 DebateState(docs/contracts/internal.md, load가 읽은
값)·카탈로그·자극·의도
분류.
원칙 "결정은 결정적 코드가, 말투는 LLM이".
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.catalog import DEBATE_PHASES, SEATS, Catalog

MAX_SPEAKERS = 2
MAX_PRESSURE = 3
CONSECUTIVE_LIMIT = 3

# 카드 지시(담당 자리에게만). 카드 이름·요소 이름·다른 자리 성향은 넣지 않는다.
CARD_INSTRUCTIONS: dict[str, str] = {
    "persuasion_keyword": (
        "자기 키워드를 이유 한 가지와 함께 미세요. 지원자가 낸 안이 있으면 그 안의 약점 하나를 "
        "물어보세요. 지원자가 아직 안을 내지 않았으면 모두에게 자기 키워드를 미세요."
    ),
    "flexibility_majority": "[내 입장 계획]의 안을 지지한다고 분명하게 밝히세요.",
    "listening_pressure": (
        '[내 입장 계획]의 안을 강하게 미세요. 지원자 안은 짧게 일축하고("그건 핵심이 아니에요") '
        "같은 근거를 반복하세요. 욕설·인신공격·비하는 하지 않습니다."
    ),
    "persuasion_solution": (
        "지원자 안의 약점(비용·실현 가능성 등) 하나를 자료 근거로 짚고 [내 입장 계획]의 안을 "
        "미세요. 지원자가 아직 안을 내지 않았으면 모두에게 자기 안을 미세요."
    ),
}
PRESSURE_FOLLOWUP = (
    "지원자가 반박하면 [내 입장 계획]의 안으로 다시 짧게 재반박하세요. 같은 근거를 반복하고 "
    "인신공격·비하는 하지 않습니다."
)
# 카드 담당이 아닌 자리가 그 단계에서 지킬 선점 금지(docs/product/scenario.md 4.2를 중립 문장으로)
PREEMPT_BANS: dict[str, str] = {
    "persuasion_keyword": "다른 사람 대신 지원자 안을 요약하거나 변호하지 않는다.",
    "persuasion_solution": "다른 사람 대신 지원자 안을 요약하거나 변호하지 않는다.",
    "flexibility_majority": (
        "지원자에게 승복을 권하거나 다음 단계에서 보상하자는 제안을 먼저 하지 않는다."
    ),
    "listening_pressure": "강하게 주장하는 동료를 말리거나 지원자 편을 들어 중재하지 않는다.",
    "inclusion_silence": "머뭇거린 동료에게 발언 기회를 주거나 그 동료의 이름을 부르지 않는다.",
    "honesty_credit": "누가 어떤 아이디어를 냈는지 먼저 말하거나 기여 칸 작성을 권하지 않는다.",
}


# ---- DebateState 읽기 ------------------------------------------------------------------------
def options(ds: dict[str, Any]) -> list[dict[str, Any]]:
    return list(ds.get("options") or [])


def option_by_id(ds: dict[str, Any], option_id: str | None) -> dict[str, Any] | None:
    return next((o for o in options(ds) if o["option_id"] == option_id), None)


def option_by_key(ds: dict[str, Any], key: str | None) -> dict[str, Any] | None:
    if not key:
        return None
    return next((o for o in options(ds) if o.get("scenario_option_key") == key), None)


def support_of(ds: dict[str, Any], participant: str) -> str | None:
    return (ds.get("current_support") or {}).get(participant)


def key_of(ds: dict[str, Any], option_id: str | None) -> str | None:
    option = option_by_id(ds, option_id)
    return option.get("scenario_option_key") if option else None


def keyword_key(ds: dict[str, Any], catalog: Catalog) -> str | None:
    """확정된 키워드 칸의 키(문제 상황·해결책 안 키의 접두사)."""
    for slot in ds.get("confirmed") or []:
        if slot.get("phase") == "keyword":
            return catalog.keyword_key_for(slot.get("value"))
    return None


def card_progress(ds: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["card_id"]: c for c in ds.get("card_progress") or []}


def slot_empty(ds: dict[str, Any]) -> bool:
    return ((ds.get("slot") or {}).get("status") or "empty") == "empty"


# ---- 기준안 ---------------------------------------------------------------------------------
def lead_key(ds: dict[str, Any], catalog: Catalog) -> str | None:
    """문제 상황 기준안 = 지원자 현재 지지 안이 아닌 첫 시나리오 안(P1 → P2 → P3)."""
    kw = keyword_key(ds, catalog)
    if kw is None:
        return None
    rule = catalog.mission.get("lead_option") or {}
    order = [f"{kw}_{p}" for p in rule.get("order", ["p1", "p2", "p3"])]
    candidate_key = key_of(ds, support_of(ds, "candidate"))
    return next((k for k in order if k != candidate_key), order[0])


def locked_lead(ds: dict[str, Any], catalog: Catalog) -> dict[str, Any] | None:
    """잠긴 기준안 {option_id, key}. api가 잠갔으면 그 값, 카드가 시작됐는데 잠금이 없으면
    계산값.
    """
    progress = card_progress(ds)
    lock_card = (catalog.mission.get("lead_option") or {}).get("lock_card", "flexibility_majority")
    for card_id in (lock_card, "listening_pressure"):
        locked = (progress.get(card_id) or {}).get("locked_option_id")
        if locked:
            return {"option_id": locked, "key": key_of(ds, locked)}
    return None


def held_lead(ds: dict[str, Any], catalog: Catalog) -> dict[str, Any] | None:
    """api 잠금 값이 없을 때, 카드가 이미 시작됐으면 고집형의 현재 지지 안(확정 전 불변)이
    기준안이다.
    """
    stubborn = catalog.seat_of("stubborn")
    option_id = support_of(ds, stubborn) if stubborn else None
    return {"option_id": option_id, "key": key_of(ds, option_id)} if option_id else None


# ---- 입장 계획 -------------------------------------------------------------------------------
@dataclass
class StancePlan:
    seat: str
    phase: str
    key: str | None = None  # 계획 안의 scenario_option_key(지원자 안으로 옮기면 None일 수 있음)
    option_id: str | None = None  # 행이 이미 있으면 그 id
    label: str | None = None
    declared: bool = True  # False = 아직 입장을 밝히지 않음(문제 상황 카드 전, 기획안 단계)
    can_move: bool = False
    moved: bool = False
    note: str = ""

    @property
    def targets(self) -> set[str]:
        return {v for v in (self.key, self.option_id) if v}

    @property
    def fallback_key(self) -> str:
        if self.phase == "problem" and not self.declared:
            return "problem_before_card"
        return self.phase


@dataclass
class PlanContext:
    ds: dict[str, Any]
    catalog: Catalog
    card_active: bool  # 문제 상황 카드(기준안 잠금)가 시작됐는지(이번 run 포함)
    lead: dict[str, Any] | None  # {option_id, key}
    counter_count: int = 0  # 지원자가 비판형 계획 안에 반대·대안 제안한 횟수(high)
    extra: dict[str, Any] = field(default_factory=dict)


def _base_key(seat: str, phase: str, ctx: PlanContext) -> tuple[str | None, bool]:
    """(그 단계 고정 안의 키, 입장을 밝히는지)."""
    stances = (ctx.catalog.persona(seat).get("stances") or {}).get(phase) or {}
    if phase == "keyword":
        return stances.get("push"), True
    kw = keyword_key(ctx.ds, ctx.catalog)
    if phase == "problem":
        if not ctx.card_active or ctx.lead is None:
            return None, False
        lead = ctx.lead.get("key")
        if stances.get("after_card") == "last_non_lead" and kw:
            order = [f"{kw}_{p}" for p in ("p1", "p2", "p3")]
            rest = [k for k in order if k != lead]
            return (rest[-1] if rest else lead), True
        return lead, True
    if phase == "solution":
        push = stances.get("push")
        return (f"{kw}_{push}" if kw and push else None), bool(push)
    return None, False


def stance_plan(seat: str, phase: str, ctx: PlanContext) -> StancePlan:
    persona = ctx.catalog.persona(seat)
    key, declared = _base_key(seat, phase, ctx)
    plan = StancePlan(seat=seat, phase=phase, key=key, declared=declared and key is not None)
    persuadable = persona.get("persuadable") or {}
    plan.can_move = bool(persuadable) and phase in (persuadable.get("stages") or [])
    if plan.declared:
        option = option_by_key(ctx.ds, key)
        if phase == "problem" and ctx.lead and key == ctx.lead.get("key"):
            plan.option_id = ctx.lead.get("option_id") or (option or {}).get("option_id")
        else:
            plan.option_id = (option or {}).get("option_id")
        if plan.can_move:
            own = support_of(ctx.ds, seat)
            candidate = support_of(ctx.ds, "candidate")
            if own and not _same(ctx.ds, own, plan):
                # 이미 지원자 안으로 옮김(단계당 1회, 그 뒤 고정)
                plan.option_id, plan.key, plan.moved = own, key_of(ctx.ds, own), True
            elif (
                ctx.counter_count >= int(persuadable.get("count", 2))
                and candidate
                and not _same(ctx.ds, candidate, plan)
            ):
                plan.option_id, plan.key, plan.moved = candidate, key_of(ctx.ds, candidate), True
    if plan.option_id:
        option = option_by_id(ctx.ds, plan.option_id)
        plan.label = (option or {}).get("label")
    if plan.label is None and plan.key:
        plan.label = ctx.catalog.label_of(plan.key)
    return plan


def _same(ds: dict[str, Any], option_id: str, plan: StancePlan) -> bool:
    return option_id in plan.targets or key_of(ds, option_id) in plan.targets


def counter_count(intents: list[dict[str, Any]], base_targets: set[str]) -> int:
    """설득 가능 규칙의 횟수: 그 안 대상 oppose 또는 다른 안 propose(confidence high)."""
    count = 0
    for result in intents:
        if result.get("confidence") != "high":
            continue
        target = result.get("target_option_id") or result.get("scenario_option_key")
        if result.get("intent") == "oppose" and target and target in base_targets:
            count += 1
        elif result.get("intent") == "propose" and (
            (target and target not in base_targets) or result.get("new_option_label")
        ):
            count += 1
    return count


# ---- 카드 ------------------------------------------------------------------------------------
def card_actors(card: dict[str, Any], ds: dict[str, Any], catalog: Catalog) -> list[str]:
    """카드 담당 자리(docs/product/scenario.md 4.2, cards.yaml actor_mode)."""
    mode = card.get("actor_mode")
    actor = card.get("actor")
    if mode == "none" or not actor:
        return []
    actors = actor if isinstance(actor, list) else [actor]
    seats = catalog.seats_by_priority(actors)
    if mode in ("single", "joint"):
        return seats
    candidate_key = key_of(ds, support_of(ds, "candidate"))
    if mode == "priority":
        for seat in seats:
            push = ((catalog.persona(seat).get("stances") or {}).get("keyword") or {}).get("push")
            if push != candidate_key:
                return [seat]
        return seats[:1]
    if mode == "conditional":
        rule = card.get("actor_rule") or {}
        default = catalog.seat_of(rule.get("default", ""))
        then = catalog.seat_of(rule.get("then", ""))
        kw = keyword_key(ds, catalog)
        critic = catalog.seat_of("critic")
        push = (
            ((catalog.persona(critic).get("stances") or {}).get("solution") or {}).get("push")
            if critic
            else None
        )
        critic_key = f"{kw}_{push}" if kw and push else None
        if critic_key and candidate_key == critic_key and then:
            return [then]
        return [default] if default else seats[:1]
    return seats[:1]


@dataclass
class CardSpeaker:
    seat: str
    card_ids: list[str]


def card_brief(stimulus: dict[str, Any], ds: dict[str, Any], catalog: Catalog) -> list[CardSpeaker]:
    """이번 run에서 카드 발언을 할 자리와 그 발언이 전달하는 카드들.

    card_due면 그 카드 + 같은 단계에서 예정 시각이 같은(같은 offset) 미전달 카드를 함께(공동 처리),
    모든 자극에서 이미 시작됐지만 담당 전원이 전달하지 않은 카드의 남은 화자를 이어서 넣는다.
    """
    phase = stimulus["phase"]
    if phase not in DEBATE_PHASES:
        return []
    progress = card_progress(ds)
    targets: list[dict[str, Any]] = []
    if stimulus.get("type") == "card_due" and stimulus.get("card_id"):
        base = catalog.card(stimulus["card_id"])
        if base and base["stage_id"] == phase:
            for card in catalog.cards_in(phase):
                if card["card_id"] == base["card_id"] or (
                    card.get("offset_min") is not None
                    and card.get("offset_min") == base.get("offset_min")
                ):
                    targets.append(card)
    for card in catalog.cards_in(phase):
        if card["card_id"] in progress and card not in targets:
            targets.append(card)
    by_seat: dict[str, list[str]] = {}
    for card in targets:
        delivered = set((progress.get(card["card_id"]) or {}).get("delivered") or [])
        for seat in card_actors(card, ds, catalog):
            if seat not in delivered:
                by_seat.setdefault(seat, []).append(card["card_id"])
    order = [s for s in SEATS if s in by_seat]
    # 카드 순서상 먼저 나오는 카드의 첫 담당이 먼저 말한다
    first_card = {
        seat: min(catalog_order(catalog, c) for c in ids) for seat, ids in by_seat.items()
    }
    order.sort(key=lambda s: (first_card[s], _actor_rank(catalog, by_seat[s][0], s)))
    return [CardSpeaker(seat, by_seat[seat]) for seat in order]


def catalog_order(catalog: Catalog, card_id: str) -> int:
    ids = [c["card_id"] for c in catalog.cards.get("cards", [])]
    return ids.index(card_id) if card_id in ids else len(ids)


def _actor_rank(catalog: Catalog, card_id: str, seat: str) -> int:
    card = catalog.card(card_id) or {}
    actor = card.get("actor") or []
    actors = actor if isinstance(actor, list) else [actor]
    ptype = catalog.type_of(seat)
    return actors.index(ptype) if ptype in actors else len(actors)


def first_delivery(card_id: str, ds: dict[str, Any]) -> bool:
    """이 카드의 첫 카드 발언인지(아직 아무 자리도 전달하지 않음)."""
    return not ((card_progress(ds).get(card_id) or {}).get("delivered"))


def preempt_bans(
    seat: str, phase: str, ds: dict[str, Any], catalog: Catalog, brief: list[CardSpeaker]
) -> list[str]:
    """그 자리가 지킬 선점 금지(그 단계에서 시작된 카드 중 자기가 담당이 아닌 것)."""
    started = set(card_progress(ds)) | {c for s in brief for c in s.card_ids}
    if phase == "proposal":
        started.add("honesty_credit")
    bans: list[str] = []
    for card in catalog.cards_in(phase):
        cid = card["card_id"]
        if cid not in started:
            continue
        if seat in card_actors(card, ds, catalog):
            continue
        text = PREEMPT_BANS.get(cid)
        if text and text not in bans:
            bans.append(text)
    return bans


# ---- 진행 매니저 -------------------------------------------------------------------------------
def fill_seat(ds: dict[str, Any], option_id: str, catalog: Catalog) -> str | None:
    """합의 안 지지자 중 우선순위(critic > quiet > stubborn) 첫 자리."""
    priority = (catalog.mission.get("consensus_fill") or {}).get(
        "filler_priority", ["critic", "quiet", "stubborn"]
    )
    supporters = [s for s in SEATS if support_of(ds, s) == option_id]
    for seat in catalog.seats_by_priority(priority):
        if seat in supporters:
            return seat
    return supporters[0] if supporters else None


def speaker(
    seat: str, purpose: str, option_id: str | None = None, card_ids: list[str] | None = None
) -> dict[str, Any]:
    return {"seat": seat, "purpose": purpose, "option_id": option_id, "card_ids": card_ids or []}


def recent_streak_seat(transcript: list[dict[str, Any]]) -> str | None:
    """마지막 CONSECUTIVE_LIMIT개 발언이 모두 같은 AI 자리면 그 자리(연속 3회 금지)."""
    tail = [m.get("sender") for m in transcript[-CONSECUTIVE_LIMIT:]]
    if len(tail) == CONSECUTIVE_LIMIT and len(set(tail)) == 1 and tail[0] in SEATS:
        return tail[0]
    return None


def called_seats(text: str, addressed: list[str], catalog: Catalog) -> list[str]:
    """지명 응답 순서: 메시지에서 "이름님"으로 먼저 부른 자리부터, 분류 addressed_to의 나머지는
    뒤에. 분류가 놓친 호명도 넣는다(분류 순서가 호명 순서와 달라 다른 자리가 먼저 답하던 문제).
    """
    found: list[tuple[int, str]] = []
    for seat in SEATS:
        name = catalog.name_of(seat)
        given = name[1:] if len(name) >= 3 else name
        hit = re.search(f"(?:{re.escape(name)}|{re.escape(given)})\\s*님", text)
        if hit:
            found.append((hit.start(), seat))
    order = [seat for _, seat in sorted(found)]
    return order + [s for s in addressed if s in SEATS and s not in order]


def rule_decision(
    stimulus: dict[str, Any],
    ds: dict[str, Any],
    catalog: Catalog,
    brief: list[CardSpeaker],
    intent: dict[str, Any] | None,
    transcript: list[dict[str, Any]],
) -> dict[str, Any]:
    """규칙 1~5의 결과. speakers가 비고 자극이 message면 호출자가 턴 LLM(규칙 6)을 부른다."""
    phase = stimulus["phase"]
    kind = stimulus["type"]
    decision: dict[str, Any] = {
        "stimulus": kind,
        "generation": stimulus.get("generation"),
        "speakers": [],
        "fill": None,
        "invite": False,
        "llm_used": False,
        "dropped_seats": [],
        "reason": "",
    }
    # 1. 이미 합의가 있으면 채우기만(화자 0명)
    consensus = ds.get("consensus_option_id")
    if consensus and slot_empty(ds) and phase in DEBATE_PHASES:
        seat = fill_seat(ds, consensus, catalog)
        if seat:
            decision["fill"] = {"option_id": consensus, "seat": seat}
            decision["reason"] = "합의 성립(지원자 포함 3/4), 채우기만"
            return decision
    speakers: list[dict[str, Any]] = []
    # 2. 카드 화자(미전달 화자 이어 말하기 포함)
    for item in brief[:MAX_SPEAKERS]:
        speakers.append(speaker(item.seat, "card", None, list(item.card_ids)))
    if kind == "card_due":
        decision["speakers"] = speakers
        decision["reason"] = "카드 예정 시각" if speakers else "카드 담당 전원 전달됨"
        return decision
    used = {s["seat"] for s in speakers}
    streak = recent_streak_seat(transcript)
    quiet = catalog.seat_of("quiet")
    # 3. 지명 응답(최대 2명). 소극형은 이름이 불렸거나 전체 질문일 때만
    if kind == "message" and intent:
        text = next(
            (
                str(m.get("content") or "")
                for m in transcript
                if str(m.get("id")) == str(stimulus.get("message_id"))
            ),
            "",
        )
        named = called_seats(text, intent.get("addressed_to") or [], catalog)
        if intent.get("intent") == "question" and not named and phase in DEBATE_PHASES:
            named = [s for s in (quiet, catalog.seat_of("critic")) if s]
            decision["reason"] = "전체 질문"
        for seat in named:
            if seat not in used and len(speakers) < MAX_SPEAKERS:
                speakers.append(speaker(seat, "respond"))
                used.add(seat)
        if named and not decision["reason"]:
            decision["reason"] = "지원자가 이름을 부름"
    # 4. 침묵 초대(단계당 1회, 고정 자리에서는 비판형)
    if kind == "idle" and phase in DEBATE_PHASES and not ds.get("invite_delivered"):
        inviter = catalog.seats_by_priority(["agreeable", "divergent", "critic"])
        if inviter and inviter[0] not in used and len(speakers) < MAX_SPEAKERS:
            speakers.append(speaker(inviter[0], "invite"))
            decision["invite"] = True
            decision["reason"] = "지원자 침묵"
    # 5. 단계 시작: 고정 입장 자리(소극형 제외). 문제 상황은 카드 전이라 고집형 일반 발언만
    if kind == "phase_started":
        if phase == "problem":
            stubborn = catalog.seat_of("stubborn")
            if stubborn and stubborn not in used:
                speakers.append(speaker(stubborn, "react"))
        elif phase in ("keyword", "solution"):
            for seat in catalog.seats_by_priority(["stubborn", "critic"]):
                if seat not in used and seat != streak and len(speakers) < MAX_SPEAKERS:
                    speakers.append(speaker(seat, "push_option"))
                    used.add(seat)
        decision["reason"] = decision["reason"] or "단계 시작"
    decision["speakers"] = speakers[:MAX_SPEAKERS]
    return decision


def validate_turn_llm(
    out: dict[str, Any], catalog: Catalog, transcript: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """턴 LLM 출력 검증. 허용 밖은 버린다(소극형·연속 3회 자리·목적 밖)."""
    quiet = catalog.seat_of("quiet")
    streak = recent_streak_seat(transcript)
    result: list[dict[str, Any]] = []
    for item in out.get("speakers") or []:
        if not isinstance(item, dict):
            continue
        seat, purpose = item.get("seat"), item.get("purpose")
        if seat not in SEATS or seat == quiet or seat == streak:
            continue
        if purpose not in ("respond", "push_option", "react"):
            continue
        if any(s["seat"] == seat for s in result):
            continue
        option_id = item.get("option_id")
        result.append(speaker(seat, purpose, option_id if isinstance(option_id, str) else None))
        if len(result) == MAX_SPEAKERS:
            break
    return result


def pressure_count(transcript: list[dict[str, Any]], seat: str, phase: str) -> int:
    return sum(1 for m in transcript if m.get("sender") == seat and m.get("phase") == phase)
