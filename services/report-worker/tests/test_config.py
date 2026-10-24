"""키 선택 규칙 사본이 api roles.py와 같은지(허용 목록·개인 키 경로 없음)."""

import pytest
from report_worker.config import RESTRICTED_MODELS, evaluator_model, key_for


def test_restricted_allowlist_matches_api_rule():
    assert RESTRICTED_MODELS == {
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5-nano",
    }
    for model in RESTRICTED_MODELS:
        assert key_for(model, {"OPENAI_API_KEY": "k"}) == ("k", None)
    with pytest.raises(ValueError, match="allowlist"):
        key_for("gpt-4o", {"OPENAI_API_KEY": "k", "OPENAI_API_KEY_PERSONAL": "p"})
    with pytest.raises(ValueError, match="OPENAI_API_KEY is required"):
        key_for("gpt-5-nano", {})
    assert evaluator_model({}) == "gpt-4o-mini"
