"""LLM 래퍼: 역할별 모델·키·temperature, 결정적 LLM 모드(LLM_FAKE=1), AGENT_RUNNER 기본값."""

import pytest
from app.agent import intent
from app.agent.debate import DebateLLMs
from app.agent.factory import DEFAULT_AGENT_RUNNER, runner_kind
from app.agent.llm import (
    ROLE_CONFIG,
    DeterministicLLM,
    FakeLLM,
    OpenAILLM,
    Trace,
    Usage,
    make_llm,
    model_for,
)
from app.agent.llm.roles import RESTRICTED_MODELS, key_name_for
from langchain_core.messages import HumanMessage

FAKE_ENV = {"LLM_FAKE": "1"}
KEYS = {"OPENAI_API_KEY": "test-restricted"}


def test_role_models_and_temperature():
    env = {**KEYS, "MODEL_PERSONA": "gpt-4.1-mini"}
    assert model_for("persona", env) == "gpt-4.1-mini" and model_for("turn", env) == "gpt-4o-mini"
    for role, config in ROLE_CONFIG.items():
        llm = make_llm(role, env)
        assert isinstance(llm, OpenAILLM) and llm.temperature == config.temperature
        assert len(llm.retry_delays) == config.retries


def test_missing_key_is_config_error():
    with pytest.raises(ValueError, match="OPENAI_API_KEY is required"):
        make_llm("persona", {"MODEL_PERSONA": "gpt-5.4-mini"})


def test_restricted_key_allowlist():
    """허용 목록의 gpt-*만 OPENAI_API_KEY. 목록 밖 gpt-*는 키가 있어도 설정 오류(개인 키 없음)."""
    assert RESTRICTED_MODELS == {
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5-nano",
    }
    for model in RESTRICTED_MODELS:
        assert key_name_for(model) == ("OPENAI_API_KEY", None)
    assert key_name_for("gemini-2.5-flash")[0] == "GEMINI_API_KEY"
    with pytest.raises(ValueError, match="allowlist"):
        make_llm("persona", {**KEYS, "OPENAI_API_KEY_PERSONAL": "x", "MODEL_PERSONA": "gpt-4o"})


async def test_wrapper_passes_role_temperature():
    llm = FakeLLM(["a", "b"], temperature=0.3)
    await llm.ainvoke([HumanMessage("x")], usage=Usage(), trace=Trace("r", "s", "n"))
    await llm.ainvoke(
        [HumanMessage("x")], usage=Usage(), trace=Trace("r", "s", "n"), temperature=0.0
    )
    assert [c.temperature for c in llm.calls] == [0.3, 0.0]


def test_llm_fake_needs_no_key():
    for role in ROLE_CONFIG:
        llm = make_llm(role, FAKE_ENV)
        assert isinstance(llm, DeterministicLLM) and llm.model == "fake"


def test_agent_runner_default_is_session():
    assert DEFAULT_AGENT_RUNNER == "session"
    assert runner_kind(None) == runner_kind("") == "session" and runner_kind("fake") == "fake"
    with pytest.raises(ValueError):
        runner_kind("graph")


async def test_fake_intent_rules(catalog):
    llm = make_llm("intent", FAKE_ENV)
    seats = {"p1": "강한결", "p2": "윤하람", "p3": "서지안"}
    options = [{"option_id": "opt-fire", "label": "불", "scenario_option_key": "fire"}]

    async def classify(text):
        out, used = await intent.classify(
            llm,
            text,
            phase="keyword",
            catalog=catalog.catalog_options("keyword", None),
            options=options,
            recent=[],
            seats=seats,
        )
        assert not used
        return out

    assert (await classify("저는 바람이 좋겠어요"))["scenario_option_key"] == "wind"
    out = await classify("불은 반대예요")
    assert (
        out["intent"] == "oppose"
        and out["target_option_id"] == "opt-fire"
        and out["confidence"] == "high"
    )
    assert out["reservation"] is False  # 안을 직접 지목한 반대는 유보가 아니다
    out = await classify("불은 아니에요. 효과가 약해요")
    assert out["intent"] == "oppose" and out["reservation"] is True
    out = await classify("하람님 생각은요?")
    assert out["intent"] == "question" and out["addressed_to"] == ["p2"]
    assert (await classify("음 잘 모르겠네요"))["confidence"] == "low"


