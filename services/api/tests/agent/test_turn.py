"""진행 매니저 규칙, 합의 채우기(fill), typing·generation, 재실행 멱등."""

import uuid

from app.agent.runner import Stimulus
from conftest import intent_of, say

WIND = {"option_id": "wind", "stance": "support"}


def _stim(kind: str, phase: str, generation: int) -> Stimulus:
    return Stimulus(type=kind, phase=phase, generation=generation)


def keyword_with(h, supporters: dict[str, str]) -> dict[str, str]:
    """키워드 안 행을 만들고 지지를 놓는다. 반환 {키: option_id}.

    지원자 지지가 있으면 지원자의 최신 발언이 그 안을 지지로 세는 발언이었던 상태로 둔다.
    """
    ids = {}
    for key in dict.fromkeys(supporters.values()):
        ids[key] = h.server.add_option(key, "p1")
    for participant, key in supporters.items():
        h.server.set_support(participant, ids[key])
    if "candidate" in supporters:
        h.server.confirmed = ids[supporters["candidate"]]
    return ids


# ---- 턴 규칙 ---------------------------------------------------------------------------------
async def test_phase_started_keyword_pushers_and_problem_react(harness):
    h = harness(
        "keyword",
        persona=[
            say("불이 맞아요.", {"option_id": "fire", "stance": "support"}),
            say("바람이죠.", WIND),
        ],
    )
    run = await h.run("phase_started")
    posts = h.posted()
    # 첫 화자는 답할 메시지 없이 말을 꺼내고, 둘째 화자는 같은 실행에서 첫 화자 발언에 답한다
    assert [(s["seat"], s["reply_to"]) for s in run.decision["speakers"]] == [
        ("p1", None),
        ("p3", posts[0]["id"]),
    ]
    assert [m["seq"] for m in posts] == [1, 2]
    assert [m["meta"]["reply_to"] for m in posts] == [None, posts[0]["id"]]
    assert len(h.slept) == 1  # 둘째 화자 간격

    p = harness(
        "problem", persona=[say("자료부터 봐요.", {"option_id": "water_p1", "stance": "support"})]
    )
    p.server.confirm_keyword("water")
    run = await p.run("phase_started")
    assert [s["seat"] for s in run.decision["speakers"]] == ["p1"]
    # 카드 전에는 고집형도 입장을 밝히지 않는다(계획 밖 → 고정 문구, 입장 없음)
    assert p.server.support["p1"] is None and not p.server.options


async def test_second_speaker_sees_the_first_speakers_line(harness):
    """같은 run의 둘째 화자 컨텍스트(최근 대화)에 첫 화자가 방금 게시한 발언이 들어간다."""
    h = harness(
        "keyword",
        persona=[
            say("불이 맞아요. 쓰임이 넓어요.", {"option_id": "fire", "stance": "support"}),
            say("바람이죠.", WIND),
        ],
    )
    await h.run("phase_started")
    assert [s["seat"] for s in h.snapshots] == ["p1", "p3"]
    recent = h.snapshots[1]["text"].rsplit("[최근 대화]", 1)[1]
    assert "강한결: 불이 맞아요. 쓰임이 넓어요." in recent


async def test_proposal_phase_started_has_no_speakers(harness):
    h = harness("proposal")
    run = await h.run("phase_started")
    assert run.decision["speakers"] == [] and not h.turn.calls


async def test_named_reply_first_and_at_most_two(harness):
    h = harness("keyword")
    mid = h.candidate(
        "한결님, 하람님, 지안님 모두 어떠세요?", intent_of("question", to=("p1", "p2", "p3"))
    )
    run = await h.run("message", message_id=mid)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p1", "p2"]
    assert len(h.posted()) == 2 and not h.turn.calls


async def test_named_order_follows_the_message_not_the_classifier(harness):
    h = harness("solution")
    h.server.confirm_keyword("water")
    mid = h.candidate(
        "서지안님, 하람님 대신 의견을 좀 말해 줄래요?", intent_of("question", to=("p2", "p3"))
    )
    run = await h.run("message", message_id=mid)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p3", "p2"]

    # 분류가 호명을 놓쳐도 "이름님"으로 부른 자리가 답한다
    mid = h.candidate("윤하람님 생각도 궁금해요.", intent_of("other", confidence="low"))
    run = await h.run("message", message_id=mid)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p2"] and not h.turn.calls


