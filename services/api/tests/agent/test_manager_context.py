"""진행 매니저가 정하는 답할 메시지(reply_to)·응답자 배정·상황 정보(C1)."""

from app.agent import intent as intent_mod
from app.agent.debate import DebateGraphRunner
from app.agent.persona import REACTION_LABEL
from conftest import intent_of, say

CARD = "persuasion_keyword"


def _situation(text: str) -> str:
    return text.split("[상황]\n", 1)[1].split("\n\n", 1)[0]


async def test_addressed_message_is_answered_with_reply_to(harness):
    h = harness("keyword", persona=[say("하람이에요. 저는 아직 고민 중이에요.")])
    mid = h.candidate("하람님은 어떻게 생각하세요?", intent_of("question", to=("p2",)))
    run = await h.run("message", message_id=mid)
    assert run.decision["speakers"] == [
        {"seat": "p2", "reply_to": mid, "card_ids": [], "invite": False}
    ]
    post = h.posted("p2")[0]
    assert post["meta"]["reply_to"] == mid and post["meta"]["named"] is True


async def test_named_seat_overflow_is_answered_next_run_after_restart(harness):
    """화자 상한을 넘은 지명 자리는 다음 실행이 기록에서 다시 찾아 답한다(메모리에 기대지 않음)."""
    h = harness("keyword")
    first = h.candidate(
        "한결님, 하람님, 지안님 모두 어떠세요?", intent_of("question", to=("p1", "p2", "p3"))
    )
    run = await h.run("message", message_id=first)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p1", "p2"]
    assert run.decision["queued"] == [{"seat": "p3", "message_id": first}]
    # 재시작: 새 실행기, 의도 분류 메모리 캐시도 비운다(저장된 분류 candidate_intents만 남음)
    intent_mod._cache.clear()
    h.runner = DebateGraphRunner(
        llms=h.runner.llms,
        tools=h.runner.tools,
        scenario=h.runner.scenario,
        ports_for=lambda _sid: h.ports,
        sleep=h.runner.sleep,
    )
    h.server.invites.add("keyword")  # 침묵 초대는 이미 했다
    run = await h.run("idle")
    assert [(s["seat"], s["reply_to"]) for s in run.decision["speakers"]] == [("p3", first)]
    assert h.posted("p3")[0]["meta"]["reply_to"] == first
    # 답했으므로 그다음 실행에서는 다시 답하지 않는다
    run = await h.run("idle")
    assert run.decision["speakers"] == []


async def test_overflow_and_new_message_each_get_their_own_reply_to(harness):
    """앞 메시지의 남은 지명 자리와 새 메시지 응답자가 한 실행에서 각자 다른 메시지에 답한다."""
    h = harness("keyword", turn=['{"speakers": [{"seat": "p1"}]}'])
    first = h.candidate(
        "한결님, 하람님, 지안님 모두 어떠세요?", intent_of("question", to=("p1", "p2", "p3"))
    )
    await h.run("message", message_id=first)
    second = h.candidate("저는 물이 좋아요.", intent_of("propose", key="water"))
    run = await h.run("message", message_id=second)
    assert [(s["seat"], s["reply_to"]) for s in run.decision["speakers"]] == [
        ("p3", first),
        ("p1", second),
    ]
    assert [m["meta"]["reply_to"] for m in h.posted()[-2:]] == [first, second]


async def test_card_speakers_leave_named_seat_for_the_next_run(harness):
    """카드 화자가 자리를 채워도 지명은 버려지지 않고 다음 실행에서 답한다."""
    h = harness("keyword")
    h.server.cards[CARD] = {"card_id": CARD, "locked_option_id": None, "delivered": []}
    h.server.cards["x"] = {"card_id": "x", "locked_option_id": None, "delivered": []}
    mid = h.candidate("한결님, 하람님 생각은요?", intent_of("question", to=("p1", "p2")))
    run = await h.run("message", message_id=mid)
    seats = [s["seat"] for s in run.decision["speakers"]]
    assert seats[0] == "p3" and run.decision["speakers"][0]["card_ids"] == [CARD]
    # 카드 화자도 이번 지원자 메시지에 답한다
    assert all(s["reply_to"] == mid for s in run.decision["speakers"])
    left = {q["seat"] for q in run.decision["queued"]}
    assert left == {"p1", "p2"} - set(seats)
    run = await h.run("idle")
    assert {s["seat"] for s in run.decision["speakers"]} >= left


