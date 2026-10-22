"""서비스 README의 생성 블록(API·MCP 도구·테스트)을 채우고 features.md의 테스트 참조를 검사한다.

실행(저장소 루트): uv run --project services/api python scripts/docs_gen.py [--check] [--features]
- 인자 없음: README의 `<!-- gen:NAME -->…<!-- /gen:NAME -->` 사이를 다시 쓴다.
- --check: 생성 결과가 커밋된 README와 다르면 차이를 출력하고 실패한다(파일은 쓰지 않는다).
- --features: docs/features.md의 테스트 참조가 실제로 있는지 본다(pytest는 수집, 나머지는 grep).

API 목록은 routers/*.py, MCP 도구는 각 서비스 tools.py를 정적으로 파싱해(import·DB·네트워크 불필요)
만들고, pytest 테스트는 서비스별 `pytest --collect-only`, web 테스트는 정적 파싱으로 모은다.
"""

import argparse
import ast
import difflib
import os
import re
import subprocess
import sys
from collections import defaultdict
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_SERVICES = ("api", "debate-mcp", "rag-mcp", "assessment-mcp", "report-worker")
MCP_SERVICES = {
    "debate-mcp": "debate_mcp",
    "rag-mcp": "rag_mcp",
    "assessment-mcp": "assessment_mcp",
}
READMES = [f"services/{s}/README.md" for s in PY_SERVICES] + ["apps/web/README.md"]
NOTE = "> 생성 블록이다. 코드를 바꾸면 `make docs-gen`으로 갱신한다."
# README마다 반드시 있어야 하는 생성 블록. 마커가 빠지거나 짝이 틀리면 그 블록이
# 검사에서 조용히 빠지므로 생성·검사 전에 이 목록과 대조한다.
REQUIRED_BLOCKS = {
    "services/api/README.md": ("api", "tests"),
    "services/debate-mcp/README.md": ("mcp-tools", "tests"),
    "services/rag-mcp/README.md": ("mcp-tools", "tests"),
    "services/assessment-mcp/README.md": ("mcp-tools", "tests"),
    "services/report-worker/README.md": ("tests",),
    "apps/web/README.md": ("tests",),
}

