"""토론 그래프(진행 매니저 + 토론 동료 3)와 AgentRunner 구현.

진행 매니저 규칙은 `app/agent/manager`, 토론 동료 respond는 `app/agent/persona`.
"""

from app.agent.debate.graph import DebateDeps, DebateLLMs, build_graph
from app.agent.debate.runner import DebateGraphRunner, mcp_tools, thread_id

__all__ = ["DebateDeps", "DebateGraphRunner", "DebateLLMs", "build_graph", "mcp_tools", "thread_id"]
