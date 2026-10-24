"""세션별 WebSocket 연결, 단일 송신 큐(seq 부여), typing 상태. 한 프로세스(api) 안에서만 유효하다.

프레임 봉투: {"type", "seq", "payload"} (docs/contracts/ws.md "WebSocket").
모든 서버 프레임은 enqueue()에서
기다림 없이 seq를 1씩 받고 세션별 FIFO 송신 큐에 들어가며, 실제 소켓 전송은 세션마다 하나인 송신
작업이 순서대로 한다(느린 연결이 호출자·다른 세션을 막지 않음). 연결이 없어도 seq는 오르고 프레임은
버려진다(재연결한 클라이언트는 /state의 ws_seq부터 이어 간다). 다른 경로에서 소켓에 직접
쓰지 않는다.
예외: 연결 교체 때 옛 소켓에 보내는 session.replaced(seq 0)는 큐를 거치지 않는다.
typing: 지원자는 5초마다 start를 갱신하고 6초 동안 갱신이 없으면 만료(R5-9). 페르소나 typing은
SessionPorts.send_typing이 여기로 보낸다.
"""

import asyncio
import contextlib
import logging
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Protocol

log = logging.getLogger(__name__)

REPLACED_CLOSE_CODE = 4000
STALLED_CLOSE_CODE = 1011  # 전송 실패·타임아웃으로 서버가 끊음(클라이언트는 재연결)
CANDIDATE_TYPING_TTL = timedelta(seconds=6)
SEND_TIMEOUT_SECONDS = 10.0  # 프레임 하나를 이 안에 못 보내면 멈춘 연결로 보고 끊는다


class SocketLike(Protocol):
    async def send_json(self, data: Any) -> None: ...
    async def close(self, code: int = 1000) -> None: ...


class Hub:
    def __init__(self, now: Callable[[], datetime], record: bool = False) -> None:
        self._now = now
        self._sockets: dict[uuid.UUID, SocketLike] = {}
        self._outbox: dict[uuid.UUID, deque[tuple[SocketLike, dict[str, Any]]]] = defaultdict(deque)
        self._writers: dict[uuid.UUID, asyncio.Task] = {}
        self.send_timeout = SEND_TIMEOUT_SECONDS
        # hub가 전송 실패·타임아웃으로 먼저 끊은 연결의 끊김 기록(AppContext: connection_gaps)
        self.on_drop: Callable[[uuid.UUID], Awaitable[None]] | None = None
        self._seq: dict[uuid.UUID, int] = defaultdict(int)
        self._candidate_typing_until: dict[uuid.UUID, datetime] = {}
        self._persona_typing: dict[uuid.UUID, set[str]] = defaultdict(set)
        # 테스트용: 세션별 보낸 프레임 전부
        self.record = record
        self.sent: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)

    # ---- 연결 -------------------------------------------------------------------------------

    async def connect(self, session_id: uuid.UUID, ws: SocketLike) -> None:
        """새 연결은 새 송신 작업으로 시작한다. 옛 연결의 송신 작업·대기 프레임은 버린다(재연결
        클라이언트는 /state의 ws_seq부터 잇는다)."""
        previous = self._sockets.get(session_id)
        self._drop_writer(session_id)
        self._sockets[session_id] = ws
        if previous is not None and previous is not ws:
            try:
                replaced = {"type": "session.replaced", "seq": 0, "payload": {}}
                await asyncio.wait_for(previous.send_json(replaced), self.send_timeout)
                await asyncio.wait_for(previous.close(code=REPLACED_CLOSE_CODE), self.send_timeout)
            except Exception:
                log.debug("previous socket already closed", exc_info=True)

    def disconnect(self, session_id: uuid.UUID, ws: SocketLike) -> bool:
        if self._sockets.get(session_id) is ws:
            del self._sockets[session_id]
            self._drop_writer(session_id)
            return True
        return False

    def _drop_writer(self, session_id: uuid.UUID) -> None:
        """그 세션의 송신 작업을 멈추고 대기 프레임(옛 소켓 참조)을 지운다."""
        self._outbox.pop(session_id, None)
        writer = self._writers.pop(session_id, None)
        if writer is not None and writer is not asyncio.current_task():
            writer.cancel()

    def is_connected(self, session_id: uuid.UUID) -> bool:
        return session_id in self._sockets

    def is_current(self, session_id: uuid.UUID, ws: SocketLike) -> bool:
        return self._sockets.get(session_id) is ws

    # ---- 송신 -------------------------------------------------------------------------------

    def last_seq(self, session_id: uuid.UUID) -> int:
        return self._seq[session_id]

    def enqueue(self, session_id: uuid.UUID, type_: str, payload: dict[str, Any]) -> int:
        """seq를 붙여 송신 큐에 넣는다(기다리지 않음). 호출 순서 = seq 순서 = 전송 순서."""
        self._seq[session_id] += 1
        frame = {"type": type_, "seq": self._seq[session_id], "payload": payload}
        if self.record:
            self.sent[session_id].append(frame)
        ws = self._sockets.get(session_id)
        if ws is not None:
            self._outbox[session_id].append((ws, frame))
            writer = self._writers.get(session_id)
            if writer is None or writer.done():
                self._writers[session_id] = asyncio.ensure_future(self._flush(session_id))
        return frame["seq"]

    async def send(self, session_id: uuid.UUID, type_: str, payload: dict[str, Any]) -> int:
        return self.enqueue(session_id, type_, payload)

    async def send_many(self, session_id: uuid.UUID, events: list[tuple[str, dict]]) -> None:
        self.enqueue_many(session_id, events)

    def enqueue_many(self, session_id: uuid.UUID, events: list[tuple[str, dict]]) -> None:
        for type_, payload in events:
            self.enqueue(session_id, type_, payload)

    async def _flush(self, session_id: uuid.UUID) -> None:
        outbox = self._outbox[session_id]
        while outbox:
            ws, frame = outbox.popleft()
            if self._sockets.get(session_id) is not ws:
                continue  # 교체·끊긴 연결로 가던 프레임은 버린다
            try:
                await asyncio.wait_for(ws.send_json(frame), self.send_timeout)
            except Exception:
                log.info("drop socket for session %s", session_id, exc_info=True)
                if self.disconnect(session_id, ws) and self.on_drop is not None:
                    try:
                        await self.on_drop(session_id)
                    except Exception:
                        log.exception("connection gap record failed for %s", session_id)
                with contextlib.suppress(Exception):  # 클라이언트가 끊김을 알고 재연결하게
                    await asyncio.wait_for(ws.close(code=STALLED_CLOSE_CODE), self.send_timeout)
                return
        if self._writers.get(session_id) is asyncio.current_task():
            del self._writers[session_id]
            self._outbox.pop(session_id, None)

    # ---- typing -----------------------------------------------------------------------------

    def candidate_typing(self, session_id: uuid.UUID, state: str) -> None:
        if state == "start":
            self._candidate_typing_until[session_id] = self._now() + CANDIDATE_TYPING_TTL
        else:
            self._candidate_typing_until.pop(session_id, None)

    def is_candidate_typing(self, session_id: uuid.UUID) -> bool:
        until = self._candidate_typing_until.get(session_id)
        return until is not None and self._now() < until

    async def persona_typing(self, session_id: uuid.UUID, seat: str, state: str) -> None:
        if state == "start":
            self._persona_typing[session_id].add(seat)
        else:
            self._persona_typing[session_id].discard(seat)
        await self.send(session_id, "typing", {"participant": seat, "state": state})

    def typing_seats(self, session_id: uuid.UUID) -> list[str]:
        return sorted(self._persona_typing[session_id])