async def test_excluded_seat_does_not_answer(harness):
    """지원자가 뺀 자리(분류 excluded)는 이름이 불렸어도 답하지 않는다(지안님 말고 하람님)."""
    h = harness("solution")
    h.server.confirm_keyword("water")
    mid = h.candidate(
        "지안님 말고 하람님 생각은 어때요?", intent_of("question", to=("p2",), excluded=("p3",))
    )
    run = await h.run("message", message_id=mid)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p2"]
    # 분류가 대상만 내고 뺀 자리를 놓치면 이름으로 부른 자리는 그대로 답한다(대상이 먼저)
    mid = h.candidate("지안님 말고 하람님 생각은 어때요?", intent_of("question", to=("p2",)))
    run = await h.run("message", message_id=mid)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p2", "p3"]


async def test_excluded_seat_is_not_picked_for_open_question_or_by_turn_llm(harness):
    h = harness(
        "solution",
        turn=[
            {"speakers": [{"seat": "p3", "purpose": "respond"}, {"seat": "p1", "purpose": "react"}]}
        ],
    )
    h.server.confirm_keyword("water")
    # 전체 질문 기본 화자(소극형·비판형)에서도 뺀 자리는 빠진다
    mid = h.candidate("지안님 말고 다른 분들은요?", intent_of("question", excluded=("p3",)))
    run = await h.run("message", message_id=mid)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p2"]
    # 규칙 화자가 없어 턴 LLM이 골라도 뺀 자리는 넣지 않는다
    mid = h.candidate("지안님 말고요.", intent_of("other", excluded=("p3",)))
    run = await h.run("message", message_id=mid)
    assert run.decision["llm_used"] and [s["seat"] for s in run.decision["speakers"]] == ["p1"]


async def test_named_seat_missed_by_the_classifier_still_answers(harness):
    """분류가 일부 호명을 놓쳐도 "이름님"으로 부른 자리는 모두 답한다(C1)."""
    h = harness("solution")
    h.server.confirm_keyword("water")
    mid = h.candidate("한결님, 하람님 생각은요?", intent_of("question", to=("p2",)))
    run = await h.run("message", message_id=mid)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p2", "p1"]


async def test_quiet_only_when_called_or_asked_all(harness):
    h = harness(
        "keyword",
        turn=[
            {"speakers": [{"seat": "p2", "purpose": "respond"}, {"seat": "p1", "purpose": "react"}]}
        ],
    )
    mid = h.candidate("음 그렇군요.", intent_of("other", confidence="low"))
    run = await h.run("message", message_id=mid)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p1"]
    assert run.decision["llm_used"] is True and len(h.turn.calls) == 1

    mid = h.candidate("다들 어떤 키워드가 좋으세요?", intent_of("question"))
    run = await h.run("message", message_id=mid)
    assert "p2" in [s["seat"] for s in run.decision["speakers"]]
    assert len(h.turn.calls) == 1  # 규칙이 화자를 정했으므로 턴 LLM 없음


async def test_turn_llm_only_for_message(harness):
    h = harness("keyword")
    h.server.invites.add("keyword")  # 초대가 이미 전달돼 idle 규칙도 0명
    h.server.cards["persuasion_keyword"] = {
        "card_id": "persuasion_keyword",
        "locked_option_id": None,
        "delivered": ["p3"],
    }
    for kind, card_id in (("idle", None), ("card_due", "persuasion_keyword")):
        run = await h.run(kind, card_id=card_id)
        assert run.decision["speakers"] == []
    assert not h.turn.calls


async def test_turn_llm_failure_falls_back_to_a_default_responder(harness):
    """턴 LLM이 실패하거나 0명을 골라도 지원자 메시지에는 한 명이 답한다(C1)."""
    h = harness("keyword", turn=[RuntimeError("down")] * 3)
    mid = h.candidate("음.", intent_of("other", confidence="low"))
    run = await h.run("message", message_id=mid)
    assert run.ok and [(s["seat"], s["reply_to"]) for s in run.decision["speakers"]] == [
        ("p3", mid)
    ]
    assert [m["sender"] for m in h.posted()] == ["p3"]


async def test_silence_invite_once_by_critic(harness):
    h = harness("solution", persona=[say("김지원님은 어떻게 생각하세요?")])
    h.server.confirm_keyword("water")
    run = await h.run("idle")
    assert run.decision["invite"] is True
    post = h.posted()[0]
    assert post["sender"] == "p3" and post["is_invite"] is True
    second = await h.run("idle")
    assert second.decision["speakers"] == [] and len(h.posted()) == 1


