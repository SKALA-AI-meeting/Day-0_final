"""진행 매니저의 결정적 규칙(docs/architecture/agents.md "진행 매니저 규칙"·"카드별
실행"·"stance_plan").

LLM 없는 순수 함수만 둔다. 입력은 DebateState(docs/contracts/internal.md, load가 읽은
값)·카탈로그·자극·의도
분류.
원칙 "결정은 결정적 코드가, 말투는 LLM이".
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.agent.catalog import DEBATE_PHASES, SEATS, Catalog

MAX_SPEAKERS = 2
MAX_PRESSURE = 3
CONSECUTIVE_LIMIT = 3

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
    counter_count: int = 0  # 지원자가 비판형 계획 안이 아닌 안을 지지로 세는 발언으로 낸 횟수
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


def counter_count(
    intents: list[dict[str, Any]], base_targets: set[str], rule: dict[str, Any] | None = None
) -> int:
    """설득 가능 규칙의 횟수(personas.yaml `persuadable`, B2).

    세는 발언은 규칙의 `intents`(의도와 대상 `own_option`∣`candidate_option`) 중 하나이고
    confidence가 규칙 값(기본 high)이며 대상 안에 유보 표현이 없는 것이다. `candidate_option`은
    비판형 계획 안이 아닌 안(새 안 포함)이다. 반대만 한 발언은 규칙에 없으면 세지 않는다.
    """
    rule = rule or {}
    specs = rule.get("intents") or [
        {"intent": "propose", "target": "candidate_option"},
        {"intent": "agree", "target": "candidate_option"},
    ]
    confidence = rule.get("confidence", "high")
    count = 0
    for result in intents:
        if result.get("confidence") != confidence or result.get("reservation"):
            continue
        target = result.get("target_option_id") or result.get("scenario_option_key")
        own = bool(target) and target in base_targets
        other = (bool(target) and not own) or bool(result.get("new_option_label"))
        for spec in specs:
            if spec.get("intent") != result.get("intent"):
                continue
            if (spec.get("target") == "own_option" and own) or (
                spec.get("target") == "candidate_option" and other
            ):
                count += 1
                break
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
    seat: str,
    *,
    reply_to: str | None = None,
    card_ids: list[str] | None = None,
    invite: bool = False,
) -> dict[str, Any]:
    """진행 매니저 결정의 화자 한 명(docs/contracts/internal.md "진행 매니저 결정 스키마").

    정하는 것은 누가(`seat`) 어떤 메시지에 답하는지(`reply_to`)와 그 자리의 차례(카드 `card_ids`,
    침묵 초대 `invite`)뿐이다. 무엇을 어떻게 말할지는 토론 동료가 정한다(C1).
    """
    return {
        "seat": seat,
        "reply_to": reply_to,
        "card_ids": list(card_ids or []),
        "invite": invite,
    }


def recent_streak_seat(transcript: list[dict[str, Any]]) -> str | None:
    """마지막 CONSECUTIVE_LIMIT개 발언이 모두 같은 AI 자리면 그 자리(연속 3회 금지)."""
    tail = [m.get("sender") for m in transcript[-CONSECUTIVE_LIMIT:]]
    if len(tail) == CONSECUTIVE_LIMIT and len(set(tail)) == 1 and tail[0] in SEATS:
        return tail[0]
    return None


Names = (
    Catalog | dict[str, str]
)  # 카탈로그 또는 {자리: 표시 이름}(api가 채용 건 복사본에서 만든 값)


def _name_forms(seat: str, catalog: Names) -> list[str]:
    """자리 표시 이름의 두 형태(성까지 붙인 이름, 세 글자 이상이면 성을 뗀 이름)."""
    name = catalog.name_of(seat) if isinstance(catalog, Catalog) else catalog.get(seat, seat)
    return [name, name[1:]] if len(name) >= 3 else [name]


def named_seats(text: str, catalog: Names, *, honorific: bool = True) -> list[str]:
    """메시지에서 이름으로 부른 자리(메시지에 나온 순서). honorific이면 "이름님"만, 아니면 "님"
    유무와 상관없이 이름이 나온 자리."""
    found: list[tuple[int, str]] = []
    for seat in SEATS:
        forms = "|".join(re.escape(f) for f in _name_forms(seat, catalog))
        pattern = f"(?:{forms})\\s*님" if honorific else f"(?:{forms})"
        hit = re.search(pattern, text)
        if hit:
            found.append((hit.start(), seat))
    return [seat for _, seat in sorted(found)]


def excluded_seats(text: str, excluded: list[str] | None, catalog: Names) -> list[str]:
    """분류 excluded 중 메시지에 이름이 실제로 나온 자리만("님" 유무 무관).

    뺀다는 것은 그 동료를 이름으로 가리켰다는 사실을 전제한다. "3번은 보류"의 안 번호를 자리로
    읽은 분류는 기록과 맞지 않아 버린다.
    """
    named = set(named_seats(text, catalog, honorific=False))
    return [s for s in excluded or [] if s in named]


def called_seats(
    text: str, addressed: list[str], catalog: Names, excluded: list[str] | None = None
) -> list[str]:
    """지명 응답 자리와 순서.

    대상 = (분류 addressed_to ∪ 메시지에서 "이름님"으로 부른 자리) − 분류 excluded. 부른 자리는
    지원자가 답하지 말라고 뺀 자리(excluded)가 아니면 빠지지 않는다(C1).
    순서 = addressed_to 자리 먼저(메시지에 나온 순서), 그다음 나머지 부른 자리(메시지 순서).
    "지안님 말고 하람님"은 분류가 지안님을 excluded로 내므로 하람님만 답한다.
    """
    skip = set(excluded or [])
    order = [seat for seat in named_seats(text, catalog) if seat not in skip]
    targets = [s for s in addressed if s in SEATS and s not in skip]
    first = [s for s in order if s in targets] + [s for s in targets if s not in order]
    return first + [s for s in order if s not in first]


def message_text(stimulus: dict[str, Any], transcript: list[dict[str, Any]]) -> str:
    """자극 메시지의 본문(기록에 없으면 빈 문자열)."""
    return next(
        (
            str(m.get("content") or "")
            for m in transcript
            if str(m.get("id")) == str(stimulus.get("message_id"))
        ),
        "",
    )


# ---- 답할 메시지와 상황 정보(C1) -----------------------------------------------------------------
def intent_map(
    ds: dict[str, Any], stimulus: dict[str, Any], intent: dict[str, Any] | None
) -> dict[str, dict[str, Any]]:
    """지원자 메시지 id → 저장된 의도 분류(DebateState `candidate_intents`) + 이번 자극의 분류.

    원천이 DB라 재시작·다른 프로세스에서도 같다.
    """
    found = {str(r.get("message_id")): r for r in ds.get("candidate_intents") or []}
    if intent and stimulus.get("message_id"):
        mid = str(stimulus["message_id"])
        # 이번 분류가 저장 행을 대신하되 메시지 id·저장 시각은 남긴다(상황 사실 계산용)
        found[mid] = {**(found.get(mid) or {}), **intent, "message_id": mid}
    return found


def addressed(
    message: dict[str, Any], intents: dict[str, dict[str, Any]], catalog: Names
) -> list[str]:
    """지원자 메시지가 부른 자리(`called_seats`).

    분류 addressed_to ∪ "이름님" − 이름이 나온 excluded. 분류 행이 없으면 메시지의 "이름님"만 본다.
    """
    if message.get("sender") != "candidate":
        return []
    text = str(message.get("content") or "")
    intent = intents.get(str(message.get("id"))) or {}
    excluded = excluded_seats(text, intent.get("excluded"), catalog)
    return called_seats(text, intent.get("addressed_to") or [], catalog, excluded)


def open_mentions(
    messages: list[dict[str, Any]], intents: dict[str, dict[str, Any]], catalog: Names
) -> list[dict[str, str]]:
    """아직 답하지 않은 지명 `[{seat, message_id, since, sender, content}]`.

    `messages`는 그 단계 토론방 메시지 전체(저장 순서, AI 발언은 `reply_to` 포함)다. 지원자
    메시지가 자리를 부를 때마다 그 자리의 열린 지명이 쌓이고, 그 자리가 어떤 지원자 메시지에
    답하면(`reply_to`) 그 메시지와 그 앞의 지명이 모두 닫힌다. `message_id`는 답할 메시지(그 자리의
    가장 최근 열린 지명)이고 `sender`·`content`는 그 보낸 사람·본문, `since`는 가장 오래된 열린
    지명이다. 순서는 `since`가 오래된 자리 먼저(같은 메시지 안에서는 부른 순서)라 화자 상한(2)을
    넘어 밀린 자리가 다음 실행에서 먼저 답하고, 계속 불려도 굶지 않는다.
    """
    pending: dict[str, list[tuple[int, int, str]]] = {}
    index_of: dict[str, int] = {}
    for index, message in enumerate(messages):
        message_id = str(message.get("id"))
        index_of[message_id] = index
        sender = message.get("sender")
        if sender == "candidate":
            for position, seat in enumerate(addressed(message, intents, catalog)):
                pending.setdefault(seat, []).append((index, position, message_id))
        elif sender in SEATS and message.get("reply_to"):
            target = index_of.get(str(message["reply_to"]))
            if target is not None and messages[target].get("sender") == "candidate":
                pending[sender] = [p for p in pending.get(sender, []) if p[0] > target]
    rows = sorted(
        (marks[0][0], marks[0][1], seat, marks[-1][2], marks[0][2])
        for seat, marks in pending.items()
        if marks
    )
    return [
        {
            "seat": seat,
            "message_id": mid,
            "since": first,
            "sender": str(messages[index_of[mid]].get("sender")),
            "content": str(messages[index_of[mid]].get("content") or ""),
        }
        for _, _, seat, mid, first in rows
    ]


def pending_mentions(
    ds: dict[str, Any],
    stimulus: dict[str, Any],
    transcript: list[dict[str, Any]],
    intents: dict[str, dict[str, Any]],
    catalog: Catalog,
) -> list[dict[str, str]]:
    """이번 실행이 볼 열린 지명.

    api가 그 단계 전체 기록에서 계산한 DebateState `open_mentions`를 쓰고(대화 창 30개에 묶이지
    않음), 없으면(이전 api) 대화 창에서 계산한다. 이번 지원자 메시지의 분류가 아직 저장되지 않았을
    수 있어 그 메시지가 부른 자리는 이번 분류로 더한다(이미 있는 자리는 답할 메시지만 이번 것으로).
    """
    if "open_mentions" in ds:
        found = [dict(row) for row in ds.get("open_mentions") or []]
    else:
        found = open_mentions(transcript, intents, catalog)
    current = next(
        (
            m
            for m in transcript
            if stimulus.get("type") == "message"
            and str(m.get("id")) == str(stimulus.get("message_id"))
        ),
        None,
    )
    if current is not None:
        mid = str(current.get("id"))
        for seat in addressed(current, intents, catalog):
            row = next((r for r in found if r["seat"] == seat), None)
            text = {"sender": "candidate", "content": str(current.get("content") or "")}
            if row is None:
                found.append({"seat": seat, "message_id": mid, "since": mid, **text})
            else:
                row.update({"message_id": mid, **text})
    return found


def reactions(
    seat: str,
    transcript: list[dict[str, Any]],
    intents: dict[str, dict[str, Any]],
    catalog: Catalog,
) -> list[str] | None:
    """이 자리의 마지막 발언 뒤 그 발언에 반응한 참여자(나온 순서).

    반응 = 그 발언에 답한 다른 동료 발언(`reply_to`가 그 발언) 또는 이 자리를 부른 지원자 메시지.
    이 단계에서 이 자리가 아직 말하지 않았으면 None.
    """
    index = next(
        (i for i in range(len(transcript) - 1, -1, -1) if transcript[i].get("sender") == seat),
        None,
    )
    if index is None:
        return None
    mine = str(transcript[index].get("id"))
    found: list[str] = []
    for message in transcript[index + 1 :]:
        sender = message.get("sender")
        if sender == "candidate":
            hit = seat in addressed(message, intents, catalog)
        else:
            hit = sender in SEATS and sender != seat and str(message.get("reply_to")) == mine
        if hit and sender not in found:
            found.append(str(sender))
    return found


def intents_toward(
    rows: list[dict[str, Any]],
    targets: set[str],
    since: Any,
    ds: dict[str, Any],
    catalog: Catalog,
    transcript: list[dict[str, Any]],
) -> dict[str, Any]:
    """내가 입장을 밝힌 뒤(since) 지원자 메시지의 저장 의도를 내 지지 안 기준으로 센 사실(C2).

    `toward_mine`: 대상이 내 지지 안인 `oppose`·`withhold`·`question` 수와 마지막 메시지 본문(대화
    창에 있을 때만). `counters`: 내 안이 아닌 안을 지지로 세는 발언(`counter_count`와 같은 규칙,
    high·유보 없음)의 수와 그 안 라벨. 뜻은 저장된 분류가, 횟수는 코드가 정한다. 낱말 검사 없음.
    """
    toward = {"oppose": 0, "withhold": 0, "question": 0, "last": None}
    labels: list[str] = []
    text_of = {str(m.get("id")): str(m.get("content") or "") for m in transcript}
    for row in rows if since is not None else []:
        # 저장 시각이 없는 행은 이번 자극 메시지(가장 최신)라 센다
        if _after(row.get("created_at"), since) is False:
            continue
        target = row.get("target_option_id") or row.get("scenario_option_key")
        mine = bool(target) and (target in targets or key_of(ds, target) in targets)
        intent = row.get("intent")
        if mine and intent in ("oppose", "withhold", "question"):
            toward[intent] += 1
            toward["last"] = text_of.get(str(row.get("message_id"))) or toward["last"]
            continue
        if (
            intent in ("propose", "agree")
            and row.get("confidence") == "high"
            and not row.get("reservation")
            and ((target and not mine) or row.get("new_option_label"))
        ):
            option = option_by_id(ds, target) or option_by_key(ds, target)
            label = (
                (option or {}).get("label")
                or catalog.label_of(str(target or ""))
                or row.get("new_option_label")
            )
            if label and label not in labels:
                labels.append(str(label))
    return {"toward_mine": toward, "counters": {"n": len(labels), "labels": labels}}


def _after(value: Any, since: Any) -> bool | None:
    """value가 since보다 뒤인지(둘 다 ISO 시각). 비교할 수 없으면 None."""
    try:
        a = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return a > b


def situation(
    seat: str,
    spk: dict[str, Any],
    reply_to: str | None,
    transcript: list[dict[str, Any]],
    intents: dict[str, dict[str, Any]],
    catalog: Catalog,
    mentions: list[dict[str, Any]] | None = None,
    *,
    ds: dict[str, Any] | None = None,
    stance_targets: set[str] | None = None,
    since: Any = None,
    move: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """토론 동료에게 줄 상황 정보(사실만, 지시 없음, C1·C2).

    답할 메시지가 대화 창(최근 30개) 밖이면 DebateState `open_mentions`에 api가 담은 그 메시지의
    보낸 사람·본문을 쓴다(창 밖 지명이어도 "답할 메시지: 없음"이 되지 않게).

    - `reply_to`: 답할 메시지 `{id, sender, content}` 또는 None
    - `named`: 답할 메시지가 이 자리를 부른 지원자 메시지인지
    - `card_turn`: 이번 발언이 이 자리의 카드 차례인지(`card_brief`가 정한 카드가 있음)
    - `invite`: 침묵 초대 차례인지
    - `reactions`: 이 자리의 마지막 발언 뒤 반응한 참여자(`reactions`, 아직 말하지 않았으면 None)
    - `toward_mine`·`counters`: 내 지지 안(`stance_targets`)에 대한 지원자의 저장 의도 수
      (`intents_toward`, 이 자리의 단계 첫 발언 `since` 뒤). 입장이 없으면 None
    - `move`: 지지가 옮겨졌는데 아직 말로 밝히지 않았을 때 `{from, to}`
    """
    target = next((m for m in transcript if reply_to and str(m.get("id")) == reply_to), None)
    if target is None and reply_to:
        row = next((r for r in mentions or [] if str(r.get("message_id")) == reply_to), None)
        if row is not None:
            target = {"id": reply_to, "sender": row.get("sender"), "content": row.get("content")}
    found: dict[str, Any] = {
        "reply_to": None
        if target is None
        else {
            "id": str(target.get("id")),
            "sender": target.get("sender"),
            "content": str(target.get("content") or ""),
        },
        "named": target is not None and seat in addressed(target, intents, catalog),
        "card_turn": bool(spk.get("card_ids")),
        "invite": bool(spk.get("invite")),
        "reactions": reactions(seat, transcript, intents, catalog),
        "toward_mine": None,
        "counters": None,
        "move": move,
    }
    if stance_targets and ds is not None:
        # 저장 시각이 없는 행은 이번 자극 메시지라 맨 뒤(가장 최신)
        rows = sorted(intents.values(), key=lambda r: str(r.get("created_at") or "~"))
        found.update(intents_toward(rows, stance_targets, since, ds, catalog, transcript))
    return found


def default_responder(
    transcript: list[dict[str, Any]],
    message_id: str,
    catalog: Catalog,
    skip: set[str],
) -> str | None:
    """지원자 메시지에 답할 자리가 정해지지 않았을 때의 자리(지원자 메시지에는 최소 1명, C1).

    그 메시지 직전에 말한 AI 자리(지원자가 이어 받은 상대) → 우선순위 비판형 > 고집형 순서.
    소극형·연속 3회 자리·`skip`(뺀 자리·이미 고른 자리)은 고르지 않고, 그래서 남는 자리가 없으면
    `skip`이 아닌 첫 자리.
    """
    index = next(
        (i for i, m in enumerate(transcript) if str(m.get("id")) == message_id), len(transcript)
    )
    blocked = set(skip) | {catalog.seat_of("quiet"), recent_streak_seat(transcript)}
    previous = next(
        (
            m.get("sender")
            for m in reversed(transcript[:index])
            if m.get("sender") in SEATS and m.get("sender") not in blocked
        ),
        None,
    )
    if previous:
        return str(previous)
    for seat in [*catalog.seats_by_priority(["critic", "stubborn"]), *SEATS]:
        if seat not in blocked:
            return seat
    return next((s for s in SEATS if s not in skip), None)


def rule_decision(
    stimulus: dict[str, Any],
    ds: dict[str, Any],
    catalog: Catalog,
    brief: list[CardSpeaker],
    intent: dict[str, Any] | None,
    transcript: list[dict[str, Any]],
) -> dict[str, Any]:
    """규칙 1~5의 결과. 지원자 메시지에 답하는 화자가 없으면 호출자가 턴 LLM(규칙 7)과 기본 응답
    자리(`default_responder`)로 채운다.
    """
    phase = stimulus["phase"]
    kind = stimulus["type"]
    decision: dict[str, Any] = {
        "stimulus": kind,
        "generation": stimulus.get("generation"),
        "speakers": [],
        "fill": None,
        "invite": False,
        "llm_used": False,
        "queued": [],
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
    intents = intent_map(ds, stimulus, intent)
    pending = [
        (r["seat"], r["message_id"])
        for r in pending_mentions(ds, stimulus, transcript, intents, catalog)
    ]
    waiting = dict(pending)
    speakers: list[dict[str, Any]] = []
    fixed: set[str] = set()  # 고정 문장 카드 화자: 어떤 메시지에도 답하지 않는다(지명은 열린 채)
    # 2. 카드 화자(미전달 화자 이어 말하기 포함). 답하지 않은 지명이 있는 자리는 그 메시지에 답한다
    for item in brief[:MAX_SPEAKERS]:
        if any((catalog.card(c) or {}).get("fixed_line") for c in item.card_ids):
            fixed.add(item.seat)
            speakers.append(speaker(item.seat, card_ids=item.card_ids))
            continue
        speakers.append(speaker(item.seat, reply_to=waiting.get(item.seat), card_ids=item.card_ids))
    used = {s["seat"] for s in speakers}
    # 3. 지명 응답: 답하지 않은 지명(이번 메시지 포함)을 오래 기다린 자리부터. 넘치면 다음 실행
    for seat, mid in pending:
        if seat in used:
            if seat in fixed:
                decision["queued"].append({"seat": seat, "message_id": mid})
            continue
        if len(speakers) < MAX_SPEAKERS:
            speakers.append(speaker(seat, reply_to=mid))
            used.add(seat)
        else:
            decision["queued"].append({"seat": seat, "message_id": mid})
    if pending:
        decision["reason"] = "지원자가 이름을 부름"
    if kind == "card_due":
        decision["speakers"] = speakers
        decision["reason"] = decision["reason"] or (
            "카드 예정 시각" if speakers else "카드 담당 전원 전달됨"
        )
        return decision
    current = str(stimulus.get("message_id")) if kind == "message" else None
    if current:
        text = message_text(stimulus, transcript)
        excluded = excluded_seats(text, (intent or {}).get("excluded"), catalog)
        answered = any(s["reply_to"] == current for s in speakers)
        # 전체 질문(부른 자리 없음): 소극형·비판형(뺀 자리 제외)
        if (intent or {}).get("intent") == "question" and not answered and phase in DEBATE_PHASES:
            quiet = catalog.seat_of("quiet")
            if quiet and answered_open_question_last(quiet, transcript, intents, catalog):
                excluded = [*excluded, quiet]
            for seat in (quiet, catalog.seat_of("critic")):
                if seat and seat not in excluded and seat not in used:
                    if len(speakers) < MAX_SPEAKERS:
                        speakers.append(speaker(seat, reply_to=current))
                        used.add(seat)
            decision["reason"] = decision["reason"] or "전체 질문"
        # 답할 메시지가 정해지지 않은 화자(카드 화자)는 이번 지원자 메시지에 답한다
        for item in speakers:
            if item["reply_to"] is None and item["seat"] not in fixed:
                item["reply_to"] = current
    # 4. 침묵 초대(단계당 1회, 고정 자리에서는 비판형)
    if kind == "idle" and phase in DEBATE_PHASES and not ds.get("invite_delivered"):
        inviter = catalog.seats_by_priority(["agreeable", "divergent", "critic"])
        if inviter and inviter[0] not in used and len(speakers) < MAX_SPEAKERS:
            speakers.append(speaker(inviter[0], invite=True))
            decision["invite"] = True
            decision["reason"] = decision["reason"] or "지원자 침묵"
    # 5. 단계 시작: 고정 입장 자리(소극형 제외). 문제 상황은 카드 전이라 고집형 한 명
    if kind == "phase_started":
        if phase == "problem":
            stubborn = catalog.seat_of("stubborn")
            if stubborn and stubborn not in used and len(speakers) < MAX_SPEAKERS:
                speakers.append(speaker(stubborn))
        elif phase in ("keyword", "solution"):
            streak = recent_streak_seat(transcript)
            for seat in catalog.seats_by_priority(["stubborn", "critic"]):
                if seat not in used and seat != streak and len(speakers) < MAX_SPEAKERS:
                    speakers.append(speaker(seat))
                    used.add(seat)
        decision["reason"] = decision["reason"] or "단계 시작"
    decision["speakers"] = speakers[:MAX_SPEAKERS]
    return decision


def answered_open_question_last(
    seat: str,
    transcript: list[dict[str, Any]],
    intents: dict[str, dict[str, Any]],
    catalog: Names,
) -> bool:
    """이 자리의 마지막 발언이 부르지 않은 지원자 질문(전체 질문)에 답한 것이고 그 뒤 이 자리가 말한
    적이 없는지(상태 조건). 참이면 새 맥락 없이 같은 자리가 또 전체 질문을 받는 상태라 소극형을 전체
    질문 화자에서 뺀다(지명·카드 차례는 제한하지 않는다, C2 구현 세부).
    """
    last = next((m for m in reversed(transcript) if m.get("sender") == seat), None)
    if last is None or not last.get("reply_to"):
        return False
    answered = next((m for m in transcript if str(m.get("id")) == str(last["reply_to"])), None)
    if answered is None or answered.get("sender") != "candidate":
        return False
    return seat not in addressed(answered, intents, catalog)


def needs_responder(decision: dict[str, Any], stimulus: dict[str, Any]) -> bool:
    """지원자 메시지 자극인데 그 메시지에 답하는 화자가 없고 자리가 남았는지(규칙 7 대상)."""
    if stimulus.get("type") != "message" or decision.get("fill") is not None:
        return False
    current = str(stimulus.get("message_id"))
    speakers = decision.get("speakers") or []
    return len(speakers) < MAX_SPEAKERS and not any(s["reply_to"] == current for s in speakers)


def validate_turn_llm(
    out: dict[str, Any], catalog: Catalog, transcript: list[dict[str, Any]]
) -> list[str]:
    """턴 LLM이 고른 자리(최대 2). 허용 밖(소극형·연속 3회 자리·없는 자리·중복)은 버린다.

    예전 출력의 `purpose`·`option_id`가 와도 읽지 않는다(무엇을 말할지는 동료가 정한다).
    """
    quiet = catalog.seat_of("quiet")
    streak = recent_streak_seat(transcript)
    result: list[str] = []
    for item in out.get("speakers") or []:
        seat = item.get("seat") if isinstance(item, dict) else item
        if seat not in SEATS or seat == quiet or seat == streak or seat in result:
            continue
        result.append(seat)
        if len(result) == MAX_SPEAKERS:
            break
    return result


def pressure_count(card: dict[str, Any], seat: str) -> int:
    """압박 발언 수: 압박 카드의 첫 발언부터 그 자리가 이 단계에서 한 발언(카드 발언 포함).

    카드 전 발언(단계 시작 반응 등)은 세지 않는다. api가 전체 기록에서 센 값
    (DebateState `card_progress[].spoken_since_trigger`)을 쓰므로 최근 대화 창에 묶이지 않는다.
    """
    return int((card.get("spoken_since_trigger") or {}).get(seat) or 0)
