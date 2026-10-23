"""debate-mcp 도구 7개(조회 3, 변경 4). 모두 api 내부 REST로 전달만 하고 판단은 api가 한다.

반환은 `{ok, data}` 또는 `{ok:false, error:{code, message, retryable}}`. 목록·입출력 정본은
docs/contracts/mcp.md "debate-mcp". 멱등 키((run_id, seq), scenario_option_key·source_message_id,
run_id)와 expected_generation은 가공 없이 그대로 보낸다. 지원자의 안·입장은 api가 의도 분류로 직접
저장하므로 proposer·participant·seat에 candidate는 없다.
"""

from typing import Annotated, Any, Literal

from pydantic import Field

from debate_mcp import client
from debate_mcp.server import mcp

SessionId = Annotated[str, Field(description="세션 ID. 그래프가 주입하며 모델이 바꾸지 않는다.")]
RunId = Annotated[
    str,
    Field(min_length=1, description="이 실행의 run ID. 같은 키로 다시 호출해도 중복되지 않는다."),
]
Seat = Literal["p1", "p2", "p3"]
SlotPhase = Literal["keyword", "problem", "solution"]
Phase = Literal["keyword", "problem", "solution", "proposal"]

# get_transcript가 돌려주는 메시지 필드(meta·intent·origin·card_ids 없음). reply_to는 AI 발언이 답한
# 메시지 id(meta.reply_to, 없으면 null)
TRANSCRIPT_FIELDS = ("id", "sender", "content", "phase", "is_invite", "t_min")
DRAFT_FIELDS = ("sections", "revision", "updated_at")


def _base(session_id: str) -> str:
    return f"/internal/sessions/{session_id}"


def _drop_none(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if v is not None}


