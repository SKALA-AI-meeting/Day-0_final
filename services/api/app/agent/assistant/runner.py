"""어시스턴트: AssistantRunner Protocol 구현(docs/architecture/agents.md "어시스턴트", R5-13).

```
agent(LLM, 도구 바인딩) ⇄ tools(search_materials·get_material) … 최대 3회 → answer → guard
```

- 모델이 질문을 보고 도구 호출 여부와 인자를 스스로 고른다. 응답당 도구 3회, 넘으면 지금까지
  결과로 답한다.
- 열린 자료만 검색된다. 도구가 `not_released`만 돌려주면 LLM 답 대신 고정 문장("키워드가 정해지면
  …").
- 근거(결정 D1): 답의 숫자가 받은 자료·질문에 없으면 재생성 1회 → 그래도 남으면 그 수치가 든
  문장을 뺀다. 인용은 실제로 쓴 자료(제목·id를 적었거나 숫자·12자 이상 구간이 글자 그대로 겹치는
  자료)만 `cited_material_ids`로 내고 본문 끝에 출처 제목을 붙인다. 도구 결과가 없으면 인용도
  출처 줄도 없다.
- 길이: 출처 줄을 뺀 상한 안의 마지막 문장 끝에서 자른다(문장 중간 하드컷 없음).
- guard: 공통 금지 패턴 → 재생성 1회 → 그 그룹의 notice. 토론방 대화·다른 참여자 정보는 보지 않는다.
- 요청·응답 메시지 저장, assistant_calls, 60회 한도(429)는 api가 한다. 같은 세션 호출은 세션
  Lock으로
  하나씩 돌고, 자기 대화(thread `assistant:{session_id}`)는 프로세스 메모리에 최근 몇 턴만 둔다.
"""

import asyncio
import logging
import re
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.agent.catalog import Catalog
from app.agent.guard import guard_from_prompts
from app.agent.llm import BaseLLM, LLMUnavailable, Trace, Usage
from app.agent.mcp_client import ASSISTANT_TOOLS, RAG, McpClient, ToolScope, error_code, for_model
from app.agent.runner import AssistantResult
from app.masking import mask

log = logging.getLogger(__name__)

MAX_TOOL_CALLS = 3
QUESTION_CHARS = 2000
HISTORY_TURNS = 6
SOURCE_PREFIX = "출처: "
FALLBACK_TEXT = "잠시 후 다시 시도해 주세요."
LOCKED_TEXT = "키워드가 정해지면 자료를 찾을 수 있어요."
MISSING_TEXT = "질문하신 내용은 열린 자료에 없어요."
SPAN_CHARS = 12  # 이 길이 이상 글자 그대로 겹치면 그 자료를 쓴 것으로 본다

# 숫자 토큰(쉼표·소수점 포함)과 바로 뒤 단위 한 글자. 평가셋(tests/eval/assistant_eval.py)의 채점
# 규칙과 같다.
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
UNIT = re.compile(r"[분%개원톤일배명년세곳회㎡주월시만억]")
# 문장 끝: 마침표류(닫는 따옴표·괄호 허용) 뒤 공백·끝, 또는 줄바꿈
SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]」』]*(?=\s|$)|\n")

# 본문에서 손대지 않는 구역: 코드 블록(``` · ~~~ 펜스, 4칸·탭 들여쓴 줄), URL.
# 인라인 코드는 맨 id 치환에서만 보호한다.
FENCE_OR_URL = re.compile(
    r"```.*?```|~~~.*?~~~|^(?: {4}|\t)[^\n]*|https?://\S+", re.DOTALL | re.MULTILINE
)
INLINE_CODE = re.compile(r"`[^`\n]*`")

RagFactory = Callable[[uuid.UUID], AbstractAsyncContextManager[ToolScope]]
ScenarioLoader = Callable[[uuid.UUID], Awaitable[dict[str, Any]]]


def thread_id(session_id: object) -> str:
    return f"assistant:{session_id}"