async def test_consecutive_three_blocks_turn_llm_choice(harness):
    h = harness("keyword", turn=['{"speakers": [{"seat": "p3", "purpose": "react"}]}'])
    mid = h.candidate("음.", intent_of("other", confidence="low"))
    for _ in range(3):
        h.server.messages.append(
            {
                "id": str(uuid.uuid4()),
                "sender": "p3",
                "content": "음",
                "phase": "keyword",
                "is_invite": False,
                "t_min": 1,
            }
        )
    run = await h.run("message", message_id=mid)
    # 턴 LLM이 고른 연속 3회 자리는 버리고, 기본 응답 자리도 그 자리를 건너뛴다
    assert [s["seat"] for s in run.decision["speakers"]] == ["p1"]


# ---- 합의 채우기 -----------------------------------------------------------------------------
async def test_existing_consensus_fills_without_respond(harness):
    h = harness("keyword")
    ids = keyword_with(h, {"candidate": "water", "p1": "water", "p3": "water"})
    mid = h.candidate("물로 가요.", intent_of("agree", option_id=ids["water"]))
    run = await h.run("message", message_id=mid)
    assert run.decision["speakers"] == [] and run.decision["fill"] == {
        "option_id": ids["water"],
        "seat": "p3",
    }
    names = h.tool_names()
    assert "post_message" not in names and names.count("fill_slot") == 1
    assert not h.persona.calls
    slot = h.server.slots["keyword"]
    assert slot["filled_by"] == "p3" and slot["message"]["seq"] == 5
    assert slot["message"]["content"] == "그럼 물로 정리할게요."
    assert h.server.phase == "problem"


async def test_ai_move_does_not_fill_until_candidate_confirms(harness):
    """비판형이 지원자 안으로 옮겨 3/4가 돼도 같은 run에서 채우지 않고, 지원자의 최신 발언이 그 안의
    명시적 동의일 때 이후 run에서 채운다(B2)."""
    h = harness(
        "keyword",
        persona=[say("그래요, 물로 가도 괜찮아요.", {"option_id": "water", "stance": "support"})],
    )
    ids = keyword_with(h, {"candidate": "water", "p1": "water", "p3": "wind"})
    h.server.messages.append(
        {
            "id": "x",
            "sender": "p3",
            "content": "바람이죠",
            "phase": "keyword",
            "is_invite": False,
            "t_min": 0,
        }
    )
    # 서지안이 바람을 민 뒤 지원자가 물을 두 번 명시적으로 제안 → 설득 가능 규칙으로 물로 옮긴다
    for text in ("저는 물이 좋아요.", "바람 말고 물로 가요."):
        h.candidate(text, intent_of("propose", option_id=ids["water"]))
    run = await h.run(
        "message", message_id=h.candidate("지안님?", intent_of("question", to=("p3",)))
    )
    assert run.decision["speakers"][0]["seat"] == "p3"
    assert h.server.support["p3"] == ids["water"]
    assert run.decision["fill"] is None and "fill_slot" not in h.tool_names()
    # 다음 run도 지원자의 최신 발언이 동의가 아니면 채우지 않는다
    run = await h.run(
        "message", message_id=h.candidate("하람님 생각은요?", intent_of("question", to=("p2",)))
    )
    assert run.decision["fill"] is None and "keyword" not in h.server.slots
    run = await h.run(
        "message",
        message_id=h.candidate("좋아요, 물로 정해요.", intent_of("agree", option_id=ids["water"])),
    )
    assert run.decision["fill"] == {"option_id": ids["water"], "seat": "p3"}
    assert h.server.slots["keyword"]["resolution"] == "consensus"


async def test_reserved_agreement_does_not_fill(harness):
    """유보 표현이 든 동의는 지지로 저장하지 않고 합의 채움도 하지 않는다(B1·B2)."""
    h = harness("keyword")
    ids = keyword_with(h, {"p1": "water", "p3": "water"})
    mid = h.candidate(
        "물 좋긴 한데 범위가 걱정이에요.",
        intent_of("agree", option_id=ids["water"], reservation=True),
    )
    run = await h.run("message", message_id=mid)
    assert h.server.support["candidate"] is None
    assert run.decision["fill"] is None and "fill_slot" not in h.tool_names()


