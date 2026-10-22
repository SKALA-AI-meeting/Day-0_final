"""e2e 러너: 실제 서비스를 상대로 대본을 끝까지 돌리고 단언·기록을 남긴다.

형식과 단언은 docs/guides/testing.md e2e 절, step 키는 scenarios/e2e/README.md.

    uv run --project services/api python tests/e2e/run.py --script all_five      # 벽시계 15분
    uv run --project services/api python tests/e2e/run.py --script timeout_all --clock fake
    uv run --project services/api python tests/e2e/run.py --auth                # 단언 8
    uv run --project services/api python tests/e2e/run.py --faults all          # 단언 10

흐름: 관리자 가입·로그인 → 채용 건(15분)·초대 → 입장·동의·시작 전 /state·시작 → WS 연결 →
step 진행 → 제출·자동 제출 → session.ended → report_ready 대기 → 관리자 draft·검토(단언 6)·
final·전체 기록 → 내부 API·DB에서 결과 수집 → 단언(asserts.py) → 기록(records.py).

시계: `real`은 벽시계로 기다리고(실 스택 15분), `fake`는 내부 테스트 API `advance`로 시간을
당긴다. 실패한 step은 멈추지 않고 step_errors에 남긴다(러너는 결함을 가리지 않는다).
"""

import argparse
import asyncio
import contextlib
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# `python tests/e2e/run.py`로 실행할 때도 e2e 패키지를 찾도록 tests/를 경로에 넣는다
sys.path[:0] = [str(Path(__file__).resolve().parents[1])]
from e2e import asserts, catalog, records  # noqa: E402
from e2e import script as scripts  # noqa: E402
from e2e.client import (  # noqa: E402
    Api,
    ApiError,
    Connector,
    Socket,
    Target,
    Transcript,
    env,
    parse_time,
)
from e2e.db import Db, NullDb, PgDb  # noqa: E402

REPORT_WAIT_SECONDS = 120
CARD_WAIT_SECONDS = 60  # 가짜 시계: 카드를 당긴 뒤 관찰이 생기기까지
SETTLE_SECONDS = 40
TYPING_HOLD_SECONDS = 3.0
RECONNECT_SECONDS = 90
PASSWORD = "e2e-password-1234"


class RealClock:
    fake = False

    async def advance(self, runner: "Runner", *, to: str | None = None, seconds: float = 0) -> None:
        return None

    async def wait_minute(self, runner: "Runner", minute: float) -> None:
        while (now := await runner.t_min()) < minute:
            await asyncio.sleep(min(max((minute - now) * 60, 0.05), 1.0))


class FakeClock:
    fake = True

    async def advance(self, runner: "Runner", *, to: str | None = None, seconds: float = 0) -> None:
        body = {"to": to} if to else {"seconds": max(int(seconds), 1)}
        result = await runner.api.json(
            "POST", f"/internal/test/sessions/{runner.sid}/advance", internal=True, json_body=body
        )
        runner.advances.append({"at": time.time(), "seconds": result["shifted_seconds"]})

    async def wait_minute(self, runner: "Runner", minute: float) -> None:
        now = await runner.t_min()
        if minute > now:
            await self.advance(runner, seconds=(minute - now) * 60)


