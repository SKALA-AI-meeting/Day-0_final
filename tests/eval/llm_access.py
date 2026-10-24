"""호스트 러너(가상 지원자·판정 모델)가 쓰는 키 선택 규칙과 과금 동의 검사.

키 규칙은 `services/api/app/agent/llm/roles.py`가 정본이고 여기는 같은 목록의 사본이다
(report-worker `config.py`, `scripts/profile.py`도 같다). 허용 목록의 gpt-*만 `OPENAI_API_KEY`로
부르고, 목록 밖 gpt-*는 키가 있어도 즉시 오류다(개인 키 경로 없음).

판정 모델(`VC_JUDGE_MODEL`)에는 기본값이 없다. 보정 세트의 오류 여부 일치가 gpt-4o 0.98에 비해
gpt-5.4-mini 0.93·gpt-4.1-mini 0.91이라 제한 키 모델은 통과 기준에 못 미쳤고, 작은 판정 모델로
낸 수치는 기록된 기준선과 비교할 수 없다. 켤 때는 모델을 명시하고 결과에 그 모델을 남긴다.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

RESTRICTED_MODELS = frozenset(
    {"gpt-4o-mini", "gpt-4.1-mini", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5-nano"}
)
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
OPENAI_BASE_URL = "https://api.openai.com/v1"


def key_name_for(model: str) -> tuple[str, str]:
    """(env 이름, base_url). 허용 목록 밖 모델은 설정 오류."""
    if model in RESTRICTED_MODELS:
        return "OPENAI_API_KEY", OPENAI_BASE_URL
    if model.startswith("gemini-"):
        return "GEMINI_API_KEY", GEMINI_BASE_URL
    if model.startswith("gpt-"):
        allowed = ", ".join(sorted(RESTRICTED_MODELS))
        raise ValueError(f"모델 {model}은 제한 키 허용 목록({allowed}) 밖이다")
    raise ValueError(f"키 규칙이 없는 모델: {model}")


def api_key_for(model: str, values: Mapping[str, str]) -> tuple[str, str]:
    """(키 값, base_url). 비어 있으면 오류(값은 메시지에 넣지 않는다)."""
    name, base_url = key_name_for(model)
    key = values.get(name, "")
    if not key:
        raise RuntimeError(f"{name}가 비어 있다(모델 {model})")
    return key, base_url


def judge_model(values: Mapping[str, str]) -> str:
    """판정 모델. 기본값 없음: `VC_JUDGE_MODEL`을 명시해야 한다."""
    model = values.get("VC_JUDGE_MODEL", "")
    if not model:
        raise RuntimeError(
            "VC_JUDGE_MODEL이 비어 있다. 판정 모델은 기본값 없이 명시한다"
            "(제한 키 모델은 보정 기준 미달이라 기록된 기준선과 비교할 수 없다)"
        )
    return model


def require_allow_api(what: str, values: Mapping[str, str] | None = None) -> None:
    """과금 API를 부르는 진입점에서 호출한다. `ALLOW_API=1`이 아니면 바로 끝낸다."""
    env = os.environ if values is None else values
    if env.get("ALLOW_API") != "1":
        sys.exit(f"{what}: 실제 API 호출(과금)이다. ALLOW_API=1을 명시해야 실행한다")