async def test_withhold_withdraws_candidate_support(harness):
    h = harness("keyword")
    ids = keyword_with(h, {"candidate": "water", "p1": "water", "p3": "water"})
    mid = h.candidate("물은 일단 보류할게요.", intent_of("withhold", option_id=ids["water"]))
    run = await h.run("message", message_id=mid)
    assert h.server.support["candidate"] is None
    assert run.decision["fill"] is None and "fill_slot" not in h.tool_names()


async def test_fill_slot_conflict_saves_nothing(harness):
    h = harness("keyword")
    keyword_with(h, {"candidate": "water", "p1": "water", "p3": "water"})
    original = h.server.t_fill_slot
    h.server.t_fill_slot = lambda **_: {
        "ok": False,
        "error": {"code": "conflict", "message": "no_consensus"},
    }
    run = await h.run("phase_started")
    assert run.ok and run.decision["fill_result"] == "no_consensus"
    assert not [m for m in h.server.messages if m.get("seq") == 5]
    assert h.server.phase == "keyword"
    h.server.t_fill_slot = original


async def test_no_fill_without_consensus(harness):
    h = harness("keyword", persona=[say("저는 바람이요.", WIND)])
    keyword_with(h, {"candidate": "water", "p1": "water"})
    mid = h.candidate("지안님 생각은요?", intent_of("question", to=("p3",)))
    run = await h.run("message", message_id=mid)
    assert run.decision["fill"] is None and "fill_slot" not in h.tool_names()
    assert "keyword" not in h.server.slots


async def test_same_run_id_rerun_converges(harness):
    h = harness("keyword")
    keyword_with(h, {"candidate": "water", "p1": "water", "p3": "water"})
    run_id = uuid.uuid4()
    await h.run("phase_started", run_id=run_id)
    # 같은 run_id 재실행은 단계가 바뀌어 skipped, 공지는 1건 그대로
    rerun = await h.runner.run(
        h.session_id, _stim("phase_started", "keyword", h.ports.generation), run_id
    )
    assert rerun.status == "skipped"
    assert len([m for m in h.server.messages if m.get("seq") == 5]) == 1

    g = harness(
        "keyword",
        persona=[
            say("불이 맞아요.", {"option_id": "fire", "stance": "support"}),
            say("바람이죠.", WIND),
        ]
        * 2,
    )
    run_id = uuid.uuid4()
    await g.run("phase_started", run_id=run_id)
    await g.run("phase_started", run_id=run_id)
    assert len(g.posted()) == 2 and len(g.server.options) == 2


# ---- typing·generation -----------------------------------------------------------------------
async def test_candidate_typing_defers_and_requeues(harness):
    h = harness(
        "keyword", persona=[say("불이 맞아요.", {"option_id": "fire", "stance": "support"})]
    )
    h.ports.typing = True
    run = await h.run("phase_started")
    assert run.status == "deferred" and not h.posted()
    assert sum(h.slept) == 10.0
    assert [s.type for s in h.ports.enqueued] == ["phase_started"]
    assert h.ports.typing_sent[-1] == ("p1", "stop")


async def test_generation_change_drops_remaining(harness):
    h = harness(
        "keyword", persona=[say("불이 맞아요.", {"option_id": "fire", "stance": "support"})]
    )

    def second(_messages):
        h.ports.generation += 1
        return say("바람이죠.", WIND)

    h.persona.script.append(second)
    run = await h.run("phase_started")
    assert run.status == "finished" and run.decision["dropped_seats"] == ["p3"]
    assert [m["sender"] for m in h.posted()] == ["p1"]
    assert h.ports.typing_sent[-1] == ("p3", "stop")


async def test_stale_message_generation_is_skipped(harness):
    h = harness("keyword")
    mid = h.candidate("물이요.", intent_of("propose", key="water"))
    h.candidate("아니 바람이요.", intent_of("propose", key="wind"))
    run = await h.run("message", message_id=mid, generation=h.ports.generation - 1)
    assert run.status == "skipped" and not h.persona.calls


# ---- 복구·관측 ----------------------------------------------------------------------------------
async def test_persona_llm_failure_skips_speaker(harness):
    h = harness("keyword", persona=[RuntimeError("down")] * 3)
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    run = await h.run("message", message_id=mid)
    assert run.fallback_used and run.error.startswith("persona_llm_unavailable:p1")
    assert not h.posted() and run.usage["retries"] == 2
    assert h.ports.typing_sent[-1] == ("p1", "stop")


