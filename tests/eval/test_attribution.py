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
                        "quote_span": "지원자님이 가뭄에 동의하셨죠",
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
        {
            "target": "candidate",
            "claim": "지원자가 가뭄에 동의했다",
            "span": "지원자님이 가뭄에 동의하셨죠",
            "draft": None,
        }
    ]
    assert payload["rejected_unrecoverable"] == 0
    # 거부 구절 기록이 없으면(PR 10 빌드) 주장만으로 판정하지 않고 복구 불가로 센다
    blind = [dict(m, meta={k: v for k, v in m["meta"].items() if k != "quote_span"}) for m in t]
    payload = at.payload_for(blind, 2)
    assert payload["draft_rejected_claims"] == [] and payload["rejected_unrecoverable"] == 1
    assert at.rejected_claim(blind[2]["meta"])["recoverable"] is False


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
    # 예전 형식은 pass·none이 생략돼 있어 linkage를 복원할 수 없다고 표시한다
    assert t[2]["meta"] == {"linkage": "unavailable", "quote_check": "safe"}
    assert t[2]["content"].endswith("물이시군요.")
    assert at.draft_checks(t[2]["meta"])["guard"] == "unknown"
    assert at.draft_checks(t[0]["meta"])["quote_check"] == "unknown"


def test_markdown_round_trip_keeps_multiline_phase_and_all_flags():
    events = [
        {
            "session": "attribution_problem",
            "run": 0,
            "phase": "problem",
            "kind": "phase_started",
            "posts": [
                {
                    "id": "a1",
                    "seat": "p1",
                    "content": "첫 줄\n둘째 줄: 콜론 포함",
                    "meta": {"guard": "pass", "stance_check": "pass", "quote_check": "none"},
                }
            ],
        },
        {
            "session": "attribution_problem",
            "run": 0,
            "phase": "problem",
            "kind": "evidence",
            "say": "지원자 말\n두 줄",
            "message_id": "c1",
            "posts": [
                {
                    "id": "a2",
                    "seat": "p3",
                    "content": "지원자님이 가뭄에 동의하셨죠",
                    "meta": {
                        "guard": "pass",
                        "stance_check": "regenerated",
                        "quote_check": "regenerated",
                        "quote_violation": "candidate의 기록에 없는 말: 지원자가 가뭄에 동의했다",
                        "quote_span": "지원자님이 가뭄에 동의하셨죠",
                        "model": "x",
                    },
                }
            ],
        },
    ]
    md = pe.transcript_md(events, {"p1": "강한결", "p3": "서지안"})
    back = at.transcript_from_markdown(md)
    original = at.transcript_from_events(events)
    assert [(m["sender"], m["phase"], m["order"], m["content"]) for m in back] == [
        (m["sender"], m["phase"], m["order"], m["content"]) for m in original
    ]
    assert back[0]["phase"] == "problem"  # 세션 id 접두가 아니어도 단계가 남는다
    assert at.draft_checks(back[0]["meta"]) == at.draft_checks(original[0]["meta"])
    assert at.draft_checks(back[2]["meta"]) == at.draft_checks(original[2]["meta"])
    assert at.rejected_claim(back[2]["meta"]) == {  # 거부 구절도 md를 거쳐 남는다
        "target": "candidate",
        "claim": "지원자가 가뭄에 동의했다",
        "span": "지원자님이 가뭄에 동의하셨죠",
        "draft": None,
        "recoverable": True,
    }
    # 예전 md의 여러 줄 발언(이어지는 줄)은 앞 발언에 붙는다
    legacy = "## 대화\n\n### problem\n\n- (단계 시작)\n  - 강한결: 첫 줄\n둘째 줄\n- (카드 x)\n"
    assert at.transcript_from_markdown(legacy)[0]["content"] == "첫 줄\n둘째 줄"
    assert at._phase_of("attribution_solution") == "solution" and at._phase_of("x") == ""


