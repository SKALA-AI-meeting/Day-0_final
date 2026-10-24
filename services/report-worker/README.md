# report-worker

세션이 끝나면 큐 작업으로 리포트 그래프를 돌려 평가 초안을 만들고, 담당자 검토 완료 뒤 최종 리포트를 확정하는 워커.

## 한눈에

| 항목 | 내용 |
|---|---|
| 역할 | 큐 소비자 3개(`report.generate`·`report.approve`·`report.dlq`)와 리포트 그래프 `collect → aggregate → evaluate → draft → approval[interrupt] → finalize`(thread `report:{session_id}:{eval_round}`, PostgresSaver) |
| 사용 기술 | Python 3.12, LangGraph(PostgresSaver), langchain-openai, aio-pika, mcp 클라이언트, httpx, psycopg |
| 포트 | 없음(헬스는 30초마다 갱신하는 heartbeat 파일 `HEARTBEAT_PATH`, 기본 `/tmp/heartbeat`) |
| 필요한 env | `DATABASE_URL` `RABBITMQ_URL` `API_INTERNAL_URL` `INTERNAL_TOKEN` `ASSESSMENT_MCP_URL` `ASSESSMENT_MCP_TOKEN` `OPENAI_API_KEY` `OPENAI_MODEL` `MODEL_EVALUATOR` `LLM_FAKE` `LLM_FAULT` `HEARTBEAT_PATH` |
| 의존 서비스 | rabbitmq(작업 수신), postgres(체크포인트), api 내부 앱(8001), assessment-mcp(9103) |

env 이름은 `report_worker/config.py`·`llm.py`·`heartbeat.py`와 `infra/.env.example`을 대조한 것이다. `LLM_FAKE=1`이면 평가 호출이 결정적 고정 응답이 되고, `LLM_FAULT`는 e2e 장애 주입 전용이다.

## 구현된 기능

| 기능 | 코드 | PRD |
|---|---|---|
| 세션 종료 뒤 리포트 초안 자동 생성(큐 작업) | `consumer.py`, `graph.py` | [F-32](../../PRD.md) |
| 요소 5개를 따로 평가해 하위 차원 수준·근거·확신도 산출, 근거 없으면 기록 없음, 요소 단위 1회 재요청 | `evaluate.py` | [F-33](../../PRD.md) |
| 요소 수준(평균 반올림)·기록 없음 사유 | `evaluate.py` | [F-34](../../PRD.md) |
| 사실 지표·기여 대조·칸 결과를 초안에 조립(LLM 없음) | `report.py` | [F-35, F-37](../../PRD.md) |
| 검토 완료 뒤 같은 작업을 이어 최종 리포트 확정 | `graph.py`(interrupt), `consumer.py` | [F-09](../../PRD.md) |
| 실패 재시도(백오프 5·15·45초, attempt≥3 → DLQ, 복구 뒤 재생성) | `consumer.py` | [F-36](../../PRD.md) |

기능별 검증 테스트는 [`docs/features.md`](../../docs/features.md)에 있다.

## 구조

| 모듈 | 내용 |
|---|---|
| `assessment.py` | assessment-mcp 클라이언트(`X-Assessment-Token`). `get_session_record`·`get_rubric`·`get_contributions`·`get_transcript_span`·`save_evaluation`만 |
| `api.py` | 내부 REST(X-Internal-Token): 세션 정보, 어시스턴트 호출(요청·응답 원문 포함), `GET /evaluations`, draft·final·report_failed |
| `evaluate.py` | 요소별 평가 호출(기본 5회, 다른 요소 결과 없음. 조건부로 요소 재요청), 최종 행에서 요소 요약 조립, 워커 검증(형식·근거·금지어·사실 모순), 재요청 뒤에도 실패면 `evaluate_failed`, 확신도 clamp, 요소 수준(평균 .5 올림), 프롬프트 버전 맵 |
| `scenes.py` | 차원별 핵심 장면(핵심 카드 창, 결정 전후 자르기)·보조 장면(`aux_scenes`)과 장면마다의 지원자 메시지(저장 순서·단계 기준), 평가 프롬프트 `[사실]` 블록 |
| `report.py` | aggregate(사실 지표 그대로, 칸·카드·관찰 창, 기여 대조 `contribution_check`)와 draft 조립(근거 원문·앞 5·뒤 3 맥락·record_ref, 카드 장면은 `report_template` 그대로, 총점 없음, LLM 없음) |
| `graph.py` | LangGraph 그래프, thread·run_id 규칙(`uuid5(session_id, "evaluate:"+eval_round)`) |
| `consumer.py` | 메시지 처리 규칙(같은 eval_round만 체크포인트 재개, 재시도 백오프 5·15·45초, attempt≥3 → DLQ, dlq는 api 복구까지 60초 간격)과 aio-pika 연결 |
| `llm.py` | `MODEL_EVALUATOR`(비면 `OPENAI_MODEL`), temperature 0.1, JSON 출력, 전송 실패 재시도 2회 뒤 예외(저장 없이 큐 재시도) |

