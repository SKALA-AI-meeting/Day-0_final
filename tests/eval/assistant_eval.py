"""어시스턴트 근거 실행기와 채점(docs/guides/testing.md "어시스턴트 근거 픽스처", 결정 D1).

질문(assistant_cases.yaml)마다 어시스턴트 답 {content, cited_material_ids, tool_calls}를
받아 규칙으로 채점한다.
- live(`make eval-live`): 실 스택 공개 API로 15분 데모 채용 건에 세션 하나를 만들고
  키워드를 "물"로 확정해 자료를 연 뒤(지원자 확정 API) `POST /sessions/{id}/assistant`로
  묻는다. 실제 rag-mcp·실제 모델을 쓰고, 끝나면 세션을 취소한다. 관리자 계정은
  virtual_candidate/drive.py와 같은 seed 기본값(출력하지 않음).
- mock(`make test-e2e`): api 에이전트 테스트의 가짜 rag와 FakeLLM으로 같은 채점 경로를 돈다.

채점(LLM 판정 없음):
- 숫자: 답의 숫자 토큰(한 자리는 단위가 붙은 것만)이 열린 자료 원문의 숫자 토큰에 있는지
- 출처 줄: 도구 결과가 없는데 "출처:" 줄이 붙었는지
- 잘림: 마지막 줄이 문장으로 끝나지 않거나 글자 수 상한(guard max_chars)에 닿았는지
- 인용: grounded·numbers는 인용 1개 이상이고 모두 cite_from 안(쓴 자료만),
  out_of_materials는 인용 0
- 자료에 없음: out_of_materials는 "자료에 없다"는 취지 문장
"""

import re
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MISSION = ROOT / "scenarios" / "missions" / "debate"
GROUPS = ("grounded", "out_of_materials", "numbers", "long")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
SENTENCE_END = re.compile(r"[.!?…)\]」』”\"'요다죠까음함]\s*$")
MISSING = re.compile(r"자료.{0,20}(없|않|못\s*찾)")
UNIT = re.compile(r"[분%개원톤일배명년세곳회㎡주월시만억]")
SOURCE_LINE = re.compile(r"^\s*출처\s*:", re.MULTILINE)


def load_cases() -> dict[str, Any]:
    return yaml.safe_load((HERE / "assistant_cases.yaml").read_text("utf-8"))


def max_chars() -> int:
    spec = yaml.safe_load((MISSION / "prompts" / "guard_patterns.yaml").read_text("utf-8"))
    return int(spec.get("max_chars", 400))


def keyword_materials(keyword: str) -> list[dict[str, Any]]:
    words = yaml.safe_load((MISSION / "keywords.yaml").read_text("utf-8"))["keywords"]
    return next(k for k in words if k["key"] == keyword)["materials"]


def corpus(keyword: str) -> str:
    return "\n".join(
        (MISSION / "materials" / m["file"]).read_text("utf-8") for m in keyword_materials(keyword)
    )


def _numbers(text: str) -> list[tuple[str, str]]:
    """(숫자, 바로 뒤 단위 글자 또는 "") 목록. 쉼표는 지운다."""
    out = []
    for found in NUMBER.finditer(text):
        value = found.group(0).rstrip(",.").replace(",", "")
        rest = text[found.end() : found.end() + 2].lstrip()
        unit = rest[:1] if UNIT.match(rest[:1]) else ""
        out.append((value, unit))
    return out


def ungrounded_numbers(text: str, source: str) -> list[str]:
    """답의 숫자 중 자료 원문에 없는 것.

    단위가 붙은 네 자리 미만 숫자는 (숫자, 단위) 쌍으로 비교한다(25분을 2분으로 쓰는 오기).
    단위 없는 숫자는 두 자리 이상만 숫자로 비교한다(목록 번호와 섞이기 때문).
    """
    pairs = set(_numbers(source))
    values = {v for v, _ in pairs}
    bare = {v for v, u in pairs if not u}  # 표 칸처럼 단위 없이 나온 숫자는 단위를 붙여 써도 된다
    out = []
    for value, unit in _numbers(text):
        if unit and len(value) < 4:
            ok = (value, unit) in pairs or value in bare
        elif len(value) >= 2:
            ok = value in values
        else:
            continue
        if not ok:
            out.append(value + unit)
    return out


def truncated(text: str, limit: int) -> bool:
    body = text.strip()
    lines = [x for x in body.splitlines() if x.strip() and not SOURCE_LINE.match(x)]
    last = lines[-1] if lines else ""
    return len(body) >= limit - 2 or not SENTENCE_END.search(last)


