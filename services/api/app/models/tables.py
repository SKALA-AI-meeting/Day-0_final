"""테이블 모델. infra/db/schema.sql과 1:1이며 DDL은 그 파일이
정본이다(docs/architecture/data-model.md).

여기서 create_all 하지 않는다. CHECK·부분 unique 인덱스는 DDL에만 있다.
LangGraph checkpointer 테이블과 rag-mcp 전용 material_chunks는 매핑하지 않는다.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TZ = TIMESTAMP(timezone=True)
NOW = text("now()")
GEN_UUID = text("gen_random_uuid()")
EMPTY_JSON = text("'{}'::jsonb")
EMPTY_LIST_JSON = text("'[]'::jsonb")
EMPTY_ARRAY = text("'{}'")


class Base(DeclarativeBase):
    pass


class VoiceState(Base):
    __tablename__ = "voice_states"
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=EMPTY_JSON)


class SpeechJob(Base):
    __tablename__ = "speech_jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    audio: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)


class SpeechQuota(Base):
    __tablename__ = "speech_quotas"
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=EMPTY_JSON)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID)


def _uuid(fk: str | None = None, *, nullable: bool = True) -> Mapped[Any]:
    args = (ForeignKey(fk),) if fk else ()
    return mapped_column(UUID(as_uuid=True), *args, nullable=nullable)


def _session_fk() -> Mapped[uuid.UUID]:
    return _uuid("sessions.id", nullable=False)


def _created() -> Mapped[datetime]:
    return mapped_column(TZ, server_default=NOW)


# 계정·채용 건 ------------------------------------------------------------------


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created()


class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = _uuid("companies.id", nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created()


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = _uuid("companies.id", nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    mission_id: Mapped[str] = mapped_column(Text, server_default=text("'debate'"))
    session_minutes: Mapped[int] = mapped_column(Integer, server_default=text("60"))
    catalog_version: Mapped[str] = mapped_column(Text, nullable=False)
    scenario: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created()


class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[uuid.UUID] = _pk()
    position_id: Mapped[uuid.UUID] = _uuid("positions.id", nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TZ)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZ)
    previous_session_id: Mapped[uuid.UUID | None] = _uuid()
    reissued_at: Mapped[datetime | None] = mapped_column(TZ)
    superseded_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = _created()


# 세션과 토론 상태 ----------------------------------------------------------------


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = _pk()
    invite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invites.id"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'invited'"))
    phase: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    consented_at: Mapped[datetime | None] = mapped_column(TZ)
    started_at: Mapped[datetime | None] = mapped_column(TZ)
    ends_at: Mapped[datetime | None] = mapped_column(TZ)
    phase_started_at: Mapped[datetime | None] = mapped_column(TZ)
    phase_deadline_at: Mapped[datetime | None] = mapped_column(TZ)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    invite_delivered_phases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    submitted_at: Mapped[datetime | None] = mapped_column(TZ)
    completed_at: Mapped[datetime | None] = mapped_column(TZ)
    ended_at: Mapped[datetime | None] = mapped_column(TZ)
    llm_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    debate_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    cost_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    report_enqueued_at: Mapped[datetime | None] = mapped_column(TZ)
    approve_enqueued_at: Mapped[datetime | None] = mapped_column(TZ)
    eval_round: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    failed_stage: Mapped[str | None] = mapped_column(Text)
    failed_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    sender: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[uuid.UUID | None] = _uuid()
    seq: Mapped[int | None] = mapped_column(Integer)
    client_msg_id: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str | None] = mapped_column(Text)
    is_invite: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    card_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    cited_material_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=EMPTY_JSON)
    created_at: Mapped[datetime] = _created()


class Option(Base):
    __tablename__ = "options"
    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    proposer: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_option_key: Mapped[str | None] = mapped_column(Text)
    source_message_id: Mapped[uuid.UUID | None] = _uuid("messages.id")
    created_at: Mapped[datetime] = _created()


class MessageIntent(Base):
    __tablename__ = "message_intents"
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id"), primary_key=True
    )
    session_id: Mapped[uuid.UUID] = _session_fk()
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str] = mapped_column(Text, nullable=False)
    target_option_id: Mapped[uuid.UUID | None] = _uuid("options.id")
    scenario_option_key: Mapped[str | None] = mapped_column(Text)
    new_option_label: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(Text, nullable=False)
    addressed_to: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    model: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class Stance(Base):
    __tablename__ = "stances"
    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    participant: Mapped[str] = mapped_column(Text, nullable=False)
    option_id: Mapped[uuid.UUID] = _uuid("options.id", nullable=False)
    stance: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_id: Mapped[uuid.UUID] = _uuid("messages.id", nullable=False)
    run_id: Mapped[uuid.UUID | None] = _uuid()
    created_at: Mapped[datetime] = _created()


class Slot(Base):
    __tablename__ = "slots"
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id"), primary_key=True
    )
    phase: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'empty'"))
    value: Mapped[str | None] = mapped_column(Text)
    option_id: Mapped[uuid.UUID | None] = _uuid("options.id")
    filled_by: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    support: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    confirmed_at: Mapped[datetime | None] = mapped_column(TZ)
    run_id: Mapped[uuid.UUID | None] = _uuid()


class Proposal(Base):
    __tablename__ = "proposals"
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id"), primary_key=True
    )
    sections: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSON
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = _created()
    submitted: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    submitted_at: Mapped[datetime | None] = mapped_column(TZ)
    auto_submitted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    submit_blank_sections: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    blank_warning_shown: Mapped[bool | None] = mapped_column(Boolean)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = _session_fk()
    graph: Mapped[str] = mapped_column(Text, nullable=False)
    stimulus: Mapped[str | None] = mapped_column(Text)
    ref_id: Mapped[str | None] = mapped_column(Text)
    generation: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=EMPTY_JSON)
    queued_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(TZ)
    finished_at: Mapped[datetime | None] = mapped_column(TZ)


class ConnectionGap(Base):
    __tablename__ = "connection_gaps"
    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    disconnected_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    reconnected_at: Mapped[datetime | None] = mapped_column(TZ)


class MaterialView(Base):
    __tablename__ = "material_views"
    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    material_id: Mapped[str] = mapped_column(Text, nullable=False)
    viewed_at: Mapped[datetime] = _created()


class AssistantCall(Base):
    __tablename__ = "assistant_calls"
    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    request_message_id: Mapped[uuid.UUID] = _uuid("messages.id", nullable=False)
    response_message_id: Mapped[uuid.UUID | None] = _uuid("messages.id")
    cited_material_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_LIST_JSON
    )
    created_at: Mapped[datetime] = _created()


class DebriefSurvey(Base):
    __tablename__ = "debrief_surveys"
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id"), primary_key=True
    )
    answers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    free_comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


# 관찰·분석·리포트 -----------------------------------------------------------------


class SessionCard(Base):
    __tablename__ = "session_cards"
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id"), primary_key=True
    )
    card_id: Mapped[str] = mapped_column(Text, primary_key=True)
    locked_option_id: Mapped[uuid.UUID | None] = _uuid("options.id")
    delivered_seats: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_LIST_JSON
    )
    locked_at: Mapped[datetime | None] = mapped_column(TZ)


class Observation(Base):
    __tablename__ = "observations"
    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    card_id: Mapped[str] = mapped_column(Text, nullable=False)
    element: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    fired_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    trigger_message_id: Mapped[uuid.UUID | None] = _uuid("messages.id")
    run_id: Mapped[uuid.UUID | None] = _uuid()
    created_at: Mapped[datetime] = _created()


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id"), primary_key=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    rubric_version: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_versions: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    elements: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    has_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = _created()


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "run_id"], ["evaluation_runs.session_id", "evaluation_runs.run_id"]
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    run_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    dimension_id: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[int | None] = mapped_column(Integer)
    no_record_reason: Mapped[str | None] = mapped_column(Text)
    evidence_refs: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_LIST_JSON
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    confidence: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class Report(Base):
    __tablename__ = "reports"
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id"), primary_key=True
    )
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    eval_run_id: Mapped[uuid.UUID | None] = _uuid()
    generated_at: Mapped[datetime] = _created()
    reviewed_at: Mapped[datetime | None] = mapped_column(TZ)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=EMPTY_JSON)
    reviewer_name: Mapped[str | None] = mapped_column(Text)


class RubricAdjustment(Base):
    __tablename__ = "rubric_adjustments"
    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    dimension_id: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[int | None] = mapped_column(Integer)
    no_record_reason: Mapped[str | None] = mapped_column(Text)
    memo: Mapped[str] = mapped_column(Text, nullable=False)
    adjusted_by: Mapped[uuid.UUID] = _uuid("admins.id", nullable=False)
    reviewer_name: Mapped[str] = mapped_column(Text, nullable=False)
    adjusted_at: Mapped[datetime] = _created()


class ReportNote(Base):
    __tablename__ = "report_notes"
    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    text: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_name: Mapped[str] = mapped_column(Text, nullable=False)
    admin_id: Mapped[uuid.UUID] = _uuid("admins.id", nullable=False)
    created_at: Mapped[datetime] = _created()


# 검색 저장소 ------------------------------------------------------------------------


class Material(Base):
    """자료 원문의 catalog_version별 사본. scripts/seed.py가 시드하고 api가 읽는다."""

    __tablename__ = "materials"
    catalog_version: Mapped[str] = mapped_column(Text, primary_key=True)
    material_id: Mapped[str] = mapped_column(Text, primary_key=True)
    keyword_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created()
