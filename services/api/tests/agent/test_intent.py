"""의도 분류(docs/contracts/internal.md "의도 분류 출력 스키마"): 픽스처 20문장 스키마, 정규화,
폴백, 실
모델
기준선(live).
"""

import json
from pathlib import Path

import pytest
from app.agent import intent
from app.agent.llm import FakeLLM, make_llm

FIXTURE = Path(__file__).parent / "fixtures" / "intent_keyword_20.jsonl"
SEATS = {"p1": "강한결", "p2": "윤하람", "p3": "서지안"}
OPTIONS = [
    {"option_id": "opt-fire", "label": "불", "scenario_option_key": "fire"},
    {"option_id": "opt-wind", "label": "바람", "scenario_option_key": "wind"},
]


def _rows() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line]


def _expected(row: dict) -> dict:
    """픽스처 기대값을 스키마 출력으로(target: opt-* = 기존 안, 키 = 카탈로그 안, new = 새 안)."""
    target = row["target"]
    return {
        "intent": row["intent"],
        "target_option_id": target if target and target.startswith("opt-") else None,
        "scenario_option_key": target
        if target and not target.startswith("opt-") and target != "new"
        else None,
        "new_option_label": "우주" if target == "new" else None,
        "confidence": "low" if row["conf"] == "low" else "high",
        "reservation": row["intent"] in ("oppose", "withhold"),
        "addressed_to": row["addressed_to"],
        "excluded": [],
    }


async def _classify(llm, text: str, catalog, options=OPTIONS, **kw):
    return await intent.classify(
        llm, text, phase="keyword", catalog=catalog, options=options, recent=[], seats=SEATS, **kw
    )


async def test_fixture_20_schema_and_confidence(catalog):
    rows = _rows()
    llm = FakeLLM([_expected(r) for r in rows])
    keyword_catalog = catalog.catalog_options("keyword", None)
    violations = 0
    for row in rows:
        out, used_fallback = await _classify(llm, row["text"], keyword_catalog)
        violations += used_fallback
        assert set(out) == set(intent.SCHEMA["properties"]) and out["confidence"] in ("high", "low")
    assert violations == 0
    assert all(c.temperature == 0.0 and c.schema == intent.SCHEMA for c in llm.calls)


async def test_catalog_option_first_mentioned_is_key(catalog):
    out = {
        "intent": "propose",
        "target_option_id": None,
        "scenario_option_key": "water",
        "new_option_label": None,
        "confidence": "high",
        "reservation": False,
        "addressed_to": [],
        "excluded": [],
    }
    result, used = await _classify(
        FakeLLM([out]), "물이요", catalog.catalog_options("keyword", None)
    )
    assert (
        not used and result["scenario_option_key"] == "water" and result["target_option_id"] is None
    )
    # 이미 행이 있는 카탈로그 안이면 option_id로(최초 제안자 보존)
    out = {**out, "scenario_option_key": "fire"}
    result, _ = await _classify(FakeLLM([out]), "불이요", catalog.catalog_options("keyword", None))
    assert result["target_option_id"] == "opt-fire" and result["scenario_option_key"] is None


async def test_message_phase_catalog_is_used(catalog):
    """단계가 바뀐 뒤 꺼낸 이전 단계 메시지는 그 단계 카탈로그로만 판정한다."""
    out = {
        "intent": "propose",
        "target_option_id": None,
        "scenario_option_key": "water_p2",
        "new_option_label": None,
        "confidence": "high",
        "reservation": False,
        "addressed_to": [],
        "excluded": [],
    }
    result, used = await _classify(
        FakeLLM([out]), "침수요", catalog.catalog_options("keyword", None)
    )
    assert used and result == intent.fallback()
    result, used = await _classify(
        FakeLLM([out]), "침수요", catalog.catalog_options("problem", "water"), options=[]
    )
    assert not used and result["scenario_option_key"] == "water_p2"


