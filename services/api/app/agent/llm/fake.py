"""테스트용 FakeLLM과 결정적 LLM 모드(LLM_FAKE=1)의 DeterministicLLM.

FakeLLM: 스크립트의 응답을 차례로 돌려주고 받은 입력을 기록한다.

스크립트 한 단계: 문자열(본문), dict(JSON 본문으로 직렬화), AIMessage(도구 호출 등),
예외(그 시도를 실패시킴), 또는 입력 메시지를 받아 그중 하나를 돌려주는 함수.
스크립트가 끝나면 default를 돌려준다.
"""

import json
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.agent.catalog import attach
from app.agent.llm.base import BaseLLM

Step = (
    str
    | dict
    | AIMessage
    | BaseException
    | Callable[[list[BaseMessage]], "str | dict | AIMessage | BaseException"]
)


@dataclass
class FakeCall:
    messages: list[BaseMessage]
    tools: list[dict[str, Any]] | None
    json_mode: bool
    config: dict[str, Any]
    temperature: float = 0.0
    schema: dict[str, Any] | None = None

    @property
    def text(self) -> str:
        """입력 전체를 한 문자열로(검사용)."""
        return "\n".join(str(m.content) for m in self.messages)


def tool_call(name: str, **args: Any) -> AIMessage:
    """도구 하나를 부르는 모델 응답."""
    call = {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}", "type": "tool_call"}
    return AIMessage(content="", tool_calls=[call])


class FakeLLM(BaseLLM):
    model = "fake"

    def __init__(
        self,
        script: Iterable[Step] = (),
        default: str | dict = "네, 확인했어요.",
        temperature: float = 0.7,
    ) -> None:
        self.script: list[Step] = list(script)
        self.default = default
        self.retry_delays = (0.0, 0.0)
        self.temperature = temperature
        self.calls: list[FakeCall] = []

    async def _call(
        self,
        messages: list[BaseMessage],
        *,
        tools: list[dict[str, Any]] | None,
        json_mode: bool,
        temperature: float,
        config: dict[str, Any],
        schema: dict[str, Any] | None = None,
    ) -> AIMessage:
        self.calls.append(FakeCall(messages, tools, json_mode, config, temperature, schema))
        step = self.script.pop(0) if self.script else self.default
        if callable(step) and not isinstance(step, BaseException):
            step = step(messages)
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, dict):
            step = json.dumps(step, ensure_ascii=False)
        message = AIMessage(content=step) if isinstance(step, str) else step
        tokens_in = sum(len(str(m.content)) for m in messages) // 2
        message.usage_metadata = {
            "input_tokens": tokens_in,
            "output_tokens": len(str(message.content)) // 2,
            "total_tokens": tokens_in + len(str(message.content)) // 2,
        }
        return message


# ---- 결정적 LLM 모드(LLM_FAKE=1) -------------------------------------------------------------
# docs/architecture/agents.md "결정적 LLM 모드". 노드 코드는 모드를 모르고, 역할마다 입력에서 필요한
# 값만 읽어
# 같은 입력에 같은 고정 응답을 낸다. 네트워크 호출·사용량(토큰·비용)은 0이다.
_PLAN = re.compile(r"\[내 입장\]\n- 이 단계의 내 안: (.+) \((\S+)\)")
_INVITE = re.compile(r"- 침묵 초대 차례: 예")
_CANDIDATE = re.compile(r"- 지원자: (.+)님")
_OPPOSE = ("아니", "반대", "글쎄")
_AGREE = ("좋아요", "동의", "찬성")
_WITHHOLD = ("보류",)
NEUTRAL_LINE = "자료부터 같이 보고 얘기해요."
TURN_SEAT = (
    "p3"  # 규칙 결과 0명일 때 우선순위 첫 자리(critic > quiet > stubborn, quiet는 지명 전 제외)
)


class DeterministicLLM(BaseLLM):
    model = "fake"

    def __init__(self, role: str, temperature: float = 0.0, retries: int = 0) -> None:
        self.role = role
        self.temperature = temperature
        # 역할 표와 같은 시도 횟수, 대기 없음(LLM_FAULT 주입이 실 모델과 같은 재시도·폴백을 타게)
        self.retry_delays = (0.0,) * retries

    async def _call(
        self,
        messages: list[BaseMessage],
        *,
        tools: list[dict[str, Any]] | None,
        json_mode: bool,
        temperature: float,
        config: dict[str, Any],
        schema: dict[str, Any] | None = None,
    ) -> AIMessage:
        body = {
            "intent": _intent,
            "persona": _persona,
            "turn": _turn,
            "assistant": _assistant,
        }.get(self.role, lambda m, t: "{}")(messages, tools)
        message = body if isinstance(body, AIMessage) else AIMessage(content=body)
        message.usage_metadata = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        return message


def _last_human(messages: list[BaseMessage]) -> str:
    return next((str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage)), "")


