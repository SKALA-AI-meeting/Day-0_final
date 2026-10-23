# 어시스턴트 (assistant)

지원자 자료 질문에 답하는 도구 호출 루프(`runner.py` `AssistantGraphRunner`). 역할 문서: [docs/agents/assistant.md](../../../../../docs/agents/assistant.md).

읽는 데이터(채용 건 복사본, 정본은 `scenarios/missions/debate/`):

- `prompts/assistant/assistant.md` (키 `assistant.md`)
- `prompts/shared/guard_patterns.yaml`, `prompts/shared/fixed_phrases.yaml` (키 `guard_patterns.yaml`, `fixed_phrases.yaml`)
- `mission.yaml` `assistant`, 자료 `materials/<keyword>/*.md`(rag-mcp 도구로만)
