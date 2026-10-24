"""귀속(누가 무엇을 말했다는 부분) 추출·판정(docs/guides/testing.md "귀속 판정").

persona_eval(대본 실행 사건 목록)과 virtual_candidate(세션 내보내기)가 함께 쓴다. 게시된 AI 발언마다
귀속을 모두 뽑고 supported / overstated / unsupported / undeterminable 중 하나로 판정한다.
판정 모델에는 그 발언이 게시되기 전까지의 원문(id·화자·단계·순서)만 준다. 귀속이 없는 발언은
"귀속 없음"이고 supported로 세지 않는다.

    uv run --project services/api python tests/eval/attribution.py \\
        judge <events.json|session.json|eval-live.md>... [--out X]
    uv run --project services/api python tests/eval/attribution.py \\
        extract <calibration.jsonl> [--labels OUT]

`judge`는 기록된 실행(persona_eval `PERSONA_EVAL_DUMP` 사건 목록, 가상 세션 `session.json`,
eval-live 페르소나 md)을 다시 판정해 요약을 낸다. `extract`는 보정 세트의 항목마다 귀속을 뽑아
`extracted_attributions`를 채우고 판정 모델의 라벨을 따로 쓴다(build_calibration.py 참고).

판정 모델은 `VC_JUDGE_MODEL`(없으면 gpt-4o), temperature 0, 구조화 출력(JSON Schema). 실제로 응답한
모델 id를 결과에 남긴다. 키는 모델 id 규칙(gpt-4o-mini → OPENAI_API_KEY, 그 외 →
OPENAI_API_KEY_PERSONAL).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
LABELS = ("supported", "overstated", "unsupported", "undeterminable")
ERRORS = ("overstated", "unsupported")
FORMS = ("statement", "presupposing_question", "elliptical")
PEERS = ("p1", "p2", "p3")
TARGETS = ("candidate", *PEERS)
DEFAULT_MODEL = "gpt-4o"
SOURCE_LIMIT = 60  # 판정 입력 원문 상한(넘으면 앞을 자르고 sources_truncated 표시)
FILL_KINDS = {"consensus_fill", "timeout_fill"}
NAMES = {"candidate": "지원자", "p1": "강한결", "p2": "윤하람", "p3": "서지안"}
REJECTED = re.compile(r"^(?P<who>[^\s:의]+)의?\s.*?:\s*(?P<claim>.+)$", re.S)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["attributions", "rejected_claims"],
    "properties": {
        "attributions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["quote", "target", "claim", "form", "verdict", "source_ids", "reason"],
                "properties": {
                    "quote": {"type": "string"},
                    "target": {"type": "string", "enum": list(TARGETS)},
                    "claim": {"type": "string"},
                    "form": {"type": "string", "enum": list(FORMS)},
                    "verdict": {"type": "string", "enum": list(LABELS)},
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
            },
        },
        "rejected_claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["claim", "verdict", "reason"],
                "properties": {
                    "claim": {"type": "string"},
                    "verdict": {"type": "string", "enum": list(LABELS)},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}


def load_prompts() -> dict[str, Any]:
    return yaml.safe_load((HERE / "attribution_prompts.yaml").read_text("utf-8"))


def judge_model(values: dict[str, str]) -> str:
    return values.get("VC_JUDGE_MODEL") or DEFAULT_MODEL


def api_key_name(model: str) -> str:
    return "OPENAI_API_KEY" if model == "gpt-4o-mini" else "OPENAI_API_KEY_PERSONAL"


def _rate(hit: int, n: int) -> float | None:
    return round(hit / n, 3) if n else None


# ---- 기록 → 순서 있는 대화 ------------------------------------------------------------
#
# 메시지 한 줄: {unit, id, sender, phase, order, content, meta, is_post}. unit은 채점 단위
# (실행·세션), order는 그 단위 안의 순서다. is_post는 판정 대상(AI 동료의 게시 발언, 합의·시간
# 초과 공지 제외).


def _phase_of(session_id: str) -> str:
    """세션 id 어디에든 든 단계 이름(`problem_scenes`·`attribution_keyword` 모두). 없으면 빈 문자열."""  # noqa: E501
    return next((p for p in ("keyword", "problem", "solution") if p in session_id), "")


def transcript_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """persona_eval 사건 목록(run_session 결과)에서. 사건마다 지원자 발언(say) 뒤 AI 발언(posts)
    순이고 단계 시작·카드 자극은 메시지가 아니다. 예전 기록은 발언 id가 없어 자리에 맞춰 만든다.
    단계는 사건의 `phase`(실행기가 시드 단계를 적는다)가 우선이고 없으면 세션 id에서 읽는다.
    게시물 줄에는 원래 post 객체(`_post`)를 붙여 호출자가 (unit, order)로 되찾게 한다."""
    out: list[dict[str, Any]] = []
    counters: Counter = Counter()
    for event in events:
        unit = (event.get("run", 0), event["session"])
        phase = event.get("phase") or _phase_of(event["session"])
        if "say" in event:
            counters[unit] += 1
            out.append(
                {
                    "unit": unit,
                    "id": event.get("message_id") or f"{unit[0]}-{unit[1]}-m{counters[unit]}",
                    "sender": "candidate",
                    "phase": phase,
                    "order": counters[unit],
                    "content": event["say"],
                    "meta": {"intent": event.get("intent")},
                    "is_post": False,
                }
            )
        for post in event.get("posts") or []:
            counters[unit] += 1
            out.append(
                {
                    "unit": unit,
                    "id": post.get("id") or f"{unit[0]}-{unit[1]}-m{counters[unit]}",
                    "sender": post["seat"],
                    "phase": phase,
                    "order": counters[unit],
                    "content": post["content"],
                    "meta": post.get("meta") or {},
                    "is_post": bool(post["content"].strip()),
                    "_post": post,
                }
            )
    return out


def post_keys(transcript: list[dict[str, Any]]) -> dict[int, tuple[tuple, int]]:
    """transcript_from_events 결과에서 {id(post 객체): (unit, order)}. 내용이 같은 발언이 다른
    실행·세션에 있어도 섞이지 않게 내용이 아니라 자리로 잇는다."""
    return {id(m["_post"]): (m["unit"], m["order"]) for m in transcript if "_post" in m}


def transcript_from_session(data: dict[str, Any]) -> list[dict[str, Any]]:
    """가상 지원자 세션 내보내기(drive.py `export_session`)에서. 토론방 메시지만, 지원자 표시 이름은
    "지원자"로 바꾼다. 어시스턴트 채널은 넣지 않는다."""
    s = data["session"]
    session = s[0] if isinstance(s, list) else s
    name = session.get("display_name") or ""
    rows = sorted(
        (m for m in data.get("messages") or [] if m.get("channel", "room") == "room"),
        key=lambda m: (m["created_at"], m.get("seq") or 0),
    )
    out = []
    for order, m in enumerate(rows, 1):
        meta = m.get("meta") or {}
        content = m["content"].replace(name, "지원자") if name else m["content"]
        out.append(
            {
                "unit": (0, session.get("id", "session")),
                "id": m["id"],
                "sender": m["sender"],
                "phase": m.get("phase") or "",
                "order": order,
                "content": content,
                "meta": meta,
                "is_post": m["sender"] in PEERS
                and meta.get("kind") not in FILL_KINDS
                and bool(content.strip()),
            }
        )
    return out


MD_SESSION = re.compile(r"^### (\S+)(?: \((\w+)\))?")
MD_CANDIDATE = re.compile(r"^- \*\*지원자\*\*\((\w+)\): (.*)$")
MD_POST = re.compile(r"^  - ([^:]+): (.*?)(?: `([^`]*)`)?$")
MD_SKIP = re.compile(r"^- \((단계 시작|카드) ?.*\)$")
MD_FLAG_KEYS = ("guard", "stance_check", "quote_check", "quote_violation", "stance_violation")


def md_escape(text: str) -> str:
    """md 한 줄에 발언 하나: 줄바꿈은 `\\n`으로 적는다(되읽을 때 복원)."""
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def md_unescape(text: str) -> str:
    return re.sub(r"\\(\\|n)", lambda m: "\n" if m.group(1) == "n" else "\\", text)


def md_flags(meta: dict[str, Any]) -> str:
    """게시물 줄 끝의 검사 기록(JSON). 값이 pass·none이어도 전부 적어 되읽을 때 linkage가 남는다."""
    kept = {k: meta[k] for k in MD_FLAG_KEYS if meta.get(k) is not None}
    return json.dumps(kept, ensure_ascii=False) if kept else ""


def transcript_from_markdown(
    text: str, names: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """eval-live 페르소나 md의 "## 대화" 절(persona_eval.transcript_md 형식)에서. 반복 실행이 섞인
    md는 세션 제목이 반복되므로 같은 제목이 다시 나오면 다음 실행으로 센다. 세션 제목의 `(단계)`가
    있으면 그 단계, 없으면 세션 id에서 읽는다. 발언 줄 끝의 백틱 JSON이 검사 기록이고, 예전 형식
    (`k=v`, pass·none 생략)이나 기록 없음은 `linkage: unavailable`로 표시해 absent와 구분한다.
    패턴에 맞지 않는 줄은 바로 앞 발언의 이어지는 줄로 붙인다(예전 md의 여러 줄 발언)."""
    seats = {v: k for k, v in (names or NAMES).items()}
    body = text.split("## 대화", 1)[-1]
    out: list[dict[str, Any]] = []
    seen: Counter = Counter()
    unit: tuple[int, str] | None = None
    phase = ""
    order = 0
    for line in body.splitlines():
        if found := MD_SESSION.match(line):
            unit, order = (seen[found.group(1)], found.group(1)), 0
            phase = found.group(2) or _phase_of(found.group(1))
            seen[found.group(1)] += 1
            continue
        if unit is None or not line.strip() or MD_SKIP.match(line):
            continue
        if found := MD_CANDIDATE.match(line):
            order += 1
            out.append(
                {
                    "unit": unit,
                    "id": f"{unit[0]}-{unit[1]}-m{order}",
                    "sender": "candidate",
                    "phase": phase,
                    "order": order,
                    "content": md_unescape(found.group(2).strip()),
                    "meta": {"kind": found.group(1)},
                    "is_post": False,
                }
            )
        elif (found := MD_POST.match(line)) and found.group(1).strip() in seats:
            order += 1
            raw = found.group(3) or ""
            if raw.startswith("{"):
                try:
                    meta = json.loads(raw)
                except json.JSONDecodeError:
                    meta = {"linkage": "unavailable"}
            else:  # 예전 형식: pass·none이 생략돼 있어 검사 기록을 복원할 수 없다
                meta = {"linkage": "unavailable"}
                meta.update(f.split("=", 1) for f in raw.split() if "=" in f)
            out.append(
                {
                    "unit": unit,
                    "id": f"{unit[0]}-{unit[1]}-m{order}",
                    "sender": seats[found.group(1).strip()],
                    "phase": phase,
                    "order": order,
                    "content": md_unescape(found.group(2).strip()),
                    "meta": meta,
                    "is_post": True,
                }
            )
        elif out and out[-1]["unit"] == unit:
            out[-1]["content"] += "\n" + line.strip()
    return out


def load_transcript(path: Path) -> list[dict[str, Any]]:
    """기록 파일 형식을 보고 고른다: 사건 목록(json 배열) · 세션 내보내기(json, messages) · md."""
    if path.suffix == ".md":
        return transcript_from_markdown(path.read_text("utf-8"))
    data = json.loads(path.read_text("utf-8"))
    if isinstance(data, list):
        return transcript_from_events(data)
    if isinstance(data, dict) and "messages" in data:
        return transcript_from_session(data)
    raise ValueError(f"{path}: 사건 목록·세션 내보내기·md 중 어느 것도 아니다")


def sources_before(transcript: list[dict[str, Any]], index: int) -> tuple[list[dict], bool]:
    """(transcript[index] 앞 같은 단위의 원문(id·화자·단계·순서·내용), 상한으로 앞이 잘렸는지).
    미래 발언은 넣지 않는다."""
    me = transcript[index]
    prior = [m for m in transcript[:index] if m["unit"] == me["unit"]]
    truncated = len(prior) > SOURCE_LIMIT
    return (
        [
            {k: m[k] for k in ("id", "sender", "phase", "order", "content")}
            for m in prior[-SOURCE_LIMIT:]
        ],
        truncated,
    )


# ---- 초안 ↔ 최종 연결(PR 10 meta, 없으면 absent) ----------------------------------------


def rejected_claim(meta: dict[str, Any]) -> dict[str, str] | None:
    """`quote_violation`("candidate의 … 돌림: <주장>")에서 (대상, 거부된 주장). 형식이 다르면 전체를
    주장으로 둔다."""
    text = meta.get("quote_violation")
    if not text:
        return None
    found = REJECTED.match(text.strip())
    if not found:
        return {"target": "", "claim": text.strip()}
    who = found.group("who")
    seats = {v: k for k, v in NAMES.items()}
    return {"target": who if who in TARGETS else seats.get(who, ""), "claim": found.group("claim")}


def draft_checks(meta: dict[str, Any]) -> dict[str, Any]:
    """게시물 meta의 검사 결과·재생성 이유. 키가 없으면 "absent"(PR 10 이전 기록·main). 검사 기록을
    복원할 수 없는 기록(예전 md)은 "unknown"으로 두고 linkage 분모에서 뺀다."""
    missing = "unknown" if meta.get("linkage") == "unavailable" else "absent"
    return {
        "quote_check": meta.get("quote_check", missing),
        "quote_violation": meta.get("quote_violation"),
        "stance_check": meta.get("stance_check", missing),
        "stance_violation": meta.get("stance_violation"),
        "guard": meta.get("guard", missing),
        "repeat": meta.get("repeat"),
    }


# ---- 판정 ------------------------------------------------------------


def payload_for(
    transcript: list[dict[str, Any]], index: int, names: dict[str, str] | None = None
) -> dict[str, Any]:
    names = names or NAMES
    post = transcript[index]
    sources, truncated = sources_before(transcript, index)
    rejected = rejected_claim(post["meta"])
    return {
        "participants": {k: names.get(k, k) for k in TARGETS},
        "post": {
            "id": post["id"],
            "speaker": post["sender"],
            "phase": post["phase"],
            "order": post["order"],
            "content": post["content"],
        },
        "sources": [{**m, "speaker_name": names.get(m["sender"], m["sender"])} for m in sources],
        "sources_truncated": truncated,
        "draft_rejected_claims": [rejected] if rejected else [],
    }


def parse_verdict(raw: Any) -> dict[str, Any]:
    """판정 출력을 정규화한다. 모르는 라벨은 undeterminable, 빈 구절은 버린다."""
    seats = {v: k for k, v in NAMES.items()}
    attributions = []
    for a in (raw or {}).get("attributions") or []:
        if not isinstance(a, dict) or not str(a.get("quote", "")).strip():
            continue
        target = str(a.get("target", ""))
        attributions.append(
            {
                "quote": str(a["quote"]).strip(),
                "target": target if target in TARGETS else seats.get(target, target),
                "claim": str(a.get("claim", "")).strip(),
                "form": a.get("form") if a.get("form") in FORMS else "statement",
                "verdict": a.get("verdict") if a.get("verdict") in LABELS else "undeterminable",
                "source_ids": [str(x) for x in a.get("source_ids") or []],
                "reason": str(a.get("reason", "")).strip(),
            }
        )
    rejected = [
        {
            "claim": str(r.get("claim", "")).strip(),
            "verdict": r.get("verdict") if r.get("verdict") in LABELS else "undeterminable",
            "reason": str(r.get("reason", "")).strip(),
        }
        for r in (raw or {}).get("rejected_claims") or []
        if isinstance(r, dict)
    ]
    return {"attributions": attributions, "rejected_claims": rejected}


Ask = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class AttributionJudge:
    """판정 호출. `ask`를 주면(테스트) HTTP 대신 그 함수를 쓴다. `used_models`는 응답이 보고한
    모델."""

    def __init__(self, values: dict[str, str], ask: Ask | None = None) -> None:
        self.model = judge_model(values)
        self.prompts = load_prompts()
        self.used_models: Counter = Counter()
        self.calls = 0
        self._ask = ask
        self.http = None
        if ask is None:
            import httpx

            key = values.get(api_key_name(self.model), "")
            if not key:
                raise RuntimeError(
                    f"{api_key_name(self.model)}가 비어 있다(판정 모델 {self.model})"
                )
            self.http = httpx.AsyncClient(
                base_url="https://api.openai.com/v1",
                headers={"Authorization": f"Bearer {key}"},
                timeout=90,
            )

    async def ask(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self._ask is not None:
            raw = await self._ask(payload)
            self.used_models[self.model] += 1
            return parse_verdict(raw)
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "attributions", "strict": True, "schema": SCHEMA},
            },
            "messages": [
                {"role": "system", "content": self.prompts["attribution"]["system"]},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        response = await self.http.post("/chat/completions", json=body)
        response.raise_for_status()
        data = response.json()
        self.used_models[data.get("model", self.model)] += 1
        return parse_verdict(json.loads(data["choices"][0]["message"]["content"]))

    def info(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "models_used": dict(self.used_models),
            "calls": self.calls,
            "prompt_version": self.prompts.get("version"),
            "date": date.today().isoformat(),
        }

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.aclose()


async def judge_posts(
    transcript: list[dict[str, Any]],
    judge: AttributionJudge,
    names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """게시된 AI 발언마다 한 행: 단위·발언·검사 결과(초안)·귀속 판정(최종)."""
    rows = []
    for index, m in enumerate(transcript):
        if not m["is_post"]:
            continue
        payload = payload_for(transcript, index, names)
        verdict = await judge.ask(payload)
        rows.append(
            {
                "run": m["unit"][0],
                "unit": list(m["unit"]),
                "post_id": m["id"],
                "seat": m["sender"],
                "phase": m["phase"],
                "order": m["order"],
                "content": m["content"],
                "sources": len(payload["sources"]),
                "sources_truncated": payload["sources_truncated"],
                "draft": draft_checks(m["meta"]),
                "attributions": verdict["attributions"],
                "rejected_claims": verdict["rejected_claims"],
            }
        )
    return rows


# ---- 요약 ------------------------------------------------------------


def _attr_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    attrs = [a for r in rows for a in r["attributions"]]
    by = Counter(a["verdict"] for a in attrs)
    posts = len(rows)
    with_attr = [r for r in rows if r["attributions"]]
    errored = [r for r in rows if any(a["verdict"] in ERRORS for a in r["attributions"])]
    hist = Counter(min(len(r["attributions"]), 3) for r in rows)
    return {
        "posts": posts,
        "attributions": len(attrs),
        "per_attribution": {
            **{
                label: {"n": by.get(label, 0), "rate": _rate(by.get(label, 0), len(attrs))}
                for label in LABELS
            },
            "error": {
                "n": sum(by.get(x, 0) for x in ERRORS),
                "rate": _rate(sum(by.get(x, 0) for x in ERRORS), len(attrs)),
            },
        },
        "per_post": {
            "with_attribution": {"n": len(with_attr), "rate": _rate(len(with_attr), posts)},
            "with_overstated": {
                "n": sum(
                    any(a["verdict"] == "overstated" for a in r["attributions"]) for r in rows
                ),
                "of_posts": posts,
                "of_posts_with_attribution": len(with_attr),
            },
            "with_unsupported": {
                "n": sum(
                    any(a["verdict"] == "unsupported" for a in r["attributions"]) for r in rows
                ),
                "of_posts": posts,
                "of_posts_with_attribution": len(with_attr),
            },
            "with_error": {
                "n": len(errored),
                "rate_of_posts": _rate(len(errored), posts),
                "rate_of_posts_with_attribution": _rate(len(errored), len(with_attr)),
            },
        },
        "frequency": {
            "attributions_per_post": round(len(attrs) / posts, 3) if posts else None,
            "histogram": {
                "0": hist.get(0, 0),
                "1": hist.get(1, 0),
                "2": hist.get(2, 0),
                "3+": hist.get(3, 0),
            },
        },
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """건수와 분모를 함께 낸다. 귀속별 비율(분모 귀속 수)과 게시물별 비율(분모 게시물 수·귀속 있는
    게시물 수)을 따로 두고, 과장과 근거 없음을 합치지 않는다. 오류 0은 효과 입증이 아니다."""
    attrs = [a for r in rows for a in r["attributions"]]
    total = _attr_counts(rows)
    by_target = {}
    for target in ("candidate", "peer"):
        sub = [a for a in attrs if (a["target"] == "candidate") == (target == "candidate")]
        by_target[target] = {
            "n": len(sub),
            **{label: sum(a["verdict"] == label for a in sub) for label in LABELS},
        }
    by_form = {
        form: {
            "n": sum(a["form"] == form for a in attrs),
            **{
                label: sum(a["form"] == form and a["verdict"] == label for a in attrs)
                for label in ERRORS
            },
        }
        for form in FORMS
    }
    by_run: dict[str, Any] = {}
    for run in sorted({r["run"] for r in rows}):
        sub = _attr_counts([r for r in rows if r["run"] == run])
        by_run[str(run)] = {
            "posts": sub["posts"],
            "attributions": sub["attributions"],
            "overstated": sub["per_attribution"]["overstated"]["n"],
            "unsupported": sub["per_attribution"]["unsupported"]["n"],
            "error_rate": sub["per_attribution"]["error"]["rate"],
            "attributions_per_post": sub["frequency"]["attributions_per_post"],
            "post_error_rate": sub["per_post"]["with_error"]["rate_of_posts"],
        }

    def span(key: str) -> list[float] | None:
        vals = [v[key] for v in by_run.values() if v[key] is not None]
        return [min(vals), max(vals)] if vals else None

    examples = [
        {
            "seat": r["seat"],
            "phase": r["phase"],
            "verdict": a["verdict"],
            "target": a["target"],
            "quote": a["quote"][:120],
            "claim": a["claim"][:120],
            "reason": a["reason"][:160],
        }
        for r in rows
        for a in r["attributions"]
        if a["verdict"] in ERRORS
    ][:12]
    return {
        **total,
        "by_target": by_target,
        "by_form": by_form,
        "runs": len(by_run),
        "by_run": by_run,
        "ranges": {
            "error_rate": span("error_rate"),
            "post_error_rate": span("post_error_rate"),
            "attributions_per_post": span("attributions_per_post"),
        },
        "sources_truncated_posts": sum(r["sources_truncated"] for r in rows),
        "linkage": linkage(rows),
        "examples": examples,
        "note": "오류 0건은 효과 입증이 아니다. 귀속 빈도와 함께 본다.",
    }


def linkage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """초안 검사 ↔ 최종 판정 연결(PR 10 meta가 있을 때. 없으면 quote_check가 absent로만 센다).

    - 정상 귀속 오탐 재생성: 검사기가 거부한 초안 주장(quote_violation) 중 판정이 supported인 것.
      분모는 판정된 거부 주장 수(undeterminable 제외 분모도 함께).
    - 안전 문구 비율: quote_check=safe · guard=safe · stance_check=fallback 각각 / 게시물 수.
    - 검사 생략 비율: quote_check=skipped / quote_check 키가 있는 게시물 수.
    - 재생성 뒤 최종: 재생성·안전 문구로 끝난 게시물 중 최종 귀속에 오류가 남은 수.

    검사 기록을 복원할 수 없는 게시물(`unknown`, 예전 md)이 하나라도 있는 기록(실행·세션 단위)은
    통째로 위 비율에서 뺀다. 그 기록은 생략된 게시물만 남고 통과한 게시물이 빠져 비율이 왜곡되기
    때문이다. 뺀 기록 수와 게시물 수를 `linkage_unavailable_records`·`_posts`로 따로 센다.
    """
    unavailable = {tuple(r["unit"]) for r in rows if r["draft"]["quote_check"] == "unknown"}
    usable = [r for r in rows if tuple(r["unit"]) not in unavailable]
    posts = len(usable)
    quote = Counter(r["draft"]["quote_check"] for r in usable)
    stance = Counter(r["draft"]["stance_check"] for r in usable)
    guard = Counter(r["draft"]["guard"] for r in usable)
    checked = posts - quote.get("absent", 0)
    rejected = [c for r in usable for c in r["rejected_claims"]]
    rejected_by = Counter(c["verdict"] for c in rejected)
    determined = len(rejected) - rejected_by.get("undeterminable", 0)
    regenerated = [r for r in usable if r["draft"]["quote_check"] in ("regenerated", "safe")]
    still = sum(any(a["verdict"] in ERRORS for a in r["attributions"]) for r in regenerated)
    return {
        "quote_check": dict(quote),
        "stance_check": dict(stance),
        "guard": dict(guard),
        "posts_with_linkage": posts,
        "quote_checked_posts": checked,
        "linkage_unavailable_records": len(unavailable),
        "linkage_unavailable_posts": len(rows) - posts,
        "skip_rate": {
            "n": quote.get("skipped", 0),
            "of": checked,
            "rate": _rate(quote.get("skipped", 0), checked),
        },
        "safe_rate": {
            "quote_safe": {"n": quote.get("safe", 0), "rate": _rate(quote.get("safe", 0), posts)},
            "guard_safe": {"n": guard.get("safe", 0), "rate": _rate(guard.get("safe", 0), posts)},
            "stance_fallback": {
                "n": stance.get("fallback", 0),
                "rate": _rate(stance.get("fallback", 0), posts),
            },
        },
        "false_positive_regeneration": {
            "rejected_claims": len(rejected),
            "judged_supported": rejected_by.get("supported", 0),
            "judged_overstated": rejected_by.get("overstated", 0),
            "judged_unsupported": rejected_by.get("unsupported", 0),
            "undeterminable": rejected_by.get("undeterminable", 0),
            "rate": _rate(rejected_by.get("supported", 0), len(rejected)),
            "rate_of_determined": _rate(rejected_by.get("supported", 0), determined),
        },
        "after_regeneration": {
            "posts": len(regenerated),
            "final_still_error": still,
            "final_clean": len(regenerated) - still,
        },
    }


def compact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """기록용 행(원문 전체 대신 요지)."""
    return [
        {
            "unit": r["unit"],
            "post_id": r["post_id"],
            "seat": r["seat"],
            "phase": r["phase"],
            "content": r["content"][:200],
            "draft": {k: v for k, v in r["draft"].items() if v not in (None, "absent")},
            "attributions": r["attributions"],
            "rejected_claims": r["rejected_claims"],
        }
        for r in rows
    ]


# ---- 명령줄 ------------------------------------------------------------


async def _judge_files(paths: list[Path], judge: AttributionJudge) -> dict[str, Any]:
    per_file = {}
    all_rows: list[dict[str, Any]] = []
    for offset, path in enumerate(paths):
        transcript = load_transcript(path)
        rows = await judge_posts(transcript, judge)
        for r in rows:  # 파일마다 다른 실행으로 세어 파일 간 범위를 낸다
            r["run"] = f"{offset}:{r['run']}"
        per_file[path.name] = summarize(rows)
        all_rows += rows
    return {
        "judge": judge.info(),
        "files": list(per_file),
        "combined": summarize(all_rows),
        "per_file": per_file,
        "rows": compact_rows(all_rows),
    }


def attribution_id(item_id: str, n: int) -> str:
    return f"{item_id}#a{n}"


async def extract_items(
    items: list[dict[str, Any]], judge: AttributionJudge, force: bool = False
) -> list[dict[str, Any]]:
    """항목마다 귀속을 뽑아 `extracted_attributions`(안정 id `attribution_id`, verdict 없음)를
    채우고 `extraction: {model, date}` 표식을 남긴다. 표식이 있는 항목(귀속이 0개여도)은 `force`가
    아니면 건드리지 않는다. 라벨은 그 id로 붙이므로 추출을 다시 하면 기존 라벨과 어긋난다. 돌려주는
    값은 판정 모델의 라벨 행(이번에 추출한 항목만)."""
    labels = []
    for item in items:
        if item.get("extraction") and not force:
            continue
        payload = {
            "participants": {k: NAMES[k] for k in TARGETS},
            "post": item["post"],
            "sources": [
                {**m, "speaker_name": NAMES.get(m["sender"], m["sender"])} for m in item["sources"]
            ],
            "sources_truncated": bool(item.get("sources_truncated", False)),
            "draft_rejected_claims": [],
        }
        verdict = await judge.ask(payload)
        item["extracted_attributions"] = [
            {
                "attribution_id": attribution_id(item["item_id"], n),
                **{k: a[k] for k in ("quote", "target", "claim", "form")},
            }
            for n, a in enumerate(verdict["attributions"], 1)
        ]
        item["extraction"] = {"model": judge.model, "date": date.today().isoformat()}
        labels.append(
            {
                "item_id": item["item_id"],
                "labels": [
                    {
                        "attribution_id": attribution_id(item["item_id"], n),
                        "verdict": a["verdict"],
                        "reason": a["reason"],
                    }
                    for n, a in enumerate(verdict["attributions"], 1)
                ],
                "missed_attributions": [],
            }
        )
    return labels


def merge_labels(
    existing: list[dict[str, Any]], fresh: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """라벨 파일 병합(항목 단위). 이번에 추출한 항목은 새 행으로 바꾸고(옛 라벨은 버림), 건너뛴
    항목의 행은 그대로 둔다."""
    rows = {r["item_id"]: r for r in existing}
    rows.update({r["item_id"]: r for r in fresh})
    return list(rows.values())


async def _extract(
    items_path: Path, labels_path: Path | None, judge: AttributionJudge, force: bool
) -> None:
    items = [json.loads(line) for line in items_path.read_text("utf-8").splitlines() if line]
    labels = await extract_items(items, judge, force)
    items_path.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), "utf-8")
    labels_path = labels_path or items_path.with_name(items_path.stem + "-judge-labels.jsonl")
    existing = (
        [json.loads(line) for line in labels_path.read_text("utf-8").splitlines() if line]
        if labels_path.is_file()
        else []
    )
    merged = merge_labels(existing, labels)
    labels_path.write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in merged), "utf-8"
    )
    print(
        json.dumps(
            {
                "items": len(items),
                "extracted_now": len(labels),
                "labels": str(labels_path),
                **judge.info(),
            },
            ensure_ascii=False,
        )
    )


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    j = sub.add_parser("judge", help="기록된 실행을 다시 판정해 요약")
    j.add_argument("paths", nargs="+", type=Path)
    j.add_argument("--out", type=Path, default=None)
    e = sub.add_parser("extract", help="보정 세트 항목의 귀속 추출(+판정 라벨 파일)")
    e.add_argument("items", type=Path)
    e.add_argument("--labels", type=Path, default=None)
    e.add_argument(
        "--force", action="store_true", help="이미 추출된 항목도 다시 뽑는다(라벨과 어긋남)"
    )
    args = parser.parse_args()
    sys.path[:0] = [str(HERE.parents[1] / "tests")]
    from e2e.client import env

    judge = AttributionJudge(env())
    try:
        if args.command == "judge":
            result = await _judge_files(args.paths, judge)
            text = json.dumps(result, ensure_ascii=False, indent=2)
            if args.out:
                args.out.write_text(text, "utf-8")
            print(
                json.dumps(
                    {"judge": result["judge"], "combined": result["combined"]},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            await _extract(args.items, args.labels, judge, args.force)
    finally:
        await judge.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
