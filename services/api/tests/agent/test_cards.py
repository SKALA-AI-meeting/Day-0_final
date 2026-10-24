"""카드 전달·기준안·관찰과 계약 테스트 C5(testing.md "수용·경청 카드")."""

import re

import pytest
from conftest import intent_of, say, stance_reply

FLEX, PRESSURE = "flexibility_majority", "listening_pressure"


def problem(harness, **scripts):
    h = harness("problem", **scripts)
    h.server.confirm_keyword("water")
    return h


def option_id(h, key: str) -> str:
    return next(o["option_id"] for o in h.server.options if o["scenario_option_key"] == key)


def bump_generation(h, line: dict):
    """두 번째 화자 생성 중에 새 지원자 메시지가 들어온 상황."""

    def step(_messages):
        h.server.candidate_says("잠깐만요, 하나 더 말할게요.")
        return line

    return step


async def test_c5_lead_lock_joint_delivery_and_observations(harness):
    h = problem(
        harness,
        persona=[
            say(
                "그건 핵심이 아니에요. 침수가 먼저예요.",
                {"option_id": "water_p2", "stance": "support"},
            ),
            say(
                "저도 침수 피해 쪽이 더 크다고 봐요.",
                {"option_id": "water_p2", "stance": "support"},
            ),
        ],
    )
    h.candidate("가뭄 문제가 제일 커요.", intent_of("propose", key="water_p1"))

    run = await h.run("card_due", card_id=FLEX)

    assert run.ok and run.status == "finished"
    p2 = option_id(h, "water_p2")
    # 기준안 = 지원자 현재 지지(P1)가 아닌 첫 시나리오 안(P2), 첫 담당 자리가 행을 만든다
    assert next(o for o in h.server.options if o["option_id"] == p2)["proposer"] == "p1"
    posts = h.posted()
    assert [m["sender"] for m in posts] == ["p1", "p3"]
    assert posts[0]["card_ids"] == [FLEX, PRESSURE] and posts[1]["card_ids"] == [FLEX]
    assert h.server.cards[FLEX]["delivered"] == ["p1", "p3"]
    assert h.server.cards[PRESSURE]["delivered"] == ["p1"]
    # 관찰은 카드마다 1건, 같은 trigger 메시지(공동 전달)
    assert {c: o["trigger_message_id"] for c, o in h.server.observations.items()} == {
        FLEX: posts[0]["id"],
        PRESSURE: posts[0]["id"],
    }
    assert [n for n, _ in h.assessment.calls] == ["record_observation"] * 2
    assert h.server.support["p1"] == h.server.support["p3"] == p2
    assert run.decision["speakers"][0]["card_ids"] == [FLEX, PRESSURE]


async def test_c5_api_lock_is_used(harness):
    h = problem(
        harness, persona=[say("침수가 핵심이에요.", {"option_id": "water_p3", "stance": "support"})]
    )
    p3 = h.server.add_option("water_p3", "p1")
    h.server.lock(FLEX, p3)
    await h.run("card_due", card_id=FLEX)
    assert h.server.support["p1"] == p3


async def test_c5_dropped_card_speaker_resumes_next_run(harness):
    h = problem(
        harness,
        persona=[
            say("침수가 먼저예요.", {"option_id": "water_p2", "stance": "support"}),
        ],
    )
    h.persona.script.append(
        bump_generation(
            h, say("저도 침수 쪽이에요.", {"option_id": "water_p2", "stance": "support"})
        )
    )
    h.candidate("가뭄 문제가 제일 커요.", intent_of("propose", key="water_p1"))

    first = await h.run("card_due", card_id=FLEX)

    assert first.decision["dropped_seats"] == ["p3"]
    assert [m["sender"] for m in h.posted()] == ["p1"]
    assert h.server.cards[FLEX]["delivered"] == ["p1"]

    h.persona.script.append(
        say("저도 침수 쪽이에요.", {"option_id": "water_p2", "stance": "support"})
    )
    second = await h.run("card_due", card_id=PRESSURE)  # 같은 시각 카드의 card_due, 새 run_id

    assert [s["seat"] for s in second.decision["speakers"]] == ["p3"]
    assert second.decision["speakers"][0]["card_ids"] == [FLEX]
    assert h.server.cards[FLEX]["delivered"] == ["p1", "p3"]
    assert len(h.server.observations) == 2 and len(h.assessment.calls) == 2
    assert h.server.support["p3"] == option_id(h, "water_p2")


