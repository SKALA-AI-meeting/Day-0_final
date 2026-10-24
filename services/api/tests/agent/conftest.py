"""agent 단위 테스트 공용 가짜: 가짜 debate·rag·assessment MCP(메모리 상태), 가짜
hub(FakeSessionPorts).

LLM은 FakeLLM, DB·네트워크는 쓰지 않는다. 가짜 서버는 docs/contracts/mcp.md의 도구 입출력과 409 세부
코드
(stale_generation·no_consensus·slot_confirmed·phase_not_open)를 같은 형식으로 흉내 낸다.
"""

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest
from app.agent import intent as intent_mod
from app.agent.catalog import SEATS, Catalog
from app.agent.debate import DebateGraphRunner, DebateLLMs
from app.agent.llm import FakeLLM
from app.agent.mcp_client import (
    ASSESSMENT_TOOLS,
    RunTools,
    fail,
    function_spec,
    ok,
)
from app.agent.runner import Stimulus
from app.config import REPO_ROOT
from app.ports import FakeSessionPorts

MISSION_DIR = REPO_ROOT / "scenarios" / "missions" / "debate"
PHASES = ("keyword", "problem", "solution", "proposal")


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return Catalog.from_dir(MISSION_DIR)


class FakeServer:
    """debate-mcp + rag-mcp 가짜(메모리). assessment 도구는 서버에 없다(클라이언트 분리)."""

    def __init__(self, catalog: Catalog, ports: FakeSessionPorts, phase: str = "keyword") -> None:
        self.catalog = catalog
        self.ports = ports
        self.phase = phase
        self.keyword: str | None = None
        self.options: list[dict[str, Any]] = []
        self.support: dict[str, str | None] = {p: None for p in ("candidate", *SEATS)}
        self.stated: dict[str, str | None] = {}  # 자리별 첫 지지 입장 기록 시각
        # 지원자의 이 단계 최신 발언이 지지로 세는 발언이면 그 안(api candidate_confirmed)
        self.confirmed: str | None = None
        self.intents: dict[str, dict[str, Any]] = {}  # message_intents(저장된 분류)
        self.opposers: dict[str, set[str]] = {}
        self.slots: dict[str, dict[str, Any]] = {}
        self.cards: dict[str, dict[str, Any]] = {}
        self.invites: set[str] = set()
        self.messages: list[dict[str, Any]] = []
        self.observations: dict[str, dict[str, Any]] = {}
        self.cost_left = 2.0
        self.candidate_name = "김지원"
        self.remaining = 600
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_tools: set[str] = set()
        self.materials = {
            "water_flood_cases": ("도심 침수 사례", "water", "하수관 수위 센서로 30분 전 경보."),
            "water_stats": ("지역별 물 부족 통계", "water", "제한 급수 일수 증가."),
        }

    # ---- 시나리오 준비 ----------------------------------------------------------------------
    def confirm_keyword(self, key: str = "water") -> None:
        self.keyword = key
        label = self.catalog.label_of(key)
        self.slots["keyword"] = {
            "phase": "keyword",
            "status": "confirmed",
            "value": label,
            "option_id": f"opt-{key}",
            "resolution": "consensus",
            "filled_by": "candidate",
        }

    def add_option(self, key: str | None, proposer: str, label: str | None = None) -> str:
        oid = f"opt-{key or uuid.uuid4().hex[:6]}"
        self.options.append(
            {
                "option_id": oid,
                "phase": self.phase,
                "label": label or self.catalog.label_of(key or "") or key,
                "proposer": proposer,
                "scenario_option_key": key,
                "created_at": "t",
            }
        )
        return oid

    def set_support(self, participant: str, option_id: str | None) -> None:
        self.support[participant] = option_id

    def candidate_says(self, content: str) -> str:
        mid = str(uuid.uuid4())
        self.messages.append(
            {
                "id": mid,
                "sender": "candidate",
                "content": content,
                "phase": self.phase,
                "is_invite": False,
                "t_min": 1.0,
            }
        )
        self.ports.generation += 1
        self.confirmed = None  # 새 최신 발언은 분류 반영 전까지 지지로 세지 않는다
        return mid

    def system_says(self, content: str) -> str:
        mid = str(uuid.uuid4())
        self.messages.append(
            {
                "id": mid,
                "sender": "system",
                "content": content,
                "phase": self.phase,
                "is_invite": False,
                "t_min": 1.0,
            }
        )
        return mid

    def apply_candidate(self, result: dict[str, Any]) -> None:
        """api 메일박스의 의도 반영(docs/contracts/internal.md): 안 행은 confidence와 무관, 지지는
        high이고 유보 표현이 없을 때만, 보류는 confidence와 무관하게 그 안 지지 철회."""
        target = result.get("target_option_id")
        key = result.get("scenario_option_key")
        if result["intent"] == "propose" and key:
            found = next((o for o in self.phase_options() if o["scenario_option_key"] == key), None)
            target = found["option_id"] if found else self.add_option(key, "candidate")
        if result["intent"] == "withhold":
            withdrawn = target or (None if key else self.support.get("candidate"))
            if withdrawn and self.support.get("candidate") == withdrawn:
                self.support["candidate"] = None
            return
        if result.get("confidence") != "high" or not target:
            return
        if result["intent"] in ("propose", "agree"):
            if not result.get("reservation"):
                self.support["candidate"] = target
                self.confirmed = target
        elif result["intent"] == "oppose":
            self.opposers.setdefault(target, set()).add("candidate")
            if self.support.get("candidate") == target:
                self.support["candidate"] = None

    def lock(self, card_id: str, option_id: str) -> None:
        """api가 card_due 첫 처리에서 기준안을 잠근 상태."""
        row = self.cards.setdefault(
            card_id, {"card_id": card_id, "locked_option_id": None, "delivered": []}
        )
        row["locked_option_id"] = option_id

    # ---- 판정 -------------------------------------------------------------------------------
    def phase_options(self) -> list[dict[str, Any]]:
        return [o for o in self.options if o["phase"] == self.phase]

    def consensus(self) -> str | None:
        cand = self.support.get("candidate")
        if not cand or cand != self.confirmed:
            return None
        count = sum(1 for p, o in self.support.items() if o == cand)
        return cand if count >= 3 else None

    def debate_state(self) -> dict[str, Any]:
        opts = []
        for o in self.phase_options():
            opts.append(
                {
                    **o,
                    "supporters": [p for p, s in self.support.items() if s == o["option_id"]],
                    "opposers": sorted(self.opposers.get(o["option_id"], set())),
                }
            )
        spoken = {o["scenario_option_key"] for o in opts}
        catalog_options = [
            {**c, "spoken": c["scenario_option_key"] in spoken}
            for c in self.catalog.catalog_options(self.phase, self.keyword)
        ]
        slot = self.slots.get(self.phase) or {"phase": self.phase, "status": "empty", "value": None}
        return {
            "phase": self.phase,
            "generation": self.ports.generation,
            "phase_remaining_seconds": self.remaining,
            "slot": {k: slot.get(k) for k in ("phase", "status", "value")},
            "options": opts,
            "catalog_options": catalog_options,
            "current_support": {
                p: s if s in {o["option_id"] for o in opts} else None
                for p, s in self.support.items()
            },
            "consensus_option_id": self.consensus() if slot["status"] == "empty" else None,
            "timeout_option_id": None,
            "candidate": {"spoke_in_phase": True, "last_message_t_min": 1.0, "typing": False},
            "invite_delivered": self.phase in self.invites,
            "last_room_message_t_min": 1.0,
            "confirmed": [s for s in self.slots.values() if s["status"] == "confirmed"],
            "card_progress": [self.card_row(c) for c in self.cards.values()],
            "cost_left": self.cost_left,
            "candidate_intents": [
                {"message_id": m["id"], "created_at": at, **self.intents[m["id"]]}
                for m, at in self.timed()
                if m["sender"] == "candidate" and m["id"] in self.intents
            ],
            "first_spoke_at": {
                seat: next((at for m, at in self.timed() if m["sender"] == seat), None)
                for seat in SEATS
            },
            "first_stated_at": {seat: self.stated.get(seat) for seat in SEATS},
            # api처럼 그 단계 전체 기록(대화 창 30개와 무관)에서 계산한 열린 지명
            "open_mentions": self.open_mentions(),
            "seats": [{"seat": "candidate", "display_name": self.candidate_name}]
            + [
                {"seat": s, "display_name": self.catalog.name_of(s), "avatar_url": f"/a/{s}.png"}
                for s in SEATS
            ],
        }

    def open_mentions(self) -> list[dict[str, str]]:
        from app.agent.manager import rules

        rows = [
            {**m, "reply_to": (m.get("meta") or {}).get("reply_to")}
            for m in self.messages
            if m.get("phase") == self.phase
        ]
        return rules.open_mentions(rows, self.intents, self.catalog)

    def card_row(self, card: dict[str, Any]) -> dict[str, Any]:
        """api처럼 카드 첫 발언과 그 뒤 AI 자리별 발언 수(전체 기록 기준)를 붙인다."""
        trigger = (self.observations.get(card["card_id"]) or {}).get("trigger_message_id")
        ids = [m["id"] for m in self.messages]
        spoken: dict[str, int] = {}
        if trigger in ids:
            start = self.messages[ids.index(trigger)]
            for m in self.messages[ids.index(trigger) :]:
                if m["sender"] in SEATS and m["phase"] == start["phase"]:
                    spoken[m["sender"]] = spoken.get(m["sender"], 0) + 1
        return {**card, "trigger_message_id": trigger, "spoken_since_trigger": spoken}

    def timed(self) -> list[tuple[dict[str, Any], str]]:
        """이 단계 메시지와 저장 순서대로 매긴 시각(api의 created_at 대역)."""
        return [
            (m, f"2026-10-21T01:{i // 60:02d}:{i % 60:02d}Z")
            for i, m in enumerate(self.messages)
            if m.get("phase") == self.phase
        ]

    def room(self, sender: str | None = None) -> list[dict[str, Any]]:
        return [m for m in self.messages if sender is None or m["sender"] == sender]

    # ---- 도구 -------------------------------------------------------------------------------
    async def _call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, args))
        if name in self.fail_tools:
            return fail("upstream_unavailable", "down", True)
        if name in ASSESSMENT_TOOLS:
            return fail("upstream_unavailable", "assessment-mcp: not configured", True)
        handler = getattr(self, f"t_{name}", None)
        if handler is None:
            return fail("not_found", name)
        try:
            return handler(**args)
        except TypeError as exc:  # 실 MCP처럼 모델이 낸 잘못된 인자는 오류 결과로 돌려준다
            return fail("invalid_input", str(exc))

    async def _all_specs(self) -> dict[str, dict[str, Any]]:
        schema = {"type": "object", "properties": {"query": {"type": "string"}}}
        return {
            n: function_spec(n, n, schema)
            for n in (
                "get_transcript",
                "get_draft",
                "search_materials",
                "get_material",
                "get_debate_state",
                "post_message",
            )
        }

    def t_get_debate_state(self) -> dict[str, Any]:
        return ok(self.debate_state())

    def t_get_transcript(self, phase: str | None = None, limit: int = 30, **_: Any):
        rows = [m for m in self.messages if phase is None or m["phase"] == phase]
        return ok(
            [
                {
                    **{k: m[k] for k in ("id", "sender", "content", "phase", "is_invite", "t_min")},
                    "reply_to": (m.get("meta") or {}).get("reply_to"),
                }
                for m in rows[-limit:]
            ]
        )

    def t_get_draft(self) -> dict[str, Any]:
        return ok({"sections": {}, "revision": 0, "updated_at": None})

    def t_post_message(
        self,
        sender,
        content,
        run_id,
        seq,
        expected_generation=None,
        is_invite=False,
        card_ids=None,
        cited_material_ids=None,
        meta=None,
    ):
        for m in self.messages:
            if m.get("run_id") == run_id and m.get("seq") == seq:
                return ok(m)
        if expected_generation is not None and expected_generation != self.ports.generation:
            return fail("conflict", "stale_generation")
        if is_invite:
            if self.phase in self.invites:
                return fail("conflict", "conflict")
            self.invites.add(self.phase)
        message = {
            "id": str(uuid.uuid4()),
            "sender": sender,
            "content": content,
            "phase": self.phase,
            "is_invite": bool(is_invite),
            "t_min": 2.0,
            "run_id": run_id,
            "seq": seq,
            "card_ids": list(card_ids or []),
            "meta": meta,
        }
        self.messages.append(message)
        for card_id in card_ids or []:
            row = self.cards.setdefault(
                card_id, {"card_id": card_id, "locked_option_id": None, "delivered": []}
            )
            if sender not in row["delivered"]:
                row["delivered"].append(sender)
        return ok(message)

    def t_propose_option(
        self, phase, label, proposer, source_message_id, run_id, scenario_option_key=None
    ):
        if phase != self.phase:
            return fail("conflict", "phase_not_open")
        for o in self.phase_options():
            if scenario_option_key and o["scenario_option_key"] == scenario_option_key:
                return ok({**o, "existing": True})
        oid = self.add_option(scenario_option_key, proposer, label)
        at = next((t for m, t in self.timed() if m["id"] == source_message_id), None)
        self.stated.setdefault(proposer, at)
        self.support[proposer] = oid
        return ok({**self.options[-1], "existing": False, "option_id": oid})

    def t_set_stance(self, participant, option_id, stance, source_message_id, run_id):
        if stance == "support":
            at = next((t for m, t in self.timed() if m["id"] == source_message_id), None)
            self.stated.setdefault(participant, at)
            self.support[participant] = option_id
            self.opposers.get(option_id, set()).discard(participant)
        else:
            self.opposers.setdefault(option_id, set()).add(participant)
            if self.support.get(participant) == option_id:
                self.support[participant] = None
        return ok({"stance_id": f"st-{len(self.calls)}"})

    def t_fill_slot(self, option_id, seat, content, run_id):
        for slot in self.slots.values():
            if slot.get("run_id") == run_id:
                return ok({"slot": slot, "message": slot["message"], "phase": self.phase})
        option = next((o for o in self.options if o["option_id"] == option_id), None)
        if option is None or option["phase"] != self.phase:
            return fail("conflict", "phase_not_open")
        if (self.slots.get(self.phase) or {}).get("status") == "confirmed":
            return fail("conflict", "slot_confirmed")
        if self.consensus() != option_id:
            return fail("conflict", "no_consensus")
        message = {
            "id": str(uuid.uuid4()),
            "sender": seat,
            "content": content,
            "phase": self.phase,
            "is_invite": False,
            "t_min": 3.0,
            "run_id": run_id,
            "seq": 5,
            "meta": {"kind": "consensus_fill"},
        }
        self.messages.append(message)
        slot = {
            "phase": self.phase,
            "status": "confirmed",
            "value": option["label"],
            "option_id": option_id,
            "resolution": "consensus",
            "filled_by": seat,
            "run_id": run_id,
            "message": message,
        }
        self.slots[self.phase] = slot
        self.phase = PHASES[PHASES.index(self.phase) + 1]
        self.ports.generation += 1
        return ok({"slot": slot, "message": message, "phase": self.phase})

    def t_search_materials(self, query: str = "", top_k: int = 3):
        if self.keyword is None:
            return fail("not_released", "materials locked")
        rows = [
            {"material_id": m, "title": t, "chunk": c, "score": 0.9}
            for m, (t, k, c) in self.materials.items()
            if k == self.keyword
        ]
        return ok(rows[:top_k])

    def t_get_material(self, material_id: str):
        if self.keyword is None or material_id not in self.materials:
            return fail("not_released", "materials locked")
        title, _, content = self.materials[material_id]
        return ok({"material_id": material_id, "title": title, "content": content})

    def record_observation(self, card_id, phase, run_id, trigger_message_id=None):
        if card_id not in self.observations:
            self.observations[card_id] = {
                "card_id": card_id,
                "phase": phase,
                "trigger_message_id": trigger_message_id,
            }
        return ok({"observation_id": f"ob-{card_id}", "fired_at": "t", "t_min": 1.0})


