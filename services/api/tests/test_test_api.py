"""테스트 API(ENABLE_TEST_API): 단계 시드 재생(SessionService 경로)과 시간 당기기.

단계 시드 yaml은 stage-kit 소유라 여기서는 같은 문법의 인라인 픽스처를 임시 scenarios 폴더에 쓴다.
"""

import uuid
from dataclasses import replace
from datetime import datetime

import pytest
from app.config import get_settings
from app.scheduler import tick
from kit import Sess

STEPS = {
    "keyword": [],
    "problem": [
        {
            "at": 0.3,
            "say": "물이 좋겠어요. 생활과 가까워요",
            "intent": {"intent": "propose", "scenario_option_key": "water", "confidence": "high"},
        },
        {
            "at": "+0.1",
            "ai": {"seat": "p3", "content": "저는 바람이 더 낫다고 봐요", "propose": "wind"},
        },
        {"at": 1.0, "confirm": {"phase": "keyword", "option_key": "water"}},
    ],
}
STEPS["solution"] = STEPS["problem"] + [
    {"at": 2.0, "say": "가뭄이 제일 커요"},
    {"at": 3.0, "confirm": {"phase": "problem", "value": "가뭄으로 생활용수 제한이 잦아짐"}},
]
STEPS["proposal"] = STEPS["solution"] + [
    {"at": 5.0, "confirm": {"phase": "solution", "value": "센서·데이터 기반 물 관리·경보"}},
]
STEPS["ended"] = STEPS["proposal"] + [
    {"at": 6.0, "proposal": {"idea_credit": "키워드: 제 제안"}},
    {"at": "+0.2", "submit": True},
]


@pytest.fixture
def seeded(ctx, tmp_path):
    import yaml

    real = get_settings().scenarios_dir
    import shutil

    try:
        (tmp_path / "missions").symlink_to(real / "missions", target_is_directory=True)
        (tmp_path / "version.py").symlink_to(real / "version.py")
    except OSError:
        shutil.copytree(real / "missions", tmp_path / "missions", dirs_exist_ok=True)
        shutil.copy2(real / "version.py", tmp_path / "version.py")
    stages = tmp_path / "e2e" / "stages"
    stages.mkdir(parents=True)
    for name, steps in STEPS.items():
        (stages / f"{name}.yaml").write_text(
            yaml.safe_dump({"steps": steps}, allow_unicode=True), encoding="utf-8"
        )
    ctx.settings = replace(ctx.settings, scenarios_dir=tmp_path)
    return ctx


@pytest.mark.parametrize("start_at", ["keyword", "problem", "solution", "proposal"])
async def test_seed_replays_to_phase_start(api, seeded, runner, start_at):
    r = await api.internal.post(
        "/internal/test/sessions", json={"session_minutes": 15, "start_at": start_at}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["phase"] == start_at and body["session_token"]
    await seeded.mailboxes.drain()
    sess = Sess(uuid.UUID(body["session_id"]), body["token"], body["session_token"], "", "")
    state = await api.state(sess)
    assert state["session"]["status"] == "in_progress" and state["session"]["phase"] == start_at
    order = ["keyword", "problem", "solution", "proposal"]
    confirmed = [s["phase"] for s in state["slots"] if s["status"] == "confirmed"]
    assert confirmed == order[: order.index(start_at)]
    assert all(s["filled_by"] == "candidate" for s in state["slots"] if s["status"] == "confirmed")
    started = datetime.fromisoformat(state["session"]["phase_started_at"])
    assert abs((started - seeded.clock.now()).total_seconds()) < 1  # 단계가 막 시작됨
    frames = api.frames(sess)
    assert [f["seq"] for f in frames] == list(range(1, len(frames) + 1)) and state["ws_seq"] == len(
        frames
    )
    session = (await api.internal.get(f"/internal/sessions/{body['session_id']}")).json()
    says = sum(1 for s in STEPS[start_at] if "say" in s)
    assert session["generation"] == says + order.index(start_at) + 1
    assert runner.types()[-1:] == ["phase_started"]  # 마지막 단계 phase_started만 그래프로


async def test_ended_seed_submits_and_publishes(api, seeded):
    r = await api.internal.post("/internal/test/sessions", json={"start_at": "ended"})
    body = r.json()
    assert r.status_code == 200 and body["phase"] == "completed"
    assert body["debrief_token"] and body["admin_report_url"].endswith(
        f"/admin/sessions/{body['session_id']}/report"
    )
    assert seeded.publisher.published[0][1]["session_id"] == body["session_id"]


async def test_advance_to_deadline_and_card(api, seeded, runner):
    body = (await api.internal.post("/internal/test/sessions", json={"start_at": "problem"})).json()
    sid = body["session_id"]
    await seeded.mailboxes.drain()
    r = await api.internal.post(
        f"/internal/test/sessions/{sid}/advance", json={"to": "card:listening_pressure"}
    )
    assert r.status_code == 200 and r.json()["shifted_seconds"] > 0
    await tick(seeded)
    await seeded.mailboxes.drain()
    assert "card_due" in runner.types()

    r = await api.internal.post(f"/internal/test/sessions/{sid}/advance", json={"to": "deadline"})
    assert r.json()["phase"] == "problem"
    await tick(seeded)
    slots = (await api.internal.get(f"/internal/sessions/{sid}/slots")).json()
    assert slots[1]["resolution"] == "timeout"
    both = await api.internal.post(
        f"/internal/test/sessions/{sid}/advance", json={"to": "deadline", "seconds": 1}
    )
    assert both.status_code == 422


async def test_test_api_is_404_when_disabled(api, ctx):
    ctx.settings = replace(ctx.settings, enable_test_api=False)
    r = await api.internal.post("/internal/test/sessions", json={"session_minutes": 7})
    assert r.status_code == 404  # 본문 검증(422)보다 먼저
