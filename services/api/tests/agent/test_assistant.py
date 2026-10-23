"""어시스턴트 tool-calling 루프(최대 3회, 인용, 잠금 안내, guard)."""

import uuid
from contextlib import asynccontextmanager

import pytest
from app.agent.assistant import AssistantGraphRunner
from app.agent.llm import FakeLLM, make_llm, tool_call
from app.agent.mcp_client import ASSISTANT_TOOLS, ToolScope
from app.ports import FakeSessionPorts
from conftest import FakeServer


def make(catalog, script, keyword: str | None = "water"):
    server = FakeServer(catalog, FakeSessionPorts(uuid.uuid4()))
    server.keyword = keyword
    llm = FakeLLM(script)

    @asynccontextmanager
    async def rag(_sid):
        yield ToolScope(server, ASSISTANT_TOOLS)

    async def scenario(_sid):
        return {
            "mission_id": "debate",
            "prompts": catalog.prompts,
            "catalog": {
                "mission": catalog.mission,
                "personas": catalog.personas,
                "cards": catalog.cards,
                "keywords": catalog.keywords,
            },
        }

    return AssistantGraphRunner(llm=llm, rag=rag, scenario=scenario), llm, server


async def test_model_picks_tools_and_cites(catalog):
    runner, llm, server = make(
        catalog,
        [
            tool_call("search_materials", query="침수 사례"),
            tool_call("get_material", material_id="water_flood_cases"),
            "도심 침수 사례를 보면 하수관 수위 센서로 30분 전에 경보했어요.",
        ],
    )
    result = await runner.answer(uuid.uuid4(), "침수 대응 사례 있어?", uuid.uuid4())
    assert [c["name"] for c in result.tool_calls] == ["search_materials", "get_material"]
    assert result.cited_material_ids == ["water_flood_cases"]
    assert "도심 침수 사례" in result.content
    assert (
        llm.calls[0].tools and llm.calls[0].temperature == 0.7
    )  # FakeLLM 기본, 역할 값은 make_llm이 넣는다
    assert result.usage["tool_calls"] == 2


async def test_tool_calls_capped_at_three(catalog):
    runner, llm, _ = make(
        catalog,
        [tool_call("search_materials", query="q")] * 3 + ["지역별 물 부족 통계를 참고하세요."],
    )
    result = await runner.answer(uuid.uuid4(), "자료?", uuid.uuid4())
    assert len(result.tool_calls) == 3
    assert llm.calls[-1].tools is None  # 3회 뒤에는 도구를 바인딩하지 않는다
    assert result.cited_material_ids and result.content


async def test_locked_materials_answer_fixed_sentence(catalog):
    runner, _, _ = make(
        catalog, [tool_call("search_materials", query="침수"), "모르겠어요."], keyword=None
    )
    result = await runner.answer(uuid.uuid4(), "침수 자료 있어?", uuid.uuid4())
    assert (
        result.content == "키워드가 정해지면 자료를 찾을 수 있어요."
        and result.cited_material_ids == []
    )


async def test_guard_replaces_with_notice(catalog):
    runner, _, _ = make(catalog, ["이렇게 하시면 합격이에요.", "점수는 높을 거예요."])
    result = await runner.answer(uuid.uuid4(), "저 붙을까요?", uuid.uuid4())
    assert result.content == catalog.notices["evaluation"]