class FakeAssessment:
    """assessment-mcp 가짜(토큰 있는 별도 클라이언트 자리)."""

    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, args))
        if name != "record_observation":
            return fail("forbidden", name)
        return self.server.record_observation(**args)

    async def _all_specs(self) -> dict[str, dict[str, Any]]:
        return {}


class Harness:
    """가짜 서버 + 포트 + FakeLLM 3개로 DebateGraphRunner를 돌린다."""

    def __init__(self, catalog: Catalog, phase: str = "keyword", persona=(), turn=(), intent=()):
        self.session_id = uuid.uuid4()
        self.ports = FakeSessionPorts(self.session_id, generation=1)
        self.server = FakeServer(catalog, self.ports, phase)
        self.assessment = FakeAssessment(self.server)
        self.persona = FakeLLM(
            persona,
            default={"content": "네, 그 얘기 더 해 봐요.", "proposes": None, "stance": None},
        )
        self.turn = FakeLLM(turn, default='{"speakers": [], "reason": "없음"}')
        self.intent = FakeLLM([model_reply(i) for i in intent], default="not json")
        self.slept: list[float] = []
        self.snapshots: list[dict[str, Any]] = []
        self.tools_seen: list[RunTools] = []

        async def sleep(seconds: float) -> None:
            self.slept.append(seconds)

        @asynccontextmanager
        async def tools(_sid):
            run_tools = RunTools(self.server, self.assessment)
            self.tools_seen.append(run_tools)
            yield run_tools

        async def scenario(_sid):
            return {
                "mission_id": "debate",
                "seats": catalog.seats,
                "prompts": catalog.prompts,
                "catalog": {
                    "mission": catalog.mission,
                    "personas": catalog.personas,
                    "cards": catalog.cards,
                    "keywords": catalog.keywords,
                },
            }

        self.runner = DebateGraphRunner(
            llms=DebateLLMs(persona=self.persona, turn=self.turn, intent=self.intent),
            tools=tools,
            scenario=scenario,
            ports_for=lambda _sid: self.ports,
            sleep=sleep,
        )
        self.runner.snapshots = self.snapshots

    async def run(
        self,
        kind: str,
        *,
        message_id: str | None = None,
        card_id: str | None = None,
        generation: int | None = None,
        run_id: uuid.UUID | None = None,
    ):
        stimulus = Stimulus(
            type=kind,
            phase=self.server.phase,
            generation=self.ports.generation if generation is None else generation,
            message_id=uuid.UUID(message_id) if message_id else None,
            card_id=card_id,
        )
        return await self.runner.run(self.session_id, stimulus, run_id or uuid.uuid4())

    def candidate(self, content: str, result: dict[str, Any] | None = None) -> str:
        """지원자 메시지 저장(generation +1) + 메일박스의 의도 분류 결과 반영."""
        mid = self.server.candidate_says(content)
        if result is not None:
            intent_mod.remember(mid, result)
            self.server.intents[mid] = dict(result)
            self.server.apply_candidate(result)
        return mid

    def posted(self, sender: str | None = None) -> list[dict[str, Any]]:
        return [m for m in self.server.room(sender) if m["sender"] in SEATS and m.get("seq") != 5]

    def tool_names(self) -> list[str]:
        return [n for n, _ in self.server.calls]


