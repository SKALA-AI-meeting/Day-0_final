"""시드: 자료 원문(materials) → 관리자 가입(이미 있으면 로그인)·채용 건 생성 → rag-mcp 재색인.

1. 미션 자료(keywords.yaml의 materials)를 현재 catalog_version으로 `materials` 테이블에 넣는다.
   이전 버전 행은 지우지 않고, 같은 버전은 덮어쓴다(멱등). DB(DATABASE_URL, 기본 localhost)만 필요.
2. api(8000)로 관리자 가입·채용 건 생성(요소 선택 없음, 세션 길이만). `--skip-position`으로 건너뜀.
3. rag-mcp(9102) `reindex_materials`로 같은 버전 청크 색인. `--skip-reindex`로 건너뜀.

    make seed SCOPE=infra       # 로컬 개발(호스트 api 8000·postgres 5432·rag 9102)
    make seed PROFILE=dev-fake  # 전체 compose: compose 안 일회성 seed 서비스(포트 공개 없음)
    uv run --project services/api python scripts/seed.py --minutes 15
    uv run --project services/api python scripts/seed.py --skip-position --skip-reindex   # 자료만
"""

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import asyncpg
import httpx
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = REPO_ROOT / "scenarios"
DEFAULT_DATABASE_URL = "postgresql://dayzero:dayzero@localhost:5432/dayzero"
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def catalog_version(mission_id: str) -> str:
    spec = importlib.util.spec_from_file_location("scenarios_version", SCENARIOS / "version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.catalog_version(mission_id, SCENARIOS)


def load_materials(mission_id: str) -> list[tuple[str, str, str, str]]:
    """[(material_id, keyword_key, title, content)]. 목록·제목 정본은 keywords.yaml."""
    root = SCENARIOS / "missions" / mission_id
    catalog = yaml.safe_load((root / "keywords.yaml").read_text(encoding="utf-8"))
    return [
        (
            item["material_id"],
            keyword["key"],
            item["title"],
            (root / "materials" / item["file"]).read_text(encoding="utf-8"),
        )
        for keyword in catalog["keywords"]
        for item in keyword.get("materials", [])
    ]


async def seed_materials(database_url: str, mission_id: str) -> tuple[str, int]:
    version = catalog_version(mission_id)
    rows = load_materials(mission_id)
    conn = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await conn.executemany(
            "INSERT INTO materials (catalog_version, material_id, keyword_key, title, content)"
            " VALUES ($1, $2, $3, $4, $5)"
            " ON CONFLICT (catalog_version, material_id) DO UPDATE"
            " SET keyword_key = EXCLUDED.keyword_key, title = EXCLUDED.title,"
            " content = EXCLUDED.content",
            [(version, *row) for row in rows],
        )
    finally:
        await conn.close()
    return version, len(rows)


def admin_token(client: httpx.Client, email: str, password: str, company: str) -> str:
    response = client.post(
        "/admin/auth/signup", json={"email": email, "password": password, "company_name": company}
    )
    if response.status_code == 409:  # 이미 가입됨
        response = client.post("/admin/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return response.json()["token"]


def create_position(
    client: httpx.Client, token: str, name: str, minutes: int, mission_id: str
) -> dict:
    response = client.post(
        "/admin/positions",
        json={"name": name, "session_minutes": minutes, "mission_id": mission_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    return response.json()


def _rpc_result(response: httpx.Response) -> dict:
    """Streamable HTTP 응답(JSON 또는 SSE의 data: 줄)에서 마지막 JSON-RPC result를 꺼낸다."""
    response.raise_for_status()
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:] for line in response.text.splitlines() if line.startswith("data:")]
        payload = json.loads(data[-1])
    else:
        payload = response.json()
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return payload["result"]


def call_mcp_tool(url: str, tool: str, arguments: dict) -> dict:
    with httpx.Client(timeout=300) as client:
        init = client.post(
            url,
            headers=MCP_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "dayzero-seed", "version": "0.1"},
                },
            },
        )
        _rpc_result(init)
        headers = dict(MCP_HEADERS)
        if session := init.headers.get("mcp-session-id"):
            headers["mcp-session-id"] = session
        client.post(
            url, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        result = _rpc_result(
            client.post(
                url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments},
                },
            )
        )
    if result.get("isError"):
        raise RuntimeError(result)
    payload = result.get("structuredContent")
    if payload is None:  # 구조화 출력이 없으면 첫 텍스트 블록의 JSON
        text = next((c["text"] for c in result.get("content", []) if c.get("type") == "text"), "{}")
        payload = json.loads(text)
    # 도구 공통 응답 {ok, data | error}. 업무 오류(ok=false)도 실패로 본다.
    if payload.get("ok") is False:
        raise RuntimeError(payload.get("error"))
    return payload.get("data", payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--rag", default="http://localhost:9102/mcp")
    parser.add_argument(
        "--database-url", default=os.environ.get("SEED_DATABASE_URL", DEFAULT_DATABASE_URL)
    )
    parser.add_argument("--mission", default="debate")
    parser.add_argument("--email", default="admin@dayzero.example")
    parser.add_argument("--password", default="dayzero-seed")
    parser.add_argument("--company", default="데모 회사")
    parser.add_argument("--position", default="서비스기획 신입")
    parser.add_argument("--minutes", type=int, choices=(60, 15), default=60)
    parser.add_argument("--skip-position", action="store_true")
    parser.add_argument("--skip-reindex", action="store_true")
    args = parser.parse_args()

    version, count = asyncio.run(seed_materials(args.database_url, args.mission))
    print(f"materials {count} catalog_version={version}")

    if not args.skip_position:
        with httpx.Client(base_url=args.api, timeout=30) as client:
            token = admin_token(client, args.email, args.password, args.company)
            position = create_position(client, token, args.position, args.minutes, args.mission)
        print(f"position {position['id']} catalog_version={position['catalog_version']}")

    if not args.skip_reindex:
        indexed = call_mcp_tool(args.rag, "reindex_materials", {"mission_id": args.mission})
        print(f"reindex {indexed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
