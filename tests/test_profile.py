"""scripts/profile.py: 겹침 순서, 위험 조합 거부, 키 허용 목록, 비밀값 비출력, ALLOW_API 게이트."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("profile_script", ROOT / "scripts" / "profile.py")
profile = importlib.util.module_from_spec(spec)
sys.modules["profile_script"] = profile
spec.loader.exec_module(profile)

SECRET = "sk-test-secret-value-do-not-print"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """저장소 프로필 파일은 그대로 쓰고 infra/.env만 임시로 둔다."""
    (tmp_path / "infra" / "profiles").mkdir(parents=True)
    for p in (ROOT / "infra" / "profiles").glob("*.env"):
        (tmp_path / "infra" / "profiles" / p.name).write_text(p.read_text("utf-8"), "utf-8")
    (tmp_path / "infra" / ".env").write_text(
        f"OPENAI_API_KEY={SECRET}\nLLM_FAKE=0\nENABLE_TEST_API=1\nJWT_SECRET=change-me\n"
        "MODEL_PERSONA=gpt-5.4-mini\n",
        "utf-8",
    )
    return tmp_path


def test_layering_profile_over_env_over_defaults(root):
    p = profile.Profile("dev-fake", root=root, shell={})
    assert p.get("LLM_FAKE") == "1"  # 프로필이 .env(0)를 덮는다
    assert p.role_models()["persona"] == "gpt-5.4-mini"  # .env가 기본값을 덮는다
    assert p.role_models()["intent"] == "gpt-4o-mini"
    assert p.problems(starting=True) == []  # 가짜 LLM은 동의·키 없이 시작


def test_profiles_exist_and_describe_without_secrets(root):
    for name in (
        "dev-fake",
        "test-text",
        "test-voice",
        "eval-text",
        "eval-voice",
        "dev-real",
        "deploy",
    ):
        p = profile.Profile(name, root=root, shell={})
        text = p.describe()
        assert SECRET not in text and "OPENAI_API_KEY 있음" in text
        assert name in text


def test_voice_profile_modes_and_deploy_guard(root):
    expected = {
        "dev-fake": "fake",
        "test-text": "off",
        "test-voice": "fake",
        "eval-text": "off",
        "eval-voice": "fake",
        "dev-real": "openai",
        "deploy": "azure",
    }
    for name, mode in expected.items():
        p = profile.Profile(name, root=root, shell={})
        assert p.voice_mode == mode
        assert not any("VOICE_" in problem for problem in p.problems())
        assert (
            "AZURE_SPEECH_KEY" in p.describe()
            if mode == "azure"
            else "AZURE_SPEECH_KEY" not in p.describe()
        )
        assert ("OpenAI 음성" in p.describe()) == (mode == "openai")
    path = root / "infra" / "profiles" / "deploy.env"
    path.write_text(
        path.read_text("utf-8").replace("VOICE_PROVIDER=azure", "VOICE_PROVIDER=fake"), "utf-8"
    )
    assert any(
        "VOICE_PROVIDER=azure" in x
        for x in profile.Profile("deploy", root=root, shell={}).problems()
    )


def test_voice_false_spelling_requires_test_api(root):
    path = root / "infra" / "profiles" / "test-text.env"
    path.write_text(
        path.read_text("utf-8").replace("VOICE_ENABLED=0", "VOICE_ENABLED=false"), "utf-8"
    )
    assert profile.Profile("test-text", root=root, shell={}).voice_mode == "off"
    path.write_text(
        path.read_text("utf-8").replace("ENABLE_TEST_API=1", "ENABLE_TEST_API=0"), "utf-8"
    )
    assert any(
        "ENABLE_TEST_API=1" in x
        for x in profile.Profile("test-text", root=root, shell={}).problems()
    )


def test_unknown_profile_lists_choices(root):
    with pytest.raises(profile.ProfileError, match="dev-fake"):
        profile.Profile("nope", root=root, shell={})


def test_real_llm_needs_allow_api_only_when_starting(root):
    p = profile.Profile("eval-text", root=root, shell={})
    assert p.problems() == []
    assert any("ALLOW_API=1" in x for x in p.problems(starting=True))
    ok = profile.Profile("eval-text", root=root, shell={"ALLOW_API": "1"})
    assert ok.problems(starting=True) == []


def test_deploy_rejects_fake_and_test_api_and_placeholders(root):
    (root / "infra" / "profiles" / "deploy.env").write_text(
        "PROFILE_PURPOSE=deploy\nLLM_FAKE=1\nENABLE_TEST_API=1\n", "utf-8"
    )
    problems = profile.Profile("deploy", root=root, shell={"ALLOW_API": "1"}).problems(
        starting=True
    )
    assert any("LLM_FAKE=1" in x for x in problems)
    assert any("ENABLE_TEST_API=1" in x for x in problems)
    assert any("JWT_SECRET" in x for x in problems)


def test_model_outside_allowlist_or_missing_key_is_rejected(root):
    (root / "infra" / ".env").write_text("MODEL_TURN=gpt-4o\nOPENAI_API_KEY_PERSONAL=x\n", "utf-8")
    problems = profile.Profile("dev-real", root=root, shell={"ALLOW_API": "1"}).problems(
        starting=True
    )
    assert any("gpt-4o" in x and "허용 목록" in x for x in problems)
    assert any("OPENAI_API_KEY가 비어 있다" in x for x in problems)
    assert profile.key_name_for("gemini-2.5-flash") == "GEMINI_API_KEY"


def test_shell_mode_conflict_and_need(root):
    p = profile.Profile("dev-fake", root=root, shell={"LLM_FAKE": "0"})
    assert any("셸의 LLM_FAKE" in x for x in p.problems())
    assert any(
        "실제 LLM 프로필" in x
        for x in profile.Profile("dev-fake", root=root, shell={}).problems(need="real")
    )
    assert any(
        "가짜 LLM 프로필" in x
        for x in profile.Profile("eval-text", root=root, shell={}).problems(need="fake")
    )


def test_profile_file_must_not_hold_secrets(root):
    (root / "infra" / "profiles" / "dev-fake.env").write_text("OPENAI_API_KEY=abc\n", "utf-8")
    with pytest.raises(profile.ProfileError, match="비밀값"):
        profile.Profile("dev-fake", root=root, shell={})


def test_render_and_exec_env(root):
    p = profile.Profile("test-text", root=root, shell={"PATH": "/bin"})
    rendered = p.rendered()
    assert (
        "LLM_FAKE=1" in rendered and f"OPENAI_API_KEY={SECRET}" in rendered
    )  # compose용 합친 파일
    env = p.exec_env()
    assert (
        env["E2E_INTERNAL_URL"] == "http://localhost:8101"
        and env["WEB_URL"] == "http://localhost:3100"
    )
    assert env["SEED_DATABASE_URL"].endswith(":5442/dayzero") and env["PATH"] == "/bin"
    d = p.derived()
    assert (
        d["COMPOSE_PROJECT"] == "dayzero-debate" and "docker-compose.e2e.yml" in d["COMPOSE_FILES"]
    )
    assert d["LLM_MODE"] == "fake" and d["ENV_FILE"] == ".env.resolved-test-text"


def test_profile_host_addresses_win_over_stale_shell(root):
    """묵은 셸 값은 덮이고, 다른 값이 명시돼 있으면 모드 변수처럼 오류다."""
    stale = {"E2E_INTERNAL_URL": "http://localhost:8001", "SEED_DATABASE_URL": "x"}
    p = profile.Profile("test-text", root=root, shell=stale)
    env = p.exec_env()
    assert env["E2E_INTERNAL_URL"] == "http://localhost:8101"
    assert env["SEED_DATABASE_URL"].endswith(":5442/dayzero")
    problems = p.problems()
    assert any("E2E_INTERNAL_URL" in x for x in problems)
    assert any("SEED_DATABASE_URL" in x for x in problems)
    same = {"E2E_INTERNAL_URL": "http://localhost:8101"}
    assert profile.Profile("test-text", root=root, shell=same).problems() == []


def test_cli_get_never_prints_secret_values(root, capsys):
    assert profile.main(["--root", str(root), "get", "eval-text", "OPENAI_API_KEY"]) == 0
    assert capsys.readouterr().out.strip() == ""
    assert profile.main(["--root", str(root), "get", "eval-text", "LLM_MODE"]) == 0
    assert capsys.readouterr().out.strip() == "live"
    assert profile.main(["--root", str(root), "doctor", "eval-text"]) == 0
    assert SECRET not in capsys.readouterr().out