def mcp_rag(url: str) -> RagFactory:
    """운영 경로: 호출마다 rag-mcp 연결을 열고 어시스턴트 허용 목록으로 고정한다."""

    @asynccontextmanager
    async def open_rag(session_id: uuid.UUID) -> AsyncIterator[ToolScope]:
        async with McpClient({RAG: url}, session_id) as client:
            yield client.scope(ASSISTANT_TOOLS)

    return open_rag


class AssistantGraphRunner:
    def __init__(
        self,
        *,
        llm: BaseLLM,
        rag: RagFactory,
        scenario: ScenarioLoader,
    ) -> None:
        self.llm = llm
        self.rag = rag
        self.scenario = scenario
        self._catalogs: dict[uuid.UUID, Catalog] = {}
        self._history: defaultdict[uuid.UUID, list[dict[str, str]]] = defaultdict(list)
        self._locks: defaultdict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def catalog(self, session_id: uuid.UUID) -> Catalog:
        if session_id not in self._catalogs:
            scenario = await self.scenario(session_id)
            self._catalogs[session_id] = Catalog.from_scenario(scenario)
        return self._catalogs[session_id]

    async def answer(
        self, session_id: uuid.UUID, prompt: str, call_id: uuid.UUID
    ) -> AssistantResult:
        async with self._locks[session_id]:
            return await self._answer(session_id, prompt, call_id)

    async def _answer(
        self, session_id: uuid.UUID, prompt: str, call_id: uuid.UUID
    ) -> AssistantResult:
        catalog = await self.catalog(session_id)
        question = mask(prompt).strip()[:QUESTION_CHARS]
        usage = Usage()
        trace = Trace(str(call_id), str(session_id), "assistant")
        async with self.rag(session_id) as rag:
            loop = _Loop(self.llm, rag, catalog, usage, trace)
            content, cited, fallback = await loop.run(question, self._history[session_id])
        history = self._history[session_id]
        history.extend(
            [{"role": "user", "content": question}, {"role": "assistant", "content": content}]
        )
        del history[: -HISTORY_TURNS * 2]
        body = usage.as_dict()
        body["fallback_used"] = fallback
        return AssistantResult(
            content=content, cited_material_ids=cited, tool_calls=loop.tool_calls, usage=body
        )