class Runner:
    def __init__(
        self,
        script: dict[str, Any],
        target: Target,
        *,
        clock: str = "real",
        stack: Any = None,
        transports: dict[str, Any] | None = None,
        connector: Connector | None = None,
        db: Db | None = None,
        llm_fake: bool = True,
        report_wait: float = REPORT_WAIT_SECONDS,
        regenerate_on_failure: bool = False,
        typing_hold: float = TYPING_HOLD_SECONDS,
        poll: float = 0.5,
        admin_email: str | None = None,
    ) -> None:
        self.script = script
        self.target = target
        self.clock = FakeClock() if clock == "fake" else RealClock()
        self.stack = stack
        self.llm_fake = llm_fake
        self.report_wait = report_wait
        self.regenerate_on_failure = regenerate_on_failure
        self.typing_hold = typing_hold
        self.poll = poll
        self.admin_email = admin_email or env().get(
            "E2E_ADMIN_EMAIL", f"e2e-{int(time.time())}@example.com"
        )
        self.db: Db = db or (PgDb(target.database_url) if target.database_url else NullDb())
        self.transcript = Transcript()
        self.api = Api(target, self.transcript, transports=transports)
        self.sock = Socket(self.transcript, connector)
        self.segments: list[int] = []  # WS 연결마다 sock.frames 시작 위치(seq는 연결마다 다시 센다)
        self.sid = ""
        self.invite_token = ""
        self.jwt = ""
        self.admin_token = ""
        self.position: dict[str, Any] = {}
        self.entry: dict[str, Any] = {}
        self.started: dict[str, Any] = {}
        self.states: dict[str, Any] = {}
        self.advances: list[dict[str, Any]] = []
        self.typing_intervals: list[tuple[float, float]] = []
        self.step_errors: list[str] = []
        self.unfired: set[str] = set()
        self.restarts: list[dict[str, Any]] = []
        self.review_result: dict[str, Any] = {}
        self.regenerated = False
        self.failed_seen = False
        self.eval_before_regen: dict[str, Any] | None = None
        self.last_t = 0.0
        self.step_marks: list[int] = []
        self.materials_before: dict[str, Any] = {}
        self.started_wall = time.time()
        self.voice_mode = False
        self.voice_task = None

    # ---- 준비 -----------------------------------------------------------------------------

    async def admin_login(self) -> None:
        body = {
            "email": self.admin_email,
            "password": PASSWORD,
            "company_name": "e2e-company",
        }
        response = await self.api.call("POST", "/admin/auth/signup", json_body=body)
        if response.status_code == 409:
            response = await self.api.call("POST", "/admin/auth/login", json_body=body)
        response.raise_for_status()
        self.admin_token = response.json()["token"]

    async def create_session(self) -> None:
        minutes = self.script.get("minutes", 15)
        self.position = await self.api.json(
            "POST",
            "/admin/positions",
            token=self.admin_token,
            json_body={"name": f"e2e-{self.script['name']}", "session_minutes": minutes},
        )
        invite = await self.api.json(
            "POST",
            f"/admin/positions/{self.position['id']}/invites",
            token=self.admin_token,
            json_body={"alias": f"e2e-{int(time.time() * 1000)}", "email": "e2e@example.com"},
        )
        self.sid = invite["session_id"]
        self.invite_token = invite["invite_url"].rstrip("/").split("/")[-1]

    async def enter(self) -> None:
        self.entry = await self.api.json("GET", f"/s/{self.invite_token}")
        consent = await self.api.json(
            "POST",
            f"/s/{self.invite_token}/consent",
            json_body={"display_name": self.entry.get("default_display_name") or "지원자"},
        )
        self.jwt = consent["session_token"]
        self.states["pre_start"] = await self.state()
        self.started = await self.api.json("POST", f"/s/{self.invite_token}/start", token=self.jwt)
        self.materials_before = await self.api.json(
            "GET", f"/sessions/{self.sid}/materials", token=self.jwt
        )
        await self.connect()
        if self.voice_mode:
            self.voice_task = asyncio.create_task(self.voice_pump())

    async def voice_pump(self):
        # API 러너의 재생 확인은 합성한다. 실제 media ended는 Playwright가 검증한다.
        base = f"/sessions/{self.sid}/voice"
        while not self.ended():
            try:
                state = await self.api.json("GET", base, token=self.jwt, log=False)
                if state["state"] == "AI_SPEAKING" and state["items"]:
                    item = state["items"][0]
                    body = {
                        "turn_id": state["turn_id"],
                        "epoch": state["epoch"],
                        "job_id": item["id"],
                    }
                    if item["status"] == "ready":
                        await self.api.json(
                            "POST",
                            base + "/playback",
                            token=self.jwt,
                            json_body={**body, "outcome": "start"},
                        )
                        await asyncio.sleep(item["duration_ms"] / 1000)
                        await self.api.json(
                            "POST",
                            base + "/playback",
                            token=self.jwt,
                            json_body={**body, "outcome": "ended"},
                        )
            except ApiError:
                pass  # 단계 전환·재연결 fence는 다음 스냅샷으로 복구
            await asyncio.sleep(self.poll)

    async def say_voice(self, text):
        import base64
        import io
        import wave

        base = f"/sessions/{self.sid}/voice"
        # 이 대본은 FakeSpeech 스택 전용이다. 실 Azure 품질 검증으로 해석하지 않는다.
        await self.api.json(
            "POST", "/internal/test/voice-transcript", internal=True, json_body={"content": text}
        )
        end = time.time() + 90
        while time.time() < end:
            state = await self.api.json("GET", base, token=self.jwt, log=False)
            if state["state"] == "USER_READY" and state.get("epoch"):
                break
            await asyncio.sleep(self.poll)
        else:
            raise TimeoutError("voice turn not ready")
        turn = {"turn_id": state["turn_id"], "epoch": state["epoch"]}
        capture = await self.api.json("POST", base + "/capture", token=self.jwt, json_body=turn)
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\0\0" * 1600)
        await self.api.json(
            "POST",
            base + "/audio",
            token=self.jwt,
            json_body={
                **turn,
                "utterance_id": capture["utterance_id"],
                "audio_base64": base64.b64encode(output.getvalue()).decode(),
                "stop_reason": "button",
            },
        )
        while time.time() < end:
            state = await self.api.json("GET", base, token=self.jwt, log=False)
            utterance = state.get("utterance") or {}
            if utterance.get("status") == "ready":
                ack = await self.sock.send_message(
                    utterance["transcript"],
                    voice={
                        "client_msg_id": utterance["id"],
                        "voice_turn_id": turn["turn_id"],
                        "epoch": turn["epoch"],
                    },
                )
                if ack is None:
                    self.step_errors.append("voice say: no ack")
                await self.settle()
                return
            await asyncio.sleep(self.poll)
        raise TimeoutError("voice transcript not ready")

    async def connect(self) -> None:
        self.segments.append(len(self.sock.frames))
        await self.sock.connect(self.target.ws_url(self.sid, self.jwt))

    async def reconnect(self, timeout: float = RECONNECT_SECONDS) -> bool:
        """resume(새 JWT) → /state → WS 다시 연결. 시간 안에 못 하면 False."""
        end = time.time() + timeout
        while time.time() < end:
            with contextlib.suppress(Exception):
                resumed = await self.api.json("POST", f"/s/{self.invite_token}/resume")
                self.jwt = resumed["session_token"]
                self.states["after_reconnect"] = await self.state()
                await self.connect()
                return True
            await asyncio.sleep(self.poll * 2)
        return False

    # ---- 조회 -----------------------------------------------------------------------------

    async def state(self) -> dict[str, Any]:
        return await self.api.json("GET", f"/sessions/{self.sid}/state", token=self.jwt, log=False)

    async def internal(self, path: str = "", **params: Any) -> Any:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        suffix = f"?{query}" if query else ""
        return await self.api.json(
            "GET", f"/internal/sessions/{self.sid}{path}{suffix}", internal=True, log=False
        )

    async def t_min(self) -> float:
        return (await self.internal())["t_min_now"]

    def frames(self, *types: str) -> list[dict[str, Any]]:
        return [f for f in self.sock.frames if not types or f["type"] in types]

    def phase_now(self) -> str | None:
        changed = self.frames("phase.changed")
        if self.frames("session.ended"):
            return "completed"
        # 시작 직후의 phase.changed(reason=start)는 WS 연결 전에 나갈 수 있어 start 응답을 쓴다
        return changed[-1]["payload"]["phase"] if changed else self.started.get("phase")

    def ended(self) -> bool:
        return bool(self.frames("session.ended"))

    async def observed_cards(self) -> set[str]:
        return {o["card_id"] for o in await self.internal("/observations")}

    async def settle(self) -> None:
        """진행 중 그래프 실행과 AI 입력 중 표시가 가라앉을 때까지."""
        end = time.time() + SETTLE_SECONDS
        await asyncio.sleep(self.poll)  # ack 뒤 AI 프레임이 도착할 틈
        while time.time() < end:
            state = await self.state()
            if not state.get("active_runs") and not state.get("typing"):
                return
            await asyncio.sleep(self.poll)

    # ---- 동작 -----------------------------------------------------------------------------

    async def say(self, text: str) -> None:
        if self.voice_mode:
            await self.say_voice(text)
            return
        if not self.sock.open:
            await self.reconnect()
        started = time.time()
        end = started + self.typing_hold
        await self.sock.typing("start")
        while (left := end - time.time()) > 0:
            await asyncio.sleep(min(left, 5.0))
            if end - time.time() > 0:
                await self.sock.typing("start")  # 5초마다 갱신
        await self.sock.typing("stop")  # 전송하면 입력 중이 끝난다
        self.typing_intervals.append((started, time.time()))
        ack = await self.sock.send_message(text)
        if ack is None:
            self.step_errors.append(f"say: no ack for {text[:30]!r}")
        await self.settle()

    async def confirm(self, spec: dict[str, Any]) -> None:
        phase = spec.get("phase") or self.phase_now()
        body = {
            **({"value": spec["value"]} if "value" in spec else {}),  # keyword는 option_key만
            **({"option_key": spec["option_key"]} if "option_key" in spec else {}),
        }
        await self.api.json(
            "POST", f"/sessions/{self.sid}/slots/{phase}/confirm", token=self.jwt, json_body=body
        )
        await self.settle()

    async def proposal(self, fields: dict[str, str]) -> None:
        for _ in range(2):
            current = await self.api.json("GET", f"/sessions/{self.sid}/proposal", token=self.jwt)
            sections = {**current["sections"], **fields}
            response = await self.api.call(
                "PUT",
                f"/sessions/{self.sid}/proposal",
                token=self.jwt,
                json_body={"sections": sections, "revision": current["revision"]},
            )
            if response.status_code == 200:
                return
            if response.status_code != 409:  # 409는 다른 탭 덮어쓰기 방지: 다시 읽고 한 번 더
                response.raise_for_status()

    async def restart(self) -> None:
        before = {m["id"] for m in (await self.state())["messages"]}
        started = time.time()
        if self.stack is not None:
            await self.stack.restart_api()
        elif cmd := env().get("E2E_RESTART_CMD"):
            subprocess.run(cmd, shell=True, check=True)  # noqa: S602 - 운영자가 정한 명령
        else:
            raise RuntimeError("restart needs a managed stack or E2E_RESTART_CMD")
        ok = await self.reconnect()
        after = {m["id"] for m in self.states.get("after_reconnect", {}).get("messages", [])}
        self.restarts.append(
            {
                "reconnected": ok,
                "seconds": round(time.time() - started, 1),
                "ids_before": sorted(before),
                "ids_after": sorted(after),
            }
        )

    # ---- 시각·사건 기다리기 ---------------------------------------------------------------

    async def wait_at(self, at: Any) -> None:
        if isinstance(at, (int, float)):
            await self.clock.wait_minute(self, float(at))
        elif isinstance(at, str) and at.startswith("+"):
            await self.clock.wait_minute(self, self.last_t + float(at[1:]))
        elif at.startswith("card:"):
            await self.wait_card(at.split(":", 1)[1])
        elif at.startswith("phase:"):
            await self.wait_phase(at.split(":", 1)[1])
        elif at.startswith("msg:"):
            await self.wait_message(at.split(":", 1)[1])
        elif at == "ended":
            await self.wait_ended()
        else:
            raise ValueError(f"unknown at {at!r}")

    async def wait_phase(self, phase: str) -> None:
        order = catalog.PHASES
        if self.ended() or self.phase_now() in (phase, "completed"):
            return
        while self.phase_now() != phase and not self.ended():
            if order.index(self.phase_now() or "keyword") > order.index(phase):
                return
            since = len(self.sock.frames)
            if self.clock.fake:
                await self.clock.advance(self, to="deadline")
            frame = await self.sock.wait_for(
                lambda f: f["type"] in ("phase.changed", "session.ended"),
                timeout=await self._phase_wait_seconds(),
                since=since,
            )
            if frame is None:
                self.step_errors.append(f"wait phase:{phase} timed out")
                return

    async def _phase_wait_seconds(self) -> float:
        if self.clock.fake:
            return 30.0
        session = await self.internal()
        deadline = parse_time(session["phase_deadline_at"])
        return max((deadline - datetime.now(UTC)).total_seconds(), 0) + 30

    async def wait_card(self, card_id: str) -> None:
        card_phase = catalog.cards()[card_id]["stage_id"]
        if card_id in await self.observed_cards():
            return
        if self.clock.fake and self.phase_now() == card_phase:
            await self.clock.advance(self, to=f"card:{card_id}")
        patience = CARD_WAIT_SECONDS
        end = time.time() + (patience if self.clock.fake else 3600 * 2)
        while time.time() < end:
            if card_id in await self.observed_cards():
                await self.settle()
                return
            phase = self.phase_now()
            if phase == "completed" or (
                phase and catalog.PHASES.index(phase) > catalog.PHASES.index(card_phase)
            ):
                self.unfired.add(card_id)  # 단계가 예정 시각 전에 끝남: 미발생
                return
            await asyncio.sleep(self.poll)
        self.step_errors.append(f"wait card:{card_id} timed out")

    async def wait_message(self, seat: str) -> None:
        """직전 step을 시작한 뒤로 그 자리의 새 메시지가 올 때까지(이미 왔으면 바로)."""
        since = self.step_marks[-1] if self.step_marks else 0
        frame = await self.sock.wait_for(
            lambda f: f["type"] == "message.new" and f["payload"]["sender"] == seat,
            timeout=60,
            since=since,
        )
        if frame is None:
            self.step_errors.append(f"wait msg:{seat} timed out")
        else:
            await self.settle()

    async def wait_ended(self) -> None:
        end = time.time() + (60 if self.clock.fake else 3600 * 2)
        while not self.ended() and time.time() < end:
            since = len(self.sock.frames)
            in_progress = (await self.internal())["status"] == "in_progress"
            if self.clock.fake and in_progress:
                await self.clock.advance(self, to="deadline")
            await self.sock.wait_for(
                lambda f: f["type"] in ("phase.changed", "session.ended"),
                timeout=30 if self.clock.fake or not in_progress else 60,
                since=since,
            )
        if not self.ended():
            self.step_errors.append("wait ended timed out")

    # ---- 진행 -----------------------------------------------------------------------------

    async def run_step(self, index: int, step: dict[str, Any]) -> None:
        mark = len(self.sock.frames)
        try:
            await self.wait_at(step["at"])
            if "say" in step:
                await self.say(step["say"])
            if "confirm" in step:
                await self.confirm(step["confirm"])
            if "material" in step:
                await self.api.json(
                    "GET", f"/sessions/{self.sid}/materials/{step['material']}", token=self.jwt
                )
            if "assistant" in step:
                await self.api.json(
                    "POST",
                    f"/sessions/{self.sid}/assistant",
                    token=self.jwt,
                    json_body={"prompt": step["assistant"]},
                )
            if "proposal" in step:
                await self.proposal(step["proposal"])
            if "restart" in step:
                await self.restart()
            if "submit" in step:
                await self.api.json(
                    "POST", f"/sessions/{self.sid}/proposal/submit", token=self.jwt, json_body={}
                )
                await self.wait_ended()
        except Exception as error:  # noqa: BLE001 - step 실패는 기록하고 다음으로
            self.step_errors.append(f"step {index} {step.get('at')}: {error!r}")
        self.step_marks.append(mark)
        with contextlib.suppress(Exception):
            self.last_t = await self.t_min()

    # ---- 리포트·검토 ----------------------------------------------------------------------

    async def admin(self, method: str, path: str, **kwargs: Any) -> Any:
        return await self.api.call(
            method, f"/admin/sessions/{self.sid}{path}", token=self.admin_token, **kwargs
        )

    async def wait_report(self, stage: str = "draft") -> dict[str, Any] | None:
        end = time.time() + self.report_wait
        while time.time() < end:
            response = await self.admin("GET", "/report", log=False)
            if response.status_code == 200 and response.json().get("stage") == stage:
                report = response.json()
                self.transcript.add("report", stage=stage)
                return report
            if self.regenerate_on_failure and not self.regenerated:
                entry = await self.api.json("GET", f"/s/{self.invite_token}", log=False)
                if entry.get("failed_stage") == "report":
                    self.failed_seen = True
                    self.eval_before_regen = await self.internal("/evaluations")
                    self.regenerated = True
                    await self.admin("POST", "/report/regenerate")
            await asyncio.sleep(self.poll)
        return None

    async def review(self, draft: dict[str, Any]) -> dict[str, Any]:
        spec = self.script["review"]
        adjust = spec["adjust"]
        name = spec["reviewer_name"]
        item = {
            "dimension_id": adjust["dimension_id"],
            "level": adjust["level"],
            "memo": adjust["memo"],
        }
        out: dict[str, Any] = {"dimension_id": adjust["dimension_id"], "level": adjust["level"]}

        async def post(adjustments: list[dict[str, Any]]) -> Any:
            return await self.admin(
                "POST", "/review", json_body={"adjustments": adjustments, "reviewer_name": name}
            )

        out["no_memo"] = (await post([{**item, "memo": ""}])).status_code
        out["no_record_without_reason"] = (await post([{**item, "level": None}])).status_code
        out["approve"] = (await post([item])).status_code
        out["readjust"] = (await post([{**item, "level": 2}])).status_code
        out["same_body"] = (await post([item])).status_code
        notes = await self.admin(
            "POST", "/report/notes", json_body={"text": "원문 확인함", "reviewer_name": name}
        )
        out["notes"] = notes.status_code
        out["final"] = await self.wait_report("final")
        out["merged"] = (await self.admin("GET", "/report", log=False)).json()
        return out

    # ---- 수집 -----------------------------------------------------------------------------

    async def collect(self, draft: dict[str, Any] | None) -> dict[str, Any]:
        async def get(path: str, **params: Any) -> Any:
            try:
                return await self.internal(path, **params)
            except ApiError:
                return None

        record = await self.admin("GET", "/record", log=False)
        return {
            "name": self.script["name"],
            "script": self.script,
            "minutes": self.script.get("minutes", 15),
            "session_id": self.sid,
            "llm_fake": self.llm_fake,
            "clock": "fake" if self.clock.fake else "real",
            "entry": self.entry,
            "started": self.started,
            "states": {**self.states, "final": await self.state()},
            "materials_before": self.materials_before,
            "frames": self.sock.frames,
            "segments": self.segments,
            "advances": self.advances,
            "typing_intervals": self.typing_intervals,
            "step_errors": self.step_errors,
            "unfired_cards": sorted(self.unfired),
            "restarts": self.restarts,
            "session": await get(""),
            "messages": await get("/messages", limit=1000),
            "observations": await get("/observations"),
            "slots": await get("/slots"),
            "facts": await get("/facts"),
            "contributions": await get("/contributions"),
            "rubric": await get("/rubric"),
            "proposal": await get("/proposal"),
            "assistant_calls": await get("/assistant_calls"),
            "evaluations": await get("/evaluations"),
            "debate_state": await get("/debate_state"),
            "draft": draft,
            "final": self.review_result.get("final"),
            "report": self.review_result.get("merged"),
            "review": {k: v for k, v in self.review_result.items() if k not in ("final", "merged")},
            "record": record.json() if record.status_code == 200 else None,
            "runs": await self.db.agent_runs(self.sid),
            "costs": await self.db.costs(self.sid),
            "intent_latency_ms": await self.db.intent_latency_ms(self.sid),
            "regenerated": self.regenerated,
            "failed_seen": self.failed_seen,
            "eval_before_regen": self.eval_before_regen,
            "langsmith_project": env().get("LANGCHAIN_PROJECT")
            if env().get("LANGCHAIN_TRACING_V2") == "true"
            else None,
            "transcript": self.transcript.lines,
        }

    async def setup(self) -> None:
        await self.admin_login()
        await self.create_session()
        await self.enter()

    async def play(self) -> None:
        """대본 step을 끝까지 돌리고 세션이 끝날 때까지 기다린다."""
        for index, step in enumerate(self.script["steps"]):
            await self.run_step(index, step)
        if not self.ended():
            await self.wait_ended()

    async def finish(self) -> dict[str, Any]:
        """리포트 대기 → (검토) → 수집. 소켓·클라이언트를 닫는다."""
        try:
            draft = await self.wait_report("draft")
            if draft is None:
                self.step_errors.append("report draft not ready")
            elif "review" in self.script:
                self.review_result = await self.review(draft)
            return await self.collect(draft)
        finally:
            if self.voice_task:
                self.voice_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.voice_task
            with contextlib.suppress(Exception):
                await self.sock.close()
            await self.api.aclose()
            await self.db.close()

    async def run(self) -> dict[str, Any]:
        await self.setup()
        await self.play()
        return await self.finish()


