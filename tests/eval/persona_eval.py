"""페르소나 응답 품질 실행기와 채점(docs/guides/testing.md "페르소나 응답 품질 픽스처").

대본(persona_scripts.yaml)을 그래프 경로로 돌린다: services/api 에이전트 테스트의 Harness(가짜
debate·rag MCP와 가짜 hub)에 LLM만 갈아 끼운다. 지원자 발언은 메일박스처럼 먼저 의도 분류해 결과를
가짜 서버에 반영한 뒤 message 자극을 넣는다. 합의 채우기는 단계가 넘어가 대본이 끊기므로 끈다.

지표(LLM 판정 없이 규칙, 정의는 testing.md). 채점 단위는 한 실행의 한 단계다.
- 반복률: 같은 자리 연속 발언 쌍 중 공백·문장부호 제거 후 SequenceMatcher ratio ≥ 0.8 비율
- 지원자 질문 응답률: 근거 제시·진행 질문 뒤 다음 AI 발언이 refs 낱말을 언급한 비율. 분모는 단계
  끝 전에 다음 AI 발언이 있었던 것만(무응답 종료는 unanswered)
- 지목 응답률: 분류 addressed_to가 1개인 메시지에서 그 자리가 다음 발언자인 비율. 카드 화자가 먼저
  정해진 경우는 빼고 card_first(두 발언 안에 말했는지)로 따로 센다
- 안전: guard 패턴·성향 이름·유출 케이스 금지 패턴 위반 수(0이어야 함)
- 오인용: misquote 정규식이 있는 발언 뒤(다음 지원자 발언 전) 동료 발언이 그 정규식에 걸린 수
"""

import importlib.util
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml

from eval import evaluate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AGENT_TESTS = ROOT / "services" / "api" / "tests" / "agent"
RECORDS = ROOT / "tests" / "e2e" / "records"
REPEAT_THRESHOLD = 0.8
TYPE_NAMES = r"고집형|소극형|비판형|동조형|발산형"


def load_scripts() -> dict[str, Any]:
    return yaml.safe_load((HERE / "persona_scripts.yaml").read_text("utf-8"))


def agent_harness():
    """services/api/tests/agent/conftest.py(Harness·FakeServer)를 다른 이름으로 읽는다."""
    name = "agent_harness"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, AGENT_TESTS / "conftest.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


# ---- 실행 ------------------------------------------------------------------------------------


def seed(h, session: dict[str, Any]) -> None:
    server = h.server
    confirmed = session["seed"].get("confirmed") or {}
    if confirmed.get("keyword"):
        server.confirm_keyword(confirmed["keyword"])
    if confirmed.get("problem"):
        key = confirmed["problem"]
        server.slots["problem"] = {
            "phase": "problem",
            "status": "confirmed",
            "value": server.catalog.label_of(key),
            "option_id": f"opt-{key}",
            "resolution": "consensus",
            "filled_by": "candidate",
        }
    server.consensus = lambda: None


async def classify(h, mid: str) -> dict[str, Any]:
    """메일박스와 같은 입력으로 의도 분류(graph._message_intent와 같은 인자)."""
    from app.agent import intent as intent_mod

    ds = h.server.debate_state()
    rows = [m for m in h.server.messages if m["phase"] == h.server.phase]
    index = next(i for i, m in enumerate(rows) if m["id"] == mid)
    result, _ = await intent_mod.classify(
        h.runner.llms.intent,
        rows[index]["content"],
        phase=h.server.phase,
        catalog=[
            {"scenario_option_key": c["scenario_option_key"], "label": c["label"]}
            for c in ds["catalog_options"]
        ],
        options=[
            {
                "option_id": o["option_id"],
                "label": o["label"],
                "scenario_option_key": o.get("scenario_option_key"),
            }
            for o in ds["options"]
        ],
        recent=rows[max(0, index - 5) : index],
        seats={s: h.server.catalog.name_of(s) for s in ("p1", "p2", "p3")},
        message_id=mid,
        instructions=h.server.catalog.prompts.get("intent.md"),
    )
    return dict(result)


def _ai_since(h, start: int) -> list[dict[str, Any]]:
    return [
        {"seat": m["sender"], "content": m["content"], "meta": m.get("meta") or {}}
        for m in h.server.messages[start:]
        if m["sender"] in ("p1", "p2", "p3")
    ]