class _Loop:
    def __init__(
        self, llm: BaseLLM, rag: ToolScope, catalog: Catalog, usage: Usage, trace: Trace
    ) -> None:
        self.llm = llm
        self.rag = rag
        self.catalog = catalog
        self.usage = usage
        self.trace = trace
        self.guard = guard_from_prompts(catalog.prompts)
        self.tool_calls: list[dict[str, Any]] = []
        self.retrieved: dict[str, str] = {}  # material_id → title(열린 자료만)
        self.texts: dict[str, list[str]] = defaultdict(list)  # material_id → 받은 조각(청크·전문)
        self.history: list[BaseMessage] = []  # 도구 왕복을 포함한 이번 응답의 메시지(재생성용)
        self.locked = False

    def _system(self) -> str:
        return self.catalog.prompts.get("assistant.md", "").strip()

    async def run(
        self, question: str, history: list[dict[str, str]]
    ) -> tuple[str, list[str], bool]:
        messages: list[BaseMessage] = [SystemMessage(self._system())]
        for turn in history[-HISTORY_TURNS * 2 :]:
            cls = HumanMessage if turn["role"] == "user" else AIMessage
            messages.append(cls(turn["content"]))
        messages.append(HumanMessage(question))
        try:
            draft = await self._agent(messages)
        except LLMUnavailable as exc:
            log.warning("assistant llm unavailable: %s", exc)
            return FALLBACK_TEXT, [], True
        if self.locked and not self.retrieved:
            return self.catalog.fixed.get("assistant_locked", LOCKED_TEXT), [], False
        if not draft:
            return FALLBACK_TEXT, [], True
        allowed = [question, *(t["content"] for t in history), *self._pieces()]
        draft = await self._ground_numbers(draft, allowed)
        if not draft:  # 확인되지 않는 수치를 빼고 나니 남는 문장이 없다
            return self.catalog.fixed.get("assistant_missing", MISSING_TEXT), [], False
        hits = self.guard.groups_hit(draft)
        if hits:
            again = await self._regenerate(
                draft, f"앞 답은 금지 표현({', '.join(hits)}) 때문에 쓸 수 없다. 다시 답한다."
            )
            if again is None or self.guard.groups_hit(again):
                key = self.guard.notice_key(hits[0]) or "evaluation"
                return self.catalog.notices.get(key, FALLBACK_TEXT), [], False
            draft = drop_sentences(again, ungrounded_numbers(again, allowed))
            if not draft:
                return self.catalog.fixed.get("assistant_missing", MISSING_TEXT), [], False
        return (*self._cite(draft), False)

    async def _ground_numbers(self, draft: str, allowed: list[str]) -> str:
        """자료·질문에 없는 숫자 → 재생성 1회 → 그래도 남으면 그 수치가 든 문장을 뺀다."""
        bad = ungrounded_numbers(draft, allowed)
        if not bad:
            return draft
        again = await self._regenerate(
            draft,
            f"앞 답의 수치 {', '.join(bad)}는 받은 자료에 없다. 자료 원문에 있는 숫자만 그대로 "
            "쓰고, 자료에서 확인되지 않는 수치는 빼고 다시 답한다. 자료에 답이 없으면 없다고 "
            "말한다.",
        )
        if again is not None:
            draft, bad = again, ungrounded_numbers(again, allowed)
        return drop_sentences(draft, bad) if bad else draft

    async def _regenerate(self, draft: str, reason: str) -> str | None:
        """도구 결과까지 포함한 같은 입력에 앞 답과 고칠 이유를 붙여 한 번 더 받는다."""
        note = HumanMessage(f"[다시 쓰기] {reason}")
        try:
            again = await self.llm.ainvoke(
                [*self.history, AIMessage(draft), note], usage=self.usage, trace=self.trace
            )
        except LLMUnavailable:
            return None
        return str(again.content or "").strip()

    def _pieces(self) -> list[str]:
        return [piece for pieces in self.texts.values() for piece in pieces]

    async def _agent(self, messages: list[BaseMessage]) -> str:
        specs = await self.rag.specs()
        history = self.history = list(messages)
        while True:
            bind = specs if specs and len(self.tool_calls) < MAX_TOOL_CALLS else None
            message = await self.llm.ainvoke(
                history, usage=self.usage, trace=self.trace, tools=bind
            )
            if not (message.tool_calls and bind):
                return str(message.content or "").strip()
            history.append(message)
            for call in message.tool_calls:
                args = dict(call.get("args") or {})
                if len(self.tool_calls) >= MAX_TOOL_CALLS:
                    result = {"ok": False, "error": {"code": "forbidden", "message": "tool limit"}}
                else:
                    result = await self.rag.call(call["name"], args)
                    self.usage.tool_calls += 1
                    self.tool_calls.append({"name": call["name"], **args})
                    self._collect(call["name"], result)
                history.append(ToolMessage(for_model(result), tool_call_id=call["id"]))

    def _collect(self, name: str, result: dict[str, Any]) -> None:
        if error_code(result) == "not_released":
            self.locked = True
            return
        data = result.get("data") if result.get("ok") else None
        items = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and item.get("material_id"):
                self.retrieved.setdefault(
                    item["material_id"], item.get("title") or item["material_id"]
                )
                text = item.get("chunk") or item.get("content") or ""
                if text:
                    self.texts[item["material_id"]].append(str(text))

    def _cite(self, text: str) -> tuple[str, list[str]]:
        """쓴 자료만 인용하고, 본문에 제목이 없는 인용 자료는 출처 줄로 붙인다. 출처 줄 자리를 뺀
        상한 안의 문장 끝에서 자른다. 쓴 자료가 없으면 인용도 출처 줄도 없다.
        """
        cited = self._used(text)  # id 표기를 지우기 전에 본다
        body = hide_ids(text, self.retrieved)
        line = ""
        for _ in range(3):  # 자르면서 제목이 떨어져 나가면 출처 줄이 길어지므로 다시 맞춘다
            missing = [self.retrieved[m] for m in cited if self.retrieved[m] not in body]
            line = SOURCE_PREFIX + ", ".join(missing) if missing else ""
            room = self.guard.max_chars - (len(line) + 1 if line else 0)
            if len(body) <= room:
                break
            body = trim_sentences(body, room)
        return (f"{body}\n{line}" if line else body), cited

    def _used(self, body: str) -> list[str]:
        """받은 자료 중 답에 쓴 것: 제목·id를 적었거나, 숫자(단위 포함)나 12자 이상 구간이 그 자료
        조각에 글자 그대로 있는 자료. 받은 순서."""
        numbers = {v + u for v, u in _numbers(body) if u or len(v) >= 2}
        windows = _windows(body)
        used = []
        for material_id, title in self.retrieved.items():
            pieces = self.texts.get(material_id) or []
            if title in body or material_id in body:
                used.append(material_id)
            elif numbers and any(
                v + u in numbers or (u and v in numbers) for p in pieces for v, u in _numbers(p)
            ):
                used.append(material_id)
            elif windows and any(w in _squash(p) for p in pieces for w in windows):
                used.append(material_id)
        return used


