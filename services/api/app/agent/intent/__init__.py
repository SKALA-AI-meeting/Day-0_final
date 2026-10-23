"""실시간 의도 분류 공개 함수(docs/contracts/internal.md "의도 분류 출력 스키마"). G-F 시그니처
동결.

api 메일박스(`services/debate.py`)가 `message` 자극마다 그래프 전에 부른다. 입력 단계는 저장된
`messages.phase`이고 카탈로그 안 전체·그 단계의 기존 안·직전 메시지 5개·자리 이름을 받는다.
모델 `MODEL_INTENT`(역할 `intent`), temperature 0, 타임아웃 10초, 재시도 없음(make_llm 역할 표).

- 실패·타임아웃·스키마 위반·허용 값 밖(없는 option_id, 카탈로그 밖 키, 자리 밖
  addressed_to·excluded)은 폴백 `{intent: other, ..., confidence: low}`, fallback=True.
- `excluded`(지원자가 답하지 말라고 뺀 자리)에 든 자리는 `addressed_to`에서 뺀다.
- 카탈로그 키가 이미 행이 있는 안을 가리키면 그 option_id로 바꾼다(최초 제안자 보존).
- `message_id`를 주면 결과를 프로세스 메모리에 남겨 토론 그래프가 같은 메시지를 다시 분류하지 않고
  읽는다(`recall`). 재시작으로 비면 그래프가 필요할 때만 다시 분류한다.
- 지시 프롬프트는 채용 건 복사본의 `intent.md`(카탈로그 `prompts/intent/intent.md`)를
  `instructions`로 받는다(없으면 내장 요약).
"""

import json
import logging
from collections import OrderedDict
from typing import Any, Literal, TypedDict

from app.agent.llm.base import JsonLLM

log = logging.getLogger(__name__)

INTENTS = ("propose", "agree", "oppose", "withhold", "question", "other")
SEAT_IDS = ("p1", "p2", "p3")
TIMEOUT_SECONDS = 10.0
CACHE_SIZE = 2048


class IntentResult(TypedDict):
    intent: Literal["propose", "agree", "oppose", "withhold", "question", "other"]
    target_option_id: str | None
    scenario_option_key: str | None
    new_option_label: str | None
    confidence: Literal["high", "low"]
    reservation: bool
    addressed_to: list[str]
    excluded: list[str]


class CatalogOption(TypedDict):
    scenario_option_key: str
    label: str


class ExistingOption(TypedDict):
    option_id: str
    label: str
    scenario_option_key: str | None


# OpenAI json_schema(strict) 출력 스키마
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(IntentResult.__annotations__),
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "target_option_id": {"type": ["string", "null"]},
        "scenario_option_key": {"type": ["string", "null"]},
        "new_option_label": {"type": ["string", "null"]},
        "confidence": {"type": "string", "enum": ["high", "low"]},
        "reservation": {"type": "boolean"},
        "addressed_to": {"type": "array", "items": {"type": "string", "enum": list(SEAT_IDS)}},
        "excluded": {"type": "array", "items": {"type": "string", "enum": list(SEAT_IDS)}},
    },
}

# 카탈로그 intent.md가 없을 때만 쓰는 최소 지침(agent-spike v2 규칙 요약)
DEFAULT_INSTRUCTIONS = """지원자 메시지 하나의 의도를 분류해 JSON 하나만 낸다.
intent: propose(안을 자기 의견으로 냄) | agree(남의 안에 동의) | oppose(안에 반대) |
withhold(안을 보류·유보하거나 판단을 미룸) | question(질문) | other.
다른 안을 내거나 지지하면 그 안을 대상으로 propose·agree(보류·반대한 안은 대상이 아님). 다른 안 지지
없이 보류만 하면 withhold, 반대만 하면 oppose.
대상이 나온 안이면 target_option_id, 행이 없는 카탈로그 안이면 scenario_option_key, 둘 다 아닌
propose면 new_option_label(20자 이내). 핵심이 같을 때만 기존 안으로 묶는다. 셋 중 하나만 채운다.
의도와 대상이 분명하면 confidence high, 아니면 low.
reservation: 대상 안에 대한 보류·반대·조건 표현이 있으면 true.
addressed_to는 이름을 부르거나 답한 자리 id 목록, 전체 질문이면 [].
excluded는 답하지 말라고 뺀 자리("A님 말고")."""

_cache: "OrderedDict[str, IntentResult]" = OrderedDict()


def fallback() -> IntentResult:
    """실패·스키마 위반 때의 결과(docs/contracts/internal.md). 호출마다 새 dict."""
    return {
        "intent": "other",
        "target_option_id": None,
        "scenario_option_key": None,
        "new_option_label": None,
        "confidence": "low",
        "reservation": False,
        "addressed_to": [],
        "excluded": [],
    }