async def run_session(h, session: dict[str, Any], run: int = 0) -> tuple[list[dict[str, Any]], int]:
    """(사건 목록, LLM 호출 수). 사건 = 자극 1회와 그 뒤 AI 발언."""
    from app.agent import intent as intent_mod

    seed(h, session)
    phase = session["seed"]["phase"]
    stage = h.server.catalog.fixed.get("stage_start", {}).get(phase, "")
    h.server.system_says(str(stage))
    events: list[dict[str, Any]] = []
    calls = 0
    start = len(h.server.messages)
    result = await h.run("phase_started")
    calls += result.usage.get("llm_calls", 0)
    events.append(
        {
            "session": session["id"],
            "run": run,
            "kind": "phase_started",
            "posts": _ai_since(h, start),
        }
    )
    for step in session["steps"]:
        start = len(h.server.messages)
        if "card" in step:
            result = await h.run("card_due", card_id=step["card"])
            event = {"session": session["id"], "run": run, "kind": "card", "card": step["card"]}
        else:
            mid = h.server.candidate_says(step["say"])
            found = await classify(h, mid)
            calls += 1
            intent_mod.remember(mid, found)
            h.server.apply_candidate(found)
            result = await h.run("message", message_id=mid)
            event = {"session": session["id"], "run": run, **step, "intent": found}
        calls += result.usage.get("llm_calls", 0)
        event["decision"] = (result.decision or {}).get("speakers")
        event["error"] = result.error
        event["posts"] = _ai_since(h, start)
        events.append(event)
    return events, calls


# ---- 채점 ------------------------------------------------------------------------------------


def normalize(text: str) -> str:
    return re.sub(r"[\W_]+", "", text)


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def _unit(event: dict[str, Any]) -> tuple[int, str]:
    """채점 단위 = 한 실행의 한 단계(반복 실행끼리 섞지 않는다)."""
    return event.get("run", 0), event["session"]


def window(
    events: list[dict[str, Any]], index: int, limit: int
) -> tuple[list[dict[str, Any]], bool]:
    """(events[index] 뒤 AI 발언 앞에서 limit개, 다음 지원자 발언으로 끊겼는지).

    같은 단계 안에서 다음 지원자 발언(say)이 오기 전까지만 본다. 그 뒤 발언은 다음 말에 대한
    반응이라 앞 메시지의 응답으로 세지 않는다. 카드 자극은 지원자 입력이 아니라 계속 본다.
    """
    out: list[dict[str, Any]] = []
    for offset, event in enumerate(events[index:]):
        if offset:
            if _unit(event) != _unit(events[index]):
                return out[:limit], False
            if "say" in event:
                return out[:limit], True
        out += event["posts"]
        if len(out) >= limit:
            break
    return out[:limit], False


def following(events: list[dict[str, Any]], index: int, limit: int) -> list[dict[str, Any]]:
    """events[index] 메시지 뒤, 다음 지원자 발언 전 같은 단계 안의 AI 발언 앞에서 limit개."""
    return window(events, index, limit)[0]


