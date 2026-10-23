"""그래프·어시스턴트가 읽는 미션 카탈로그 뷰(positions.scenario 복사본 + 프롬프트 본문).

정본 구조는 docs/product/scenario.md 6장. 실행은 채용 건에 고정된 복사본을 읽는다:
- `scenario["seats"]`: 자리 → 페르소나 유형·이름(고정 p1·p2·p3).
- `scenario["prompts"]`: 프롬프트 키 → 본문(guard_patterns.yaml·fixed_phrases.yaml 포함).
  키는 역할 폴더를 뗀 이름(`turn_manager.md`, `evaluator/common.md`)이고 규칙은
  scenarios/version.py `prompt_files`.
- `scenario["catalog"]`: `{mission, personas, cards, keywords}` = 미션 폴더 YAML 4개를 읽은 dict.
복사본에 `catalog`(4개)나 `prompts`가 없으면 현재 미션 파일로 채우지 않고 `ScenarioCopyMissing`을
올린다(세션 시작은 409 `position_outdated`로 먼저 막는다).
"""

import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from app.services.scenario import prompt_texts

PHASES = ("keyword", "problem", "solution", "proposal")
DEBATE_PHASES = ("keyword", "problem", "solution")
SEATS = ("p1", "p2", "p3")
PERSONA_TYPES = ("stubborn", "quiet", "critic", "agreeable", "divergent")
_FILES = {
    "mission": "mission.yaml",
    "personas": "personas.yaml",
    "cards": "cards.yaml",
    "keywords": "keywords.yaml",
}


def _has_batchim(word: str) -> tuple[bool, bool]:
    """(받침 있음, 받침이 ㄹ). 한글이 아니면 (False, False)."""
    for char in reversed(word.strip()):
        code = ord(char) - 0xAC00
        if 0 <= code < 11172:
            tail = code % 28
            return tail != 0, tail == 8
        if char.isalnum():
            return False, False
    return False, False


_JOSA = {
    "이": ("이", "가"),
    "가": ("이", "가"),
    "을": ("을", "를"),
    "를": ("을", "를"),
    "은": ("은", "는"),
    "는": ("은", "는"),
    "과": ("과", "와"),
    "와": ("과", "와"),
}


def attach(word: str, josa: str) -> str:
    """word + 알맞은 조사. 로/으로는 ㄹ 받침이면 '로'."""
    batchim, rieul = _has_batchim(word)
    if josa in ("로", "으로"):
        return word + ("으로" if batchim and not rieul else "로")
    pair = _JOSA.get(josa)
    if pair is None:
        return word + josa
    return word + (pair[0] if batchim else pair[1])


_VAR = re.compile(r"\{(\w+)\}(으로|로|이|가|을|를|은|는|과|와)?")


