# 의도 분류 (intent)

지원자 메시지마다 의도(제안·동의·반대·보류·질문·기타)와 대상 안, 대상 안에 대한 유보 표현 여부, 지원자가 답하지 말라고 뺀 동료를 실시간으로 분류하는 역할이다. 결과는 안별 지지자 누적(지지로 세는 것은 high이고 유보 표현이 없는 제안·동의, 보류는 그 안 지지 철회), 최초 제안자 귀속, [진행 매니저](manager.md) 규칙(지명·제외 자리, 설득 가능 규칙), 합의 판정(지원자의 최신 발언이 명시적 동의인지), 침묵 초대 조건, 토론 동료의 계획 밖 입장 문장 판정에 쓴다. 뜻의 해석은 분류가 하고, 그 결과로 무엇을 저장·확정할지는 코드가 정한다. 평가 수준에는 직접 쓰지 않는다. 상세는 [architecture/agents.md](../architecture/agents.md) "실시간 의도 분류"가 정본이다.

## 입력

- 메시지 원문과 그 메시지의 단계(`messages.phase`)
- 그 단계의 카탈로그 안 전체·이미 나온 안·직전 메시지 5개·자리 이름
- 지시 프롬프트(채용 건 복사본의 `intent.md`, 없으면 내장 요약)

## 출력

- `{intent, target_option_id, scenario_option_key, new_option_label, confidence, reservation, addressed_to, excluded}`([contracts/internal.md](../contracts/internal.md) "의도 분류 출력 스키마"). `intent`는 `propose`·`agree`·`oppose`·`withhold`·`question`·`other`
- `excluded`에 든 자리는 검증에서 `addressed_to`에서 뺀다
- 실패·타임아웃·스키마 위반이면 `other`·`confidence: low`·`reservation: false`·`fallback=True`

## 데이터 파일 (`scenarios/missions/debate/`)

| 파일 | 쓰는 내용 |
|---|---|
| `prompts/intent/intent.md` (키 `intent.md`) | 분류 지침(의도가 섞일 때 고르는 순서, 기존 안으로 묶는 기준), 예시, `confidence`·`reservation`·`excluded` 기준 |
| `keywords.yaml` | 단계별 카탈로그 안(`scenario_option_key` 후보) |

## 코드 입구

- `services/api/app/agent/intent/__init__.py`: `classify`·`fallback`·`remember`/`recall`
- 부르는 곳: api 메일박스 `services/api/app/services/debate.py`(메시지 저장 뒤), 토론 그래프 `services/api/app/agent/debate/graph.py`(`_message_intent`, stance_check 문장 판정 `_judge_sentence`)
- 모델 역할 키 `intent`, env `MODEL_INTENT`

## 검증

- 단위(LLM mock): `services/api/tests/agent/test_intent.py`(픽스처 `services/api/tests/agent/fixtures/intent_keyword_20.jsonl`)
- 단위(DB): `services/api/tests/test_c02_consensus_fill.py`(보류 철회, 유보 동의 미저장, 최신 발언 동의 조건), `services/api/tests/test_mailbox.py`(유보·제외 자리 저장)
- 평가셋: `tests/eval/intent_cases.yaml`(20문장 + 결정 사례 `decided_cases`: 보류·새 안·A 말고 B·정정·자리 빼기), `tests/eval/test_eval.py::test_intent_live`. `make eval-live`
