"""미션 카탈로그 로드, 채용 건 조립(positions.scenario), 자료 원문 시드, 시나리오 해석 함수.

규칙 정본은 docs/product/scenario.md 6장. MVP는 5요소·카드 6장·고정 자리 3개(p1 고집형 강한결,
p2 소극형 윤하람, p3 비판형 서지안)이고 요소 선택은 없다(R2). 조립 결과는 카탈로그 본문을 복사해
고정하므로 이후 카탈로그가 바뀌어도 진행 중 채용 건은 자기 복사본만 본다.
"""

import importlib.util
from datetime import datetime, timedelta
from fractions import Fraction
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Material
from app.voice.policy import pinned_policy

DEFAULT_MISSION = "debate"
PHASES = ("keyword", "problem", "solution", "proposal")
DEBATE_PHASES = ("keyword", "problem", "solution")
# 아바타 파일 이름에 들어가면 안 되는 유형 이름(C8: 지원자 응답에 유형 비노출)
PERSONA_TYPES = ("stubborn", "quiet", "critic", "agreeable", "divergent")
TYPE_LABELS = ("고집형", "소극형", "비판형", "동조형", "발산형")


class InvalidMission(ValueError):
    """카탈로그에 없는 mission_id 또는 조립 규칙 위반."""


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@cache
def _version_module(scenarios_dir: Path):
    spec = importlib.util.spec_from_file_location("scenarios_version", scenarios_dir / "version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def catalog_version(scenarios_dir: Path, mission_id: str = DEFAULT_MISSION) -> str:
    """scenarios/version.py와 같은 함수(rag-mcp 색인 키와 일치)."""
    return _version_module(scenarios_dir.resolve()).catalog_version(mission_id, scenarios_dir)


def mission_root(scenarios_dir: Path, mission_id: str) -> Path:
    root = scenarios_dir / "missions" / mission_id
    if not (root / "mission.yaml").is_file():
        raise InvalidMission(f"unknown mission: {mission_id}")
    return root


def _scale(minutes: float, session_minutes: int, floor: float = 0.0) -> float:
    return max(round(minutes * session_minutes / 60, 2), floor)


def _fraction_min(value: str, session_minutes: int) -> float:
    return round(float(Fraction(value) * session_minutes), 2)


def assemble(
    scenarios_dir: Path, mission_id: str = DEFAULT_MISSION, session_minutes: int = 60
) -> dict[str, Any]:
    """positions.scenario 한 벌. 시각 값은 session_minutes 기준으로 환산해 둔다."""
    root = mission_root(scenarios_dir, mission_id)
    mission = _yaml(root / "mission.yaml")
    personas = _yaml(root / "personas.yaml")
    cards = _yaml(root / "cards.yaml")
    keywords = _yaml(root / "keywords.yaml")
    rubric = _yaml(root / "rubric.yaml")
    phrases = _yaml(root / "prompts" / "fixed_phrases.yaml")
    prompts = {
        p.relative_to(root / "prompts").as_posix(): p.read_text(encoding="utf-8")
        for p in sorted((root / "prompts").rglob("*"))
        if p.is_file() and "fixtures" not in p.parts
    }

    seats = []
    for persona in sorted(personas["session"], key=lambda p: p["seat"]):
        avatar = persona["avatar"]
        lowered = avatar.lower()
        if any(t in lowered for t in PERSONA_TYPES) or any(t in avatar for t in TYPE_LABELS):
            raise InvalidMission(f"avatar file name reveals persona type: {avatar}")
        if not (root / personas.get("avatar_dir", "assets/avatars") / avatar).is_file():
            raise InvalidMission(f"avatar file missing: {avatar}")
        seats.append(
            {
                "seat": persona["seat"],
                "persona_type": persona["id"],
                "name": persona["name"],
                "avatar": avatar,
                "persona_description": persona.get("persona_description", ""),
            }
        )
    if [s["seat"] for s in seats] != ["p1", "p2", "p3"]:
        raise InvalidMission("seats must be p1, p2, p3")

    floor = float(mission.get("card_offset_floor_min", 0.34))
    stages, cumulative = [], 0.0
    for stage in mission["stages"]:
        cumulative += stage["budget_min"]
        stages.append(
            {
                "stage_id": stage["stage_id"],
                "name": stage["name"],
                "budget_min": round(stage["budget_min"] * session_minutes / 60, 2),
                "deadline_min": round(cumulative * session_minutes / 60, 2),
                "slot": stage["slot"],
            }
        )
    scenario_cards = []
    for card in cards["cards"]:
        offset = card.get("offset_min")
        scenario_cards.append(
            {
                **{k: v for k, v in card.items() if k not in ("offset_min", "offset_fraction")},
                "offset_min": None if offset is None else _scale(offset, session_minutes, floor),
            }
        )
    kw_map = {}
    for kw in keywords["keywords"]:
        kw_map[kw["key"]] = {
            "label": kw["label"],
            "problems": [{"key": p["key"], "label": p["label"]} for p in kw["problems"]],
            "solutions": [{"key": s["key"], "label": s["label"]} for s in kw["solutions"]],
            "materials": [
                {"material_id": m["material_id"], "title": m["title"]} for m in kw["materials"]
            ],
        }
    invite = mission["silence_invite"]
    return {
        "mission_id": mission_id,
        "voice_policy": pinned_policy(),
        "catalog_version": catalog_version(scenarios_dir, mission_id),
        "session_minutes": session_minutes,
        "name": mission["name"],
        "elements": [e["id"] for e in mission["elements"]],
        "element_info": mission["elements"],
        "seats": seats,
        "personas": personas["session"],
        "common_must_not": personas.get("common_must_not", []),
        "stages": stages,
        "cards": scenario_cards,
        "keywords": kw_map,
        "keyword_order": [kw["key"] for kw in keywords["keywords"]],
        "default_keyword": mission["default_keyword"],
        "lead_option": mission["lead_option"],
        "silence_inviter": invite["inviter"],
        "silence_invite": {
            "idle_min": _fraction_min(invite["idle_fraction"], session_minutes),
            "no_opinion_after_budget_ratio": invite["no_opinion_after_budget_ratio"],
            "opinion_intents": invite["opinion_intents"],
        },
        "time_warning_min": _fraction_min(
            mission["timeout"]["warning_before_fraction"], session_minutes
        ),
        "fill_priority": mission["consensus_fill"]["filler_priority"],
        "timeout_announcer_priority": mission["timeout"]["announcer_priority"],
        "timeout_stage_default": mission["timeout"]["stage_default"],
        "phrases": phrases,
        "proposal_template": [f["field"] for f in mission["proposal_template"]],
        "assistant": mission["assistant"],
        "admin_catalog": mission["admin_catalog"],
        "debrief": mission["debrief"],
        "office_clock": mission["office_clock"],
        "rubric": rubric,
        "prompts": prompts,
        # 그래프·어시스턴트 Catalog(app/agent/catalog.py)가 읽는 미션 YAML 원본(입장·카드·후보 고정)
        "catalog": {
            "mission": mission,
            "personas": personas,
            "cards": cards,
            "keywords": keywords,
        },
    }


async def seed_materials(
    db: AsyncSession, scenarios_dir: Path, mission_id: str, version: str
) -> int:
    """카탈로그 자료 원문을 catalog_version으로 materials에 넣는다(있으면 건너뜀, R5-18)."""
    exists = await db.scalar(
        select(Material.material_id).where(Material.catalog_version == version)
    )
    if exists:
        return 0
    root = mission_root(scenarios_dir, mission_id)
    rows = []
    for kw in _yaml(root / "keywords.yaml")["keywords"]:
        for m in kw["materials"]:
            rows.append(
                {
                    "catalog_version": version,
                    "material_id": m["material_id"],
                    "keyword_key": kw["key"],
                    "title": m["title"],
                    "content": (root / "materials" / m["file"]).read_text(encoding="utf-8"),
                }
            )
    await db.execute(insert(Material).values(rows).on_conflict_do_nothing())
    return len(rows)


# ---- 시나리오 해석 ------------------------------------------------------------------------------


def avatar_url(scenario: dict[str, Any], seat: dict[str, Any]) -> str:
    return f"/assets/missions/{scenario['mission_id']}/avatars/{seat['avatar']}"


def seat_names(scenario: dict[str, Any]) -> dict[str, str]:
    return {s["seat"]: s["name"] for s in scenario["seats"]}


def seat_of_type(scenario: dict[str, Any], persona_type: str) -> str:
    for seat in scenario["seats"]:
        if seat["persona_type"] == persona_type:
            return seat["seat"]
    raise KeyError(persona_type)


def stage(scenario: dict[str, Any], phase: str) -> dict[str, Any]:
    return next(s for s in scenario["stages"] if s["stage_id"] == phase)


def phase_deadline(scenario: dict[str, Any], started_at: datetime, phase: str) -> datetime:
    """누적 고정 마감: started_at + (예산 1..k의 합) × L/60."""
    return started_at + timedelta(minutes=stage(scenario, phase)["deadline_min"])


def next_phase(phase: str) -> str:
    index = PHASES.index(phase)
    return PHASES[index + 1] if index + 1 < len(PHASES) else "completed"


def keyword_options(scenario: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"option_key": key, "label": scenario["keywords"][key]["label"]}
        for key in scenario["keyword_order"]
    ]