def render(template: str, **values: str) -> str:
    """고정 문장 치환. `{option}로`처럼 변수 바로 뒤 조사는 값의 끝 글자로 고른다."""

    def sub(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        if value is None:
            return match.group(0)
        josa = match.group(2)
        return attach(value, josa) if josa else value

    return _VAR.sub(sub, template)


class ScenarioCopyMissing(Exception):
    """채용 건 시나리오 복사본에 실행에 필요한 카탈로그·프롬프트가 없다."""


def copy_missing(scenario: dict[str, Any]) -> list[str]:
    """복사본에서 빠진 부분(`catalog.<키>`·`prompts`). 비면 실행할 수 있다."""
    raw = scenario.get("catalog") or {}
    missing = [f"catalog.{k}" for k in _FILES if not raw.get(k)]
    return missing + ([] if scenario.get("prompts") else ["prompts"])


@dataclass
class Catalog:
    mission: dict[str, Any]
    personas: dict[str, Any]
    cards: dict[str, Any]
    keywords: dict[str, Any]
    prompts: dict[str, str]
    seats: list[dict[str, Any]] = field(default_factory=list)

    # ---- 만들기 -------------------------------------------------------------------------
    @classmethod
    def from_dir(cls, mission_dir: Path) -> "Catalog":
        parts = {k: _read_yaml(mission_dir / f) for k, f in _FILES.items()}
        prompts = prompt_texts(mission_dir.parents[1], mission_dir.name)
        catalog = cls(prompts=prompts, **parts)
        catalog.seats = catalog._default_seats()
        return catalog

    @classmethod
    def from_scenario(cls, scenario: dict[str, Any]) -> "Catalog":
        if missing := copy_missing(scenario):
            raise ScenarioCopyMissing(f"position scenario copy lacks {', '.join(missing)}")
        raw = scenario["catalog"]
        catalog = cls(prompts=dict(scenario["prompts"]), **{k: raw[k] for k in _FILES})
        catalog.seats = list(scenario.get("seats") or catalog._default_seats())
        return catalog

    def _default_seats(self) -> list[dict[str, Any]]:
        return [
            {"seat": p["seat"], "persona_type": p["id"], "name": p["name"]}
            for p in self.personas.get("session", [])
        ]

    # ---- 자리·페르소나 ---------------------------------------------------------------------
    def type_of(self, seat: str) -> str:
        return next(s["persona_type"] for s in self.seats if s["seat"] == seat)

    def seat_of(self, persona_type: str) -> str | None:
        return next((s["seat"] for s in self.seats if s["persona_type"] == persona_type), None)

    def name_of(self, seat: str) -> str:
        return next((s["name"] for s in self.seats if s["seat"] == seat), seat)

    def persona(self, seat: str) -> dict[str, Any]:
        ptype = self.type_of(seat)
        return next(p for p in self.personas["session"] if p["id"] == ptype)

    def persona_prompt(self, seat: str) -> str:
        return self.prompts.get(f"persona_{self.type_of(seat)}.md", "")

    def safe_phrase(self, seat: str) -> str:
        return self.prompts.get(f"safe_{self.type_of(seat)}.md", "").strip() or "네, 들어 볼게요."

    def seats_by_priority(self, types: list[str]) -> list[str]:
        """유형 우선순위 목록을 세션 자리 순서로(없는 유형은 건너뜀)."""
        return [seat for t in types if (seat := self.seat_of(t))]

    # ---- 키워드·안 ------------------------------------------------------------------------
    def keyword_list(self) -> list[dict[str, Any]]:
        return list(self.keywords.get("keywords", []))

    def keyword_key_for(self, value: str | None) -> str | None:
        """확정 키워드 값(라벨 '물' 또는 키 'water')을 키로."""
        if not value:
            return None
        for kw in self.keyword_list():
            if value.strip() in (kw["key"], kw["label"]):
                return kw["key"]
        return None

    def catalog_options(self, phase: str, keyword_key: str | None) -> list[dict[str, str]]:
        """그 단계의 시나리오 안 전체 [{scenario_option_key, label}]."""
        if phase == "keyword":
            return [
                {"scenario_option_key": k["key"], "label": k["label"]} for k in self.keyword_list()
            ]
        kw = next((k for k in self.keyword_list() if k["key"] == keyword_key), None)
        if kw is None or phase not in ("problem", "solution"):
            return []
        items = kw["problems"] if phase == "problem" else kw["solutions"]
        return [{"scenario_option_key": i["key"], "label": i["label"]} for i in items]

    def label_of(self, key: str) -> str | None:
        for kw in self.keyword_list():
            if kw["key"] == key:
                return kw["label"]
            for item in kw["problems"] + kw["solutions"]:
                if item["key"] == key:
                    return item["label"]
        return None

    # ---- 카드 -----------------------------------------------------------------------------
    def card(self, card_id: str) -> dict[str, Any] | None:
        return next((c for c in self.cards.get("cards", []) if c["card_id"] == card_id), None)

    def cards_in(self, phase: str) -> list[dict[str, Any]]:
        return [c for c in self.cards.get("cards", []) if c["stage_id"] == phase]

    # ---- 프롬프트·고정 문장 ------------------------------------------------------------------
    @cached_property
    def fixed(self) -> dict[str, Any]:
        return yaml.safe_load(self.prompts.get("fixed_phrases.yaml", "")) or {}

    @cached_property
    def stance_fallback(self) -> dict[str, Any]:
        return yaml.safe_load(self.prompts.get("stance_fallback.yaml", "")) or {}

    @cached_property
    def guard_spec(self) -> dict[str, Any]:
        return yaml.safe_load(self.prompts.get("guard_patterns.yaml", "")) or {}

    @property
    def notices(self) -> dict[str, str]:
        return dict(self.fixed.get("notices") or {})


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
