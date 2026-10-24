"""단계 시드 세션 생성: 내부 테스트 API로 시드를 재생해 그 단계가 막 시작된 세션을 만든다.

    python scripts/make_session.py --start-at problem --minutes 15 [--llm live]

`--start-at`은 entry(초대만)·keyword·problem·solution·proposal·ended, `--stage`는 재생할 시드 이름
(생략하면 start-at과 같다). 출력은 초대 링크·세션 id와 단계별로 응답에 있는 확인용 정보(세션 토큰,
디브리핑 토큰, 관리자 리포트 링크)다. `--llm`은 어느 스택이어야 하는지 안내만 하고 스택은 건드리지
않는다. 토큰은 시드 세션 것만 찍고 내부 토큰은 찍지 않는다.

과금 게이트: 시드 세션은 만들어지는 순간부터 AI 동료가 진행하므로, 결정적 스택이 명시되지
않았으면(`--llm live`, env `LLM_FAKE=0`, `LLM_FAKE` 없음) `ALLOW_API=1` 없이는 시작하지 않는다.
없음을 실제로 보는 것은 api(`LLM_FAKE == "1"`만 fake)와 같은 규칙이다. `stage_advance.py`도 같다
(`make stage*`는 프로필의 `LLM_FAKE`를 넘긴다).
"""

import argparse
import asyncio
import os
from pathlib import Path

import httpx
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]
START_AT = ("entry", "keyword", "problem", "solution", "proposal", "ended")
LLM_NOTE = {
    "fake": "LLM=fake: api·report-worker가 LLM_FAKE=1로 떠 있어야 결정적으로 진행된다.",
    "live": "LLM=live: api·report-worker가 LLM_FAKE 없이(실제 모델 키로) 떠 있어야 한다.",
}


def env() -> dict[str, str]:
    """infra/.env → infra/.env.local 순(나중 것이 이김), 환경변수가 가장 우선."""
    values: dict[str, str] = {}
    for name in (".env", ".env.local"):
        path = REPO_ROOT / "infra" / name
        if path.is_file():
            values.update({k: v for k, v in dotenv_values(path).items() if v is not None})
    values.update(os.environ)
    return values


def require_allow_api(values: dict[str, str], what: str, live: bool = False) -> None:
    """env `LLM_FAKE=1`로 명시된 결정적 스택이 아니면(live·0·없음) `ALLOW_API=1` 없이는 끝낸다."""
    value = values.get("LLM_FAKE")
    if live:
        source = "--llm live"
    else:
        source = "LLM_FAKE 없음" if value is None else f"env LLM_FAKE={value}"
    if not live and value == "1":
        return
    if values.get("ALLOW_API") != "1":
        raise SystemExit(
            f"{what}: 대상 스택이 실제 LLM({source})이라 세션 진행이 모델 호출(과금)이다. "
            "ALLOW_API=1을 명시해야 실행한다"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="단계 시드 세션을 만든다(내부 테스트 API).")
    parser.add_argument("--start-at", choices=START_AT, default="entry")
    parser.add_argument("--stage", help="재생할 시드 이름(기본: --start-at과 같다)")
    parser.add_argument("--minutes", type=int, choices=(15, 60), default=15)
    parser.add_argument("--alias", help="지원자 별칭(기본: 자동)")
    parser.add_argument("--admin-email", help="세션을 만들 관리자 이메일(기본: e2e@example.com)")
    parser.add_argument("--llm", choices=tuple(LLM_NOTE), default="fake")
    return parser.parse_args(argv)


def request_body(args: argparse.Namespace) -> dict:
    body: dict = {"session_minutes": args.minutes, "start_at": args.start_at}
    for key, value in (
        ("stage", args.stage),
        ("alias", args.alias),
        ("admin_email", args.admin_email),
    ):
        if value:
            body[key] = value
    return body


async def make(internal: httpx.AsyncClient, body: dict) -> dict:
    r = await internal.post("/internal/test/sessions", json=body)
    r.raise_for_status()
    return r.json()


async def main() -> None:
    args = parse_args()
    values = env()
    require_allow_api(values, "make_session", live=args.llm == "live")
    internal_url = values.get("E2E_INTERNAL_URL", "http://localhost:8001")
    headers = {"X-Internal-Token": values.get("INTERNAL_TOKEN", "")}
    async with httpx.AsyncClient(base_url=internal_url, timeout=60, headers=headers) as internal:
        made = await make(internal, request_body(args))
    print(f"session_id={made['session_id']}")
    print(f"invite_url={made['invite_url']}")
    print(f"phase={made['phase']}")
    for key in ("session_token", "debrief_token", "admin_report_url"):
        if made.get(key):
            print(f"{key}={made[key]}")
    print(LLM_NOTE[args.llm])


if __name__ == "__main__":
    asyncio.run(main())
