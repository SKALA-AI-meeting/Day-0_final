"""api 테스트 도구. 가짜 시계(FakeClock)·메모리 큐·기록 runner·고정 의도 분류로 AppContext를 띄우고
공개(8000)·내부(8001) 앱을 httpx ASGI 클라이언트로 부른다. LLM·MCP·RabbitMQ는 쓰지 않는다.
WS 수신은 routers/ws.handle_send를 직접 부르고, 보낸 프레임은 hub 기록(record_frames)으로 본다.
"""

import uuid
from dataclasses import dataclass, replace
from typing import Any

import httpx
from app.agent import intent as intent_module
from app.agent.runner import RunResult, Stimulus
from app.config import get_settings
from app.db import make_session_factory
from app.main import create_internal_app, create_public_app
from app.queue import MemoryPublisher
from app.routers.ws import handle_send
from app.services.clock import MonotonicClock
from app.services.context import AppContext


class RecordingRunner:
    """AgentRunner 대역. 실행을 기록하고, hook이 있으면 hook(ctx, session_id, stimulus, run_id)을
    부른다."""

    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, Stimulus, uuid.UUID]] = []
        self.hook = None
        self.ctx: AppContext | None = None

    async def run(self, session_id: uuid.UUID, stimulus: Stimulus, run_id: uuid.UUID) -> RunResult:
        self.calls.append((session_id, stimulus, run_id))
        if self.hook is not None:
            result = await self.hook(self.ctx, session_id, stimulus, run_id)
            if result is not None:
                return result
        return RunResult(decision={"stimulus": stimulus.type, "speakers": []})

    def types(self) -> list[str]:
        return [s.type for _, s, _ in self.calls]


class FakeClassifier:
    """의도 분류 대역. 메시지 본문 → 결과(부분 dict). 없으면 계약 폴백. 호출을 기록한다."""

    def __init__(self) -> None:
        self.rules: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, message: str, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        self.calls.append({"message": message, **kwargs})
        rule = self.rules.get(message)
        if rule is None:
            return intent_module.fallback(), True
        return {**intent_module.fallback(), **rule}, False


def make_ctx(engine, clock, runner: RecordingRunner, classifier: FakeClassifier) -> AppContext:
    settings = replace(
        get_settings(), enable_test_api=True, agent_runner="fake", voice_enabled=False
    )
    ctx = AppContext(
        settings=settings,
        session_factory=make_session_factory(engine),
        clock=MonotonicClock(clock),
        publisher=MemoryPublisher(),
        runner=runner,
        classify=classifier,
        record_frames=True,
    )
    runner.ctx = ctx
    return ctx


@dataclass
class Sess:
    id: uuid.UUID
    token: str
    jwt: str
    position_id: str
    admin: str

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.jwt}"}