def test_scene_counts_keys_by_unit_and_order_not_content():
    post = {"id": None, "seat": "p1", "content": "같은 문장", "meta": {}}
    events = [
        {
            "session": "problem",
            "run": 0,
            "kind": "evidence",
            "say": "a",
            "scene": "weak_agree",
            "posts": [dict(post)],
        },
        {
            "session": "problem",
            "run": 1,
            "kind": "evidence",
            "say": "a",
            "scene": "weak_agree",
            "posts": [dict(post)],
        },
        {  # 두 번째 실행의 카드 뒤 발언 3개: 장면 뒤 4번째 발언까지 장면에 든다
            "session": "problem",
            "run": 1,
            "kind": "card",
            "card": "x",
            "posts": [dict(post), dict(post), dict(post)],
        },
    ]
    transcript = at.transcript_from_events(events)
    rows = [
        {
            "unit": list(m["unit"]),
            "order": m["order"],
            "content": m["content"],
            "attributions": [{"verdict": "overstated"}]
            if m["unit"][0] == 0
            else [{"verdict": "not_attribution"}],
        }
        for m in transcript
        if m["is_post"]
    ]
    assert pe.scene_counts(events, rows) == {
        "weak_agree": {
            "scenes": 2,
            "posts": 5,
            "attributions": 1,
            "noise": 4,  # not_attribution은 귀속 수에 넣지 않고 따로 센다
            "overstated": 1,
            "unsupported": 0,
        }
    }
    assert len(pe.following(events, 1, 3)) == 3 and len(pe.following_all(events, 1)) == 4