def _pick(item: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {k: item[k] for k in fields if k in item}


# ───────────── 조회 ─────────────


@mcp.tool
async def get_debate_state(session_id: SessionId) -> dict[str, Any]:
    """현재 단계의 토론 상태(DebateState)와 참여자 자리 목록을 조회한다.

    단계·남은 시간·칸·안별 지지/반대·현재 지지·합의 판정(consensus_option_id)·시간 초과 시 고를 안·
    지원자 발언 여부·침묵 초대 여부·확정된 칸·카드 진행을 돌려주고, seats에는 자리별 표시 이름과
    아바타만 담는다(페르소나 유형은 빼고 전달). 결정적 노드(load) 전용이다.
    """
    state = await client.call("GET", f"{_base(session_id)}/debate_state")
    if not state["ok"]:
        return state
    session = await client.call("GET", _base(session_id))
    if not session["ok"]:
        return session
    seats = [
        {k: v for k, v in seat.items() if k != "persona_type"}
        for seat in session["data"].get("seats", [])
    ]
    return client.ok({**state["data"], "seats": seats})


@mcp.tool
async def get_transcript(
    session_id: SessionId,
    since_t_min: Annotated[
        float | None, Field(description="이 세션 시각(분) 이후 메시지만. 생략하면 처음부터")
    ] = None,
    phase: Annotated[Phase | None, Field(description="이 단계의 메시지만. 생략하면 전체")] = None,
    limit: Annotated[int, Field(ge=1, le=200, description="최대 개수(기본 30)")] = 30,
) -> dict[str, Any]:
    """토론방 최근 메시지(기본 30개)를 시간순으로 조회한다.

    각 메시지는 id·보낸 자리(sender)·본문·단계·침묵 초대 여부·세션 시각(t_min)과 그 발언이 답한
    메시지 id(reply_to, 없으면 null)만 담는다(의도 분류·메타·발언 경위는 없다). 시각과 단계로 거를
    수 있다.
    """
    # tail=N: api가 조건에 맞는 최근 N개를 오래된 순으로 준다
    params = {"since_t_min": since_t_min, "phase": phase, "tail": limit}
    result = await client.call("GET", f"{_base(session_id)}/messages", params=params)
    if not result["ok"]:
        return result
    return client.ok(
        [
            {**_pick(m, TRANSCRIPT_FIELDS), "reply_to": (m.get("meta") or {}).get("reply_to")}
            for m in result["data"]
        ]
    )


@mcp.tool
async def get_draft(session_id: SessionId) -> dict[str, Any]:
    """기획안 초안(sections 6칸·revision·updated_at)을 조회한다. 기획안 단계에서만 쓴다."""
    result = await client.call("GET", f"{_base(session_id)}/proposal")
    if not result["ok"]:
        return result
    return client.ok(_pick(result["data"], DRAFT_FIELDS))


# ───────────── 변경 ─────────────


@mcp.tool
async def post_message(
    session_id: SessionId,
    sender: Annotated[
        Literal["p1", "p2", "p3", "system"],
        Field(description="보내는 자리: p1·p2·p3 또는 system"),
    ],
    content: Annotated[str, Field(min_length=1, description="메시지 본문")],
    run_id: RunId,
    seq: Annotated[
        int,
        Field(ge=0, description="노드별 고정 슬롯. (run_id, seq)가 같으면 기존 메시지를 돌려준다"),
    ],
    expected_generation: Annotated[
        int | None,
        Field(description="실행 시작 때 generation. 다르면 conflict/stale_generation(게시 안 함)"),
    ] = None,
    is_invite: Annotated[
        bool | None, Field(description="침묵 초대 메시지면 true. 단계당 두 번째 초대는 conflict")
    ] = None,
    card_ids: Annotated[
        list[str] | None, Field(description="전달하는 카드 ID 목록(공동 전달이면 여러 개)")
    ] = None,
    cited_material_ids: Annotated[
        list[str] | None, Field(description="발언이 인용한 자료 ID 목록")
    ] = None,
    meta: Annotated[
        dict[str, Any] | None, Field(description="모델·지연·비용·stance_check 등 메타. 그대로 저장")
    ] = None,
) -> dict[str, Any]:
    """AI 동료(p1·p2·p3) 또는 시스템 메시지를 토론방에 게시한다.

    같은 run_id와 seq로 다시 부르면 새로 만들지 않고 기존 메시지를 돌려준다. card_ids가 있으면 api가
    카드마다 이 화자를 전달 자리에 더하고, is_invite면 그 단계의 침묵 초대를 기록한다.
    """
    body = {"sender": sender, "content": content, "run_id": run_id, "seq": seq}
    body.update(
        _drop_none(
            {
                "expected_generation": expected_generation,
                "is_invite": is_invite,
                "card_ids": card_ids,
                "cited_material_ids": cited_material_ids,
                "meta": meta,
            }
        )
    )
    return await client.call("POST", f"{_base(session_id)}/messages", json=body)


@mcp.tool
async def propose_option(
    session_id: SessionId,
    phase: Annotated[SlotPhase, Field(description="안을 낼 토론 단계(현재 단계여야 함)")],
    label: Annotated[str, Field(min_length=1, description="안의 표시 문구")],
    proposer: Annotated[Seat, Field(description="제안한 AI 동료 자리")],
    source_message_id: Annotated[
        str, Field(min_length=1, description="이 안을 말한 자기 메시지 ID(자유 안의 멱등 키)")
    ],
    run_id: RunId,
    scenario_option_key: Annotated[
        str | None, Field(description="시나리오 카탈로그 안이면 그 키(시나리오 안의 멱등 키)")
    ] = None,
) -> dict[str, Any]:
    """AI 동료가 말한 안을 토론 안 목록에 등록한다.

    같은 카탈로그 안이 이미 있으면(지원자가 먼저 말한 경우 포함) 새로 만들지 않고 기존 행을
    existing=true로 돌려준다(최초 제안자 보존). 현재 단계가 아니면 conflict/phase_not_open.
    """
    body = {
        "phase": phase,
        "label": label,
        "proposer": proposer,
        "source_message_id": source_message_id,
        "run_id": run_id,
    }
    body.update(_drop_none({"scenario_option_key": scenario_option_key}))
    return await client.call("POST", f"{_base(session_id)}/options", json=body)


@mcp.tool
async def set_stance(
    session_id: SessionId,
    participant: Annotated[Seat, Field(description="입장을 밝힌 AI 동료 자리")],
    option_id: Annotated[str, Field(min_length=1, description="대상 안 ID")],
    stance: Annotated[Literal["support", "oppose"], Field(description="지지 또는 반대")],
    source_message_id: Annotated[
        str, Field(min_length=1, description="이 입장을 말한 자기 메시지 ID")
    ],
    run_id: RunId,
) -> dict[str, Any]:
    """AI 동료의 안에 대한 지지·반대 입장을 기록한다. 같은 메시지·자리·안 재호출은 멱등이다."""
    body = {
        "participant": participant,
        "option_id": option_id,
        "stance": stance,
        "source_message_id": source_message_id,
        "run_id": run_id,
    }
    return await client.call("POST", f"{_base(session_id)}/stances", json=body)


@mcp.tool
async def fill_slot(
    session_id: SessionId,
    option_id: Annotated[
        str, Field(min_length=1, description="합의된 안 ID(대상 단계는 이 안의 단계)")
    ],
    seat: Annotated[Seat, Field(description="합의를 공지하고 칸을 채우는 AI 동료 자리")],
    content: Annotated[str, Field(min_length=1, description="합의 공지 문장(고정 문구)")],
    run_id: RunId,
) -> dict[str, Any]:
    """합의된 안으로 현재 단계의 칸을 채우고 공지 메시지를 남긴다(합의 채우기 전용).

    칸 확정·공지 메시지 저장·다음 단계 전환을 api가 한 트랜잭션으로 하고 {slot, message, phase}를
    돌려준다. 같은 run_id 재호출은 기존 결과. 실패는 conflict이고 message는 phase_not_open·
    slot_confirmed·no_consensus 중 하나이며 이때 칸도 공지도 저장되지 않는다.
    """
    body = {"option_id": option_id, "seat": seat, "content": content, "run_id": run_id}
    return await client.call("POST", f"{_base(session_id)}/slots/fill", json=body)
