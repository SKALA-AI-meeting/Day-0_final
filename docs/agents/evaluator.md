# 평가자 (evaluator)

세션이 끝난 뒤 지원자의 토론 태도만 보고, 공개된 행동 기준 척도(요소 5개·하위 차원·수준 앵커)로 차원별 수준과 근거를 판단해 담당자가 참고할 초안을 만드는 역할이다. 실력(안의 질·근거 정확성·기획안 품질)은 보지 않고, 최종 평가는 담당자가 한다. report-worker 안에서 돈다. 상세는 [architecture/agents.md](../architecture/agents.md) "평가 에이전트"와 [product/rubric.md](../product/rubric.md)가 정본이다.

## 입력

- assessment-mcp `get_session_record`(대화 전체·칸·기획안·사실 지표)·`get_rubric`·`get_contributions`
- 평가 사실(결정 A1): api가 계산해 `facts`에 넣는 `decisions`(칸별 결정 시각·경로·적용 규칙·확정 시점 지지 현황·지원자 안 채택 여부(시간 초과는 채택 아님, 결정 A5)·선결정한 카드(결정 A3))·`message_facts`(지원자 메시지별 답한 대상·먼저 정해진 칸·붙여넣기 의심)와, 워커가 척도(`decision_timing`·`aux_scenes`)·카드 발생 기록으로 계산하는 차원별 핵심·보조 장면과 장면마다의 지원자 메시지(`scenes.py`). 프롬프트에는 `[사실]` 블록과 메시지 행의 필드로 들어가고, 출력이 이 사실과 모순되면 워커가 거부한다([product/rubric.md](../product/rubric.md) 7.3)
- 채용 건 복사본(`positions.scenario.prompts`)의 평가 프롬프트와 평가 금지어. 복사본에 없으면 현재 파일로 채우지 않고 리포트 실패

## 출력

- 요소마다 차원 행(수준 또는 기록 없음 사유·근거 참조·확신도) (근거마다 장면 id, 대표 행동 한 구절 `summary_line`) → 검증 → 최종 차원 행에서 코드가 조립한 요소 요약(결정 A2) → assessment-mcp `save_evaluation`(형식은 [contracts/internal.md](../contracts/internal.md) EvaluationSave)
- `prompt_versions`(공통·요소 프롬프트 파일별 해시), `rubric_version`, `model`

## 장면 검증의 범위

워커는 근거가 단 장면(핵심 `core` 또는 척도 `aux_scenes`의 보조 장면) 안에 **위치하는지**만 검증한다. 메시지가 그 차원에 관련된 행동인지는 평가자의 판단이고 워커는 내용을 보지 않는다. 범위가 세션 전체인 장면(경청·배려·정직의 `session` 보조 장면, 핵심 카드가 없는 차원의 핵심 장면)에서는 `out_of_scene`이 지원자 메시지를 하나도 막지 않고, 다른 사람의 메시지(`peer_evidence`)와 그 차원에 없는 장면 id(`unknown_scene`)만 막는다. 보조 장면 근거만인 수준은 확신도 최대 `low`, 수준 5는 4로 깎는다. 보조 장면이 받는 기획안 필드(`fields`)는 그 장면 id를 달아 근거로 쓸 수 있다. 정본 [product/rubric.md](../product/rubric.md) 7.3.

## 기록 상태와 워커가 정하는 결과

기록 없음은 장면 없음(`not_applicable`·`event_not_fired`·`preempted`) / 반응 없음(`no_activity`·`auto_submitted_blank`) / 판단 보류(`evaluate_failed`)로 묶이고, 수준이 있으면 반응 있음이다(결정 A3, `rubric.yaml` `no_record_reasons[].state`). 워커는 사실로 두 가지를 정한다. 핵심 카드가 지원자 선확정으로 생기지 않은 차원의 `event_not_fired`·`no_activity`는 `preempted`로 바꾸고, 핵심 카드 단계에서 지원자 안이 채택된 결정 뒤 차원(`decision_timing: after`)은 출력과 상관없이 `not_applicable`로 둔다(결정 A5). 판단 문장(`rationale`·`summary_line`)은 담당자가 읽으므로 "차원" 대신 "세부 행동"이라고 쓴다(결정 A4).

## 데이터 파일 (`scenarios/missions/debate/`, 모두 채용 건 복사본으로 읽음)

| 파일 | 쓰는 내용 |
|---|---|
| `prompts/evaluator/common.md` (키 `evaluator/common.md`) | 공통 지침: 판단 순서·근거 규칙·출력 형식 |
| `prompts/evaluator/<element>.md` ×5 (키 `evaluator/<element>.md`) | 요소별 특수 지침 |
| `prompts/shared/guard_patterns.yaml` (키 `guard_patterns.yaml`) | `evaluator_forbidden`(합격·순위·총점 표현 금지어) |
| `rubric.yaml` | 차원·앵커(`get_rubric`으로 받음) |

report-worker는 `scenarios/`를 마운트하지 않는다. 카탈로그 파일을 직접 읽는 `load_prompts`·`load_forbidden`은 테스트와 평가셋 실행 전용이다.

## 코드 입구

- `services/report-worker/report_worker/evaluate.py`: `run_evaluation`·`evaluate_element`·`check_dimension`·`check_facts`·`validate_output`·`build_summary`, 복사본 읽기 `pinned_catalog`
- `services/report-worker/report_worker/scenes.py`: 차원별 핵심·보조 장면(`build_scenes`)과 `[사실]` 블록(`facts_block`)
- `services/api/app/services/facts.py`: 평가 사실 `evaluator_facts`(순수 함수, 평가셋 실행기도 같은 함수를 부른다)
- `services/report-worker/report_worker/graph.py`: 리포트 그래프의 `evaluate` 노드. 워커 전체가 평가자 역할이라 따로 역할 폴더를 두지 않는다
- 모델 역할 키 `evaluator`, env `MODEL_EVALUATOR`(워커 `llm.py`)

## 검증

- 단위(LLM mock): `services/report-worker/tests/test_evaluate.py`, `test_facts_checks.py`(장면 구간·사실 모순 거부), `test_graph.py`(복사본 고정·버전 맵), `services/api/tests/test_evaluator_facts.py`(평가 사실 계산)
- 평가셋: `tests/eval/bars_sessions.yaml`(대표 세션 + 장면 세션), `tests/eval/test_eval.py::test_bars_live`. `make eval PROFILE=eval-text ALLOW_API=1`
