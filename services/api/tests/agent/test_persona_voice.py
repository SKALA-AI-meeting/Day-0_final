"""토론 동료 프롬프트(C2): 입장 상태 블록, 상황 사실, 카드 차례 목적, 실제 지지 이동만 거부,
인용 대조(quote_check), 소극형 전체 질문 상태 규칙."""

import re

from app.agent.llm import LLMUnavailable
from app.agent.llm.fake import FakeLLM
from conftest import intent_of, model_reply, say

SUPPORT_FIRE = {"option_id": "fire", "stance": "support"}


def block(text: str, name: str) -> str:
    found = re.search(rf"\[{name}\]\n(.*?)(?:\n\n\[|\Z)", text, re.S)
    return found.group(1) if found else ""


def quote_llm(*verdicts: str, span: str = "김지원님") -> FakeLLM:
    """판정 행의 span은 초안에 있어야 센다(없으면 코드가 버린다)."""
    return FakeLLM(
        [
            {"attributions": [{"target": "candidate", "span": span, "claim": "x", "verdict": v}]}
            for v in verdicts
        ]
    )


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
    """입장이 없는 상태는 구조화 출력만 본다: 안을 나열하는 문장을 판정이 propose로 읽어도 통과."""
    h = problem(harness, persona=[say("가뭄, 침수, 수돗물 불신 중 어디서 제일 자주 겪나요?")])
    h.intent.script = [model_reply(intent_of("propose", key="water_p1"))]
    await h.run("phase_started")
    assert "- 이 단계의 내 안: 아직 없음" in block(h.snapshots[0]["text"], "내 입장")
    post = h.posted("p1")[0]
    assert post["meta"]["stance_check"] == "pass" and h.server.support["p1"] is None
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