def _numbers(text: str) -> list[tuple[str, str]]:
    """(숫자, 바로 뒤 단위 글자 또는 "") 목록. 쉼표는 지운다."""
    out = []
    for found in NUMBER.finditer(text):
        value = found.group(0).rstrip(",.").replace(",", "")
        rest = text[found.end() : found.end() + 2].lstrip()
        unit = rest[:1] if UNIT.match(rest[:1]) else ""
        out.append((value, unit))
    return out


def ungrounded_numbers(text: str, sources: list[str]) -> list[str]:
    """답의 숫자 중 어느 원천(자료 조각·질문·대화 이력)에도 없는 것(단위 포함 표기).

    단위가 붙은 네 자리 미만 숫자는 (숫자, 단위) 쌍으로 비교한다(25분을 5분으로 쓰는 오기). 단위
    없는 숫자는 두 자리 이상만 본다(목록 번호 제외). 표 칸처럼 단위 없이 나온 숫자는 단위를 붙여
    써도 된다. 인용 표기·출처 줄은 숫자가 없어 영향이 없다.
    """
    pairs = {pair for source in sources for pair in _numbers(source)}
    values = {v for v, _ in pairs}
    bare = {v for v, u in pairs if not u}
    out = []
    for value, unit in _numbers(text):
        if unit and len(value) < 4:
            ok = (value, unit) in pairs or value in bare
        elif len(value) >= 2:
            ok = value in values
        else:
            continue
        if not ok and value + unit not in out:
            out.append(value + unit)
    return out


def drop_sentences(text: str, bad: list[str]) -> str:
    """bad(숫자+단위 표기)가 든 문장을 뺀다. 줄 단위로 나누고 줄 안은 문장 끝으로 나눈다."""
    if not bad:
        return text
    kept_lines = []
    for line in text.split("\n"):
        parts = [p for p in _sentences(line) if not any(_has_number(p, b) for b in bad)]
        kept_lines.append(" ".join(parts).strip())
    return "\n".join(line for line in kept_lines if line.strip()).strip()


def _has_number(sentence: str, token: str) -> bool:
    return any(v + u == token for v, u in _numbers(sentence))


def _sentences(line: str) -> list[str]:
    out, last = [], 0
    for end in SENTENCE_END.finditer(line):
        out.append(line[last : end.end()].strip())
        last = end.end()
    out.append(line[last:].strip())
    return [s for s in out if s]


