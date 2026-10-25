"""SessionService 전이·멱등, 재발급·재응시, 관리자 격리, 자료 잠금·어시스턴트 한도, 비용 상한."""

import uuid
from decimal import Decimal

from app.agent.runner import AssistantResult
from app.models import EvaluationRun, Material, Message, Proposal, Session, Slot
from app.queue import GENERATE
from app.services import session as session_service
from app.services.common import Effects
from kit import bearer, finish, to_phase, types
from sqlalchemy import select


async def test_start_creates_slots_draft_notice_and_phase_started(api, ctx, runner):
    sess = await api.new_session()
    again = await api.public.post(f"/s/{sess.token}/start", headers=sess.auth)
    assert again.status_code == 200  # 멱등
    async with ctx.session_factory() as db:
        slots = (await db.scalars(select(Slot).where(Slot.session_id == sess.id))).all()
        proposal = await db.get(Proposal, sess.id)
        notice = await db.scalar(
            select(Message).where(Message.session_id == sess.id, Message.sender == "system")
        )
        # 첫 단계 안내 앞에 동료 셋의 시작 인사가 있다(test_greeting.py)
        greeted = (
            await db.scalars(
                select(Message.sender).where(
                    Message.session_id == sess.id, Message.meta["kind"].astext == "greeting"
                )
            )
        ).all()
    assert sorted((s.phase, s.status) for s in slots) == [
        ("keyword", "empty"),
        ("problem", "empty"),
        ("solution", "empty"),
    ]
    assert proposal.revision == 0 and set(proposal.sections) >= {"idea_credit", "keyword"}
    assert notice.sender == "system" and "물·불·나무·바람" in notice.content
    assert sorted(greeted) == ["p1", "p2", "p3"]
    assert runner.types() == ["phase_started"]


async def test_cancel_and_fail_record_ended_at(api, ctx, clock):
    sess = await api.new_session()
    mark = api.mark(sess)
    r = await api.public.post(f"/admin/sessions/{sess.id}/cancel", headers=bearer(sess.admin))
    assert r.json() == {"status": "cancelled"}
    assert types(api.frames_since(sess, mark)) == ["session.ended"]
    assert (
        await api.public.post(f"/admin/sessions/{sess.id}/cancel", headers=bearer(sess.admin))
    ).status_code == 409
    other = await api.new_session()
    async with ctx.session_factory() as db:
        for sid in (sess.id, other.id):
            session = await db.get(Session, sid)
            if sid == other.id:
                session_service.fail(
                    session, "session", "persona llm failed", clock.now(), Effects()
                )
            assert session.ended_at is not None
        await db.commit()
        assert (await db.get(Session, other.id)).failed_stage == "session"


async def test_report_failure_regenerates_with_new_round_when_evaluation_failed(api, ctx):
    sess = await api.new_session()
    await finish(api, sess)
    r = await api.internal.post(
        f"/internal/sessions/{sess.id}/report_failed", json={"error": "dlq"}
    )
    assert r.json() == {"status": "failed"}
    async with ctx.session_factory() as db:
        db.add(
            EvaluationRun(
                session_id=sess.id,
                run_id=uuid.uuid4(),
                rubric_version="v",
                prompt_versions={},
                model="fake",
                elements=[],
                has_failed=True,
            )
        )
        await db.commit()
    r = await api.public.post(
        f"/admin/sessions/{sess.id}/report/regenerate", headers=bearer(sess.admin)
    )
    assert r.json() == {"status": "completed"}
    assert ctx.publisher.published[-1] == (
        GENERATE,
        {"session_id": str(sess.id), "attempt": 1, "eval_round": 2},
    )


async def test_reissue_swaps_token_and_retake_reuses_alias(api):
    admin = await api.admin()
    position_id = await api.position(admin)
    first = await api.invite(admin, position_id, alias="지원자B")
    old = first["invite_url"].rsplit("/", 1)[1]
    r = await api.public.post(
        f"/admin/sessions/{first['session_id']}/reissue", headers=bearer(admin)
    )
    assert r.status_code == 200 and r.json()["session_id"] == first["session_id"]
    new = r.json()["invite_url"].rsplit("/", 1)[1]
    assert (await api.public.get(f"/s/{old}")).status_code == 404
    assert (await api.public.get(f"/s/{new}")).json()["session_id"] == first["session_id"]

    url = f"/admin/positions/{position_id}/invites"
    dup = await api.public.post(
        url, json={"alias": "지원자B", "email": "b@x.com"}, headers=bearer(admin)
    )
    assert dup.status_code == 409
    live = await api.public.post(
        url,
        json={"email": "b@x.com", "previous_session_id": first["session_id"]},
        headers=bearer(admin),
    )
    assert (live.status_code, live.json()["error"]["code"]) == (422, "invalid_previous_session")
    await api.public.post(f"/admin/sessions/{first['session_id']}/cancel", headers=bearer(admin))
    retake = await api.public.post(
        url,
        json={"email": "b@x.com", "previous_session_id": first["session_id"]},
        headers=bearer(admin),
    )
    assert retake.status_code == 200 and retake.json()["session_id"] != first["session_id"]
    rows = (
        await api.public.get(f"/admin/positions/{position_id}/candidates", headers=bearer(admin))
    ).json()
    assert [row["alias"] for row in rows] == ["지원자B", "지원자B"]
    assert rows[1]["previous_session_id"] == first["session_id"]


