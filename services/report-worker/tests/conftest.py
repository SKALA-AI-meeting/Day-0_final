"""워커 테스트 공용 픽스처: 공용 계약 픽스처(tests/fixtures/contracts/)의 세션 sess-1, FakeLLM,
가짜 assessment-mcp, 가짜 내부 API, 메모리 체크포인터로 조립한 그래프와 소비자.

가짜 assessment와 가짜 api는 같은 평가 저장소를 본다(api의 evaluation_runs·evaluations 행).
draft·final 규칙은 docs/contracts/internal.md 내부 REST와 같다(final 해시가 draft와 다르면 409
draft_mismatch).
"""

import copy
import hashlib
import importlib.util
import json
import os
import re
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from report_worker.api import ApiError
from report_worker.config import REPO_ROOT
from report_worker.consumer import ReportWorker
from report_worker.graph import Deps, build_graph
from report_worker.llm import Completion

CONTRACTS = REPO_ROOT / "tests" / "fixtures" / "contracts"
SCENARIOS = REPO_ROOT / "scenarios"


def _version_module():
    spec = importlib.util.spec_from_file_location("scenarios_version", SCENARIOS / "version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prompts_copy(folder) -> dict[str, str]:
    """api `scenario.assemble`의 `prompts`와 같은 모양(프롬프트 키 → 본문, fixtures 제외)."""
    files = _version_module().prompt_files(folder, copied_only=True)
    return {key: path.read_text(encoding="utf-8") for key, path in files.items()}


def load(name: str) -> Any:
    return json.loads((CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))


def _load_module(name: str, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = _load_module("contract_shape", REPO_ROOT / "tests" / "contract_shape.py")


def content_hash(content: dict[str, Any]) -> str:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def element_outputs() -> dict[str, dict[str, Any]]:
    """계약 픽스처 평가(internal_evaluations)를 요소별 LLM 출력 JSON으로 나눈다(워커 검증 통과).

    근거는 판단 호출 출력 형식 `evidence: [{ref, scene}]`로 바꾸고, 지원자 메시지에는 그 메시지가
    드는 장면 id(핵심 먼저)를 단다. 수준이 있는 행의 `summary_line`은 그 행의 판단 이유다.
    """
    from report_worker.scenes import build_scenes

    saved = load("internal_evaluations")
    rubric = load("internal_rubric")
    scenes = build_scenes(load("session_record"), rubric)
    owner = {d["dimension_id"]: e["element"] for e in rubric["elements"] for d in e["dimensions"]}
    outputs = {e["element"]: {"dimensions": []} for e in saved["elements"]}
    for row in saved["dimensions"]:
        dim = scenes[row["dimension_id"]]
        evidence = []
        for ref in row["evidence_refs"]:
            holding = dim.holding(ref[4:]) if ref.startswith("msg:") else []
            evidence.append({"ref": ref, "scene": holding[0]} if holding else {"ref": ref})
        out = {k: v for k, v in row.items() if k != "evidence_refs"}
        out = {"no_record_reason": None, **out, "evidence": evidence}
        if row["level"] is not None:
            out["summary_line"] = row["rationale"].rstrip(".")
        outputs[owner[row["dimension_id"]]]["dimensions"].append(out)
    return outputs


ELEMENT_LINE = re.compile(r"^\[요소\] (\w+)", re.M)


class FakeLLM:
    """요소별 출력. `script[element]`가 있으면 호출마다 앞에서 하나씩 꺼낸다(문자열·객체·예외)."""

    model = "gpt-4o-mini"
    temperature = 0.1

    def __init__(self, outputs: dict[str, Any] | None = None) -> None:
        self.outputs = outputs or element_outputs()
        self.script: dict[str, list[Any]] = {}
        self.calls: list[tuple[str, str, str]] = []  # (element, system, user)
        self.fail_next: list[Exception] = []

    async def complete(self, system: str, user: str) -> Completion:
        element = ELEMENT_LINE.search(user).group(1)
        self.calls.append((element, system, user))
        if self.fail_next:
            raise self.fail_next.pop(0)
        queue = self.script.get(element)
        out = queue.pop(0) if queue else self.outputs[element]
        if isinstance(out, Exception):
            raise out
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        return Completion(text=text, tokens_in=100, tokens_out=50)


class Store:
    """api DB의 평가 실행 행(run_id → EvaluationSave 본문)."""

    def __init__(self) -> None:
        self.evaluations: dict[str, dict[str, Any]] = {}


class FakeAssessment:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.record = load("session_record")
        self.rubric = load("internal_rubric")
        self.contributions = load("internal_contributions")
        self.calls: list[str] = []
        self.saved: list[dict[str, Any]] = []

    async def get_session_record(self, session_id: str) -> dict[str, Any]:
        self.calls.append("get_session_record")
        return copy.deepcopy(self.record)

    async def get_rubric(self, session_id: str) -> dict[str, Any]:
        self.calls.append("get_rubric")
        return copy.deepcopy(self.rubric)

    async def get_contributions(self, session_id: str) -> dict[str, Any]:
        self.calls.append("get_contributions")
        return copy.deepcopy(self.contributions)

    async def get_transcript_span(
        self, session_id: str, message_id: str, before: int = 5, after: int = 3
    ) -> dict[str, Any]:
        self.calls.append("get_transcript_span")
        messages = self.record["messages"]
        index = next(i for i, m in enumerate(messages) if m["id"] == message_id)
        return {
            "target": messages[index],
            "before": messages[max(0, index - before) : index],
            "after": messages[index + 1 : index + 1 + after],
        }

    async def save_evaluation(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append("save_evaluation")
        self.saved.append(copy.deepcopy(body))
        run_id = body["run_id"]
        if run_id in self.store.evaluations:
            return {"run_id": run_id, "existing": True, "saved": 0}
        self.store.evaluations[run_id] = copy.deepcopy(body)
        return {"run_id": run_id, "existing": False, "saved": len(body["dimensions"])}


class FakeApi:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.session = load("internal_session")
        self.session["status"] = "completed"
        self.session["position"]["name"] = "재난 대응 기획자"
        # 채용 건 생성 때 복사되는 평가 프롬프트(계약 픽스처는 생략해 둔다)
        self.session["position"]["scenario"]["prompts"] = prompts_copy(
            SCENARIOS / "missions" / "debate" / "prompts"
        )
        self.drafts: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        self.finals: list[dict[str, Any]] = []
        self.failed: list[str] = []
        self.down = False  # True면 쓰기 호출이 연결 실패
        self.evaluation_reads = 0

    def _check(self) -> None:
        if self.down:
            raise httpx.ConnectError("api down")

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.session)

    async def assistant_calls(self, session_id: str) -> list[dict[str, Any]]:
        rows = load("internal_assistant_calls")
        rows[0]["request"] = "도심 침수 사례에서 센서 경보 효과를 알려 줘"
        rows[0]["response"] = "자료에 센서 경보로 피해가 줄었다는 내용이 있어요."
        return rows

    async def get_evaluation(self, session_id: str, run_id: str) -> dict[str, Any] | None:
        self.evaluation_reads += 1
        body = self.store.evaluations.get(run_id)
        if body is None:
            return None
        failed = any(d["no_record_reason"] == "evaluate_failed" for d in body["dimensions"])
        keys = ("run_id", "rubric_version", "prompt_versions", "model", "elements", "dimensions")
        return {
            **{k: copy.deepcopy(body[k]) for k in keys},
            "has_failed": failed,
            "created_at": "x",
        }

    async def save_draft(self, session_id, content, usage):
        self._check()
        self.drafts.append((copy.deepcopy(content), usage))
        return {"status": "report_ready"}

    async def save_final(self, session_id, content):
        self._check()
        if content_hash(content) != content_hash(self.drafts[-1][0]):
            raise ApiError(409, "conflict", "draft_mismatch")
        self.finals.append(copy.deepcopy(content))
        return {"status": "reviewed", "reviewed_at": "2026-10-21T03:10:00Z"}

    async def report_failed(self, session_id, error):
        self._check()
        self.failed.append(error)
        return {"status": "failed"}


class Harness:
    def __init__(self) -> None:
        self.store = Store()
        self.llm = FakeLLM()
        self.assessment = FakeAssessment(self.store)
        self.api = FakeApi(self.store)
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.sleeps: list[float] = []
        self.new_graph()

    def new_graph(self) -> None:
        """새 체크포인터로 그래프를 다시 만든다(프로세스 재시작·체크포인트 유실 흉내)."""
        deps = Deps(self.api, self.assessment, self.llm)
        self.graph = build_graph(deps, InMemorySaver())

        async def publish(queue: str, body: dict[str, Any]) -> None:
            self.published.append((queue, body))

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.worker = ReportWorker(self.graph, self.api, publish, sleep=sleep)

    async def generate(self, eval_round: int = 1, attempt: int = 1) -> None:
        body = {"session_id": "sess-1", "attempt": attempt, "eval_round": eval_round}
        await self.worker.handle("report.generate", body, self.worker.generate)

    async def approve(self, eval_round: int = 1, attempt: int = 1) -> None:
        body = {"session_id": "sess-1", "attempt": attempt, "eval_round": eval_round}
        await self.worker.handle("report.approve", body, self.worker.approve)


@pytest.fixture
def h() -> Harness:
    return Harness()


def merged_report(draft: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """api가 GET 때 병합하는 필드를 더해 Report 계약 형태로 만든다(조정 없음)."""
    report = copy.deepcopy(draft)
    for element in report["elements"]:
        element["adjusted_level"] = None
    for dimension in report["dimensions"]:
        dimension.update(adjusted_level=None, adjusted_no_record_reason=None, adjust_memo=None)
    report["debrief"]["survey"] = None
    report.update(
        proposal=record["proposal"],
        review=None,
        notes=[],
        connection_gaps=record["session"]["connection_gaps"],
        reviewed_at=None,
        purge_at=None,
    )
    return report


def pytest_collection_modifyitems(config, items):
    """`live` 테스트(실제 API·과금)는 `ALLOW_API=1`일 때만 돈다.

    기본 실행(`-m`에 live 선택 없음)은 live를 skip한다. `-m live`처럼 live를 명시해 골랐는데
    동의가 없으면 조용히 0으로 끝나지 않고 세션을 실패로 끝낸다(수집만 하는 `--collect-only`는
    호출이 없으므로 예외: docs_gen이 live 목록을 센다).
    """
    if os.environ.get("ALLOW_API") == "1":
        return
    markexpr = config.getoption("-m", default="") or ""
    selected = re.search(r"\blive\b", markexpr) and not re.search(r"\bnot\s+live\b", markexpr)
    if selected and not config.getoption("collectonly"):
        pytest.exit(
            f"-m {markexpr!r}: live 테스트는 실제 API 호출(과금)이다. ALLOW_API=1을 명시해야 돈다",
            returncode=1,
        )
    skip = pytest.mark.skip(reason="실제 API 호출(과금). ALLOW_API=1을 명시해야 돈다")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