async def test_llm_fake_mode_assistant(catalog):
    runner, _, _ = make(catalog, [])
    runner.llm = make_llm("assistant", {"LLM_FAKE": "1"})
    result = await runner.answer(uuid.uuid4(), "침수 사례", uuid.uuid4())
    assert [c["name"] for c in result.tool_calls] == ["search_materials"]
    assert result.cited_material_ids == ["water_flood_cases"]
    assert result.content.startswith("도심 침수 사례")


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (
            "하수관 센서로 30분 전에 경보했어요.\nmaterial_id: water_flood_cases",
            "하수관 센서로 30분 전에 경보했어요.\n출처: 도심 침수 사례",
        ),
        (
            "도심 침수 사례(material_id: water_flood_cases)를 보면 30분 전에 경보했어요.",
            "도심 침수 사례를 보면 30분 전에 경보했어요.",
        ),
        (
            '도심 침수 사례를 보면 경보가 빨랐어요. cited_material_ids: ["water_flood_cases"]',
            "도심 침수 사례를 보면 경보가 빨랐어요.",
        ),
        (
            "water_flood_cases 자료를 보면 30분 전에 경보했어요.",
            "도심 침수 사례 자료를 보면 30분 전에 경보했어요.",
        ),
    ],
)
async def test_internal_material_ids_are_not_shown(catalog, answer, expected):
    runner, _, _ = make(catalog, [tool_call("search_materials", query="침수"), answer])
    result = await runner.answer(uuid.uuid4(), "침수 사례 있어?", uuid.uuid4())
    assert result.content == expected
    assert result.cited_material_ids == ["water_flood_cases"]


TITLES = {"water_flood_cases": "도심 침수 사례", "water_stats": "지역별 물 부족 통계"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 표기 뒤 숫자·문장은 그대로
        ("material_id: water_flood_cases, 30분 전에 경보했어요.", "30분 전에 경보했어요."),
        (
            "센서가 30분 전에 울렸어요 (material_id: `water_flood_cases`).",
            "센서가 30분 전에 울렸어요.",
        ),
        ('근거는 {"material_id": "water_stats"} 이에요.', "근거는 이에요."),
        (
            '요약했어요.\n"cited_material_ids": ["water_flood_cases", "water_stats"]\n다음 줄',
            "요약했어요.\n다음 줄",
        ),
        # URL·인라인 코드·코드 블록 안, 들여쓰기·빈 줄은 그대로
        (
            "링크 https://x.org/water_flood_cases 참고\n\n  들여쓴 줄  두 칸\n`water_stats`",
            "링크 https://x.org/water_flood_cases 참고\n\n  들여쓴 줄  두 칸\n`water_stats`",
        ),
        (
            "```\nmaterial_id: water_stats\n    x = 1\n```",
            "```\nmaterial_id: water_stats\n    x = 1\n```",
        ),
        # 모르는 id(이번 응답에서 받지 않은 자료)와 정상 문장·제목은 그대로
        (
            "material_id: fire_cases 는 그대로, 도심 침수 사례 30%",
            "material_id: fire_cases 는 그대로, 도심 침수 사례 30%",
        ),
        ("water_stats 기준 3.5배", "지역별 물 부족 통계 기준 3.5배"),
        # URL 바로 뒤 표기는 지워도 링크 뒤 공백 하나는 남긴다
        ("https://example.org (material_id: water_stats) 참고", "https://example.org 참고"),
        # 인라인 코드 전체가 표기면 백틱 쌍째 지운다
        ("자료는 `material_id: water_stats` 하나예요.", "자료는 하나예요."),
        # ~~~ 펜스와 4칸 들여쓴 코드 블록 안은 그대로
        ("~~~\nmaterial_id: water_stats\n~~~", "~~~\nmaterial_id: water_stats\n~~~"),
        (
            "예시:\n\n    material_id: water_stats\n    water_flood_cases",
            "예시:\n\n    material_id: water_stats\n    water_flood_cases",
        ),
    ],
)
def test_hide_ids_touches_only_known_id_notation(text, expected):
    from app.agent.assistant.runner import hide_ids

    assert hide_ids(text, TITLES) == expected


async def test_ids_hidden_with_open_and_locked_lookups(catalog):
    """열린 자료 조회 성공 + 미공개 자료 조회 거부가 섞여도 받은 자료 id만 지운다."""
    runner, _, _ = make(
        catalog,
        [
            tool_call("get_material", material_id="water_flood_cases"),
            tool_call("get_material", material_id="fire_cases"),
            "침수 경보가 빨랐어요 (material_id: water_flood_cases). fire_cases는 못 봤어요.",
        ],
    )
    result = await runner.answer(uuid.uuid4(), "사례 있어?", uuid.uuid4())
    assert result.content == "침수 경보가 빨랐어요. fire_cases는 못 봤어요.\n출처: 도심 침수 사례"
    assert result.cited_material_ids == ["water_flood_cases"]