async def test_admin_queries_are_company_scoped_and_catalog_has_no_selection(api):
    sess = await api.new_session()
    stranger = await api.admin()
    for path in (
        f"/admin/sessions/{sess.id}/record",
        f"/admin/positions/{sess.position_id}/candidates",
        f"/admin/sessions/{sess.id}/report",
    ):
        assert (await api.public.get(path, headers=bearer(stranger))).status_code == 404
    catalog = (await api.public.get("/admin/catalog", headers=bearer(stranger))).json()
    assert len(catalog["elements"]) == 5 and catalog["session_minutes_options"] == [60, 15]
    for body in ({"name": "x", "elements": ["listening"]}, {"name": "x", "session_minutes": 30}):
        r = await api.public.post("/admin/positions", json=body, headers=bearer(stranger))
        assert (r.status_code, r.json()["error"]["code"]) == (422, "invalid_input")
    assert (
        await api.internal.get(f"/internal/sessions/{sess.id}", headers={"X-Internal-Token": ""})
    ).status_code == 401
    assert (await api.public.get(f"/internal/sessions/{sess.id}")).status_code == 404


async def test_materials_locked_until_keyword_and_seeded_by_catalog_version(api, ctx):
    sess = await api.new_session()
    await api.new_session()  # 같은 catalog_version이면 다시 시드하지 않음
    locked = (await api.public.get(f"/sessions/{sess.id}/materials", headers=sess.auth)).json()
    assert locked == {"locked": True, "keyword": None, "materials": []}
    r = await api.public.get(f"/sessions/{sess.id}/materials/water_stats", headers=sess.auth)
    assert r.status_code == 423 and r.json()["error"]["code"] == "not_released"

    await to_phase(api, sess, "problem")
    listed = (await api.public.get(f"/sessions/{sess.id}/materials", headers=sess.auth)).json()
    assert listed["keyword"] == "물" and listed["materials"]
    material_id = listed["materials"][0]["material_id"]
    body = await api.public.get(f"/sessions/{sess.id}/materials/{material_id}", headers=sess.auth)
    assert body.status_code == 200 and body.json()["content"]
    async with ctx.session_factory() as db:
        versions = (await db.scalars(select(Material.catalog_version).distinct())).all()
        fire = await db.scalar(select(Material.material_id).where(Material.keyword_key == "fire"))
    assert len(versions) == 1
    other = await api.public.get(f"/sessions/{sess.id}/materials/{fire}", headers=sess.auth)
    assert other.status_code == 423
    record = (
        await api.public.get(f"/admin/sessions/{sess.id}/record", headers=bearer(sess.admin))
    ).json()
    assert any(i["type"] == "material_view" for i in record)


async def test_assistant_limit_and_cost_go_to_debate_budget(api, ctx):
    sess = await api.new_session()

    class Paid:
        async def answer(self, session_id, prompt, call_id):
            return AssistantResult(content="답", usage={"cost_usd": 0.05})

    ctx.assistant = Paid()
    for i in range(60):
        r = await api.public.post(
            f"/sessions/{sess.id}/assistant", json={"prompt": f"질문 {i}"}, headers=sess.auth
        )
        assert r.status_code == 200, r.text
    over = await api.public.post(
        f"/sessions/{sess.id}/assistant", json={"prompt": "한 번 더"}, headers=sess.auth
    )
    assert (over.status_code, over.json()["error"]["code"]) == (429, "assistant_limit")

    state = (await api.internal.get(f"/internal/sessions/{sess.id}/debate_state")).json()
    assert state["cost_left"] <= 0  # 60 × $0.05 = $3 ≥ 하드 상한 $2
    calls = (await api.internal.get(f"/internal/sessions/{sess.id}/assistant_calls")).json()
    assert (calls[0]["request"], calls[0]["response"]) == ("질문 0", "답")

    await finish(api, sess)
    await api.internal.post(
        f"/internal/sessions/{sess.id}/report/draft",
        json={"content": {"elements": [], "dimensions": []}, "usage": {"cost_usd": 0.3}},
    )
    async with ctx.session_factory() as db:
        session = await db.get(Session, sess.id)
    assert session.debate_cost_usd == Decimal("3.00") and session.cost_usd == Decimal("3.30")


async def test_purge_deletes_ended_sessions_after_90_days(api, ctx, clock):

    from app.models import Invite
    from app.scheduler import tick

    done = await api.new_session()
    await api.say(done, "물이 좋겠어요")
    await finish(api, done)
    cancelled = await api.new_session(start=False)
    await api.public.post(f"/admin/sessions/{cancelled.id}/cancel", headers=bearer(cancelled.admin))
    live = await api.new_session(minutes=60)
    clock.advance(days=90, seconds=1)
    await tick(ctx)
    async with ctx.session_factory() as db:
        assert await db.get(Session, done.id) is None
        assert await db.get(Session, cancelled.id) is None
        assert (
            await db.get(Session, live.id) is not None
        )  # in_progress는 대상이 아님(시간 초과로 completed 될 뿐)
        invites = (await db.scalars(select(Invite).where(Invite.alias == "(삭제됨)"))).all()
    assert len(invites) == 2
