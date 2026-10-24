"""저장소 루트 tests/(연결·e2e) 공용 픽스처.

서비스 단위 테스트는 각 services/<이름>/tests/에 있다.
"""

import json
import os
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
    """`live` 테스트(실제 API·과금)는 `ALLOW_API=1`일 때만 돈다. -m 마커 선택은 그대로다."""
    if os.environ.get("ALLOW_API") == "1":
        return
    skip = pytest.mark.skip(reason="실제 API 호출(과금). ALLOW_API=1을 명시해야 돈다")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