def _stance(data: dict[str, Any]) -> str:
    """입장 판정(가짜 모델, persona.STANCE_SCHEMA). 프롬프트와 같은 원리로 초안이 **끝내 어디에
    서는지**로 정한다: 가장 뒤에 나온 안 라벨이 내 안이면 keep, 다른 안이면 move("물도 좋지만 저는
    불"은 keep). 보류어가 마지막 안 언급보다 뒤에 있으면(또는 안 언급이 없으면) withdraw, 아무 안도
    없으면 none. 의도 분류 역할 모델이 `[초안]` 입력을 받으면 이 규칙을 쓴다. 결정적 테스트용 근사라
    마지막 언급이 질문·보류 대상인 문장("저는 불이 좋아요. 물은 어떤가요?")은
    실제 모델과 다르게 판정한다."""
    text = str(data.get("초안") or "")
    mine = data.get("내 안") or {}
    named = [(mine.get("label"), mine.get("id"), True)] + [
        (o.get("label"), o.get("id"), False) for o in data.get("다른 안") or []
    ]
    mentions = [
        (text.rfind(label), oid, is_mine)
        for label, oid, is_mine in named
        if label and label in text
    ]
    last = max(mentions, default=None)
    withhold_at = max((text.rfind(w) for w in _WITHHOLD if w in text), default=-1)
    if withhold_at >= 0 and (last is None or withhold_at > last[0]):
        out = {"verdict": "withdraw", "target": mine.get("id"), "confidence": "high"}
    elif last is None:
        out = {"verdict": "none", "target": None, "confidence": "high"}
    elif last[2]:
        out = {"verdict": "keep", "target": last[1], "confidence": "high"}
    else:
        out = {"verdict": "move", "target": last[1], "confidence": "high"}
    return json.dumps(out, ensure_ascii=False)


def _intent(messages: list[BaseMessage], _tools: Any) -> str:
    """키워드 규칙(가짜 모델): 보류 → withhold, 반대어 → oppose, 동의어 → agree, 안 라벨 → propose,
    물음표 → question.
    "X은/는 반대예요"는 대상 X, reservation false(안을 직접 지목한 단정 반대)."""
    data = json.loads(_last_human(messages))
    if "초안" in data:
        return _stance(data)
    text = str(data.get("메시지") or "")
    labels: list[tuple[str, str, str]] = [
        (o["label"], "target_option_id", o["option_id"]) for o in data.get("나온 안") or []
    ]
    labels += [
        (c["label"], "scenario_option_key", c["scenario_option_key"])
        for c in data.get("카탈로그 안") or []
    ]
    hit = next((x for x in sorted(labels, key=lambda x: -len(x[0])) if x[0] in text), None)
    if any(w in text for w in _WITHHOLD):
        intent = "withhold"
    elif any(w in text for w in _OPPOSE):
        intent = "oppose"
    elif any(w in text for w in _AGREE):
        intent = "agree"
    elif hit:
        intent = "propose"
    elif "?" in text:
        intent = "question"
    else:
        intent = "other"
    # "X은/는 반대예요"처럼 안을 직접 지목한 반대는 단정이다(양보·걱정을 덧붙인 유보가 아니다)
    explicit = r"\s*[은는]?\s*반대(?![는은가]?\s*아니)"
    explicit_oppose = bool(
        intent == "oppose" and hit and re.search(re.escape(hit[0]) + explicit, text)
    )
    addressed = [
        p["seat"]
        for p in data.get("참여자") or []
        if p.get("name") and (p["name"] in text or p["name"][1:] in text)
    ]
    # 모델 응답 형식(app.agent.intent SCHEMA): 대상은 한 칸(target, 나온 안 id 또는 카탈로그 키)
    out: dict[str, Any] = {
        "wanted": hit[0] if hit and intent in ("propose", "agree") else None,
        "intent": intent,
        "new_option_label": None,
        "target": None,
        "confidence": "low",
        "reservation": intent in ("oppose", "withhold") and not explicit_oppose,
        "addressed_to": addressed,
        "excluded": [],
    }
    if hit and intent in ("propose", "agree", "oppose", "withhold"):
        out["target"] = hit[2]
        out["confidence"] = "high"
    elif intent == "question":
        out["confidence"] = "high"
    return json.dumps(out, ensure_ascii=False)


def _persona(messages: list[BaseMessage], _tools: Any) -> str:
    """계획 안을 지지하는 고정 문장. 입장을 밝히지 않는 단계면 중립 문장, 초대면 초대 골격."""
    text = _last_human(messages)
    candidate = _CANDIDATE.search(text)
    plan = _PLAN.search(text)
    stance = None
    if _INVITE.search(text):
        content = f"{candidate.group(1) if candidate else '지원자'}님은 어떻게 생각하세요?"
    elif plan:
        content = f"저는 {attach(plan.group(1), '이')} 좋다고 봐요."
        stance = {"option_id": plan.group(2), "stance": "support"}
    else:
        content = NEUTRAL_LINE
    return json.dumps({"content": content, "proposes": None, "stance": stance}, ensure_ascii=False)


def _turn(_messages: list[BaseMessage], _tools: Any) -> str:
    speakers = [{"seat": TURN_SEAT}]
    return json.dumps({"speakers": speakers, "reason": "fake"}, ensure_ascii=False)


def _assistant(messages: list[BaseMessage], tools: list[dict[str, Any]] | None) -> AIMessage | str:
    """도구 1회(search_materials 질문 그대로) 뒤 첫 결과 제목을 인용한 고정 문장."""
    results = [m for m in messages if isinstance(m, ToolMessage)]
    if not results and tools:
        return tool_call("search_materials", query=_last_human(messages))
    try:
        data = json.loads(str(results[-1].content)).get("data") if results else None
    except ValueError:
        data = None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return f"{data[0].get('title')} 자료를 참고해 보세요."
    return "관련 자료를 찾지 못했어요."