async def test_extract_freezes_attributions_and_passes_truncation():
    items = [
        {
            "item_id": "i1",
            "post": {"id": "p", "speaker": "p1", "phase": "problem", "order": 2, "content": "x"},
            "sources": [
                {"id": "s", "sender": "candidate", "phase": "problem", "order": 1, "content": "y"}
            ],
            "sources_truncated": True,
            "extracted_attributions": [],
        },
        {
            "item_id": "i2",
            "post": {"id": "q", "speaker": "p1", "phase": "problem", "order": 2, "content": "x"},
            "sources": [],
            "extracted_attributions": [{"attribution_id": "i2#a1", "quote": "old"}],
            "extraction": {"model": "old", "date": "2026-10-24"},
        },
        {  # 귀속 0개로 끝난 추출도 표식이 있으면 동결
            "item_id": "i3",
            "post": {"id": "r", "speaker": "p1", "phase": "problem", "order": 2, "content": "x"},
            "sources": [],
            "extracted_attributions": [],
            "extraction": {"model": "old", "date": "2026-10-24"},
        },
    ]
    seen = []

    async def ask(payload):
        seen.append(payload["sources_truncated"])
        return {
            "attributions": [
                {
                    "quote": "q",
                    "target": "candidate",
                    "claim": "c",
                    "form": "statement",
                    "verdict": "supported",
                    "source_ids": [],
                },
                {  # 화자(p1) 자신에 대한 말: 추출 경로에서도 코드 규칙으로 not_attribution
                    "quote": "me",
                    "target": "p1",
                    "claim": "self",
                    "form": "statement",
                    "verdict": "supported",
                    "source_ids": [],
                },
            ],
            "rejected_claims": [],
        }

    judge = at.AttributionJudge({"VC_JUDGE_MODEL": "t"}, ask=ask)
    labels = await at.extract_items(items, judge)
    assert seen == [True] and judge.calls == 1  # 표식이 있는 i2·i3는 건드리지 않는다
    assert items[2]["extracted_attributions"] == [] and items[0]["extraction"]["model"] == "t"
    assert items[0]["extracted_attributions"][0]["attribution_id"] == "i1#a1"
    assert items[1]["extracted_attributions"] == [{"attribution_id": "i2#a1", "quote": "old"}]
    assert labels == [
        {
            "item_id": "i1",
            "labels": [
                {"attribution_id": "i1#a1", "verdict": "supported", "reason": ""},
                {
                    "attribution_id": "i1#a2",
                    "verdict": "not_attribution",
                    "reason": at.SELF_TARGET_REASON,
                },
            ],
            "missed_attributions": [],
        }
    ]
    assert len(items[0]["extracted_attributions"]) == 2  # 추출 목록 자체는 그대로(id 유지)
    again = await at.extract_items(items, judge, force=True)
    assert judge.calls == 4 and items[1]["extracted_attributions"][0]["quote"] == "q"
    # 라벨 병합: 다시 추출한 항목은 새 행으로 바뀌고(옛 라벨 폐기), 건너뛴 항목 행은 남는다
    old = [
        {"item_id": "i2", "labels": [{"attribution_id": "i2#a1", "verdict": "overstated"}]},
        {"item_id": "i9", "labels": []},
    ]
    merged = {r["item_id"]: r for r in at.merge_labels(old, labels)}
    assert set(merged) == {"i1", "i2", "i9"} and merged["i2"] == old[0]
    merged = {r["item_id"]: r for r in at.merge_labels(old, again)}
    assert merged["i2"]["labels"][0]["verdict"] == "supported" and "i9" in merged


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
            "rejected_claims": [
                {"claim": "y", "verdict": "supported"},
                {"claim": "z", "verdict": "not_attribution"},
            ],
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
    assert got["rejected_claims"] == [
        {"claim": "y", "verdict": "supported", "reason": ""},
        {"claim": "z", "verdict": "not_attribution", "reason": ""},  # 귀속 아닌 구절의 거부 = 오탐
    ]
    assert at.rejected_claim({}) is None
    assert at.rejected_claim({"quote_violation": "형식이 다른 사유"}) == {
        "target": "",
        "claim": "형식이 다른 사유",
        "span": None,
        "draft": None,
        "recoverable": False,
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
    assert link["false_positive_regeneration"]["rejected_unrecoverable"] == 0
    blind_rows = await at.judge_posts(
        at.transcript_from_events(
            [
                dict(
                    e,
                    posts=[
                        dict(p, meta={k: v for k, v in p["meta"].items() if k != "quote_span"})
                        for p in e["posts"]
                    ],
                )
                for e in events
            ]
        ),
        judge,
    )
    fp = at.linkage(blind_rows)["false_positive_regeneration"]
    # 판정이 거부 주장을 돌려줘도(가짜 판정기) 보내지 않았으니 버리고 복구 불가로만 센다
    assert (fp["rejected_claims"], fp["rejected_unrecoverable"]) == (0, 1)
    assert link["after_regeneration"] == {"posts": 1, "final_still_error": 1, "final_clean": 0}
    assert link["linkage_unavailable_records"] == 0 and link["posts_with_linkage"] == 4
    # 복원 불가 게시물이 하나라도 있는 기록은 통째로 비율에서 뺀다(skipped만 남아 100%가 되지 않게)
    legacy = [
        {**rows[1], "unit": [9, "x"], "draft": {**rows[1]["draft"], "quote_check": "unknown"}},
        {**rows[2], "unit": [9, "x"], "draft": {**rows[2]["draft"], "quote_check": "skipped"}},
    ]
    link2 = at.linkage(rows + legacy)
    assert link2["linkage_unavailable_records"] == 1 and link2["linkage_unavailable_posts"] == 2
    assert link2["skip_rate"] == {"n": 0, "of": 2, "rate": 0.0} and link2["posts_with_linkage"] == 4
    assert "unknown" not in link2["quote_check"] and "skipped" not in link2["quote_check"]
    # 같은 (실행, 세션) 단위라도 파일이 다르면 다른 기록이다: 예전 md가 다른 파일의 정상 기록을
    # 끌어내리지 않는다
    other = [{**r, "source": "old.md", "unit": [0, "problem"]} for r in legacy]
    link3 = at.linkage(rows + other)
    assert link3["linkage_unavailable_records"] == 1 and link3["posts_with_linkage"] == 4
    assert at.record_key(rows[1]) != at.record_key(other[0])
    scored = pe.score(events, {}, {"summary": s})
    assert scored["attribution"]["summary"]["posts"] == 4
    assert scored["fallbacks"]["quote_check"] == {"absent": 2, "regenerated": 1, "none": 1}
    assert pe.scene_counts(events, rows) == {
        "weak_agree": {
            "scenes": 1,
            "posts": 2,
            "attributions": 1,
            "noise": 0,
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
            "extracted_attributions": [
                {
                    "attribution_id": "i1#a1",
                    "target": "candidate",
                    "claim": "지원자가 가뭄에 동의함",
                }
            ],
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
                {"attribution_id": "i3#a1", "target": "p2", "claim": "x"},
                {"attribution_id": "i3#a2", "target": "candidate", "claim": "y"},
            ],
            "gold_attributions": [],
        },
    ]
    a = [
        {"item_id": "i1", "labels": [{"attribution_id": "i1#a1", "verdict": "overstated"}]},
        {
            "item_id": "i3",
            "labels": [
                {"attribution_id": "i3#a1", "verdict": "supported"},
                {"attribution_id": "i3#a2", "verdict": "unsupported"},
                {"attribution_id": "i3#a9", "verdict": "supported"},  # 항목에 없는 id는 버린다
            ],
            "missed_attributions": ["하나 빠짐"],
        },
    ]
    b = [
        {"item_id": "i1", "labels": [{"attribution_id": "i1#a1", "verdict": "overstated"}]},
        {
            "item_id": "i3",
            "labels": [
                {"attribution_id": "i3#a1", "verdict": "supported"},
                {"attribution_id": "i3#a2", "verdict": "overstated"},
            ],
        },
    ]
    got = ca.report(items, {"A": a, "B": b}, None)
    pair = got["pairs"]["A~B"]
    assert (pair["n"], pair["agree"], pair["rate"]) == (3, 2, 0.667)
    assert pair["matrix"]["unsupported"]["overstated"] == 1
    assert pair["binary_error_agree"]["agree"] == 3  # 오류 여부로는 모두 일치
    assert pair["disagreements"] == [
        {"item_id": "i3", "attribution_id": "i3#a2", "a": "unsupported", "b": "overstated"}
    ]
    assert pair["by_type"]["recorded:mention"] == {"n": 2, "agree": 1}
    recall = got["extraction"]["recall_vs_labeler"]  # A·B는 i1·i3만 봤다(i2 제외)
    assert recall["A"]["reviewed_items"] == 2 and recall["A"]["coverage"] == 0.667
    assert (recall["A"]["extracted_in_reviewed"], recall["A"]["missed"]) == (3, 1)
    assert recall["A"]["recall"] == 0.75 and "warning" in recall["A"]
    assert recall["B"]["recall"] == 1.0 and "warning" in recall["B"]
    assert got["extraction"]["gold"] == {"n": 1, "hit": 1, "recall": 1.0, "misses": []}
    assert got["extraction"]["no_attribution_items"] == {"n": 1, "extracted_something": 0}
    calib = ca.report(items, {"A": a, "B": b}, "calib")
    assert calib["pairs"]["A~B"]["n"] == 1 and calib["pairs"]["A~B"]["kappa"] is None
    perfect = ca.confusion(
        {("x", 0): "supported", ("y", 0): "overstated"},
        {("x", 0): "supported", ("y", 0): "overstated"},
    )
    assert perfect["kappa"] == 1.0


