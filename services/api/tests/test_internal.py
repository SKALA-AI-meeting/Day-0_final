"""내부 REST(8001): 계약 예시 형태, 멱등, 평가 저장 검증, 사실 지표, 기록 ref."""

import uuid
from datetime import timedelta

from app.models import EvaluationRun, Message, Slot
from kit import bearer, finish, high, to_phase
from sqlalchemy import func, select, update


async def test_session_messages_tail_and_rubric_window(api, classifier):
    sess = await api.new_session()
    classifier.rules["물이요"] = high("propose", "water")
    for text in ("하나", "둘", "물이요"):
        await api.say(sess, text)
    session = (await api.internal.get(f"/internal/sessions/{sess.id}")).json()
    assert session["position"]["name"] == "서비스 기획" and session["generation"] == 4
    assert [s["seat"] for s in session["seats"]] == ["candidate", "p1", "p2", "p3"]

    tail = (
        await api.internal.get(
            f"/internal/sessions/{sess.id}/messages", params={"tail": 2, "limit": 1, "offset": 5}
        )
    ).json()
    assert [m["content"] for m in tail] == ["둘", "물이요"]
    assert tail[1]["intent"]["intent"] == "propose" and tail[1]["origin"] == "spontaneous"

    rubric = (await api.internal.get(f"/internal/sessions/{sess.id}/rubric")).json()
    assert sum(len(e["dimensions"]) for e in rubric["elements"]) == 19
    flex = next(c for c in rubric["cards"] if c["card_id"] == "flexibility_majority")
    assert (
        flex["window"]["start"] == "card_message" and flex["window"]["end"] == "stage_end:solution"
    )


async def test_options_idempotent_and_candidate_first_proposer_kept(api, ctx, classifier):
    sess = await api.new_session()
    classifier.rules["바람 어때요"] = high("propose", "wind")
    await api.say(sess, "바람 어때요")
    message = await api.post_ai(sess, "p3", "바람이 좋아요")
    body = {
        "phase": "keyword",
        "label": "바람",
        "proposer": "p3",
        "scenario_option_key": "wind",
        "source_message_id": message["id"],
        "run_id": str(uuid.uuid4()),
    }
    r = (await api.internal.post(f"/internal/sessions/{sess.id}/options", json=body)).json()
    assert r["existing"] is True and r["proposer"] == "candidate"
    contributions = (await api.internal.get(f"/internal/sessions/{sess.id}/contributions")).json()
    assert contributions["options"][0]["first_proposer"] == "candidate"
    wrong = await api.internal.post(
        f"/internal/sessions/{sess.id}/options", json={**body, "phase": "problem"}
    )
    assert wrong.json()["error"]["code"] == "phase_not_open"


async def test_observation_idempotent_and_writes_need_in_progress(api):
    sess = await api.new_session()
    message = await api.post_ai(sess, "p3", "바람이 낫다고 봐요", card_ids=["persuasion_keyword"])
    body = {
        "card_id": "persuasion_keyword",
        "phase": "keyword",
        "trigger_message_id": message["id"],
        "run_id": str(uuid.uuid4()),
    }
    first = (
        await api.internal.post(f"/internal/sessions/{sess.id}/observations", json=body)
    ).json()
    second = (
        await api.internal.post(f"/internal/sessions/{sess.id}/observations", json=body)
    ).json()
    assert first == second
    listed = (await api.internal.get(f"/internal/sessions/{sess.id}/observations")).json()
    assert listed[0]["element"] == "persuasion" and listed[0]["delivered"] is True
    await finish(api, sess)
    late = await api.internal.post(
        f"/internal/sessions/{sess.id}/messages",
        json={"sender": "p1", "content": "늦음", "run_id": str(uuid.uuid4()), "seq": 1},
    )
    assert late.status_code == 409


async def test_save_evaluation_validates_and_is_idempotent(api, ctx):
    sess = await api.new_session()
    other = await api.new_session()
    await api.say(sess, "물이 좋아요")
    await api.say(other, "다른 세션")
    mine = (await api.internal.get(f"/internal/sessions/{sess.id}/messages")).json()
    theirs = (await api.internal.get(f"/internal/sessions/{other.id}/messages")).json()
    msg = next(m for m in mine if m["sender"] == "candidate")["id"]
    foreign = next(m for m in theirs if m["sender"] == "candidate")["id"]
    rubric = (await api.internal.get(f"/internal/sessions/{sess.id}/rubric")).json()
    early = await api.internal.post(f"/internal/sessions/{sess.id}/evaluations", json={})
    assert early.status_code in (409, 422)
    await finish(api, sess)

    dims = [
        {
            "dimension_id": d["dimension_id"],
            "level": 3,
            "evidence_refs": [f"msg:{msg}"],
            "rationale": "근거",
            "confidence": "medium",
        }
        for e in rubric["elements"]
        for d in e["dimensions"]
    ]
    dims[-1] = {
        "dimension_id": dims[-1]["dimension_id"],
        "level": None,
        "no_record_reason": "no_activity",
        "evidence_refs": [],
        "rationale": "없음",
        "confidence": None,
    }
    body = {
        "run_id": str(uuid.uuid4()),
        "rubric_version": rubric["rubric_version"],
        "prompt_versions": {"common": "h"},
        "model": "fake",
        "dimensions": dims,
        "elements": [
            {
                "element": e["element"],
                "level": 3,
                "summary": "요약",
                "dimension_count": len(e["dimensions"]),
                "no_record_count": 0,
            }
            for e in rubric["elements"]
        ],
    }
    url = f"/internal/sessions/{sess.id}/evaluations"
    for broken in (
        {**body, "dimensions": dims[:-1]},
        {**body, "dimensions": [{**dims[0], "evidence_refs": [f"msg:{foreign}"]}, *dims[1:]]},
        {**body, "dimensions": [{**dims[0], "no_record_reason": "no_activity"}, *dims[1:]]},
    ):
        r = await api.internal.post(url, json=broken)
        assert (r.status_code, r.json()["error"]["code"]) == (422, "invalid_input")
    saved = (await api.internal.post(url, json=body)).json()
    assert saved == {"run_id": body["run_id"], "existing": False, "saved": 19}
    again = (await api.internal.post(url, json=body)).json()
    assert again["existing"] is True
    got = (await api.internal.get(url)).json()
    assert len(got["elements"]) == 5 and got["has_failed"] is False
    async with ctx.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(EvaluationRun)) == 1


