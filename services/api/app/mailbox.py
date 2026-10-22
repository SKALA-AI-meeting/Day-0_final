"""세션별 메일박스(R5-1): asyncio Queue + 소비 Task 1개가 그래프 자극을 들어온 순서대로 직렬
처리한다.

처리 순서(docs/architecture/session-lifecycle.md "세션 메일박스"):
1. agent_runs가 이미 finished·skipped·deferred면 건너뛴다(같은 run_id 재실행 방지).
2. started로 표시.
3. message 자극이면 낡았는지와 상관없이 의도 분류·안·입장 반영(저장된 messages.phase 기준, 1회).
4. card_due면 session_cards 행 생성·기준안 잠금.
5. 낡았으면(세션 종료·단계 다름·message인데 generation이 현재보다 작음) skipped로 닫는다.
6. 채용 건 시나리오 복사본(catalog·prompts)이 없으면 실행하지 않고 즉시 failed(session).
7. 아니면 AgentRunner.run → 결과 상태(finished·deferred·failed) 기록, 페르소나 LLM 연속 실패 3회면
   failed(session).

세션 Lock·handled 집합은 없다. 멱등은 agent_runs.status와 DB 멱등 키로만 한다.
메일박스는 메모리이므로 api는 단일 프로세스로 운영하고, 재시작하면 recover()가 queued·started를 다시
넣는다.
"""

import asyncio
import logging
import uuid
from collections import Counter
from collections.abc import Hashable
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from app.agent.catalog import copy_missing
from app.agent.runner import RunResult, Stimulus
from app.models import AgentRun, Message, Session
from app.services import budget, debate
from app.services import session as session_service
from app.services.common import Effects, load_context

if TYPE_CHECKING:
    from app.services.context import AppContext

log = logging.getLogger(__name__)
DONE = ("finished", "skipped", "deferred")
PERSONA_LLM_FAILURE = "persona_llm_unavailable"
MAX_LLM_FAILURES = 3
SCENARIO_COPY_MISSING = "scenario_copy_missing"


def pending_key(session_id: uuid.UUID, stimulus: Stimulus) -> Hashable | None:
    """스케줄러 중복 방지 키: 같은 단계 idle, 같은 card_id card_due."""
    if stimulus.type == "idle":
        return (session_id, "idle", stimulus.phase)
    if stimulus.type == "card_due":
        return (session_id, "card_due", stimulus.card_id)
    return None


class SessionMailboxPorts:
    """SessionPorts 구현(app/ports.py). 그래프가 api 내부 상태에 닿는 유일한 길."""

    def __init__(self, ctx: "AppContext", session_id: uuid.UUID) -> None:
        self.ctx = ctx
        self.session_id = session_id

    def send_typing(self, seat: str, state: str) -> None:
        self.ctx.spawn(self.ctx.hub.persona_typing(self.session_id, seat, state))

    def get_generation(self) -> int:
        return self.ctx.generations.get(self.session_id, 0)

    def candidate_typing(self) -> bool:
        return self.ctx.hub.is_candidate_typing(self.session_id)

    def enqueue(self, stimulus: Stimulus) -> None:
        self.ctx.spawn(self.ctx.mailboxes.enqueue(self.session_id, stimulus))


