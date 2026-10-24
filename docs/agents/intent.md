# 의도 분류 (intent)

지원자 메시지마다 의도(제안·동의·반대·보류·질문·기타)와 대상 안, 대상 안에 대한 유보 표현 여부, 지원자가 답하지 말라고 뺀 동료를 실시간으로 분류하는 역할이다. 결과는 안별 지지자 누적(지지로 세는 것은 high이고 유보 표현이 없는 제안·동의, 보류는 그 안 지지 철회), 최초 제안자 귀속, [진행 매니저](manager.md) 규칙(지명·제외 자리, 설득 가능 규칙), 합의 판정(지원자의 최신 발언이 명시적 동의인지), 침묵 초대 조건에 쓴다. 토론 동료 초안의 입장 판정(stance_check)은 같은 모델 역할을 쓰지만 질문·프롬프트가 다른 별도 판정이다([persona.md](persona.md) "입장 판정"). 뜻의 해석은 분류가 하고, 그 결과로 무엇을 저장·확정할지는 코드가 정한다. 평가 수준에는 직접 쓰지 않는다. 상세는 [architecture/agents.md](../architecture/agents.md) "실시간 의도 분류"가 정본이다.

## 입력

- 메시지 원문과 그 메시지의 단계(`messages.phase`)
- 그 단계의 카탈로그 안 전체·이미 나온 안·직전 메시지 5개·자리 이름
- 지시 프롬프트(채용 건 복사본의 `intent.md`, 없으면 내장 요약)

## 출력

- `{intent, target_option_id, scenario_option_key, new_option_label, confidence, reservation, addressed_to, excluded}`([contracts/internal.md](../contracts/internal.md) "의도 분류 출력 스키마"). `intent`는 `propose`·`agree`·`oppose`·`withhold`·`question`·`other`
- `excluded`에 든 자리는 검증에서 `addressed_to`에서 뺀다. 진행 매니저는 그중 메시지에서 이름을 부른 자리만 뺀다
- 모델 응답은 계약 출력과 모양이 다르다: 먼저 `wanted`(지원자가 원하는 안을 지원자 말로, 저장하지 않음), 새 안 라벨, 대상 한 칸 `target`(나온 안 id 또는 카탈로그 키) 순서로 쓰게 하고 모듈이 계약 필드로 나눈다. 새 안 라벨이 안 라벨과 정규화 문자열로 같으면 그 안으로 바꾼다
- 실패·타임아웃·스키마 위반이면 `other`·`confidence: low`·`reservation: false`·`fallback=True`

## 데이터 파일 (`scenarios/missions/debate/`)

| 파일 | 쓰는 내용 |
|---|---|
| `prompts/intent/intent.md` (키 `intent.md`) | 분류 지침(의도가 섞일 때 고르는 순서, 양보가 섞인 말의 판단 기준, 기존 안으로 묶는 기준), 예시, `confidence`·`reservation`·`excluded` 기준. 예시는 가상의 카탈로그·이름(placeholder)으로 쓰고 평가셋 문장·실제 안 라벨을 넣지 않는다(`scenarios/missions/debate/tests/test_catalog.py`가 검사) |
| `keywords.yaml` | 단계별 카탈로그 안(`scenario_option_key` 후보) |

## 코드 입구

- `services/api/app/agent/intent/__init__.py`: `classify`·`fallback`·`remember`/`recall`
- 부르는 곳: api 메일박스 `services/api/app/services/debate.py`(메시지 저장 뒤), 토론 그래프 `services/api/app/agent/debate/graph.py`(`_message_intent`, 재시작 뒤 같은 입력으로 다시 분류)
- 모델 역할 키 `intent`, env `MODEL_INTENT`

## 검증

- 단위(LLM mock): `services/api/tests/agent/test_intent.py`(픽스처 `services/api/tests/agent/fixtures/intent_keyword_20.jsonl`)
- 단위(DB): `services/api/tests/test_c02_consensus_fill.py`(보류 철회, 유보 동의 미저장, 최신 발언 동의 조건), `services/api/tests/test_mailbox.py`(유보·제외 자리 저장)
- 평가셋: `tests/eval/intent_cases.yaml`(20문장 + 결정 사례 `decided_cases`: 보류·새 안·A 말고 B·정정·자리 빼기), `tests/eval/test_eval.py::test_intent_live`. 보류 세트 `tests/eval/intent_holdout_cases.yaml`(18문장: 양보·보류·지지·질문, 프롬프트 예시·20문장과 겹치지 않음), `test_intent_holdout_live`. `make eval PROFILE=eval-text SUITE=fixtures ALLOW_API=1`