async def test_c5_critic_moves_after_two_counters_stubborn_holds(harness):
    support_p2 = {"option_id": "water_p2", "stance": "support"}
    h = problem(
        harness,
        persona=[say("침수가 먼저예요.", support_p2), say("저도 침수 쪽이에요.", support_p2)],
    )
    h.candidate("가뭄 문제가 제일 커요.", intent_of("propose", key="water_p1"))
    await h.run("card_due", card_id=FLEX)
    p1, p2 = option_id(h, "water_p1"), option_id(h, "water_p2")

    # 설득 가능 규칙은 다른 안의 명시적 제안·동의만 센다(B2)
    h.candidate("침수보다 가뭄이 더 자주 와요. 가뭄으로 가요.", intent_of("propose", option_id=p1))
    h.candidate("저는 계속 가뭄이 맞다고 봐요.", intent_of("propose", option_id=p1))
    h.persona.script += [
        say("듣고 보니 가뭄 쪽이 맞네요.", {"option_id": p1, "stance": "support"}),
        say("그래도 침수가 핵심이에요.", support_p2),
    ]
    mid = h.candidate("지안님, 한결님 어떻게 보세요?", intent_of("question", to=("p3", "p1")))
    run = await h.run("message", message_id=mid)

    assert [s["seat"] for s in run.decision["speakers"]] == ["p3", "p1"]
    assert h.server.support["p3"] == p1  # 비판형은 지원자 안으로 이동
    assert h.server.support["p1"] == p2  # 고집형은 확정 전 불변
    assert all(m["meta"]["stance_check"] == "pass" for m in h.posted()[-2:])


async def test_critic_does_not_move_on_opposition_alone(harness):
    """기준안에 반대만 두 번 하면 비판형은 옮기지 않는다(B2: 반대 발언은 세지 않음)."""
    support_p2 = {"option_id": "water_p2", "stance": "support"}
    h = problem(
        harness,
        persona=[say("침수가 먼저예요.", support_p2), say("저도 침수 쪽이에요.", support_p2)],
    )
    h.candidate("가뭄 문제가 제일 커요.", intent_of("propose", key="water_p1"))
    await h.run("card_due", card_id=FLEX)
    p1, p2 = option_id(h, "water_p1"), option_id(h, "water_p2")
    h.candidate("침수는 이미 대책이 많아요.", intent_of("oppose", option_id=p2))
    h.candidate("침수보다 가뭄이 더 자주 와요.", intent_of("oppose", option_id=p2))
    h.persona.script += [
        say("듣고 보니 가뭄 쪽이 맞네요.", {"option_id": p1, "stance": "support"}),
        say("듣고 보니 가뭄 쪽이 맞네요.", {"option_id": p1, "stance": "support"}),
    ]
    mid = h.candidate("지안님 어떻게 보세요?", intent_of("question", to=("p3",)))
    await h.run("message", message_id=mid)
    assert h.server.support["p3"] == p2


async def test_critic_does_not_move_after_one_counter(harness):
    support_p2 = {"option_id": "water_p2", "stance": "support"}
    h = problem(
        harness,
        persona=[say("침수가 먼저예요.", support_p2), say("저도 침수 쪽이에요.", support_p2)],
    )
    h.candidate("가뭄 문제가 제일 커요.", intent_of("propose", key="water_p1"))
    await h.run("card_due", card_id=FLEX)
    p1, p2 = option_id(h, "water_p1"), option_id(h, "water_p2")
    h.candidate("침수는 이미 대책이 많아요.", intent_of("oppose", option_id=p2))
    h.persona.script += [
        say("듣고 보니 가뭄 쪽이 맞네요.", {"option_id": p1, "stance": "support"}),
        say("듣고 보니 가뭄 쪽이 맞네요.", {"option_id": p1, "stance": "support"}),
    ]
    mid = h.candidate("지안님 어떻게 보세요?", intent_of("question", to=("p3",)))

    await h.run("message", message_id=mid)

    last = h.posted("p3")[-1]
    assert last["meta"]["stance_check"] == "fallback"
    assert h.server.support["p3"] == p2