async def test_facts_and_record_refs(api, ctx, classifier):
    sess = await api.new_session()
    classifier.rules["물이 좋겠어요"] = high("propose", "water")
    await api.say(sess, "물이 좋겠어요")
    await api.post_ai(sess, "p3", "김지원님은 어떻게 생각하세요?", is_invite=True)
    await api.say(sess, "강한결님 생각도 궁금해요")
    await to_phase(api, sess, "problem")
    await api.ai_support(sess, "p3", "problem", "water_p2", "도심 폭우·침수 피해 증가")
    await api.confirm(sess, "problem", value=" 도심 폭우·침수 피해 증가. ")  # 정규화 라벨 일치
    await to_phase(api, sess, "proposal")
    proposal = (await api.public.get(f"/sessions/{sess.id}/proposal", headers=sess.auth)).json()
    sections = {**proposal["sections"], "idea_credit": "문제 상황: 서지안 제안"}
    await api.public.put(
        f"/sessions/{sess.id}/proposal",
        headers=sess.auth,
        json={"sections": sections, "revision": proposal["revision"]},
    )
    await api.public.post(f"/sessions/{sess.id}/proposal/submit", headers=sess.auth, json={})

    facts = (await api.internal.get(f"/internal/sessions/{sess.id}/facts")).json()
    assert facts["message_count"]["total"] == 2 and facts["message_count"]["keyword"] == 2
    assert (facts["spontaneous_count"], facts["invited_count"]) == (1, 1)
    assert facts["silence_invites_received"]["keyword"] == 1
    assert facts["intent_counts"]["propose"] == 1 and facts["name_mentions"]["p1"] == 1
    assert facts["own_options_proposed"] == 1 and facts["own_options_adopted"] == 1
    assert facts["ai_adopted_units"] == [
        {"phase": "problem", "option_id": facts["slot_records"][1]["option_id"], "proposer": "p3"}
    ]
    assert (
        facts["credit_field_filled"] is True and facts["first_message_sec"]["keyword"] is not None
    )
    # 평가자 사실(결정 A1): 칸 결정 경로와 지원자 메시지별 답한 대상·먼저 정해진 칸
    assert [d["phase"] for d in facts["decisions"]] == [s["phase"] for s in facts["slot_records"]]
    assert {d["path"] for d in facts["decisions"]} == {"candidate_confirm"}
    problem = next(d for d in facts["decisions"] if d["phase"] == "problem")
    assert problem["filled_by"] == "candidate" and problem["timeout_rule"] is None
    # 카드 예정 시각 전에 지원자가 확정해 생기지 않은 카드(선결정, 결정 A3)
    assert problem["preempted_cards"] == ["flexibility_majority", "listening_pressure"]
    assert problem["candidate_option_adopted"] is False  # 지원자 확정이지만 서지안 안
    # 예정 시각은 스케줄러가 읽는 단계 시작(전환 시각 = 앞 칸 확정 시각)으로 정한다. 단계 시작 안내
    # 메시지의 저장 시각이 달라도 결과는 같고, 전환 시각이 10분 앞당겨지면 문제 상황 칸 확정은 예정
    # 시각 뒤라 선결정이 아니다
    async with ctx.session_factory() as db:
        await db.execute(
            update(Message)
            .where(Message.session_id == sess.id, Message.meta["kind"].astext == "stage_start")
            .values(created_at=Message.created_at - timedelta(minutes=10))
        )
        await db.commit()

    async def problem_preempted():
        got = (await api.internal.get(f"/internal/sessions/{sess.id}/facts")).json()
        return next(d for d in got["decisions"] if d["phase"] == "problem")["preempted_cards"]

    assert await problem_preempted() == ["flexibility_majority", "listening_pressure"]
    async with ctx.session_factory() as db:
        await db.execute(
            update(Slot)
            .where(Slot.session_id == sess.id, Slot.phase == "keyword")
            .values(confirmed_at=Slot.confirmed_at - timedelta(minutes=10))
        )
        await db.commit()
    assert await problem_preempted() == []
    first, second = facts["message_facts"]
    assert first["reply_to"] is None and first["decided_before"] == []
    assert second["reply_to"] is not None  # 직전 AI 동료(서지안) 초대 메시지
    assert all(f["paste_suspect"] is None for f in facts["message_facts"])

    report_refs = {("slot", "problem"), ("proposal_submit", str(sess.id))}
    record = (
        await api.public.get(f"/admin/sessions/{sess.id}/record", headers=bearer(sess.admin))
    ).json()
    refs = {(i["ref"]["type"], i["ref"]["id"]) for i in record}
    assert report_refs <= refs and all(set(i["ref"]) == {"type", "id", "t_min"} for i in record)
