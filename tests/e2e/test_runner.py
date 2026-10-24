"""러너 happy path(mock): 계약만으로 만든 가짜 api 위에서 대본을 끝까지 돌려 단언이 통과하는지 본다.

가짜 api(fake_stack.py)는 agent의 결정적 LLM 모드와 같은 규칙으로 말하므로, 여기서 통과한다는 것은
러너의 진행·수집·단언 로직이 계약대로 동작한다는 뜻이다(서비스 구현 검증은 실 스택 실행 몫).
"""

import copy
import json

import pytest

from e2e import asserts, records
from e2e import run as run_mod
from e2e import script as scripts
from e2e.run import Runner


@pytest.fixture(autouse=True)
def text_mode(monkeypatch):
    """가짜 API 대본은 텍스트 모드이며, 실 러너의 기본 음성 모드는 별도로 확인한다."""
    monkeypatch.setenv("VOICE_ENABLED", "0")


def test_voice_defaults_to_api_mode(fake, target, monkeypatch):
    monkeypatch.delenv("VOICE_ENABLED")
    runner = Runner(scripts.load("all_five"), target, db=fake.db())
    assert runner.voice_mode


async def run_script(fake, target, name: str, **kwargs):
    runner = Runner(
        scripts.load(name),
        target,
        clock="fake",
        stack=fake,
        transports=fake.transports,
        connector=fake.connector(),
        db=fake.db(),
        typing_hold=0.05,
        poll=0.02,
        report_wait=5,
        **kwargs,
    )
    bundle = await runner.run()
    bundle["results"] = asserts.check(bundle)
    return bundle


def failures(bundle) -> dict[str, list[str]]:
    return {r["id"]: r["failures"] for r in bundle["results"] if not r["ok"]}


@pytest.mark.parametrize("name", ["all_five", "timeout_all", "honesty_claim", "restart"])
async def test_script_passes(fake, target, tmp_path, name):
    bundle = await run_script(fake, target, name)
    assert failures(bundle) == {}
    path = records.write(bundle, tmp_path)
    for file in (
        "transcript.jsonl",
        "turn_decisions.json",
        "evaluations.json",
        "report_draft.json",
        "report_final.json",
        "record.json",
        "summary.md",
    ):
        assert (path / file).is_file(), file
    kinds = {
        json.loads(line)["kind"]
        for line in (path / "transcript.jsonl").read_text("utf-8").splitlines()
    }
    assert {"rest", "ws_in", "ws_out"} <= kinds
    assert "<redacted>" in (path / "transcript.jsonl").read_text("utf-8")
    assert "| 5 | 리포트 | 통과 |" in (path / "summary.md").read_text("utf-8")


async def test_all_five_flow(fake, target):
    """all_five가 실제로 전 흐름을 밟았는지: 칸 3개 조합, 카드 6장, 검토 병합."""
    bundle = await run_script(fake, target, "all_five")
    slots = asserts.slots_by_phase(bundle)
    assert [
        (slots[p]["filled_by"], slots[p]["resolution"]) for p in ("keyword", "problem", "solution")
    ] == [
        ("candidate", "consensus"),
        ("p3", "consensus"),
        ("p3", "consensus"),
    ]
    assert len(bundle["observations"]) == 6
    assert bundle["review"]["approve"] == 200
    assert bundle["final"]["stage"] == "final"
    assert bundle["measure"]["first_persona_p95_sec"] is not None


async def test_broken_contract_is_reported(fake, target):
    """단언이 실제로 걸러 내는지: 근거 없는 수준·총점 필드를 심으면 5가 실패한다."""
    bundle = await run_script(fake, target, "timeout_all")
    draft = bundle["draft"]
    draft["dimensions"][0]["evidence_refs"] = []
    draft["total_score"] = 3.2
    assert not asserts.check(bundle)[5]["ok"]


def _typing(b):
    frame = next(
        f for f in b["frames"] if f["type"] == "message.new" and f["payload"]["sender"] == "p1"
    )
    b["typing_intervals"].append((frame["_at"] - 1, frame["_at"] + 1))


def _three_speakers(b):
    b["runs"][0]["decision"]["speakers"] = [{"seat": "p1"}, {"seat": "p2"}, {"seat": "p3"}]


def _fill_without_candidate(b):
    slot = next(s for s in b["slots"] if s["filled_by"] == "p3")
    slot["support"] = [{"option_id": slot["option_id"], "supporters": ["p1", "p3"]}]


def _drop_card(b):
    b["observations"] = [o for o in b["observations"] if o["card_id"] != "inclusion_silence"]