async def test_card_due_keyword_critic_pushes_own_keyword(harness):
    h = harness(
        "keyword",
        persona=[
            say(
                "저는 바람이 낫다고 봐요. 물은 너무 넓지 않아요?",
                {"option_id": "wind", "stance": "support"},
            )
        ],
    )
    h.candidate("물이 좋겠어요.", intent_of("propose", key="water"))
    run = await h.run("card_due", card_id="persuasion_keyword")
    assert run.decision["speakers"] == [
        {"seat": "p3", "reply_to": None, "card_ids": ["persuasion_keyword"], "invite": False}
    ]
    assert list(h.server.observations) == ["persuasion_keyword"]


async def test_card_due_keyword_stubborn_when_candidate_backs_wind(harness):
    h = harness(
        "keyword", persona=[say("불이 맞아요.", {"option_id": "fire", "stance": "support"})]
    )
    h.candidate("바람이 좋겠어요.", intent_of("propose", key="wind"))
    run = await h.run("card_due", card_id="persuasion_keyword")
    assert [s["seat"] for s in run.decision["speakers"]] == ["p1"]


async def test_inclusion_silence_fixed_line_without_llm(harness):
    h = harness("solution")
    h.server.confirm_keyword("water")
    run = await h.run("card_due", card_id="inclusion_silence")
    post = h.posted("p2")[0]
    assert post["content"] == h.server.catalog.card("inclusion_silence")["fixed_line"]
    assert post["card_ids"] == ["inclusion_silence"] and not h.persona.calls
    assert run.ok and list(h.server.observations) == ["inclusion_silence"]


async def test_persuasion_solution_swaps_actor_not_stance(harness):
    h = harness(
        "solution",
        persona=[say("센서 데이터가 먼저예요.", {"option_id": "water_s1", "stance": "support"})],
    )
    h.server.confirm_keyword("water")
    h.candidate("주민 캠페인이 좋겠어요.", intent_of("propose", key="water_s2"))
    run = await h.run("card_due", card_id="persuasion_solution")
    assert [s["seat"] for s in run.decision["speakers"]] == ["p1"]
    assert h.server.support["p1"] == option_id(h, "water_s1")
    assert h.server.support["p3"] is None


async def test_honesty_credit_observed_on_proposal_start(harness):
    h = harness("proposal")
    h.server.confirm_keyword("water")
    notice = h.server.system_says("기획안 작성 단계예요.")
    run = await h.run("phase_started")
    assert run.decision["speakers"] == [] and not h.posted()
    assert h.server.observations["honesty_credit"]["trigger_message_id"] == notice


async def test_moved_critic_must_record_support_for_candidate_option(harness):
    """세션 3e987267: 이동한 비판형이 옛 안 oppose만 내고 지원자 안 지지를 밝히지 않아
    합의가 나지 않았다."""
    h = harness(
        "solution",
        persona=[
            say("센서가 맞아요."),
            say("캠페인이 낫죠.", {"option_id": "water_s2", "stance": "support"}),
        ],
    )
    h.server.confirm_keyword("water")
    # 해결책 카드 두 장이 전달된 뒤(전달 전에는 합의 채움을 보류한다, C3)
    h.server.deliver("persuasion_solution", "p3")
    h.server.deliver("inclusion_silence", "p2")
    await h.run("phase_started")
    s2 = option_id(h, "water_s2")
    h.candidate("생활 인프라가 좋다고 생각해요.", intent_of("propose", key="water_s3"))
    s3 = option_id(h, "water_s3")
    h.server.set_support("p2", s3)
    oppose_old = say("인프라는 초기 비용이 커요.", {"option_id": s2, "stance": "oppose"})
    h.persona.script += [oppose_old, oppose_old]
    mid = h.candidate(
        "지안님, 캠페인보다 인프라로 가요.", intent_of("propose", option_id=s3, to=("p3",))
    )
    await h.run("message", message_id=mid)

    # 계획 밖 안 oppose만 냄 → 재생성 → 그래도 지지 없음 → 이동 직후 고정 문구 + 지지 저장 → 합의
    post = h.posted("p3")[-1]
    assert post["meta"]["stance_check"] == "fallback"
    assert post["meta"]["stance_violation"] == "지원자 안으로 옮겼는데 그 안 지지를 밝히지 않음"
    assert (
        post["content"]
        == "듣고 보니 빗물 저금통·투수 포장 같은 생활 인프라가 더 낫겠네요. 저도 그쪽으로 옮길게요."
    )
    assert len(h.persona.calls) == 4  # 단계 시작 2 + 최초 1 + 재생성 1
    assert h.server.support["p3"] == s3  # 이동이 기록돼 지원자+p2+p3 지지
    # 이동과 같은 run에서는 채우지 않는다(B2). 지원자의 최신 발언이 그 안 동의면 다음 run에서 채운다
    assert "solution" not in h.server.slots
    agreed = h.candidate("네, 인프라로 정해요.", intent_of("agree", option_id=s3))
    await h.run("message", message_id=agreed)
    assert h.server.slots["solution"]["option_id"] == s3


