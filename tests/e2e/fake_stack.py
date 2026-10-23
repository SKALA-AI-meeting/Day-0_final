"""mock 모드용 가짜 api: 계약과 계약 픽스처만으로 만든다.

기준은 docs/contracts/README.md와 tests/fixtures/contracts/다. 러너를 실 스택 없이 돌려 단언 로직을
확인하는 용도라서 서비스 코드(api·agent·worker)는 import하지 않는다.

- 지원자 메시지 분류는 agent의 결정적 LLM 모드(LLM_FAKE=1)와 같은 규칙이다: 안 라벨이 문장에
  있으면 그 안, "좋아요·동의·찬성" agree, "아니·반대·글쎄" oppose, 물음표 question.
  페르소나는 시나리오 고정 입장으로 말한다.
- 시간은 실제 세션처럼 `started_at`을 앞당겨(테스트 API advance) 흐르게 하고, 스케줄러 틱(`tick`)이
  시간 초과 채우기·카드·침묵 초대·자동 제출을 처리한다.
- httpx.MockTransport 두 개(공개 8000·내부 8001)와 WS 연결기(connector)를 낸다.
- 리포트 워커는 요청 안에서 동기로 돈다. 장애 주입(`fail_report_draft`·`set_llm_fault`)과
  워커 → 내부 api 호출 기록(`worker_log`)은 실 스택의 장애 주입 프록시와 같은 모양이다.
"""

import asyncio
import copy
import json
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

from e2e import catalog
from e2e.catalog import load_contract

SECRET = "e2e-fake-secret-e2e-fake-secret-0123"
INTERNAL_TOKEN = "e2e-fake-internal"
SEATS = ("p1", "p2", "p3")
NAMES = {"candidate": "지원자", "p1": "강한결", "p2": "윤하람", "p3": "서지안"}
ELEMENT_NAMES = ("persuasion", "listening", "inclusion", "flexibility", "honesty")
CONFIRM_WORDS = ("좋아요", "동의", "찬성")
OPPOSE_WORDS = ("아니", "반대", "글쎄")
FILL_PRIORITY = ("p3", "p2", "p1")  # critic > quiet > stubborn
SILENCE_SECONDS_PER_MIN = 3.0  # L/20 = 세션 분 × 3초
JSON = {"content-type": "application/json"}


def iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def response(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body, headers=JSON)


def error(status: int, code: str, message: str = "") -> httpx.Response:
    return response(status, {"error": {"code": code, "message": message or code}})


def option_catalog(keyword_key: str | None) -> dict[str, list[dict[str, str]]]:
    """단계별 카탈로그 안 [{key, label}]."""
    words = catalog.keywords()
    result = {"keyword": [{"key": k, "label": v["label"]} for k, v in words.items()]}
    if keyword_key:
        entry = words[keyword_key]
        result["problem"] = [{"key": p["key"], "label": p["label"]} for p in entry["problems"]]
        result["solution"] = [{"key": s["key"], "label": s["label"]} for s in entry["solutions"]]
    return result


class Session:
    def __init__(
        self, stack: "FakeStack", company: str, position: dict[str, Any], alias: str
    ) -> None:
        self.stack = stack
        self.id = f"sess-{uuid.uuid4().hex[:8]}"
        self.company = company
        self.position = position
        self.alias = alias
        self.minutes = position["session_minutes"]
        self.invite_token = uuid.uuid4().hex
        self.status = "invited"
        self.phase: str | None = None
        self.display_name = alias
        self.consented = False
        self.started_at: datetime | None = None
        self.phase_started_at: datetime | None = None
        self.completed_at: datetime | None = None
        self.generation = 0
        self.messages: list[dict[str, Any]] = []
        self.options: list[dict[str, Any]] = []
        self.stances: dict[str, dict[str, str | None]] = {p: {} for p in catalog.PHASES}
        self.slots: dict[str, dict[str, Any]] = {}
        self.observations: list[dict[str, Any]] = []
        self.delivered: dict[str, set[str]] = {}
        self.locked: str | None = None
        self.invited: set[str] = set()
        self.keyword_key: str | None = None
        self.proposal: dict[str, Any] = {
            "sections": dict.fromkeys(
                ("keyword", "problem", "solution", "rationale", "expected_effect", "idea_credit"),
                "",
            ),
            "revision": 0,
            "updated_at": None,
            "submitted_at": None,
            "auto_submitted": False,
        }
        self.assistant_calls: list[dict[str, Any]] = []
        self.pressure: dict[str, int] = {}
        self.moved: set[str] = set()
        # 단계별 지원자 최신 발언이 지지로 세는 발언이면 그 안 id(api candidate_confirmed)
        self.confirmed: dict[str, str | None] = {}
        self.origin_next = "spontaneous"
        self.last_candidate_at: datetime | None = None
        self.agent_runs: list[dict[str, Any]] = []
        self.intents: dict[str, dict[str, Any]] = {}
        self.report: dict[str, Any] | None = None
        self.report_stage: str | None = None
        self.eval_run: dict[str, Any] | None = None
        self.eval_round = 1
        self.failed_stage: str | None = None
        self.adjustments: list[dict[str, Any]] = []
        self.review: dict[str, Any] | None = None
        self.review_body: Any = None
        self.notes: list[dict[str, Any]] = []
        self.sockets: list[FakeWS] = []
        self.seq = 0
        self.superseded = False
        self.llm_failures = 0

    # ---- 시간 ----------------------------------------------------------------------------

    def now(self) -> datetime:
        return datetime.now(UTC)

    @property
    def ends_at(self) -> datetime:
        return self.started_at + timedelta(minutes=self.minutes)

    def deadline(self, phase: str) -> datetime:
        return self.started_at + timedelta(minutes=catalog.phase_deadlines(self.minutes)[phase])

    def t_min(self, at: datetime | None = None) -> float:
        return (
            round(((at or self.now()) - self.started_at).total_seconds() / 60, 3)
            if self.started_at
            else 0.0
        )

    # ---- WS ------------------------------------------------------------------------------

    def emit(self, type_: str, payload: dict[str, Any]) -> None:
        self.seq += 1
        for ws in self.sockets:
            ws.push({"type": type_, "seq": self.seq, "payload": payload})

    def message_payload(self, m: dict[str, Any]) -> dict[str, Any]:
        keys = ("id", "sender", "content", "phase", "is_invite", "t_min", "created_at")
        return {k: m[k] for k in keys}


class FakeWS:
    """websockets 연결 모양: send·close·close_code·비동기 반복."""

    def __init__(self, stack: "FakeStack", session: Session) -> None:
        self.stack = stack
        self.session = session
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.close_code: int | None = None

    def push(self, frame: dict[str, Any]) -> None:
        self.queue.put_nowait(json.dumps(frame, ensure_ascii=False))

    async def send(self, raw: str) -> None:
        frame = json.loads(raw)
        if frame["type"] == "message.send":
            await self.stack.candidate_message(self.session, frame["payload"])

    async def close(self) -> None:
        self.drop(1000)

    def drop(self, code: int) -> None:
        if self.close_code is None:
            self.close_code = code
            if self in self.session.sockets:
                self.session.sockets.remove(self)
            self.queue.put_nowait(None)

    def __aiter__(self) -> "FakeWS":
        return self

    async def __anext__(self) -> str:
        raw = await self.queue.get()
        if raw is None:
            raise StopAsyncIteration
        return raw


