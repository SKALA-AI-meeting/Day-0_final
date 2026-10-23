"""평가자 사실(결정 A1) 순수 함수: 결정 경로·지원자 안 채택, 답한 대상, 먼저 정해진 칸,
붙여넣기 의심."""

from app.services.facts import (
    PASTE_OVERLAP,
    decision_facts,
    evaluator_facts,
    message_facts,
    paste_overlap,
    phase_starts,
)


def slot(phase, option_id, filled_by, resolution, supporters, t):
    return {
        "phase": phase,
        "option_id": option_id,
        "value": option_id,
        "filled_by": filled_by,
        "resolution": resolution,
        "support": [{"option_id": option_id, "supporters": supporters}],
        "t_min": t,
    }


def msg(id_, sender, t, addressed=None, content="짧은 말", phase="keyword"):
    intent = {"addressed_to": addressed or []} if sender == "candidate" else None
    return {
        "id": id_,
        "sender": sender,
        "content": content,
        "phase": phase,
        "t_min": t,
        "intent": intent,
    }


def test_decision_path_and_candidate_adoption_exclude_timeout():
    options = [
        {"option_id": "kw-mine", "first_proposer": "candidate"},
        {"option_id": "pb-lead", "first_proposer": "p1"},
        {"option_id": "sl-p2", "first_proposer": "p2"},
    ]
    slots = [
        slot("solution", "sl-p2", "p3", "timeout", ["candidate", "p2"], 40.0),
        slot("keyword", "kw-mine", "candidate", "consensus", ["candidate", "p2"], 8.0),
        slot("problem", "pb-lead", "p3", "consensus", ["candidate", "p1", "p3"], 20.0),
    ]
    rows = decision_facts(slots, options, lead_option_ids=["pb-lead"])
    assert [r["phase"] for r in rows] == ["keyword", "problem", "solution"]
    keyword, problem, solution = rows
    assert keyword["path"] == "candidate_confirm" and keyword["candidate_backed"]
    assert keyword["candidate_option_adopted"] is False  # 지원자 확정인데 지지 2명(반대 남음)
    assert problem["path"] == "ai_consensus_fill"
    assert problem["candidate_backed"] is False  # 지원자가 지지해도 카드 기준안은 지원자 안이 아님
    # 시간 초과로 정해진 칸은 지원자 안이어도 채택이 아니다(결정 A5)
    assert solution["path"] == "timeout" and solution["candidate_backed"]
    assert solution["candidate_option_adopted"] is False
    fill = decision_facts(
        [slot("solution", "sl-p2", "p3", "consensus", ["candidate"], 40.0)], options
    )
    assert fill[0]["path"] == "ai_consensus_fill" and fill[0]["candidate_option_adopted"] is True
    assert all(r["timeout_rule"] is None for r in rows)
    ruled = decision_facts([{**slots[0], "timeout_rule": "most_support"}], options)
    assert ruled[0]["timeout_rule"] == "most_support"


def test_preempted_cards_when_candidate_confirms_before_card_time():
    options = [{"option_id": "pb-mine", "first_proposer": "candidate"}]
    schedule = [
        {"card_id": "flexibility_majority", "phase": "problem", "t_min": 5.0, "at": 300.0},
        {"card_id": "listening_pressure", "phase": "problem", "t_min": 5.0, "at": 300.0},
        {"card_id": "persuasion_solution", "phase": "solution", "t_min": 9.0, "at": 540.0},
    ]

    def problem(filled_by, resolution, at, t):
        row = slot("problem", "pb-mine", filled_by, resolution, ["candidate"], t)
        return {**row, "at": at}

    # 지원자가 예정 시각 전에 직접 확정 → 그 단계의 발동하지 않은 카드만 선결정
    early = decision_facts([problem("candidate", "consensus", 299.5, 5.0)], options, (), schedule)
    assert early[0]["preempted_cards"] == ["flexibility_majority", "listening_pressure"]
    # 반올림한 분이 같아도 초로 가른다. 예정 시각 뒤 확정·발동한 카드·AI 채움·시간 초과는 아니다
    late = decision_facts([problem("candidate", "consensus", 300.5, 5.0)], options, (), schedule)
    assert late[0]["preempted_cards"] == []
    fired = [{**c, "fired": c["card_id"] == "listening_pressure"} for c in schedule]
    partly = decision_facts([problem("candidate", "consensus", 120.0, 2.0)], options, (), fired)
    assert partly[0]["preempted_cards"] == ["flexibility_majority"]
    for filled_by, resolution in (("p3", "consensus"), ("p3", "timeout")):
        row = decision_facts([problem(filled_by, resolution, 120.0, 2.0)], options, (), schedule)
        assert row[0]["preempted_cards"] == []
    # 초가 없으면 분으로 비교한다
    no_at = decision_facts(
        [slot("problem", "pb-mine", "candidate", "consensus", [], 2.0)],
        options,
        (),
        [{k: v for k, v in c.items() if k != "at"} for c in schedule],
    )
    assert no_at[0]["preempted_cards"] == ["flexibility_majority", "listening_pressure"]


