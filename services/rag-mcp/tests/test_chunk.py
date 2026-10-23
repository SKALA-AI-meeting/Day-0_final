"""청크 경계(결정 D1): 줄·문장·공백 경계에서만 나누고 숫자·단어 중간에서 끊지 않는다. DB 불필요."""

from rag_mcp import materials
from rag_mcp.config import REPO_ROOT

SCENARIOS = REPO_ROOT / "scenarios"


def test_every_line_of_every_material_stays_whole():
    """실제 자료 16개: 모든 줄(표 행·목록 항목)이 어느 청크 안에 통째로 있고 상한을 넘지 않는다."""
    for material in materials.load_materials(SCENARIOS):
        chunks = materials.chunk(material.content)
        assert chunks and all(len(c) <= materials.CHUNK_CHARS for c in chunks)
        for line in material.content.splitlines():
            if line.strip():
                assert any(line.strip() in c for c in chunks), (material.material_id, line)


def test_number_is_not_split_across_chunks():
    """기준선 오류(청크가 "평균 2" / "5분"으로 끊김)가 다시 나지 않는다."""
    flood = next(
        m for m in materials.load_materials(SCENARIOS) if m.material_id == "water_flood_cases"
    )
    chunks = materials.chunk(flood.content)
    assert any("평균 25분 앞당겼다" in c for c in chunks)
    assert not any(c.endswith("평균 2") for c in chunks)


def test_overlap_repeats_last_unit_of_previous_chunk():
    lines = [f"- {i:02d}번 항목은 길이가 비슷한 한 줄이다." for i in range(12)]
    chunks = materials.chunk("\n".join(lines), size=120, overlap=60)
    assert len(chunks) > 1
    for before, after in zip(chunks, chunks[1:], strict=False):
        assert after.startswith(before.splitlines()[-1])
    # 겹침을 빼면 모든 줄이 순서대로 한 번씩 나온다
    seen = [line for c in chunks for line in c.splitlines()]
    assert [x for i, x in enumerate(seen) if i == 0 or x != seen[i - 1]] == lines


def test_long_line_splits_at_sentence_then_space_boundaries():
    sentence = "이 문장은 마침표로 끝난다. "
    text = sentence * 10  # 한 줄 140자 > 상한
    chunks = materials.chunk(text, size=60, overlap=0)
    assert all(len(c) <= 60 for c in chunks)
    assert all(c.rstrip().endswith("끝난다.") for c in chunks)
    words = " ".join(["단어"] * 40)  # 마침표 없는 긴 줄 → 공백 경계
    pieces = materials.chunk(words, size=20, overlap=0)
    assert all(len(p) <= 20 and not p.startswith(" ") and "단어" in p for p in pieces)
    assert "".join(pieces).replace("\n", "").replace(" ", "") == words.replace(" ", "")
    assert materials.chunk("가" * 30, size=10, overlap=0) == ["가" * 10] * 3  # 마지막 수단


def test_blank_lines_are_separators_only():
    assert materials.chunk("첫 줄\n\n\n둘째 줄\n") == ["첫 줄\n둘째 줄"]


def test_index_version_folds_chunker_version():
    assert materials.index_version("abc123") == f"abc123-c{materials.CHUNKER_VERSION}"