이 워커가 평가자 역할이다(역할 문서 [docs/agents/evaluator.md](../../docs/agents/evaluator.md)). 평가 프롬프트(카탈로그 `prompts/evaluator/{common,<element>}.md`, 키 `evaluator/{common,<element>}.md`)와 금지어(카탈로그 `prompts/shared/guard_patterns.yaml`, 키 `guard_patterns.yaml`의 `evaluator_forbidden`)는 채용 건에 복사된 것을 읽는다. 복사본에 하나라도 없으면 현재 파일로 채우지 않고 재시도 없이 리포트 실패로 둔다. 카탈로그 파일을 읽지 않으므로 `scenarios` 마운트가 필요 없다.

## 실행

저장소 루트에서 실행한다. postgres·rabbitmq·api(내부 8001)·assessment-mcp(9103)가 떠 있어야 한다. 로컬 실행 env: `infra/.env.local`을 만들고 DB·큐·내부 API·MCP 주소를 `localhost`로 바꾼다(항목은 [configuration.md](../../docs/guides/configuration.md) "로컬 실행용 `infra/.env.local`"). 로컬 서비스는 `infra/.env.local` → `infra/.env` 순으로 읽는다.

```
make infra-up                                                      # postgres, rabbitmq, rag-mcp
uv run --directory services/report-worker python -m report_worker  # heartbeat + 소비자. infra/.env.local → infra/.env를 읽는다
```

## 큐와 API

이 서비스는 HTTP 포트가 없다. 소비하는 큐는 위 3개이고 메시지 형식은 [`docs/contracts/queue.md`](../../docs/contracts/queue.md)에 있다. 호출하는 api 내부 REST와 MCP 도구는 `api.py`·`assessment.py`의 표를 따른다([`docs/contracts/internal.md`](../../docs/contracts/internal.md), [`docs/contracts/mcp.md`](../../docs/contracts/mcp.md)).

## 테스트

pytest 단위 테스트다. FakeLLM·가짜 assessment·가짜 api·메모리 체크포인터로 돌아 외부 서비스가 필요 없다. `live` 표시는 실제 모델을 부르는 테스트로 `make test`에서 빠진다.

```
uv run --directory services/report-worker pytest -q    # 이 서비스만
make test                                               # 전체
```

<!-- gen:tests -->
> 생성 블록이다. 코드를 바꾸면 `make docs-gen`으로 갱신한다.

총 83개(live 2개 포함, `make test`는 live를 뺀다).

<details><summary><code>tests/test_config.py</code> (1)</summary>

- `test_restricted_allowlist_matches_api_rule`

</details>

<details><summary><code>tests/test_evaluate.py</code> (29)</summary>

- `test_fixture_outputs_pass_validation`
- `test_invalid_dimension_retries_element_once_then_evaluate_failed` ×7
- `test_level4_with_one_candidate_unit_is_capped_to_3_medium_not_rejected`
- `test_retry_prompt_names_dimension_and_reject_reason`
- `test_failed_row_rationale_has_guidance_and_reason_code`
- `test_retry_result_replaces_first_output`
- `test_valid_output_calls_once_and_summary_is_assembled_from_rows`
- `test_confidence_over_cap_is_clamped_not_rejected`
- `test_core_card_not_fired_caps_low_and_one_persuasion_card_caps_medium`
- `test_credit_record_counts_contribution_reference`
- `test_forbidden_summary_line_retries_like_rationale`
- `test_missing_summary_line_falls_back_to_first_rationale_sentence`
- `test_unparseable_twice_fails_every_dimension`
- `test_core_card_not_fired_without_support_mark_becomes_event_not_fired`
- `test_partially_delivered_core_card_counts_as_not_fired`
- `test_partially_delivered_core_card_with_aux_scene_evidence_caps_low`
- `test_summary_lists_no_record_rows_by_state_without_llm`
- `test_element_level_rounds_half_up_and_null_when_no_record`
- `test_numeric_msg_refs_are_fixed_when_session_has_m_prefixed_id`
- `test_fixed_numeric_ref_to_ai_message_does_not_count_as_evidence`
- `test_live_one_element_has_no_evaluate_failed` (live)
- `test_with_refs_exposes_all_four_reference_kinds`
- `test_live_honesty_credit_record_cites_idea_credit` (live)