class Api:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.public = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_public_app(ctx)), base_url="http://api"
        )
        self.internal = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_internal_app(ctx)),
            base_url="http://api-internal",
            headers={"X-Internal-Token": ctx.settings.internal_token},
        )

    async def __aenter__(self) -> "Api":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.public.aclose()
        await self.internal.aclose()

    # ---- 관리자 ----------------------------------------------------------------------------

    async def admin(self, email: str | None = None) -> str:
        email = email or f"admin-{uuid.uuid4().hex[:6]}@example.com"
        r = await self.public.post(
            "/admin/auth/signup",
            json={"email": email, "password": "pw-123456", "company_name": "테스트 회사"},
        )
        assert r.status_code == 200, r.text
        return r.json()["token"]

    async def position(self, admin: str, minutes: int = 60) -> str:
        r = await self.public.post(
            "/admin/positions",
            json={"name": "서비스 기획", "session_minutes": minutes},
            headers=bearer(admin),
        )
        assert r.status_code == 200, r.text
        return r.json()["id"]

    async def invite(self, admin: str, position_id: str, alias: str = "지원자A") -> dict:
        r = await self.public.post(
            f"/admin/positions/{position_id}/invites",
            json={"alias": alias, "email": "a@example.com"},
            headers=bearer(admin),
        )
        assert r.status_code == 200, r.text
        return r.json()

    # ---- 지원자 ----------------------------------------------------------------------------

    async def new_session(
        self, minutes: int = 60, start: bool = True, display_name: str | None = "김지원"
    ) -> Sess:
        admin = await self.admin()
        position_id = await self.position(admin, minutes)
        invite = await self.invite(admin, position_id)
        token = invite["invite_url"].rsplit("/", 1)[1]
        r = await self.public.post(f"/s/{token}/consent", json={"display_name": display_name})
        assert r.status_code == 200, r.text
        sess = Sess(
            uuid.UUID(r.json()["session_id"]), token, r.json()["session_token"], position_id, admin
        )
        if start:
            r = await self.public.post(f"/s/{token}/start", headers=sess.auth)
            assert r.status_code == 200, r.text
            await self.ctx.mailboxes.drain()
        return sess

    async def state(self, sess: Sess) -> dict:
        r = await self.public.get(f"/sessions/{sess.id}/state", headers=sess.auth)
        assert r.status_code == 200, r.text
        return r.json()

    async def confirm(self, sess: Sess, phase: str, **body: Any) -> httpx.Response:
        r = await self.public.post(
            f"/sessions/{sess.id}/slots/{phase}/confirm", json=body, headers=sess.auth
        )
        await self.ctx.mailboxes.drain()
        return r

    async def say(self, sess: Sess, content: str, client_msg_id: str | None = None) -> None:
        """WS message.send 한 건. 메일박스가 끝날 때까지 기다린다."""
        await handle_send(
            self.ctx,
            sess.id,
            {"client_msg_id": client_msg_id or uuid.uuid4().hex, "content": content},
        )
        await self.ctx.mailboxes.drain()

    def frames(self, sess: Sess) -> list[dict[str, Any]]:
        return list(self.ctx.hub.sent[sess.id])

    def mark(self, sess: Sess) -> int:
        """지금까지 보낸 프레임 수. frames_since와 함께 한 동작이 보낸 프레임만 본다."""
        return len(self.ctx.hub.sent[sess.id])

    def frames_since(self, sess: Sess, mark: int) -> list[dict[str, Any]]:
        return self.ctx.hub.sent[sess.id][mark:]

    # ---- 내부(debate-mcp 대역) --------------------------------------------------------------

    async def post_ai(self, sess: Sess, seat: str, content: str, **extra: Any) -> dict:
        body = {"sender": seat, "content": content, "run_id": str(uuid.uuid4()), "seq": 1, **extra}
        r = await self.internal.post(f"/internal/sessions/{sess.id}/messages", json=body)
        assert r.status_code == 200, r.text
        return r.json()

    async def ai_support(self, sess: Sess, seat: str, phase: str, key: str, label: str) -> str:
        """AI 자리가 시나리오 안을 말하고(propose_option) 지지(set_stance)한다. option_id를
        돌려준다."""
        message = await self.post_ai(sess, seat, f"{label}가 좋겠어요")
        r = await self.internal.post(
            f"/internal/sessions/{sess.id}/options",
            json={
                "phase": phase,
                "label": label,
                "proposer": seat,
                "scenario_option_key": key,
                "source_message_id": message["id"],
                "run_id": str(uuid.uuid4()),
            },
        )
        assert r.status_code == 200, r.text
        option_id = r.json()["option_id"]
        r = await self.internal.post(
            f"/internal/sessions/{sess.id}/stances",
            json={
                "participant": seat,
                "option_id": option_id,
                "stance": "support",
                "source_message_id": message["id"],
                "run_id": str(uuid.uuid4()),
            },
        )
        assert r.status_code == 200, r.text
        return option_id


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def types(frames: list[dict[str, Any]]) -> list[str]:
    return [f["type"] for f in frames]


def high(intent: str, key: str | None = None, **extra: Any) -> dict[str, Any]:
    """의도 분류 고정 결과(confidence high)."""
    return {"intent": intent, "scenario_option_key": key, "confidence": "high", **extra}


async def to_phase(api: Api, sess: Sess, phase: str) -> None:
    """지원자 확정으로 그 단계까지 간다(키워드 water, 문제 water_p1, 해결책 water_s1). 지난 단계는
    건너뛴다."""
    order = [
        ("keyword", {"value": "물", "option_key": "water"}),
        ("problem", {"value": "가뭄으로 생활용수 제한이 잦아짐"}),
        ("solution", {"value": "센서·데이터 기반 물 관리·경보"}),
    ]
    current_phase = (await api.state(sess))["session"]["phase"]
    names = [name for name, _ in order]
    for current, body in order:
        if current == phase:
            return
        if current_phase in names and names.index(current) < names.index(current_phase):
            continue
        r = await api.confirm(sess, current, **body)
        assert r.status_code == 200, r.text


async def finish(api: Api, sess: Sess) -> None:
    """proposal 단계까지 확정하고 제출한다."""
    await to_phase(api, sess, "proposal")
    r = await api.public.post(f"/sessions/{sess.id}/proposal/submit", headers=sess.auth, json={})
    assert r.status_code == 200, r.text


async def draft_content(api: Api, sess: Sess, evidence: list[str]) -> dict[str, Any]:
    """평가표 19차원 전부 수준 3(근거 evidence), 요소 5행의 draft content(워커 출력 대역)."""
    rubric = (await api.internal.get(f"/internal/sessions/{sess.id}/rubric")).json()
    dims, elements = [], []
    for e in rubric["elements"]:
        for d in e["dimensions"]:
            dims.append(
                {
                    "element": e["element"],
                    "dimension_id": d["dimension_id"],
                    "level": 3,
                    "no_record_reason": None,
                    "rationale": "근거 있음",
                    "confidence": "medium",
                    "evidence_refs": evidence,
                }
            )
        elements.append(
            {
                "element": e["element"],
                "name": e["name"],
                "level": 3,
                "summary": "요약",
                "dimension_count": len(e["dimensions"]),
                "no_record_count": 0,
            }
        )
    return {
        "session_id": str(sess.id),
        "stage": "draft",
        "evaluation": {
            "run_id": str(uuid.uuid4()),
            "rubric_version": rubric["rubric_version"],
            "prompt_versions": {"common": "h"},
            "model": "fake",
        },
        "elements": elements,
        "dimensions": dims,
        "facts": {},
        "cards": [],
    }