class FakeStack:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.by_token: dict[str, Session] = {}
        self.admins: dict[str, dict[str, str]] = {}
        self.positions: dict[str, dict[str, Any]] = {}
        self.rubric = load_contract("internal_rubric")
        self.report_fixture = load_contract("report")
        self._worker_log: list[dict[str, Any]] = []
        self.runner_kwargs: dict[str, Any] = {}
        self.draft_failures = 0  # fail_report_draft: 남은 주입 횟수
        self.llm_faults: dict[str, int] = {}
        self.catalog_version = "cat-debate-fake"
        self.restarted = 0
        self.down = False
        self.transports = {
            "public": httpx.MockTransport(self.public),
            "internal": httpx.MockTransport(self.internal),
        }

    # ---- 공통 인증 ----------------------------------------------------------------------

    def bearer(self, request: httpx.Request) -> dict[str, Any] | None:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            return None
        try:
            return jwt.decode(header[7:], SECRET, algorithms=["HS256"])
        except jwt.PyJWTError:
            return None

    def issue(self, kind: str, subject: str, ttl: int = 3600) -> str:
        return jwt.encode(
            {"kind": kind, "sub": subject, "exp": int(time.time()) + ttl, "n": uuid.uuid4().hex},
            SECRET,
            "HS256",
        )

    # ---- 장애 주입·재시작 ----------------------------------------------------------------

    async def fail_report_draft(self, times: int) -> None:
        self.draft_failures = times

    async def set_llm_fault(self, role: str, times: int) -> None:
        self.llm_faults[role] = times

    def worker_log(self) -> list[dict[str, Any]]:
        return list(self._worker_log)

    async def restart_api(self) -> None:
        """api 프로세스 재시작: 연결 끊김, WS seq 0부터. 상태(DB)는 유지."""
        self.restarted += 1
        for session in self.sessions.values():
            for ws in list(session.sockets):
                ws.drop(1006)
            session.seq = 0
        self.down = True
        await asyncio.sleep(0.05)
        self.down = False
        for session in self.sessions.values():
            if session.status == "in_progress":
                self.tick(session)

    def db(self) -> "FakeDb":
        return FakeDb(self)

    def connector(self):
        async def connect(url: str) -> FakeWS:
            match = re.search(r"/ws/sessions/([^?]+)\?token=(.+)$", url)
            session = self.sessions[match.group(1)]
            claims = jwt.decode(match.group(2), SECRET, algorithms=["HS256"])
            if claims["sub"] != session.id:
                raise ConnectionError("4403")
            ws = FakeWS(self, session)
            for old in list(session.sockets):
                old.drop(4000)
            session.sockets.append(ws)
            return ws

        return connect

    # ---- 공개 REST -----------------------------------------------------------------------

    async def public(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("api restarting")
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else None
        if path.startswith("/internal"):
            return error(404, "not_found")
        if path.startswith("/admin"):
            return self.admin(request, method, path, body)
        if path.startswith("/s/"):
            return self.entry_routes(request, method, path, body)
        if path.startswith("/sessions/"):
            return self.session_routes(request, method, path, body)
        return error(404, "not_found")

    def admin_claims(self, request: httpx.Request) -> dict[str, Any] | None:
        claims = self.bearer(request)
        return claims if claims and claims.get("kind") == "admin" else None

    def admin(self, request: httpx.Request, method: str, path: str, body: Any) -> httpx.Response:
        if path == "/admin/auth/signup":
            if body["email"] in self.admins:
                return error(409, "conflict")
            self.admins[body["email"]] = {
                "company": f"co-{len(self.admins) + 1}",
                "password": body["password"],
            }
            return response(
                200, {"token": self.issue("admin", self.admins[body["email"]]["company"])}
            )
        if path == "/admin/auth/login":
            admin = self.admins.get(body["email"])
            if not admin or admin["password"] != body["password"]:
                return error(401, "unauthorized")
            return response(200, {"token": self.issue("admin", admin["company"])})
        claims = self.admin_claims(request)
        if not claims:
            return error(401, "unauthorized")
        company = claims["sub"]
        if path == "/admin/positions" and method == "POST":
            if "elements" in body or body.get("session_minutes", 60) not in (60, 15):
                return error(422, "invalid_input")
            pos = {
                "id": f"pos-{len(self.positions) + 1}",
                "name": body["name"],
                "session_minutes": body.get("session_minutes", 60),
                "company": company,
                "seats": [
                    {
                        "seat": s,
                        "name": NAMES[s],
                        "persona_type": catalog.seats()[s],
                        "avatar_url": f"/assets/missions/debate/avatars/avatar-0{i + 1}.png",
                    }
                    for i, s in enumerate(SEATS)
                ],
                "keywords": [v["label"] for v in catalog.keywords().values()],
                "catalog_version": self.catalog_version,
            }
            self.positions[pos["id"]] = pos
            return response(200, {k: v for k, v in pos.items() if k != "company"})
        match = re.fullmatch(r"/admin/positions/([^/]+)/(invites|candidates)", path)
        if match:
            pos = self.positions.get(match.group(1))
            if not pos or pos["company"] != company:
                return error(404, "not_found")
            if match.group(2) == "candidates":
                return response(
                    200,
                    [
                        {"session_id": s.id, "alias": s.alias, "status": s.status}
                        for s in self.sessions.values()
                        if s.position is pos
                    ],
                )
            return self.invite(pos, body)
        match = re.fullmatch(r"/admin/sessions/([^/]+)(/.*)", path)
        session = self.sessions.get(match.group(1)) if match else None
        if not session or session.position["company"] != company:
            return error(404, "not_found")
        return self.admin_session(session, method, match.group(2), body)

    def invite(self, pos: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        previous = self.sessions.get(body.get("previous_session_id") or "")
        if body.get("previous_session_id"):
            if previous is None or previous.status not in ("cancelled", "failed"):
                return error(422, "invalid_previous_session")
            previous.superseded = True
        alias = body.get("alias") or (previous.alias if previous else None)
        if any(
            s.alias == alias and s.position is pos and not s.superseded
            for s in self.sessions.values()
        ):
            return error(409, "conflict")
        session = Session(self, pos["company"], pos, alias)
        self.sessions[session.id] = session
        self.by_token[session.invite_token] = session
        return response(
            200,
            {
                "session_id": session.id,
                "invite_url": f"http://localhost:3000/s/{session.invite_token}",
                "expires_at": iso(datetime.now(UTC) + timedelta(hours=72)),
                "notice_text": "안내",
            },
        )

    def admin_session(self, s: Session, method: str, path: str, body: Any) -> httpx.Response:
        if path == "/reissue" and method == "POST":
            if s.status != "invited":
                return error(409, "conflict")
            del self.by_token[s.invite_token]
            s.invite_token = uuid.uuid4().hex
            self.by_token[s.invite_token] = s
            return response(
                200,
                {
                    "session_id": s.id,
                    "invite_url": f"http://localhost:3000/s/{s.invite_token}",
                    "expires_at": iso(datetime.now(UTC) + timedelta(hours=72)),
                    "notice_text": "안내",
                },
            )
        if path == "/report" and method == "GET":
            if s.failed_stage == "report":
                failed = {"failed_stage": "report", "failed_step": "draft"}
                return response(
                    409,
                    {
                        "error": {
                            "code": "report_failed",
                            "message": "report generation failed",
                            **failed,
                        }
                    },
                )
            if s.report is None:
                return error(409, "report_not_ready")
            return response(200, self.merged_report(s))
        if path == "/report/regenerate" and method == "POST":
            if s.failed_stage != "report":
                return error(409, "conflict")
            s.failed_stage = None
            self.generate_report(s)
            return response(200, {"status": "completed"})
        if path == "/record" and method == "GET":
            return response(200, self.record(s))
        if path == "/review" and method == "POST":
            return self.review_route(s, body)
        if path == "/report/notes" and method == "POST":
            if s.status not in ("approving", "reviewed"):
                return error(409, "conflict")
            s.notes.append(
                {
                    "id": f"n-{len(s.notes) + 1}",
                    "text": body["text"],
                    "reviewer_name": body["reviewer_name"],
                    "created_at": iso(s.now()),
                }
            )
            return response(200, {"notes": s.notes})
        return error(404, "not_found")

    def entry_routes(
        self, request: httpx.Request, method: str, path: str, body: Any
    ) -> httpx.Response:
        parts = path.split("/")
        session = self.by_token.get(parts[2])
        if session is None:
            return error(404, "not_found")
        action = parts[3] if len(parts) > 3 else ""
        if method == "GET" and not action:
            entry = {
                **load_contract("entry"),
                "session_id": session.id,
                "status": session.status,
                "default_display_name": session.alias,
                "duration_minutes": session.minutes,
            }
            if session.failed_stage:
                entry["failed_stage"] = session.failed_stage
            if session.status not in ("invited", "in_progress"):
                entry["debrief_token"] = self.issue("debrief", session.id)
            return response(200, entry)
        if action == "consent" and method == "POST":
            session.consented = True
            session.display_name = (body or {}).get("display_name") or session.alias
            return response(
                200,
                {"session_token": self.issue("candidate", session.id), "session_id": session.id},
            )
        claims = self.bearer(request)
        if action == "resume" and method == "POST":
            if session.status != "in_progress":
                return error(409, "conflict")
            return response(
                200,
                {"session_token": self.issue("candidate", session.id), "session_id": session.id},
            )
        if action == "start" and method == "POST":
            if not claims or claims["sub"] != session.id:
                return error(401, "unauthorized")
            if session.status == "invited":
                self.start(session)
            return response(
                200,
                {
                    "session_id": session.id,
                    "started_at": iso(session.started_at),
                    "ends_at": iso(session.ends_at),
                    "phase": "keyword",
                },
            )
        return error(404, "not_found")

    def session_routes(
        self, request: httpx.Request, method: str, path: str, body: Any
    ) -> httpx.Response:
        parts = path.split("/")
        session = self.sessions.get(parts[2])
        claims = self.bearer(request)
        if claims is None or claims.get("kind") not in ("candidate", "debrief"):
            return error(401, "unauthorized")
        if session is None or claims["sub"] != session.id:
            return error(403, "forbidden")
        if session.status == "in_progress":
            self.tick(session)
        rest = "/".join(parts[3:])
        if rest == "state" and method == "GET":
            return response(200, self.state(session))
        if rest == "materials" and method == "GET":
            return response(200, self.materials(session))
        if rest.startswith("materials/") and method == "GET":
            return self.material(session, parts[4])
        if rest == "assistant" and method == "POST":
            return self.assistant(session, body)
        if rest == "proposal" and method == "GET":
            return response(200, self.proposal_view(session))
        if rest == "proposal" and method == "PUT":
            return self.proposal_put(session, body)
        if rest == "proposal/submit" and method == "POST":
            return self.proposal_submit(session)
        match = re.fullmatch(r"slots/(\w+)/confirm", rest)
        if match and method == "POST":
            return self.confirm(session, match.group(1), body)
        return error(404, "not_found")

    # ---- 시작·상태 -----------------------------------------------------------------------

    def start(self, s: Session) -> None:
        s.status = "in_progress"
        s.phase = "keyword"
        s.started_at = s.phase_started_at = s.now()
        s.generation += 1
        s.emit(
            "phase.changed",
            {
                "phase": "keyword",
                "previous_phase": None,
                "reason": "start",
                "phase_started_at": iso(s.phase_started_at),
                "phase_deadline_at": iso(s.deadline("keyword")),
                "t_min": 0.0,
            },
        )
        self.timer(s)
        self.stage_start(s, "keyword")

    def timer(self, s: Session) -> None:
        end = s.deadline(s.phase) if s.phase != "completed" else s.now()
        s.emit(
            "timer",
            {
                "phase": s.phase,
                "phase_deadline_at": iso(end),
                "phase_remaining_seconds": max(int((end - s.now()).total_seconds()), 0),
                "session_remaining_seconds": max(int((s.ends_at - s.now()).total_seconds()), 0),
            },
        )

    def remaining_text(self, s: Session) -> str:
        left = (s.deadline(s.phase) - s.now()).total_seconds()
        return f"{max(round(left / 60), 1)}분" if left >= 60 else f"{max(int(left), 1)}초"

    def stage_start(self, s: Session, phase: str) -> dict[str, Any]:
        texts = {
            "keyword": "키워드 단계예요. 후보 물·불·나무·바람 중 하나를 함께 정해 주세요.",
            "problem": "문제 상황 단계예요. 키워드와 관련된 문제 상황 하나를 함께 정해 주세요.",
            "solution": "해결책 단계예요. 정한 문제의 해결 방향 하나를 함께 정해 주세요.",
            "proposal": "기획안 작성 단계예요. 기획안 탭이 열렸어요. 직접 쓰고 제출해 주세요.",
        }
        message = self.post(
            s,
            "system",
            f"{texts[phase]} 이 단계 {self.remaining_text(s)} 남았어요.",
            meta={"kind": "stage_start"},
        )
        s.agent_runs.append(self.run_record(s, "phase_started", []))
        if phase == "proposal":
            self.observe(s, "honesty_credit", message["id"])
            s.delivered.setdefault("honesty_credit", set())
        return message

    def run_record(
        self, s: Session, stimulus: str, speakers: list[dict[str, Any]], **extra: Any
    ) -> dict[str, Any]:
        decision = {
            "stimulus": stimulus,
            "generation": s.generation,
            "speakers": speakers,
            "fill": extra.get("fill"),
            "invite": extra.get("invite", False),
            "llm_used": False,
            "dropped_seats": [],
            "reason": "fake",
        }
        return {
            "run_id": str(uuid.uuid4()),
            "stimulus": stimulus,
            "status": "finished",
            "decision": decision,
            "usage": {
                "llm_calls": 0,
                "retries": 0,
                "tool_calls": 0,
                "cost_usd": 0.0,
                "latency_ms": 0,
            },
        }

    def public_slots(self, s: Session) -> list[dict[str, Any]]:
        rows = []
        for phase in catalog.PHASES[:3]:
            slot = s.slots.get(phase)
            keys = ("phase", "status", "value", "filled_by", "resolution", "t_min")
            rows.append(
                {k: slot[k] for k in keys}
                if slot
                else {
                    "phase": phase,
                    "status": "empty",
                    "value": None,
                    "filled_by": None,
                    "resolution": None,
                    "t_min": None,
                }
            )
        return rows

    def state(self, s: Session) -> dict[str, Any]:
        started = s.started_at is not None
        deadlines = catalog.phase_deadlines(s.minutes)
        budgets = [
            {
                "phase": p,
                "budget_min": catalog.BUDGET_MIN[p],
                "deadline_t_min": round(deadlines[p] * 60 / s.minutes, 2),
            }
            for p in catalog.PHASES
        ]
        phase = s.phase
        session = {
            "id": s.id,
            "alias": s.alias,
            "status": s.status,
            "phase": phase,
            "started_at": iso(s.started_at) if started else None,
            "ends_at": iso(s.ends_at) if started else None,
            "session_minutes": s.minutes,
            "phase_started_at": iso(s.phase_started_at) if started else None,
            "phase_deadline_at": iso(s.deadline(phase))
            if started and phase in catalog.PHASES
            else None,
            "phase_remaining_seconds": int((s.deadline(phase) - s.now()).total_seconds())
            if started and phase in catalog.PHASES
            else None,
            "session_remaining_seconds": int((s.ends_at - s.now()).total_seconds())
            if started
            else None,
            "office_start": "09:00",
            "office_end": "10:00",
            "completed_at": iso(s.completed_at) if s.completed_at else None,
        }
        participants = [{"id": "candidate", "display_name": s.display_name}] + [
            {
                "id": seat,
                "display_name": NAMES[seat],
                "avatar_url": f"/assets/missions/debate/avatars/avatar-0{i + 1}.png",
            }
            for i, seat in enumerate(SEATS)
        ]
        return {
            "session": session,
            "keyword_options": [
                {"option_key": k, "label": v["label"]} for k, v in catalog.keywords().items()
            ],
            "phases": budgets,
            "participants": participants,
            "slots": self.public_slots(s),
            "messages": [s.message_payload(m) for m in s.messages],
            "materials": self.materials(s),
            "proposal": self.proposal_view(s),
            "assistant": [
                {k: c[k] for k in ("call_id", "request", "response", "cited_material_ids", "t_min")}
                for c in s.assistant_calls
            ],
            "typing": [],
            "active_runs": [],
            "connection_gaps": [],
            "ws_seq": s.seq,
        }

    def materials(self, s: Session) -> dict[str, Any]:
        if not s.keyword_key:
            return {"locked": True, "keyword": None, "materials": []}
        entry = catalog.keywords()[s.keyword_key]
        return {
            "locked": False,
            "keyword": entry["label"],
            "materials": [
                {"material_id": m["material_id"], "title": m["title"]} for m in entry["materials"]
            ],
        }

    def material(self, s: Session, material_id: str) -> httpx.Response:
        listing = self.materials(s)
        found = next((m for m in listing["materials"] if m["material_id"] == material_id), None)
        if listing["locked"] or not found:
            return error(423, "not_released")
        return response(200, {**found, "content": f"# {found['title']}\n본문"})

    def assistant(self, s: Session, body: dict[str, Any]) -> httpx.Response:
        listing = self.materials(s)
        if listing["locked"]:
            return response(
                200,
                {
                    "call_id": "locked",
                    "message_id": "x",
                    "content": "키워드가 정해지면 자료를 찾을 수 있어요.",
                    "cited_material_ids": [],
                    "tool_calls": [],
                },
            )
        cited = [listing["materials"][0]["material_id"]]
        call = {
            "call_id": f"ac-{len(s.assistant_calls) + 1}",
            "request": body["prompt"],
            "response": f"{listing['materials'][0]['title']} 자료에 따르면 관련 수치가 있어요.",
            "cited_material_ids": cited,
            "t_min": s.t_min(),
            "created_at": iso(s.now()),
        }
        s.assistant_calls.append(call)
        return response(
            200,
            {
                "call_id": call["call_id"],
                "message_id": f"am-{len(s.assistant_calls)}",
                "content": call["response"],
                "cited_material_ids": cited,
                "tool_calls": [{"name": "search_materials", "query": body["prompt"]}],
            },
        )

    # ---- 기획안·제출 ---------------------------------------------------------------------

    def proposal_view(self, s: Session) -> dict[str, Any]:
        p = s.proposal
        return {
            "sections": dict(p["sections"]),
            "revision": p["revision"],
            "updated_at": p["updated_at"] or iso(s.now()),
            "submitted_at": p["submitted_at"],
        }

    def proposal_put(self, s: Session, body: dict[str, Any]) -> httpx.Response:
        if s.status != "in_progress":
            return error(409, "session_ending")
        if body["revision"] != s.proposal["revision"]:
            return error(409, "conflict")
        s.proposal["sections"] = dict(body["sections"])
        s.proposal["revision"] += 1
        s.proposal["updated_at"] = iso(s.now())
        return response(
            200, {"revision": s.proposal["revision"], "updated_at": s.proposal["updated_at"]}
        )

    def proposal_submit(self, s: Session) -> httpx.Response:
        if s.status != "in_progress":
            return response(
                200,
                {
                    "status": "completed",
                    "completed_at": iso(s.completed_at),
                    "submitted_at": s.proposal["submitted_at"],
                },
            )
        if s.phase != "proposal":
            return error(409, "phase_not_open")
        self.finish(s, "submitted", auto=False)
        return response(
            200,
            {
                "status": "completed",
                "completed_at": iso(s.completed_at),
                "submitted_at": s.proposal["submitted_at"],
            },
        )

    def finish(self, s: Session, reason: str, auto: bool) -> None:
        s.status = "completed"
        s.phase = "completed"
        s.completed_at = s.now()
        s.proposal["submitted_at"] = iso(s.completed_at)
        s.proposal["auto_submitted"] = auto
        s.emit("session.ended", {"reason": reason})
        self.generate_report(s)

    # ---- 칸 확정 -------------------------------------------------------------------------

    def confirm(self, s: Session, phase: str, body: dict[str, Any]) -> httpx.Response:
        if s.status != "in_progress":
            return error(409, "session_ending")
        if phase != s.phase or phase not in catalog.PHASES[:3]:
            return error(409, "phase_not_open")
        if phase in s.slots:
            if s.slots[phase]["value"] == body["value"]:
                return response(
                    200,
                    {"slot": self.public_slots(s)[catalog.PHASES.index(phase)], "phase": s.phase},
                )
            return error(409, "slot_confirmed")
        option = None
        if phase == "keyword":
            key = body.get("option_key")
            words = catalog.keywords()
            if key not in words:
                return error(422, "invalid_keyword")
            body = {**body, "value": words[key]["label"]}
            option = self.option_for(s, phase, key, words[key]["label"], "candidate", None)
        else:
            if not body.get("value"):
                return error(422, "invalid_input")
            key = body.get("option_key")
            option = next(
                (
                    o
                    for o in s.options
                    if o["phase"] == phase
                    and (o["key"] == key or _norm(o["label"]) == _norm(body["value"]))
                ),
                None,
            )
        self.confirm_slot(s, phase, body["value"], option, "candidate", "consensus")
        return response(
            200, {"slot": self.public_slots(s)[catalog.PHASES.index(phase)], "phase": s.phase}
        )

    def confirm_slot(
        self,
        s: Session,
        phase: str,
        value: str,
        option: dict[str, Any] | None,
        filled_by: str,
        resolution: str,
    ) -> None:
        """칸 확정 → slot.confirmed → 단계 전환(phase.changed·timer·materials.opened).

        공지 메시지는 호출 쪽이 먼저 게시한다.
        """
        support = self.support_snapshot(s, phase)
        s.slots[phase] = {
            "phase": phase,
            "status": "confirmed",
            "value": value,
            "option_id": option["id"] if option else None,
            "filled_by": filled_by,
            "resolution": resolution,
            "support": support,
            "confirmed_at": iso(s.now()),
            "t_min": s.t_min(),
        }
        s.emit(
            "slot.confirmed",
            {
                "phase": phase,
                "value": value,
                "filled_by": filled_by,
                "resolution": resolution,
                "t_min": s.t_min(),
            },
        )
        previous = phase
        index = catalog.PHASES.index(phase) + 1
        s.phase = catalog.PHASES[index]
        s.phase_started_at = s.now()
        s.generation += 1
        if phase == "keyword":
            s.keyword_key = option["key"] if option else None
        if s.phase == "proposal":
            for name in catalog.PHASES[:3]:
                if not s.proposal["sections"][name]:
                    s.proposal["sections"][name] = s.slots[name]["value"]
        s.emit(
            "phase.changed",
            {
                "phase": s.phase,
                "previous_phase": previous,
                "reason": "timeout" if resolution == "timeout" else "confirmed",
                "phase_started_at": iso(s.phase_started_at),
                "phase_deadline_at": iso(s.deadline(s.phase)),
                "t_min": s.t_min(),
            },
        )
        self.timer(s)
        if phase == "keyword":
            listing = self.materials(s)
            s.emit(
                "materials.opened",
                {"keyword": listing["keyword"], "materials": listing["materials"]},
            )
        self.stage_start(s, s.phase)

    def support_snapshot(self, s: Session, phase: str) -> list[dict[str, Any]]:
        rows = []
        for o in s.options:
            if o["phase"] == phase:
                rows.append(
                    {
                        "option_id": o["id"],
                        "label": o["label"],
                        "supporters": [p for p, v in s.stances[phase].items() if v == o["id"]],
                    }
                )
        return rows

    # ---- 메시지·안·입장 ------------------------------------------------------------------

    def post(
        self,
        s: Session,
        sender: str,
        content: str,
        *,
        is_invite: bool = False,
        card_ids: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        origin: str | None = None,
        run_seq: int = 0,
    ) -> dict[str, Any]:
        at = s.now()
        message = {
            "id": f"m-{len(s.messages) + 1}",
            "sender": sender,
            "content": content,
            "phase": s.phase,
            "is_invite": is_invite,
            "t_min": s.t_min(at),
            "created_at": iso(at),
            "card_ids": card_ids or [],
            "meta": meta or {},
            "origin": origin,
            "intent": None,
        }
        s.messages.append(message)
        s.emit("message.new", s.message_payload(message))
        return message

    def option_for(
        self, s: Session, phase: str, key: str | None, label: str, proposer: str, source: str | None
    ) -> dict[str, Any]:
        found = next(
            (
                o
                for o in s.options
                if o["phase"] == phase
                and ((key and o["key"] == key) or _norm(o["label"]) == _norm(label))
            ),
            None,
        )
        if found:
            return found
        option = {
            "id": f"opt-{len(s.options) + 1}",
            "phase": phase,
            "key": key,
            "label": label,
            "proposer": proposer,
            "source_message_id": source,
            "t_min": s.t_min(),
            "created_at": iso(s.now()),
        }
        s.options.append(option)
        return option

    def classify(self, s: Session, content: str, phase: str) -> dict[str, Any]:
        """결정적 LLM 모드의 의도 분류 규칙."""
        options = option_catalog(s.keyword_key).get(phase, [])
        hits = sorted(
            ((content.find(o["label"]), o) for o in options if o["label"] in content),
            key=lambda t: t[0],
        )
        target = hits[0][1] if hits else None
        addressed = [seat for seat in SEATS if NAMES[seat] in content]
        if any(w in content for w in OPPOSE_WORDS):
            intent = "oppose" if target else "other"
        elif any(w in content for w in CONFIRM_WORDS):
            intent = "agree" if target else "other"
        elif "?" in content or "？" in content:
            intent = "question"
        else:
            intent = "propose" if target else "other"
        confidence = (
            "high" if (target and len(hits) == 1) or intent in ("question", "other") else "low"
        )
        return {
            "intent": intent,
            "target": target,
            "confidence": confidence,
            "addressed_to": addressed,
        }

    async def candidate_message(self, s: Session, payload: dict[str, Any]) -> None:
        if s.status != "in_progress":
            s.emit("error", {"code": "session_ending", "message": "ended"})
            return
        content = payload["content"]
        phase = s.phase
        origin = s.origin_next
        s.origin_next = "spontaneous"
        s.generation += 1
        message = self.post(s, "candidate", content, origin=origin)
        s.emit(
            "message.ack",
            {
                "client_msg_id": payload["client_msg_id"],
                "message_id": message["id"],
                "run_id": str(uuid.uuid4()),
            },
        )
        s.last_candidate_at = s.now()
        verdict = self.classify(s, content, phase)
        message["intent"] = {
            "intent": verdict["intent"],
            "target_option_id": None,
            "addressed_to": verdict["addressed_to"],
            "latency_ms": 900,
        }
        s.intents[message["id"]] = message["intent"]
        option = None
        target = verdict["target"]
        if target and verdict["intent"] in ("propose", "agree", "oppose"):
            existing = next(
                (o for o in s.options if o["phase"] == phase and o["key"] == target["key"]), None
            )
            if existing is None and verdict["intent"] in ("propose", "agree"):
                existing = self.option_for(
                    s, phase, target["key"], target["label"], "candidate", message["id"]
                )
            option = existing
            message["intent"]["target_option_id"] = option["id"] if option else None
        s.confirmed[phase] = None
        if option and verdict["confidence"] == "high":
            if verdict["intent"] in ("propose", "agree"):
                s.stances[phase]["candidate"] = option["id"]
                s.confirmed[phase] = option["id"]
            elif s.stances[phase].get("candidate") == option["id"]:
                s.stances[phase]["candidate"] = None
        self.persuade(s, phase, verdict, option)
        if self.try_fill(s, phase):
            return
        # 이번 발언으로 지지가 바뀌어도 같은 run에서 다시 판정하지 않는다(B2)
        speakers = self.respond(s, phase, verdict, message)
        s.agent_runs.append(
            self.run_record(
                s,
                "message",
                [
                    {"seat": seat, "purpose": "respond", "option_id": None, "card_ids": []}
                    for seat in speakers
                ],
            )
        )

    def persuade(
        self, s: Session, phase: str, verdict: dict[str, Any], option: dict[str, Any] | None
    ) -> None:
        """서지안 설득 가능 규칙: 다른 안의 명시적 제안·동의를 두 번 받으면 지원자 안으로 옮긴다
        (단계당 1회). 반대만 한 발언은 세지 않는다(B2)."""
        if (
            phase not in catalog.PHASES[:3]
            or phase in s.moved
            or option is None
            or verdict["confidence"] != "high"
        ):
            return
        own = s.stances[phase].get("p3")
        if own is None:
            return
        if verdict["intent"] in ("propose", "agree") and option["id"] != own:
            s.pressure[phase] = s.pressure.get(phase, 0) + 1
        if s.pressure.get(phase, 0) >= 2 and s.stances[phase].get("candidate"):
            s.stances[phase]["p3"] = s.stances[phase]["candidate"]
            s.moved.add(phase)

    def consensus_option(self, s: Session, phase: str) -> dict[str, Any] | None:
        stances = s.stances[phase]
        for o in s.options:
            if o["phase"] != phase:
                continue
            supporters = [p for p, v in stances.items() if v == o["id"]]
            if (
                len(supporters) >= 3
                and stances.get("candidate") == o["id"]
                and s.confirmed.get(phase) == o["id"]
            ):
                return o
        return None

    def try_fill(self, s: Session, phase: str) -> bool:
        if phase in s.slots or phase not in catalog.PHASES[:3]:
            return False
        option = self.consensus_option(s, phase)
        if option is None:
            return False
        supporters = [p for p, v in s.stances[phase].items() if v == option["id"]]
        seat = next(x for x in FILL_PRIORITY if x in supporters)
        self.post(
            s,
            seat,
            f"그럼 {option['label']}로 정리할게요.",
            meta={"kind": "consensus_fill"},
            run_seq=5,
        )
        self.confirm_slot(s, phase, option["label"], option, seat, "consensus")
        s.agent_runs.append(
            self.run_record(s, "message", [], fill={"option_id": option["id"], "seat": seat})
        )
        return True

    # ---- 페르소나 ------------------------------------------------------------------------

    def persona_option(self, s: Session, phase: str, seat: str) -> dict[str, Any] | None:
        """자리의 고정 안(단계별 입장). 문제 상황은 잠긴 기준안(카드 뒤)."""
        if not s.keyword_key and phase != "keyword":
            return None
        if phase == "keyword":
            key = {"p1": "fire", "p2": "tree", "p3": "wind"}[seat]
            return self.option_for(s, phase, key, catalog.keywords()[key]["label"], seat, None)
        if phase == "problem":
            if seat == "p2" and s.locked:
                problems = [p["key"] for p in catalog.keywords()[s.keyword_key]["problems"]]
                lead_key = next(o["key"] for o in s.options if o["id"] == s.locked)
                key = [k for k in problems if k != lead_key][-1]
            elif s.locked:
                return next(o for o in s.options if o["id"] == s.locked)
            else:
                return None
        else:
            key = f"{s.keyword_key}_{ {'p1': 's1', 'p2': 's3', 'p3': 's2'}[seat] }"
        label = next(x["label"] for x in option_catalog(s.keyword_key)[phase] if x["key"] == key)
        return self.option_for(s, phase, key, label, seat, None)

    def speak(
        self,
        s: Session,
        seat: str,
        text: str | None = None,
        *,
        card_ids: list[str] | None = None,
        push: bool = True,
    ) -> dict[str, Any]:
        phase = s.phase
        option = self.persona_option(s, phase, seat) if push else None
        if text is None:
            text = (
                f"{option['label']} 쪽이 맞다고 생각해요."
                if option
                else "자료를 같이 보면서 얘기해 봐요."
            )
        message = self.post(s, seat, text, card_ids=card_ids)
        if option and not (seat == "p3" and phase in s.moved):
            s.stances[phase][seat] = s.stances[phase].get(seat) or option["id"]
        return message

    def respond(
        self, s: Session, phase: str, verdict: dict[str, Any], message: dict[str, Any]
    ) -> list[str]:
        if (
            self.llm_faults.get("persona", 0) > 0
        ):  # 페르소나 LLM 타임아웃(최초 + 재시도 2회): 그 발언 생략
            self.llm_faults["persona"] = max(self.llm_faults["persona"] - 3, 0)
            return []
        addressed = verdict["addressed_to"]
        if addressed:
            speakers = [addressed[0]]
        elif verdict["intent"] in ("oppose", "question"):
            speakers = ["p1"]
        else:
            speakers = ["p3"]
        for seat in speakers:
            if seat == "p3" and phase == "problem" and not s.locked:
                self.speak(s, seat, push=False)
            else:
                self.speak(s, seat)
        return speakers

    # ---- 스케줄러 틱 ---------------------------------------------------------------------

    def card_due(self, s: Session, card_id: str) -> datetime | None:
        offset = catalog.card_offset_min(card_id, s.minutes)
        return None if offset is None else s.phase_started_at + timedelta(minutes=offset)

    def pending(self, s: Session) -> list[tuple[datetime, str, str]]:
        """(예정 시각, 종류, 대상). 지금 단계에서 처리해야 할 것."""
        if s.status != "in_progress" or s.phase not in catalog.PHASES:
            return []
        items: list[tuple[datetime, str, str]] = []
        phase = s.phase
        if phase in catalog.PHASES[:3] and phase not in s.slots:
            items.append((s.deadline(phase), "timeout", phase))
        if phase == "proposal":
            items.append((s.ends_at, "autosubmit", phase))
        for card_id, card in catalog.cards().items():
            if card["stage_id"] != phase or card_id == "honesty_credit":
                continue
            if card_id in s.delivered and self.card_complete(s, card_id):
                continue
            due = self.card_due(s, card_id)
            if due and not any(o["card_id"] == card_id for o in s.observations):
                items.append((due, "card", card_id))
        if phase in catalog.PHASES[:3] and phase not in s.invited:
            base = max(s.last_candidate_at or s.phase_started_at, s.phase_started_at)
            items.append(
                (base + timedelta(seconds=s.minutes * SILENCE_SECONDS_PER_MIN), "idle", phase)
            )
        return items

    def card_complete(self, s: Session, card_id: str) -> bool:
        actors = {
            "flexibility_majority": {"p1", "p3"},
            "listening_pressure": {"p1"},
            "inclusion_silence": {"p2"},
        }.get(card_id)
        return (
            actors is not None
            and actors <= s.delivered.get(card_id, set())
            or (actors is None and bool(s.delivered.get(card_id)))
        )

    def tick(self, s: Session) -> None:
        for _ in range(20):
            due = sorted(p for p in self.pending(s) if p[0] <= s.now())
            if not due:
                return
            _, kind, target = due[0]
            if kind == "timeout":
                self.timeout_fill(s, target)
            elif kind == "autosubmit":
                self.finish(s, "timeout", auto=True)
            elif kind == "card":
                self.run_card(s, target)
            elif kind == "idle":
                s.invited.add(target)
                self.post(s, "p3", f"{s.display_name}님은 어떻게 생각하세요?", is_invite=True)
                s.origin_next = "invited"
                s.agent_runs.append(
                    self.run_record(
                        s,
                        "idle",
                        [{"seat": "p3", "purpose": "invite", "option_id": None, "card_ids": []}],
                        invite=True,
                    )
                )

    def observe(self, s: Session, card_id: str, trigger: str) -> None:
        if any(o["card_id"] == card_id for o in s.observations):
            return
        at = s.now()
        s.observations.append(
            {
                "observation_id": f"ob-{len(s.observations) + 1}",
                "card_id": card_id,
                "element": catalog.cards()[card_id]["element"],
                "phase": s.phase,
                "fired_at": iso(at),
                "t_min": s.t_min(at),
                "trigger_message_id": trigger,
            }
        )

    def lock_lead(self, s: Session) -> dict[str, Any]:
        if s.locked:
            return next(o for o in s.options if o["id"] == s.locked)
        problems = catalog.keywords()[s.keyword_key]["problems"]
        support = s.stances["problem"].get("candidate")
        mine = next((o["key"] for o in s.options if o["id"] == support), None)
        key = next((p["key"] for p in problems if p["key"] != mine), problems[0]["key"])
        label = next(p["label"] for p in problems if p["key"] == key)
        option = self.option_for(s, "problem", key, label, "p1", None)
        s.locked = option["id"]
        return option

    def run_card(self, s: Session, card_id: str) -> None:
        phase = s.phase
        speakers: list[dict[str, Any]] = []
        if card_id == "persuasion_keyword":
            support = s.stances["keyword"].get("candidate")
            mine = next((o["key"] for o in s.options if o["id"] == support), None)
            seat = "p3" if mine != "wind" else "p1"
            self.persona_option(s, phase, seat)
            m = self.speak(
                s,
                seat,
                "그 키워드를 고르면 구체적으로 어떤 문제를 풀 수 있나요?",
                card_ids=[card_id],
            )
            self.observe(s, card_id, m["id"])
            s.delivered.setdefault(card_id, set()).add(seat)
            speakers = [{"seat": seat, "purpose": "card", "option_id": None, "card_ids": [card_id]}]
        elif card_id in ("flexibility_majority", "listening_pressure"):
            lead = self.lock_lead(s)
            m1 = self.speak(
                s,
                "p1",
                f"{lead['label']}가 기준이에요. 그건 핵심이 아니에요.",
                card_ids=["flexibility_majority", "listening_pressure"],
            )
            s.stances[phase]["p1"] = lead["id"]
            for card in ("flexibility_majority", "listening_pressure"):
                self.observe(s, card, m1["id"])
                s.delivered.setdefault(card, set()).add("p1")
            self.speak(
                s, "p3", f"저도 {lead['label']}가 맞다고 봐요.", card_ids=["flexibility_majority"]
            )
            s.stances[phase]["p3"] = lead["id"]
            s.delivered["flexibility_majority"].add("p3")
            speakers = [
                {
                    "seat": "p1",
                    "purpose": "card",
                    "option_id": lead["id"],
                    "card_ids": ["flexibility_majority", "listening_pressure"],
                },
                {
                    "seat": "p3",
                    "purpose": "card",
                    "option_id": lead["id"],
                    "card_ids": ["flexibility_majority"],
                },
            ]
        elif card_id == "inclusion_silence":
            m = self.post(
                s,
                "p2",
                "저… 하나 생각한 게 있긴 한데… 아니에요, 먼저 얘기하세요.",
                card_ids=[card_id],
            )
            self.observe(s, card_id, m["id"])
            s.delivered.setdefault(card_id, set()).add("p2")
            speakers = [{"seat": "p2", "purpose": "card", "option_id": None, "card_ids": [card_id]}]
        elif card_id == "persuasion_solution":
            support = s.stances["solution"].get("candidate")
            mine = next((o["key"] for o in s.options if o["id"] == support), None)
            seat = "p3" if not (mine or "").endswith("_s2") else "p1"
            option = self.persona_option(s, phase, seat)
            m = self.speak(
                s,
                seat,
                f"{option['label']} 쪽이 낫고, 그 안은 비용이 걱정돼요.",
                card_ids=[card_id],
            )
            self.observe(s, card_id, m["id"])
            s.delivered.setdefault(card_id, set()).add(seat)
            speakers = [
                {"seat": seat, "purpose": "card", "option_id": option["id"], "card_ids": [card_id]}
            ]
        s.agent_runs.append(self.run_record(s, "card_due", speakers))
        self.try_fill(s, phase)

    def timeout_fill(self, s: Session, phase: str) -> None:
        stances = s.stances[phase]
        counts = {
            o["id"]: sum(1 for v in stances.values() if v == o["id"])
            for o in s.options
            if o["phase"] == phase
        }
        chosen = None
        if counts:
            top = max(counts.values())
            tied = [o for o in s.options if o["phase"] == phase and counts[o["id"]] == top]
            mine = stances.get("candidate")
            chosen = next((o for o in tied if o["id"] == mine), tied[0])
        else:
            key = "water" if phase == "keyword" else None
            chosen = (
                self.option_for(s, phase, key, "물", "p3", None) if phase == "keyword" else None
            )
        if chosen is None:
            problems = catalog.keywords()[s.keyword_key][
                "problems" if phase == "problem" else "solutions"
            ]
            chosen = self.option_for(s, phase, problems[0]["key"], problems[0]["label"], "p3", None)
        self.post(
            s,
            "p3",
            f"시간이 다 돼서 대화에서 가장 많이 모인 의견인 {chosen['label']}로 할게요.",
            meta={"kind": "timeout_fill"},
        )
        self.confirm_slot(s, phase, chosen["label"], chosen, "p3", "timeout")

    def advance(self, s: Session, body: dict[str, Any]) -> httpx.Response:
        if s.status != "in_progress":
            return error(409, "conflict")
        if ("seconds" in body) == ("to" in body):
            return error(422, "invalid_input")
        if "seconds" in body:
            seconds = body["seconds"]
        else:
            target = body["to"]
            if target == "deadline":
                moment = s.deadline(s.phase)
            elif target == "idle":
                base = max(s.last_candidate_at or s.phase_started_at, s.phase_started_at)
                moment = base + timedelta(seconds=s.minutes * SILENCE_SECONDS_PER_MIN)
            else:
                moment = self.card_due(s, target.split(":", 1)[1]) or s.now()
            seconds = max(int((moment - s.now()).total_seconds()) + 1, 1)
        s.started_at -= timedelta(seconds=seconds)
        s.phase_started_at -= timedelta(seconds=seconds)
        if s.last_candidate_at:
            s.last_candidate_at -= timedelta(seconds=seconds)
        self.tick(s)
        return response(
            200,
            {
                "shifted_seconds": seconds,
                "phase": s.phase,
                "phase_deadline_at": iso(s.deadline(s.phase))
                if s.phase in catalog.PHASES
                else None,
                "ends_at": iso(s.ends_at),
            },
        )

    # ---- 워커·리포트 ---------------------------------------------------------------------

    def wlog(self, method: str, path: str, status: int, injected: bool = False) -> None:
        self._worker_log.append(
            {
                "method": method,
                "path": path,
                "status": status,
                "injected": injected,
                "at": time.time(),
            }
        )

    def generate_report(self, s: Session) -> None:
        """report.generate 소비: 평가(저장 1회) → draft 저장. 3회 실패하면 failed(report)."""
        for _ in range(3):
            if self.llm_faults.get("evaluator", 0) > 0:
                self.llm_faults["evaluator"] -= 1  # 평가 LLM 타임아웃: 저장 없이 큐 재시도
                continue
            if s.eval_run is None:
                s.eval_run = self.evaluate(s)
                self.wlog("POST", f"/internal/sessions/{s.id}/evaluations", 200)
            if self.draft_failures > 0:
                self.draft_failures -= 1
                self.wlog("POST", f"/internal/sessions/{s.id}/report/draft", 503, injected=True)
                continue
            s.report = self.build_report(s)
            s.report_stage = "draft"
            s.status = "report_ready"
            self.wlog("POST", f"/internal/sessions/{s.id}/report/draft", 200)
            return
        s.failed_stage = "report"

    def evaluate(self, s: Session) -> dict[str, Any]:
        """결정적 LLM 모드의 평가 출력: 지원자 메시지 근거가 있으면 3·medium, 없으면 기록 없음."""
        fired = {o["card_id"] for o in s.observations}
        candidate = [m for m in s.messages if m["sender"] == "candidate"]
        credit = s.proposal["sections"]["idea_credit"].strip()
        rows = []
        for element in self.rubric["elements"]:
            for dim in element["dimensions"]:
                did, core = dim["dimension_id"], dim["core_cards"]
                row = {
                    "dimension_id": did,
                    "level": None,
                    "no_record_reason": None,
                    "evidence_refs": [],
                    "rationale": "기록 없음",
                    "confidence": None,
                }
                if did == "hon_credit_record":
                    if credit:
                        refs = ["proposal:idea_credit"] + [
                            f"contribution:{o['id']}" for o in self.ai_adopted(s)
                        ]
                        row.update(
                            level=3,
                            evidence_refs=refs,
                            rationale="기여 칸을 적었고 대조 결과를 반영함(앵커 3).",
                            confidence="medium",
                        )
                    else:
                        row.update(
                            no_record_reason="auto_submitted_blank",
                            rationale="기여 칸이 비어 있음.",
                        )
                elif core and not any(c in fired for c in core):
                    row.update(
                        no_record_reason="event_not_fired", rationale="핵심 카드가 발생하지 않음."
                    )
                elif candidate:
                    row.update(
                        level=3,
                        evidence_refs=[f"msg:{candidate[0]['id']}"],
                        rationale="핵심 장면의 지원자 발언에 맞춰 판단함(앵커 3).",
                        confidence="medium",
                    )
                else:
                    row.update(no_record_reason="no_activity", rationale="지원자 활동이 없음.")
                rows.append(row)
        return {"run_id": f"run-eval-{s.id}-r{s.eval_round}", "dimensions": rows}

    def ai_adopted(self, s: Session) -> list[dict[str, Any]]:
        adopted = []
        for slot in s.slots.values():
            option = next((o for o in s.options if o["id"] == slot["option_id"]), None)
            if option and option["proposer"] in SEATS:
                adopted.append(option)
        return adopted

    def build_report(self, s: Session) -> dict[str, Any]:
        report = copy.deepcopy(self.report_fixture)
        dims = []
        meta = {
            d["dimension_id"]: (e["element"], d)
            for e in self.rubric["elements"]
            for d in e["dimensions"]
        }
        messages = {m["id"]: m for m in s.messages}
        for row in s.eval_run["dimensions"]:
            element, spec = meta[row["dimension_id"]]
            evidence = [self.evidence_item(s, ref, messages) for ref in row["evidence_refs"]]
            dims.append(
                {
                    "element": element,
                    "dimension_id": row["dimension_id"],
                    "name": spec["name"],
                    "level": row["level"],
                    "no_record_reason": row["no_record_reason"],
                    "rationale": row["rationale"],
                    "confidence": row["confidence"],
                    "reviewer_hint": None
                    if row["level"] is None
                    else next(
                        lv["reviewer_hint"] for lv in spec["levels"] if lv["level"] == row["level"]
                    ),
                    "adjusted_level": None,
                    "adjusted_no_record_reason": None,
                    "adjust_memo": None,
                    "evidence_refs": row["evidence_refs"],
                    "evidence": evidence,
                }
            )
        elements = []
        for name in ELEMENT_NAMES:
            levels = [d["level"] for d in dims if d["element"] == name and d["level"] is not None]
            elements.append(
                {
                    "element": name,
                    "name": name,
                    "level": int(sum(levels) / len(levels) + 0.5) if levels else None,
                    "summary": "지원자 발언을 근거로 판단했고 기록 없음 차원을 함께 밝힙니다.",
                    "dimension_count": sum(d["element"] == name for d in dims),
                    "no_record_count": sum(
                        d["element"] == name and d["level"] is None for d in dims
                    ),
                    "low_confidence_count": 0,
                    "adjusted_level": None,
                }
            )
        credit = s.proposal["sections"]["idea_credit"]
        check = []
        for option in self.ai_adopted(s):
            credited = [seat for seat in SEATS if NAMES[seat] in credit]
            check.append(
                {
                    "phase": option["phase"],
                    "option_id": option["id"],
                    "label": option["label"],
                    "first_proposer": option["proposer"],
                    "credited_in_proposal": credited,
                    "credit_quote": credit,
                }
            )
        report["session_id"] = s.id
        report["alias"] = s.alias
        report["stage"] = "draft"
        report["position"] = {"name": s.position["name"], "session_minutes": s.minutes}
        report["evaluation"] = {**report["evaluation"], "run_id": s.eval_run["run_id"]}
        report["elements"] = elements
        report["dimensions"] = dims
        report["contribution_check"] = check
        report["slots"] = [
            {
                "phase": p,
                "value": sl["value"],
                "option_id": sl["option_id"],
                "filled_by": sl["filled_by"],
                "resolution": sl["resolution"],
                "t_min": sl["t_min"],
                "first_proposer": next(
                    (o["proposer"] for o in s.options if o["id"] == sl["option_id"]), None
                ),
                "support": sl["support"],
            }
            for p, sl in s.slots.items()
        ]
        fired = {o["card_id"]: o for o in s.observations}
        report["cards"] = [
            {
                "card_id": c,
                "element": catalog.cards()[c]["element"],
                "fired_at": fired[c]["t_min"] if c in fired else None,
                "delivered": c in fired,
                "scene": catalog.cards()[c]["report_template"],
            }
            for c in catalog.cards()
        ]
        report["debrief"] = {"cards": [], "survey": None}
        report["review"] = None
        report["notes"] = []
        report["reviewed_at"] = None
        return report

    def evidence_item(
        self, s: Session, ref: str, messages: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        kind, _, key = ref.partition(":")
        if kind == "msg":
            m = messages[key]
            index = s.messages.index(m)

            def ctx(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
                keys = ("id", "sender", "content", "t_min")
                return [{k: x[k] for k in keys} for x in items]

            return {
                "ref": ref,
                "kind": "message",
                "counted": m["sender"] == "candidate",
                "speaker": m["sender"],
                "phase": m["phase"],
                "t_min": m["t_min"],
                "origin": m["origin"],
                "quote": m["content"],
                "context": {
                    "before": ctx(s.messages[max(index - 5, 0) : index]),
                    "after": ctx(s.messages[index + 1 : index + 4]),
                },
                "record_ref": {"type": "message", "id": m["id"], "t_min": m["t_min"]},
            }
        if kind == "proposal":
            return {
                "ref": ref,
                "kind": "proposal",
                "counted": True,
                "field": key,
                "quote": s.proposal["sections"][key],
                "record_ref": {
                    "type": "proposal_submit",
                    "id": "proposal",
                    "t_min": s.t_min(s.completed_at),
                },
            }
        return {
            "ref": ref,
            "kind": "contribution",
            "counted": False,
            "record_ref": {"type": "contribution", "id": key, "t_min": 0.0},
        }

    def merged_report(self, s: Session) -> dict[str, Any]:
        report = copy.deepcopy(s.report)
        report["stage"] = s.report_stage
        report["proposal"] = {
            "submitted_at": s.proposal["submitted_at"],
            "auto_submitted": s.proposal["auto_submitted"],
            "sections": dict(s.proposal["sections"]),
        }
        report["ai_usage"] = {
            "used": bool(s.assistant_calls),
            "events": [
                {
                    "call_id": c["call_id"],
                    "t_min": c["t_min"],
                    "request_quote": c["request"],
                    "response_quote": c["response"],
                    "cited_material_ids": c["cited_material_ids"],
                }
                for c in s.assistant_calls
            ],
        }
        last = {a["dimension_id"]: a for a in s.adjustments}
        for dim in report["dimensions"]:
            adj = last.get(dim["dimension_id"])
            if adj:
                dim["adjusted_level"], dim["adjusted_no_record_reason"], dim["adjust_memo"] = (
                    adj["level"],
                    adj.get("no_record_reason"),
                    adj["memo"],
                )
        for element in report["elements"]:
            if any(
                last.get(d["dimension_id"])
                for d in report["dimensions"]
                if d["element"] == element["element"]
            ):
                values = [
                    d["adjusted_level"] if last.get(d["dimension_id"]) else d["level"]
                    for d in report["dimensions"]
                    if d["element"] == element["element"]
                ]
                values = [v for v in values if v is not None]
                element["adjusted_level"] = int(sum(values) / len(values) + 0.5) if values else None
        report["review"] = s.review
        report["notes"] = s.notes
        report["reviewed_at"] = (
            s.review["reviewed_at"] if s.review and s.report_stage == "final" else None
        )
        return report

    def review_route(self, s: Session, body: dict[str, Any]) -> httpx.Response:
        if s.report is None or s.status not in ("report_ready", "approving", "reviewed"):
            return error(409, "conflict")
        if s.status in ("approving", "reviewed"):
            return (
                response(200, {"status": s.status})
                if body == s.review_body
                else error(409, "already_reviewed")
            )
        ids = {d["dimension_id"] for d in s.report["dimensions"]}
        seen: set[str] = set()
        if not (1 <= len(body.get("reviewer_name") or "") <= 40):
            return error(422, "invalid_input")
        for adj in body["adjustments"]:
            if adj["dimension_id"] not in ids:
                return error(422, "invalid_dimension")
            if not adj.get("memo"):
                return error(422, "memo_required")
            if adj["dimension_id"] in seen:
                return error(422, "duplicate_dimension")
            seen.add(adj["dimension_id"])
            level, reason = adj.get("level"), adj.get("no_record_reason")
            if (level is None) == (reason is None) or (level is not None and not 1 <= level <= 5):
                return error(422, "invalid_input")
        s.adjustments = list(body["adjustments"])
        s.review_body = body
        s.review = {"reviewer_name": body["reviewer_name"], "reviewed_at": iso(s.now())}
        s.status = "approving"
        self.wlog("POST", f"/internal/sessions/{s.id}/report/final", 200)
        s.report_stage = "final"  # 워커가 같은 draft content로 final 저장
        s.status = "reviewed"
        return response(200, {"status": "approving"})

    def record(self, s: Session) -> list[dict[str, Any]]:
        rows = [
            {
                "t_min": m["t_min"],
                "type": "message",
                "by": m["sender"],
                "summary": m["content"],
                "ref": {"type": "message", "id": m["id"], "t_min": m["t_min"]},
            }
            for m in s.messages
        ]
        rows += [
            {
                "t_min": sl["t_min"],
                "type": "slot",
                "by": sl["filled_by"],
                "summary": sl["value"],
                "ref": {"type": "slot", "id": p, "t_min": sl["t_min"]},
            }
            for p, sl in s.slots.items()
        ]
        return sorted(rows, key=lambda r: r["t_min"])

    # ---- 내부 REST -----------------------------------------------------------------------

    async def internal(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("api restarting")
        if request.headers.get("x-internal-token") != INTERNAL_TOKEN:
            return error(401, "unauthorized")
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        match = re.fullmatch(r"/internal/(?:test/)?sessions/([^/]+)(/.*)?", path)
        session = self.sessions.get(match.group(1)) if match else None
        if session is None:
            return error(404, "not_found")
        sub = match.group(2) or ""
        if sub == "/advance" and request.method == "POST":
            return self.advance(session, body)
        if session.status == "in_progress":
            self.tick(session)
        query = dict(request.url.params)
        reads = {
            "": self.internal_session,
            "/messages": self.internal_messages,
            "/observations": self.internal_observations,
            "/slots": lambda s: [{k: v for k, v in sl.items()} for sl in s.slots.values()],
            "/facts": lambda s: (
                self.build_report(s)["facts"] if s.eval_run else self.report_fixture["facts"]
            ),
            "/contributions": self.contributions,
            "/rubric": lambda s: self.rubric,
            "/proposal": lambda s: {
                **self.proposal_view(s),
                "auto_submitted": s.proposal["auto_submitted"],
            },
            "/assistant_calls": lambda s: [
                {
                    "id": c["call_id"],
                    "request": c["request"],
                    "response": c["response"],
                    "cited_material_ids": c["cited_material_ids"],
                    "created_at": c["created_at"],
                }
                for c in s.assistant_calls
            ],
            "/evaluations": lambda s: (
                {
                    "run_id": s.eval_run["run_id"],
                    "has_failed": False,
                    "dimensions": s.eval_run["dimensions"],
                }
                if s.eval_run
                else None
            ),
            "/debate_state": lambda s: {
                "phase": s.phase,
                "options": [
                    {"option_id": o["id"], "label": o["label"], "proposer": o["proposer"]}
                    for o in s.options
                ],
            },
        }
        if sub in reads and request.method == "GET":
            data = reads[sub](session, query) if sub == "/messages" else reads[sub](session)
            return response(200, data) if data is not None else error(404, "not_found")
        return error(404, "not_found")

    def internal_session(self, s: Session) -> dict[str, Any]:
        seats = [{"seat": "candidate", "display_name": s.display_name}] + [
            {
                "seat": seat,
                "display_name": NAMES[seat],
                "avatar_url": "",
                "persona_type": catalog.seats()[seat],
            }
            for seat in SEATS
        ]
        progress = [
            {
                "card_id": c,
                "locked_option_id": s.locked
                if c in ("flexibility_majority", "listening_pressure")
                else None,
                "delivered": sorted(v),
            }
            for c, v in s.delivered.items()
        ]
        return {
            "id": s.id,
            "status": s.status,
            "phase": s.phase,
            "alias": s.alias,
            "display_name": s.display_name,
            "position": {
                "mission_id": "debate",
                "session_minutes": s.minutes,
                "catalog_version": self.catalog_version,
            },
            "seats": seats,
            "started_at": iso(s.started_at) if s.started_at else None,
            "ends_at": iso(s.ends_at) if s.started_at else None,
            "phase_started_at": iso(s.phase_started_at) if s.phase_started_at else None,
            "phase_deadline_at": iso(s.deadline(s.phase)) if s.phase in catalog.PHASES else None,
            "completed_at": iso(s.completed_at) if s.completed_at else None,
            "ended_at": iso(s.completed_at) if s.completed_at else None,
            "t_min_now": s.t_min(),
            "generation": s.generation,
            "invite_delivered_phases": sorted(s.invited),
            "card_progress": progress,
            "connection_gaps": [],
        }

    def internal_messages(self, s: Session, query: dict[str, str]) -> list[dict[str, Any]]:
        return copy.deepcopy(s.messages)

    def internal_observations(self, s: Session) -> list[dict[str, Any]]:
        rows = []
        for o in s.observations:
            card = o["card_id"]
            done = self.card_complete(s, card) if card != "honesty_credit" else True
            rows.append({**o, "delivered": done})
        return rows

    def contributions(self, s: Session) -> dict[str, Any]:
        options = [
            {
                "option_id": o["id"],
                "phase": o["phase"],
                "label": o["label"],
                "first_proposer": o["proposer"],
                "source_message_id": o["source_message_id"],
                "t_min": o["t_min"],
                "adopted": any(sl["option_id"] == o["id"] for sl in s.slots.values()),
            }
            for o in s.options
        ]
        slots = [
            {
                "phase": p,
                "option_id": sl["option_id"],
                "first_proposer": next(
                    (o["proposer"] for o in s.options if o["id"] == sl["option_id"]), None
                ),
                "filled_by": sl["filled_by"],
                "resolution": sl["resolution"],
            }
            for p, sl in s.slots.items()
        ]
        return {"options": options, "slots": slots}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[.,!?·]", "", text)).strip().lower()


class FakeDb:
    """러너의 db 인터페이스(e2e.db.Db)."""

    available = True

    def __init__(self, stack: FakeStack) -> None:
        self.stack = stack

    async def agent_runs(self, session_id: str) -> list[dict[str, Any]]:
        return list(self.stack.sessions[session_id].agent_runs)

    async def costs(self, session_id: str) -> dict[str, Any]:
        return {"cost_usd": 0.0, "debate_cost_usd": 0.0}

    async def intent_latency_ms(self, session_id: str) -> list[int]:
        return [i["latency_ms"] for i in self.stack.sessions[session_id].intents.values()]

    async def close(self) -> None:
        return None
