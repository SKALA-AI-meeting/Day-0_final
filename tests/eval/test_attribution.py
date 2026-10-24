"""귀속 추출·판정 도구의 순수 부분(LLM 없이): 원문 조립, 출력 파싱, 지표 계산, 보정 세트·일치도."""

import json
import sys
from pathlib import Path

from eval import attribution as at
from eval import persona_eval as pe

sys.path[:0] = [str(Path(__file__).resolve().parent)]
import build_calibration as bc  # noqa: E402
import calibration_agreement as ca  # noqa: E402


def _events():
    return [
        {
            "session": "problem",
            "run": 0,
            "kind": "phase_started",
            "posts": [{"id": "a1", "seat": "p1", "content": "저는 가뭄이 핵심이에요.", "meta": {}}],
        },
        {
            "session": "problem",
            "run": 0,
            "kind": "evidence",
            "say": "침수가 더 급해요.",
            "message_id": "c1",
            "scene": "weak_agree",
            "posts": [
                {
                    "id": "a2",
                    "seat": "p3",
                    "content": "지원자님이 가뭄에 동의하셨죠.",
                    "meta": {
                        "quote_check": "regenerated",
                        "quote_violation": "candidate의 이 단계 발언·입장 기록에 없는 말을 "
                        "그 사람 것으로 돌림: 지원자가 가뭄에 동의했다",
                        "stance_check": "pass",
                        "guard": "pass",
                    },
                },
                {
                    "id": "a3",
                    "seat": "p2",
                    "content": "저도 자료를 볼게요.",
                    "meta": {"quote_check": "none"},
                },
            ],
        },
        {
            "session": "problem",
            "run": 1,
            "kind": "phase_started",
            "posts": [{"id": "b1", "seat": "p1", "content": "가뭄이 핵심이에요.", "meta": {}}],
        },
    ]


def test_transcript_from_events_keeps_order_and_only_past_sources():
    t = at.transcript_from_events(_events())
    assert [m["sender"] for m in t] == ["p1", "candidate", "p3", "p2", "p1"]
    assert [m["order"] for m in t] == [1, 2, 3, 4, 1]  # 실행이 바뀌면 순서도 새로 센다
    sources, truncated = at.sources_before(t, 2)  # a2 앞: a1·c1만, 미래(a3)·다른 실행(b1) 없음
    assert [s["id"] for s in sources] == ["a1", "c1"] and not truncated
    assert at.sources_before(t, 4) == ([], False)
    payload = at.payload_for(t, 2)
    assert payload["post"]["id"] == "a2" and payload["sources"][1]["speaker_name"] == "지원자"
    assert payload["draft_rejected_claims"] == [
        {"target": "candidate", "claim": "지원자가 가뭄에 동의했다"}
    ]


def test_transcript_from_session_and_markdown():
    data = {
        "session": [{"id": "s", "display_name": "태오"}],
        "messages": [
            {
                "id": "m1",
                "sender": "candidate",
                "phase": "keyword",
                "content": "물이요",
                "created_at": "2026-10-23T00:00:01+00:00",
                "meta": {},
            },
            {
                "id": "m2",
                "sender": "p1",
                "phase": "keyword",
                "content": "태오님은 물이시군요",
                "created_at": "2026-10-23T00:00:02+00:00",
                "meta": {"quote_check": "pass"},
            },
            {
                "id": "m3",
                "sender": "p2",
                "phase": "keyword",
                "content": "그럼 물로 정리할게요.",
                "created_at": "2026-10-23T00:00:03+00:00",
                "meta": {"kind": "consensus_fill"},
            },
            {
                "id": "m4",
                "sender": "assistant",
                "channel": "assistant",
                "phase": "keyword",
                "content": "자료",
                "created_at": "2026-10-23T00:00:04+00:00",
                "meta": {},
            },
        ],
    }
    t = at.transcript_from_session(data)
    assert [m["id"] for m in t] == ["m1", "m2", "m3"]
    assert t[1]["content"] == "지원자님은 물이시군요" and t[1]["is_post"]
    assert not t[2]["is_post"]  # 합의 공지는 판정 대상이 아니다
    md = (
        "# x\n\n## 대화\n\n### keyword\n\n- (단계 시작)\n  - 강한결: 불이 좋아요.\n"
        "- **지원자**(evidence): 물이요.\n  - 서지안: 지원자님은 물이시군요. `quote_check=safe`\n"
        "### keyword\n\n- (단계 시작)\n  - 강한결: 불이 좋아요.\n"
    )
    t = at.transcript_from_markdown(md)
    assert [(m["unit"][0], m["sender"]) for m in t] == [
        (0, "p1"),
        (0, "candidate"),
        (0, "p3"),
        (1, "p1"),
    ]
    assert t[2]["meta"] == {"quote_check": "safe"} and t[2]["content"].endswith("물이시군요.")