@pytest.mark.parametrize(
    "step",
    [
        RuntimeError("timeout"),
        "not json",
        {"intent": "agree"},
        {
            "intent": "agree",
            "target_option_id": "nope",
            "scenario_option_key": None,
            "new_option_label": None,
            "confidence": "high",
            "reservation": False,
            "addressed_to": [],
            "excluded": [],
        },
        {
            "intent": "withhold",
            "target_option_id": None,
            "scenario_option_key": None,
            "new_option_label": None,
            "confidence": "high",
            "reservation": "yes",
            "addressed_to": [],
            "excluded": [],
        },
        {
            "intent": "question",
            "target_option_id": None,
            "scenario_option_key": None,
            "new_option_label": None,
            "confidence": "high",
            "reservation": False,
            "addressed_to": [],
            "excluded": ["p9"],
        },
    ],
)
async def test_failure_falls_back_to_other(catalog, step):
    llm = FakeLLM([step])
    llm.retry_delays = ()  # 의도 분류는 재시도 없음
    result, used = await _classify(llm, "x", catalog.catalog_options("keyword", None))
    assert used and result == intent.fallback()


async def test_message_id_is_remembered(catalog):
    out = {
        "intent": "question",
        "target_option_id": None,
        "scenario_option_key": None,
        "new_option_label": None,
        "confidence": "high",
        "reservation": False,
        "addressed_to": ["p2"],
        "excluded": [],
    }
    await _classify(
        FakeLLM([out]), "하람님?", catalog.catalog_options("keyword", None), message_id="m-1"
    )
    assert intent.recall("m-1")["addressed_to"] == ["p2"]


async def test_withhold_and_reservation_pass_validation(catalog):
    out = {
        "intent": "withhold",
        "target_option_id": "opt-wind",
        "scenario_option_key": None,
        "new_option_label": None,
        "confidence": "high",
        "reservation": True,
        "addressed_to": [],
        "excluded": [],
    }
    result, used = await _classify(
        FakeLLM([out]), "바람은 일단 보류요", catalog.catalog_options("keyword", None)
    )
    assert not used and result["intent"] == "withhold" and result["reservation"] is True
    assert result["target_option_id"] == "opt-wind"


async def test_excluded_seat_is_removed_from_addressed(catalog):
    """지원자가 뺀 자리는 분류가 addressed_to에 함께 넣어도 대상에서 빠진다."""
    out = {
        "intent": "question",
        "target_option_id": None,
        "scenario_option_key": None,
        "new_option_label": None,
        "confidence": "high",
        "reservation": False,
        "addressed_to": ["p3", "p2"],
        "excluded": ["p3"],
    }
    result, used = await _classify(
        FakeLLM([out]), "지안님 말고 하람님은요?", catalog.catalog_options("keyword", None)
    )
    assert not used and result["addressed_to"] == ["p2"] and result["excluded"] == ["p3"]


async def test_fallback_has_no_reservation_or_exclusion():
    assert intent.fallback()["reservation"] is False and intent.fallback()["excluded"] == []


@pytest.mark.live
async def test_live_accuracy_baseline(catalog):
    """실 모델(MODEL_INTENT) 정확도 기준선 기록(agent-spike 0.85). 통과 조건이 아니다."""
    llm = make_llm("intent")
    rows = _rows()
    hits = 0
    for row in rows:
        out, _ = await _classify(
            llm,
            row["text"],
            catalog.catalog_options("keyword", None),
            instructions=catalog.prompts.get("intent.md"),
        )
        target = (
            out["target_option_id"]
            or out["scenario_option_key"]
            or ("new" if out["new_option_label"] else None)
        )
        hits += out["intent"] == row["intent"] and target == row["target"]
    print(f"intent accuracy {hits}/{len(rows)} = {hits / len(rows):.2f}")


async def test_only_one_target_field_is_kept(catalog):
    both = {
        "intent": "propose",
        "target_option_id": "opt-fire",
        "scenario_option_key": "fire",
        "new_option_label": "불 축제",
        "confidence": "high",
        "reservation": False,
        "addressed_to": [],
        "excluded": [],
    }
    result, used = await _classify(
        FakeLLM([both]), "불이요", catalog.catalog_options("keyword", None)
    )
    assert not used and result["target_option_id"] == "opt-fire"
    assert result["scenario_option_key"] is None and result["new_option_label"] is None
