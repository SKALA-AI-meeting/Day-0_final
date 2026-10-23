"""가상 지원자 드라이버(docs/guides/virtual-candidate.md).

실 스택에 15분 세션 하나를 API·WS로 끝까지 진행한다.

    uv run --project services/api python tests/eval/virtual_candidate/drive.py --mode scripted
    uv run --project services/api python tests/eval/virtual_candidate/drive.py --mode autonomous

순서: 관리자 로그인(seed 기본 계정. 비밀번호는 env `VC_ADMIN_PASSWORD` 또는
scripts/seed.py 기본값을 읽어 쓰고 출력하지 않음) → "서비스기획 신입 (15분 데모)" 채용 건에
초대 → 동의·시작 → WS 연결 → 대본형은 scripted.yaml 장면을 단계 시각에, 자율형은
LLM(autonomous.md 성격)이 보이는 대화로 다음 행동을 고른다 → 제출(또는 자동 제출) →
리포트 대기 → DB에서 세션 전체를 읽어 `session.json`으로 내보내고 metrics.py로 지표를 낸다.

기록 폴더(기본 `tests/e2e/records/virtual/<일시>-<mode>/`, git 무시): `log.md`(시각·단계·
행동·의도·직후 반응), `transcript.jsonl`(WS 프레임·REST 호출, 토큰 값은 가림),
`session.json`, `metrics.json`. 실제 모델을 부르고 15분 넘게 걸려 `make test`에 들지 않는다.
"""

import argparse
import asyncio
import contextlib
import json
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import websockets
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / "tests")]
from e2e.client import env  # noqa: E402

POSITION_NAME = "서비스기획 신입 (15분 데모)"
KEYWORDS = yaml.safe_load(
    (ROOT / "scenarios" / "missions" / "debate" / "keywords.yaml").read_text("utf-8")
)["keywords"]
EXPORT_TABLES = (
    "messages",
    "message_intents",
    "agent_runs",
    "options",
    "stances",
    "slots",
    "session_cards",
    "observations",
    "proposals",
    "assistant_calls",
    "material_views",
    "connection_gaps",
    "evaluation_runs",
    "evaluations",
    "reports",
)
ORDER = {"messages": "created_at, seq", "agent_runs": "queued_at"}
PEERS = ("p1", "p2", "p3")
INVITE_LINK = re.compile(r"/s/[A-Za-z0-9_-]{16,}")  # 초대 토큰이 든 경로·안내 문안
CONFIRM_AFTER = 0.4  # 자율형: 단계 길이의 이 비율이 지나기 전 "이걸로 정하기"는 미룬다


def seed_admin(values: dict[str, str]) -> tuple[str, str]:
    """관리자 계정. env가 우선이고 없으면 scripts/seed.py의 argparse 기본값(값은 출력하지
    않는다)."""
    seed = (ROOT / "scripts" / "seed.py").read_text("utf-8")

    def default(flag: str) -> str:
        found = re.search(rf'add_argument\("--{flag}", default="([^"]+)"', seed)
        return found.group(1) if found else ""

    return (
        values.get("VC_ADMIN_EMAIL") or default("email"),
        values.get("VC_ADMIN_PASSWORD") or default("password"),
    )


def keyword_entry(label_or_key: str | None) -> dict[str, Any] | None:
    return next((k for k in KEYWORDS if label_or_key in (k["key"], k["label"])), None)