def _preempt(b):
    quiet_card = next(o for o in b["observations"] if o["card_id"] == "inclusion_silence")
    b["messages"].append(
        {
            "id": "x",
            "sender": "p3",
            "content": "하람님 의견은요?",
            "phase": "solution",
            "t_min": quiet_card["t_min"] + 0.1,
            "is_invite": False,
            "card_ids": [],
            "meta": {},
        }
    )


def _shift_deadline(b):
    b["advances"].append({"at": 0.0, "seconds": 120})


def _readjust_accepted(b):
    b["review"]["readjust"] = 200


def _quiet_unprompted(b):
    b["messages"].append(
        {
            "id": "y",
            "sender": "p2",
            "content": "제 생각은요",
            "phase": "keyword",
            "t_min": 0.1,
            "is_invite": False,
            "card_ids": [],
            "meta": {},
        }
    )


VIOLATIONS = [
    ("2", _typing),
    ("2", _three_speakers),
    ("2", _fill_without_candidate),
    ("2", _quiet_unprompted),
    ("3", _drop_card),
    ("4", _preempt),
    ("1", _shift_deadline),
    ("6", _readjust_accepted),
]


@pytest.mark.parametrize(("number", "mutate"), VIOLATIONS, ids=[m.__name__ for _, m in VIOLATIONS])
async def test_each_assertion_catches_its_violation(fake, target, number, mutate):
    bundle = await run_script(fake, target, "all_five")
    assert failures(bundle) == {}
    broken = copy.deepcopy(bundle)
    mutate(broken)
    assert not {r["id"]: r for r in asserts.check(broken)}[number]["ok"]


async def test_preemption_only_checked_until_candidate_invites_quiet(fake, target):
    bundle = await run_script(fake, target, "all_five")
    card = next(o for o in bundle["observations"] if o["card_id"] == "inclusion_silence")

    def add(sender, content, t_min, **extra):
        bundle["messages"].append(
            {
                "id": f"x-{sender}-{t_min}",
                "sender": sender,
                "content": content,
                "phase": "solution",
                "t_min": t_min,
                "is_invite": False,
                "card_ids": [],
                "meta": {},
                **extra,
            }
        )

    original = copy.deepcopy(bundle)
    add("p3", "하람님 의견은요?", card["t_min"] + 0.1)
    assert asserts.assert_4_preemption(bundle)  # 초대 전 언급은 잡는다
    bundle = copy.deepcopy(original)
    add("candidate", "윤하람님은 어떻게 생각하세요?", card["t_min"] + 0.05)
    add("p3", "하람님도 말씀하신 것처럼…", card["t_min"] + 0.1)
    assert asserts.assert_4_preemption(bundle) == []  # 초대 뒤 언급은 통과


async def test_live_llm_mode_skips_level_expectations(fake, target, monkeypatch):
    """실 LLM(LLM_FAKE=0)이면 expect.dimensions의 level 비교는 건너뛰고 근거 참조는 그대로 본다."""
    bundle = await run_script(fake, target, "all_five", llm_fake=False)
    draft = bundle["draft"]
    next(d for d in draft["dimensions"] if d["dimension_id"] == "listen_engage")["level"] = 4
    bundle["llm_fake"] = True
    assert any("listen_engage 수준 4 != 3" in f for f in asserts.assert_5_report(bundle))
    bundle["llm_fake"] = False
    assert not any("!= 3" in f for f in asserts.assert_5_report(bundle))
    monkeypatch.setenv("LLM_FAKE", "0")
    assert run_mod.detect_llm_fake(False)[0] is False
    assert run_mod.detect_llm_fake(False)[1] == "env LLM_FAKE=0"
    monkeypatch.setenv("LLM_FAKE", "1")
    assert run_mod.detect_llm_fake(False)[0] is True
    monkeypatch.delenv("LLM_FAKE")
    assert run_mod.detect_llm_fake(False)[0] is False  # 없음 = 실제 LLM(api와 같다)


def test_live_stack_requires_allow_api():
    with pytest.raises(SystemExit, match="ALLOW_API=1"):
        run_mod.require_allow_api("env LLM_FAKE=0", {})
    run_mod.require_allow_api("env LLM_FAKE=0", {"ALLOW_API": "1"})


async def test_assistant_must_cite_keyword_materials(fake, target):
    """assistant step이 있는 대본: 인용이 비면(색인 버전 불일치 등) 단언 5가 실패한다."""
    bundle = await run_script(fake, target, "all_five")
    assert asserts._assistant_citations(bundle) == []
    for call in bundle["assistant_calls"]:
        call["cited_material_ids"] = []
    assert any("인용하지 못함" in f for f in asserts.assert_5_report(bundle))
    bundle["assistant_calls"][0]["cited_material_ids"] = ["fire_stats"]
    assert any("확정 키워드의 자료가 아님" in f for f in asserts.assert_5_report(bundle))
