"""미션 자료 읽기, 구조 경계 청크(상한 400자), 카탈로그 버전.

자료는 `scenarios/missions/<id>/keywords.yaml`의 키워드별 `materials`다.

자료 목록·제목·키워드는 keywords.yaml이 정본이고 본문은 `materials/<file>`이다. 같은 목록을
`scripts/seed.py`가 `materials` 테이블(원문)에 시드하고 rag-mcp는 `material_chunks`(색인)에 쓴다.
"""

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

CHUNK_CHARS = 400
OVERLAP_CHARS = 100
# 청크 규칙 버전. 규칙이 바뀌면 올린다. 색인 키(index_version)에 들어가 옛 규칙의 색인은 검색되지
# 않고(upstream_unavailable "reindex") 다시 색인해야 한다.
CHUNKER_VERSION = 2  # 앞 청크 끝 단위가 이 길이 이하면 다음 청크 머리에 겹친다
SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")
DEFAULT_MISSION = "debate"


@dataclass(frozen=True)
class Material:
    material_id: str
    keyword_key: str
    title: str
    content: str


def load_materials(scenarios_dir: Path, mission_id: str = DEFAULT_MISSION) -> list[Material]:
    root = scenarios_dir / "missions" / mission_id
    catalog = yaml.safe_load((root / "keywords.yaml").read_text(encoding="utf-8"))
    return [
        Material(
            item["material_id"],
            keyword["key"],
            item["title"],
            (root / "materials" / item["file"]).read_text(encoding="utf-8"),
        )
        for keyword in catalog["keywords"]
        for item in keyword.get("materials", [])
    ]


def chunk(content: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """줄(문단·표 행·목록 항목) 단위로 묶어 상한 size 안의 청크를 만든다.

    한 줄이 상한을 넘을 때만 문장 경계로, 문장도 넘으면 공백 경계로 나눈다(숫자·단어 중간에서
    끊지 않는다). 앞 청크의 마지막 단위가 overlap 이하면 다음 청크 머리에 다시 넣어 경계에 걸친
    문맥을 두 청크가 모두 갖게 한다. 빈 줄은 단위 구분으로만 쓰고 청크에 넣지 않는다.
    """
    units = [
        unit
        for line in content.strip().splitlines()
        if line.strip()
        for unit in _split(line.strip(), size)
    ]
    chunks: list[str] = []
    current: list[str] = []
    for unit in units:
        if current and len("\n".join([*current, unit])) > size:
            chunks.append("\n".join(current))
            tail = current[-1]
            current = [tail] if len(tail) <= overlap and len(tail) + 1 + len(unit) <= size else []
        current.append(unit)
    if current:
        chunks.append("\n".join(current))
    return chunks


def _split(line: str, size: int) -> list[str]:
    """상한을 넘는 줄만 문장 → 공백 경계 순서로 나눈다."""
    if len(line) <= size:
        return [line]
    out: list[str] = []
    for sentence in SENTENCE_END.split(line):
        while len(sentence) > size:
            cut = sentence.rfind(" ", 0, size + 1)
            if cut <= 0:
                cut = size  # 공백이 없는 긴 토큰은 마지막 수단으로 상한에서 자른다
            out.append(sentence[:cut].rstrip())
            sentence = sentence[cut:].lstrip()
        if sentence:
            out.append(sentence)
    return out


def index_version(catalog_version: str) -> str:
    """`material_chunks.catalog_version`에 저장하는 색인 키: 카탈로그 해시 + 청크 규칙 버전."""
    return f"{catalog_version}-c{CHUNKER_VERSION}"


def catalog_version(scenarios_dir: Path, mission_id: str = DEFAULT_MISSION) -> str:
    """`scenarios/version.py`의 함수를 파일 경로로 import한다(api·seed와 같은 함수)."""
    spec = importlib.util.spec_from_file_location("scenarios_version", scenarios_dir / "version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.catalog_version(mission_id, scenarios_dir)
