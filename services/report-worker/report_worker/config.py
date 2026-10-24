"""환경 설정. compose는 env_file로 주입하고, 로컬은 infra/.env.local → infra/.env 순으로 읽는다.

평가 모델·키 선택은 api `app/agent/llm/roles.py`의 평가 역할(`evaluator`) 규칙 사본이다:
`MODEL_EVALUATOR`가 비면 `OPENAI_MODEL`(기본 gpt-4o-mini), 키는 모델 id로 고른다(제한 키 허용 목록의
gpt-* → OPENAI_API_KEY, gemini-* → GEMINI_API_KEY, 그 밖은 설정 오류. 개인 키 경로 없음).
허용 목록을 바꾸면 roles.py와 함께 고친다.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 로컬은 <repo>/services/report-worker/report_worker/config.py. 컨테이너에는 저장소 루트가 없다.
_parents = Path(__file__).resolve().parents
REPO_ROOT = _parents[3] if len(_parents) > 3 else _parents[-1]

MODEL = "gpt-4o-mini"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
# roles.py RESTRICTED_MODELS와 같은 목록
RESTRICTED_MODELS = frozenset(
    {"gpt-4o-mini", "gpt-4.1-mini", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5-nano"}
)
# roles.py ROLE_CONFIG["evaluator"]와 같은 값
EVALUATOR_TIMEOUT = 60.0
EVALUATOR_RETRY_DELAYS = (1.0, 3.0)
EVALUATOR_TEMPERATURE = 0.1


def _load_env_files() -> None:
    # 이미 있는 환경변수(compose env_file)는 덮어쓰지 않는다. .env.local의 같은 키가 이긴다.
    for name in (".env.local", ".env"):
        path = REPO_ROOT / "infra" / name
        if path.is_file():
            load_dotenv(path, override=False)


@dataclass(frozen=True)
class Settings:
    api_internal_url: str
    internal_token: str
    assessment_mcp_url: str
    assessment_mcp_token: str
    database_url: str
    rabbitmq_url: str
    model_evaluator: str


def checkpoint_url(database_url: str) -> str:
    """api는 SQLAlchemy(asyncpg) 형식을 쓴다. PostgresSaver(psycopg)는 드라이버 표기 없이 받는다."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def evaluator_model(env: Mapping[str, str]) -> str:
    return env.get("MODEL_EVALUATOR") or env.get("OPENAI_MODEL") or MODEL


def key_for(model: str, env: Mapping[str, str]) -> tuple[str, str | None]:
    """(api_key, base_url). 키가 비어 있으면 설정 오류로 멈춘다(다른 키로 몰래 바꾸지 않는다)."""
    if model in RESTRICTED_MODELS:
        name, base_url = "OPENAI_API_KEY", None
    elif model.startswith("gemini-"):
        name, base_url = "GEMINI_API_KEY", GEMINI_BASE_URL
    elif model.startswith("gpt-"):
        allowed = ", ".join(sorted(RESTRICTED_MODELS))
        raise ValueError(f"model {model} is not in the restricted-key allowlist ({allowed})")
    else:
        raise ValueError(f"no key rule for model: {model}")
    key = env.get(name, "")
    if not key:
        raise ValueError(f"{name} is required for model {model}")
    return key, base_url


def load_settings() -> Settings:
    _load_env_files()
    env = os.environ
    return Settings(
        api_internal_url=env.get("API_INTERNAL_URL", "http://api:8001").rstrip("/"),
        internal_token=env.get("INTERNAL_TOKEN", "change-me-internal"),
        assessment_mcp_url=env.get("ASSESSMENT_MCP_URL", "http://assessment-mcp:9103/mcp"),
        assessment_mcp_token=env.get("ASSESSMENT_MCP_TOKEN", ""),
        database_url=checkpoint_url(env.get("DATABASE_URL", "")),
        rabbitmq_url=env.get("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/"),
        model_evaluator=evaluator_model(env),
    )
