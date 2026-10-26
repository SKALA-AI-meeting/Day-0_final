"""서비스 공용: 오류, 커밋 뒤 효과(WS·자극·큐), 시각 변환, 직렬화, 조회 도우미."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Invite, Message, Position, Session, Slot

if TYPE_CHECKING:
    from app.agent.runner import Stimulus

ENDED_STATUSES = frozenset(
    {"completed", "report_ready", "approving", "reviewed", "failed", "cancelled"}
)
RETENTION = timedelta(days=90)


class ServiceError(Exception):
    """계약 오류 `{"error": {code, message, ...extra}}`와 HTTP 상태."""

    def __init__(self, status: int, code: str, message: str | None = None, **extra: Any) -> None:
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message or code
        self.extra = extra


def conflict(code: str = "conflict", message: str | None = None) -> ServiceError:
    return ServiceError(409, code, message)


def not_found(message: str = "not found") -> ServiceError:
    return ServiceError(404, "not_found", message)


def invalid(code: str = "invalid_input", message: str | None = None) -> ServiceError:
    return ServiceError(422, code, message)


@dataclass
class Effects:
    """한 트랜잭션이 커밋된 뒤에 할 일. 커밋 전에는 아무것도 보내지 않는다."""

    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    stimuli: list[tuple["Stimulus", uuid.UUID]] = field(default_factory=list)
    publish: list[str] = field(default_factory=list)
    generation: int | None = None

    def emit(self, type_: str, payload: dict[str, Any]) -> None:
        self.events.append((type_, payload))


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def t_min(session: Session, at: datetime | None) -> float | None:
    if at is None or session.started_at is None:
        return None
    return round(max((at - session.started_at).total_seconds(), 0) / 60, 2)


def purge_at(session: Session) -> str | None:
    return iso(session.ended_at + RETENTION) if session.ended_at else None


async def load_session(db: AsyncSession, session_id: uuid.UUID, lock: bool = False) -> Session:
    query = select(Session).where(Session.id == session_id)
    if lock:
        query = query.with_for_update()
    session = await db.scalar(query)
    if session is None:
        raise not_found("session not found")
    return session


async def load_context(
    db: AsyncSession, session_id: uuid.UUID, lock: bool = False
) -> tuple[Session, Position, Invite]:
    session = await load_session(db, session_id, lock)
    invite = await db.get(Invite, session.invite_id)
    position = await db.get(Position, invite.position_id)
    return session, position, invite


PUBLIC_MESSAGE_KINDS = frozenset({"greeting"})


def serialize_message(session: Session, m: Message) -> dict[str, Any]:
    out = {
        "id": str(m.id),
        "sender": m.sender,
        "content": m.content,
        "phase": m.phase,
        "is_invite": m.is_invite,
        "t_min": t_min(session, m.created_at) or 0.0,
        "created_at": iso(m.created_at),
    }
    # 지원자 메시지만: 보낸 쪽의 client_msg_id(음성은 발언 id). 화면이 ack를 놓쳐도(재연결)
    # /state로 그 발언이 저장됐는지 확인한다
    if m.sender == "candidate" and m.client_msg_id:
        out["client_msg_id"] = m.client_msg_id
    # 공개해도 되는 종류만 내보낸다. 세션 시작 인사(greeting)는 화면이 인사를 다 들을 때까지
    # 인사 화면을 두는 데 쓴다. 카드 고정 문장(card_fixed_line) 같은 설계된 장면 표시는
    # 지원자에게 보이지 않게 내보내지 않는다
    kind = (m.meta or {}).get("kind")
    if kind in PUBLIC_MESSAGE_KINDS:
        out["kind"] = kind
    return out


def slot_outcome(slot: Slot) -> str | None:
    """표시용 칸 결과(저장하지 않음). 지원자 확정은 확정 순간 확정 안 지지자가 4명 중 3명
    이상일 때만 합의, AI 합의 채우기는 항상 합의."""
    if slot.status != "confirmed":
        return None
    if slot.resolution == "timeout":
        return "timeout"
    if slot.filled_by != "candidate":
        return "consensus"
    supporters = next(
        (r["supporters"] for r in slot.support or [] if r["option_id"] == str(slot.option_id)),
        [],
    )
    return "consensus" if slot.option_id and len(supporters) >= 3 else "candidate_decision"


def slot_public(session: Session, slot: Slot) -> dict[str, Any]:
    return {
        "phase": slot.phase,
        "status": slot.status,
        "value": slot.value,
        "filled_by": slot.filled_by,
        "resolution": slot.resolution,
        "outcome": slot_outcome(slot),
        "t_min": t_min(session, slot.confirmed_at),
    }


def slot_full(session: Session, slot: Slot) -> dict[str, Any]:
    return {
        **slot_public(session, slot),
        "option_id": str(slot.option_id) if slot.option_id else None,
        "timeout_rule": slot.timeout_rule,
        "support": slot.support,
        "confirmed_at": iso(slot.confirmed_at),
    }


def empty_sections(template: list[str]) -> dict[str, str]:
    return {f: "" for f in template}