async def test_judge_files_keeps_same_named_files_apart(tmp_path):
    data = {
        "session": [{"id": "s", "display_name": ""}],
        "messages": [
            {
                "id": "m1",
                "sender": "candidate",
                "phase": "keyword",
                "content": "물",
                "created_at": "2026-10-23T00:00:01+00:00",
                "meta": {},
            },
            {
                "id": "m2",
                "sender": "p1",
                "phase": "keyword",
                "content": "불",
                "created_at": "2026-10-23T00:00:02+00:00",
                "meta": {},
            },
        ],
    }
    paths = []
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        paths.append(tmp_path / name / "session.json")
        paths[-1].write_text(json.dumps(data), "utf-8")

    async def ask(payload):
        return {"attributions": [], "rejected_claims": []}

    got = await at._judge_files(paths, at.AttributionJudge({"VC_JUDGE_MODEL": "t"}, ask=ask))
    assert got["files"] == ["a/session.json", "b/session.json"] == list(got["per_file"])
    assert got["combined"]["posts"] == 2 and [r["source"] for r in got["rows"]] == got["files"]
    assert at.source_labels([paths[0], paths[0]]) == ["session.json", "session.json#2"]


async def test_relabel_keeps_ids_and_not_attribution_is_noise_not_error():
    items = [
        {
            "item_id": "i1",
            "post": {"id": "p", "speaker": "p1", "phase": "problem", "order": 2, "content": "x"},
            "sources": [
                {"id": "s", "sender": "candidate", "phase": "problem", "order": 1, "content": "y"}
            ],
            "extracted_attributions": [
                {"attribution_id": "i1#a1", "quote": "q1", "target": "candidate", "claim": "c1"},
                {"attribution_id": "i1#a2", "quote": "q2", "target": "p1", "claim": "c2"},
            ],
        },
        {"item_id": "i2", "post": {}, "sources": [], "extracted_attributions": []},
    ]
    seen = []

    async def ask(payload):
        seen.append((payload["_prompt"], [a["attribution_id"] for a in payload["attributions"]]))
        return {
            "labels": [{"attribution_id": "i1#a9", "verdict": "supported", "reason": "없는 id"}]
        }

    judge = at.AttributionJudge({"VC_JUDGE_MODEL": "t"}, ask=ask)
    assert "[귀속의 정의]" in judge.system("relabel") and "{criteria}" not in judge.system(
        "relabel"
    )
    rows = await at.relabel_items(items, judge)
    # target이 화자 자신(i1#a2)은 호출 없이 not_attribution, 귀속 0개 항목(i2)은 호출 없음
    assert seen == [("relabel", ["i1#a1"])] and judge.calls == 1
    assert rows[0]["labels"] == [  # 목록의 id만·원래 순서, 빠진 id는 undeterminable
        {"attribution_id": "i1#a1", "verdict": "undeterminable", "owner": "unclear", "reason": ""},
        {
            "attribution_id": "i1#a2",
            "verdict": "not_attribution",
            "owner": "speaker",
            "reason": "화자 자신에 대한 말(target == speaker)",
        },
    ]
    assert rows[1] == {"item_id": "i2", "labels": [], "missed_attributions": []}

    def row(order, seat, verdicts):
        return {
            "run": 0,
            "unit": [0, "s"],
            "order": order,
            "seat": seat,
            "phase": "problem",
            "content": "",
            "sources_truncated": False,
            "draft": at.draft_checks({}),
            "rejected_claims": [],
            "attributions": [
                {
                    "quote": "q",
                    "target": "candidate",
                    "claim": "",
                    "form": "statement",
                    "verdict": v,
                    "source_ids": [],
                    "reason": "",
                }
                for v in verdicts
            ],
        }

    # 요약: not_attribution은 귀속 수·비율 밖이고 extraction_noise로 따로 센다
    s = at.summarize(
        [row(1, "p1", ["not_attribution", "overstated"]), row(2, "p3", ["not_attribution"])]
    )
    assert (s["attributions"], s["extraction_noise"]) == (1, 2)
    assert s["per_attribution"]["overstated"] == {"n": 1, "rate": 1.0}
    assert s["per_post"]["with_attribution"] == {"n": 1, "rate": 0.5}
    assert s["frequency"]["histogram"] == {"0": 1, "1": 1, "2": 0, "3+": 0}
    parsed = at.parse_verdict(
        {
            "attributions": [
                {
                    "quote": "q",
                    "target": "p1",
                    "claim": "",
                    "form": "statement",
                    "verdict": "not_attribution",
                }
            ]
        }
    )
    assert parsed["attributions"][0]["verdict"] == "not_attribution"


