"""docs/product/rubric.md의 BARS 표를 읽는 파서.

rubric.yaml이 정본 문서의 표(차원 목록·수준 앵커·담당자 참고 문구·기록 없음 사유와 상태)와
글자 그대로 같은지 검사할 때 쓴다. 표준 라이브러리만 쓴다.
"""

import re
from pathlib import Path

ELEMENT_HEADING = re.compile(r"^### 3\.\d (?P<name>\S+) `(?P<id>[a-z]+)`")
DIM_ROW = re.compile(
    r"^\| `(?P<id>(pers|listen|incl|flex|hon)_[a-z_]+)` \| (?P<name>[^|]+?) "
    r"\| (?P<definition>[^|]+?) \| (?P<positive>[^|]+?) \| (?P<negative>[^|]+?) \|$"
)
ANCHOR_HEADING = re.compile(
    r"^\*\*`(?P<id>(pers|listen|incl|flex|hon)_[a-z_]+)` (?P<name>[^*]+)\*\*$"
)
LEVEL_ROW = re.compile(r"^\| (?P<level>[1-5]) \| (?P<anchor>[^|]+?) \| (?P<example>.+?) \|$")
HINT_ROW = re.compile(
    r"^\| `(?P<id>(pers|listen|incl|flex|hon)_[a-z_]+)` \| (?P<h5>[^|]+?) \| (?P<h4>[^|]+?) "
    r"\| (?P<h3>[^|]+?) \| (?P<h2>[^|]+?) \| (?P<h1>[^|]+?) \|$"
)
REASON_ROW = re.compile(
    r"^\| `(?P<code>[a-z_]+)` \| (?P<state>[^|`]+?) \| (?P<meaning>[^|]+?) \| (?P<label>[^|]+?) \|$"
)


def default_doc_path() -> Path:
    # scenarios/missions/debate/tests/rubric_doc.py → 저장소 루트
    return Path(__file__).resolve().parents[4] / "docs" / "product" / "rubric.md"


def parse_rubric(path: Path | None = None) -> dict:
    """rubric.md에서 요소·차원·앵커·참고 문구·기록 없음 사유를 뽑는다."""
    text = (path or default_doc_path()).read_text(encoding="utf-8")
    lines = text.splitlines()
    elements: list[dict] = []
    dims: dict[str, dict] = {}
    current_el: dict | None = None
    current_anchor: str | None = None
    in_hints = False
    in_reasons = False
    reasons: list[dict] = []

    for line in lines:
        m = ELEMENT_HEADING.match(line)
        if m:
            current_el = {"element": m["id"], "name": m["name"], "dimensions": []}
            elements.append(current_el)
            current_anchor = None
            in_hints = False
            continue
        if line.startswith("## "):
            current_el = None if not line.startswith("## 3.") else current_el
            in_reasons = line.startswith("## 5.")
            current_anchor = None
            in_hints = False
            continue
        if in_reasons:
            r = REASON_ROW.match(line)
            if r:
                reasons.append(
                    {"code": r["code"], "state": r["state"].strip(), "label": r["label"].strip()}
                )
            continue
        if current_el is None:
            continue
        if line.startswith("**정의**:"):
            current_el["definition"] = line.removeprefix("**정의**:").strip()
            continue
        if line.startswith("**담당자 참고 문구**"):
            in_hints = True
            current_anchor = None
            continue
        if in_hints:
            h = HINT_ROW.match(line)
            if h and h["id"] in dims:
                dims[h["id"]]["hints"] = {
                    5: h["h5"].strip(),
                    4: h["h4"].strip(),
                    3: h["h3"].strip(),
                    2: h["h2"].strip(),
                    1: h["h1"].strip(),
                }
            if line.startswith("공통 안내:"):
                current_el["reviewer_notice"] = line.removeprefix("공통 안내:").strip()
            continue
        d = DIM_ROW.match(line)
        if d and d["id"] not in dims:
            dim = {
                "dimension_id": d["id"],
                "name": d["name"].strip(),
                "definition": d["definition"].strip(),
                "positive": d["positive"].strip(),
                "negative": d["negative"].strip(),
                "levels": {},
            }
            dims[d["id"]] = dim
            current_el["dimensions"].append(dim)
            continue
        a = ANCHOR_HEADING.match(line)
        if a:
            current_anchor = a["id"]
            continue
        if current_anchor:
            lv = LEVEL_ROW.match(line)
            if lv:
                dims[current_anchor]["levels"][int(lv["level"])] = {
                    "anchor": lv["anchor"].strip(),
                    "example": lv["example"].strip(),
                }
                continue
            if line.startswith("기록 없음:"):
                dims[current_anchor]["no_record"] = line.removeprefix("기록 없음:").strip()
                current_anchor = None

    return {"elements": elements, "no_record_reasons": reasons}