@pytest.mark.parametrize("via", ["sentence", "proposes"])
async def test_moved_critic_support_overrides_structured_oppose(harness, via):
    """문장 판정이나 proposes는 지원자 안 지지인데 구조화 stance가 옛 안 oppose면 저장 입장을
    계획 안 지지로 정정한다."""
    s3 = "opt-water_s3"
    judged = stance_reply("keep", s3) if via == "sentence" else "not json"
    h = harness(
        "solution",
        persona=[
            say("센서가 맞아요."),
            say("캠페인이 낫죠.", {"option_id": "water_s2", "stance": "support"}),
        ],
        intent=["not json", "not json", judged],
    )
    h.server.confirm_keyword("water")
    await h.run("phase_started")
    s2 = option_id(h, "water_s2")
    h.candidate("생활 인프라가 좋다고 생각해요.", intent_of("propose", key="water_s3"))
    h.server.set_support("p2", s3)
    proposes = {"scenario_option_key": "water_s3"} if via == "proposes" else None
    h.persona.script += [
        say("듣고 보니 인프라가 낫네요.", {"option_id": s2, "stance": "oppose"}, proposes)
    ]
    mid = h.candidate(
        "지안님, 캠페인보다 인프라로 가요.", intent_of("propose", option_id=s3, to=("p3",))
    )
    await h.run("message", message_id=mid)

    post = h.posted("p3")[-1]
    assert (
        post["content"] == "듣고 보니 인프라가 낫네요." and post["meta"]["stance_check"] == "pass"
    )
    assert h.server.support["p3"] == s3
    assert "solution" not in h.server.slots  # 이동과 같은 run에서는 채우지 않는다(B2)


async def test_pressure_counts_from_the_card_message_up_to_three(harness):
    """압박 횟수는 카드 첫 발언부터 센다(카드 전 발언 제외). 카드 발언 포함 최대 3회."""

    support_p2 = {"option_id": "water_p2", "stance": "support"}
    h = problem(
        harness,
        persona=[
            say("침수가 먼저예요.", support_p2),
            say("저도 침수 쪽이에요.", support_p2),
            say("그래도 침수가 핵심이에요.", support_p2),
            say("피해 규모가 침수 쪽이 커요.", support_p2),
            say("침수 대비가 더 급해요.", support_p2),
        ],
    )
    for n in range(3):  # 카드 전 고집형 발언(단계 시작 반응 등)
        h.server.messages.append(
            {
                "id": f"pre-{n}",
                "sender": "p1",
                "content": f"자료부터 봐요 {n}",
                "phase": "problem",
                "is_invite": False,
                "t_min": 0.5,
            }
        )
    h.candidate("가뭄 문제가 제일 커요.", intent_of("propose", key="water_p1"))
    await h.run("card_due", card_id=FLEX)

    followups = []
    for text in ("가뭄이 더 자주 와요.", "가뭄 피해도 커요.", "그래도 가뭄이에요."):
        h.snapshots.clear()
        mid = h.candidate(f"한결님, {text}", intent_of("oppose", key="water_p2", to=("p1",)))
        await h.run("message", message_id=mid)
        followups.append(_pressure_round(h.snapshots[0]["text"]))
    # 카드 발언(1회) + 재반박 2회 = 3회(회차는 사실로, 장면 목적은 cards.yaml followup_purpose),
    # 그 뒤에는 카드 차례 블록이 없다
    assert followups == [2, 3, None]


def _pressure_round(text: str) -> int | None:
    found = re.search(r"압박 발언 (\d)회째, 최대 3회", text)
    return int(found.group(1)) if found else None


