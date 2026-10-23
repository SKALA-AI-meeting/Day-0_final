"""평가 에이전트 LLM 호출(`MODEL_EVALUATOR`, temperature 0.1, JSON 출력).

전송 오류·타임아웃은 재시도 2회(1·3초) 뒤 LLMUnavailable로 올린다. 평가는 폴백 문장이 없다:
아무것도 저장하지 않고 작업을 실패시켜 큐 재시도(attempt+1)에 맡긴다(docs/services/report-worker.md
4단계).
호출마다 사용량을 모아 reports.usage로 남긴다. 테스트는 같은 Protocol의 FakeLLM을 쓴다.
"""

import asyncio
import json
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from report_worker.config import (
    EVALUATOR_RETRY_DELAYS,
    EVALUATOR_TEMPERATURE,
    EVALUATOR_TIMEOUT,
    key_for,
)

log = logging.getLogger("report_worker.llm")


class LLMUnavailable(Exception):
    """최초 포함 3회 모두 전송 실패·타임아웃. 큐 재시도 신호."""


def parse_fault(value: str | None) -> dict[str, int]:
    """`LLM_FAULT=<role>:timeout:<N>` → {role: N}. 형식이 다르면 무시한다(e2e 장애 주입 전용)."""
    parts = (value or "").strip().split(":")
    if len(parts) == 3 and parts[1] == "timeout" and parts[2].isdigit():
        return {parts[0]: int(parts[2])}
    return {}


# 프로세스 시작 때 한 번 읽는다. 남은 횟수는 프로세스 메모리에서 줄어든다.
FAULTS = parse_fault(os.environ.get("LLM_FAULT"))


def take_fault(role: str) -> bool:
    if FAULTS.get(role, 0) <= 0:
        return False
    FAULTS[role] -= 1
    return True


@dataclass
class Completion:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0


class LLM(Protocol):
    model: str
    temperature: float

    async def complete(self, system: str, user: str) -> Completion: ...


def empty_usage(model: str) -> dict:
    return {
        "model": model,
        "llm_calls": 0,
        "element_retries": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "latency_ms": 0,
    }


async def complete_tracked(llm: LLM, system: str, user: str, usage: dict) -> str:
    # 장애 주입은 재시도까지 소진한 평가 호출 1회로 센다(실제 호출 없이 큐 재시도 경로로)
    if take_fault("evaluator"):
        raise LLMUnavailable("LLM_FAULT: evaluator timeout")
    started = time.monotonic()
    usage["llm_calls"] += 1
    try:
        result = await llm.complete(system, user)
    finally:
        usage["latency_ms"] += int((time.monotonic() - started) * 1000)
    usage["tokens_in"] += result.tokens_in
    usage["tokens_out"] += result.tokens_out
    return result.text


class OpenAILLM:
    def __init__(self, model: str, env: Mapping[str, str]) -> None:
        from langchain_openai import ChatOpenAI

        key, base_url = key_for(model, env)
        self.model = model
        self.temperature = EVALUATOR_TEMPERATURE
        # 재시도는 complete가 센다(SDK 자체 재시도는 끈다)
        self._chat = ChatOpenAI(
            model=model,
            api_key=key,
            base_url=base_url,
            temperature=self.temperature,
            timeout=EVALUATOR_TIMEOUT,
            max_retries=0,
            model_kwargs={"response_format": {"type": "json_object"}},
        )

    async def complete(self, system: str, user: str) -> Completion:
        last: Exception | None = None
        for attempt in range(len(EVALUATOR_RETRY_DELAYS) + 1):
            if attempt:
                await asyncio.sleep(EVALUATOR_RETRY_DELAYS[attempt - 1])
            try:
                reply = await self._chat.ainvoke([("system", system), ("user", user)])
            except Exception as exc:
                log.warning("evaluator llm failed (attempt %d): %r", attempt + 1, exc)
                last = exc
                continue
            meta = reply.usage_metadata or {}
            return Completion(
                text=str(reply.content).strip(),
                tokens_in=meta.get("input_tokens", 0),
                tokens_out=meta.get("output_tokens", 0),
            )
        raise LLMUnavailable(f"evaluator llm failed after retries: {last!r}")


class FakeEvaluatorLLM:
    """`LLM_FAKE=1`: 호출 없이 요소 척도의 차원마다 고정 출력(비용 0, 키 불필요).

    `[사실]`의 그 차원 핵심 장면에 지원자 메시지가 있으면 첫 메시지를 근거(장면 `core`)로 수준
    3·medium, 핵심 장면이 없으면 event_not_fired, 있는데 지원자 메시지가 없으면 no_activity.
    워커 검증(근거 형식·개수·금지어·사실 모순)을 그대로 통과하고, 같은 입력에는 같은 출력을 낸다.
    """

    model = "fake"
    temperature = 0.0

    async def complete(self, system: str, user: str) -> Completion:
        facts = json.loads(user.split("\n\n[사실]\n", 1)[1].split("\n\n[세션 기록]", 1)[0])
        dimensions = []
        for dimension in facts["dimensions"]:
            core = dimension["scenes"][0]
            if core["candidate_messages"]:
                row = {
                    "level": 3,
                    "evidence": [{"ref": core["candidate_messages"][0], "scene": "core"}],
                    "rationale": "고정 출력: 핵심 장면 지원자 메시지 한 건을 근거로 중간 수준.",
                    "summary_line": "고정 출력: 핵심 장면 지원자 메시지 한 건으로 판단함",
                    "confidence": "medium",
                }
            else:
                reason = "no_activity" if core["exists"] else "event_not_fired"
                row = {
                    "level": None,
                    "no_record_reason": reason,
                    "evidence": [],
                    "rationale": "고정 출력: 핵심 장면에 지원자 메시지가 없다.",
                    "confidence": None,
                }
            dimensions.append({"dimension_id": dimension["dimension_id"], **row})
        return Completion(text=json.dumps({"dimensions": dimensions}, ensure_ascii=False))