async def test_agreeable_phrasing_with_reservation_passes(harness):
    h = harness("keyword", persona=[say("물도 일리는 있는데, 그래도 불이에요.", SUPPORT_FIRE)])
    h.intent.script = [model_reply(intent_of("agree", key="water", reservation=True))]
    mid = h.candidate("한결님, 물은요?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["meta"]["stance_check"] == "pass" and len(h.persona.calls) == 1
    assert h.server.support["p1"] == next(
        o["option_id"] for o in h.server.options if o["scenario_option_key"] == "fire"
    )


async def test_explicit_support_move_in_text_is_rejected_even_with_own_stance(harness):
    h = harness(
        "keyword",
        persona=[say("물로 가요.", SUPPORT_FIRE), say("저는 그래도 불이에요.", SUPPORT_FIRE)],
    )
    h.intent.script = [
        model_reply(intent_of("agree", key="water")),
        model_reply(intent_of("propose", key="fire")),
    ]
    mid = h.candidate("한결님, 물은요?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["content"] == "저는 그래도 불이에요."
    assert post["meta"]["stance_check"] == "regenerated"
    # 재생성 뒤 게시 문장의 판정(불 지지)과 저장 입장이 같다
    assert h.server.support["p1"] == next(
        o["option_id"] for o in h.server.options if o["scenario_option_key"] == "fire"
    )


async def test_withdrawal_in_text_conflicting_with_structured_support_is_regenerated(harness):
    """문장은 철회, 구조화는 지지 → 충돌이라 재생성. 재생성 뒤 게시 문장 판정과 저장 입장이 같다."""
    h = harness(
        "keyword",
        persona=[say("불은 보류할게요.", SUPPORT_FIRE), say("저는 그래도 불이에요.", SUPPORT_FIRE)],
    )
    h.intent.script = [
        model_reply(intent_of("withhold", key="fire")),
        model_reply(intent_of("propose", key="fire")),
    ]
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["content"] == "저는 그래도 불이에요."
    assert post["meta"]["stance_check"] == "regenerated"
    assert h.server.support["p1"] == next(
        o["option_id"] for o in h.server.options if o["scenario_option_key"] == "fire"
    )


async def test_withdrawal_of_own_option_in_text_is_rejected_without_structured_stance(harness):
    h = harness("keyword", persona=[say("불은 보류할게요."), say("불은 보류할게요.")])
    h.intent.script = [
        model_reply(intent_of("withhold", key="fire")),
        model_reply(intent_of("withhold", key="fire")),
    ]
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["meta"]["stance_check"] == "fallback"
    assert post["meta"]["stance_violation"] == "문장이 자기 계획 안을 철회함"


# ---- 인용 대조 -----------------------------------------------------------------------------------


async def test_quote_check_passes_a_real_quote(harness):
    h = harness("keyword", persona=[say("김지원님이 침수를 말씀하셨죠. 그래도 불이에요.")])
    h.runner.llms.quote = quote_llm("supported")
    mid = h.candidate("한결님, 침수가 심해요.", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["meta"]["quote_check"] == "pass"
    user = str(h.runner.llms.quote.calls[0].messages[-1].content)
    assert "한결님, 침수가 심해요." in user and '"target": "candidate"' in user


async def test_quote_check_regenerates_then_safe_phrase(harness):
    h = harness(
        "keyword",
        persona=[say("김지원님도 불에 동의하셨죠."), say("김지원님이 불이 좋다고 하셨잖아요.")],
    )
    h.runner.llms.quote = quote_llm("contradicted", "not_found")
    mid = h.candidate("한결님, 저는 물이요.", intent_of("propose", key="water", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["content"] in h.server.catalog.safe_phrases("p1")
    assert post["meta"]["quote_check"] == "safe" and "quote_violation" in post["meta"]
    assert len(h.persona.calls) == 2
    assert "인용 오류" in str(h.persona.calls[1].messages[-1].content)


async def test_quote_check_regenerated_draft_is_rechecked_in_order(harness):
    """재생성 초안도 guard → 입장 판정 → 인용 판정 순으로 검사하고 게시 문장과 저장 입장이 같다."""
    h = harness(
        "keyword",
        persona=[
            say("김지원님도 불에 동의하셨죠."),
            say("김지원님이 물을 말씀하셨죠. 저는 불이에요.", SUPPORT_FIRE),
        ],
    )
    h.runner.llms.quote = quote_llm("not_found", "supported")
    h.intent.script = [
        model_reply(intent_of("other")),
        model_reply(intent_of("propose", key="fire")),
    ]
    mid = h.candidate("한결님, 저는 물이요.", intent_of("propose", key="water", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["content"].endswith("저는 불이에요.")
    assert post["meta"]["quote_check"] == "regenerated"
    assert len(h.runner.llms.quote.calls) == 2 and len(h.intent.calls) == 2
    assert h.server.support["p1"] == next(
        o["option_id"] for o in h.server.options if o["scenario_option_key"] == "fire"
    )


async def test_quote_check_uses_full_phase_history_not_the_window(harness):
    h = harness("keyword", persona=[say("김지원님이 처음에 침수 얘기를 하셨죠.")])
    h.runner.llms.quote = quote_llm("supported")
    h.candidate("침수가 심해요.", intent_of("other", confidence="low"))
    for n in range(35):
        h.server.system_says(f"안내 {n}")  # 첫 메시지가 대화 창 30개 밖으로 밀린다
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    user = str(h.runner.llms.quote.calls[0].messages[-1].content)
    assert "침수가 심해요." in user
    assert ("get_transcript", {"phase": "keyword", "limit": 200}) in h.server.calls


async def test_quote_check_covers_unnamed_attribution_to_the_candidate(harness):
    h = harness("keyword", persona=[say("아까 그렇게 말씀하셨죠. 그래도 불이에요.")])
    h.runner.llms.quote = quote_llm("supported", span="아까 그렇게 말씀하셨죠")
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    assert len(h.runner.llms.quote.calls) == 1  # 이름이 없어도 지원자에 답하는 발언은 검사


async def test_quote_check_drops_rows_whose_span_is_not_in_the_draft(harness):
    """판정이 초안 자신의 주장을 귀속으로 읽으면(span이 초안에 없음) 코드가 버리고 게시한다."""
    h = harness("keyword", persona=[say("김지원님이 물이라고 하셨죠. 저는 그래도 불이에요.")])
    h.runner.llms.quote = quote_llm("not_found", span="지원자가 불이 싫다고 했다")
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    post = h.posted("p1")[0]
    assert post["content"] == "김지원님이 물이라고 하셨죠. 저는 그래도 불이에요."
    assert post["meta"]["quote_check"] == "pass" and len(h.persona.calls) == 1


async def test_quote_check_covers_the_candidate_called_by_role(harness):
    h = harness("keyword", persona=[say("지원자님은 침수를 강조하셨는데요. 그래도 불이에요.")])
    h.runner.llms.quote = quote_llm("supported", span="지원자님은 침수를 강조하셨는데요")
    await h.run("phase_started")  # 답할 메시지 없음: 역할 호칭만으로 대상이 된다
    assert len(h.runner.llms.quote.calls) == 1


async def test_quote_check_is_skipped_without_targets(harness):
    h = harness("keyword", persona=[say("불이 맞아요.", SUPPORT_FIRE)])
    h.runner.llms.quote = quote_llm("contradicted")
    await h.run("phase_started")  # 답할 메시지 없음, 이름 없음
    assert len(h.runner.llms.quote.calls) == 0
    assert h.posted("p1")[0]["meta"]["quote_check"] == "none"


async def test_quote_check_failure_posts_with_skipped_mark(harness):
    h = harness("keyword", persona=[say("김지원님 말씀처럼 불이에요.")])
    h.runner.llms.quote = FakeLLM([LLMUnavailable("timeout"), "not json"])
    for text in ("한결님?", "한결님, 다시요?"):
        mid = h.candidate(text, intent_of("question", to=("p1",)))
        await h.run("message", message_id=mid)
        h.persona.script = [say("아까 침수라고 하셨는데, 그래도 저는 불이 맞다고 봐요.")]
    assert [p["meta"]["quote_check"] for p in h.posted("p1")] == ["skipped", "skipped"]


async def test_quote_check_is_skipped_when_sources_are_incomplete(harness):
    """출처 조회 실패나 상한(200)에 닿은 조회는 판단하지 않고 skipped로 게시한다."""
    from app.agent.debate import graph

    h = harness("keyword", persona=[say("김지원님 말씀처럼 불이에요.")])
    h.runner.llms.quote = quote_llm("not_found")
    original = h.server.t_get_transcript
    h.server.t_get_transcript = lambda **_: {"ok": False, "error": {"code": "upstream"}}
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    assert h.posted("p1")[0]["meta"]["quote_check"] == "skipped"
    h.server.t_get_transcript = original
    for n in range(graph.QUOTE_TRANSCRIPT_LIMIT):
        h.server.system_says(f"안내 {n}")
    h.persona.script = [say("김지원님 말씀처럼 불이에요. 네.")]
    mid = h.candidate("한결님, 다시요?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    assert h.posted("p1")[1]["meta"]["quote_check"] == "skipped"
    assert len(h.runner.llms.quote.calls) == 0


async def test_quote_check_gate_needs_a_reporting_form(harness):
    """이름이 있어도 남의 말을 전하는 꼴이 아니면(호칭·질문) 호출하지 않는다."""
    h = harness("keyword", persona=[say("김지원님, 물은 왜 좋다고 보세요? 저는 불이에요.")])
    h.runner.llms.quote = quote_llm("not_found")
    mid = h.candidate("한결님?", intent_of("question", to=("p1",)))
    await h.run("message", message_id=mid)
    assert len(h.runner.llms.quote.calls) == 0
    assert h.posted("p1")[0]["meta"]["quote_check"] == "none"


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
