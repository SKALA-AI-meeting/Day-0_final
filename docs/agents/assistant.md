# 어시스턴트 (assistant)

지원자가 토론 중 자료를 물어보는 개인 도우미다. LangGraph가 아닌 Python 도구 호출 루프로, 모델이 rag-mcp 도구를 골라 확정 키워드 자료로 답한다. 토론방에 말하지 않고 평가 정보에 접근하지 않는다. 상세는 [architecture/agents.md](../architecture/agents.md) "어시스턴트"가 정본이다.

## 입력

- 지원자 질문과 세션별 이어지는 대화 이력(프로세스 메모리)
- 도구: rag-mcp `search_materials`·`get_material`(응답당 최대 `max_tool_calls`). 키워드 확정 전에는 잠금 안내

## 출력

- 답 본문과 `cited_material_ids`(실제로 쓴 자료만: 제목·id를 적었거나 숫자·12자 이상 구간이 글자 그대로 겹치는 자료), 기록은 `messages`(channel=assistant)·`assistant_calls`
- 받은 자료·지원자 질문에 없는 숫자는 재생성 1회(도구 루프 재실행) 뒤에도 남으면 그 문장을 뺀다. 상한을 넘으면 줄여 달라는 재생성 1회 뒤 출처 줄을 뺀 상한 안의 문장 끝에서 자른다. 도구 결과가 없으면 출처 줄이 없다(결정 D1)
- guard에 걸리면 그룹별 안내문(`notices`)으로 교체

## 데이터 파일 (`scenarios/missions/debate/`)

| 파일 | 쓰는 내용 |
|---|---|
| `prompts/assistant/assistant.md` (키 `assistant.md`) | 판단 순서(자료에 없으면 없다고 말함)·숫자 원문·인용 규칙 |
| `prompts/shared/guard_patterns.yaml` (키 `guard_patterns.yaml`) | 금지 패턴·`max_chars` |
| `prompts/shared/fixed_phrases.yaml` (키 `fixed_phrases.yaml`) | 잠금 안내(`assistant_locked`)·확인되지 않는 수치를 뺀 뒤 남는 문장이 없을 때(`assistant_missing`)·안내문(`notices`) |
| `mission.yaml` `assistant` | 세션당 호출 한도·도구 호출 한도·도구 목록 |
| `materials/<keyword>/*.md` | 답의 근거(rag-mcp가 줄·문장 경계 청크로 색인, 숫자·문장이 청크 사이에서 끊기지 않음) |

## 코드 입구

- `services/api/app/agent/assistant/runner.py`: `AssistantGraphRunner`(도구 루프 `_Loop`)
- 모델 역할 키 `assistant`, env `MODEL_ASSISTANT`

## 검증

- 단위(LLM mock): `services/api/tests/agent/test_assistant.py`(인용 필터·숫자 검사·문장 단위 자르기), 청크 경계 `services/rag-mcp/tests/test_chunk.py`
- 평가셋: `tests/eval/assistant_cases.yaml`(실행기 `tests/eval/assistant_eval.py`, 테스트 `tests/eval/test_assistant_eval.py`). `make eval PROFILE=eval-text ALLOW_API=1`
