"""consensus.judge 단위(가짜 시각): 현재 지지는 참여자당 하나, 3/4 즉시 합의(지원자의 최신 발언이
그 안을 지지로 세는 발언일 때), 보류 철회, 시간 초과 선택 4단계와 적용 규칙."""

from datetime import UTC, datetime, timedelta

from app.services.consensus import judge

T0 = datetime(2026, 10, 21, tzinfo=UTC)


def at(sec: int) -> datetime:
    return T0 + timedelta(seconds=sec)


def option(id_: str, proposer: str, sec: int) -> dict:
    return {"id": id_, "proposer": proposer, "created_at": at(sec)}


def stance(who: str, id_: str, kind: str, sec: int) -> dict:
    return {"participant": who, "option_id": id_, "stance": kind, "created_at": at(sec)}


def test_three_of_four_with_candidate_is_consensus_immediately():
    options = [option("A", "p1", 0)]
    stances = [stance("p3", "A", "support", 1)]
    assert judge(options, stances)["consensus_option_id"] is None
    stances.append(stance("candidate", "A", "support", 2))
    assert judge(options, stances, confirmed_option_id="A")["consensus_option_id"] == "A"


def test_consensus_needs_latest_candidate_message_to_confirm():
    """지지 3/4여도 지원자의 최신 발언이 그 안을 지지로 세는 발언이 아니면 합의가 아니다(B2)."""
    options = [option("A", "p1", 0)]
    stances = [stance("p3", "A", "support", 1), stance("candidate", "A", "support", 2)]
    result = judge(options, stances)  # 최신 발언이 질문 등
    assert result["supporters"]["A"] == ["candidate", "p1", "p3"]
    assert result["consensus_option_id"] is None
    assert judge(options, stances, confirmed_option_id="B")["consensus_option_id"] is None


def test_withhold_withdraws_support_without_opposing():
    options = [option("A", "p1", 0), option("B", "p3", 1)]
    stances = [
        stance("p3", "A", "support", 2),
        stance("candidate", "A", "support", 3),
        stance("candidate", "B", "withhold", 4),  # 다른 안 보류는 지지를 바꾸지 않음
    ]
    result = judge(options, stances, confirmed_option_id="A")
    assert result["current_support"]["candidate"] == "A" and result["consensus_option_id"] == "A"
    stances.append(stance("candidate", "A", "withhold", 5))
    result = judge(options, stances, confirmed_option_id="A")
    assert result["current_support"]["candidate"] is None
    assert result["consensus_option_id"] is None
    assert result["opposers"]["A"] == []  # 보류는 반대가 아니다


def test_ai_only_majority_is_not_consensus():
    options = [option("A", "p1", 0)]
    stances = [stance("p2", "A", "support", 1), stance("p3", "A", "support", 2)]
    assert judge(options, stances)["consensus_option_id"] is None


def test_support_for_other_option_replaces_and_later_oppose_withdraws():
    options = [option("A", "p1", 0), option("B", "p3", 1)]
    stances = [stance("candidate", "A", "support", 2), stance("candidate", "B", "support", 3)]
    result = judge(options, stances)
    assert result["current_support"]["candidate"] == "B"
    assert result["supporters"]["A"] == ["p1"]
    stances.append(stance("candidate", "A", "oppose", 4))  # 다른 안 oppose는 지지를 바꾸지 않음
    assert judge(options, stances)["current_support"]["candidate"] == "B"
    stances.append(stance("candidate", "B", "oppose", 5))
    assert judge(options, stances)["current_support"]["candidate"] is None


def _timeout(options, stances, default=None):
    result = judge(options, stances, default_option_id=default)
    return result["timeout_option_id"], result["timeout_rule"]


def test_timeout_candidate_support_wins_over_majority():
    """지원자가 지지하는 안이 있으면 지지자가 더 많은 안보다 먼저다(B3)."""
    a, b = option("A", "p1", 0), option("B", "p3", 1)
    stances = [stance("p2", "A", "support", 2), stance("candidate", "B", "support", 3)]
    assert _timeout([a, b], stances) == ("B", "candidate_support")


def test_timeout_withheld_candidate_falls_to_most_support():
    """지원자가 보류로 지지를 철회하면 지원자 규칙을 건너뛰고 최다 지지 안이다."""
    a, b = option("A", "p1", 0), option("B", "p3", 1)
    stances = [
        stance("p2", "A", "support", 2),
        stance("candidate", "B", "support", 3),
        stance("candidate", "B", "withhold", 4),
    ]
    assert _timeout([a, b], stances) == ("A", "most_support")


def test_timeout_tie_or_no_support_takes_earliest():
    """최다가 동률이면 그 안들 중 먼저 제안된 안, 지지자가 없으면 전체에서 먼저 제안된 안."""
    a, b, c = option("A", "p1", 0), option("B", "p3", 1), option("C", "candidate", 2)
    assert _timeout([a, b, c], []) == ("A", "earliest")  # A 1(p1)·B 1(p3)·C 0
    stances = [stance("p1", "C", "support", 3), stance("p3", "C", "support", 4)]
    assert _timeout([a, b, c], stances) == ("C", "most_support")
    stances.append(stance("p2", "B", "support", 5))  # B 1(p2)·C 2 → C 단독 최다
    assert _timeout([a, b, c], stances) == ("C", "most_support")
    tie = [stance("p1", "C", "support", 3), stance("p2", "B", "support", 4)]  # A 0·B 2·C 1
    assert _timeout([a, b, c], tie) == ("B", "most_support")
    tie.append(stance("p2", "C", "support", 5))  # A 0·B 1(p3)·C 2 → C
    assert _timeout([a, b, c], tie) == ("C", "most_support")
    tie.append(stance("p1", "A", "support", 6))  # A 1·B 1·C 1 → 먼저 제안된 A
    assert _timeout([a, b, c], tie) == ("A", "earliest")
    nobody = [option("X", "candidate", 0), option("Y", "candidate", 1)]
    assert _timeout(nobody, []) == ("X", "earliest")
    same_time = [option("Y", "candidate", 0), option("X", "candidate", 0)]
    assert _timeout(same_time, []) == ("Y", "earliest")  # 시각이 같으면 먼저 저장된 행


def test_timeout_without_options_uses_default():
    assert _timeout([], [], default="LEAD") == ("LEAD", "default")
    assert _timeout([], []) == (None, "default")
