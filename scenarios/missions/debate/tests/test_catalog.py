"""토론 미션 카탈로그 정합성 검사.

실행: uv run --project services/api pytest -q scenarios/missions/debate/tests
- 모든 YAML이 읽힌다
- 참조 무결성: 카드 담당 → 페르소나 자리, 안 → 키워드, 자료·프롬프트·아바타 파일 존재, 픽스처의 id
- 값이 정본 문서(docs/product/scenario.md·rubric.md) 표와 같다(5요소 19차원 포함)
"""

import importlib.util
import re
from fractions import Fraction
from pathlib import Path

import pytest
import yaml
from rubric_doc import parse_rubric

MISSION = Path(__file__).resolve().parents[1]
REPO = MISSION.parents[2]
SCENARIO_DOC = REPO / "docs" / "product" / "scenario.md"

ELEMENTS = ["persuasion", "listening", "inclusion", "flexibility", "honesty"]
SESSION_TYPES = ["stubborn", "quiet", "critic"]
ALL_TYPES = SESSION_TYPES + ["agreeable", "divergent"]
TYPE_WORDS = ALL_TYPES + ["고집", "소극", "비판", "동조", "발산"]
STAGES = ["keyword", "problem", "solution", "proposal"]


def load(rel: str):
    return yaml.safe_load((MISSION / rel).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mission():
    return load("mission.yaml")


@pytest.fixture(scope="module")
def keywords():
    return load("keywords.yaml")


@pytest.fixture(scope="module")
def personas():
    return load("personas.yaml")


@pytest.fixture(scope="module")
def cards():
    return load("cards.yaml")


@pytest.fixture(scope="module")
def rubric():
    return load("rubric.yaml")


def scale(offset_min: float, minutes: int) -> float:
    """docs/product/scenario.md 1.3: L 비율 환산, 0.01분 반올림, 카드 최소 0.34분."""
    return max(round(offset_min * minutes / 60, 2), 0.34)


# ---------- 로드 ----------


def test_all_yaml_load():
    files = sorted(MISSION.rglob("*.yaml"))
    assert len(files) >= 12
    for f in files:
        assert yaml.safe_load(f.read_text(encoding="utf-8")) is not None, f


# ---------- mission.yaml ----------


def test_stages_match_doc(mission):
    stages = mission["stages"]
    assert [s["stage_id"] for s in stages] == STAGES
    assert [s["budget_min"] for s in stages] == [10, 18, 18, 14]
    assert [s["deadline_min"] for s in stages] == [10, 28, 46, 60]
    assert [s["slot"] for s in stages] == ["keyword", "problem", "solution", None]
    for s in stages:
        assert Fraction(s["deadline_fraction"]) * 60 == s["deadline_min"]
    # 15분 압축 마감(docs/product/scenario.md 1.3 표)
    assert [float(Fraction(s["deadline_fraction"]) * 15) for s in stages] == [2.5, 7, 11.5, 15]
    assert mission["session_minutes_options"] == [60, 15]
    assert mission["office_clock"] == {"start": "09:00", "end": "10:00"}
    assert mission["slot_states"] == ["empty", "confirmed"]
    assert mission["card_offset_floor_min"] == 0.34


def test_rules_match_doc(mission, keywords):
    assert mission["id"] == "debate"
    assert mission["default_keyword"] == "water"
    assert mission["default_keyword"] in {k["key"] for k in keywords["keywords"]}
    assert mission["consensus_fill"]["min_supporters"] == 3
    assert mission["consensus_fill"]["candidate_must_support"] is True
    assert mission["consensus_fill"]["wait_min"] == 0
    assert mission["consensus_fill"]["filler_priority"] == ["critic", "quiet", "stubborn"]
    assert mission["timeout"]["selection"] == [
        "candidate_support",
        "most_support",
        "earliest",
        "default",
    ]
    assert "announcer_priority" not in mission["timeout"]
    assert Fraction(mission["timeout"]["warning_before_fraction"]) * 60 == 2
    assert mission["timeout"]["stage_default"] == {
        "keyword": "default_keyword",
        "problem": "locked_lead_or_p1",
        "solution": "s1",
    }
    si = mission["silence_invite"]
    assert si["inviter"] == "critic"
    assert si["max_per_stage"] == 1
    assert si["stages"] == ["keyword", "problem", "solution"]
    assert Fraction(si["idle_fraction"]) * 60 == 3
    assert Fraction(si["idle_fraction"]) * 15 * 60 == 45  # 15분 기준 45초
    assert si["no_opinion_after_budget_ratio"] == 0.5
    assert mission["lead_option"]["order"] == ["p1", "p2", "p3"]
    assert mission["lead_option"]["lock_card"] == "flexibility_majority"


def test_proposal_template(mission):
    fields = [f["field"] for f in mission["proposal_template"]]
    assert fields == [
        "keyword",
        "problem",
        "solution",
        "rationale",
        "expected_effect",
        "idea_credit",
    ]
    credit = mission["proposal_template"][-1]
    assert credit["hint"] == "이 기획에 반영된 의견과 그 의견을 낸 사람"
    assert all(f["hint"] is None for f in mission["proposal_template"][:-1])


def test_elements_match_rubric(mission, rubric):
    assert [e["id"] for e in mission["elements"]] == ELEMENTS
    names = {e["element"]: e["name"] for e in rubric["elements"]}
    for e in mission["elements"]:
        assert e["name"] == names[e["id"]]
        assert e["meaning"] and e["how_we_see"]


def test_debrief_intro(mission):
    assert mission["debrief"]["intro_notice"] == (
        "같은 채용 건의 모든 지원자가 같은 장면을 겪었고, 정답 없이 반응을 봅니다."
    )


# ---------- keywords.yaml ----------


def _doc_keyword_rows() -> dict[str, dict]:
    """docs/product/scenario.md 2.4 표에서 키워드 행을 읽는다."""
    rows = {}
    pat = re.compile(
        r"^\| `(?P<key>[a-z]+)` \| (?P<label>[^|]+?) \| (?P<problems>[^|]+?) "
        r"\| (?P<solutions>[^|]+?) \| (?P<materials>[^|]+?) \|$"
    )
    for line in SCENARIO_DOC.read_text(encoding="utf-8").splitlines():
        m = pat.match(line)
        if m and m["key"] in {"water", "fire", "tree", "wind"}:
            probs = [re.sub(r"^P\d ", "", p.strip()) for p in m["problems"].split(" · ")]
            sols = [re.sub(r"^S\d ", "", s.strip()) for s in m["solutions"].split(" · ")]
            mats = [t.strip() for t in m["materials"].split(" · ")]
            rows[m["key"]] = {
                "label": m["label"],
                "problems": probs,
                "solutions": sols,
                "materials": mats,
            }
    return rows


def test_keywords_match_doc(keywords):
    doc = _doc_keyword_rows()
    assert list(doc) == ["water", "fire", "tree", "wind"]
    kws = keywords["keywords"]
    assert [k["key"] for k in kws] == list(doc)
    for k in kws:
        d = doc[k["key"]]
        assert k["label"] == d["label"]
        assert [p["label"] for p in k["problems"]] == d["problems"]
        assert [s["label"] for s in k["solutions"]] == d["solutions"]
        assert [m["title"] for m in k["materials"]] == d["materials"]


def test_option_keys_and_directions(keywords):
    for k in keywords["keywords"]:
        key = k["key"]
        assert [p["key"] for p in k["problems"]] == [f"{key}_p{i}" for i in (1, 2, 3)]
        assert [s["key"] for s in k["solutions"]] == [f"{key}_s{i}" for i in (1, 2, 3)]
        assert [s["direction"] for s in k["solutions"]] == ["data", "participation", "infra"]


def test_materials_exist_and_unique(keywords):
    ids = []
    for k in keywords["keywords"]:
        assert len(k["materials"]) == 4
        for m in k["materials"]:
            assert m["material_id"].startswith(k["key"] + "_")
            assert m["file"].startswith(k["key"] + "/")
            path = MISSION / "materials" / m["file"]
            assert path.is_file(), path
            assert path.read_text(encoding="utf-8").startswith(f"# {m['title']}\n")
            ids.append(m["material_id"])
    assert len(ids) == len(set(ids)) == 16
    on_disk = {
        p.relative_to(MISSION / "materials").as_posix()
        for p in (MISSION / "materials").rglob("*.md")
    }
    listed = {m["file"] for k in keywords["keywords"] for m in k["materials"]}
    assert on_disk == listed


# ---------- personas.yaml ----------


def test_session_personas_match_doc(personas):
    s = personas["session"]
    assert [(p["seat"], p["id"], p["name"]) for p in s] == [
        ("p1", "stubborn", "강한결"),
        ("p2", "quiet", "윤하람"),
        ("p3", "critic", "서지안"),
    ]
    assert personas["seat_order"] == ["p1", "p2", "p3"]
    by = {p["id"]: p for p in s}
    # 2.3 단계별 고정 입장
    assert {t: by[t]["stances"]["keyword"]["push"] for t in SESSION_TYPES} == {
        "stubborn": "fire",
        "quiet": "tree",
        "critic": "wind",
    }
    assert {t: by[t]["stances"]["problem"]["after_card"] for t in SESSION_TYPES} == {
        "stubborn": "lead",
        "quiet": "last_non_lead",
        "critic": "lead",
    }
    assert all(by[t]["stances"]["problem"]["before_card"] == "none" for t in SESSION_TYPES)
    assert {t: by[t]["stances"]["solution"]["push"] for t in SESSION_TYPES} == {
        "stubborn": "s1",
        "quiet": "s3",
        "critic": "s2",
    }
    # 설득 가능 규칙
    assert by["stubborn"]["persuadable"] is False and by["quiet"]["persuadable"] is False
    rule = by["critic"]["persuadable"]
    assert rule["count"] == 2 and rule["per_stage_max"] == 1 and rule["confidence"] == "high"
    assert rule["stages"] == ["keyword", "problem", "solution"]
    assert by["quiet"]["speaks_only_when_invited"] is True
    assert by["critic"]["silence_inviter"] is True


def test_persona_keyword_stances_are_keywords(personas, keywords):
    keys = {k["key"] for k in keywords["keywords"]}
    for p in personas["session"]:
        assert p["stances"]["keyword"]["push"] in keys
        assert p["stances"]["solution"]["push"] in keywords["solution_directions"]


def test_persona_files_and_avatars(personas):
    for p in personas["session"]:
        assert (MISSION / p["prompt"]).is_file()
        assert (MISSION / p["safe_phrase"]).is_file()
        avatar = MISSION / personas["avatar_dir"] / p["avatar"]
        assert avatar.is_file(), avatar
        assert avatar.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert re.fullmatch(r"avatar-\d{2}\.png", p["avatar"])
    names = [p.name.lower() for p in (MISSION / "assets" / "avatars").iterdir()]
    for n in names:
        assert not any(w in n for w in TYPE_WORDS), n


def test_extension_pool_not_assembled(personas):
    pool = personas["extension_pool"]
    assert pool["used_in_assembly"] is False
    assert [(p["id"], p["name"]) for p in pool["personas"]] == [
        ("agreeable", "이다온"),
        ("divergent", "최해솔"),
    ]
    assert not {p["id"] for p in pool["personas"]} & {p["id"] for p in personas["session"]}


# ---------- cards.yaml ----------

DOC_CARDS = [
    ("persuasion_keyword", "persuasion", "keyword", 3, 0.75),
    ("flexibility_majority", "flexibility", "problem", 3, 0.75),
    ("listening_pressure", "listening", "problem", 3, 0.75),
    ("inclusion_silence", "inclusion", "solution", 2, 0.5),
    ("persuasion_solution", "persuasion", "solution", 5, 1.25),
    ("honesty_credit", "honesty", "proposal", None, None),
]


def test_cards_match_doc(cards):
    got = [(c["card_id"], c["element"], c["stage_id"], c["offset_min"]) for c in cards["cards"]]
    assert got == [d[:4] for d in DOC_CARDS]
    for c, d in zip(cards["cards"], DOC_CARDS, strict=True):
        if c["offset_min"] is None:
            assert c["offset_fraction"] is None
            continue
        assert Fraction(c["offset_fraction"]) * 60 == c["offset_min"]
        assert scale(c["offset_min"], 15) == d[4]
        assert scale(c["offset_min"], 15) >= 0.34


def test_card_actors_resolve_to_seats(cards, personas):
    seat_of = {p["id"]: p["seat"] for p in personas["session"]}
    expected = {
        "persuasion_keyword": (["critic", "stubborn"], "priority"),
        "flexibility_majority": (["stubborn", "critic"], "joint"),
        "listening_pressure": ("stubborn", "single"),
        "inclusion_silence": ("quiet", "single"),
        "persuasion_solution": (["critic", "stubborn"], "conditional"),
        "honesty_credit": (None, "none"),
    }
    for c in cards["cards"]:
        assert (c["actor"], c["actor_mode"]) == expected[c["card_id"]]
        actors = c["actor"] if isinstance(c["actor"], list) else [c["actor"]] if c["actor"] else []
        for a in actors:
            assert a in seat_of, (c["card_id"], a)
        if c["actor_mode"] == "conditional":
            r = c["actor_rule"]
            assert (r["default"], r["then"]) == ("critic", "stubborn")
            assert r["stance_unchanged"] is True
    by = {c["card_id"]: c for c in cards["cards"]}
    assert by["listening_pressure"]["max_pressure"] == 3
    assert (
        by["inclusion_silence"]["fixed_line"]
        == "저… 하나 생각한 게 있긴 한데… 아니에요, 먼저 얘기하세요."
    )


def test_card_texts_and_windows(cards, rubric):
    elements = {e["element"] for e in rubric["elements"]}
    for c in cards["cards"]:
        assert c["element"] in elements
        for f in ("name", "content", "preempt_ban", "debrief", "report_template"):
            assert c[f], (c["card_id"], f)
        w = c["window"]
        assert w["start"] in {"stage_start", "card_message"}
        end = w["end"]
        assert end == "submit" or (end.startswith("stage_end:") and end.split(":")[1] in STAGES)
    assert [e["event_id"] for e in cards["flow_events"]] == [
        "stage_start",
        "time_warning",
        "consensus_fill",
        "timeout_fill",
        "silence_invite",
        "submit",
    ]
    assert cards["graph_stimuli"] == ["message", "phase_started", "idle", "card_due"]


# ---------- rubric.yaml ----------


def test_rubric_19_dimensions(rubric):
    els = rubric["elements"]
    assert [e["element"] for e in els] == ELEMENTS
    assert [len(e["dimensions"]) for e in els] == [4, 4, 4, 4, 3]
    ids = [d["dimension_id"] for e in els for d in e["dimensions"]]
    assert len(ids) == len(set(ids)) == 19
    prefix = {
        "persuasion": "pers_",
        "listening": "listen_",
        "inclusion": "incl_",
        "flexibility": "flex_",
        "honesty": "hon_",
    }
    for e in els:
        for d in e["dimensions"]:
            assert d["dimension_id"].startswith(prefix[e["element"]])
            assert [lv["level"] for lv in d["levels"]] == [5, 4, 3, 2, 1]
            assert all(lv["anchor"] and lv["example"] and lv["reviewer_hint"] for lv in d["levels"])


def test_rubric_matches_doc_verbatim(rubric):
    doc = parse_rubric()
    assert [e["element"] for e in doc["elements"]] == ELEMENTS
    for de, ye in zip(doc["elements"], rubric["elements"], strict=True):
        assert ye["element"] == de["element"]
        assert ye["name"] == de["name"]
        assert ye["definition"] == de["definition"]
        assert ye.get("reviewer_notice") == de.get("reviewer_notice")
        assert [d["dimension_id"] for d in ye["dimensions"]] == [
            d["dimension_id"] for d in de["dimensions"]
        ]
        for dd, yd in zip(de["dimensions"], ye["dimensions"], strict=True):
            assert yd["name"] == dd["name"]
            assert yd["definition"] == dd["definition"]
            assert yd["positive_signals"] == dd["positive"]
            assert yd["negative_signals"] == dd["negative"]
            assert yd["no_record"] == dd["no_record"]
            for lv in yd["levels"]:
                src = dd["levels"][lv["level"]]
                assert lv["anchor"] == src["anchor"], (yd["dimension_id"], lv["level"])
                assert lv["example"] == src["example"], (yd["dimension_id"], lv["level"])
                assert lv["reviewer_hint"] == dd["hints"][lv["level"]], (
                    yd["dimension_id"],
                    lv["level"],
                )
    states = {s["state"]: s["label"] for s in rubric["record_states"]}
    assert states == {"no_scene": "장면 없음", "no_response": "반응 없음", "failed": "판단 보류"}
    assert [(r["code"], states[r["state"]], r["label"]) for r in rubric["no_record_reasons"]] == [
        (r["code"], r["state"], r["label"]) for r in doc["no_record_reasons"]
    ]
    # 선결정·판단 보류는 워커만 쓴다(rubric.md 5장)
    assert {r["code"] for r in rubric["no_record_reasons"] if r["worker_only"]} == {
        "evaluate_failed",
        "preempted",
    }
    prio = sorted(rubric["no_record_reasons"], key=lambda r: r["priority"])
    assert [r["code"] for r in prio] == [
        "evaluate_failed",
        "preempted",
        "event_not_fired",
        "auto_submitted_blank",
        "not_applicable",
        "no_activity",
    ]


def test_rubric_core_cards_exist(rubric, cards):
    card_el = {c["card_id"]: c["element"] for c in cards["cards"]}
    for e in rubric["elements"]:
        for d in e["dimensions"]:
            for cid in d["core_cards"]:
                assert card_el[cid] == e["element"], (d["dimension_id"], cid)


def test_rubric_scene_data(rubric, cards):
    """결정 전후 구분과 보조 장면(평가 워커의 장면 계산 입력)."""
    card_ids = {c["card_id"] for c in cards["cards"]}
    phases = {"keyword", "problem", "solution", "proposal"}
    timed = {}
    for e in rubric["elements"]:
        for d in e["dimensions"]:
            if "decision_timing" in d:
                assert d["decision_timing"] in ("before", "after") and d["core_cards"]
                timed[d["dimension_id"]] = d["decision_timing"]
            ids = [a["id"] for a in d["aux_scenes"]]
            assert len(ids) == len(set(ids)) and "core" not in ids, d["dimension_id"]
            for scene in d["aux_scenes"]:
                assert scene["label"] and (scene.get("phases") or scene.get("cards"))
                assert set(scene.get("phases") or []) <= phases
                assert set(scene.get("cards") or []) <= card_ids
                assert all(isinstance(f, str) and f for f in scene.get("fields") or [])
    assert timed == {
        "flex_openness": "before",
        "flex_acceptance": "after",
        "flex_forward": "after",
    }


# ---------- prompts ----------

PROMPT_FILES = [
    "prompts/persona/common_rules.md",
    "prompts/manager/turn_manager.md",
    "prompts/intent/intent.md",
    "prompts/persona/stance_check.md",
    "prompts/assistant/assistant.md",
    "prompts/evaluator/common.md",
    "prompts/persona/stance_fallback.yaml",
    "prompts/shared/guard_patterns.yaml",
    "prompts/shared/fixed_phrases.yaml",
]


def test_prompt_files_exist():
    for rel in PROMPT_FILES:
        assert (MISSION / rel).is_file(), rel
    for t in SESSION_TYPES:
        assert (MISSION / f"prompts/persona/persona_{t}.md").is_file()
        assert (MISSION / f"prompts/persona/safe_{t}.md").read_text(encoding="utf-8").strip()
    for el in ELEMENTS:  # prompt_versions = common + 요소 5개
        assert (MISSION / f"prompts/evaluator/{el}.md").is_file()


def test_persona_prompts_do_not_name_types():
    for t in SESSION_TYPES:
        text = (MISSION / f"prompts/persona/persona_{t}.md").read_text(encoding="utf-8")
        assert not re.search(r"고집형|소극형|비판형|동조형|발산형|stubborn|quiet|critic", text), t


def test_fixed_phrases_match_doc():
    fp = load("prompts/shared/fixed_phrases.yaml")
    assert fp["consensus_fill"].replace("{option}", "X") == "그럼 X로 정리할게요."
    # 시간 초과 안내는 적용 규칙마다 한 문장이고 그 규칙을 말한다(B4)
    timeout_fill = fp["timeout_fill"]
    assert list(timeout_fill) == load("mission.yaml")["timeout"]["selection"]
    assert all("{option}로" in t for t in timeout_fill.values())
    assert "{candidate}님이 지지한" in timeout_fill["candidate_support"]
    assert "지지가 가장 많은" in timeout_fill["most_support"]
    assert "먼저 나온" in timeout_fill["earliest"]
    assert "나온 안이 없어" in timeout_fill["default"]
    assert fp["silence_invite"].replace("{candidate}", "OO") == "OO님은 어떻게 생각하세요?"
    assert fp["time_warning"].replace("{remaining}", "2분") == "이 단계 2분 남았어요."
    assert fp["invalid_keyword"] == "이번엔 네 개 중에서 골라야 해요."
    assert fp["assistant_locked"] == "키워드가 정해지면 자료를 찾을 수 있어요."
    assert set(fp["stage_start"]) == set(STAGES)
    assert set(fp["notices"]) >= {"evaluation", "staging", "future", "identity"}


def test_stance_fallback_covers_seats(personas):
    sf = load("prompts/persona/stance_fallback.yaml")
    persuadable = {p["id"]: bool(p.get("persuadable")) for p in personas["session"]}
    for t in SESSION_TYPES:
        base = {"keyword", "problem_before_card", "problem", "solution", "proposal"}
        assert set(sf[t]) - {"moved"} == base
        # 이동 직후 문구는 설득 가능 자리에만 둔다
        assert ("moved" in sf[t]) == persuadable[t]
        for key, v in sf[t].items():
            assert v["stance"] in {"support", "none"}
            # 같은 문구 연속 게시를 피할 다른 문장이 하나 이상 있고, 입장 표시 규칙은 같다
            assert v.get("alternatives"), (t, key)
            for text in (v["text"], *v["alternatives"]):
                assert ("{option}" in text) == (v["stance"] == "support"), (t, key, text)


@pytest.fixture(scope="module")
def guard():
    g = load("prompts/shared/guard_patterns.yaml")
    groups = {
        grp["id"]: [re.compile(p, re.IGNORECASE) for p in grp["patterns"]] for grp in g["groups"]
    }
    groups["__allow_exact__"] = set(g["allow_exact"])
    return g, groups


def _hits(groups, text):
    if text.strip() in groups["__allow_exact__"]:
        return set()
    return {
        gid
        for gid, pats in groups.items()
        if gid != "__allow_exact__" and any(p.search(text) for p in pats)
    }


def test_guard_patterns_examples(guard):
    g, groups = guard
    notices = set(load("prompts/shared/fixed_phrases.yaml")["notices"])
    assert g["max_chars"] == 400
    for grp in g["groups"]:
        assert grp["notice"] in notices
        for text in grp["examples"]["hit"]:
            assert grp["id"] in _hits(groups, text), (grp["id"], text)
        for text in grp["examples"]["miss"]:
            assert not _hits(groups, text), (grp["id"], text, _hits(groups, text))
    ev = [re.compile(p, re.IGNORECASE) for p in g["evaluator_forbidden"]["patterns"]]
    for text in g["evaluator_forbidden"]["examples"]["hit"]:
        assert any(p.search(text) for p in ev), text
    for text in g["evaluator_forbidden"]["examples"]["miss"]:
        assert not any(p.search(text) for p in ev), text


def test_guard_allows_exact_evaluation_notice(guard):
    """정해진 평가 안내문은 정확히 일치할 때만 통과하고, 변형은 여전히 verdict에 걸린다."""
    g, groups = guard
    notice = load("prompts/shared/fixed_phrases.yaml")["notices"]["evaluation"]
    rules = (MISSION / "prompts/persona/common_rules.md").read_text(encoding="utf-8")
    assert f'평가 방식·기준을 물으면: "{notice}"' in rules
    assert g["allow_exact"] == [notice]
    assert _hits(groups, notice) == set()
    assert _hits(groups, f"  {notice}\n") == set()
    assert "verdict" in _hits(groups, notice + " 평가 기준은 경청이에요.")
    assert "verdict" in _hits(groups, "평가 방식은 경청을 보는 거예요.")


def test_guard_does_not_block_fixed_lines(guard, cards):
    _, groups = guard
    by = {c["card_id"]: c for c in cards["cards"]}
    sf = load("prompts/persona/stance_fallback.yaml")
    lines = [by["inclusion_silence"]["fixed_line"]]
    for t in SESSION_TYPES:
        safe = (MISSION / f"prompts/persona/safe_{t}.md").read_text(encoding="utf-8")
        safe_lines = [line.strip() for line in safe.splitlines() if line.strip()]
        assert len(safe_lines) >= 2, t  # 연속 게시를 피할 다른 줄
        lines += safe_lines
    lines += [
        text.replace("{option}", "물")
        for t in SESSION_TYPES
        for v in sf[t].values()
        for text in (v["text"], *(v.get("alternatives") or []))
    ]
    for text in lines:
        assert not _hits(groups, text), (text, _hits(groups, text))


# ---------- fixtures ----------


def test_fixtures_reference_valid_ids(guard, cards, personas):
    _, groups = guard
    seats = {p["seat"] for p in personas["session"]}
    types = {p["id"] for p in personas["session"]}
    card_ids = {c["card_id"] for c in cards["cards"]}
    notices = set(load("prompts/shared/fixed_phrases.yaml")["notices"])
    sf = load("prompts/persona/stance_fallback.yaml")

    leak = load("prompts/fixtures/leak.yaml")["cases"]
    assert len(leak) >= 5
    for c in leak:
        assert c["expected_notice"] in notices
        assert c.get("target_seat", "p1") in seats
        for p in c["must_not_regex"]:
            re.compile(p)

    pre = load("prompts/fixtures/preemption.yaml")["cases"]
    assert {c["card"] for c in pre} == card_ids  # 카드마다 1건
    for c in pre:
        assert c["speaker"] in types and c["phase"] in STAGES

    for c in load("prompts/fixtures/verdict.yaml")["cases"]:
        assert c["expect_guard_group"] in groups
        assert c["expect_guard_group"] in _hits(groups, c["bad_output"]), c["id"]

    for c in load("prompts/fixtures/stance_offplan.yaml")["cases"]:
        assert c["seat"] in seats and c["persona"] in types
        t, key = c["expected_fallback"].split(".")
        assert t == c["persona"] and key in sf[t]


# ---------- catalog_version ----------


def test_catalog_version_hashable():
    spec = importlib.util.spec_from_file_location(
        "scenario_version", REPO / "scenarios" / "version.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    v = mod.catalog_version("debate")
    assert re.fullmatch(r"[0-9a-f]{16}", v)
    assert v == mod.catalog_version("debate")  # 같은 내용이면 같은 해시


# ---------- 프롬프트 키 ----------

# 채용 건 복사본(positions.scenario.prompts)과 실행 코드가 쓰는 키.
# 역할 폴더로 옮겨도 바뀌면 안 된다
PROMPT_KEYS = {
    "turn_manager.md",
    "common_rules.md",
    "stance_fallback.yaml",
    *(f"persona_{t}.md" for t in ("stubborn", "quiet", "critic")),
    *(f"safe_{t}.md" for t in ("stubborn", "quiet", "critic")),
    "stance_check.md",
    "intent.md",
    "assistant.md",
    "evaluator/common.md",
    *(f"evaluator/{e}.md" for e in ELEMENTS),
    "fixed_phrases.yaml",
    "guard_patterns.yaml",
}


def test_prompt_keys_stay_stable_across_role_folders():
    spec = importlib.util.spec_from_file_location(
        "scenario_version", REPO / "scenarios" / "version.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    copied = mod.prompt_files(MISSION / "prompts", copied_only=True)
    assert set(copied) == PROMPT_KEYS
    every = mod.prompt_files(MISSION / "prompts")
    assert set(every) - set(copied) == {
        f"fixtures/{n}.yaml" for n in ("leak", "preemption", "stance_offplan", "verdict")
    }


# ---------- 의도 분류 프롬프트 ↔ 평가셋 분리 ----------


def _plain(text: str) -> str:
    """문장부호·공백 차이를 없앤 비교용 문자열."""
    return re.sub(r"[^\w]", "", text)


def test_intent_prompt_examples_do_not_leak_eval_sentences_or_catalog_labels(keywords):
    """intent.md 예시에 평가셋 문장이나 카탈로그 안 라벨이 들어가면 평가 점수가 부풀므로 막는다.

    예시는 가상의 안(placeholder)으로 쓴다. 한 글자 키워드 라벨(물·불 등)은 보통 낱말에 들어가
    부분 문자열 검사가 뜻이 없으므로 두 글자 이상 라벨만 본다.
    """
    prompt = _plain((MISSION / "prompts/intent/intent.md").read_text(encoding="utf-8"))
    eval_dir = REPO / "tests" / "eval"
    sentences: list[tuple[str, str]] = []
    for name in ("intent_cases.yaml", "intent_holdout_cases.yaml"):
        data = yaml.safe_load((eval_dir / name).read_text(encoding="utf-8"))
        for group in ("cases", "decided_cases"):
            sentences += [(f"{name}:{c['id']}", c["text"]) for c in data.get(group) or []]
    assert len(sentences) >= 36
    leaked = [ref for ref, text in sentences if _plain(text) in prompt]
    assert leaked == [], f"평가셋 문장이 프롬프트에 있음: {leaked}"
    labels = [k["label"] for k in keywords["keywords"]]
    for k in keywords["keywords"]:
        labels += [p["label"] for p in k["problems"]] + [s["label"] for s in k["solutions"]]
    found = [label for label in labels if len(label) >= 2 and _plain(label) in prompt]
    assert found == [], f"카탈로그 라벨이 프롬프트에 있음: {found}"
    for key in ("water", "fire", "tree", "wind"):  # 시나리오 안 키도 쓰지 않는다
        assert not re.search(rf"\b{key}(_[ps]\d)?\b", prompt)
