"""실시간 의도 분류 공개 함수(docs/contracts/internal.md "의도 분류 출력 스키마"). G-F 시그니처
동결.

api 메일박스(`services/debate.py`)가 `message` 자극마다 그래프 전에 부른다. 입력 단계는 저장된
`messages.phase`이고 카탈로그 안 전체·그 단계의 기존 안·직전 메시지 5개·자리 이름을 받는다.
모델 `MODEL_INTENT`(역할 `intent`), temperature 0, 타임아웃 10초, 재시도 없음(make_llm 역할 표).

- 실패·타임아웃·스키마 위반·허용 값 밖(없는 option_id, 카탈로그 밖 키, 자리 밖
  addressed_to·excluded)은 폴백 `{intent: other, ..., confidence: low}`, fallback=True.
- 모델 응답(`SCHEMA`)은 먼저 지원자가 원하는 안을 지원자 말로 적게 하고(`wanted`, 저장하지
  않음), 대상 안을 한 칸(`target`: 나온 안 id 또는 카탈로그 키)으로 받되 새 안 라벨을 그보다 먼저
  쓰게 한다. 모듈이 계약 출력(`OUTPUT_FIELDS`, `target_option_id`·`scenario_option_key`)으로
  나눈다. 대상 칸이 둘이면 모델이 아직 말하지 않은 카탈로그 키에 새 아이디어를 넣었고, 원하는 안을
  먼저 적지 않으면 보류와 새 제안이 섞인 메시지를 보류로만 분류했다.
- 새 안 라벨이 나온 안·카탈로그 안 라벨과 정규화 문자열로 같으면 그 안으로 바꾼다(문자열 일치만).
- `excluded`(지원자가 답하지 말라고 뺀 자리)에 든 자리는 `addressed_to`에서 뺀다.
- 카탈로그 키가 이미 행이 있는 안을 가리키면 그 option_id로 바꾼다(최초 제안자 보존).
- `message_id`를 주면 결과를 프로세스 메모리에 남겨 토론 그래프가 같은 메시지를 다시 분류하지 않고
  읽는다(`recall`). 재시작으로 비면 그래프가 필요할 때만 다시 분류한다.
- 지시 프롬프트는 채용 건 복사본의 `intent.md`(카탈로그 `prompts/intent/intent.md`)를
  `instructions`로 받는다(없으면 내장 요약).
"""

import json
import logging
import re
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


# 계약 출력 필드(docs/contracts/internal.md "의도 분류 출력 스키마")
OUTPUT_FIELDS = tuple(IntentResult.__annotations__)

# 모델 응답 OpenAI json_schema(strict). 속성 순서대로 생성되므로 새 안 라벨을 대상보다 먼저 둔다
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "wanted",
        "intent",
        "new_option_label",
        "target",
        "confidence",
        "reservation",
        "addressed_to",
        "excluded",
    ],
    "properties": {
        "wanted": {"type": ["string", "null"]},
        "intent": {"type": "string", "enum": list(INTENTS)},
        "new_option_label": {"type": ["string", "null"]},
        "target": {"type": ["string", "null"]},
        "confidence": {"type": "string", "enum": ["high", "low"]},
        "reservation": {"type": "boolean"},
        "addressed_to": {"type": "array", "items": {"type": "string", "enum": list(SEAT_IDS)}},
        "excluded": {"type": "array", "items": {"type": "string", "enum": list(SEAT_IDS)}},
    },
}

# 카탈로그 intent.md가 없을 때만 쓰는 최소 지침(agent-spike v2 규칙 요약)
DEFAULT_INSTRUCTIONS = """지원자 메시지 하나의 의도를 분류해 JSON 하나만 낸다.
wanted: 지원자가 원하거나 내놓은 안을 지원자 말로 20자 이내(없으면 null). 먼저 쓴다.
intent: propose(안을 자기 의견으로 냄) | agree(남의 안에 동의) | oppose(안에 반대) |
withhold(안을 보류·유보하거나 판단을 미룸) | question(질문) | other.
다른 안을 내거나 지지하면 그 안을 대상으로 propose·agree(보류·반대한 안은 대상이 아님). 다른 안 지지
없이 보류만 하면 withhold, 반대만 하면 oppose.
target은 대상이 나온 안이면 그 option_id, 행이 없는 카탈로그 안이면 그 scenario_option_key. 핵심이
같은 안이 없는 propose면 target은 null이고 new_option_label(20자 이내). 둘 중 하나만 채운다.
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


def _plain(value: str) -> str:
    """라벨 비교용 정규화(문장부호·공백 차이만 없앤다, 칸 확정의 안 연결과 같은 규칙)."""
    value = re.sub(r"[^\w\s]", "", value.strip().lower())
    return re.sub(r"\s+", " ", value).strip()


def _from_response(
    out: dict[str, Any], options: list[ExistingOption], catalog: list[CatalogOption]
) -> dict[str, Any] | None:
    """모델 응답의 `target` 한 칸을 계약 필드로 나눈다. 알 수 없는 값이면 None(폴백)."""
    if set(out) != set(SCHEMA["properties"]):
        return None
    out = dict(out)
    out.pop("wanted")
    target = out.pop("target")
    ids = {o["option_id"] for o in options}
    keys = {c["scenario_option_key"] for c in catalog}
    if target is not None and target not in ids and target not in keys:
        return None
    out["target_option_id"] = target if target in ids else None
    out["scenario_option_key"] = target if target is not None and target not in ids else None
    label = out.get("new_option_label")
    if target is None and isinstance(label, str) and label.strip():
        same = _plain(label)
        hit = next((o["option_id"] for o in options if _plain(o["label"]) == same), None)
        if hit:
            out["target_option_id"] = hit
        else:
            key = next(
                (c["scenario_option_key"] for c in catalog if _plain(c["label"]) == same), None
            )
            out["scenario_option_key"] = key
    return out


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
    if set(out) != set(OUTPUT_FIELDS):
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
            shaped = _from_response(out, options, catalog) if isinstance(out, dict) else None
            result = _validate(_normalize(shaped, options), options, catalog) if shaped else None
        except Exception as exc:  # 전송·타임아웃·파싱 실패 모두 폴백
            log.warning("intent classify failed: %r", exc)
    used_fallback = result is None
    if result is None:
        result = fallback()
    if message_id is not None:
        remember(message_id, result)
    return result, used_fallback
