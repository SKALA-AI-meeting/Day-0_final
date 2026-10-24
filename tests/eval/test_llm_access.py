"""호스트 러너 키 규칙 사본과 판정 모델 기본값 없음, ALLOW_API 게이트."""

import pytest

from eval import llm_access as la


def test_allowlist_same_as_api_rule():
    assert la.RESTRICTED_MODELS == {
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5-nano",
    }
    assert la.key_name_for("gpt-5.4-nano") == ("OPENAI_API_KEY", la.OPENAI_BASE_URL)
    assert la.key_name_for("gemini-2.5-flash")[0] == "GEMINI_API_KEY"
    with pytest.raises(ValueError, match="허용 목록"):
        la.key_name_for("gpt-4o")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        la.api_key_for("gpt-4o-mini", {"OPENAI_API_KEY_PERSONAL": "x"})


def test_judge_model_has_no_default():
    with pytest.raises(RuntimeError, match="VC_JUDGE_MODEL"):
        la.judge_model({})
    assert la.judge_model({"VC_JUDGE_MODEL": "gpt-4o-mini"}) == "gpt-4o-mini"


def test_require_allow_api_exits_without_consent():
    with pytest.raises(SystemExit, match="ALLOW_API=1"):
        la.require_allow_api("판정", {})
    la.require_allow_api("판정", {"ALLOW_API": "1"})