async def test_procedural_message_gets_at_least_one_responder(harness):
    """부른 자리 없는 진행 발언에도 한 명이 답한다(턴 LLM이 0명을 골라도)."""
    h = harness("keyword", turn=['{"speakers": [], "reason": "혼잣말"}'])
    h.server.messages.append(
        {
            "id": "ai-1",
            "sender": "p1",
            "content": "불이 쓰임이 넓어요.",
            "phase": "keyword",
            "is_invite": False,
            "t_min": 1,
        }
    )
    mid = h.candidate("네 다음으로 넘어가죠.", intent_of("other", confidence="low"))
    run = await h.run("message", message_id=mid)
    # 직전에 말한 AI 자리(지원자가 이어 받은 상대)가 답한다
    assert [(s["seat"], s["reply_to"]) for s in run.decision["speakers"]] == [("p1", mid)]
    assert run.decision["llm_used"] is True
    assert h.posted("p1")[-1]["meta"]["reply_to"] == mid


async def test_proposal_phase_message_is_answered(harness):
    h = harness("proposal")
    h.server.confirm_keyword("water")
    mid = h.candidate("이제 기획안 쓸게요.", intent_of("other", confidence="low"))
    run = await h.run("message", message_id=mid)
    assert len(run.decision["speakers"]) == 1
    assert run.decision["speakers"][0]["reply_to"] == mid


async def test_situation_block_is_facts_only(harness):
    h = harness(
        "keyword", persona=[say("불이 맞아요.", {"option_id": "fire", "stance": "support"})]
    )
    await h.run("phase_started")  # 강한결·서지안 첫 발언
    mid = h.candidate("한결님, 불은 범위가 넓지 않나요?", intent_of("question", to=("p1",)))
    h.snapshots.clear()
    await h.run("message", message_id=mid)
    text = h.snapshots[0]["text"]
    assert "[이번 목적]" not in text
    block = _situation(text)
    assert block.splitlines() == [
        '- 답할 메시지: 김지원(지원자): "한결님, 불은 범위가 넓지 않나요?"',
        "- 이 메시지에서 지원자가 나를 이름으로 불렀는지: 예",
        "- 이번 발언이 내 카드 차례인지: 아니요",
        "- 내 지난 발언에 답한 동료·나를 부른 지원자 메시지: 서지안, 김지원(지원자)",
    ]


async def test_situation_without_a_message_and_on_card_turn(harness):
    h = harness("keyword")
    h.candidate("물이 좋겠어요.", intent_of("propose", key="water"))
    await h.run("card_due", card_id=CARD)
    block = _situation(h.snapshots[0]["text"])
    assert "- 답할 메시지: 없음" in block
    assert "- 이번 발언이 내 카드 차례인지: 예" in block
    assert f"- {REACTION_LABEL}: 이 단계에서 아직 말하지 않음" in block


async def test_silence_invite_is_a_situation_fact(harness):
    h = harness("solution")
    h.server.confirm_keyword("water")
    run = await h.run("idle")
    assert run.decision["speakers"][0]["invite"] is True
    assert "- 침묵 초대 차례: 예" in _situation(h.snapshots[0]["text"])


def test_reactions_count_replies_and_name_calls_only(catalog):
    from app.agent.manager import rules

    transcript = [
        {"id": "a", "sender": "p1", "content": "불이요."},
        {"id": "b", "sender": "candidate", "content": "음 그렇군요."},
        {"id": "c", "sender": "p3", "content": "바람이죠.", "reply_to": "b"},
    ]
    assert rules.reactions("p1", transcript, {}, catalog) == []
    transcript.append(
        {"id": "d", "sender": "p3", "content": "한결님 말도 일리가 있어요.", "reply_to": "a"}
    )
    transcript.append({"id": "e", "sender": "candidate", "content": "한결님 근거가 궁금해요."})
    assert rules.reactions("p1", transcript, {}, catalog) == ["p3", "candidate"]
    assert rules.reactions("p2", transcript, {}, catalog) is None