async def test_fake_stance_judge_follows_where_the_draft_ends(catalog):
    """결정적 입장 판정도 양보 원리를 따른다: 마지막에 선 안이 내 안이면 keep."""
    from app.agent import persona
    from app.agent.manager.rules import StancePlan

    llm = make_llm("intent", FAKE_ENV)
    ds = {
        "options": [{"option_id": "opt-fire", "label": "불", "scenario_option_key": "fire"}],
        "catalog_options": catalog.catalog_options("keyword", None),
    }
    plan = StancePlan(seat="p1", phase="keyword", key="fire", option_id="opt-fire", label="불")

    async def judge(text):
        return await persona.judge_stance(llm, text, plan, ds, [])

    assert (await judge("물도 좋지만 저는 불이 낫다고 봐요."))["verdict"] == "keep"
    moved = await judge("불도 좋지만 저는 물이 낫다고 봐요.")
    assert moved["verdict"] == "move" and moved["targets"] == {"water"}
    assert (await judge("불은 보류할게요."))["verdict"] == "withdraw"
    assert (await judge("보류는 아니고, 그래도 불이에요."))["verdict"] == "keep"
    assert (await judge("왜 그렇게 보세요?"))["verdict"] == "none"


async def _scripted_session(h) -> dict:
    """같은 단계 시드·같은 지원자 입력: 단계 시작 → 지원자 제안(메일박스 분류) → 지명 질문."""
    await h.run("phase_started")
    for text in ("저는 물이 좋다고 생각해요.", "지안님 생각은요?"):
        mid = h.server.candidate_says(text)
        ds = h.server.debate_state()
        result, _ = await intent.classify(
            h.runner.llms.intent,
            text,
            phase="keyword",
            catalog=[
                {k: c[k] for k in ("scenario_option_key", "label")} for c in ds["catalog_options"]
            ],
            options=[
                {k: o[k] for k in ("option_id", "label", "scenario_option_key")}
                for o in ds["options"]
            ],
            recent=[],
            seats={"p1": "강한결", "p2": "윤하람", "p3": "서지안"},
            message_id=mid,
        )
        h.server.apply_candidate(result)
        await h.run("message", message_id=mid)
    key = {o["option_id"]: o["scenario_option_key"] for o in h.server.options}
    return {
        "posts": [
            (m["sender"], m["content"], m["meta"]["guard"], m["meta"]["stance_check"])
            for m in h.posted()
        ],
        "options": sorted((o["scenario_option_key"], o["proposer"]) for o in h.server.options),
        "support": {p: key.get(o) for p, o in h.server.support.items()},
        "slots": dict(h.server.slots),
    }


async def test_llm_fake_mode_is_deterministic(harness):
    outcomes = []
    for _ in range(2):
        h = harness("keyword")
        h.runner.llms = DebateLLMs(
            persona=make_llm("persona", FAKE_ENV),
            turn=make_llm("turn", FAKE_ENV),
            intent=make_llm("intent", FAKE_ENV),
        )
        outcomes.append(await _scripted_session(h))
    first, second = outcomes
    assert first == second
    assert [p[0] for p in first["posts"]] == [
        "p1",
        "p3",
        "p3",
        "p3",
    ]  # 단계 시작 2, 턴 LLM 1, 지명 1
    assert all(p[2] == "pass" and p[3] == "pass" for p in first["posts"])
    assert first["support"] == {"candidate": "water", "p1": "fire", "p2": None, "p3": "wind"}
