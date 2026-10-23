"""러너가 시나리오 카탈로그(scenarios/missions/debate)에서 읽는 값.

카드 시각·단계, 자리-유형, 안 라벨, 고정 문장.

코드는 데이터를 해석만 한다. 카드 예정 시각은 단계 시작 + offset_min × (세션 분 / 60), 최소 20초.
"""

import json
from functools import cache
from pathlib import Path
from typing import Any

import yaml

CONTRACTS = Path(__file__).resolve().parents[1] / "fixtures" / "contracts"
MISSION = Path(__file__).resolve().parents[2] / "scenarios" / "missions" / "debate"
PHASES = ("keyword", "problem", "solution", "proposal")
BUDGET_MIN = {"keyword": 10, "problem": 18, "solution": 18, "proposal": 14}  # 60분 기준
MIN_OFFSET_MIN = 20 / 60


def _load(name: str) -> Any:
    return yaml.safe_load((MISSION / name).read_text("utf-8"))


@cache
def cards() -> dict[str, dict[str, Any]]:
    return {c["card_id"]: c for c in _load("cards.yaml")["cards"]}


@cache
def seats() -> dict[str, str]:
    """자리 id -> 페르소나 유형 id (p1 stubborn, p2 quiet, p3 critic)."""
    return {p["seat"]: p["id"] for p in _load("personas.yaml")["session"]}


def seat_of(persona_type: str) -> str:
    return next(seat for seat, kind in seats().items() if kind == persona_type)


@cache
def keywords() -> dict[str, dict[str, Any]]:
    return {k["key"]: k for k in _load("keywords.yaml")["keywords"]}


def card_offset_min(card_id: str, session_minutes: int) -> float | None:
    """카드 예정 시각의 단계 시작 대비 오프셋(분). 기획안 단계 시작 카드는 None."""
    offset = cards()[card_id]["offset_min"]
    if offset is None:
        return None
    return max(offset * session_minutes / 60, MIN_OFFSET_MIN)


def phase_deadlines(session_minutes: int) -> dict[str, float]:
    """단계별 누적 고정 마감(세션 시작 기준 분)."""
    total, result = 0.0, {}
    for phase in PHASES:
        total += BUDGET_MIN[phase] * session_minutes / 60
        result[phase] = total
    return result


@cache
def fixed_phrases() -> dict[str, Any]:
    return _load("prompts/shared/fixed_phrases.yaml")


def ro(word: str) -> str:
    """조사 로/으로: 받침이 없거나 ㄹ 받침이면 로(api `scenario._ro`와 같은 규칙)."""
    code = ord(word[-1]) - 0xAC00 if word else -1
    if 0 <= code <= 11171 and code % 28 not in (0, 8):
        return "으로"
    return "로"


def timeout_notice(rule: str, option: str, candidate: str) -> str:
    """시간 초과 안내 문장(적용 규칙별, 결정 B4)."""
    template = fixed_phrases()["timeout_fill"][rule].replace("{option}로", "{option}" + ro(option))
    return template.format(option=option, candidate=candidate)


def load_contract(name: str) -> Any:
    """tests/fixtures/contracts/<name>.json (docs/contracts/api.md 예시)."""
    return json.loads((CONTRACTS / f"{name}.json").read_text("utf-8"))