def test_parse_verdict_normalizes_unknown_values():
    got = at.parse_verdict(
        {
            "attributions": [
                {
                    "quote": "지원자님이 동의",
                    "target": "지원자",
                    "claim": "x",
                    "form": "weird",
                    "verdict": "maybe",
                    "source_ids": [1],
                },
                {
                    "quote": "",
                    "target": "p1",
                    "claim": "",
                    "form": "statement",
                    "verdict": "supported",
                },
            ],
            "rejected_claims": [{"claim": "y", "verdict": "supported"}],
        }
    )
    assert got["attributions"] == [
        {
            "quote": "지원자님이 동의",
            "target": "candidate",
            "claim": "x",
            "form": "statement",
            "verdict": "undeterminable",
            "source_ids": ["1"],
            "reason": "",
        }
    ]
    assert got["rejected_claims"] == [{"claim": "y", "verdict": "supported", "reason": ""}]
    assert at.rejected_claim({}) is None
    assert at.rejected_claim({"quote_violation": "형식이 다른 사유"}) == {
        "target": "",
        "claim": "형식이 다른 사유",
    }


async def test_judge_posts_and_summary_math():
    answers = {
        "a1": {"attributions": [], "rejected_claims": []},
        "a2": {
            "attributions": [
                {
                    "quote": "지원자님이 가뭄에 동의하셨죠",
                    "target": "candidate",
                    "claim": "동의",
                    "form": "statement",
                    "verdict": "overstated",
                    "source_ids": ["c1"],
                    "reason": "r",
                }
            ],
            "rejected_claims": [
                {"claim": "지원자가 가뭄에 동의했다", "verdict": "supported", "reason": ""}
            ],
        },
        "a3": {"attributions": [], "rejected_claims": []},
        "b1": {
            "attributions": [
                {
                    "quote": "x",
                    "target": "p3",
                    "claim": "y",
                    "form": "elliptical",
                    "verdict": "unsupported",
                    "source_ids": [],
                    "reason": "",
                },
                {
                    "quote": "z",
                    "target": "candidate",
                    "claim": "w",
                    "form": "statement",
                    "verdict": "supported",
                    "source_ids": ["c1"],
                    "reason": "",
                },
            ],
            "rejected_claims": [],
        },
    }
    seen = []

    async def ask(payload):
        seen.append(payload)
        assert all(s["order"] < payload["post"]["order"] for s in payload["sources"])
        return answers[payload["post"]["id"]]

    judge = at.AttributionJudge({"VC_JUDGE_MODEL": "test-model"}, ask=ask)
    events = _events()
    rows = await at.judge_posts(at.transcript_from_events(events), judge)
    assert [r["post_id"] for r in rows] == ["a1", "a2", "a3", "b1"] and judge.calls == 4
    assert judge.info()["models_used"] == {"test-model": 4}
    s = at.summarize(rows)
    assert (s["posts"], s["attributions"]) == (4, 3)
    assert s["per_attribution"]["overstated"] == {"n": 1, "rate": 0.333}
    assert s["per_attribution"]["unsupported"] == {"n": 1, "rate": 0.333}
    assert s["per_attribution"]["error"]["rate"] == 0.667
    assert s["per_post"]["with_attribution"] == {"n": 2, "rate": 0.5}
    assert s["per_post"]["with_error"] == {
        "n": 2,
        "rate_of_posts": 0.5,
        "rate_of_posts_with_attribution": 1.0,
    }
    assert s["frequency"] == {
        "attributions_per_post": 0.75,
        "histogram": {"0": 2, "1": 1, "2": 1, "3+": 0},
    }
    assert s["by_target"]["peer"]["unsupported"] == 1 and s["by_target"]["candidate"]["n"] == 2
    assert s["by_form"]["elliptical"] == {"n": 1, "overstated": 0, "unsupported": 1}
    assert s["runs"] == 2 and s["ranges"]["attributions_per_post"] == [0.333, 2.0]
    link = s["linkage"]
    assert link["quote_check"] == {"absent": 2, "regenerated": 1, "none": 1}
    assert link["skip_rate"] == {"n": 0, "of": 2, "rate": 0.0}
    assert link["false_positive_regeneration"]["rejected_claims"] == 1
    assert link["false_positive_regeneration"]["rate_of_determined"] == 1.0
    assert link["after_regeneration"] == {"posts": 1, "final_still_error": 1, "final_clean": 0}
    scored = pe.score(events, {}, {"summary": s})
    assert scored["attribution"]["summary"]["posts"] == 4
    assert scored["fallbacks"]["quote_check"] == {"absent": 2, "regenerated": 1, "none": 1}
    assert pe.scene_counts(events, rows) == {
        "weak_agree": {
            "scenes": 1,
            "posts": 2,
            "attributions": 1,
            "overstated": 1,
            "unsupported": 0,
        }
    }