async def test_pressure_count_is_not_limited_to_the_recent_window(harness):
    """카드 발언·재반박이 최근 대화 30개 밖으로 밀려도 압박 횟수가 다시 시작되지 않는다."""

    support_p2 = {"option_id": "water_p2", "stance": "support"}
    h = problem(
        harness,
        persona=[
            say("침수가 먼저예요.", support_p2),
            say("저도 침수 쪽이에요.", support_p2),
            say("그래도 침수가 핵심이에요.", support_p2),
            say("피해 규모가 침수 쪽이 커요.", support_p2),
            say("침수 대비가 더 급해요.", support_p2),
        ],
    )
    h.candidate("가뭄 문제가 제일 커요.", intent_of("propose", key="water_p1"))
    await h.run("card_due", card_id=FLEX)
    for text in ("가뭄이 더 자주 와요.", "가뭄 피해도 커요."):
        mid = h.candidate(f"한결님, {text}", intent_of("oppose", key="water_p2", to=("p1",)))
        await h.run("message", message_id=mid)
    for n in range(35):  # 카드 발언과 재반박 2회가 최근 대화 창 밖으로 밀린다
        h.server.system_says(f"안내 {n}")
    h.snapshots.clear()
    mid = h.candidate("한결님, 그래도 가뭄이에요.", intent_of("oppose", key="water_p2", to=("p1",)))
    await h.run("message", message_id=mid)
    assert _pressure_round(h.snapshots[0]["text"]) is None  # 이미 3회


async def test_critic_count_survives_restart(harness):
    """설득 가능 규칙 횟수는 저장된 의도(DebateState candidate_intents)에서 센다. 프로세스 메모리가
    비어도(재시작) 같은 결과다."""
    from app.agent import intent as intent_mod

    support_p2 = {"option_id": "water_p2", "stance": "support"}
    h = problem(
        harness,
        persona=[say("침수가 먼저예요.", support_p2), say("저도 침수 쪽이에요.", support_p2)],
    )
    h.candidate("가뭄 문제가 제일 커요.", intent_of("propose", key="water_p1"))
    await h.run("card_due", card_id=FLEX)
    p1 = option_id(h, "water_p1")
    h.candidate("가뭄으로 가요.", intent_of("propose", option_id=p1))
    h.candidate("저는 계속 가뭄이 맞다고 봐요.", intent_of("propose", option_id=p1))
    h.persona.script += [say("듣고 보니 가뭄 쪽이 맞네요.", {"option_id": p1, "stance": "support"})]
    mid = h.candidate("지안님 어떻게 보세요?", intent_of("question", to=("p3",)))
    intent_mod._cache.clear()  # 재시작: 분류 결과 메모리가 비었다

    await h.run("message", message_id=mid)
    assert h.server.support["p3"] == p1


# ---- 카드 이끎(C3): 복선 → 연결 → 실행, 카드 전달 전 합의 채움 보류 --------------------------
def steer_of(h, card_id: str, stage: str) -> str:
    return h.server.catalog.card(card_id)["steer"][stage]


def card_block(text: str) -> list[str]:
    found = re.search(r"\[카드 차례\]\n(.*?)(?:\n\n\[|\Z)", text, re.S)
    return found.group(1).splitlines() if found else []


async def test_manager_leads_card_seed_bridge_execute_in_order(harness):
    """키워드 카드 담당(비판형)이 단계 시작에서 복선, 다음 지원자 메시지에서 연결(턴 LLM 없이 담당을
    고름), card_due에서 실행 순서로 말한다. 카드 시각·담당·장면은 그대로다."""
    h = harness("keyword", turn=[RuntimeError("불려서는 안 된다")])
    card = "persuasion_keyword"

    started = await h.run("phase_started")
    assert [s["seat"] for s in started.decision["speakers"]] == ["p1", "p3"]
    seed = next(s for s in h.snapshots if s["seat"] == "p3")
    assert card_block(seed["text"]) == [
        "- 이번 발언이 내 카드 차례: 아니요 (장면 전, 복선 차례)",
        f"- 이번 발언에 담을 복선: {steer_of(h, card, 'seed')}",
    ]
    assert card_block(next(s for s in h.snapshots if s["seat"] == "p1")["text"]) == []

    mid = h.candidate("음, 어떤 게 좋을까요.", intent_of("other", confidence="low"))
    bridge = await h.run("message", message_id=mid)
    assert [(s["seat"], s["reply_to"]) for s in bridge.decision["speakers"]] == [("p3", mid)]
    assert bridge.decision["llm_used"] is False and not h.turn.calls
    assert bridge.decision["reason"] == "카드 전 이끎 차례(복선·연결)"
    assert card_block(h.snapshots[-1]["text"]) == [
        "- 이번 발언이 내 카드 차례: 아니요 (장면 전, 연결 차례)",
        f"- 이번 발언에 담을 연결: {steer_of(h, card, 'bridge')}",
    ]
    assert card not in h.server.cards  # 복선·연결은 카드 전달이 아니다

    # 복선·연결을 마친 뒤의 지원자 메시지는 이끌지 않는다(턴 LLM 실패 → 기본 응답 자리)
    again = await h.run("message", message_id=h.candidate("그렇군요.", intent_of("other")))
    assert again.decision["llm_used"] is True
    assert card_block(h.snapshots[-1]["text"]) == []

    done = await h.run("card_due", card_id=card)
    assert done.decision["speakers"][0]["card_ids"] == [card]
    assert card_block(h.snapshots[-1]["text"])[0] == "- 이번 발언이 내 카드 차례: 예"
    assert h.server.cards[card]["delivered"] == ["p3"]


