"""토론 동료 프롬프트(C2): 입장 상태 블록, 상황 사실, 카드 차례 목적, 실제 지지 이동만 거부,
소극형 전체 질문 상태 규칙."""

import re

from conftest import intent_of, say, stance_reply

SUPPORT_FIRE = {"option_id": "fire", "stance": "support"}


def block(text: str, name: str) -> str:
    found = re.search(rf"\[{name}\]\n(.*?)(?:\n\n\[|\Z)", text, re.S)
    return found.group(1) if found else ""


def problem(harness, **scripts):
    h = harness("problem", **scripts)
    h.server.confirm_keyword("water")
    return h


# ---- 입장 상태 블록과 상황 사실 ------------------------------------------------------------------


async def test_stance_block_is_state_not_instruction(harness):
    h = harness("keyword", persona=[say("불이 맞아요.", SUPPORT_FIRE), say("왜요?")])
    await h.run("phase_started")
    first = block(h.snapshots[0]["text"], "내 입장")
    assert first.splitlines() == [
        "- 이 단계의 내 안: 불 (fire)",
        "- 이 안 지지를 말로 밝혔는지: 아니요 (아직 기록 없음)",
        "- 이 단계에서 지지를 옮길 수 있는지: 아니요",
    ]
    assert "[내 입장 계획]" not in h.snapshots[0]["text"]
    h.snapshots.clear()
    mid = h.candidate("한결님, 왜요?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    assert "- 이 안 지지를 말로 밝혔는지: 예 (기록됨)" in block(h.snapshots[0]["text"], "내 입장")


async def test_undeclared_stance_is_a_fact_and_only_structured_output_counts(harness):
    """입장이 없는 상태는 구조화 출력만 본다: 내 안이 없어 입장 판정을 묻지 않는다."""
    h = problem(harness, persona=[say("가뭄, 침수, 수돗물 불신 중 어디서 제일 자주 겪나요?")])
    h.intent.script = [stance_reply("move", "water_p1")]
    await h.run("phase_started")
    assert "- 이 단계의 내 안: 아직 없음" in block(h.snapshots[0]["text"], "내 입장")
    post = h.posted("p1")[0]
    assert post["meta"]["stance_check"] == "pass" and h.server.support["p1"] is None
    assert h.intent.calls == []  # 판정 호출 없음
    h.persona.script = [
        say("가뭄이죠.", {"option_id": "water_p1", "stance": "support"}),
        say("음."),
    ]
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[1]  # 구조화 지지는 위반 → 재생성(두 번째 초안은 입장 없음)
    assert post["meta"]["stance_check"] == "regenerated" and h.server.support["p1"] is None


async def test_unnamed_objection_counts_from_stored_intents(harness):
    h = harness("keyword", persona=[say("불이 맞아요.", SUPPORT_FIRE), say("그래도 불이에요.")])
    await h.run("phase_started")
    h.candidate("불은 아닌 것 같아요.", intent_of("oppose", key="fire"))  # 이름 없는 반박
    h.candidate("물이 좋겠어요.", intent_of("propose", key="water"))
    h.candidate(
        "역시 물이요.", intent_of("agree", key="water")
    )  # 같은 안을 두 번: 메시지 수 2, 안 1
    h.snapshots.clear()
    mid = h.candidate(
        "한결님, 불은 범위가 넓지 않나요?", intent_of("question", key="fire", to=("p1",))
    )
    await h.run("message", message_id=mid)
    lines = block(h.snapshots[0]["text"], "상황").splitlines()
    assert (
        "- 내가 입장을 밝힌 뒤 내 안을 두고 온 지원자 메시지: 반대 1개, 보류 0개, 질문 1개 "
        '(마지막: "한결님, 불은 범위가 넓지 않나요?")'
    ) in lines
    assert "- 내가 입장을 밝힌 뒤 지원자가 다른 안을 지지한 메시지: 2개 (안: 물)" in lines
    assert "candidate_intents" not in h.snapshots[0]["text"]


async def test_moved_stance_is_shown_as_a_fact(harness):
    h = harness(
        "keyword",
        persona=[
            say("불이요.", SUPPORT_FIRE),
            say("바람이요.", {"option_id": "wind", "stance": "support"}),
        ],
    )
    await h.run("phase_started")
    for _ in range(2):
        h.candidate("물이 좋아요.", intent_of("propose", key="water"))
    h.persona.script = [
        say("듣고 보니 물이 낫겠네요.", {"option_id": "water", "stance": "support"})
    ]
    h.snapshots.clear()
    mid = h.candidate("지안님?", intent_of("question", to=("p3",)))
    await h.run("message", message_id=mid)
    text = h.snapshots[0]["text"]
    assert "- 내 지지가 옮겨졌는데 아직 말로 밝히지 않음: 예 (바람 → 물, 지원자 안)" in text
    assert "이번 발언에서 이 안을 지지한다고 밝힌다" not in text


# ---- 카드 차례: 사실 + 장면 목적(데이터) -------------------------------------------------


async def test_card_turn_shows_facts_and_scene_purpose_from_data(harness):
    h = harness("keyword")
    h.candidate("물이 좋겠어요.", intent_of("propose", key="water"))
    await h.run("card_due", card_id="persuasion_keyword")
    card = block(h.snapshots[0]["text"], "카드 차례")
    purpose = h.server.catalog.card("persuasion_keyword")["purpose"]
    assert card.splitlines() == ["- 이번 발언이 내 카드 차례: 예", f"- 이 장면의 목적: {purpose}"]
    assert "[카드 지시]" not in h.snapshots[0]["text"] and "미세요" not in card


# ---- 실제 지지 이동만 거부 ---------------------------------------------------------------


def fire_id(h) -> str:
    return next(o["option_id"] for o in h.server.options if o["scenario_option_key"] == "fire")


async def test_stance_judge_gets_its_own_question_not_the_intent_prompt(harness):
    """입장 판정은 지원자 의도 분류 프롬프트가 아니라 stance_check.md와 [내 안]·[초안]을 쓴다."""
    h = harness("keyword", persona=[say("물도 일리는 있는데, 그래도 불이에요.", SUPPORT_FIRE)])
    h.intent.script = [stance_reply("keep", "fire")]
    mid = h.candidate("한결님, 물은요?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    call = h.intent.calls[0]
    assert call.schema is not None and set(call.schema["properties"]) == {
        "verdict",
        "target",
        "confidence",
    }
    system, user = (str(m.content) for m in call.messages)
    assert system == h.server.catalog.prompts["stance_check.md"]
    assert '"내 안": {"id": "fire", "label": "불"}' in user and '"초안": "물도' in user
    assert '"메시지"' not in user and "카탈로그 안" not in user and "wanted" not in user


async def test_agreeable_phrasing_with_reservation_passes(harness):
    """양보 뒤 자기 안에 서는 문장은 keep → 통과(유보 예외 코드 없이 질문 자체가 거른다)."""
    h = harness("keyword", persona=[say("물도 일리는 있는데, 그래도 불이에요.", SUPPORT_FIRE)])
    h.intent.script = [stance_reply("keep", "fire")]
    mid = h.candidate("한결님, 물은요?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["meta"]["stance_check"] == "pass" and len(h.persona.calls) == 1
    assert h.server.support["p1"] == fire_id(h)


async def test_concession_to_other_option_keeps_support_when_assigned_that_option(harness):
    """ "A도 좋지만 저는 B가 낫다"를 B 담당 자리가 말하면 keep: 구조화 stance 없이도 지지 기록."""
    h = harness("keyword", persona=[say("물도 좋지만 저는 불이 낫다고 봐요.")])
    h.intent.script = [stance_reply("keep", "fire")]
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["meta"]["stance_check"] == "pass" and len(h.intent.calls) == 1
    assert h.server.support["p1"] == fire_id(h)


async def test_concession_to_own_option_then_other_is_a_move(harness):
    """같은 문장을 A(불) 담당이 "불도 좋지만 저는 물"로 말하면 move → 재생성."""
    h = harness(
        "keyword",
        persona=[
            say("불도 좋지만 저는 물이 낫다고 봐요.", SUPPORT_FIRE),
            say("저는 그래도 불이에요.", SUPPORT_FIRE),
        ],
    )
    h.intent.script = [stance_reply("move", "water"), stance_reply("keep", "fire")]
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["content"] == "저는 그래도 불이에요."
    assert post["meta"]["stance_check"] == "regenerated"
    assert post["meta"]["stance_violation"] == "문장이 계획과 다른 안을 지지함"
    assert h.server.support["p1"] == fire_id(h)


async def test_explicit_support_move_in_text_is_rejected_even_with_own_stance(harness):
    h = harness(
        "keyword",
        persona=[say("물로 가요.", SUPPORT_FIRE), say("저는 그래도 불이에요.", SUPPORT_FIRE)],
    )
    h.intent.script = [stance_reply("move", "water"), stance_reply("keep", "fire")]
    mid = h.candidate("한결님, 물은요?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["content"] == "저는 그래도 불이에요."
    assert post["meta"]["stance_check"] == "regenerated"
    # 재생성 뒤 게시 문장의 판정(불 지지)과 저장 입장이 같다
    assert h.server.support["p1"] == fire_id(h)


async def test_move_verdict_naming_own_option_is_not_trusted(harness):
    """move인데 고른 안이 내 안이면 앞뒤가 맞지 않는 판정이라 쓰지 않는다(통과)."""
    h = harness("keyword", persona=[say("저는 불이요.", SUPPORT_FIRE)])
    h.intent.script = [stance_reply("move", "fire")]
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    assert h.posted("p1")[0]["meta"]["stance_check"] == "pass"


async def test_low_confidence_or_none_verdict_passes(harness):
    h = harness("keyword", persona=[say("물로 가요.", SUPPORT_FIRE), say("왜요?")])
    h.intent.script = [stance_reply("move", "water", confidence="low"), stance_reply("none")]
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    assert [p["meta"]["stance_check"] for p in h.posted("p1")] == ["pass"]
    mid = h.candidate("한결님, 이유는요?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    assert [p["meta"]["stance_check"] for p in h.posted("p1")] == ["pass", "pass"]


async def test_withdrawal_in_text_conflicting_with_structured_support_is_regenerated(harness):
    """문장은 철회, 구조화는 지지 → 충돌이라 재생성. 재생성 뒤 게시 문장 판정과 저장 입장이 같다."""
    h = harness(
        "keyword",
        persona=[say("불은 보류할게요.", SUPPORT_FIRE), say("저는 그래도 불이에요.", SUPPORT_FIRE)],
    )
    h.intent.script = [stance_reply("withdraw", "fire"), stance_reply("keep", "fire")]
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["content"] == "저는 그래도 불이에요."
    assert post["meta"]["stance_check"] == "regenerated"
    assert h.server.support["p1"] == fire_id(h)


async def test_withdrawal_of_own_option_in_text_is_rejected_without_structured_stance(harness):
    h = harness("keyword", persona=[say("불은 보류할게요."), say("불은 보류할게요.")])
    h.intent.script = [stance_reply("withdraw", "fire"), stance_reply("withdraw", "fire")]
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["meta"]["stance_check"] == "fallback"
    assert post["meta"]["stance_violation"] == "문장이 자기 계획 안을 철회함"


# ---- 소극형 전체 질문 상태 규칙 ------------------------------------------------------------------


async def test_quiet_skips_a_second_open_question_until_spoken_again(harness):
    h = harness("keyword")
    first = h.candidate("다들 어떤 키워드가 좋으세요?", intent_of("question"))
    run = await h.run("message", message_id=first)
    assert "p2" in [s["seat"] for s in run.decision["speakers"]]
    second = h.candidate("그럼 다들 이유는요?", intent_of("question"))
    run = await h.run("message", message_id=second)
    assert "p2" not in [s["seat"] for s in run.decision["speakers"]]  # 새 맥락 없이 또 전체 질문
    named = h.candidate("하람님은요?", intent_of("question", to=("p2",)))
    run = await h.run("message", message_id=named)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p2"]  # 지명은 늘 답한다
    third = h.candidate("다들 다른 의견 있으세요?", intent_of("question"))
    run = await h.run("message", message_id=third)
    assert "p2" in [s["seat"] for s in run.decision["speakers"]]  # 지명에 답한 뒤라 다시 받는다


async def test_quiet_exclusion_needs_an_answered_open_question(harness):
    """부르지 않은 메시지에 답했어도 그 메시지의 저장 의도가 question이 아니면 빼지 않는다."""
    h = harness("keyword", turn=[{"speakers": [{"seat": "p2"}]}])
    # 턴 LLM이 소극형을 고르지 않으므로 기본 응답 자리가 답한다. 소극형 발언을 직접 넣는다
    said = h.candidate("물이 좋아요.", intent_of("propose", key="water"))
    h.server.messages.append(
        {
            "id": "q-1",
            "sender": "p2",
            "content": "음, 그렇네요.",
            "phase": "keyword",
            "is_invite": False,
            "t_min": 1.0,
            "meta": {"reply_to": said},
        }
    )
    asked = h.candidate("다들 어떤 키워드가 좋으세요?", intent_of("question"))
    run = await h.run("message", message_id=asked)
    assert "p2" in [s["seat"] for s in run.decision["speakers"]]