# 라우터 함수에 docstring이 없어 요약은 여기서 관리한다. 빠진 경로가 있으면 생성이 실패한다.
SUMMARIES = {
    "GET /sessions/{session_id}/voice": "음성 턴·재생·전사 상태 복구",
    "POST /sessions/{session_id}/voice/capture": "AI 완료 장벽 뒤 사용자 발언권 발급",
    "POST /sessions/{session_id}/voice/abort": "장치·파일 오류 발언권 복구",
    "POST /sessions/{session_id}/voice/playback": "실제 재생 결과 확인·재시도",
    "POST /sessions/{session_id}/voice/audio": "최종 PCM WAV 접수·STT 작업 예약",
    "POST /sessions/{session_id}/voice/retry": "동일 완성 파일 STT 제한 재시도",
    "GET /sessions/{session_id}/voice/assets/{job_id}": "세션 전용 TTS WAV 조회",
    "POST /internal/test/voice-transcript": "FakeSpeech 전사 대본 설정(테스트 전용)",
    "POST /admin/auth/signup": "담당자 가입(회사 생성)",
    "POST /admin/auth/login": "담당자 로그인(JWT 발급)",
    "GET /admin/catalog": "채용 건 생성 화면에 보일 요소 안내와 샘플 리포트",
    "GET /admin/positions": "회사의 채용 건 목록",
    "POST /admin/positions": "채용 건 생성(시나리오 조립·고정)",
    "GET /admin/positions/{position_id}/candidates": "채용 건의 지원자 목록(상태·요소 수준 칩)",
    "POST /admin/positions/{position_id}/invites": "지원자 초대 링크 발급(72시간)",
    "POST /admin/sessions/{session_id}/reissue": (
        "시작 전 같은 세션의 초대 링크 재발급(실패 세션은 거부)"
    ),
    "POST /admin/sessions/{session_id}/cancel": "초대·진행 세션 취소",
    "GET /admin/sessions/{session_id}/report": "리포트 조회(초안·검토 완료본)",
    "POST /admin/sessions/{session_id}/report/regenerate": "실패한 리포트 다시 생성",
    "POST /admin/sessions/{session_id}/review": "차원 수준 조정과 검토 완료(최종 리포트 확정)",
    "POST /admin/sessions/{session_id}/report/notes": "검토 완료 뒤 메모 추가",
    "GET /admin/sessions/{session_id}/record": "전체 기록 타임라인",
    "GET /s/{token}": "초대 링크 입장 안내(회사·길이·문의처)",
    "POST /s/{token}/consent": "안내 동의와 표시 이름 저장",
    "POST /s/{token}/resume": "같은 링크로 이어하기(세션 토큰 재발급)",
    "POST /s/{token}/start": "세션 시작(칸·기획안 초안 생성, 첫 단계 시작)",
    "GET /sessions/{session_id}/state": "토론 화면 상태(단계·칸·시간·참여자)",
    "POST /sessions/{session_id}/slots/{phase}/confirm": "칸 확정(키워드·문제 상황·해결책)",
    "GET /sessions/{session_id}/materials": "참고 자료 목록(키워드 확정 전에는 잠김)",
    "GET /sessions/{session_id}/materials/{material_id}": "열린 참고 자료 본문",
    "POST /sessions/{session_id}/assistant": "AI 어시스턴트 질문(세션당 60회)",
    "GET /sessions/{session_id}/proposal": "기획안 조회",
    "PUT /sessions/{session_id}/proposal": "기획안 자동 저장",
    "POST /sessions/{session_id}/proposal/submit": "기획안 제출(세션 종료)",
    "GET /sessions/{session_id}/debrief": "디브리핑(회고·설명)",
    "POST /sessions/{session_id}/debrief/survey": "디브리핑 설문 제출",
    "WS /ws/sessions/{session_id}": "토론방 실시간 채널(메시지·입력 중·단계 전환)",
    "GET /health": "헬스 체크",
    "GET /internal/sessions/{session_id}": "세션 요약과 자리 목록",
    "GET /internal/sessions/{session_id}/debate_state": "현재 단계 토론 상태(DebateState)",
    "GET /internal/sessions/{session_id}/messages": "토론방 메시지 조회",
    "POST /internal/sessions/{session_id}/messages": "동료 메시지 게시(generation 검사)",
    "POST /internal/sessions/{session_id}/options": "안 제안(최초 제안자 기록)",
    "POST /internal/sessions/{session_id}/stances": "안 입장(지지·반대) 기록",
    "POST /internal/sessions/{session_id}/slots/fill": "합의된 칸 채우기",
    "GET /internal/sessions/{session_id}/proposal": "기획안 조회",
    "GET /internal/sessions/{session_id}/materials": "열린 자료 목록과 catalog_version(rag-mcp용)",
    "GET /internal/sessions/{session_id}/materials/{material_id}": "자료 원문(rag-mcp용)",
    "POST /internal/sessions/{session_id}/observations": "카드 관찰 기록(멱등)",
    "GET /internal/sessions/{session_id}/observations": "카드 관찰 조회",
    "GET /internal/sessions/{session_id}/transcript_span": "근거 메시지 앞뒤 대화 조회",
    "GET /internal/sessions/{session_id}/contributions": "기여 대조 자료",
    "GET /internal/sessions/{session_id}/rubric": "척도(요소·차원·수준 앵커)",
    "POST /internal/sessions/{session_id}/evaluations": "평가 저장(검증·멱등)",
    "GET /internal/sessions/{session_id}/evaluations": "저장된 평가 조회",
    "GET /internal/sessions/{session_id}/facts": "사실 지표",
    "GET /internal/sessions/{session_id}/slots": "칸 확정 결과",
    "GET /internal/sessions/{session_id}/assistant_calls": "어시스턴트 호출 기록",
    "POST /internal/sessions/{session_id}/report/draft": "리포트 초안 저장",
    "POST /internal/sessions/{session_id}/report/final": "리포트 최종본 저장",
    "POST /internal/sessions/{session_id}/report_failed": "리포트 생성 실패 기록",
    "POST /internal/test/sessions": "단계 시드 세션 생성(ENABLE_TEST_API=1)",
    "POST /internal/test/sessions/{session_id}/advance": "세션 시간 당기기(ENABLE_TEST_API=1)",
}