async def test_steering_skips_cards_without_steer_data_and_quiet(harness):
    """이끎 데이터가 없는 카드(머뭇거림 고정 한 줄)는 이끌지 않고 소극형은 이끎 담당으로 고르지
    않는다. 해결책 단계는 반론 카드 담당(비판형)만 복선을 받는다."""
    h = harness("solution")
    h.server.confirm_keyword("water")
    await h.run("phase_started")
    texts = {s["seat"]: s["text"] for s in h.snapshots}
    assert "복선 차례" in card_block(texts["p3"])[0] and card_block(texts["p1"]) == []
    assert "p2" not in texts


async def test_ai_fill_is_held_until_the_phase_cards_are_delivered(harness):
    """합의가 있어도 그 단계 카드가 전달되기 전에는 채우지 않고 화자를 정한다. 전달 뒤 run에서
    지원자의 최신 발언이 그 안의 동의면 채운다(C3)."""
    h = harness(
        "keyword",
        persona=[say("저는 바람이 더 낫다고 봐요.", {"option_id": "wind", "stance": "support"})],
    )
    water = h.server.add_option("water", "p1")
    for who in ("candidate", "p1", "p2"):
        h.server.set_support(who, water)
    h.server.confirmed = water
    mid = h.candidate("물로 가요.", intent_of("agree", option_id=water))

    held = await h.run("message", message_id=mid)
    assert held.decision["fill"] is None and held.decision["fill_held"] == ["persuasion_keyword"]
    assert [s["seat"] for s in held.decision["speakers"]] == ["p3"]
    assert "fill_slot" not in h.tool_names() and "keyword" not in h.server.slots

    # 카드 전달(담당 비판형의 카드 발언) 뒤 지원자가 다시 동의하면 채운다
    await h.run("card_due", card_id="persuasion_keyword")
    assert h.server.cards["persuasion_keyword"]["delivered"] == ["p3"]
    again = h.candidate("그래도 물로 정해요.", intent_of("agree", option_id=water))
    filled = await h.run("message", message_id=again)
    assert filled.decision["fill"] == {"option_id": water, "seat": "p2"}
    assert filled.decision["fill_held"] == [] and h.server.slots["keyword"]["option_id"] == water


async def test_partially_delivered_joint_card_still_holds_fill(harness):
    """공동 담당 카드는 전원이 전달할 때까지 보류한다(이전 api처럼 `pending_cards`가 없으면 그래프가
    같은 규칙으로 계산)."""
    h = problem(harness)
    p2 = h.server.add_option("water_p2", "p1")
    for who in ("candidate", "p1", "p3"):
        h.server.set_support(who, p2)
    h.server.confirmed = p2
    h.server.deliver(FLEX, "p1")
    h.server.deliver(PRESSURE, "p1")
    original = h.server.debate_state
    h.server.debate_state = lambda: {k: v for k, v in original().items() if k != "pending_cards"}
    mid = h.candidate("침수로 가요.", intent_of("agree", option_id=p2))
    run = await h.run("message", message_id=mid)
    assert run.decision["fill"] is None and run.decision["fill_held"] == [FLEX]
    h.server.debate_state = original
    h.server.deliver(FLEX, "p3")
    run = await h.run("message", message_id=h.candidate("네.", intent_of("agree", option_id=p2)))
    assert run.decision["fill"] == {"option_id": p2, "seat": "p3"}