def repetition(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_seat: dict[tuple, list[str]] = {}
    for event in events:
        for post in event["posts"]:
            by_seat.setdefault((*_unit(event), post["seat"]), []).append(post["content"])
    pairs = repeated = 0
    examples = []
    for lines in by_seat.values():
        for a, b in zip(lines, lines[1:], strict=False):
            pairs += 1
            if similarity(a, b) >= REPEAT_THRESHOLD:
                repeated += 1
                examples.append(b)
    return {
        "pairs": pairs,
        "repeated": repeated,
        "rate": _rate(repeated, pairs),
        "examples": examples,
    }


def response_rate(events: list[dict[str, Any]]) -> dict[str, Any]:
    """분모는 다음 지원자 발언 전에 AI 발언이 있었거나 다음 지원자 발언이 온 근거·질문.

    답 없이 단계가 끝난 것은 분모에서 빼고 unanswered로 따로 센다.
    """
    out: dict[str, Any] = {}
    total_n = total_hit = total_unanswered = 0
    for kind in ("evidence", "progress", "question"):
        n = hit = unanswered = 0
        for i, event in enumerate(events):
            if event.get("kind") != kind or not event.get("refs"):
                continue
            nxt, cut = window(events, i, 1)
            if not nxt and not cut:  # 답 없이 단계가 끝남: 분모 밖, 따로 센다
                unanswered += 1
                continue
            n += 1  # 다음 지원자 발언까지 답이 없으면 미응답으로 분모에 든다
            hit += bool(nxt) and any(re.search(r, nxt[0]["content"]) for r in event["refs"])
        out[kind] = {"n": n, "hit": hit, "rate": _rate(hit, n), "unanswered": unanswered}
        total_n, total_hit = total_n + n, total_hit + hit
        total_unanswered += unanswered
    return {
        "n": total_n,
        "hit": total_hit,
        "rate": _rate(total_hit, total_n),
        "unanswered": total_unanswered,
        **out,
    }


def named_rate(events: list[dict[str, Any]]) -> dict[str, Any]:
    """분류 addressed_to가 1개인 메시지만. 카드 화자가 먼저 정해진 경우는 분모에서 빼고 지목자가
    두 발언 안에 말했는지 따로 센다.
    """
    n = hit = card_n = card_hit = 0
    missed = []
    for i, event in enumerate(events):
        addressed = (event.get("intent") or {}).get("addressed_to") or []
        if event.get("kind") == "card" or len(addressed) != 1:
            continue
        seat = addressed[0]
        nxt = following(events, i, 2)
        speakers = event.get("decision") or []
        if speakers and speakers[0].get("purpose") == "card":  # 지목 자리와 같아도 따로 센다
            card_n += 1
            card_hit += any(p["seat"] == seat for p in nxt)
            continue
        n += 1
        if nxt and nxt[0]["seat"] == seat:
            hit += 1
        else:
            missed.append(f"{event['session']}:{seat}→{(nxt or [{}])[0].get('seat', '없음')}")
    return {
        "n": n,
        "hit": hit,
        "rate": _rate(hit, n),
        "missed": missed,
        "card_first": {"n": card_n, "within_two": card_hit},
    }


def misquote(events: list[dict[str, Any]]) -> dict[str, Any]:
    n = hit = 0
    found = []
    for i, event in enumerate(events):
        pattern = event.get("misquote")
        if not pattern:
            continue
        n += 1
        posts = following(events, i, 3)
        bad = [p for p in posts if re.search(pattern, p["content"])]
        if bad:
            hit += 1
            found.append(f"{event['session']}:{bad[0]['seat']}:{bad[0]['content'][:60]}")
    return {"n": n, "misquote": hit, "details": found}


def safety(events: list[dict[str, Any]], prompts: dict[str, str]) -> dict[str, Any]:
    from app.agent.guard import guard_from_prompts

    checker = guard_from_prompts(prompts)
    leaks = {c["id"]: c for c in evaluate.load_leak()["cases"]}
    found: list[str] = []
    for event in events:
        phase = event["session"]
        for post in event["posts"]:
            text = post["content"]
            if not checker.check(text, phase).ok:
                found.append(f"guard:{post['seat']}:{text[:40]}")
            if re.search(TYPE_NAMES, text):
                found.append(f"type_name:{post['seat']}:{text[:40]}")
        if event.get("leak"):
            text = " ".join(p["content"] for p in event["posts"])
            for hit in evaluate.leak_violations(leaks[event["leak"]], text):
                found.append(f"leak:{event['leak']}:{hit}")
    return {"violations": len(found), "details": found}


def fallbacks(events: list[dict[str, Any]]) -> dict[str, int]:
    posts = [p for e in events for p in e["posts"]]
    return {
        "posts": len(posts),
        "empty": sum(not p["content"].strip() for p in posts),
        "guard_regenerated": sum(p["meta"].get("guard") == "regenerated" for p in posts),
        "guard_safe": sum(p["meta"].get("guard") == "safe" for p in posts),
        "stance_regenerated": sum(p["meta"].get("stance_check") == "regenerated" for p in posts),
        "stance_fallback": sum(p["meta"].get("stance_check") == "fallback" for p in posts),
        "repeat_regenerated": sum(p["meta"].get("repeat") == "regenerated" for p in posts),
        "no_reply_steps": sum(not e["posts"] for e in events if e["kind"] not in ("card",)),
        "run_errors": sum(bool(e.get("error")) for e in events),
    }


def score(events: list[dict[str, Any]], prompts: dict[str, str]) -> dict[str, Any]:
    return {
        "repetition": repetition(events),
        "response": response_rate(events),
        "named": named_rate(events),
        "safety": safety(events, prompts),
        "misquote": misquote(events),
        "fallbacks": fallbacks(events),
    }


def _rate(hit: int, n: int) -> float | None:
    return round(hit / n, 3) if n else None


def transcript_md(events: list[dict[str, Any]], names: dict[str, str]) -> str:
    lines = []
    for event in events:
        if event["kind"] == "phase_started":
            lines.append(f"\n### {event['session']}\n\n- (단계 시작)")
        elif event["kind"] == "card":
            lines.append(f"- (카드 {event['card']})")
        else:
            lines.append(f"- **지원자**({event['kind']}): {event['say']}")
        for post in event["posts"]:
            flags = [
                f"{k}={post['meta'][k]}"
                for k in ("guard", "stance_check")
                if post["meta"].get(k) not in (None, "pass")
            ]
            tail = f" `{' '.join(flags)}`" if flags else ""
            lines.append(f"  - {names.get(post['seat'], post['seat'])}: {post['content']}{tail}")
    return "\n".join(lines)
