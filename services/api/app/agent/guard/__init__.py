"""guard: 모든 LLM 생성 텍스트(페르소나·어시스턴트)에 코드에서 무조건 실행한다(harness.md
Guardrails).

패턴 정본은 카탈로그 `prompts/shared/guard_patterns.yaml`(키 `guard_patterns.yaml`)이고 실행은
채용 건에 고정된 복사본을 읽는다.
- 적용 단위는 메시지 전체다. 어느 그룹 패턴이든 걸리면 hit.
- `allow_exact`: 메시지 전체(앞뒤 공백 제거)가 목록의 문장과 글자 그대로 같으면 어느 그룹도 검사하지
  않는다(정해진 평가 안내문 보존). 부분 포함·변형 문장은 예외가 아니다.
- 그룹의 `phases`가 있으면 그 단계 발언에만 적용한다(예: credit은 기획안 단계만). 호출한 노드가
  단계를 넘기고, 단계를 모르는 호출(어시스턴트)은 단계 범위 그룹을 건너뛴다.
- `names`(지원자 표시 이름 등 사람이 정한 이름)는 가린 뒤 검사한다. 이름 안의 글자("합성-대본"의
  '대본')가 금지 패턴에 걸려 이름을 부른 발언이 막히지 않게 하기 위함이다. 자르기는 원문 기준이다.
- 처리 정책(재생성 1회 → 페르소나는 자리별 안전 문구, 어시스턴트는 그룹 notice)은 호출한 노드가
  정하고, 이 모듈은 판정·자르기·교체 문장만 준다. max_chars 초과는 자른다.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

GUARD_FILE = "guard_patterns.yaml"
MASK = "○"


@dataclass(frozen=True)
class Group:
    id: str
    notice: str  # fixed_phrases.yaml notices의 키
    patterns: tuple[re.Pattern[str], ...]
    phases: frozenset[str] = frozenset()  # 비어 있으면 모든 단계, 아니면 그 단계 발언에만 적용

    def applies(self, phase: str | None) -> bool:
        return not self.phases or phase in self.phases

    def hits(self, text: str) -> bool:
        return any(p.search(text) for p in self.patterns)


@dataclass(frozen=True)
class GuardResult:
    text: str
    hits: tuple[str, ...]  # 걸린 그룹 id(설정 순서)
    truncated: bool

    @property
    def ok(self) -> bool:
        return not self.hits


class Guard:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.max_chars = int(spec.get("max_chars", 400))
        self.allow_exact = frozenset(s.strip() for s in spec.get("allow_exact") or ())
        self.groups = tuple(
            Group(
                id=g["id"],
                notice=g.get("notice", ""),
                patterns=tuple(re.compile(p, re.IGNORECASE) for p in g.get("patterns") or ()),
                phases=frozenset(g.get("phases") or ()),
            )
            for g in spec.get("groups") or ()
        )

    @classmethod
    def from_yaml(cls, text: str) -> "Guard":
        return cls(yaml.safe_load(text) or {})

    def groups_hit(
        self, text: str, phase: str | None = None, names: Iterable[str] = ()
    ) -> list[str]:
        """phase는 발언 단계. 단계 범위가 있는 그룹은 단계가 맞을 때만 본다(None이면 건너뜀).
        names는 검사 전에 가린다."""
        body = text.strip()
        if body in self.allow_exact:
            return []
        body = mask_names(body, names)
        return [g.id for g in self.groups if g.applies(phase) and g.hits(body)]

    def check(self, text: str, phase: str | None = None, names: Iterable[str] = ()) -> GuardResult:
        """판정 + 자르기. 문장은 바꾸지 않는다(교체는 노드가 정한다)."""
        body = text.strip()
        hits = tuple(self.groups_hit(body, phase, names))
        truncated = len(body) > self.max_chars
        if truncated:
            body = body[: self.max_chars].rstrip()
        return GuardResult(body, hits, truncated)

    def notice_key(self, group_id: str) -> str | None:
        return next((g.notice for g in self.groups if g.id == group_id), None)


def mask_names(text: str, names: Iterable[str]) -> str:
    """이름을 같은 길이의 가림 글자로 바꾼다(긴 이름부터, 이름 뒤 "님" 등 문장 구조는 그대로)."""
    for name in sorted({n.strip() for n in names if n and n.strip()}, key=len, reverse=True):
        text = text.replace(name, MASK * len(name))
    return text


@lru_cache(maxsize=16)
def _cached(text: str) -> Guard:
    return Guard.from_yaml(text)


def guard_from_prompts(prompts: dict[str, str]) -> Guard:
    """채용 건에 고정된 패턴(prompts 복사본)으로 만든 Guard. 같은 본문은 다시 컴파일하지 않는다."""
    return _cached(prompts.get(GUARD_FILE, ""))