def remember(message_id: Any, result: IntentResult) -> None:
    key = str(message_id)
    _cache[key] = dict(result)  # type: ignore[assignment]
    _cache.move_to_end(key)
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)


def recall(message_id: Any) -> IntentResult | None:
    """같은 프로세스에서 분류한 메시지의 결과(없으면 None)."""
    found = _cache.get(str(message_id))
    return dict(found) if found else None  # type: ignore[return-value]


def build_user(
    message: str,
    *,
    phase: str,
    catalog: list[CatalogOption],
    options: list[ExistingOption],
    recent: list[dict[str, Any]],
    seats: dict[str, str],
) -> str:
    body = {
        "단계": phase,
        "참여자": [{"seat": s, "name": n} for s, n in seats.items()],
        "카탈로그 안": catalog,
        "나온 안": options,
        "직전 메시지": [
            {"sender": m.get("sender"), "content": m.get("content")} for m in recent[-5:]
        ],
        "메시지": message,
    }
    return json.dumps(body, ensure_ascii=False)


TARGET_FIELDS = ("target_option_id", "scenario_option_key", "new_option_label")


def _normalize(out: dict[str, Any], options: list[ExistingOption]) -> dict[str, Any]:
    """대상 필드를 계약대로 하나만 남긴다(나온 안 id > 카탈로그 키 > 새 라벨).

    카탈로그 키가 이미 행이 있는 안이면 그 option_id로 바꾼다(최초 제안자 보존).
    """
    out = dict(out)
    key = out.get("scenario_option_key")
    for option in options:
        if key and option.get("scenario_option_key") == key:
            out["target_option_id"], out["scenario_option_key"] = option["option_id"], None
    kept = next((f for f in TARGET_FIELDS if out.get(f)), None)
    for field in TARGET_FIELDS:
        if field != kept and field in out:
            out[field] = None
    return out


def _validate(
    out: dict[str, Any], options: list[ExistingOption], catalog: list[CatalogOption]
) -> IntentResult | None:
    if set(out) - set(SCHEMA["properties"]) or set(SCHEMA["required"]) - set(out):
        return None
    if out["intent"] not in INTENTS or out["confidence"] not in ("high", "low"):
        return None
    ids = {o["option_id"] for o in options}
    keys = {c["scenario_option_key"] for c in catalog}
    if out["target_option_id"] not in (None, *ids):
        return None
    if out["scenario_option_key"] not in (None, *keys):
        return None
    label = out["new_option_label"]
    if label is not None and (not isinstance(label, str) or len(label) > 40):
        return None
    if not isinstance(out["reservation"], bool):
        return None
    addressed, excluded = out["addressed_to"], out["excluded"]
    for seats in (addressed, excluded):
        if not isinstance(seats, list) or any(a not in SEAT_IDS for a in seats):
            return None
    excluded = list(dict.fromkeys(excluded))
    return {
        "intent": out["intent"],
        "target_option_id": out["target_option_id"],
        "scenario_option_key": out["scenario_option_key"],
        "new_option_label": label,
        "confidence": out["confidence"],
        "reservation": out["reservation"],
        "addressed_to": [a for a in dict.fromkeys(addressed) if a not in excluded],
        "excluded": excluded,
    }


async def classify(
    llm: JsonLLM | None,
    message: str,
    *,
    phase: str,
    catalog: list[CatalogOption],
    options: list[ExistingOption],
    recent: list[dict[str, Any]],
    seats: dict[str, str],
    message_id: Any = None,
    instructions: str | None = None,
) -> tuple[IntentResult, bool]:
    """지원자 메시지 하나의 의도. 반환 (결과, fallback).

    seats는 {자리 id: 표시 이름}, recent는 직전 메시지 [{sender, content}] 최대 5개.
    결과의 target_option_id는 options 안의 id, scenario_option_key는 catalog 안의 키만 허용한다.
    llm이 None이면 폴백만 돌려준다. message_id·instructions는 G-F 뒤 더한 선택 인자다.
    """
    result: IntentResult | None = None
    if llm is not None:
        try:
            out = await llm.complete_json(
                "intent",
                instructions or DEFAULT_INSTRUCTIONS,
                build_user(
                    message,
                    phase=phase,
                    catalog=catalog,
                    options=options,
                    recent=recent,
                    seats=seats,
                ),
                SCHEMA,
                timeout=TIMEOUT_SECONDS,
                temperature=0.0,
            )
            result = _validate(_normalize(out, options), options, catalog)
        except Exception as exc:  # 전송·타임아웃·파싱 실패 모두 폴백
            log.warning("intent classify failed: %r", exc)
    used_fallback = result is None
    if result is None:
        result = fallback()
    if message_id is not None:
        remember(message_id, result)
    return result, used_fallback