async def test_steering_counts_full_phase_posts_not_the_recent_window(harness):
    """이끎 차례는 대화 창(최근 30개)이 아니라 그 단계 전체 발언 수(DebateState
    `phase_message_counts`)로 정한다. 복선 발언이 창 밖으로 밀려도 연결을 건너뛰지 않고, 연결 뒤에는
    다시 복선으로 돌아가지 않는다."""
    h = harness("keyword", turn=[RuntimeError("불려서는 안 된다")])
    await h.run("phase_started")  # p1·p3 발언(p3 복선)
    for i in range(35):
        h.candidate(f"음 {i}.", intent_of("other", confidence="low"))
    assert all(m["sender"] == "candidate" for m in h.server.messages[-30:])
    mid = h.candidate("어떤 게 좋을까요.", intent_of("other", confidence="low"))
    run = await h.run("message", message_id=mid)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p3"]
    assert "연결 차례" in card_block(h.snapshots[-1]["text"])[0]
    # 복선·연결을 마친 뒤(전체 발언 2회)에는 창에 자기 발언이 없어도 이끌지 않는다
    for i in range(35):
        h.candidate(f"또 {i}.", intent_of("other", confidence="low"))
    h.turn.script = ['{"speakers": [{"seat": "p1"}]}']
    run = await h.run(
        "message", message_id=h.candidate("그렇군요.", intent_of("other", confidence="low"))
    )
    assert run.decision["llm_used"] is True and card_block(h.snapshots[-1]["text"]) == []


async def test_steering_stops_at_the_card_time_even_if_delivery_is_pending(harness):
    """허용 구간은 카드 예정 시각(단계 시작 + offset_min, 스케줄러와 같은 기준)까지다. 시각이 지나
    카드가 아직 전달되지 않았어도(재시도 중) 복선·연결로 고르지 않는다."""
    h = harness("keyword", turn=['{"speakers": [{"seat": "p1"}]}'])
    offset = h.server.catalog.card("persuasion_keyword")["offset_min"]
    h.server.elapsed = int(offset * 60)  # 예정 시각 도달
    mid = h.candidate("음.", intent_of("other", confidence="low"))
    run = await h.run("message", message_id=mid)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p1"]
    assert run.decision["llm_used"] is True and card_block(h.snapshots[-1]["text"]) == []
    # 이끎 구간 값이 없는(이전 api) DebateState에서도 이끌지 않는다
    original = h.server.debate_state
    h.server.debate_state = lambda: {
        k: v for k, v in original().items() if k != "phase_elapsed_seconds"
    }
    h.server.elapsed = 0
    h.turn.script = ['{"speakers": [{"seat": "p1"}]}']
    run = await h.run("message", message_id=h.candidate("네.", intent_of("other")))
    assert run.decision["llm_used"] is True and card_block(h.snapshots[-1]["text"]) == []
    h.server.debate_state = original


async def test_steering_follows_the_current_assignee_with_its_own_stage(harness):
    """이끎 대상은 그 순간의 카드 담당이다. 지원자 지지로 담당이 바뀌면 새 담당이 자기 발언 수의
    차례부터 이끈다(카드마다 차례를 저장하지 않음). 비판형이 복선을 한 뒤 지원자가 비판형 키워드
    (바람)를 지지하면 담당이 고집형으로 바뀌고, 고집형은 단계 시작 발언 1회가 있어 연결 차례다."""
    h = harness("keyword", turn=[RuntimeError("불려서는 안 된다")])
    await h.run("phase_started")
    h.candidate("바람이 좋겠어요.", intent_of("propose", key="wind"))
    mid = h.candidate("어떻게들 보세요.", intent_of("other", confidence="low"))
    run = await h.run("message", message_id=mid)
    assert [s["seat"] for s in run.decision["speakers"]] == ["p1"]
    lines = card_block(h.snapshots[-1]["text"])
    assert "연결 차례" in lines[0] and steer_of(h, "persuasion_keyword", "bridge") in lines[1]
    done = await h.run("card_due", card_id="persuasion_keyword")
    assert done.decision["speakers"][0]["seat"] == "p1"