# ---- 근거(결정 D1): 쓴 자료만 인용, 숫자 검사, 문장 단위 자르기, 도구 없으면 출처 없음 ----------

FLOOD = (
    "- 하수관 수위 센서를 시범 설치한 두 동네는 경보를 평균 25분 앞당겼다.\n"
    "- 반지하 가구 물막이판 설치 지원은 신청 가구의 38%만 받았다."
)
STATS = "- 최근 5년 동안 제한 급수 일수가 세 배 넘게 늘었다. 2025년 제한 급수 일수는 21일이다."


def grounded(catalog, script):
    runner, llm, server = make(catalog, script)
    server.materials = {
        "water_flood_cases": ("도심 침수 사례", "water", FLOOD),
        "water_stats": ("지역별 물 부족 통계", "water", STATS),
    }
    return runner, llm, server


async def test_cites_only_materials_actually_used(catalog):
    """받은 자료 2개 중 숫자를 쓴 자료만 인용하고 출처 줄도 그 자료만."""
    runner, _, _ = grounded(
        catalog,
        [
            tool_call("search_materials", query="경보"),
            "센서를 단 동네는 경보를 평균 25분 앞당겼어요.",
        ],
    )
    result = await runner.answer(uuid.uuid4(), "경보를 얼마나 앞당겼어요?", uuid.uuid4())
    assert result.cited_material_ids == ["water_flood_cases"]
    assert result.content == "센서를 단 동네는 경보를 평균 25분 앞당겼어요.\n출처: 도심 침수 사례"


async def test_verbatim_span_counts_as_used(catalog):
    runner, _, _ = grounded(
        catalog,
        [
            tool_call("search_materials", query="제한 급수"),
            "제한 급수 일수가 세 배 넘게 늘었다고 해요.",
        ],
    )
    result = await runner.answer(uuid.uuid4(), "제한 급수는요?", uuid.uuid4())
    assert result.cited_material_ids == ["water_stats"]
    assert result.content.endswith("\n출처: 지역별 물 부족 통계")


async def test_unrelated_answer_cites_nothing_even_with_tool_results(catalog):
    """자료 밖 질문: 검색은 했지만 답이 자료와 겹치지 않으면 인용 0, 출처 줄 없음."""
    runner, _, _ = grounded(
        catalog, [tool_call("search_materials", query="연봉"), "신입 연봉은 열린 자료에 없어요."]
    )
    result = await runner.answer(uuid.uuid4(), "신입 연봉이 얼마예요?", uuid.uuid4())
    assert result.cited_material_ids == [] and "출처" not in result.content
    assert result.content == "신입 연봉은 열린 자료에 없어요."


async def test_no_tool_results_means_no_source_line(catalog):
    runner, _, _ = grounded(catalog, ["문장을 이렇게 다듬어 보세요: 침수 대응이 먼저예요."])
    result = await runner.answer(uuid.uuid4(), "이 문장 다듬어 줘", uuid.uuid4())
    assert result.tool_calls == [] and result.cited_material_ids == []
    assert "출처" not in result.content


async def test_ungrounded_number_regenerated_once(catalog):
    """자료에 없는 숫자(5분)는 재생성 1회로 고치고, 재생성 입력에 없는 수치를 알린다."""
    runner, llm, _ = grounded(
        catalog,
        [
            tool_call("search_materials", query="경보"),
            "센서를 단 동네는 경보를 평균 5분 앞당겼어요.",
            "센서를 단 동네는 경보를 평균 25분 앞당겼어요.",
        ],
    )
    result = await runner.answer(uuid.uuid4(), "몇 분 앞당겼어요?", uuid.uuid4())
    assert "25분" in result.content and "평균 5분" not in result.content
    note = llm.calls[-1].messages[-1].content
    assert note.startswith("[다시 쓰기]") and "5분" in note
    assert result.cited_material_ids == ["water_flood_cases"]


