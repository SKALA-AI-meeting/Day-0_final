# 진행 매니저 (manager)

누가 언제 말할지, 카드 담당과 입장 계획을 정하는 결정적 규칙(`rules.py`). 그래프 노드 `cards`·`turn`·`fill`은 `../debate/graph.py`가 이 규칙을 불러 실행한다. 역할 문서: [docs/agents/manager.md](../../../../../docs/agents/manager.md).

읽는 데이터(채용 건 복사본, 정본은 `scenarios/missions/debate/`):

- `prompts/manager/turn_manager.md` (키 `turn_manager.md`)
- `prompts/shared/fixed_phrases.yaml` (키 `fixed_phrases.yaml`)
- `personas.yaml`, `cards.yaml`, `keywords.yaml`, `mission.yaml`