async def test_trace_and_decision(harness):
    h = harness(
        "keyword", persona=[say("불이 맞아요.", {"option_id": "fire", "stance": "support"})]
    )
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    run_id = uuid.uuid4()
    run = await h.run("message", message_id=mid, run_id=run_id)
    assert h.persona.calls[0].config["metadata"]["agent_run_id"] == str(run_id)
    assert set(run.decision) >= {
        "stimulus",
        "generation",
        "speakers",
        "fill",
        "invite",
        "llm_used",
        "dropped_seats",
        "reason",
    }


async def test_unexpected_error_after_respond_stops_typing(harness):
    fire = {"option_id": "fire", "stance": "support"}
    h = harness("keyword", persona=[say("불이 맞아요.", fire)])
    original = h.server._call

    async def call(name, args):  # 게시 중 예상 밖 예외(LLMUnavailable가 아님)
        if name == "post_message":
            raise RuntimeError("boom")
        return await original(name, args)

    h.server._call = call
    run = await h.run("phase_started")
    assert run.status == "failed" and not run.ok
    assert ("p1", "start") in h.ports.typing_sent and h.ports.typing_sent[-1] == ("p1", "stop")


async def test_llm_fault_persona_timeout_skips_one_speaker(harness, monkeypatch):
    from app.agent.llm import base, make_llm

    assert base.parse_fault("persona:timeout:3") == {"persona": 3}
    monkeypatch.setattr(base, "FAULTS", {"persona": 3})
    h = harness("keyword")
    h.runner.llms.persona = make_llm("persona", {"LLM_FAKE": "1"})
    mid = h.candidate("한결님, 지안님?", intent_of("question", to=("p1", "p3")))
    run = await h.run("message", message_id=mid)
    # 첫 화자는 최초+재시도 2회가 모두 타임아웃 → 발언 생략, 다음 화자는 정상 게시
    assert run.error.startswith("persona_llm_unavailable:p1") and run.usage["retries"] == 2
    assert [m["sender"] for m in h.posted()] == ["p3"]
    assert base.FAULTS == {"persona": 0}


# ---- 설득 가능 규칙 ----------------------------------------------------------------------------
def test_counter_count_counts_only_explicit_alternatives(catalog):
    """비판형 이동 횟수는 다른 안의 명시적 제안·동의(high, 유보 없음)만 센다(B2)."""
    from app.agent.manager import rules

    rule = catalog.persona(catalog.seat_of("critic"))["persuadable"]
    own = {"wind", "opt-wind"}
    counted = [
        intent_of("propose", option_id="opt-water"),
        intent_of("agree", key="tree"),
        {**intent_of("propose"), "new_option_label": "우주"},
    ]
    ignored = [
        intent_of("oppose", option_id="opt-wind"),  # 반대만
        intent_of("propose", option_id="opt-water", reservation=True),  # 유보 표현
        intent_of("agree", option_id="opt-water", confidence="low"),
        intent_of("withhold", option_id="opt-water"),
        intent_of("agree", option_id="opt-wind"),  # 자기 안 동의
    ]
    assert rules.counter_count(counted + ignored, own, rule) == 3
    assert rules.counter_count(ignored, own, rule) == 0


async def test_excluded_seat_must_be_named_in_the_message(harness):
    """분류가 이름을 부르지 않은 자리를 excluded로 내면(안 번호 "3번"을 자리로 읽음) 버린다."""
    h = harness("solution")
    h.server.confirm_keyword("water")
    mid = h.candidate(
        "3번은 보류예요. 다들 어떻게 보세요?", intent_of("question", excluded=("p3",))
    )
    run = await h.run("message", message_id=mid)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p2", "p3"]


async def test_excluded_seat_named_without_honorific(harness):
    """이름을 "님" 없이 불러 뺀 경우도 뺀다("서지안 말고 하람님", "지안 말고 하람님")."""
    for text in ("서지안 말고 하람님 생각은요?", "지안 말고 하람님 생각은요?"):
        h = harness("solution")
        h.server.confirm_keyword("water")
        mid = h.candidate(text, intent_of("question", to=("p2",), excluded=("p3",)))
        run = await h.run("message", message_id=mid)
        assert [s["seat"] for s in run.decision["speakers"]] == ["p2"], text


def test_excluded_seats_is_a_name_fact_check(catalog):
    from app.agent.manager import rules

    assert rules.excluded_seats("서지안 말고요", ["p3"], catalog) == ["p3"]
    assert rules.excluded_seats("지안님 빼고", ["p3"], catalog) == ["p3"]
    assert rules.excluded_seats("3번은 보류", ["p3"], catalog) == []
