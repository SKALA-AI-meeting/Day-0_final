"""세션 export(session.json)에서 AI 동작 지표를 계산한다(docs/guides/virtual-candidate.md "지표").

    uv run --project services/api python tests/eval/virtual_candidate/metrics.py \\
        <session.json>... [--judge]

입력은 drive.py `export_session`이 만든 표별 JSON(session·position·messages·
message_intents·agent_runs·options·stances·slots·session_cards·observations·
assistant_calls·evaluation_runs·evaluations …)이다. 사실(누가·언제·있다/없다·횟수)은
규칙으로 센다. 뜻을 읽어야 하는 지표(요약-차원 모순, 동료 응답 여부, 오인용, 귀속 판정)와 거짓
합의의 "명시적 동의" 판정만 `--judge`일 때 LLM 판정을 쓴다. 판정 프롬프트는 judge_prompts.yaml
(귀속 판정은 ../attribution_prompts.yaml)에 고정하고 결과에 모델·날짜를 남긴다. 판정 입력과
결과에는 지원자 표시 이름 대신 "지원자"를 쓴다.
"""

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

sys.path[:0] = [str(Path(__file__).resolve().parents[2])]
from eval import attribution, llm_access  # noqa: E402  귀속 판정(공용)·키 규칙·ALLOW_API
from eval.assistant_eval import (  # noqa: E402  같은 채점 규칙(숫자·출처 줄·잘림)
    SOURCE_LINE,
    max_chars,
    truncated,
    ungrounded_numbers,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MATERIALS = ROOT / "scenarios" / "missions" / "debate" / "materials"
PEERS = ("p1", "p2", "p3")
PHASES = ("keyword", "problem", "solution", "proposal")
DEBATE_PHASES = PHASES[:3]
FILL_KINDS = {"consensus_fill", "timeout_fill"}
FAILED_CODE = re.compile(r"\(사유: ([^)]+)\)")


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _rate(hit: int, n: int) -> float | None:
    return round(hit / n, 3) if n else None


class Session:
    """export 한 건을 지표 계산에 쓰기 좋게 묶는다."""

    def __init__(self, data: dict[str, Any]) -> None:
        s = data["session"]
        self.session = s[0] if isinstance(s, list) else s
        self.data = data
        self.scenario = (data.get("position") or {}).get("scenario") or {}
        self.name = self.session.get("display_name") or ""
        self.messages = sorted(
            data.get("messages") or [], key=lambda m: (m["created_at"], m.get("seq") or 0)
        )
        self.by_id = {m["id"]: m for m in self.messages}
        self.room = [m for m in self.messages if m.get("channel", "room") == "room"]
        self.intents = {i["message_id"]: i for i in data.get("message_intents") or []}
        self.options = {o["id"]: o for o in data.get("options") or []}
        self.slots = {s["phase"]: s for s in data.get("slots") or []}
        self.evaluations = data.get("evaluations") or []
        self.phase_bounds = self._phase_bounds()

    def anon(self, text: str) -> str:
        return text.replace(self.name, "지원자") if self.name else text

    def _phase_bounds(self) -> dict[str, tuple[datetime, datetime]]:
        """단계별 [시작, 끝). 키워드 시작 = started_at, 다음 단계 시작 = 앞 칸 확정 시각."""
        start = _ts(self.session.get("started_at"))
        end = _ts(self.session.get("completed_at") or self.session.get("ended_at"))
        if end is None and self.messages:
            end = _ts(self.messages[-1]["created_at"])
        bounds: dict[str, tuple[datetime, datetime]] = {}
        cursor = start
        for phase in PHASES:
            if cursor is None:
                break
            slot = self.slots.get(phase)
            stop = _ts(slot.get("confirmed_at")) if slot and phase != "proposal" else None
            bounds[phase] = (cursor, stop or end)
            if stop is None:
                break
            cursor = stop
        return bounds

    def candidate_room(self) -> list[dict[str, Any]]:
        return [m for m in self.room if m["sender"] == "candidate"]

    def persona_room(self) -> list[dict[str, Any]]:
        return [
            m
            for m in self.room
            if m["sender"] in PEERS and (m.get("meta") or {}).get("kind") not in FILL_KINDS
        ]

    # ---- 카드 창 ------------------------------------------------------------------------------

    def card_window(self, card_id: str) -> tuple[datetime, datetime] | None:
        card = next((c for c in self.scenario.get("cards") or [] if c["card_id"] == card_id), None)
        obs = next(
            (o for o in self.data.get("observations") or [] if o["card_id"] == card_id), None
        )
        if card is None or obs is None:
            return None
        window = card.get("window") or {}
        stage = self.phase_bounds.get(card["stage_id"])
        if window.get("start") == "card_message":
            trigger = self.by_id.get(obs.get("trigger_message_id") or "")
            begin = _ts(trigger["created_at"]) if trigger else _ts(obs.get("fired_at"))
        else:
            begin = stage[0] if stage else _ts(obs.get("fired_at"))
        end_phase = str(window.get("end", "")).removeprefix("stage_end:")
        bound = self.phase_bounds.get(end_phase) or stage
        finish = bound[1] if bound else None
        if begin is None or finish is None:
            return None
        return begin, finish

    def dimension_windows(self, dimension_id: str) -> list[tuple[datetime, datetime]] | None:
        """핵심 카드 창 목록. 핵심 카드가 없으면 세션 전체(None을 돌려주면 판단 불가)."""
        dims = {
            d["dimension_id"]: d
            for e in (self.scenario.get("rubric") or {}).get("elements") or []
            for d in e.get("dimensions") or []
        }
        dim = dims.get(dimension_id)
        if dim is None:
            return None
        cores = dim.get("core_cards") or []
        if not cores:
            first = min((b[0] for b in self.phase_bounds.values()), default=None)
            last = max((b[1] for b in self.phase_bounds.values()), default=None)
            return [(first, last)] if first and last else None
        windows = [w for w in (self.card_window(c) for c in cores) if w]
        return windows

    def candidate_in(self, windows: list[tuple[datetime, datetime]]) -> list[dict[str, Any]]:
        out = []
        for m in self.candidate_room():
            t = _ts(m["created_at"])
            if any(a <= t <= b for a, b in windows):
                out.append(m)
        return out


# ---- 평가 ---------------------------------------------------------------------------------------


def evaluation_metrics(s: Session) -> dict[str, Any]:
    rows = s.evaluations
    reasons = Counter(r.get("no_record_reason") or "level" for r in rows)
    failed = Counter(
        (FAILED_CODE.search(r.get("rationale") or "") or [None, "unknown"])[1]
        for r in rows
        if r.get("no_record_reason") == "evaluate_failed"
    )
    missed, missed_detail = 0, []
    no_activity = [r for r in rows if r.get("no_record_reason") == "no_activity"]
    for row in no_activity:
        windows = s.dimension_windows(row["dimension_id"])
        if not windows:
            continue
        inside = s.candidate_in(windows)
        if inside:
            missed += 1
            missed_detail.append(f"{row['dimension_id']}:{len(inside)}")
    with_peer, only_peer, peer_detail = 0, 0, []
    for row in rows:
        if row.get("level") is None:
            continue
        refs = row.get("evidence_refs") or []
        senders = [
            s.by_id[r[4:]]["sender"] for r in refs if r.startswith("msg:") and r[4:] in s.by_id
        ]
        units = sum(x == "candidate" for x in senders) + sum(
            1
            for r in refs
            if r.startswith("proposal:")
            or (
                r.startswith("slot:") and (s.slots.get(r[5:]) or {}).get("filled_by") == "candidate"
            )
        )
        if any(x != "candidate" for x in senders):
            with_peer += 1
            peer_detail.append(row["dimension_id"])
        if units == 0:
            only_peer += 1
    return {
        "dimensions": len(rows),
        "no_record_reasons": dict(reasons),
        "evaluate_failed": {
            "n": reasons.get("evaluate_failed", 0),
            "rate": _rate(reasons.get("evaluate_failed", 0), len(rows)),
            "by_code": dict(failed),
        },
        "activity_missed": {
            "no_activity": len(no_activity),
            "missed": missed,
            "detail": missed_detail,
        },
        "evidence_peer_message": {
            "leveled": sum(r.get("level") is not None for r in rows),
            "with_peer_ref": with_peer,
            "no_candidate_unit": only_peer,
            "dimensions": peer_detail,
        },
    }


# ---- 합의·시간 초과
# --------------------------------------------------------------------------------


def consensus_metrics(s: Session) -> dict[str, Any]:
    fills, timeouts = [], []
    for phase in DEBATE_PHASES:
        slot = s.slots.get(phase)
        if not slot or slot.get("status") != "confirmed":
            continue
        if slot.get("filled_by") in PEERS and slot.get("resolution") == "consensus":
            fills.append(_fill_row(s, phase, slot))
        if slot.get("resolution") == "timeout":
            timeouts.append(_timeout_row(s, phase, slot))
    resolution = Counter(
        f"{s.slots[p].get('resolution')}/{_who(s.slots[p].get('filled_by'))}"
        for p in DEBATE_PHASES
        if s.slots.get(p)
    )
    return {
        "slot_resolution": dict(resolution),
        "ai_fills": fills,
        "timeouts": timeouts,
        "timeout_notice_missing": sum(not t["notice_present"] for t in timeouts),
        "timeout_notice_wrong": sum(
            t["notice_present"] and not t["notice_matches_rule"] for t in timeouts
        ),
        "timeout_announcer_persona": sum(t["announcer"] in PEERS for t in timeouts),
        "timeout_choice_wrong": sum(not t["choice_matches_rule"] for t in timeouts),
        "timeout_rule_unrecorded": sum(t["recorded_rule"] != t["rule"] for t in timeouts),
    }


def _who(seat: str | None) -> str:
    return "candidate" if seat == "candidate" else "ai" if seat in PEERS else str(seat)


def _fill_row(s: Session, phase: str, slot: dict[str, Any]) -> dict[str, Any]:
    confirmed = _ts(slot["confirmed_at"])
    before = [
        m for m in s.candidate_room() if m["phase"] == phase and _ts(m["created_at"]) <= confirmed
    ]
    latest = before[-1] if before else None
    intent = s.intents.get(latest["id"]) if latest else None
    classified_support = bool(
        intent
        and intent["intent"] in ("propose", "agree")
        and intent["confidence"] == "high"
        and not intent.get("reservation")
        and intent.get("target_option_id") == slot.get("option_id")
    )
    return {
        "phase": phase,
        "option": (s.options.get(slot.get("option_id")) or {}).get("label"),
        "latest_candidate": s.anon(latest["content"])[:160] if latest else None,
        "latest_intent": (f"{intent['intent']}:{intent['confidence']}" if intent else None),
        "classified_support": classified_support,
        "explicit_agreement": None,  # --judge가 채운다
    }


def _timeout_row(s: Session, phase: str, slot: dict[str, Any]) -> dict[str, Any]:
    """시간 초과 선택과 안내가 규칙(결정 B3·B4)과 맞는지. 확정 시점 지지 스냅샷(안 생성 순)으로
    적용 규칙을 다시 계산하고, 칸에 기록된 규칙·고른 안·안내 문장(채용 건 복사본의 규칙별 문장)을
    그 규칙과 대조한다."""
    support = slot.get("support") or []
    counts = {x["option_id"]: len(x.get("supporters") or []) for x in support}
    chosen = slot.get("option_id")
    candidate_pick = next(
        (x["option_id"] for x in support if "candidate" in (x.get("supporters") or [])), None
    )
    top = max(counts.values(), default=0)
    tied = [o for o, c in counts.items() if c == top]
    if not counts:
        rule, expected = "default", chosen
    elif candidate_pick:
        rule, expected = "candidate_support", candidate_pick
    elif top > 0 and len(tied) == 1:
        rule, expected = "most_support", tied[0]
    else:
        rule, expected = "earliest", tied[0]
    notice = next(
        (
            m
            for m in s.room
            if (m.get("meta") or {}).get("kind") == "timeout_fill" and m["phase"] == phase
        ),
        None,
    )
    # 안내가 있는지와, 있으면 그 문장이 어느 규칙의 문장인지를 따로 본다
    claimed = None
    if notice:
        label = (s.options.get(chosen) or {}).get("label") or slot.get("value") or ""
        claimed = next(
            (r for r, text in _timeout_notices(s, label).items() if text == notice["content"]),
            "unstated",
        )
    return {
        "phase": phase,
        "rule": rule,
        "recorded_rule": slot.get("timeout_rule"),
        "support_counts": sorted(counts.values(), reverse=True),
        "choice_matches_rule": chosen == expected,
        "notice_present": notice is not None,
        "notice_claims": claimed,
        "notice_matches_rule": notice is not None and claimed == rule,
        "announcer": notice["sender"] if notice else None,
        "filled_by": slot.get("filled_by"),
        "candidate_supported_chosen": candidate_pick == chosen if candidate_pick else None,
    }


def _ro(word: str) -> str:
    code = ord(word[-1]) - 0xAC00 if word else -1
    return "으로" if 0 <= code <= 11171 and code % 28 not in (0, 8) else "로"


def _timeout_notices(s: Session, label: str) -> dict[str, str]:
    """규칙별 안내 문장(채용 건 복사본의 고정 문장, api `scenario.phrase`와 같은 치환)."""
    templates = (s.scenario.get("phrases") or {}).get("timeout_fill") or {}
    if isinstance(templates, str):  # 규칙별 문장 전의 채용 건
        templates = {"legacy": templates}
    return {
        rule: t.replace("{option}로", "{option}" + _ro(label)).format(
            option=label, candidate=s.name or "지원자"
        )
        for rule, t in templates.items()
    }


# ---- 토론 동료
# -------------------------------------------------------------------------------------


def persona_metrics(s: Session) -> dict[str, Any]:
    posts = [m for m in s.persona_room() if m["phase"] in DEBATE_PHASES]
    checks = Counter((m.get("meta") or {}).get("stance_check") or "none" for m in posts)
    first_support: dict[tuple[str, str], str] = {}
    for st in sorted(s.data.get("stances") or [], key=lambda x: x["created_at"]):
        if st["participant"] in PEERS and st["stance"] == "support":
            first_support.setdefault((st["phase"], st["participant"]), st["option_id"])
    pushed = 0
    by_message = {
        st["source_message_id"]: st
        for st in s.data.get("stances") or []
        if st["participant"] in PEERS and st["stance"] == "support"
    }
    for m in posts:
        st = by_message.get(m["id"])
        if st and first_support.get((m["phase"], m["sender"])) == st["option_id"]:
            pushed += 1
    configured = [c for c in s.scenario.get("cards") or [] if c.get("stage_id") in s.phase_bounds]
    fired = {o["card_id"] for o in s.data.get("observations") or []}
    delivered = {
        c["card_id"] for c in s.data.get("session_cards") or [] if c.get("delivered_seats")
    }
    return {
        "persona_posts": len(posts),
        "stance_check": dict(checks),
        "stance_fallback_rate": _rate(checks.get("fallback", 0), len(posts)),
        "own_option_push": {"n": pushed, "rate": _rate(pushed, len(posts))},
        "cards": {
            "reachable": len(configured),
            "fired": len(fired),
            "delivered": len(delivered & fired),
            "not_fired": sorted({c["card_id"] for c in configured} - fired),
        },
        "replies": reply_metrics(s),
    }


def reply_metrics(s: Session) -> dict[str, Any]:
    """지원자가 동료를 부르거나 물은 메시지(addressed_to 있음 또는 question) 뒤, 다음 지원자 메시지
    전까지
    동료 발언이 있었는지, 부른 동료가 말했는지(규칙). 그 발언이 실제로 답했는지는 --judge.

    `reply_to`는 진행 매니저가 정해 동료 발언 `meta.reply_to`에 남긴 답한 메시지로 센 값이다(다음
    지원자 메시지 뒤에 답한 것 포함). 그 기록이 없는 세션(이전 버전)이면 None.
    """
    rows = []
    cands = s.candidate_room()
    linked: dict[str, list[str]] = {}
    recorded = False
    for p in s.persona_room():
        meta = p.get("meta") or {}
        recorded = recorded or "reply_to" in meta
        if meta.get("reply_to"):
            linked.setdefault(meta["reply_to"], []).append(p["sender"])
    for i, m in enumerate(cands):
        if m["phase"] not in DEBATE_PHASES:
            continue
        intent = s.intents.get(m["id"]) or {}
        addressed = intent.get("addressed_to") or []
        if not addressed and intent.get("intent") != "question":
            continue
        t0 = _ts(m["created_at"])
        t1 = _ts(cands[i + 1]["created_at"]) if i + 1 < len(cands) else None
        after = [
            p
            for p in s.persona_room()
            if _ts(p["created_at"]) > t0
            and (t1 is None or _ts(p["created_at"]) < t1)
            and p["phase"] == m["phase"]
        ]
        rows.append(
            {
                "message_id": m["id"],
                "addressed": addressed,
                "any_reply": bool(after),
                "addressed_spoke": bool(addressed) and any(p["sender"] in addressed for p in after),
                "replies": [p["id"] for p in after[:3]],
                "reply_to_seats": linked.get(m["id"], []),
                "answered": None,  # --judge
            }
        )
    named = [r for r in rows if r["addressed"]]
    debate = [m for m in cands if m["phase"] in DEBATE_PHASES]
    by_reply = None
    if recorded:
        by_reply = {
            "all_n": len(debate),
            "all_unanswered": sum(not linked.get(m["id"]) for m in debate),
            "named_seats": sum(len(r["addressed"]) for r in named),
            "named_seats_unanswered": sum(
                seat not in r["reply_to_seats"] for r in named for seat in r["addressed"]
            ),
        }
    return {
        "n": len(rows),
        "no_reply": sum(not r["any_reply"] for r in rows),
        "named_n": len(named),
        "named_unanswered": sum(not r["addressed_spoke"] for r in named),
        "reply_to": by_reply,
        "rows": rows,
    }


# ---- 어시스턴트 -----------------------------------------------------------------------


def material_text(material_ids: list[str]) -> str:
    out = []
    for mid in material_ids:
        found = list(MATERIALS.glob(f"*/{mid}.md"))
        if found:
            out.append(found[0].read_text("utf-8"))
    return "\n".join(out)


def assistant_metrics(s: Session) -> dict[str, Any]:
    keyword = (s.slots.get("keyword") or {}).get("value")
    keys = (
        {k["label"]: k for k in (s.scenario.get("keywords") or {}).values()}
        if isinstance(s.scenario.get("keywords"), dict)
        else {}
    )
    open_ids = [m["material_id"] for m in (keys.get(keyword) or {}).get("materials") or []]
    corpus = material_text(open_ids)
    rows = []
    for call in s.data.get("assistant_calls") or []:
        answer = s.by_id.get(call.get("response_message_id") or "")
        text = answer["content"] if answer else ""
        missing = ungrounded_numbers(text, corpus) if corpus else []
        rows.append(
            {
                "tool_calls": len(call.get("tool_calls") or []),
                "cited": len(call.get("cited_material_ids") or []),
                "source_line_without_tool": bool(SOURCE_LINE.search(text))
                and not call.get("tool_calls"),
                "numbers_not_in_materials": missing,
                "maybe_truncated": bool(text) and truncated(text, max_chars()),
                "chars": len(text),
            }
        )
    return {
        "calls": len(rows),
        "with_ungrounded_number": sum(bool(r["numbers_not_in_materials"]) for r in rows),
        "source_line_without_tool": sum(r["source_line_without_tool"] for r in rows),
        "maybe_truncated": sum(r["maybe_truncated"] for r in rows),
        "rows": rows,
    }


# ---- 합치기
# ------------------------------------------------------------------------------------------


def compute(data: dict[str, Any]) -> dict[str, Any]:
    s = Session(data)
    return {
        "session": {
            "id": s.session.get("id"),
            "status": s.session.get("status"),
            "candidate_messages": len(s.candidate_room()),
            "minutes": (s.data.get("position") or {}).get("session_minutes"),
        },
        "evaluation": evaluation_metrics(s),
        "consensus": consensus_metrics(s),
        "persona": persona_metrics(s),
        "assistant": assistant_metrics(s),
        "judge": None,
    }


# ---- LLM 판정 ------------------------------------------------------------------------------------


def load_judge_prompts() -> dict[str, Any]:
    return yaml.safe_load((HERE / "judge_prompts.yaml").read_text("utf-8"))


class Judge:
    def __init__(self, values: dict[str, str]) -> None:
        import httpx

        self.model = llm_access.judge_model(values)
        key, base_url = llm_access.api_key_for(self.model, values)
        self.http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=60,
        )
        self.prompts = load_judge_prompts()

    async def ask(self, key: str, body: dict[str, Any]) -> dict[str, Any]:
        spec = self.prompts[key]
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": spec["system"]},
                {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
            ],
        }
        response = await self.http.post("/chat/completions", json=payload)
        response.raise_for_status()
        return json.loads(response.json()["choices"][0]["message"]["content"])

    async def aclose(self) -> None:
        await self.http.aclose()


