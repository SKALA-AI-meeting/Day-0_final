"""api가 실 에이전트를 조립하는 입구(AGENT_RUNNER가 session일 때 services/context.py가 부른다).

- `db_scenario(factory)`: 세션의 채용 건에 고정된 positions.scenario를 읽는다.
- `build_debate_runner(...)`: 역할별 LLM(make_llm, ROLE_CONFIG의 temperature 포함)·MCP
  3개(assessment는
  별도 클라이언트·토큰)·SessionPorts로 DebateGraphRunner를 만든다.
- `build_assistant_runner(...)`: rag-mcp만 쓰는 어시스턴트.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.assistant import AssistantGraphRunner, mcp_rag
from app.agent.debate import DebateGraphRunner, DebateLLMs, mcp_tools
from app.agent.debate.runner import ScenarioLoader
from app.agent.llm import make_llm
from app.agent.runner import PortsFor
from app.config import Settings

# AGENT_RUNNER 기본값. config.py와 runner 선택 코드가 같은 값을 쓴다(fake는 api 단위 테스트 주입용)
DEFAULT_AGENT_RUNNER = "session"
RUNNER_KINDS = ("session", "fake")


def runner_kind(value: str | None) -> str:
    """AGENT_RUNNER 값 → session | fake. 비어 있으면 기본값, 모르는 값은 설정 오류."""
    kind = (value or "").strip() or DEFAULT_AGENT_RUNNER
    if kind not in RUNNER_KINDS:
        raise ValueError(f"unknown AGENT_RUNNER: {kind}")
    return kind


def db_scenario(factory: async_sessionmaker[AsyncSession]) -> ScenarioLoader:
    from app.models import Invite, Position, Session

    async def load(session_id: uuid.UUID) -> dict[str, Any]:
        async with factory() as db:
            scenario = await db.scalar(
                select(Position.scenario)
                .join(Invite, Invite.position_id == Position.id)
                .join(Session, Session.invite_id == Invite.id)
                .where(Session.id == session_id)
            )
        if scenario is None:
            raise LookupError(f"scenario not found for session {session_id}")
        return scenario

    return load


def build_debate_runner(
    settings: Settings,
    scenario: ScenarioLoader,
    ports_for: PortsFor,
    checkpointer: Any = None,
) -> DebateGraphRunner:
    llms = DebateLLMs(persona=make_llm("persona"), turn=make_llm("turn"), intent=make_llm("intent"))
    return DebateGraphRunner(
        llms=llms,
        tools=mcp_tools(
            debate_url=settings.debate_mcp_url,
            rag_url=settings.rag_mcp_url,
            assessment_url=settings.assessment_mcp_url,
            assessment_token=settings.assessment_mcp_token,
        ),
        scenario=scenario,
        ports_for=ports_for,
        checkpointer=checkpointer,
    )


def build_assistant_runner(settings: Settings, scenario: ScenarioLoader) -> AssistantGraphRunner:
    return AssistantGraphRunner(
        llm=make_llm("assistant"),
        rag=mcp_rag(settings.rag_mcp_url),
        scenario=scenario,
    )