def intent_of(
    kind: str,
    *,
    option_id: str | None = None,
    key: str | None = None,
    to: tuple[str, ...] = (),
    confidence: str = "high",
    reservation: bool = False,
    excluded: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "intent": kind,
        "target_option_id": option_id,
        "scenario_option_key": key,
        "new_option_label": None,
        "confidence": confidence,
        "reservation": reservation,
        "addressed_to": list(to),
        "excluded": list(excluded),
    }


def model_reply(result: Any) -> Any:
    """계약 출력 형태(target_option_id·scenario_option_key)를 의도 분류 모델 응답 형태(target 한
    칸)로 바꾼다. dict가 아니거나 이미 응답 형태면 그대로."""
    if not isinstance(result, dict) or "target" in result or "target_option_id" not in result:
        return result
    out = {k: v for k, v in result.items() if k not in ("target_option_id", "scenario_option_key")}
    out["target"] = result.get("target_option_id") or result.get("scenario_option_key")
    out["wanted"] = None
    return out


def say(content: str, stance: dict | None = None, proposes: dict | None = None) -> dict:
    return {"content": content, "proposes": proposes, "stance": stance}


@pytest.fixture
def harness(catalog):
    def make(phase: str = "keyword", **scripts) -> Harness:
        return Harness(catalog, phase, **scripts)

    return make
