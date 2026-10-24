"""live 게이트: 세 conftest의 사본이 같고, -m 선택·기본 실행·collect-only가 문서대로 동작한다."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFTESTS = (
    ROOT / "tests" / "conftest.py",
    ROOT / "services" / "api" / "tests" / "conftest.py",
    ROOT / "services" / "report-worker" / "tests" / "conftest.py",
)


def _hooks(path: Path) -> str:
    text = path.read_text("utf-8")
    return text[text.index("def pytest_collection_modifyitems") :]


def test_three_conftests_share_the_same_gate():
    first = _hooks(CONFTESTS[0])
    assert all(_hooks(p) == first for p in CONFTESTS[1:])


@pytest.fixture
def suite(tmp_path: Path) -> Path:
    (tmp_path / "conftest.py").write_text(
        "import os\nimport pytest\n" + _hooks(CONFTESTS[0]), "utf-8"
    )
    (tmp_path / "test_x.py").write_text(
        "import pytest\n\n@pytest.mark.live\ndef test_live(): pass\n\ndef test_mock(): pass\n",
        "utf-8",
    )
    return tmp_path


def _run(suite: Path, *args: str, allow: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "ALLOW_API"}
    if allow is not None:
        env["ALLOW_API"] = allow
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "markers=live"]
        + list(args),
        cwd=suite,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("expr", ["live", "live or not live", "not (not live)"])
def test_selecting_live_without_consent_fails(suite, expr):
    r = _run(suite, "-m", expr)
    assert r.returncode == 1 and "ALLOW_API=1" in r.stdout + r.stderr


@pytest.mark.parametrize("expr", ["not live", "not (live)"])
def test_excluding_live_runs_mock_only(suite, expr):
    r = _run(suite, "-m", expr)
    assert r.returncode == 0 and re.search(r"1 passed", r.stdout)


def test_default_run_skips_live(suite):
    r = _run(suite)
    assert r.returncode == 0 and "1 passed" in r.stdout and "1 skipped" in r.stdout


def test_collect_only_and_consent_pass(suite):
    assert _run(suite, "--collect-only", "-m", "live").returncode == 0
    assert "1 passed" in _run(suite, "-m", "live", allow="1").stdout