</details>

<details><summary><code>tests/test_facts_checks.py</code> (21)</summary>

- `test_core_scene_follows_card_window_rule_by_order_and_phase`
- `test_rounded_minutes_do_not_move_a_message_across_the_decision`
- `test_aux_scenes_come_from_rubric_data`
- `test_scene_without_core_cards_is_session_and_undelivered_card_is_missing`
- `test_after_decision_core_is_missing_when_slot_not_decided_and_old_copy_has_no_cut`
- `test_facts_block_lists_scenes_per_dimension`
- `test_prompt_has_facts_block_and_message_facts_once`
- `test_no_activity_and_event_not_fired_rejected_when_core_scene_has_them`
- `test_peer_message_as_evidence_is_rejected`
- `test_evidence_scene_tag_is_checked_against_membership_not_words`
- `test_after_decision_core_rejects_evidence_before_decision`
- `test_before_decision_core_excludes_later_messages`
- `test_fact_contradiction_retries_with_fact_then_evaluate_failed` ×3
- `test_retry_keeps_rows_valid_in_first_output`
- `test_core_missing_with_only_core_tagged_evidence_becomes_event_not_fired`
- `test_tidy_fixes_only_single_meaning_shapes`
- `test_output_shapes_with_one_meaning_are_read`
- `test_level5_from_auxiliary_scene_evidence_only_is_capped_to_4_low`
- `test_proposal_field_tagged_with_aux_scene_counts_as_scene_evidence`

</details>

<details><summary><code>tests/test_graph.py</code> (23)</summary>

- `test_generate_evaluates_each_element_once_and_saves_draft`
- `test_draft_shape_matches_report_contract`
- `test_redelivery_reuses_checkpoint_without_llm`
- `test_rerun_reuses_saved_assembled_summary_without_llm`
- `test_crash_after_save_recovers_elements_without_llm`
- `test_new_eval_round_starts_new_thread_from_collect`
- `test_approve_resumes_and_final_equals_draft`
- `test_finalize_failure_resumes_same_round_finalize_on_reapprove`
- `test_approve_before_draft_republishes`
- `test_llm_timeout_saves_nothing_and_requeues`
- `test_three_failures_go_to_dlq_and_dlq_marks_failed`
- `test_c7_honesty_credit_contrast`
- `test_llm_fake_runs_graph_to_draft_without_key`
- `test_llm_fake_not_confused_by_block_header_in_candidate_message`
- `test_llm_fake_same_input_same_result`
- `test_llm_fault_evaluator_timeout_requeues_then_succeeds`
- `test_position_keeps_evaluator_prompts_after_catalog_change`
- `test_pinned_prompts_cover_retry_rewrite_and_rerun`
- `test_new_position_gets_changed_evaluator_prompts`
- `test_position_without_pinned_prompts_fails_without_retry` ×4

</details>

<details><summary><code>tests/test_heartbeat.py</code> (1)</summary>

- `test_beat_writes_current_time`

</details>

<details><summary><code>tests/test_states.py</code> (8)</summary>

- `test_preempted_core_card_turns_not_fired_and_no_activity_into_preempted`
- `test_not_preempted_card_stays_event_not_fired_and_model_preempted_is_checked`
- `test_old_rubric_copy_keeps_event_not_fired_and_one_no_record_group`
- `test_facts_block_marks_preempted_dimensions`
- `test_report_cards_mark_preempted_only_for_cards_that_did_not_fire`
- `test_adopted_candidate_option_fixes_only_after_decision_dimensions`
- `test_adopted_and_preempted_after_dimensions_are_preempted`
- `test_facts_block_gives_fixed_result_and_no_after_scene_when_adopted`

</details>
<!-- /gen:tests -->

## 관련 문서

- [`docs/services/report-worker.md`](../../docs/services/report-worker.md): 그래프·평가 호출·복구
- [`docs/product/rubric.md`](../../docs/product/rubric.md): 척도·검증 규칙
- [`docs/contracts/queue.md`](../../docs/contracts/queue.md): 큐 메시지
- [`docs/architecture/session-lifecycle.md`](../../docs/architecture/session-lifecycle.md): 리포트 상태 전이
- [`docs/guides/configuration.md`](../../docs/guides/configuration.md): env
