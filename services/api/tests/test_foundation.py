"""G-F 교차 인터페이스 stub(시그니처 동결): AgentRunner·SessionPorts·의도 분류·역할별 모델."""

import json
import uuid

import pytest
from app.agent import intent
from app.agent.llm import ROLES, model_for
from app.agent.runner import FakeAgentRunner, Stimulus
from app.config import REPO_ROOT
from app.ports import FakeSessionPorts

CONTRACTS = REPO_ROOT / "tests" / "fixtures" / "contracts"


def _contract(name: str):
    return json.loads((CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))


async def test_fake_runner_posts_with_typing_and_generation():
    posted: list[dict] = []

    async def post_message(**kwargs):
        posted.append(kwargs)

    sid, run_id, mid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ports = FakeSessionPorts(sid, generation=3)
    runner = FakeAgentRunner(post_message, ports_for=lambda _: ports)
    stimulus = Stimulus(type="message", phase="keyword", generation=3, message_id=mid)

    result = await runner.run(sid, stimulus, run_id)

    assert result.ok and result.status == "finished"
    assert result.decision["stimulus"] == "message"
    assert stimulus.ref_id == str(mid)
    assert posted[0]["sender"] == "p1" and posted[0]["expected_generation"] == 3
    assert ports.typing_sent == [("p1", "start"), ("p1", "stop")]


async def test_intent_stub_returns_contract_fallback():
    result, used_fallback = await intent.classify(
        None, "물이 좋겠어요", phase="keyword", catalog=[], options=[], recent=[], seats={}
    )
    assert used_fallback
    assert result == _contract("intent_classification_fallback")
    assert set(intent.OUTPUT_FIELDS) == set(_contract("intent_classification"))


def test_role_models_use_new_env_names():
    assert ROLES == ("intent", "turn", "persona", "assistant", "evaluator")
    env = {"OPENAI_MODEL": "gpt-4o-mini", "MODEL_PERSONA": "gpt-5.4-mini"}
    assert model_for("persona", env) == "gpt-5.4-mini"
    assert model_for("intent", env) == "gpt-4o-mini"
    with pytest.raises(ValueError):
        model_for("colleague", env)