def test_calibration_build_is_deterministic_and_split(tmp_path):
    data = tmp_path / "pr10"
    (data / "review-fix").mkdir(parents=True)
    events = []
    for n in range(60):
        events.append(
            {
                "session": "problem",
                "run": 0,
                "kind": "evidence",
                "say": f"지원자 발언 {n}",
                "message_id": f"c{n}",
                "posts": [
                    {
                        "id": f"a{n}",
                        "seat": "p1",
                        "content": f"지원자님 말씀 {n} 들었어요",
                        "meta": {},
                    },
                    {"id": f"b{n}", "seat": "p3", "content": f"제 생각은 {n}입니다", "meta": {}},
                ],
            }
        )
    (data / "review-fix" / "persona-x.json").write_text(json.dumps(events, ensure_ascii=False))
    items = bc.build(data, 50, 1)
    again = bc.build(data, 50, 1)
    assert [i["item_id"] for i in items] == [i["item_id"] for i in again]
    assert len(items) == 50 and len({i["item_id"] for i in items}) == 50
    kinds = {i["kind"] for i in items}
    assert kinds == {"recorded", "handwritten"}
    hints = [i["type_hint"] for i in items if i["kind"] == "recorded"]
    assert hints.count("plain") == 10 and hints.count("mention") == 30  # 40개 중 1/4은 언급 없음
    assert all(i["split"] in ("calib", "holdout") for i in items)
    assert 14 <= sum(i["split"] == "holdout" for i in items) <= 18
    assert all(s["order"] < i["post"]["order"] for i in items for s in i["sources"])
    assert all("/" not in i["source"] for i in items)  # 경로가 아니라 파일 이름만
    types = bc.summary(items)["by_type"]
    assert {"normal", "overstated", "wrong_speaker", "real_reversal", "no_attribution"} <= set(
        types
    )


def test_agreement_confusion_kappa_and_recall():
    items = [
        {
            "item_id": "i1",
            "split": "calib",
            "type": "overstated",
            "extracted_attributions": [{"target": "candidate", "claim": "지원자가 가뭄에 동의함"}],
            "gold_attributions": [{"target": "candidate", "claim": "지원자가 가뭄에 동의함"}],
        },
        {
            "item_id": "i2",
            "split": "calib",
            "type": "no_attribution",
            "extracted_attributions": [],
            "gold_attributions": [],
        },
        {
            "item_id": "i3",
            "split": "holdout",
            "type_hint": "mention",
            "extracted_attributions": [
                {"target": "p2", "claim": "x"},
                {"target": "candidate", "claim": "y"},
            ],
            "gold_attributions": [],
        },
    ]
    a = [
        {"item_id": "i1", "labels": [{"index": 0, "verdict": "overstated"}]},
        {
            "item_id": "i3",
            "labels": [
                {"index": 0, "verdict": "supported"},
                {"index": 1, "verdict": "unsupported"},
            ],
            "missed_attributions": ["하나 빠짐"],
        },
    ]
    b = [
        {"item_id": "i1", "labels": [{"index": 0, "verdict": "overstated"}]},
        {
            "item_id": "i3",
            "labels": [{"index": 0, "verdict": "supported"}, {"index": 1, "verdict": "overstated"}],
        },
    ]
    got = ca.report(items, {"A": a, "B": b}, None)
    pair = got["pairs"]["A~B"]
    assert (pair["n"], pair["agree"], pair["rate"]) == (3, 2, 0.667)
    assert pair["matrix"]["unsupported"]["overstated"] == 1
    assert pair["binary_error_agree"]["agree"] == 3  # 오류 여부로는 모두 일치
    assert pair["disagreements"] == [
        {"item_id": "i3", "index": 1, "a": "unsupported", "b": "overstated"}
    ]
    assert pair["by_type"]["recorded:mention"] == {"n": 2, "agree": 1}
    assert got["extraction"]["recall_vs_labeler"] == {"A": 0.75, "B": 1.0}
    assert got["extraction"]["gold"] == {"n": 1, "hit": 1, "recall": 1.0, "misses": []}
    assert got["extraction"]["no_attribution_items"] == {"n": 1, "extracted_something": 0}
    calib = ca.report(items, {"A": a, "B": b}, "calib")
    assert calib["pairs"]["A~B"]["n"] == 1 and calib["pairs"]["A~B"]["kappa"] is None
    perfect = ca.confusion(
        {("x", 0): "supported", ("y", 0): "overstated"},
        {("x", 0): "supported", ("y", 0): "overstated"},
    )
    assert perfect["kappa"] == 1.0