def score_case(
    case: dict[str, Any], answer: dict[str, Any], source: str, limit: int
) -> dict[str, Any]:
    text = answer.get("content") or ""
    cited = list(answer.get("cited_material_ids") or [])
    tools = list(answer.get("tool_calls") or [])
    expect = case.get("expect") or {}
    problems = []
    numbers = ungrounded_numbers(text, source)
    if numbers:
        problems.append(f"자료에 없는 숫자 {numbers}")
    if SOURCE_LINE.search(text) and not tools:
        problems.append("도구 결과 없이 출처 줄")
    if truncated(text, limit):
        problems.append("잘림 의심")
    if case["group"] in ("grounded", "numbers"):
        allowed = set(expect.get("cite_from") or [])
        if not cited:
            problems.append("인용 없음")
        elif allowed and not set(cited) <= allowed:
            problems.append(f"쓰지 않은 자료까지 인용 {cited}")
    if case["group"] == "out_of_materials" and cited:
        problems.append(f"자료 밖 질문에 인용 {cited}")
    if expect.get("says_missing") and not MISSING.search(text):
        problems.append("자료에 없다는 답 없음")
    patterns = expect.get("must_include") or []
    if patterns and not any(re.search(p, text) for p in patterns):
        problems.append("기대 내용 없음")
    for pattern in expect.get("must_not") or []:
        if re.search(pattern, text):
            problems.append(f"금지 패턴 {pattern}")
    return {
        "id": case["id"],
        "group": case["group"],
        "pass": not problems,
        "problems": problems,
        "chars": len(text),
        "cited": cited,
        "tools": len(tools),
    }


def score(cases: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[bool]] = {}
    for row in rows:
        groups.setdefault(row["group"], []).append(row["pass"])
    passed = sum(r["pass"] for r in rows)
    flag = {
        "ungrounded_number": sum(any("숫자" in p for p in r["problems"]) for r in rows),
        "source_without_tool": sum(any("출처 줄" in p for p in r["problems"]) for r in rows),
        "truncated": sum(any("잘림" in p for p in r["problems"]) for r in rows),
        "no_missing_notice": sum(any("자료에 없다" in p for p in r["problems"]) for r in rows),
    }
    return {
        "n": len(rows),
        "pass": round(passed / len(rows), 3) if rows else None,
        "groups": {g: f"{sum(v)}/{len(v)}" for g, v in groups.items()},
        "flags": flag,
        "failed": {r["id"]: r["problems"] for r in rows if not r["pass"]},
    }


# ---- live: 실 스택 공개 API ----------------------------------------------------------------------


async def live_answers(cases: list[dict[str, Any]], keyword: str) -> list[dict[str, Any]]:
    """15분 데모 채용 건에 세션을 만들어 키워드를 확정하고 질문마다 답을 받은 뒤 취소한다."""
    import sys
    import uuid

    import httpx
    from e2e.client import env

    sys.path[:0] = [str(HERE / "virtual_candidate")]
    from drive import POSITION_NAME, seed_admin

    values = env()
    base = values.get("VC_API_URL", "http://localhost:8000")
    email, password = seed_admin(values)
    answers: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=base, timeout=120) as http:
        login = await http.post("/admin/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        admin = {"Authorization": f"Bearer {login.json()['token']}"}
        positions = (await http.get("/admin/positions", headers=admin)).json()
        name = values.get("VC_POSITION", POSITION_NAME)
        position = next(p for p in positions if p["name"] == name)
        invite = await http.post(
            f"/admin/positions/{position['id']}/invites",
            headers=admin,
            json={
                "alias": f"평가셋-어시스턴트-{uuid.uuid4().hex[:6]}",
                "email": "eval@example.test",
            },
        )
        invite.raise_for_status()
        token = invite.json()["invite_url"].rstrip("/").split("/")[-1]
        consent = (await http.post(f"/s/{token}/consent", json={"display_name": "평가셋"})).json()
        sid = consent["session_id"]
        auth = {"Authorization": f"Bearer {consent['session_token']}"}
        try:
            (await http.post(f"/s/{token}/start", headers=auth)).raise_for_status()
            confirm = await http.post(
                f"/sessions/{sid}/slots/keyword/confirm",
                headers=auth,
                json={"value": keyword, "option_key": keyword},
            )
            confirm.raise_for_status()
            for case in cases:
                reply = await http.post(
                    f"/sessions/{sid}/assistant", headers=auth, json={"prompt": case["text"]}
                )
                body = reply.json() if reply.status_code == 200 else {}
                answers.append(
                    {
                        "content": body.get("content", f"HTTP {reply.status_code}"),
                        "cited_material_ids": body.get("cited_material_ids", []),
                        "tool_calls": body.get("tool_calls", []),
                    }
                )
        finally:
            await http.post(f"/admin/sessions/{sid}/cancel", headers=admin)
    return answers
