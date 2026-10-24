"""장애 주입 2종(단언 10)과 인증 경계(단언 8)를 가짜 api 위에서 돌린다."""

import httpx
import pytest

from e2e import auth, faults
from e2e.faults import Proxy


@pytest.fixture(autouse=True)
def text_mode(monkeypatch):
    """가짜 api는 텍스트 모드라 러너도 텍스트로 맞춘다(러너 기본은 api와 같은 음성 켬)."""
    monkeypatch.setenv("VOICE_ENABLED", "0")


def wire(fake):
    fake.runner_kwargs = {
        "transports": fake.transports,
        "connector": fake.connector(),
        "db": fake.db(),
        "typing_hold": 0.05,
        "poll": 0.02,
    }


def failed(bundle) -> dict[str, list[str]]:
    return {r["id"]: r["failures"] for r in bundle["results"] if not r["ok"]}


async def test_dlq_then_regenerate(fake, target):
    wire(fake)
    bundle = await faults.run_dlq(fake, target)
    assert failed(bundle) == {}
    assert bundle["failed_seen"] and bundle["regenerated"]


async def test_llm_timeout_persona_skipped_evaluator_retried(fake, target):
    wire(fake)
    bundle = await faults.run_llm_timeout(fake, target)
    assert failed(bundle) == {}
    assert bundle["fault"]["persona_replies_faulted"] == 0


async def test_proxy_injects_then_passes():
    """프록시: 일치 요청을 times번 503으로 돌려주고 그다음부터 업스트림으로 보낸다."""

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "report_ready"})

    proxy = Proxy("http://upstream", transport=httpx.MockTransport(upstream))
    proxy.fail("report_draft", 2)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy), base_url="http://proxy"
    ) as client:
        path = "/internal/sessions/s1/report/draft"
        codes = [(await client.post(path, json={})).status_code for _ in range(3)]
    assert codes == [503, 503, 200]
    assert [e["injected"] for e in proxy.log] == [True, True, False]


async def test_auth_boundaries(fake, target, tmp_path):
    async def probe(session_id: str) -> str:
        return '{"ok": false, "error": {"code": "forbidden"}}'

    ok = await auth.run(target, tmp_path, transports=fake.transports, mcp_probe=probe)
    assert ok
    (report,) = tmp_path.glob("*-auth/auth.md")
    assert "| 만료 JWT로 /state | 통과 |" in report.read_text("utf-8")