def catalog_options(
    scenario: dict[str, Any], phase: str, keyword_key: str | None
) -> list[dict[str, str]]:
    """그 단계의 카탈로그 안 전체 [{scenario_option_key, label}] (시나리오 순서)."""
    if phase == "keyword":
        return [
            {"scenario_option_key": o["option_key"], "label": o["label"]}
            for o in keyword_options(scenario)
        ]
    if phase not in ("problem", "solution") or keyword_key is None:
        return []
    kw = scenario["keywords"][keyword_key]
    items = kw["problems"] if phase == "problem" else kw["solutions"]
    return [{"scenario_option_key": o["key"], "label": o["label"]} for o in items]


def phrase(scenario: dict[str, Any], key: str, **values: str) -> str:
    template = scenario["phrases"][key]
    if "option" in values:
        # 조사 처리: 받침 있으면 "으로", 없거나 ㄹ 받침이면 "로" (문장 골격은 "{option}로")
        option = values["option"]
        template = template.replace("{option}로", "{option}" + _ro(option))
    return template.format(**values)


def _ro(word: str) -> str:
    if not word:
        return "로"
    last = word[-1]
    code = ord(last) - 0xAC00
    if 0 <= code <= 11171:
        jong = code % 28
        return "로" if jong in (0, 8) else "으로"
    return "로"


def remaining_text(seconds: float) -> str:
    seconds = max(int(round(seconds)), 0)
    if seconds >= 60:
        return f"{round(seconds / 60)}분"
    return f"{seconds}초"


def card(scenario: dict[str, Any], card_id: str) -> dict[str, Any]:
    return next(c for c in scenario["cards"] if c["card_id"] == card_id)


def card_seats(scenario: dict[str, Any], card_def: dict[str, Any]) -> list[str]:
    """담당 자리 전원(joint) 또는 우선순위 첫 자리 후보. delivered 판정은 joint면 전원, 아니면
    1명."""
    actor = card_def.get("actor")
    if not actor:
        return []
    actors = actor if isinstance(actor, list) else [actor]
    return [seat_of_type(scenario, a) for a in actors]


def card_required_count(card_def: dict[str, Any]) -> int:
    if not card_def.get("actor"):
        return 0
    return len(card_def["actor"]) if card_def.get("actor_mode") == "joint" else 1