async def judge(
    data: dict[str, Any],
    result: dict[str, Any],
    j: Judge,
    attribution_judge: attribution.AttributionJudge | None = None,
) -> dict[str, Any]:
    s = Session(data)
    names = {p: n for p, n in (("p1", "강한결"), ("p2", "윤하람"), ("p3", "서지안"))}
    names["candidate"] = "지원자"

    def line(m: dict[str, Any]) -> str:
        return f"{names.get(m['sender'], m['sender'])}: {s.anon(m['content'])}"

    out: dict[str, Any] = {"model": j.model, "date": date.today().isoformat()}
    # 1) 요약-차원 모순
    contradictions = []
    owned = {
        e["element"]: {d["dimension_id"] for d in e.get("dimensions") or []}
        for e in (s.scenario.get("rubric") or {}).get("elements") or []
    }
    for element in (s.data.get("evaluation_runs") or [{}])[0].get("elements") or []:
        dims = [
            {
                "dimension_id": r["dimension_id"],
                "level": r.get("level"),
                "no_record_reason": r.get("no_record_reason"),
                "rationale": s.anon(r.get("rationale") or ""),
            }
            for r in s.evaluations
            if r["dimension_id"] in owned.get(element["element"], set())
        ]
        verdict = await j.ask(
            "summary_contradiction",
            {"summary": s.anon(element.get("summary") or ""), "dimensions": dims},
        )
        contradictions.append({"element": element["element"], **verdict})
    out["summary_contradiction"] = {
        "elements": len(contradictions),
        "contradicts": sum(bool(c.get("contradicts")) for c in contradictions),
        "rows": contradictions,
    }
    # 2) 거짓 합의: AI 채움 직전 지원자 최신 발언이 명시적 동의였는지
    for fill in result["consensus"]["ai_fills"]:
        if fill["latest_candidate"] is None:
            fill["explicit_agreement"] = False
            continue
        verdict = await j.ask(
            "explicit_agreement",
            {"option": fill["option"], "message": fill["latest_candidate"]},
        )
        fill["explicit_agreement"] = bool(verdict.get("explicit_agreement"))
        fill["judge_reason"] = verdict.get("reason")
    out["false_consensus"] = sum(
        not f["explicit_agreement"] for f in result["consensus"]["ai_fills"]
    )
    # 3) 동료가 지원자 말에 실제로 답했는지
    rows = result["persona"]["replies"]["rows"]
    for row in rows:
        if not row["any_reply"]:
            row["answered"] = False
            continue
        verdict = await j.ask(
            "reply_answers",
            {
                "candidate": line(s.by_id[row["message_id"]]),
                "replies": [line(s.by_id[r]) for r in row["replies"]],
            },
        )
        row["answered"] = bool(verdict.get("answered"))
    out["ignored"] = {
        "n": len(rows),
        "ignored": sum(not r["answered"] for r in rows),
        "rate": _rate(sum(not r["answered"] for r in rows), len(rows)),
    }
    # 4) 오인용: 지원자를 이름으로 인용한 동료 발언
    quotes = [
        m
        for m in s.persona_room()
        if s.name and re.search(rf"{re.escape(s.name)}\s*님", m["content"])
    ]
    misquotes = []
    for m in quotes:
        earlier = [
            s.anon(c["content"])
            for c in s.candidate_room()
            if _ts(c["created_at"]) < _ts(m["created_at"])
        ]
        verdict = await j.ask(
            "misquote", {"persona_message": line(m), "candidate_messages": earlier[-8:]}
        )
        if verdict.get("misquote"):
            misquotes.append({"message": line(m)[:160], "reason": verdict.get("reason")})
    out["misquote"] = {"quotes": len(quotes), "misquotes": len(misquotes), "rows": misquotes}
    # 5) 귀속 판정: 동료 발언 전체(토론 단계)에서 귀속을 뽑아 그 전 원문과 대조(주 지표. 4는 보조)
    if attribution_judge is not None:
        transcript = [
            m
            for m in attribution.transcript_from_session(data)
            if not m["is_post"] or m["phase"] in DEBATE_PHASES
        ]
        rows = await attribution.judge_posts(transcript, attribution_judge, names)
        out["attribution"] = {
            "judge": attribution_judge.info(),
            "summary": attribution.summarize(rows),
            "rows": attribution.compact_rows(rows),
        }
    return out


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--out", type=Path, default=None, help="결과 JSON(여러 세션이면 목록)")
    args = parser.parse_args()
    sys.path[:0] = [str(ROOT / "tests")]
    from e2e.client import env

    values = env()
    if args.judge:
        llm_access.require_allow_api("metrics --judge", values)
    j = Judge(values) if args.judge else None
    aj = attribution.AttributionJudge(values) if args.judge else None
    results = []
    try:
        for path in args.paths:
            data = json.loads(path.read_text("utf-8"))
            result = compute(data)
            if j:
                result["judge"] = await judge(data, result, j, aj)
            results.append(result)
    finally:
        if j:
            await j.aclose()
        if aj:
            await aj.aclose()
    text = json.dumps(results if len(results) > 1 else results[0], ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, "utf-8")
    print(text)


if __name__ == "__main__":
    asyncio.run(_main())
