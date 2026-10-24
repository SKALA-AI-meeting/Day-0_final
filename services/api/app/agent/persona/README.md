# 토론 동료 (persona)

자리별 토론 동료의 발언 생성(respond): 컨텍스트 조립(입장 상태·상황 사실·카드 차례), 도구 루프, 계획 밖 입장 판정, 고정 문구. 그래프 노드 `respond`·`guard`·`stance_check`·`apply`는 `../debate/graph.py`에 있다. 역할 문서: [docs/agents/persona.md](../../../../../docs/agents/persona.md).

읽는 데이터(채용 건 복사본, 정본은 `scenarios/missions/debate/`):

- `prompts/persona/persona_<type>.md`, `prompts/persona/safe_<type>.md` (키 `persona_<type>.md`, `safe_<type>.md`)
- `prompts/persona/common_rules.md`, `prompts/persona/stance_fallback.yaml` (키 `common_rules.md`, `stance_fallback.yaml`)
- `prompts/shared/guard_patterns.yaml`, `prompts/shared/fixed_phrases.yaml` (키 `guard_patterns.yaml`, `fixed_phrases.yaml`)
- `personas.yaml`, `cards.yaml`(`purpose`·`followup_purpose`), 자료 `materials/<keyword>/*.md`(rag-mcp 도구로만)