async def test_ungrounded_number_dropped_after_second_failure(catalog):
    """재생성해도 남은 수치는 그 문장만 빼고 게시한다."""
    runner, _, _ = grounded(
        catalog,
        [
            tool_call("search_materials", query="제한 급수"),
            "2018년에는 3일이었어요. 2025년에는 21일이에요.",
            "2019년은 4일이에요. 2025년은 21일이에요.",
        ],
    )
    result = await runner.answer(uuid.uuid4(), "연도별 제한 급수 일수?", uuid.uuid4())
    assert result.content == "2025년은 21일이에요.\n출처: 지역별 물 부족 통계"
    assert result.cited_material_ids == ["water_stats"]


async def test_all_sentences_dropped_falls_back_to_missing_text(catalog):
    runner, _, _ = grounded(
        catalog,
        [
            tool_call("search_materials", query="하수관"),
            "하수관은 총 1,200km예요.",
            "약 1200km예요.",
        ],
    )
    result = await runner.answer(uuid.uuid4(), "하수관 길이?", uuid.uuid4())
    assert result.content == catalog.fixed["assistant_missing"]
    assert result.cited_material_ids == []


async def test_numbers_from_the_question_are_allowed(catalog):
    """기획안 문장 다듬기: 지원자가 쓴 숫자는 자료에 없어도 그대로 쓸 수 있다."""
    runner, llm, _ = grounded(catalog, ["3개 동네에 센서를 먼저 설치하자는 제안입니다."])
    result = await runner.answer(uuid.uuid4(), "'3개 동네 센서 먼저' 문장 다듬어 줘", uuid.uuid4())
    assert result.content == "3개 동네에 센서를 먼저 설치하자는 제안입니다."
    assert len(llm.calls) == 1


async def test_long_answer_is_cut_at_sentence_end(catalog):
    sentence = "빗물 저금통은 설치가 빠르지만 가구가 직접 관리해야 해요. "
    runner, _, _ = grounded(
        catalog, [tool_call("get_material", material_id="water_flood_cases"), sentence * 12]
    )
    result = await runner.answer(uuid.uuid4(), "길게 설명해 줘", uuid.uuid4())
    assert len(result.content) <= 400 and result.content.endswith("해요.")
    assert result.cited_material_ids == []  # 자료와 겹치는 숫자·구간이 없으면 인용하지 않는다


def test_trim_sentences_prefers_sentence_then_space_then_hard_cut():
    from app.agent.assistant.runner import trim_sentences

    assert trim_sentences("짧다.", 10) == "짧다."
    assert (
        trim_sentences("첫 문장이에요. 둘째 문장이에요. 셋째", 20)
        == "첫 문장이에요. 둘째 문장이에요."
    )
    assert trim_sentences("- 첫 줄\n- 둘째 줄\n- 셋째 줄", 14) == "- 첫 줄\n- 둘째 줄"
    assert trim_sentences("마침표 없이 공백만 있는 긴 문장", 10) == "마침표 없이 공백만"
    assert trim_sentences("공백도마침표도없는긴글자열", 5) == "공백도마침"


def test_ungrounded_numbers_rules():
    from app.agent.assistant.runner import drop_sentences, ungrounded_numbers

    sources = [FLOOD, STATS, "| 2021 | 48 | 120 | 9 |"]
    assert ungrounded_numbers("평균 25분, 38%, 2025년 21일, 120가구, 1. 센서", sources) == []
    assert ungrounded_numbers("평균 5분 앞당겼고 2018년 3일", sources) == ["5분", "2018년", "3일"]
    assert ungrounded_numbers("48mm", sources) == []  # 표 칸 숫자는 단위를 붙여 써도 된다
    assert (
        drop_sentences("25분이에요. 5분 아니에요.\n2018년 값: 3일", ["5분", "2018년"])
        == "25분이에요."
    )