class Mailboxes:
    def __init__(self, ctx: "AppContext") -> None:
        self.ctx = ctx
        self._queues: dict[uuid.UUID, asyncio.Queue] = {}
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        self._pending: Counter = Counter()
        self._ports: dict[uuid.UUID, SessionMailboxPorts] = {}

    def ports(self, session_id: uuid.UUID) -> SessionMailboxPorts:
        if session_id not in self._ports:
            self._ports[session_id] = SessionMailboxPorts(self.ctx, session_id)
        return self._ports[session_id]

    def has_pending(self, key: Hashable) -> bool:
        return self._pending[key] > 0

    async def enqueue(self, session_id: uuid.UUID, stimulus: Stimulus) -> uuid.UUID:
        """새 run_id로 agent_runs queued 행을 만든 뒤 큐에 넣는다(card_due·idle·deferred 재큐)."""
        from app.services.phases import queue_run

        run_id = uuid.uuid4()
        async with self.ctx.session_factory() as db:
            session = await db.get(Session, session_id)
            await queue_run(db, session, stimulus, run_id, self.ctx.clock.now())
            await db.commit()
        self.put(session_id, stimulus, run_id)
        return run_id

    def put(self, session_id: uuid.UUID, stimulus: Stimulus, run_id: uuid.UUID) -> None:
        """agent_runs 행이 이미 queued로 저장된 자극을 큐에 넣는다."""
        key = pending_key(session_id, stimulus)
        if key is not None:
            self._pending[key] += 1
        queue = self._queues.get(session_id)
        if queue is None:
            queue = self._queues[session_id] = asyncio.Queue()
            self._tasks[session_id] = asyncio.create_task(self._consume(session_id, queue))
        queue.put_nowait((stimulus, run_id))

    async def drain(self) -> None:
        """테스트용: 모든 큐가 빌 때까지(처리 중 새로 들어온 자극 포함) 기다린다."""
        for _ in range(1000):
            await self.ctx.drain_spawned()
            busy = [q for q in self._queues.values() if q._unfinished_tasks]
            if not busy and not self.ctx.spawned:
                return
            await asyncio.gather(*(q.join() for q in busy))

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._queues.clear()

    async def _consume(self, session_id: uuid.UUID, queue: asyncio.Queue) -> None:
        while True:
            stimulus, run_id = await queue.get()
            try:
                from app.voice.controller import blocked

                while await blocked(self.ctx, session_id):
                    await asyncio.sleep(0.25)
                await self.process(session_id, stimulus, run_id)
            except Exception:
                log.exception("mailbox run %s failed", run_id)
                await self._finish(run_id, RunResult(ok=False, status="failed", error="crash"))
            finally:
                key = pending_key(session_id, stimulus)
                if key is not None:
                    self._pending[key] -= 1
                queue.task_done()

    async def process(self, session_id: uuid.UUID, stimulus: Stimulus, run_id: uuid.UUID) -> str:
        ctx = self.ctx
        async with ctx.session_factory() as db:
            run = await db.get(AgentRun, run_id)
            if run is None or run.status in DONE:
                return run.status if run else "missing"
            run.status = "started"
            run.started_at = ctx.clock.now()
            await db.commit()
            session, position, _ = await load_context(db, session_id)
            if stimulus.type == "message" and stimulus.message_id is not None:
                message = await db.get(Message, stimulus.message_id)
                await debate.classify_and_apply(
                    db, session, position, message, ctx.classify, ctx.clock
                )
                await db.commit()
            ctx.set_generation(session_id, session.generation)
            stale = (
                session.status != "in_progress"
                or session.phase != stimulus.phase
                or (stimulus.type == "message" and stimulus.generation < session.generation)
            )
            if not stale and stimulus.type == "card_due" and stimulus.card_id:
                session = await db.get(Session, session_id, with_for_update=True)
                await debate.lock_lead_option(db, session, position, stimulus.card_id, ctx.clock)
                await db.commit()
            missing = copy_missing(position.scenario)
            position_id = position.id
        if stale:
            await self._finish(run_id, RunResult(status="skipped"))
            return "skipped"
        if missing:
            # 현재 파일로 채우지 않는다. 진행할 수 없으니 시간 초과·자동 제출을 기다리지 않고 끝낸다
            log.error("position %s scenario copy lacks %s; session failed", position_id, missing)
            await self._finish(
                run_id, RunResult(ok=False, status="failed", error=SCENARIO_COPY_MISSING)
            )
            await self._fail_session(session_id, f"{SCENARIO_COPY_MISSING}: {', '.join(missing)}")
            return "failed"
        try:
            result = await ctx.runner.run(session_id, stimulus, run_id)
        except Exception as exc:
            log.exception("runner failed for %s", run_id)
            result = RunResult(ok=False, status="failed", error=type(exc).__name__)
        await self._finish(run_id, result)
        await self._count_llm_failure(session_id, result)
        return result.status

    async def _count_llm_failure(self, session_id: uuid.UUID, result: RunResult) -> None:
        """페르소나 LLM 실패(error `persona_llm_unavailable:<seat>`)가 연속 3회면 failed(session).

        정상 run이면 연속 횟수를 0으로 되돌린다. 그 밖의 실패는 세지 않는다.
        """
        failed = (result.error or "").startswith(PERSONA_LLM_FAILURE)
        if not failed and not result.ok:
            return
        fx = Effects()
        async with self.ctx.ordered(session_id):
            async with self.ctx.session_factory() as db:
                session = await db.get(Session, session_id, with_for_update=True)
                if session is None or session.status != "in_progress":
                    return
                session.llm_failures = session.llm_failures + 1 if failed else 0
                if session.llm_failures >= MAX_LLM_FAILURES:
                    session_service.fail(
                        session, "session", "persona llm failed 3 times", self.ctx.clock.now(), fx
                    )
                await db.commit()
            self.ctx.emit(session_id, fx)
        await self.ctx.publish(session_id, fx)

    async def _fail_session(self, session_id: uuid.UUID, reason: str) -> None:
        fx = Effects()
        async with self.ctx.ordered(session_id):
            async with self.ctx.session_factory() as db:
                session = await db.get(Session, session_id, with_for_update=True)
                if session is None:
                    return
                session_service.fail(session, "session", reason, self.ctx.clock.now(), fx)
                await db.commit()
            self.ctx.emit(session_id, fx)
        await self.ctx.publish(session_id, fx)

    async def _finish(self, run_id: uuid.UUID, result: RunResult) -> None:
        values: dict[str, Any] = {
            "status": result.status,
            "finished_at": self.ctx.clock.now(),
            "usage": result.usage or {},
        }
        if result.decision is not None:
            values["decision"] = result.decision
        async with self.ctx.session_factory() as db:
            session_id = await db.scalar(
                update(AgentRun)
                .where(AgentRun.run_id == run_id)
                .values(**values)
                .returning(AgentRun.session_id)
            )
            if session_id is not None:
                await budget.add_debate_cost(db, session_id, result.usage)
            await db.commit()

    async def recover(self, stale_before=None) -> int:
        """재시작: in_progress 세션의 queued·started run을 같은 run_id로 다시 넣는다.

        stale_before를 주면(실행 중 점검) started_at이 그보다 이른 started만 failed로 닫고 다시
        넣는다.
        """
        async with self.ctx.session_factory() as db:
            query = (
                select(AgentRun, Session)
                .join(Session, Session.id == AgentRun.session_id)
                .where(Session.status == "in_progress", AgentRun.graph == "debate")
            )
            if stale_before is None:
                query = query.where(AgentRun.status.in_(("queued", "started")))
            else:
                query = query.where(
                    AgentRun.status == "started", AgentRun.started_at < stale_before
                )
            rows = (await db.execute(query.order_by(AgentRun.queued_at))).all()
            items = []
            for run, _session in rows:
                stimulus = await self._rebuild(db, run)
                if stimulus is None:
                    continue
                run.status = "queued"
                items.append((run.session_id, stimulus, run.run_id))
            await db.commit()
        for session_id, stimulus, run_id in items:
            self.put(session_id, stimulus, run_id)
        return len(items)

    async def _rebuild(self, db, run: AgentRun) -> Stimulus | None:
        generation = run.generation or 0
        if run.stimulus == "message":
            message = await db.get(Message, uuid.UUID(run.ref_id))
            return Stimulus("message", message.phase, generation, message_id=message.id)
        if run.stimulus == "card_due":
            from app.services import scenario as sc

            _, position, _ = await load_context(db, run.session_id)
            phase = sc.card(position.scenario, run.ref_id)["stage_id"]
            return Stimulus("card_due", phase, generation, card_id=run.ref_id)
        if run.stimulus in ("phase_started", "idle") and run.ref_id:
            return Stimulus(run.stimulus, run.ref_id, generation)
        return None
