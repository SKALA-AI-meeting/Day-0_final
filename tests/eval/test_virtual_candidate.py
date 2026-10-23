"""가상 지원자 드라이버의 순수 함수(스택·LLM 불필요)."""

import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent / "virtual_candidate")]
from drive import POSITION_NAME, newest_position  # noqa: E402


def test_newest_position_picks_the_latest_reseed():
    """재시드로 같은 이름이 여럿이면 가장 최근 채용 건(최신 시나리오 복사본)을 쓴다."""
    positions = [
        {"id": "old", "name": "데모", "created_at": "2026-10-20T01:00:00Z"},
        {"id": "other", "name": "다른 건", "created_at": "2026-10-23T09:00:00Z"},
        {"id": "new", "name": "데모", "created_at": "2026-10-23T01:00:00Z"},
    ]
    assert newest_position(positions, "데모")["id"] == "new"
    assert newest_position(positions, "없음") is None


def test_default_position_is_the_15_minute_seed():
    """기본 채용 건은 seed.py가 만드는 이름이고, 같은 이름의 60분 시드가 더 최근이어도 15분 건을
    고른다."""
    assert POSITION_NAME == "서비스기획 신입"
    positions = [
        {
            "id": "short",
            "name": POSITION_NAME,
            "created_at": "2026-10-23T01:00:00Z",
            "session_minutes": 15,
        },
        {
            "id": "long",
            "name": POSITION_NAME,
            "created_at": "2026-10-23T02:00:00Z",
            "session_minutes": 60,
        },
    ]
    assert newest_position(positions, POSITION_NAME)["id"] == "short"
    assert newest_position(positions, POSITION_NAME, minutes=None)["id"] == "long"
