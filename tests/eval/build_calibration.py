"""귀속 판정 보정 세트 만들기(docs/guides/testing.md "귀속 판정").

    uv run --project services/api python tests/eval/build_calibration.py \\
        [--data ~/day-zero-ai-data/metrics/pr10] \\
        [--out ~/day-zero-ai-data/calibration/misattribution-v1.jsonl] [--size 50] [--seed 1]

기록된 실행(`virtual/`·`review-fix/` 아래 session.json과 persona_eval 사건 덤프, 그 두 폴더와 루트의
eval-live 페르소나 md)에서 AI 동료 게시물을 뽑고, 유형별 고정 초안(정상·과장·화자 혼동·실제 번복·
귀속 없음·전제 질문·생략형·동료 말 인용)을 더해 40~60개 항목을 만든다. 항목마다 게시 전 원문
(id·화자·단계·순서)을 붙이고 `calib`·`holdout`으로 나눈다. 결과는 저장소 밖에 쓴다(실 세션 대화는
저장소에 넣지 않는다). 귀속 추출(`extracted_attributions`)은 `attribution.py extract`가 채운다.

항목: {item_id, split, kind(recorded|handwritten), type(고정 초안의 유형, 기록 항목은 type_hint),
source(파일 이름만), post{id, speaker, phase, order, content},
sources[{id, sender, phase, order, content}], sources_truncated(원문 상한으로 앞이 잘렸는지),
draft_checks, extracted_attributions[], gold_attributions[](고정 초안만: 기대 귀속과 verdict)}.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

sys.path[:0] = [str(Path(__file__).resolve().parents[1])]
from eval import attribution  # noqa: E402

HOME = Path.home()
DEFAULT_DATA = HOME / "day-zero-ai-data" / "metrics" / "pr10"
DEFAULT_OUT = HOME / "day-zero-ai-data" / "calibration" / "misattribution-v1.jsonl"
MENTION = re.compile(r"지원자|님|강한결|윤하람|서지안|한결|하람|지안|말씀|하셨|라고|다들")
HOLDOUT_EVERY = 3  # 섞은 뒤 3개마다 1개를 holdout


def _msg(mid: str, sender: str, phase: str, order: int, content: str) -> dict[str, Any]:
    return {"id": mid, "sender": sender, "phase": phase, "order": order, "content": content}


def handwritten() -> list[dict[str, Any]]:
    """유형별 고정 초안(합성, 실제 사람 발언 아님). gold_attributions는 기대 귀속·verdict."""
    base = [
        _msg(
            "h-c1",
            "candidate",
            "problem",
            1,
            "저는 가뭄도 중요하다고 생각하지만, 침수가 더 급하다고 봐요.",
        ),
        _msg(
            "h-p1",
            "p1",
            "problem",
            2,
            "저는 가뭄이 핵심이라고 봅니다. 생활용수 제한이 매년 반복돼요.",
        ),
        _msg("h-p3", "p3", "problem", 3, "가뭄은 예측 데이터가 있으니 대응이 쉬운 편 아닐까요?"),
        _msg(
            "h-c2", "candidate", "problem", 4, "B도 괜찮은 것 같아요. 그래도 아직 정한 건 아니에요."
        ),
    ]
    sol = [
        _msg(
            "s-c1",
            "candidate",
            "solution",
            1,
            "비용이 센서보다 낮다는 자료가 있으면 캠페인으로 가도 괜찮아요.",
        ),
        _msg(
            "s-p2",
            "p2",
            "solution",
            2,
            "저… 빗물 저금통 같은 생활 인프라도 생각해 볼 수 있을 것 같아요.",
        ),
        _msg(
            "s-c2",
            "candidate",
            "solution",
            3,
            "생각해 보니 지안님 말씀이 맞네요. 저 캠페인으로 바꿀게요.",
        ),
    ]

    def item(iid, type_, speaker, phase, content, sources, gold):
        return {
            "item_id": iid,
            "kind": "handwritten",
            "type": type_,
            "source": "handwritten",
            "post": {
                "id": iid,
                "speaker": speaker,
                "phase": phase,
                "order": sources[-1]["order"] + 1,
                "content": content,
            },
            "sources": sources,
            "sources_truncated": False,
            "draft_checks": {},
            "extracted_attributions": [],
            "gold_attributions": gold,
        }

    return [
        item(
            "hw-normal-1",
            "normal",
            "p3",
            "problem",
            "지원자님은 침수가 더 급하다고 보셨는데, 피해 규모 근거가 있을까요?",
            base[:1],
            [
                {
                    "target": "candidate",
                    "claim": "지원자가 침수가 더 급하다고 봄",
                    "verdict": "supported",
                }
            ],
        ),
        item(
            "hw-overstated-1",
            "overstated",
            "p1",
            "problem",
            "지원자님도 가뭄이 중요하다고 동의하셨으니 가뭄으로 정리하죠.",
            base[:2],
            [{"target": "candidate", "claim": "지원자가 가뭄에 동의함", "verdict": "overstated"}],
        ),
        item(
            "hw-overstated-2",
            "overstated",
            "p3",
            "problem",
            "지원자님이 B가 제일 낫다고 하셨는데, 근거를 더 들어볼 수 있을까요?",
            base,
            [
                {
                    "target": "candidate",
                    "claim": "지원자가 B가 제일 낫다고 함",
                    "verdict": "overstated",
                }
            ],
        ),
        item(
            "hw-wrong-speaker-1",
            "wrong_speaker",
            "p1",
            "problem",
            "지원자님 말씀처럼 가뭄은 예측 데이터가 있어 대응이 쉽죠.",
            base[:3],
            [
                {
                    "target": "candidate",
                    "claim": "지원자가 가뭄은 예측 데이터로 대응이 쉽다고 함",
                    "verdict": "unsupported",
                }
            ],
        ),
        item(
            "hw-reversal-1",
            "real_reversal",
            "p1",
            "solution",
            "지원자님이 캠페인으로 바꾸셨으니, 센서 쪽 근거를 한 번만 더 들어 주세요.",
            sol,
            [
                {
                    "target": "candidate",
                    "claim": "지원자가 캠페인으로 입장을 바꿈",
                    "verdict": "supported",
                }
            ],
        ),
        item(
            "hw-none-1",
            "no_attribution",
            "p2",
            "problem",
            "저는 침수 피해가 생활에 더 가깝다고 생각해요. 자료를 같이 볼까요?",
            base[:2],
            [],
        ),
        item(
            "hw-none-2",
            "no_attribution",
            "p3",
            "solution",
            "지원자님, 캠페인의 지속성은 어떻게 확보할 수 있을까요?",
            sol[:1],
            [],
        ),
        item(
            "hw-presup-1",
            "presupposing_question",
            "p3",
            "problem",
            "지원자님은 B 편이시죠? 그럼 근거를 같이 정리해 봐요.",
            base,
            [
                {
                    "target": "candidate",
                    "claim": "지원자가 B 편임",
                    "verdict": "overstated",
                    "form": "presupposing_question",
                }
            ],
        ),
        item(
            "hw-elliptical-1",
            "elliptical",
            "p1",
            "solution",
            "다들 캠페인으로 가자는 분위기니 저도 조건을 좁혀 볼게요.",
            sol[:2],
            [
                {
                    "target": "candidate",
                    "claim": "지원자가 캠페인으로 가자고 함",
                    "verdict": "overstated",
                    "form": "elliptical",
                }
            ],
        ),
        item(
            "hw-peer-quote-1",
            "quoting_persona",
            "p1",
            "solution",
            "하람님이 말씀하신 빗물 저금통은 설치 비용이 걸리고, 지원자님 조건(비용 자료)도 "
            "아직이에요.",
            sol[:2],
            [
                {"target": "p2", "claim": "윤하람이 빗물 저금통을 제안함", "verdict": "supported"},
                {
                    "target": "candidate",
                    "claim": "지원자가 비용 자료를 조건으로 걸었음",
                    "verdict": "supported",
                },
            ],
        ),
    ]


def recorded(data_dir: Path) -> list[dict[str, Any]]:
    """기록된 실행에서 후보 항목(게시물 + 그 전 원문)을 모은다."""
    files: list[Path] = []
    for sub in ("virtual", "review-fix"):
        root = data_dir / sub
        if root.is_dir():
            files += sorted(root.rglob("session.json"))
            files += sorted(p for p in root.glob("*.json") if p.name.startswith("persona-"))
            files += sorted(root.glob("eval-live-persona-*.md"))
    files += sorted(data_dir.glob("eval-live-persona-*.md"))
    items = []
    for path in files:
        try:
            transcript = attribution.load_transcript(path)
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        for index, m in enumerate(transcript):
            if not m["is_post"] or m["phase"] not in ("keyword", "problem", "solution"):
                continue
            sources, truncated = attribution.sources_before(transcript, index)
            if not sources:
                continue
            key = hashlib.sha1(f"{path.name}|{m['unit']}|{m['order']}".encode()).hexdigest()[:10]
            items.append(
                {
                    "item_id": f"rec-{key}",
                    "kind": "recorded",
                    "type": None,
                    "type_hint": "mention" if MENTION.search(m["content"]) else "plain",
                    "source": path.name if path.name != "session.json" else path.parent.name,
                    "post": {
                        "id": m["id"],
                        "speaker": m["sender"],
                        "phase": m["phase"],
                        "order": m["order"],
                        "content": m["content"],
                    },
                    "sources": sources,
                    "sources_truncated": truncated,
                    "draft_checks": {
                        k: v
                        for k, v in attribution.draft_checks(m["meta"]).items()
                        if v not in (None, "absent")
                    },
                    "extracted_attributions": [],
                    "gold_attributions": [],
                }
            )
    return items


def select(pool: list[dict[str, Any]], size: int, seed: int) -> list[dict[str, Any]]:
    """같은 문장은 하나만, 언급이 있는 게시물 3 : 없는 게시물 1로 고정 난수로 뽑는다."""
    rng = random.Random(seed)
    seen: set[str] = set()
    unique = []
    for item in pool:
        text = re.sub(r"\s+", "", item["post"]["content"])
        if text not in seen:
            seen.add(text)
            unique.append(item)
    mention = [i for i in unique if i["type_hint"] == "mention"]
    plain = [i for i in unique if i["type_hint"] == "plain"]
    rng.shuffle(mention)
    rng.shuffle(plain)
    want_plain = max(1, size // 4)
    chosen = mention[: size - want_plain] + plain[:want_plain]
    if len(chosen) < size:  # 한쪽이 모자라면 다른 쪽으로 채운다
        rest = [i for i in mention + plain if i not in chosen]
        chosen += rest[: size - len(chosen)]
    return chosen


def split(items: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed + 1)
    order = list(items)
    rng.shuffle(order)
    for n, item in enumerate(order):
        item["split"] = "holdout" if n % HOLDOUT_EVERY == HOLDOUT_EVERY - 1 else "calib"
    return sorted(order, key=lambda i: (i["kind"], i["item_id"]))


def build(data_dir: Path, size: int, seed: int) -> list[dict[str, Any]]:
    fixed = handwritten()
    pool = recorded(data_dir)
    chosen = select(pool, max(0, size - len(fixed)), seed)
    return split(fixed + chosen, seed)


def summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    for i in items:
        key = i["type"] or f"recorded:{i.get('type_hint')}"
        by_type[key] = by_type.get(key, 0) + 1
    return {
        "items": len(items),
        "calib": sum(i["split"] == "calib" for i in items),
        "holdout": sum(i["split"] == "holdout" for i in items),
        "by_type": by_type,
        "sources": sorted({i["source"] for i in items}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--size", type=int, default=50)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    if not 40 <= args.size <= 60:
        parser.error("--size는 40~60")
    items = build(args.data, args.size, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), "utf-8")
    print(json.dumps({"out": str(args.out), **summary(items)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
