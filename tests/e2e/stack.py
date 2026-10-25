"""러너가 앱 프로세스를 직접 띄우는 실 스택(장애 주입용).

인프라(postgres·rabbitmq·rag-mcp)는 `make up SCOPE=infra`로 떠 있어야 한다. 러너는 이 worktree의
api·debate-mcp·assessment-mcp·report-worker를 하위 프로세스로 띄우고, report-worker의
`API_INTERNAL_URL`을 러너 안 프록시(faults.Proxy)로 돌린다. 끝나면 모든 앱 프로세스를 종료한다.
web은 쓰지 않으므로 띄우지 않는다.

주입 방식(서비스 쪽 약속은 docs/guides/testing.md "러너가 기대는 계약과 장애 주입 훅"):
| 장애 | 방식 |
|---|---|
| 큐 DLQ | 프록시가 워커의 `POST /report/draft`를 3회 503 → 재시도 3회 실패 → DLQ |
| 페르소나 LLM timeout | api를 `LLM_FAULT=persona:timeout:3`으로 재기동(호출 3회 타임아웃) |
| 평가 LLM timeout | 워커를 `LLM_FAULT=evaluator:timeout:1`로 재기동 |
"""

import asyncio
import contextlib
import os
import signal
import socket
import subprocess
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from e2e.client import REPO_ROOT, Target, env
from e2e.faults import Proxy

STARTUP_SECONDS = 120
SERVICES = {
    "api": ("services/api", "app.run", "http://127.0.0.1:8000/health"),
    "debate-mcp": ("services/debate-mcp", "debate_mcp", "http://127.0.0.1:9101/health"),
    "assessment-mcp": ("services/assessment-mcp", "assessment_mcp", "http://127.0.0.1:9103/health"),
    "report-worker": ("services/report-worker", "report_worker", None),
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalStack:
    def __init__(self, log_dir: Path) -> None:
        self.env = env()
        self.log_dir = log_dir
        self.internal_url = "http://127.0.0.1:8001"
        self.proxy = Proxy(self.internal_url)
        self.procs: dict[str, subprocess.Popen] = {}
        self.extra_env: dict[str, dict[str, str]] = {}
        self.servers: list[uvicorn.Server] = []
        self.tasks: list[asyncio.Task] = []
        self.proxy_url = ""

    @property
    def target(self) -> Target:
        return Target.from_env()

    async def start(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        port = free_port()
        server = uvicorn.Server(
            uvicorn.Config(self.proxy, host="127.0.0.1", port=port, log_level="warning")
        )
        self.servers.append(server)
        self.tasks.append(asyncio.create_task(server.serve()))
        self.proxy_url = f"http://127.0.0.1:{port}"
        for name in SERVICES:
            await self.spawn(name)

    async def spawn(self, name: str) -> None:
        folder, module, health = SERVICES[name]
        child_env = {
            **os.environ,
            "AGENT_RUNNER": "session",
            "ENABLE_TEST_API": "1",
            "LLM_FAKE": "1",
            **self.extra_env.get(name, {}),
        }
        if name == "report-worker":
            child_env["API_INTERNAL_URL"] = self.proxy_url
        if self.env.get("E2E_DATABASE_URL"):
            child_env["DATABASE_URL"] = self.env["E2E_DATABASE_URL"]
        child_env.pop("VIRTUAL_ENV", None)
        log = (self.log_dir / f"{name}.log").open("a")
        self.procs[name] = subprocess.Popen(
            ["uv", "run", "--quiet", "python", "-m", module],
            cwd=REPO_ROOT / folder,
            env=child_env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        if health:
            await self._wait(health)

    async def _wait(self, url: str) -> None:
        async with httpx.AsyncClient() as client:
            for _ in range(STARTUP_SECONDS * 4):
                with contextlib.suppress(httpx.TransportError):
                    if (await client.get(url)).status_code == 200:
                        return
                await asyncio.sleep(0.25)
        raise TimeoutError(url)

    def _stop(self, name: str, sig: int = signal.SIGTERM) -> None:
        proc = self.procs.pop(name, None)
        if proc and proc.poll() is None:
            proc.send_signal(sig)
            proc.wait(timeout=30)

    async def restart(self, name: str, **child_env: str) -> None:
        self._stop(name)
        if child_env:
            self.extra_env.setdefault(name, {}).update(child_env)
        await self.spawn(name)

    # ---- 장애 주입 약속(faults.py) -------------------------------------------------------

    async def restart_api(self) -> None:
        await self.restart("api")

    async def fail_report_draft(self, times: int) -> None:
        self.proxy.fail("report_draft", times)

    async def set_llm_fault(self, role: str, times: int) -> None:
        service = "api" if role == "persona" else "report-worker"
        await self.restart(service, LLM_FAULT=f"{role}:timeout:{times}")

    def worker_log(self) -> list[dict[str, Any]]:
        return list(self.proxy.log)

    def reset_faults(self) -> None:
        self.proxy.rules.clear()
        self.proxy.log.clear()
        self.extra_env.clear()

    async def stop(self) -> None:
        for name in list(self.procs):
            self._stop(name)
        for server in self.servers:
            server.should_exit = True
        await asyncio.gather(*self.tasks, return_exceptions=True)