def test_agreement_normalizes_labeler_not_attribution_reason():
    items = [
        {
            "item_id": "i1",
            "split": "calib",
            "type": "normal",
            "extracted_attributions": [
                {"attribution_id": "i1#a1", "target": "p1", "claim": "x"},
                {"attribution_id": "i1#a2", "target": "candidate", "claim": "y"},
            ],
            "gold_attributions": [],
        }
    ]
    human = [
        {
            "item_id": "i1",
            "labels": [
                {
                    "attribution_id": "i1#a1",
                    "verdict": "supported",
                    "reason": "귀속 아님: 자기 의견",
                },
                {"attribution_id": "i1#a2", "verdict": "supported", "reason": "원문과 같다"},
            ],
        }
    ]
    judge = [
        {
            "item_id": "i1",
            "labels": [
                {"attribution_id": "i1#a1", "verdict": "not_attribution", "reason": ""},
                {"attribution_id": "i1#a2", "verdict": "supported", "reason": ""},
            ],
        }
    ]
    got = ca.report(items, {"A": human, "judge": judge}, None)
    assert got["pairs"]["A~judge"]["agree"] == 2 and got["pairs"]["A~judge"]["kappa"] == 1.0
    assert got["distribution"]["A"] == {"not_attribution": 1, "supported": 1}
    assert got["pairs_without_not_attribution_class"]["A~judge"]["agree"] == 2
    recall = got["extraction"]["recall_vs_labeler"]["A"]  # "귀속 아님"은 추출 성공이 아니라 noise
    assert (recall["extracted_in_reviewed"], recall["noise_in_reviewed"]) == (1, 1)
    assert recall["recall"] == 1.0
    old_judge = [  # v1 판정은 그런 구절을 unsupported로 냈다
        {
            "item_id": "i1",
            "labels": [
                {"attribution_id": "i1#a1", "verdict": "unsupported", "reason": ""},
                {"attribution_id": "i1#a2", "verdict": "supported", "reason": ""},
            ],
        }
    ]
    got = ca.report(items, {"A": human, "judge": old_judge}, None)
    assert got["pairs"]["A~judge"]["matrix"]["not_attribution"]["unsupported"] == 1
    assert got["pairs_without_not_attribution_class"]["A~judge"]["agree"] == 1
