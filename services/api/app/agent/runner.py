"""AgentRunner 경계(docs/architecture/agents.md "AgentRunner 인터페이스와 SessionPorts"). G-F 동결.

api 메일박스는 이 Protocol만 알고, agent 트랙의 LangGraph 토론 그래프가 같은 시그니처로 갈아 끼운다.
자극은 4종(docs/architecture/session-lifecycle.md "그래프 자극")이고 `timeout`·`room_quiet`는 없다.
같은 run_id
재실행은
메시지 (session_id, run_id, seq) 등 멱등 키로 중복이 생기지 않아야 한다.
FakeAgentRunner는 LLM 없이 자리별 고정 문장을 post_message 하는 개발·테스트용 구현이다.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.ports import SessionPorts

StimulusType = Literal["message", "phase_started", "idle", "turn_elapsed", "card_due"]
Phase = Literal["keyword", "problem", "solution", "proposal"]
Seat = Literal["p1", "p2", "p3"]
# 메일박스 run 상태: queued → started → finished | deferred | skipped | failed (agent_runs.status)
RunStatus = Literal["finished", "deferred", "skipped", "failed"]


@dataclass(frozen=True)
class Stimulus:
    """docs/architecture/session-lifecycle.md `Stimulus = {type, phase, generation, message_id?,
    card_id?}`.

    generation: message 자극은 그 메시지 저장 트랜잭션이 올린 값(enqueue 때 다시 읽지 않음),
    그 밖의 자극은 enqueue 시점의 sessions.generation. message 자극의 phase는 messages.phase.
    run_id는 자극에 넣지 않고 api 메일박스가 발급해 run()에 따로 준다.
    """

    type: StimulusType
    phase: Phase
    generation: int
    message_id: uuid.UUID | None = None
    card_id: str | None = None

    @property
    def ref_id(self) -> str | None:
        """agent_runs.ref_id에 남기는 값(message면 message_id, card_due면 card_id)."""
        if self.message_id is not None:
            return str(self.message_id)
        return self.card_id


@dataclass
class RunResult:
    """docs/architecture/agents.md RunResult + 메일박스 상태(agent-spike 7절).

    decision은 진행 매니저 결정 스키마(docs/contracts/internal.md), usage는 agent_runs.usage
    {llm_calls, retries, tokens_in, tokens_out, cost_usd, latency_ms, tool_calls}.
    """

    ok: bool = True
    status: RunStatus = "finished"
    fallback_used: bool = False
    error: str | None = None
    decision: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class AgentRunner(Protocol):
    async def run(
        self, session_id: uuid.UUID, stimulus: Stimulus, run_id: uuid.UUID
    ) -> RunResult: ...


# debate-mcp post_message와 같은 키워드 인자(session_id, sender, content, run_id, seq,
# expected_generation?, is_invite?, card_ids?, cited_material_ids?, meta?). 운영은 MCP 경유,
# 테스트·개발은 내부 저장을 직접 부른다.
PostMessage = Callable[..., Awaitable[Any]]
PortsFor = Callable[[uuid.UUID], SessionPorts]

# 노드별 고정 seq 슬롯(docs/architecture/agents.md): 화자1 발언=1, 화자2=2, 합의 채우기 공지=5
SPEAKER_SEQ = 1
FAKE_LINES: dict[StimulusType, tuple[Seat, str]] = {
    "message": ("p1", "[fake] 네, 그 의견 잘 들었어요."),
    "phase_started": ("p1", "[fake] 이번 단계도 같이 정해 봐요."),
    "idle": ("p3", "[fake] 지원자님 생각은 어떠세요?"),
    "turn_elapsed": ("p3", "[fake] 이 부분은 제가 이어서 생각해 볼게요."),
}


class FakeAgentRunner:
    """message·phase_started·turn_elapsed → 자리 고정 문장 1건, idle → 비판형 자리 초대 1건.

    card_due는 게시하지 않는다.
    """

    def __init__(self, post_message: PostMessage, ports_for: PortsFor | None = None) -> None:
        self._post = post_message
        self._ports_for = ports_for
        self.calls: list[tuple[uuid.UUID, Stimulus, uuid.UUID]] = []

    async def run(self, session_id: uuid.UUID, stimulus: Stimulus, run_id: uuid.UUID) -> RunResult:
        self.calls.append((session_id, stimulus, run_id))
        line = FAKE_LINES.get(stimulus.type)
        decision: dict[str, Any] = {
            "stimulus": stimulus.type,
            "generation": stimulus.generation,
            "speakers": [],
            "fill": None,
            "invite": stimulus.type == "idle",
            "llm_used": False,
            "queued": [],
            "dropped_seats": [],
            "reason": "fake",
        }
        if line is None:
            return RunResult(decision=decision, usage={"llm_calls": 0})
        seat, content = line
        decision["speakers"] = [
            {
                "seat": seat,
                "reply_to": str(stimulus.message_id) if stimulus.message_id else None,
                "card_ids": [],
                "invite": stimulus.type == "idle",
            }
        ]
        ports = self._ports_for(session_id) if self._ports_for else None
        if ports is not None:
            ports.send_typing(seat, "start")
        try:
            await self._post(
                session_id=session_id,
                sender=seat,
                content=content,
                run_id=run_id,
                seq=SPEAKER_SEQ,
                expected_generation=stimulus.generation,
                is_invite=stimulus.type == "idle",
                meta={
                    "model": "fake",
                    "reply_to": str(stimulus.message_id) if stimulus.message_id else None,
                },
            )
        finally:
            if ports is not None:
                ports.send_typing(seat, "stop")
        return RunResult(decision=decision, usage={"llm_calls": 0})


@dataclass
class AssistantResult:
    """어시스턴트 응답(docs/contracts/mcp.md assistant_response의
    content·cited_material_ids·tool_calls)."""

    content: str
    cited_material_ids: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


class AssistantRunner(Protocol):
    """POST /sessions/{id}/assistant의 훅. 메일박스 밖에서 실행한다.

    api가 요청·응답 메시지와 assistant_calls를 기록하고 60회 한도를 막으므로 구현은 답만 만든다.
    """

    async def answer(
        self, session_id: uuid.UUID, prompt: str, call_id: uuid.UUID
    ) -> AssistantResult: ...


FAKE_ASSISTANT = "[fake] 어시스턴트 응답입니다. 열린 자료를 참고해 답합니다."


class FakeAssistantRunner:
    async def answer(
        self, session_id: uuid.UUID, prompt: str, call_id: uuid.UUID
    ) -> AssistantResult:
        return AssistantResult(content=FAKE_ASSISTANT)
