"""보정 세트 라벨 일치도·혼동행렬·귀속 추출 재현율(docs/guides/testing.md "귀속 판정").

    uv run --project services/api python tests/eval/calibration_agreement.py \\
        --items ~/day-zero-ai-data/calibration/misattribution-v1.jsonl \\
        --labels A=<라벨 A.jsonl> B=<라벨 B.jsonl> judge=<판정 라벨.jsonl> [--split calib|holdout]

라벨 파일 한 줄 = 항목 하나: {"item_id", "labels": [{"index": 추출 귀속 번호, "verdict": 라벨}],
"missed_attributions": ["추출에 빠진 귀속 설명", …]}. 라벨은 supported·overstated·unsupported·
undeterminable. 같은 (item_id, index)에 두 라벨러가 모두 라벨을 냈을 때만 일치도에 든다.

출력: 라벨러 쌍마다 일치율·Cohen's kappa·혼동행렬(행 = 첫째, 열 = 둘째), 항목 유형별 일치율,
불일치 항목 목록(팀이 확인할 것), 귀속 추출 재현율 = 추출 귀속 / (추출 귀속 + 라벨러가 빠졌다고
적은 귀속) 과 고정 초안의 gold 대비 재현율(문구 유사도로 짝지음). 사람 정답이 아니라 모델 두 개의
라벨이므로 결과는 "두 모델이 같이 본 것"이지 정답률이 아니다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from typing import Any

sys.path[:0] = [str(Path(__file__).resolve().parents[1])]
from eval.attribution import LABELS  # noqa: E402

MATCH = 0.45  # gold 귀속과 추출 귀속을 같은 것으로 보는 claim 유사도


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def label_map(rows: list[dict[str, Any]]) -> dict[tuple[str, int], str]:
    out = {}
    for row in rows:
        for lab in row.get("labels") or []:
            verdict = lab.get("verdict")
            if verdict in LABELS:
                out[(row["item_id"], int(lab["index"]))] = verdict
    return out


def confusion(a: dict, b: dict) -> dict[str, Any]:
    """같은 키에 둘 다 라벨이 있는 것만. 행 a, 열 b."""
    keys = sorted(set(a) & set(b))
    matrix = {x: {y: 0 for y in LABELS} for x in LABELS}
    for k in keys:
        matrix[a[k]][b[k]] += 1
    n = len(keys)
    agree = sum(matrix[x][x] for x in LABELS)
    pa = agree / n if n else None
    pe = (
        sum((sum(matrix[x].values()) / n) * (sum(matrix[y][x] for y in LABELS) / n) for x in LABELS)
        if n
        else None
    )
    kappa = None if n == 0 or pe is None or pe == 1 else round((pa - pe) / (1 - pe), 3)
    # 오류 여부(과장·근거 없음 vs 나머지)만 보는 이진 일치
    err = {"overstated", "unsupported"}
    binary = sum((a[k] in err) == (b[k] in err) for k in keys)
    return {
        "n": n,
        "agree": agree,
        "rate": round(pa, 3) if pa is not None else None,
        "kappa": kappa,
        "binary_error_agree": {
            "n": n,
            "agree": binary,
            "rate": round(binary / n, 3) if n else None,
        },
        "matrix": matrix,
        "disagreements": [
            {"item_id": k[0], "index": k[1], "a": a[k], "b": b[k]} for k in keys if a[k] != b[k]
        ],
    }


def by_type(items: dict[str, dict], a: dict, b: dict) -> dict[str, Any]:
    out: dict[str, dict[str, int]] = {}
    for k in set(a) & set(b):
        item = items.get(k[0]) or {}
        key = item.get("type") or f"recorded:{item.get('type_hint')}"
        bucket = out.setdefault(key, {"n": 0, "agree": 0})
        bucket["n"] += 1
        bucket["agree"] += a[k] == b[k]
    return out


def extraction_recall(
    items: list[dict[str, Any]], labels: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    extracted = sum(len(i.get("extracted_attributions") or []) for i in items)
    missed = {
        name: sum(
            len(r.get("missed_attributions") or [])
            for r in rows
            if r["item_id"] in {i["item_id"] for i in items}
        )
        for name, rows in labels.items()
    }
    gold_n = gold_hit = 0
    gold_misses = []
    for item in items:
        for g in item.get("gold_attributions") or []:
            gold_n += 1
            best = max(
                (
                    SequenceMatcher(None, g["claim"], e.get("claim", "")).ratio()
                    for e in item.get("extracted_attributions") or []
                    if e.get("target") == g.get("target")
                ),
                default=0.0,
            )
            if best >= MATCH:
                gold_hit += 1
            else:
                gold_misses.append({"item_id": item["item_id"], "claim": g["claim"]})
    no_attr_items = [i for i in items if i.get("type") == "no_attribution"]
    false_extractions = sum(bool(i.get("extracted_attributions")) for i in no_attr_items)
    return {
        "extracted": extracted,
        "missed_by_labeler": missed,
        "recall_vs_labeler": {
            name: round(extracted / (extracted + m), 3) if extracted + m else None
            for name, m in missed.items()
        },
        "gold": {
            "n": gold_n,
            "hit": gold_hit,
            "recall": round(gold_hit / gold_n, 3) if gold_n else None,
            "misses": gold_misses,
        },
        "no_attribution_items": {"n": len(no_attr_items), "extracted_something": false_extractions},
    }


def report(
    items: list[dict[str, Any]], labels: dict[str, list[dict[str, Any]]], split: str | None
) -> dict[str, Any]:
    if split:
        items = [i for i in items if i.get("split") == split]
    ids = {i["item_id"] for i in items}
    by_id = {i["item_id"]: i for i in items}
    maps = {
        name: {k: v for k, v in label_map(rows).items() if k[0] in ids}
        for name, rows in labels.items()
    }
    pairs = {}
    for a, b in combinations(sorted(maps), 2):
        pairs[f"{a}~{b}"] = {
            **confusion(maps[a], maps[b]),
            "by_type": by_type(by_id, maps[a], maps[b]),
        }
    distribution = {name: dict(Counter(m.values())) for name, m in maps.items()}
    return {
        "split": split or "all",
        "items": len(items),
        "labelers": sorted(maps),
        "labels": {name: len(m) for name, m in maps.items()},
        "distribution": distribution,
        "pairs": pairs,
        "extraction": extraction_recall(items, labels),
        "note": "두 모델의 라벨이지 사람 정답이 아니다. 불일치 항목만 팀이 확인한다.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--items", type=Path, required=True)
    parser.add_argument("--labels", nargs="+", required=True, help="이름=경로")
    parser.add_argument("--split", choices=("calib", "holdout"), default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    labels = {}
    for spec in args.labels:
        name, _, path = spec.partition("=")
        if not path:
            parser.error(f"--labels는 이름=경로: {spec}")
        labels[name] = read_jsonl(Path(path).expanduser())
    result = report(read_jsonl(args.items.expanduser()), labels, args.split)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, "utf-8")
    print(text)


if __name__ == "__main__":
    main()