def test_message_facts_reply_target_and_decided_phases():
    messages = [
        msg("s1", "system", 0.0),
        msg("a1", "p1", 1.0),
        msg("a2", "p2", 2.0),
        msg("c1", "candidate", 3.0),  # 부른 자리 없음 → 직전 AI 동료
        msg("c2", "candidate", 9.0, ["p1"]),  # p1을 부름 → p1의 직전 메시지
        msg("c3", "candidate", 9.0, ["p3"], phase="problem"),  # 부른 자리의 메시지가 없음
    ]
    slots = [slot("keyword", "kw", "candidate", "consensus", ["candidate"], 9.0)]
    rows = {r["message_id"]: r for r in message_facts(messages, slots, [])}
    assert set(rows) == {"c1", "c2", "c3"}
    assert rows["c1"]["reply_to"] == "a2" and rows["c1"]["addressed_to"] == []
    assert rows["c2"]["reply_to"] == "a1" and rows["c2"]["addressed_to"] == ["p1"]
    assert rows["c3"]["reply_to"] is None
    # 반올림한 분이 같아도 단계로 가른다: 키워드 단계 메시지는 키워드 결정 전, 문제 상황은 후
    assert rows["c2"]["decided_before"] == []
    assert rows["c3"]["decided_before"] == ["keyword"]


def test_paste_suspect_needs_prior_answer_and_long_overlap():
    answer = (
        "도심 침수 사례 자료에 따르면 피해의 70%가 비가 시작되고 1시간 안에 발생했으며, "
        "도심 면적의 62%가 아스팔트로 덮여 빗물이 스미지 못합니다."
    )
    pasted = "도심 침수 사례 자료에 따르면 피해의 70%가 비가 시작되고 1시간 안에 발생했습니다."
    own = "저는 비가 오면 반지하가 먼저 잠기는 게 걱정이라 침수 쪽이 급하다고 생각해요."
    assert paste_overlap(pasted, answer) >= PASTE_OVERLAP
    assert paste_overlap(own, answer) < PASTE_OVERLAP
    assert paste_overlap("네 좋아요", "네 좋아요") == 0.0  # 짧은 메시지는 보지 않음
    messages = [
        msg("c1", "candidate", 5.0, content=pasted),  # 답보다 먼저 쓴 메시지
        msg("c2", "candidate", 8.0, content=pasted),
        msg("c3", "candidate", 9.0, content=own),
    ]
    facts = evaluator_facts(
        messages, [], [], [{"call_id": "ac-1", "t_min": 6.0, "response": answer}]
    )
    rows = {r["message_id"]: r["paste_suspect"] for r in facts["message_facts"]}
    assert rows["c1"] is None and rows["c3"] is None
    same_minute = evaluator_facts(  # 같은 분이어도 초가 답보다 앞서면 의심 아님
        [{**messages[1], "at": 359.0}],
        [],
        [],
        [{"call_id": "ac-1", "t_min": 6.0, "at": 360.0, "response": answer}],
    )
    assert same_minute["message_facts"][0]["paste_suspect"] is None
    assert rows["c2"]["call_id"] == "ac-1" and rows["c2"]["overlap"] >= PASTE_OVERLAP
    assert facts["decisions"] == []


def test_phase_starts_follow_transition_times_like_the_card_scheduler():
    from datetime import UTC, datetime, timedelta

    t0 = datetime(2026, 10, 21, 1, 0, tzinfo=UTC)
    confirmed = {"keyword": t0 + timedelta(minutes=2), "problem": t0 + timedelta(minutes=6)}
    # 지나간 단계는 앞 칸 확정(단계 전환) 시각, 진행 중인 단계는 phase_started_at(시간 당기기 반영)
    shifted = t0 + timedelta(minutes=5)
    starts = phase_starts(t0, confirmed, "solution", shifted)
    assert starts == {
        "keyword": t0,
        "problem": t0 + timedelta(minutes=2),
        "solution": shifted,  # 앞 칸 확정 시각(6분)이 아니라 스케줄러가 읽는 값
    }
    assert phase_starts(t0, {}, None, None) == {"keyword": t0}
