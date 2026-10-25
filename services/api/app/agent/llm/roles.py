"""역할별 모델·키 선택과 호출 설정(docs/architecture/agents.md "역할별 모델"). G-F 동결.

규칙은 여기 한 곳이다.

- 역할 5개는 env `MODEL_INTENT`·`MODEL_TURN`·`MODEL_PERSONA`·`MODEL_ASSISTANT`·`MODEL_EVALUATOR`를
  읽고 비어 있으면 `OPENAI_MODEL`(기본 gpt-4o-mini)을 쓴다. 다른 이름의 역할 env는 읽지 않는다.
- 키는 모델 id로 고른다: 제한 키 허용 목록(`RESTRICTED_MODELS`)의 gpt-* → OPENAI_API_KEY,
  gemini-* → GEMINI_API_KEY(OpenAI 호환 엔드포인트). 목록 밖 gpt-*는 설정 오류로 즉시 멈추고
  다른 키로 바꾸지 않는다(개인 키 경로 없음). 대체 모델 없음.
  이 규칙의 정본은 이 파일이고 report-worker `config.py`, `tests/eval/llm_access.py`,
  `scripts/profile.py`가 같은 목록·규칙의 사본을 둔다(네 곳을 함께 고친다).
- 재시도는 역할 표의 횟수(기본 2회, 의도 분류는 0회) 뒤 LLMUnavailable → 호출한 노드의 규칙 폴백.
  report-worker는 같은 규칙의 사본을 쓴다(평가 역할 `evaluator`).
- env `LLM_FAKE=1`이면 키·모델을 고르지 않고 역할별 DeterministicLLM을 준다(결정적 LLM 모드).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from app.agent.llm.base import MODEL, RETRY_DELAYS, BaseLLM
from app.agent.llm.fake import DeterministicLLM
from app.agent.llm.openai import OpenAILLM
from app.config import get_settings

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
# 제한 키(OPENAI_API_KEY)로 부를 수 있는 모델. 여기 없는 gpt-*는 쓰지 않는다
RESTRICTED_MODELS = frozenset(
    {"gpt-4o-mini", "gpt-4.1-mini", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5-nano"}
)
# 음성(STT·TTS) 모델도 같은 제한 키를 쓴다(VOICE_PROVIDER=openai, decisions.md 2026-10-25).
# LLM 역할 모델로는 고를 수 없고 app/voice/speech.py의 OpenAISpeech만 쓴다.
RESTRICTED_SPEECH_MODELS = frozenset(
    {"gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1", "gpt-4o-mini-tts", "tts-1"}
)


@dataclass(frozen=True)
class RoleConfig:
    env: str
    timeout: float  # 초. 호출 한 번의 상한
    retries: int  # 최초 호출 뒤 재시도 횟수
    temperature: float


ROLE_CONFIG: dict[str, RoleConfig] = {
    "intent": RoleConfig("MODEL_INTENT", timeout=10.0, retries=0, temperature=0.0),
    "turn": RoleConfig("MODEL_TURN", timeout=15.0, retries=2, temperature=0.0),
    "persona": RoleConfig("MODEL_PERSONA", timeout=20.0, retries=2, temperature=0.7),
    "assistant": RoleConfig("MODEL_ASSISTANT", timeout=30.0, retries=2, temperature=0.3),
    "evaluator": RoleConfig("MODEL_EVALUATOR", timeout=60.0, retries=2, temperature=0.1),
}
ROLES = tuple(ROLE_CONFIG)


def role_config(role: str) -> RoleConfig:
    if role not in ROLE_CONFIG:
        raise ValueError(f"unknown llm role: {role}")
    return ROLE_CONFIG[role]


def model_for(role: str, env: Mapping[str, str] | None = None) -> str:
    config = role_config(role)
    env = _env(env)
    return env.get(config.env) or env.get("OPENAI_MODEL") or MODEL


def key_name_for(model: str) -> tuple[str, str | None]:
    """(env 이름, base_url). 허용 목록 밖 모델은 설정 오류(키 유무와 무관)."""
    if model in RESTRICTED_MODELS:
        return "OPENAI_API_KEY", None
    if model.startswith("gemini-"):
        return "GEMINI_API_KEY", GEMINI_BASE_URL
    if model.startswith("gpt-"):
        allowed = ", ".join(sorted(RESTRICTED_MODELS))
        raise ValueError(f"model {model} is not in the restricted-key allowlist ({allowed})")
    raise ValueError(f"no key rule for model: {model}")


def key_for(model: str, env: Mapping[str, str] | None = None) -> tuple[str, str | None]:
    """(api_key, base_url). 키가 비어 있으면 설정 오류로 멈춘다(다른 키로 몰래 바꾸지 않는다)."""
    env = _env(env)
    name, base_url = key_name_for(model)
    key = env.get(name, "")
    if not key:
        raise ValueError(f"{name} is required for model {model}")
    return key, base_url


def speech_key(model: str, env: Mapping[str, str] | None = None) -> str:
    """음성 모델의 제한 키. 허용 목록 밖이거나 키가 비면 설정 오류로 멈춘다.

    다른 키로 바꾸지 않는다.
    """
    if model not in RESTRICTED_SPEECH_MODELS:
        allowed = ", ".join(sorted(RESTRICTED_SPEECH_MODELS))
        raise ValueError(f"speech model {model} is not in the restricted-key allowlist ({allowed})")
    key = _env(env).get("OPENAI_API_KEY", "")
    if not key:
        raise ValueError(f"OPENAI_API_KEY is required for speech model {model}")
    return key


def make_llm(role: str, env: Mapping[str, str] | None = None) -> BaseLLM:
    """그 역할의 LLM 래퍼(역할 표의 타임아웃·재시도·temperature).

    env를 주지 않으면 infra/.env를 읽은 프로세스 환경을 쓴다.
    """
    config = role_config(role)
    env = _env(env)
    if env.get("LLM_FAKE") == "1":
        return DeterministicLLM(role, temperature=config.temperature, retries=config.retries)
    model = model_for(role, env)
    key, base_url = key_for(model, env)
    delays = tuple(RETRY_DELAYS[: config.retries])
    llm = OpenAILLM(
        key,
        model,
        timeout=config.timeout,
        retry_delays=delays,
        base_url=base_url,
        temperature=config.temperature,
    )
    llm.role = role
    return llm


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    if env is not None:
        return env
    get_settings()  # .env.local → .env를 프로세스 환경에 읽어 둔다
    return os.environ
