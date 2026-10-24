"""시간 당기기: 내부 테스트 API로 세션 시계를 앞당겨 시간 초과·침묵 초대·카드를 기다리지 않고 본다.

    python scripts/stage_advance.py --sid <session_id> --to deadline|idle|card:<card_id>|<초>

처리는 다음 스케줄러 틱이 평소 경로로 한다.
"""

import argparse
import asyncio

import httpx
from make_session import env, require_allow_api


def parse_to(to: str) -> dict:
    return {"seconds": int(to)} if to.isdigit() else {"to": to}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="세션 시간을 앞당긴다(내부 테스트 API).")
    parser.add_argument("--sid", required=True, help="세션 id")
    parser.add_argument("--to", required=True, help="deadline|idle|card:<card_id>|<초>")
    return parser.parse_args(argv)


async def main() -> None:
    args = parse_args()
    values = env()
    require_allow_api(values, "stage_advance")
    internal_url = values.get("E2E_INTERNAL_URL", "http://localhost:8001")
    headers = {"X-Internal-Token": values.get("INTERNAL_TOKEN", "")}
    async with httpx.AsyncClient(base_url=internal_url, timeout=30, headers=headers) as internal:
        r = await internal.post(
            f"/internal/test/sessions/{args.sid}/advance", json=parse_to(args.to)
        )
    r.raise_for_status()
    for key, value in r.json().items():
        print(f"{key}={value}")


if __name__ == "__main__":
    asyncio.run(main())
