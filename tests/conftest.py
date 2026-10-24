"""저장소 루트 tests/(연결·e2e) 공용 픽스처.

서비스 단위 테스트는 각 services/<이름>/tests/에 있다.
"""

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = REPO_ROOT / "tests" / "fixtures" / "contracts"


def load_contract(name: str) -> Any:
    """tests/fixtures/contracts/<name>.json (docs/contracts/api.md 예시)."""
    return json.loads((CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def contract():
    return load_contract


@pytest.fixture(scope="session")
def scenarios_dir() -> Path:
    return REPO_ROOT / "scenarios"


def pytest_collection_modifyitems(config, items):
    """`live` 테스트(실제 API·과금)는 `ALLOW_API=1`일 때만 돈다.

    기본 실행(`-m`에 live 선택 없음)은 live를 skip한다. `-m live`처럼 live를 명시해 골랐는데
    동의가 없으면 조용히 0으로 끝나지 않고 세션을 실패로 끝낸다(수집만 하는 `--collect-only`는
    호출이 없으므로 예외: docs_gen이 live 목록을 센다).
    """
    if os.environ.get("ALLOW_API") == "1":
        return
    markexpr = config.getoption("-m", default="") or ""
    selected = re.search(r"\blive\b", markexpr) and not re.search(r"\bnot\s+live\b", markexpr)
    if selected and not config.getoption("collectonly"):
        pytest.exit(
            f"-m {markexpr!r}: live 테스트는 실제 API 호출(과금)이다. ALLOW_API=1을 명시해야 돈다",
            returncode=1,
        )
    skip = pytest.mark.skip(reason="실제 API 호출(과금). ALLOW_API=1을 명시해야 돈다")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
