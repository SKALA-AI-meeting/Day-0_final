"""가상 지원자 드라이버의 순수 함수(스택·LLM 불필요)."""

import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent / "virtual_candidate")]
from drive import newest_position  # noqa: E402


def test_newest_position_picks_the_latest_reseed():
    """재시드로 같은 이름이 여럿이면 가장 최근 채용 건(최신 시나리오 복사본)을 쓴다."""
    positions = [
        {"id": "old", "name": "데모", "created_at": "2026-10-20T01:00:00Z"},
        {"id": "other", "name": "다른 건", "created_at": "2026-10-23T09:00:00Z"},
        {"id": "new", "name": "데모", "created_at": "2026-10-23T01:00:00Z"},
    ]
    assert newest_position(positions, "데모")["id"] == "new"
    assert newest_position(positions, "없음") is None