def trim_sentences(text: str, limit: int) -> str:
    """limit 안의 마지막 문장 끝(또는 줄 끝)에서 자른다. 문장 끝이 없으면 마지막 공백, 그것도 없으면
    limit에서 자른다."""
    body = text.strip()
    if len(body) <= limit:
        return body
    head = body[: limit + 1]
    ends = [m.end() for m in SENTENCE_END.finditer(head) if m.end() <= limit]
    if ends:
        return body[: ends[-1]].rstrip()
    cut = head.rfind(" ", 0, limit + 1)
    return body[: cut if cut > 0 else limit].rstrip()


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _windows(body: str, size: int = SPAN_CHARS) -> list[str]:
    flat = _squash(body)
    return [flat[i : i + size] for i in range(0, max(len(flat) - size + 1, 0))]


def hide_ids(text: str, titles: dict[str, str]) -> str:
    """지원자에게 보이는 본문에서 이번 응답이 받은 자료 id(titles의 키)만 지운다.

    - "material_id: id", "(material_id=`id`)", {"material_id": "id"}, "cited_material_ids": [...]
      같은 표기는 표기째 지운다(값이 모두 아는 id일 때만). 공백은 지운 자리 주변만 정리한다.
    - 표기 없이 남은 아는 id는 자료 제목으로 바꾼다.
    - 코드 블록·URL 안은 그대로 두고, 인라인 코드 안의 맨 id도 그대로 둔다. 인용 id는
      cited_material_ids 필드로만 나간다.
    """
    if not titles:
        return text
    ids = "|".join(re.escape(m) for m in sorted(titles, key=len, reverse=True))
    value = rf"[\"'`]?(?:{ids})[\"'`]?"
    label = (
        rf"[\"'`]?(?:cited_)?material_ids?[\"']?[ \t]*[:=][ \t]*"
        rf"(?:\[[ \t]*{value}(?:[ \t]*,[ \t]*{value})*[ \t]*\]|{value})(?![A-Za-z0-9_\-])"
    )
    labelled = re.compile(
        rf"(?P<pre>[ \t]*)(?:\([ \t]*{label}[ \t]*\)|\{{[ \t]*{label}[ \t]*\}}|{label})"
        r"(?P<comma>[ \t]*,)?(?P<post>[ \t]*)"
    )
    bare = re.compile(rf"(?<![A-Za-z0-9_\-/.])(?:{ids})(?![A-Za-z0-9_\-])")

    def dropper(line_start: bool):
        def drop(match: re.Match[str]) -> str:
            """표기와 그 앞뒤 공백만 바꾼다(줄 처음이면 뒤 쉼표까지, 문장 중간이면 공백 하나)."""
            line = match.string
            pre, post = match.group("pre"), match.group("post")
            if line_start and not line[: match.start()].strip():
                return ""
            if match.group("comma"):
                return "," + post
            nxt = line[match.end() : match.end() + 1]
            return "" if not nxt or nxt in ".,!?)" else (pre or post)

        return drop

    def outside(pattern: re.Pattern[str], body: str, fn) -> str:
        """pattern 구역은 그대로 두고 나머지 조각에 fn(조각, 줄 처음에서 시작하는지)."""
        out, last = [], 0
        for found in pattern.finditer(body):
            out.append(fn(body[last : found.start()], last == 0 or body[last - 1] == "\n"))
            out.append(found.group(0))
            last = found.end()
        out.append(fn(body[last:], last == 0 or body[last - 1] == "\n"))
        return "".join(out)

    def strip_labels(chunk: str, line_start: bool) -> str:
        lines = chunk.split("\n")
        kept = []
        for i, line in enumerate(lines):
            new = labelled.sub(dropper(i > 0 or line_start), line)
            whole = (i > 0 or line_start) and i < len(lines) - 1
            if whole and line.strip() and not new.strip():
                continue  # 표기만 있던 줄은 줄째 없앤다
            kept.append(new)
        return "\n".join(kept)

    def plain(chunk: str, _line_start: bool) -> str:
        return bare.sub(lambda m: titles[m.group(0)], chunk)

    body = outside(FENCE_OR_URL, text, strip_labels)
    body = outside(FENCE_OR_URL, body, lambda c, _s: outside(INLINE_CODE, c, plain))
    return body.strip()