class Driver:
    def __init__(self, mode: str, out: Path, values: dict[str, str]) -> None:
        self.mode = mode
        self.out = out
        self.values = values
        self.api_url = values.get("VC_API_URL", "http://localhost:8000")
        self.http = httpx.AsyncClient(base_url=self.api_url, timeout=90)
        self.jwt = ""
        self.admin = ""
        self.sid = ""
        self.phase = "keyword"
        self.frames: list[dict[str, Any]] = []
        self.ended = asyncio.Event()
        self.phase_changed = asyncio.Event()
        self.ws: Any = None
        out.mkdir(parents=True, exist_ok=True)
        self.transcript = (out / "transcript.jsonl").open("a", encoding="utf-8")
        self.log_path = out / "log.md"

    # ---- 기록 ------------------------------------------------------------------------------

    def record(self, kind: str, **data: Any) -> None:
        self.transcript.write(
            json.dumps({"at": time.time(), "kind": kind, **data}, ensure_ascii=False) + "\n"
        )
        self.transcript.flush()

    def log(self, action: str, intent: str, reaction: str = "") -> None:
        def clean(text: str) -> str:
            return str(text).replace("|", "/").replace("\n", " ")

        line = (
            f"| {datetime.now():%H:%M:%S} | {self.phase} | {clean(action)} | {clean(intent)} "
            f"| {clean(reaction)} |\n"
        )
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line)
        print(line.rstrip(), flush=True)

    # ---- REST ------------------------------------------------------------------------------

    async def call(
        self, method: str, path: str, *, token: str | None = None, body: Any = None
    ) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = await self.http.request(method, path, json=body, headers=headers)
        try:
            data = response.json()
        except ValueError:
            data = response.text
        secret = ("token", "invite_url")  # 초대 링크도 인증 토큰을 담는다
        safe = (
            {k: "<redacted>" if any(x in k for x in secret) else v for k, v in data.items()}
            if isinstance(data, dict)
            else data
        )
        safe = json.loads(INVITE_LINK.sub("/s/<invite>", json.dumps(safe, ensure_ascii=False)))
        shown = INVITE_LINK.sub("/s/<invite>", path)
        if "auth" not in path:
            self.record("rest", method=method, path=shown, status=response.status_code, body=safe)
        return response.status_code, data

    async def must(self, method: str, path: str, **kw: Any) -> Any:
        status, data = await self.call(method, path, **kw)
        if status >= 400:
            raise RuntimeError(f"{method} {path} → {status} {data}")
        return data

    async def state(self) -> dict[str, Any]:
        return await self.must("GET", f"/sessions/{self.sid}/state", token=self.jwt)

    # ---- 시작 ------------------------------------------------------------------------------

    async def start(self, alias: str, display_name: str) -> None:
        email, password = seed_admin(self.values)
        status, data = await self.call(
            "POST", "/admin/auth/login", body={"email": email, "password": password}
        )
        if status != 200:
            raise RuntimeError(
                f"관리자 로그인 실패({status}). VC_ADMIN_EMAIL·VC_ADMIN_PASSWORD 확인"
            )
        self.admin = data["token"]
        positions = await self.must("GET", "/admin/positions", token=self.admin)
        name = self.values.get("VC_POSITION", POSITION_NAME)
        position = next((p for p in positions if p["name"] == name), None)
        if position is None:
            raise RuntimeError(f"채용 건 없음: {name} (make seed 확인)")
        invite = await self.must(
            "POST",
            f"/admin/positions/{position['id']}/invites",
            token=self.admin,
            body={"alias": alias, "email": f"{uuid.uuid4().hex[:8]}@example.test"},
        )
        invite_token = invite["invite_url"].rstrip("/").split("/")[-1]
        await self.must("GET", f"/s/{invite_token}")
        consent = await self.must(
            "POST", f"/s/{invite_token}/consent", body={"display_name": display_name}
        )
        self.jwt, self.sid = consent["session_token"], consent["session_id"]
        started = await self.must("POST", f"/s/{invite_token}/start", token=self.jwt)
        self.phase = started["phase"]
        with self.log_path.open("w", encoding="utf-8") as f:
            f.write(
                f"# 가상 지원자 기록 ({self.mode})\n\n- 별칭: {alias}\n- 표시명: {display_name}\n"
                f"- 시작: {started['started_at']}\n- 세션 id: {self.sid}\n"
                f"- 관리자 기록: http://localhost:3000/admin/sessions/{self.sid}/record\n"
                "- 초대 링크는 인증 토큰을 담고 있어 남기지 않는다.\n\n"
                "| 시각 | 단계 | 행동 | 의도 | 직후 반응 |\n|---|---|---|---|---|\n"
            )
        print(f"SESSION {self.sid}", flush=True)

    async def listen(self) -> None:
        url = self.api_url.replace("http", "ws", 1) + f"/ws/sessions/{self.sid}?token={self.jwt}"
        while not self.ended.is_set():
            try:
                async with websockets.connect(url, max_size=None) as ws:
                    self.ws = ws
                    async for raw in ws:
                        frame = json.loads(raw)
                        if frame.get("type") in ("typing", "timer"):
                            continue
                        self.frames.append(frame)
                        self.record("ws", frame=frame)
                        kind, payload = frame.get("type"), frame.get("payload") or {}
                        if kind == "message.new" and payload.get("sender") != "candidate":
                            self.log(
                                f"{payload.get('sender')}: {payload.get('content', '')[:200]}",
                                "(관찰)",
                            )
                        elif kind == "phase.changed":
                            self.phase = payload.get("phase", self.phase)
                            self.phase_changed.set()
                            self.log(f"phase.changed → {self.phase}", "(관찰)", str(payload))
                        elif kind == "slot.confirmed":
                            self.log(
                                "slot.confirmed", "(관찰)", json.dumps(payload, ensure_ascii=False)
                            )
                        elif kind == "session.ended":
                            self.log("session.ended", "(관찰)", str(payload))
                            self.ended.set()
                            return
            except (OSError, websockets.WebSocketException) as exc:
                self.record("ws_error", error=type(exc).__name__)
                await asyncio.sleep(2)

    async def say(self, text: str, intent: str) -> None:
        payload = {"client_msg_id": str(uuid.uuid4()), "content": text}
        await self.ws.send(json.dumps({"type": "message.send", "payload": payload}))
        self.record("say", text=text, intent=intent)
        self.log(f"보냄: {text}", intent)

    async def ask_assistant(self, text: str, intent: str) -> None:
        status, data = await self.call(
            "POST", f"/sessions/{self.sid}/assistant", token=self.jwt, body={"prompt": text}
        )
        answer = data.get("content", "") if isinstance(data, dict) else str(data)
        self.log(f"어시스턴트 질문: {text}", intent, f"{status} {answer[:200]}")

    async def confirm(self, value: str, option_key: str | None, intent: str) -> None:
        body = {"value": value} | ({"option_key": option_key} if option_key else {})
        status, data = await self.call(
            "POST", f"/sessions/{self.sid}/slots/{self.phase}/confirm", token=self.jwt, body=body
        )
        self.log(
            f"이걸로 정하기: {value}",
            intent,
            f"{status} {json.dumps(data, ensure_ascii=False)[:200]}",
        )

    async def fill_proposal(self, sections: dict[str, str], intent: str) -> None:
        current = await self.must("GET", f"/sessions/{self.sid}/proposal", token=self.jwt)
        merged = {**current["sections"], **{k: v for k, v in sections.items() if v}}
        status, data = await self.call(
            "PUT",
            f"/sessions/{self.sid}/proposal",
            token=self.jwt,
            body={"sections": merged, "revision": current["revision"]},
        )
        self.log(
            f"기획안 저장: {json.dumps(merged, ensure_ascii=False)[:300]}", intent, str(status)
        )

    async def submit(self, intent: str) -> None:
        status, data = await self.call(
            "POST",
            f"/sessions/{self.sid}/proposal/submit",
            token=self.jwt,
            body={"blank_warning_shown": False},
        )
        self.log("제출", intent, f"{status} {data}")

    # ---- 단계 시계 ---------------------------------------------------------------------------

    async def phase_clock(self) -> tuple[str, float, float]:
        """(단계, 단계 시작 이후 초, 단계 길이 초)."""
        s = (await self.state())["session"]
        if not s.get("phase_started_at") or not s.get("phase_deadline_at"):
            return s["phase"], 0.0, 1.0
        started = datetime.fromisoformat(s["phase_started_at"])
        deadline = datetime.fromisoformat(s["phase_deadline_at"])
        length = (deadline - started).total_seconds()
        elapsed = length - float(s.get("phase_remaining_seconds") or 0)
        return s["phase"], elapsed, length

    def peer_lines_since(self, since: int) -> list[dict[str, Any]]:
        return [
            f["payload"]
            for f in self.frames[since:]
            if f.get("type") == "message.new" and f["payload"].get("sender") in PEERS
        ]

    async def confirmed_keyword(self) -> dict[str, Any] | None:
        slots = (await self.state())["slots"]
        value = next((s["value"] for s in slots if s["phase"] == "keyword" and s["value"]), None)
        return keyword_entry(value)

    # ---- 대본형 ------------------------------------------------------------------------------

    async def run_scripted(self, brief: dict[str, Any]) -> None:
        pending = list(brief["scenes"])
        marks: dict[str, int] = {}
        last_frame = 0
        while pending and not self.ended.is_set():
            phase, elapsed, length = await self.phase_clock()
            # 이미 지난 단계의 장면은 건너뛴 것으로 기록한다
            for scene in [s for s in pending if _before(s["phase"], phase)]:
                self.log(f"장면 {scene['id']} 건너뜀", "단계가 먼저 끝남")
                pending.remove(scene)
            due = [s for s in pending if s["phase"] == phase and elapsed >= s["at"] * length]
            for scene in due:
                pending.remove(scene)
                if scene.get("unless_reply"):
                    replies = self.peer_lines_since(last_frame)
                    if any(re.search(scene["unless_reply"], r.get("content", "")) for r in replies):
                        self.log(f"장면 {scene['id']} 건너뜀", "동료가 이미 반응함")
                        continue
                last_frame = len(self.frames)
                marks[scene["id"]] = last_frame
                await self.do_scene(scene, brief)
            await asyncio.sleep(1.5)

    async def do_scene(self, scene: dict[str, Any], brief: dict[str, Any]) -> None:
        keyword = await self.confirmed_keyword() or keyword_entry("water")
        fill = {
            "problem0": keyword["problems"][0]["label"],
            "solution_new": brief["new_solution"][keyword["key"]],
        }
        intent = f"{scene['id']}"
        action = scene["action"]
        if action == "say":
            await self.say(scene["text"].format(**fill), intent)
        elif action == "assistant":
            await self.ask_assistant(scene["text"].format(**fill), intent)
        elif action == "confirm":
            await self.confirm(scene["value"].format(**fill), scene.get("option_key"), intent)
        elif action == "proposal":
            await self.fill_proposal(scene["sections"], intent)
        elif action == "submit":
            await self.submit(intent)

    # ---- 자율형 ------------------------------------------------------------------------------

    async def run_autonomous(self, persona: str) -> None:
        model = self.values.get("VC_MODEL") or self.values.get("OPENAI_MODEL") or "gpt-4o-mini"
        key_name = "OPENAI_API_KEY" if model == "gpt-4o-mini" else "OPENAI_API_KEY_PERSONAL"
        key = self.values.get(key_name, "")
        if not key:
            raise RuntimeError(f"{key_name}가 비어 있다")
        llm = httpx.AsyncClient(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {key}"},
            timeout=60,
        )
        last_said = time.time()
        seen = 0
        proposal_done = False
        try:
            while not self.ended.is_set():
                await asyncio.sleep(6)
                state = await self.state()
                phase = state["session"]["phase"]
                if state["session"]["status"] != "in_progress":
                    break
                _, elapsed, length = await self.phase_clock()
                if phase == "proposal":
                    # 사람처럼 대화를 다시 읽고 쓰는 시간을 둔다: 단계 30%에 작성, 60%에 제출
                    if not proposal_done and elapsed >= 0.3 * length:
                        await self.autonomous_proposal(llm, model, persona, state)
                        proposal_done = True
                    if proposal_done and elapsed >= 0.6 * length:
                        await self.submit("기획안 제출")
                        break
                    continue
                messages = [m for m in state["messages"] if m.get("phase") == phase]
                new = len(state["messages"]) > seen
                idle = time.time() - last_said
                if not new and idle < 45:
                    continue
                if state.get("typing") and idle < 60:
                    continue  # 동료가 입력 중이면 기다린다
                seen = len(state["messages"])
                choice = await self.decide(llm, model, persona, state, messages)
                action = choice.get("action")
                why = choice.get("reason", "")
                if action == "say" and choice.get("text"):
                    await self.say(choice["text"], why)
                    last_said = time.time()
                elif action == "assistant" and choice.get("text"):
                    await self.ask_assistant(choice["text"], why)
                    last_said = time.time()
                elif action == "confirm" and (choice.get("value") or choice.get("option_key")):
                    if elapsed < CONFIRM_AFTER * length:
                        self.record("confirm_too_early", choice=choice)
                        continue  # 단계 초반 확정은 사람 행동으로 보기 어려워 미룬다
                    value, key = self.resolve_option(state, choice)
                    await self.confirm(value, key, why)
                    last_said = time.time()
                else:
                    self.record("wait", reason=why)
        finally:
            await llm.aclose()

    def resolve_option(
        self, state: dict[str, Any], choice: dict[str, Any]
    ) -> tuple[str, str | None]:
        """LLM이 키나 라벨 어느 쪽으로 답해도 확정 본문(value=라벨, option_key=키)으로 맞춘다."""
        phase = state["session"]["phase"]
        raw_value, raw_key = str(choice.get("value") or ""), choice.get("option_key")
        if phase == "keyword":
            found = keyword_entry(raw_key) or keyword_entry(raw_value)
            return (found["label"], found["key"]) if found else (raw_value, raw_key)
        keyword = keyword_entry(
            next((s["value"] for s in state["slots"] if s["phase"] == "keyword"), None)
        )
        group = keyword["problems" if phase == "problem" else "solutions"] if keyword else []
        found = next(
            (
                o
                for o in group
                if raw_key in (o["key"], o["label"]) or raw_value in (o["key"], o["label"])
            ),
            None,
        )
        return (found["label"], found["key"]) if found else (raw_value, None)

    def visible(self, state: dict[str, Any]) -> str:
        names = {p["id"]: p["display_name"] for p in state["participants"]}
        names["candidate"] = "나(민준)"
        lines = [
            f"[{m.get('phase')}] {names.get(m['sender'], m['sender'])}: {m['content']}"
            for m in state["messages"][-24:]
        ]
        return "\n".join(lines)

    def options_text(self, state: dict[str, Any], keyword: dict[str, Any] | None) -> str:
        phase = state["session"]["phase"]
        if phase == "keyword":
            opts = [(o["option_key"], o["label"]) for o in state["keyword_options"]]
        elif keyword:
            group = "problems" if phase == "problem" else "solutions"
            opts = [(o["key"], o["label"]) for o in keyword[group]]
        else:
            opts = []
        return ", ".join(f"{k}={label}" for k, label in opts)

    async def decide(
        self,
        llm: httpx.AsyncClient,
        model: str,
        persona: str,
        state: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        keyword = await self.confirmed_keyword()
        s = state["session"]
        left = int(s.get("phase_remaining_seconds") or 0)
        slots = json.dumps([(x["phase"], x["value"]) for x in state["slots"]], ensure_ascii=False)
        options = self.options_text(state, keyword)
        user = (
            f"지금 단계: {s['phase']} (이 단계 남은 시간 {left}초)\n"
            f"정해진 칸: {slots}\n"
            f"이 단계 후보(이걸로 정하기의 키=라벨, 새 안도 가능): {options}\n\n"
            f"보이는 대화(오래된 것 → 최근):\n{self.visible(state)}\n\n"
            'JSON 하나로만 답한다: {"action": "say|assistant|confirm|wait", "text": "...", '
            '"value": "confirm일 때 칸 값", "option_key": "confirm일 때 후보 키 또는 null", '
            '"reason": "왜 그렇게 하는지 한 줄"}'
        )
        body = {
            "model": model,
            "temperature": 0.8,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": persona}, {"role": "user", "content": user}],
        }
        try:
            response = await llm.post("/chat/completions", json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            self.record("llm_error", error=type(exc).__name__)
            return {"action": "wait", "reason": "LLM 오류"}

    async def autonomous_proposal(
        self, llm: httpx.AsyncClient, model: str, persona: str, state: dict[str, Any]
    ) -> None:
        current = await self.must("GET", f"/sessions/{self.sid}/proposal", token=self.jwt)
        user = (
            "기획안 단계다. 지금까지 대화를 바탕으로 기획안 칸을 채운다. 각 칸은 1~2문장.\n"
            f"현재 기획안: {json.dumps(current['sections'], ensure_ascii=False)}\n\n"
            f"대화:\n{self.visible(state)}\n\n"
            'JSON으로만: {"keyword": "...", "problem": "...", "solution": "...", '
            '"rationale": "...", "expected_effect": "...", '
            '"idea_credit": "이 기획에 반영된 의견과 그 의견을 낸 사람"}'
        )
        body = {
            "model": model,
            "temperature": 0.5,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": persona}, {"role": "user", "content": user}],
        }
        try:
            response = await llm.post("/chat/completions", json=body)
            response.raise_for_status()
            sections = json.loads(response.json()["choices"][0]["message"]["content"])
        except (httpx.HTTPError, KeyError, ValueError):
            sections = {}
        keep = {k: str(v) for k, v in sections.items() if k in current["sections"]}
        await self.fill_proposal(keep, "기획안 작성")

    # ---- 끝 ---------------------------------------------------------------------------------

    async def wait_report(self, limit: float = 900) -> bool:
        deadline = time.time() + limit
        while time.time() < deadline:
            status, data = await self.call(
                "GET", f"/admin/sessions/{self.sid}/report", token=self.admin
            )
            if status == 200:
                return True
            if status == 409 and isinstance(data, dict) and "failed" in json.dumps(data):
                return False
            await asyncio.sleep(10)
        return False

    async def close(self) -> None:
        self.transcript.close()
        await self.http.aclose()


def _before(a: str, b: str) -> bool:
    order = ["keyword", "problem", "solution", "proposal", "completed"]
    return order.index(a) < (order.index(b) if b in order else len(order))


def export_session(session_id: str, values: dict[str, str]) -> dict[str, Any]:
    """DB에서 세션 한 건을 표별 JSON으로 읽는다(읽기 전용). compose의 postgres 컨테이너에서 psql을
    부른다(포트를 열지 않은 스택). 토큰 해시·초대 이메일 같은 열은 읽지 않는다.
    """
    container = values.get("VC_PG_CONTAINER", "infra-postgres-1")
    url = values.get("DATABASE_URL", "")
    found = re.search(r"//([^:/@]+)(?::[^@]*)?@[^/]+/([^?]+)", url)
    user, db = (found.group(1), found.group(2)) if found else ("dayzero", "dayzero")

    def query(sql: str) -> Any:
        out = subprocess.run(
            ["docker", "exec", container, "psql", "-U", user, "-d", db, "-Atc", sql],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(out.stdout.strip() or "null")

    sid = session_id.replace("'", "")
    data: dict[str, Any] = {
        "session": query(
            "select row_to_json(t) from (select id, status, phase, display_name, consented_at, "
            "started_at, ends_at, phase_started_at, phase_deadline_at, generation, submitted_at, "
            "completed_at, ended_at, llm_failures, debate_cost_usd, cost_usd, eval_round, "
            f"failed_stage, failed_reason, created_at from sessions where id='{sid}') t"
        ),
        "position": query(
            "select row_to_json(t) from (select p.id, p.mission_id, p.catalog_version, "
            "p.session_minutes, p.scenario from positions p join invites i on i.position_id=p.id "
            f"join sessions s on s.invite_id=i.id where s.id='{sid}') t"
        ),
    }
    for table in EXPORT_TABLES:
        order = ORDER.get(table, "1")
        data[table] = query(
            f"select coalesce(json_agg(t order by {order}), '[]'::json) from "
            f"(select * from {table} where session_id='{sid}') t"
        )
    return data


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("scripted", "autonomous"), required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--no-report", action="store_true", help="리포트를 기다리지 않는다")
    parser.add_argument("--export-only", default=None, help="세션 id: 진행 없이 내보내기·지표만")
    args = parser.parse_args()
    values = env()
    stamp = f"{datetime.now():%Y%m%d-%H%M}"
    out = args.out or ROOT / "tests" / "e2e" / "records" / "virtual" / f"{stamp}-{args.mode}"
    out.mkdir(parents=True, exist_ok=True)
    sys.path[:0] = [str(HERE)]
    import metrics

    if args.export_only:
        data = export_session(args.export_only, values)
        (out / "session.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        result = metrics.compute(data)
        (out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    driver = Driver(args.mode, out, values)
    if args.mode == "scripted":
        brief = yaml.safe_load((HERE / "scripted.yaml").read_text("utf-8"))
        alias, name = f"{brief['alias_prefix']}-{stamp[-4:]}", brief["display_name"]
    else:
        brief = (HERE / "autonomous.md").read_text("utf-8")
        alias, name = f"합성-자율-{stamp[-4:]}", "민준"
    try:
        await driver.start(alias, name)
        listener = asyncio.create_task(driver.listen())
        while driver.ws is None:
            await asyncio.sleep(0.2)
        if args.mode == "scripted":
            await driver.run_scripted(brief)
        else:
            await driver.run_autonomous(brief)
        # 대본이 먼저 끝나도 세션 종료(제출·자동 제출)까지 기다린다
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(driver.ended.wait(), timeout=20 * 60)
        listener.cancel()
        ready = True if args.no_report else await driver.wait_report()
        driver.log("리포트", "대기 결과", "ready" if ready else "not ready")
        data = export_session(driver.sid, values)
        (out / "session.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        result = metrics.compute(data)
        (out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        print(f"DONE {out}", flush=True)
    finally:
        await driver.close()


if __name__ == "__main__":
    asyncio.run(main())
