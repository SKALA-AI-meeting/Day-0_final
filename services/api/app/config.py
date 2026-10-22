"""환경 설정. compose는 env_file로 주입하고, 로컬은 infra/.env.local → infra/.env 순으로 읽는다."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# 로컬: <repo>/services/api/app/config.py. 컨테이너(/app/app/config.py)에는 저장소 루트가 없다.
_parents = Path(__file__).resolve().parents
REPO_ROOT = _parents[3] if len(_parents) > 3 else _parents[-1]


def _load_env_files() -> None:
    # 이미 있는 환경변수(compose env_file)는 덮어쓰지 않는다.
    # .env.local을 먼저 읽으므로 같은 키는 로컬 값이 이긴다.
    for name in (".env.local", ".env"):
        path = REPO_ROOT / "infra" / name
        if path.is_file():
            load_dotenv(path, override=False)


DEFAULT_SCENARIOS_DIR = "/scenarios"
DEFAULT_WEB_ORIGIN = "http://localhost:3000"


def _scenarios_dir(value: str) -> Path:
    """컨테이너는 /scenarios 마운트. 기본값(/scenarios)이 로컬에 없을 때만 저장소 scenarios/로 대체.

    사용자가 다른 경로를 지정했는데 없으면 설정 오류로 본다.
    """
    path = Path(value)
    if path.is_dir():
        return path
    if value == DEFAULT_SCENARIOS_DIR:
        return REPO_ROOT / "scenarios"
    raise ValueError(f"SCENARIOS_DIR does not exist: {value}")


def _origins(value: str) -> tuple[str, ...]:
    return tuple(o.strip() for o in value.split(",") if o.strip())


@dataclass(frozen=True)
class Settings:
    database_url: str
    rabbitmq_url: str
    jwt_secret: str
    internal_token: str
    scenarios_dir: Path
    enable_test_api: bool
    # LLM 호출만 결정적 고정 응답(FakeLLM)으로 바꾸는 모드. 래퍼(app/agent/llm)가 같은 env를 읽는다
    llm_fake: bool
    openai_api_key: str
    openai_model: str
    debate_mcp_url: str
    rag_mcp_url: str
    assessment_mcp_url: str
    # assessment-mcp 토큰은 결정적 노드 모듈만 쓴다. 페르소나 클라이언트에는 넘기지 않는다
    assessment_mcp_token: str
    agent_runner: str
    web_origins: tuple[str, ...]
    contact_email: str = "recruit@example.com"
    web_base_url: str = "http://localhost:3000"
    public_port: int = 8000
    internal_port: int = 8001
    voice_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    _load_env_files()
    env = os.environ
    return Settings(
        database_url=env.get(
            "DATABASE_URL", "postgresql+asyncpg://dayzero:dayzero@localhost:5432/dayzero"
        ),
        rabbitmq_url=env.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"),
        jwt_secret=env.get("JWT_SECRET", "change-me"),
        internal_token=env.get("INTERNAL_TOKEN", "change-me-internal"),
        scenarios_dir=_scenarios_dir(env.get("SCENARIOS_DIR", DEFAULT_SCENARIOS_DIR)),
        enable_test_api=env.get("ENABLE_TEST_API") == "1",
        llm_fake=env.get("LLM_FAKE") == "1",
        openai_api_key=env.get("OPENAI_API_KEY", ""),
        openai_model=env.get("OPENAI_MODEL", "gpt-4o-mini"),
        debate_mcp_url=env.get("DEBATE_MCP_URL", "http://localhost:9101/mcp"),
        rag_mcp_url=env.get("RAG_MCP_URL", "http://localhost:9102/mcp"),
        assessment_mcp_url=env.get("ASSESSMENT_MCP_URL", "http://localhost:9103/mcp"),
        assessment_mcp_token=env.get("ASSESSMENT_MCP_TOKEN", ""),
        agent_runner=env.get("AGENT_RUNNER", "session"),
        web_origins=_origins(env.get("WEB_ORIGIN", DEFAULT_WEB_ORIGIN)),
        contact_email=env.get("CONTACT_EMAIL", "recruit@example.com"),
        web_base_url=env.get("WEB_BASE_URL", DEFAULT_WEB_ORIGIN),
    )
