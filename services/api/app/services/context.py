"""공개·내부 앱이 한 프로세스에서 함께 쓰는 객체(hub, 메일박스, runner, 큐, DB, 시계).

run.py가 build_context()로 한 번 만들고 두 앱의 app.state.ctx에 넣는다. 테스트는 같은 생성자에
가짜 시계·가짜 runner·메모리 publisher를 넣는다.
"""

import asyncio
import importlib
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from app import queue
from app.agent import intent as intent_module
from app.agent.runner import (
    AgentRunner,
    AssistantRunner,
    FakeAgentRunner,
    FakeAssistantRunner,
)
from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory
from app.hub import Hub
from app.mailbox import Mailboxes
from app.models import Session
from app.services.clock import Clock, MonotonicClock, SystemClock
from app.services.common import Effects

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    clock: Clock
    publisher: queue.Publisher
    runner: AgentRunner | None = None
    assistant: AssistantRunner = field(default_factory=FakeAssistantRunner)
    classify: Callable[..., Awaitable[tuple[dict, bool]]] | None = None
    hub: Hub = None  # type: ignore[assignment]
    mailboxes: Mailboxes = None  # type: ignore[assignment]
    generations: dict[uuid.UUID, int] = field(default_factory=dict)
    spawned: set[asyncio.Task] = field(default_factory=set)
    record_frames: bool = False
    engine: AsyncEngine | None = None
    speech: Any = None
    order_locks: dict[uuid.UUID, asyncio.Lock] = field(
        default_factory=lambda: defaultdict(asyncio.Lock)
    )

    def __post_init__(self) -> None:
        if self.speech is None:
            from app.voice.speech import provider

            self.speech = provider()
        if self.hub is None:
            self.hub = Hub(self.clock.now, record=self.record_frames)
        if self.hub.on_drop is None:
            self.hub.on_drop = self.mark_disconnected
        if self.mailboxes is None:
            self.mailboxes = Mailboxes(self)
        if self.classify is None:

            async def classify(message: str, **kwargs: Any):
                return await intent_module.classify(None, message, **kwargs)

            self.classify = classify
        if self.runner is None:
            self.runner = FakeAgentRunner(self.post_message, self.mailboxes.ports)

    def spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self.spawned.add(task)
        task.add_done_callback(self.spawned.discard)

    async def drain_spawned(self) -> None:
        while self.spawned:
            await asyncio.gather(*list(self.spawned), return_exceptions=True)

    def ordered(self, session_id: uuid.UUID) -> asyncio.Lock:
        """세션별 순서 잠금. '행 잠금 → 커밋 → emit(프레임에 seq 부여·송신 큐에 넣기)'을 감싸 커밋
        순서 = WS 프레임 순서를 보장한다(게시 커밋 뒤 전송 전에 확정이 끼어들어 이전 단계 발언이
        확정 프레임 뒤에 가는 것을 막음). 실제 소켓 전송(hub 송신 작업)과 브로커 발행(publish)은
        잠금 밖이라 느린 연결·브로커가 다른 쓰기를 막지 않는다. 잠금 안에서 runner 실행이나 같은
        세션의 다른 ordered 구간을 기다리지 않는다."""
        return self.order_locks[session_id]

    def set_generation(self, session_id: uuid.UUID, generation: int) -> None:
        """generation 캐시는 늘기만 한다(늦게 읽은 옛 값으로 되돌아가지 않음)."""
        self.generations[session_id] = max(self.generations.get(session_id, generation), generation)

    def emit(self, session_id: uuid.UUID, fx: Effects) -> None:
        """커밋 직후(순서 잠금 안): generation 캐시 → 프레임 enqueue → 자극 enqueue(대기 없음)."""
        if fx.generation is not None:
            self.set_generation(session_id, fx.generation)
        self.hub.enqueue_many(session_id, fx.events)
        for stimulus, run_id in fx.stimuli:
            self.mailboxes.put(session_id, stimulus, run_id)

    async def publish(self, session_id: uuid.UUID, fx: Effects) -> None:
        """순서 잠금 밖: 리포트 큐 발행(브로커 확인 뒤 *_enqueued_at 기록)."""
        for routing_key in fx.publish:
            async with self.session_factory() as db:
                session = await db.get(Session, session_id)
                await queue.publish_report(
                    db, self.publisher, session, routing_key, self.clock.now()
                )

    async def apply(self, session_id: uuid.UUID, fx: Effects) -> None:
        """순서 잠금이 필요 없는 경로의 커밋 뒤 효과: emit → publish."""
        self.emit(session_id, fx)
        await self.publish(session_id, fx)

    async def mark_disconnected(self, session_id: uuid.UUID) -> None:
        """현재 연결이 끊긴 시각을 connection_gaps에 남긴다(진행 중 세션만). ws 라우터 종료와
        hub가 먼저 끊은 경우(전송 실패·타임아웃)가 같은 경로를 쓴다."""
        from app.models import ConnectionGap
        from app.services.common import load_session

        async with self.session_factory() as db:
            session = await load_session(db, session_id)
            if session.status == "in_progress":
                db.add(ConnectionGap(session_id=session_id, disconnected_at=self.clock.now()))
                await db.commit()

    async def post_message(self, *, session_id: uuid.UUID, **body: Any) -> dict[str, Any]:
        """내부 POST /messages와 같은 저장(개발용 FakeAgentRunner의 post_message)."""
        from app.services import debate
        from app.services.common import load_session, serialize_message

        fx = Effects()
        async with self.ordered(session_id):
            async with self.session_factory() as db:
                session = await load_session(db, session_id, lock=True)
                message = await debate.post_internal_message(db, session, body, self.clock, fx)
                await db.commit()
                payload = serialize_message(session, message)
            self.emit(session_id, fx)
        await self.publish(session_id, fx)
        return payload


def get_context(conn: HTTPConnection) -> AppContext:
    """FastAPI 의존성. HTTP 요청과 WebSocket 모두에서 쓴다."""
    return conn.app.state.ctx


def _load_factory(path: str) -> Callable[[AppContext], Any]:
    module, _, name = path.partition(":")
    return getattr(importlib.import_module(module), name)


def build_context(settings: Settings | None = None) -> AppContext:
    """AGENT_RUNNER=session(기본)이면 agent 트랙의 토론 그래프·어시스턴트·의도 분류 LLM을 붙인다.

    fake는 FakeAgentRunner(단위 테스트 주입용), `모듈:함수`는 그 함수(ctx)가 AgentRunner를 만든다.
    LLM_FAKE=1이어도 runner는 그대로이고 LLM 래퍼(make_llm)가 고정 응답을 돌려준다.
    """
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    ctx = AppContext(
        settings=settings,
        session_factory=factory,
        clock=MonotonicClock(SystemClock()),
        publisher=queue.RabbitPublisher(settings.rabbitmq_url),
        engine=engine,
    )
    if settings.agent_runner == "session":
        # agent 트랙 소유 모듈(app/agent/factory.py)이라 실제 실행 때만 가져온다
        from app.agent.factory import build_assistant_runner, build_debate_runner, db_scenario
        from app.agent.llm import make_llm

        scenario = db_scenario(factory)
        ctx.runner = build_debate_runner(settings, scenario, ctx.mailboxes.ports)
        ctx.assistant = build_assistant_runner(settings, scenario)
        intent_llm = make_llm("intent")

        async def classify(message: str, **kwargs: Any):
            return await intent_module.classify(intent_llm, message, **kwargs)

        ctx.classify = classify
    elif settings.agent_runner not in ("", "fake"):
        ctx.runner = _load_factory(settings.agent_runner)(ctx)
    return ctx
