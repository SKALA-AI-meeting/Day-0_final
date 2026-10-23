"""합의 판정과 시간 초과 선택(docs/architecture/session-lifecycle.md "합의 판정과 시간 초과 선택").
순수
함수.

DebateState, 내부 `POST /slots/fill` 검사, 시간 초과 채우기가 같은 함수를 쓴다.

- 현재 지지는 참여자당 하나: 참여자별 그 단계의 마지막 support 안. 다른 안 support는 대체하고,
  그 뒤 같은 안 oppose·withhold(지원자 보류·기권)는 철회(null). 다른 안 oppose·withhold는 바꾸지
  않는다. withhold는 반대자로 세지 않는다.
- AI 자리의 제안(안 행 proposer)은 제안자 지지를 포함한다. 지원자는 지지로 세는 발언(high인
  propose·agree이고 유보 표현 없음)일 때만 support 행이 생기므로 안 행만으로는 지지로 세지
  않는다(R4·B1).
- consensus: 지원자 포함 3명 이상이 같은 안을 현재 지지하고, 지원자의 그 단계 최신 발언이 그 안을
  지지로 세는 발언(`confirmed_option_id`, B2)일 때(대기 없음).
- timeout: 지지자 최다 → 동률이면 지원자 지지 안 → 먼저 제안된 안 → 안이 없으면 단계 기준안.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

PARTICIPANTS = ("candidate", "p1", "p2", "p3")
AI_SEATS = ("p1", "p2", "p3")
MIN_SUPPORTERS = 3


def _events(options: Iterable[dict], stances: Iterable[dict]) -> list[tuple]:
    events = []
    for o in options:
        if o["proposer"] in AI_SEATS:
            events.append((o["created_at"], 0, o["proposer"], o["id"], "support"))
    for i, s in enumerate(stances):
        events.append((s["created_at"], 1 + i, s["participant"], s["option_id"], s["stance"]))
    events.sort(key=lambda e: (e[0], e[1]))
    return events


def judge(
    options: list[dict[str, Any]],
    stances: list[dict[str, Any]],
    default_option_id: Any = None,
    confirmed_option_id: Any = None,
) -> dict[str, Any]:
    """options: [{id, proposer, created_at}], stances: 시간순 [{participant, option_id, stance,
    created_at}]. confirmed_option_id: 지원자의 그 단계 최신 발언이 지지로 세는 발언이면 그 대상
    안(아니면 None이라 합의가 없다).

    반환: {consensus_option_id, timeout_option_id, current_support, supporters, opposers}.
    """
    current: dict[str, Any] = {p: None for p in PARTICIPANTS}
    last: dict[tuple[str, Any], str] = {}
    for _, _, who, option_id, stance in _events(options, stances):
        last[(who, option_id)] = stance
        if stance == "support":
            current[who] = option_id
        elif current.get(who) == option_id:
            current[who] = None
    supporters: dict[Any, list[str]] = {o["id"]: [] for o in options}
    opposers: dict[Any, list[str]] = {o["id"]: [] for o in options}
    for who in PARTICIPANTS:
        if current[who] in supporters:
            supporters[current[who]].append(who)
    for (who, option_id), stance in last.items():
        if stance == "oppose" and current.get(who) != option_id and option_id in opposers:
            opposers[option_id].append(who)

    consensus = None
    for option_id, people in supporters.items():
        if (
            len(people) >= MIN_SUPPORTERS
            and "candidate" in people
            and option_id == confirmed_option_id
        ):
            consensus = option_id
            break

    timeout = default_option_id
    if options:
        top = max(len(p) for p in supporters.values())
        tied = [o for o in options if len(supporters[o["id"]]) == top]
        if len(tied) > 1 and current["candidate"] in {o["id"] for o in tied}:
            timeout = current["candidate"]
        else:
            timeout = min(tied, key=lambda o: _created(o))["id"]
    return {
        "consensus_option_id": consensus,
        "timeout_option_id": timeout,
        "current_support": current,
        "supporters": supporters,
        "opposers": opposers,
    }


def _created(option: dict[str, Any]) -> datetime:
    return option["created_at"]


def support_snapshot(options: list[dict[str, Any]], result: dict[str, Any]) -> list[dict]:
    """칸 확정 순간의 지지 현황 [{option_id, supporters}] (안 생성 순)."""
    ordered = sorted(options, key=_created)
    return [
        {"option_id": str(o["id"]), "supporters": result["supporters"][o["id"]]} for o in ordered
    ]
