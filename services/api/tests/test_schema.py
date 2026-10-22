"""완료 기준(G-F): 토론형 schema.sql이 data-model.md 테이블을 갖고 임시 DB에 적용되며,
app/models가 같은 테이블·컬럼을 매핑한다."""

from app.models import Base
from sqlalchemy import text

TABLES = {
    "companies", "admins", "positions", "invites", "voice_states", "speech_jobs", "speech_quotas",
    "sessions", "messages", "message_intents", "options", "stances", "slots", "proposals",
    "agent_runs", "connection_gaps", "material_views", "assistant_calls", "debrief_surveys",
    "session_cards", "observations", "evaluation_runs", "evaluations", "reports",
    "rubric_adjustments", "report_notes", "materials", "material_chunks",
}  # fmt: skip
UNMAPPED = {"material_chunks"}  # rag-mcp 전용


async def _columns(db, table: str) -> set[str]:
    rows = await db.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
        {"t": table},
    )
    return set(rows.scalars())


async def test_schema_matches_data_model_and_models(db):
    rows = await db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
    assert {t for t in rows.scalars() if not t.startswith("checkpoint")} == TABLES
    assert set(Base.metadata.tables) == TABLES - UNMAPPED
    for name, table in Base.metadata.tables.items():
        assert {c.name for c in table.columns} == await _columns(db, name), name


async def test_debate_constraints(db):
    # 칸은 empty|confirmed만, 평가 차원은 level과 기록 없음 사유가 배타
    checks = await db.execute(
        text(
            "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE contype = 'c'"
        )
    )
    defs = [f"{t}: {d}" for t, d in checks]
    assert any(d.startswith("slots:") and "'confirmed'" in d and "'filled'" not in d for d in defs)
    assert any(d.startswith("evaluations:") and "no_record_reason IS NULL" in d for d in defs)
    assert not any("reporting" in d for d in defs)