def detect_llm_fake(live: bool) -> tuple[bool, str]:
    """스택이 결정적 LLM 모드(LLM_FAKE=1)인지와 판별 근거. --live가 우선, 아니면 env의 LLM_FAKE."""
    if live:
        return False, "--live 옵션"
    value = env().get("LLM_FAKE")
    if value is None:
        return True, "LLM_FAKE 없음(기본: 결정적 모드로 가정)"
    return value.strip().lower() in ("1", "true", "yes"), f"env LLM_FAKE={value}"


async def run_script(name: str, target: Target, out: Path, clock: str, live: bool) -> bool:
    script = scripts.load(name)
    fake, source = detect_llm_fake(live)
    runner = Runner(script, target, clock=clock, llm_fake=fake)
    runner.voice_mode = True
    bundle = await runner.run()
    bundle["voice_validation"] = (
        "FakeSpeech / synthetic playback ack; browser media tested separately"
    )
    bundle["llm_mode_source"] = source
    bundle["results"] = asserts.check(bundle)
    records.write(bundle, out)
    return asserts.passed(bundle["results"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--script", help="대본 이름 또는 경로 (all_five·timeout_all·honesty_claim·restart)"
    )
    parser.add_argument("--clock", choices=["real", "fake"], default="real")
    parser.add_argument(
        "--live",
        action="store_true",
        help="실제 LLM 스택으로 강제(env LLM_FAKE보다 우선, 수준 값 기대를 건너뜀)",
    )
    parser.add_argument("--auth", action="store_true", help="단언 8 인증 경계")
    parser.add_argument("--faults", help="장애 주입: dlq, llm_timeout, all (러너가 앱을 띄움)")
    parser.add_argument("--out", type=Path, default=records.RECORDS)
    args = parser.parse_args()
    target = Target.from_env()
    if args.auth:
        from e2e import auth

        ok = asyncio.run(auth.run(target, args.out))
    elif args.faults:
        from e2e import faults

        ok = asyncio.run(faults.run_faults(args.faults, args.out))
    elif args.script:
        ok = asyncio.run(run_script(args.script, target, args.out, args.clock, args.live))
    else:
        parser.error("--script, --auth, --faults 중 하나가 필요하다")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