TEST_CALL = re.compile(r"""\b(?:it|test)(?:\.\w+)?\(\s*(["'`])(.+?)\1""")


def first_sentence(doc: str | None) -> str:
    paragraph = " ".join((doc or "").strip().split("\n\n")[0].split())
    head, dot, _ = paragraph.partition(". ")
    return head + dot.strip()


def router_routes(path: Path) -> list[tuple[str, str, str]]:
    """routers/<이름>.py에서 (메서드, 전체 경로, 함수 이름)을 선언 순서대로 꺼낸다."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prefix = ""
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if getattr(call.func, "id", "") == "APIRouter":
                for kw in call.keywords:
                    if kw.arg == "prefix":
                        prefix = ast.literal_eval(kw.value)
    routes = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            if (
                isinstance(deco, ast.Call)
                and isinstance(deco.func, ast.Attribute)
                and getattr(deco.func.value, "id", "") == "router"
                and deco.func.attr in ("get", "post", "put", "patch", "delete", "websocket")
            ):
                method = "WS" if deco.func.attr == "websocket" else deco.func.attr.upper()
                routes.append((method, prefix + ast.literal_eval(deco.args[0]), node.name))
    return routes


def gen_api(_: str) -> str:
    base = ROOT / "services/api/app/routers"
    groups = [
        ("공개 앱(8000)", ["candidate", "admin", "ws"]),
        ("내부 앱(8001, `X-Internal-Token`)", ["internal"]),
    ]
    out = [NOTE, ""]
    for title, names in groups:
        out += [f"**{title}**", "", "| 메서드 | 경로 | 하는 일 |", "|---|---|---|"]
        rows = [r for n in names for r in router_routes(base / f"{n}.py")]
        rows.insert(0, ("GET", "/health", "health"))
        for method, route, func in rows:
            summary = SUMMARIES.get(f"{method} {route}")
            if summary is None:
                raise SystemExit(f"SUMMARIES에 없는 경로: {method} {route} ({func})")
            out.append(f"| {method} | `{route}` | {summary} |")
        out.append("")
    return "\n".join(out).rstrip()


def mcp_tools(service: str) -> list[tuple[str, str, list[str]]]:
    path = ROOT / f"services/{service}/{MCP_SERVICES[service]}/tools.py"
    tools = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and any(
            getattr(d, "attr", getattr(d, "id", "")) == "tool"
            or getattr(getattr(d, "func", None), "attr", "") == "tool"
            for d in node.decorator_list
        ):
            args = [a.arg for a in node.args.args + node.args.kwonlyargs]
            tools.append((node.name, first_sentence(ast.get_docstring(node)), args))
    return tools


def gen_mcp_tools(service: str) -> str:
    out = [NOTE, "", "| 도구 | 하는 일 | 인자 |", "|---|---|---|"]
    for name, summary, args in mcp_tools(service):
        out.append(f"| `{name}` | {summary} | {', '.join(f'`{a}`' for a in args)} |")
    return "\n".join(out)


def _pytest(cwd: Path, extra: list[str], env: dict[str, str] | None = None) -> list[str]:
    cmd = ["uv", "run", *extra]
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})}
    )
    ids = [line for line in proc.stdout.splitlines() if "::" in line and not line.startswith(" ")]
    if proc.returncode not in (0, 5) or not ids:
        raise SystemExit(f"pytest 수집 실패({cwd}):\n{proc.stdout[-800:]}{proc.stderr[-800:]}")
    return ids


@cache
def collect_service(service: str) -> tuple[tuple[str, ...], frozenset[str]]:
    """(전체 node id, live node id). node id는 서비스 폴더 기준이다."""
    cwd = ROOT / "services" / service
    base = ["pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
    every = _pytest(cwd, base + ["-m", "live or not live"])
    live = subprocess.run(
        ["uv", "run", *base, "-m", "live"], cwd=cwd, capture_output=True, text=True
    )
    return tuple(every), frozenset(x for x in live.stdout.splitlines() if "::" in x)


@cache
def collect_root() -> tuple[str, ...]:
    """저장소 루트 검사(make test의 ROOT_TESTS)와 e2e·eval mock 테스트."""
    root_ids = _pytest(
        ROOT,
        [
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_contract_fixtures.py",
            "scenarios/missions/debate/tests",
        ],
    )
    e2e_ids = _pytest(
        ROOT,
        [
            "--project",
            "services/api",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "asyncio_mode=auto",
            "-o",
            "markers=live: 실제 LLM 호출",
            "tests/e2e",
            "tests/eval",
        ],
        {"PYTHONPATH": f"{ROOT / 'services/api'}:{ROOT}"},
    )
    return tuple(root_ids + e2e_ids)


def base_id(node_id: str) -> str:
    return re.sub(r"\[.*\]$", "", node_id)


def details(files: dict[str, list[str]], live: frozenset[str] = frozenset()) -> list[str]:
    out = []
    for file, names in files.items():
        counts: dict[str, int] = defaultdict(int)
        for n in names:
            counts[base_id(n)] += 1
        out += [f"<details><summary><code>{file}</code> ({len(names)})</summary>", ""]
        for n, c in counts.items():
            tag = " (live)" if any(x.startswith(f"{file}::{n}") for x in live) else ""
            out.append(f"- `{n}`{' ×' + str(c) if c > 1 else ''}{tag}")
        out += ["", "</details>", ""]
    return out


def gen_tests_py(service: str) -> str:
    every, live = collect_service(service)
    files: dict[str, list[str]] = defaultdict(list)
    for node_id in every:
        file, name = node_id.split("::", 1)
        files[file].append(name)
    live_note = f"(live {len(live)}개 포함, `make test`는 live를 뺀다)" if live else ""
    out = [
        NOTE,
        "",
        f"총 {len(every)}개{live_note}.",
        "",
    ]
    return "\n".join(out + details(files, live)).rstrip()


def gen_tests_web(_: str) -> str:
    web = ROOT / "apps/web"
    units, specs = defaultdict(list), defaultdict(list)
    for path in sorted(web.rglob("*.test.ts*")):
        if "node_modules" in path.parts or ".next" in path.parts:
            continue
        units[path.relative_to(web).as_posix()] = [
            m[1] for m in TEST_CALL.findall(path.read_text())
        ]
    for path in sorted((web / "e2e").rglob("*.spec.ts")):
        specs[path.relative_to(web).as_posix()] = [
            m[1] for m in TEST_CALL.findall(path.read_text())
        ]
    out = [NOTE, ""]
    n_unit = sum(map(len, units.values()))
    n_spec = sum(map(len, specs.values()))
    out += [f"**단위·컴포넌트 테스트(vitest, `pnpm test`)**: 테스트 제목 {n_unit}개(정적 파싱)", ""]
    out += _plain_details(units)
    out += [
        f"**단계별 화면 테스트(Playwright, `make e2e-stages`)**: 테스트 제목 {n_spec}개(정적 파싱)",
        "",
    ]
    out += _plain_details(specs)
    return "\n".join(out).rstrip()


def _plain_details(files: dict[str, list[str]]) -> list[str]:
    out = []
    for file, titles in files.items():
        out += [f"<details><summary><code>{file}</code> ({len(titles)})</summary>", ""]
        out += [f"- {t}" for t in titles] + ["", "</details>", ""]
    return out


def generate(readme: str, name: str) -> str:
    service = Path(readme).parent.name
    if name == "api":
        return gen_api(service)
    if name == "mcp-tools":
        return gen_mcp_tools(service)
    if name == "tests":
        return gen_tests_web(service) if service == "web" else gen_tests_py(service)
    raise SystemExit(f"알 수 없는 생성 블록: {name} ({readme})")


def marker_problems(text: str, required: tuple[str, ...]) -> list[str]:
    """생성 블록 마커 검사.

    필수 블록마다 시작·끝이 정확히 한 쌍이고 시작이 끝보다 앞이며, 블록이 겹치지 않고,
    목록 밖 블록이나 짝 없는 마커가 없어야 한다.
    """
    starts = [(m.start(), m["name"]) for m in re.finditer(r"<!-- gen:(?P<name>[\w-]+) -->", text)]
    ends = [(m.start(), m["name"]) for m in re.finditer(r"<!-- /gen:(?P<name>[\w-]+) -->", text)]
    problems = []
    names = {n for _, n in starts} | {n for _, n in ends} | set(required)
    for name in sorted(names):
        s_pos = [p for p, n in starts if n == name]
        e_pos = [p for p, n in ends if n == name]
        if name not in required:
            problems.append(f"필수 목록에 없는 블록: {name}")
        elif len(s_pos) != 1 or len(e_pos) != 1:
            problems.append(
                f"{name}: 시작 마커 {len(s_pos)}개, 끝 마커 {len(e_pos)}개(각 1개여야 함)"
            )
        elif s_pos[0] > e_pos[0]:
            problems.append(f"{name}: 끝 마커가 시작 마커보다 앞에 있다")
    spans = sorted(
        (sp, ep, n)
        for n in required
        for sp in [p for p, m in starts if m == n][:1]
        for ep in [p for p, m in ends if m == n][:1]
    )
    for (_, e1, n1), (s2, _, n2) in zip(spans, spans[1:], strict=False):
        if s2 < e1:
            problems.append(f"{n1}·{n2}: 블록이 겹친다")
    return problems


def render(readme: str) -> str:
    path = ROOT / readme
    text = path.read_text(encoding="utf-8")

    def fill(match: re.Match[str]) -> str:
        name = match["name"]
        return f"<!-- gen:{name} -->\n{generate(readme, name)}\n<!-- /gen:{name} -->"

    return re.sub(
        r"<!-- gen:(?P<name>[\w-]+) -->.*?<!-- /gen:(?P=name) -->", fill, text, flags=re.S
    )


def check_features() -> list[str]:
    """features.md 표의 테스트 참조(백틱 안 `경로::이름`, `.yaml` 경로)가 실제로 있는지 본다."""
    text = (ROOT / "docs/features.md").read_text(encoding="utf-8")
    known = {f"services/{s}/{i}" for s in PY_SERVICES for i in collect_service(s)[0]}
    known |= set(collect_root())
    known_base = {base_id(k) for k in known}
    problems = []
    for ref in sorted(set(re.findall(r"`([^`\n]+(?:::[^`\n]+|\.yaml))`", text))):
        file, _, name = ref.partition("::")
        if file.endswith(".py"):
            if ref not in known and base_id(ref) not in known_base:
                problems.append(f"pytest 테스트가 없다: {ref}")
        elif not (ROOT / file).is_file():
            problems.append(f"파일이 없다: {file}")
        elif name and name not in (ROOT / file).read_text(encoding="utf-8"):
            problems.append(f"스펙 제목이 파일에 없다: {ref}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="커밋된 README와 다르면 실패")
    parser.add_argument("--features", action="store_true", help="features.md 테스트 참조 검사")
    args = parser.parse_args()
    failed = False
    stale = False
    if args.features:
        problems = check_features()
        for p in problems:
            print(f"features.md: {p}")
        failed = bool(problems)
        if not args.check:
            return int(failed)
    for readme in READMES:
        text = (ROOT / readme).read_text(encoding="utf-8")
        problems = marker_problems(text, REQUIRED_BLOCKS[readme])
        if problems:
            for p in problems:
                print(f"{readme}: {p}")
            failed = True
            continue
        new = render(readme)
        if new == text:
            continue
        if args.check:
            sys.stdout.writelines(
                difflib.unified_diff(
                    text.splitlines(True), new.splitlines(True), readme, f"{readme} (생성)"
                )
            )
            failed = stale = True
        else:
            (ROOT / readme).write_text(new, encoding="utf-8")
            print(f"갱신: {readme}")
    if stale:
        print("\n생성 블록이 코드와 다르다. make docs-gen으로 갱신한다.")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
