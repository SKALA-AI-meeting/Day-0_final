"""미션 카탈로그 버전 해시와 프롬프트 키. api(채용 건 고정)와 rag-mcp(색인 키)가 같은 함수를 쓴다.

표준 라이브러리만 쓴다. 두 서비스가 `scenarios/`를 읽기 전용으로 마운트하고 파일 경로로 import한다.
"""

import hashlib
from pathlib import Path

# 미션 폴더 바로 아래에서 해시에 넣지 않는 폴더(카탈로그 검사 코드)
EXCLUDED_TOP = frozenset({"tests"})

# `prompts/` 아래 역할 폴더 → 프롬프트 키 앞부분. 키는 `positions.scenario.prompts`와 실행 코드가
# 쓰는 이름이라 폴더를 옮겨도 바뀌지 않는다(예: `manager/turn_manager.md` → `turn_manager.md`,
# `evaluator/common.md` → `evaluator/common.md`).
# 여기 없는 폴더나 `prompts/` 바로 아래 파일은 오류다.
PROMPT_KEY_PREFIX = {
    "manager": "",
    "persona": "",
    "intent": "",
    "assistant": "",
    "shared": "",
    "evaluator": "evaluator/",
    "fixtures": "fixtures/",
}
# 채용 건 복사본(`positions.scenario.prompts`)에 넣지 않는 폴더(프롬프트 픽스처)
NOT_COPIED = frozenset({"fixtures"})


def mission_dir(mission_id: str, scenarios_dir: str | Path | None = None) -> Path:
    """미션 폴더 경로. scenarios_dir을 주지 않으면 이 파일이 있는 scenarios/ 기준."""
    root = Path(scenarios_dir) if scenarios_dir else Path(__file__).resolve().parent
    return root / "missions" / mission_id


def catalog_version(mission_id: str, scenarios_dir: str | Path | None = None) -> str:
    """`scenarios/missions/<mission_id>/` 폴더 전체 내용의 해시(16자리 hex).

    자료·프롬프트·픽스처 본문만 바뀌어도 버전이 바뀐다.
    파일 경로도 해시에 넣어 이동·이름 변경을 구분한다.
    점으로 시작하는 파일·폴더(.DS_Store 등)와 __pycache__, 미션 바로 아래 `tests/`(카탈로그
    정합성 검사, 조립·실행 코드가 읽지 않음)는 넣지 않는다.
    검사 코드만 고쳐서는 버전이 바뀌지 않는다.
    """
    root = mission_dir(mission_id, scenarios_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"mission not found: {root}")
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if rel.parts[0] in EXCLUDED_TOP or any(
            part.startswith(".") or part == "__pycache__" for part in rel.parts
        ):
            continue
        digest.update(rel.as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def prompt_files(prompts_dir: str | Path, *, copied_only: bool = False) -> dict[str, Path]:
    """프롬프트 키 → 파일 경로. `copied_only`면 채용 건에 복사하지 않는 픽스처를 뺀다.

    키는 역할 폴더를 떼고 `PROMPT_KEY_PREFIX`의 앞부분을 붙인 이름이다. 등록되지 않은 폴더,
    `prompts/` 바로 아래 파일, 두 파일이 같은 키가 되는 경우는 `ValueError`.
    """
    root = Path(prompts_dir)
    files: dict[str, Path] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        role = rel.parts[0]
        if len(rel.parts) < 2 or role not in PROMPT_KEY_PREFIX:
            raise ValueError(f"prompt file outside a role folder: {rel.as_posix()}")
        if copied_only and role in NOT_COPIED:
            continue
        key = PROMPT_KEY_PREFIX[role] + Path(*rel.parts[1:]).as_posix()
        if key in files:
            raise ValueError(f"duplicate prompt key {key}: {files[key]} and {path}")
        files[key] = path
    return files