async def test_repeatedly_named_seats_do_not_starve(harness):
    """세 자리를 연달아 세 번 불러도(화자 상한 2) 오래 기다린 자리가 먼저 답해 모두 답한다."""
    h = harness("keyword")
    h.server.invites.add("keyword")
    to = ("p1", "p2", "p3")
    mids, runs = [], []
    for _ in range(3):
        mid = h.candidate("한결님, 하람님, 지안님 모두 어떠세요?", intent_of("question", to=to))
        mids.append(mid)
        runs.append(await h.run("message", message_id=mid))
    runs.append(await h.run("idle"))
    replies = [[(s["seat"], s["reply_to"]) for s in r.decision["speakers"]] for r in runs]
    assert replies == [
        [("p1", mids[0]), ("p2", mids[0])],
        [("p3", mids[1]), ("p1", mids[1])],  # 첫 메시지부터 기다린 서지안이 먼저
        [("p2", mids[2]), ("p1", mids[2])],
        [("p3", mids[2])],
    ]
    assert h.server.open_mentions() == []


async def test_mention_pushed_out_of_the_transcript_window_is_still_answered(harness):
    h = harness("keyword")
    h.server.invites.add("keyword")
    mid = h.candidate("지안님 어떻게 보세요?", intent_of("question", to=("p3",)))
    for i in range(35):  # 최근 대화 30개 밖으로 민다
        h.candidate(f"음 {i}", intent_of("other", confidence="low"))
    run = await h.run("idle")
    assert [(s["seat"], s["reply_to"]) for s in run.decision["speakers"]] == [("p3", mid)]
    # 창 밖 메시지여도 [상황]에 그 메시지의 보낸 사람·본문이 들어간다
    block = _situation(h.snapshots[-1]["text"])
    assert '- 답할 메시지: 김지원(지원자): "지안님 어떻게 보세요?"' in block
    assert "- 이 메시지에서 지원자가 나를 이름으로 불렀는지: 예" in block


async def test_fixed_card_line_does_not_answer_a_mention(harness):
    """고정 문장 카드 발언(머뭇거림 한 줄)은 답한 메시지를 남기지 않는다. 지명은 다음 실행이
    답한다."""
    h = harness("solution")
    h.server.confirm_keyword("water")
    h.server.invites.add("solution")
    mid = h.candidate("하람님 생각은요?", intent_of("question", to=("p2",)))
    run = await h.run("card_due", card_id="inclusion_silence")
    assert run.decision["speakers"][0] == {
        "seat": "p2",
        "reply_to": None,
        "card_ids": ["inclusion_silence"],
        "invite": False,
    }
    assert run.decision["queued"] == [{"seat": "p2", "message_id": mid}]
    assert h.posted("p2")[0]["meta"]["reply_to"] is None
    run = await h.run("idle")
    assert [(s["seat"], s["reply_to"]) for s in run.decision["speakers"]] == [("p2", mid)]
    assert h.posted("p2")[-1]["meta"]["reply_to"] == mid


def test_open_mentions_age_by_earliest_and_close_on_later_reply(catalog):
    from app.agent.manager import rules

    def cand(i):
        return {"id": f"c{i}", "sender": "candidate", "content": "지안님?"}

    msgs = [cand(1), cand(2)]
    assert rules.open_mentions(msgs, {}, catalog) == [
        {
            "seat": "p3",
            "message_id": "c2",
            "since": "c1",
            "sender": "candidate",
            "content": "지안님?",
        }
    ]
    msgs.append({"id": "a", "sender": "p3", "content": "네", "reply_to": "c2"})
    assert rules.open_mentions(msgs, {}, catalog) == []  # 뒤 지명에 답하면 앞 지명도 닫힌다
