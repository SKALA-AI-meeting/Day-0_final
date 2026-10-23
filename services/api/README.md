# api

현재 음성 회의·Azure F0 설정·오류 복구는 [음성 구현](../../docs/architecture/voice.md), 공개 인터페이스는 [음성 계약](../../docs/contracts/voice.md)을 따른다. 공용 채팅 입력창과 말풍선은 음성 회의 UI로 대체한다. 내부 메시지 기록과 기존 합의·평가 경로는 유지한다.

토론형 세션의 상태·단계·칸·메일박스·스케줄러와 토론 에이전트(턴 매니저·페르소나·어시스턴트·의도 분류)를 한 프로세스에서 돌리고, 공개(8000)·내부(8001) REST와 WebSocket을 낸다.

## 한눈에

| 항목 | 내용 |
|---|---|
| 역할 | 인증, 세션 수명주기(초대~제출), 단계·칸·합의 판정, 토론 그래프 실행, 실시간 의도 분류, 어시스턴트, 리포트 저장·검토 |
| 사용 기술 | Python 3.12, FastAPI + uvicorn, SQLAlchemy(asyncpg), LangGraph·langchain-openai, aio-pika, fastmcp(MCP 클라이언트) |
| 포트 | 공개 8000(`/admin`·`/s`·`/sessions`·`/ws`·`/assets`), 내부 8001(`/internal`, `X-Internal-Token`) |
| 필요한 env | `DATABASE_URL` `RABBITMQ_URL` `JWT_SECRET` `INTERNAL_TOKEN` `ASSESSMENT_MCP_TOKEN` `DEBATE_MCP_URL` `RAG_MCP_URL` `ASSESSMENT_MCP_URL` `SCENARIOS_DIR` `OPENAI_API_KEY` `OPENAI_MODEL` `MODEL_INTENT` `MODEL_TURN` `MODEL_PERSONA` `MODEL_ASSISTANT` `MODEL_EVALUATOR` `OPENAI_API_KEY_PERSONAL` `GEMINI_API_KEY` `WEB_ORIGIN` `WEB_BASE_URL` `CONTACT_EMAIL` `SESSION_COST_HARD_USD` `AGENT_RUNNER` `LLM_FAKE` `LLM_FAULT` `ENABLE_TEST_API` |
| 의존 서비스 | postgres(필수), rabbitmq(큐 발행), debate-mcp·rag-mcp·assessment-mcp(토론 그래프·결정적 노드의 도구 호출) |

env의 의미·기본값은 `config.py`(`get_settings`)와 `agent/llm/roles.py`, 예시는 `infra/.env.example`이다. 로컬 실행은 `infra/.env.local` → `infra/.env` 순으로 읽고 이미 있는 환경변수를 덮어쓰지 않는다. `LLM_FAKE=1`은 LLM 호출만 결정적 고정 응답으로 바꾸고, `ENABLE_TEST_API=1`이면 `/internal/test/*`가 열린다(운영은 비움). `LLM_FAULT`는 e2e 장애 주입 전용이다.

## 구현된 기능

| 기능 | 코드 | PRD |
|---|---|---|
| 담당자 가입·로그인, 회사 단위 조회 제한 | `routers/admin.py`, `auth.py` | [F-01](../../PRD.md) |
| 초대·재초대·취소, 채용 건 생성과 시나리오 고정 | `routers/admin.py`, `services/admin.py`, `services/scenario.py` | [F-02, F-08](../../PRD.md) |
| 입장·동의·시작·이어하기 | `routers/candidate.py` | [F-10, F-11, F-19](../../PRD.md) |
| 토론방 메시지·실시간 전달·입력 중 | `routers/ws.py`, `hub.py`, `services/debate.py` | [F-12](../../PRD.md) |
| 칸 확정·합의 판정·마감 채우기 | `services/debate.py`, `services/consensus.py`, `services/timeout.py` | [F-13, F-26](../../PRD.md) |
| 단계 전환·남은 시간·임박 안내 | `services/phases.py`, `scheduler.py` | [F-14, F-20](../../PRD.md) |
| 참고 자료 잠금·열림 | `routers/candidate.py`(materials), `routers/internal.py` | [F-15](../../PRD.md) |
| 어시스턴트(도구 호출 최대 3회, 세션당 60회) | `agent/assistant/` | [F-16](../../PRD.md) |
| 기획안 저장·제출·자동 제출 | `routers/candidate.py`, `scheduler.py` | [F-17](../../PRD.md) |
| 디브리핑·설문 | `routers/candidate.py` | [F-18](../../PRD.md) |
| 턴 조절, 고정 입장·근거 발언, 침묵 초대 | `agent/debate/`(그래프·페르소나·규칙) | [F-21, F-22, F-25](../../PRD.md) |
| 카드 실행(고정 시각) | `scheduler.py`, `agent/debate/` | [F-23](../../PRD.md) |
| 판정·유도 발언 금지(결정적 검사 → 재생성 → 안전 문구) | `agent/guard/` | [F-24](../../PRD.md) |
| 실시간 의도 분류 | `agent/intent/`, `mailbox.py` | [F-27](../../PRD.md) |
| 관찰·AI 활용 기록, 사실 지표, 기여 대조 자료 | `routers/internal.py`, `services/facts.py` | [F-30, F-31, F-37, F-35](../../PRD.md) |
| 리포트 저장·검토 완료·재생성, 전체 기록 | `routers/admin.py`, `services/reports.py` | [F-03, F-04, F-04a, F-05, F-09, F-36](../../PRD.md) |

기능별 검증 테스트와 상태는 [`docs/features.md`](../../docs/features.md)에 있다.

## 실행

저장소 루트에서 실행한다. 먼저 인프라(`postgres`·`rabbitmq`·`rag-mcp`)를 띄우고 env 파일 두 개를 준비한다. 로컬 실행 env: `infra/.env.local`을 만들고 DB·큐·내부 API·MCP 주소를 `localhost`로 바꾼다(항목은 [configuration.md](../../docs/guides/configuration.md) "로컬 실행용 `infra/.env.local`"). 로컬 서비스는 `infra/.env.local` → `infra/.env` 순으로 읽는다.

```
cp infra/.env.example infra/.env                              # 비밀값 채움(처음 한 번)
cp infra/.env infra/.env.local                                # 서비스 주소를 localhost로(처음 한 번)
make infra-up                                                 # postgres 5432, rabbitmq 5672, rag-mcp 9102
uv run --directory services/api python -m app.run --reload    # 8000 + 8001 + 스케줄러(한 프로세스)
```

debate-mcp·assessment-mcp도 떠 있어야 토론 그래프와 리포트 평가가 동작한다([debate-mcp](../debate-mcp/README.md), [assessment-mcp](../assessment-mcp/README.md)). 전체를 컨테이너로 띄우려면 `make up`.

## API

<!-- gen:api -->
> 생성 블록이다. 코드를 바꾸면 `make docs-gen`으로 갱신한다.

**공개 앱(8000)**

| 메서드 | 경로 | 하는 일 |
|---|---|---|
| GET | `/health` | 헬스 체크 |
| GET | `/s/{token}` | 초대 링크 입장 안내(회사·길이·문의처) |
| POST | `/s/{token}/consent` | 안내 동의와 표시 이름 저장 |
| POST | `/s/{token}/resume` | 같은 링크로 이어하기(세션 토큰 재발급) |
| POST | `/s/{token}/start` | 세션 시작(칸·기획안 초안 생성, 첫 단계 시작) |
| GET | `/sessions/{session_id}/state` | 토론 화면 상태(단계·칸·시간·참여자) |
| POST | `/sessions/{session_id}/slots/{phase}/confirm` | 칸 확정(키워드·문제 상황·해결책) |
| GET | `/sessions/{session_id}/materials` | 참고 자료 목록(키워드 확정 전에는 잠김) |
| GET | `/sessions/{session_id}/materials/{material_id}` | 열린 참고 자료 본문 |
| POST | `/sessions/{session_id}/assistant` | AI 어시스턴트 질문(세션당 60회) |
| GET | `/sessions/{session_id}/proposal` | 기획안 조회 |
| PUT | `/sessions/{session_id}/proposal` | 기획안 자동 저장 |
| POST | `/sessions/{session_id}/proposal/submit` | 기획안 제출(세션 종료) |
| GET | `/sessions/{session_id}/debrief` | 디브리핑(회고·설명) |
| POST | `/sessions/{session_id}/debrief/survey` | 디브리핑 설문 제출 |
| POST | `/admin/auth/signup` | 담당자 가입(회사 생성) |
| POST | `/admin/auth/login` | 담당자 로그인(JWT 발급) |
| GET | `/admin/catalog` | 채용 건 생성 화면에 보일 요소 안내와 샘플 리포트 |
| GET | `/admin/positions` | 회사의 채용 건 목록 |
| POST | `/admin/positions` | 채용 건 생성(시나리오 조립·고정) |
| GET | `/admin/positions/{position_id}/candidates` | 채용 건의 지원자 목록(상태·요소 수준 칩) |
| POST | `/admin/positions/{position_id}/invites` | 지원자 초대 링크 발급(72시간) |
| POST | `/admin/sessions/{session_id}/reissue` | 시작 전 같은 세션의 초대 링크 재발급(실패 세션은 거부) |
| POST | `/admin/sessions/{session_id}/cancel` | 초대·진행 세션 취소 |
| GET | `/admin/sessions/{session_id}/report` | 리포트 조회(초안·검토 완료본) |
| POST | `/admin/sessions/{session_id}/report/regenerate` | 실패한 리포트 다시 생성 |
| POST | `/admin/sessions/{session_id}/review` | 차원 수준 조정과 검토 완료(최종 리포트 확정) |
| POST | `/admin/sessions/{session_id}/report/notes` | 검토 완료 뒤 메모 추가 |
| GET | `/admin/sessions/{session_id}/record` | 전체 기록 타임라인 |
| WS | `/ws/sessions/{session_id}` | 토론방 실시간 채널(메시지·입력 중·단계 전환) |

**내부 앱(8001, `X-Internal-Token`)**

| 메서드 | 경로 | 하는 일 |
|---|---|---|
| GET | `/health` | 헬스 체크 |
| GET | `/internal/sessions/{session_id}` | 세션 요약과 자리 목록 |
| GET | `/internal/sessions/{session_id}/debate_state` | 현재 단계 토론 상태(DebateState) |
| GET | `/internal/sessions/{session_id}/messages` | 토론방 메시지 조회 |
| POST | `/internal/sessions/{session_id}/messages` | 동료 메시지 게시(generation 검사) |
| POST | `/internal/sessions/{session_id}/options` | 안 제안(최초 제안자 기록) |
| POST | `/internal/sessions/{session_id}/stances` | 안 입장(지지·반대) 기록 |
| POST | `/internal/sessions/{session_id}/slots/fill` | 합의된 칸 채우기 |
| GET | `/internal/sessions/{session_id}/proposal` | 기획안 조회 |
| GET | `/internal/sessions/{session_id}/materials` | 열린 자료 목록과 catalog_version(rag-mcp용) |
| GET | `/internal/sessions/{session_id}/materials/{material_id}` | 자료 원문(rag-mcp용) |
| POST | `/internal/sessions/{session_id}/observations` | 카드 관찰 기록(멱등) |
| GET | `/internal/sessions/{session_id}/observations` | 카드 관찰 조회 |
| GET | `/internal/sessions/{session_id}/transcript_span` | 근거 메시지 앞뒤 대화 조회 |
| GET | `/internal/sessions/{session_id}/contributions` | 기여 대조 자료 |
| GET | `/internal/sessions/{session_id}/rubric` | 척도(요소·차원·수준 앵커) |
| POST | `/internal/sessions/{session_id}/evaluations` | 평가 저장(검증·멱등) |
| GET | `/internal/sessions/{session_id}/evaluations` | 저장된 평가 조회 |
| GET | `/internal/sessions/{session_id}/facts` | 사실 지표 |
| GET | `/internal/sessions/{session_id}/slots` | 칸 확정 결과 |
| GET | `/internal/sessions/{session_id}/assistant_calls` | 어시스턴트 호출 기록 |
| POST | `/internal/sessions/{session_id}/report/draft` | 리포트 초안 저장 |
| POST | `/internal/sessions/{session_id}/report/final` | 리포트 최종본 저장 |
| POST | `/internal/sessions/{session_id}/report_failed` | 리포트 생성 실패 기록 |
| POST | `/internal/test/sessions` | 단계 시드 세션 생성(ENABLE_TEST_API=1) |
| POST | `/internal/test/sessions/{session_id}/advance` | 세션 시간 당기기(ENABLE_TEST_API=1) |
| POST | `/internal/test/voice-transcript` | FakeSpeech 전사 대본 설정(테스트 전용) |
<!-- /gen:api -->

요청·응답 형식은 [`docs/contracts/api.md`](../../docs/contracts/api.md)(공개), [`docs/contracts/internal.md`](../../docs/contracts/internal.md)(내부), [`docs/contracts/ws.md`](../../docs/contracts/ws.md)(WebSocket)에 있다.

## 구조

- `services/session.py`·`phases.py`: 상태·단계 전이(SessionService), 누적 마감, 전환 프레임.
- `services/debate.py`: 지원자 메시지 저장·generation, 의도 분류 반영, 안·입장, 칸 확정·합의 채우기, DebateState. `services/consensus.py`는 순수 판정 함수, `services/timeout.py`는 마감 채우기.
- `agent/`: `debate/`(턴 매니저 그래프·페르소나·규칙), `assistant/`, `intent/`, `guard/`(금지 문구 검사), `llm/`(역할별 모델·fake), `mcp_client/`.
- `mailbox.py`: 세션별 직렬 메일박스(agent_runs 상태로 멱등). `scheduler.py`: 5초 틱(마감·임박 안내·idle·card_due·자동 제출·run 재실행·outbox·purge).
- `services/test_sessions.py`: 테스트 API. 단계 시드 `scenarios/e2e/stages/<이름>.yaml`을 서비스 함수로 재생한다.
- 테스트 도구: `tests/kit.py`(가짜 시계·기록 runner·고정 의도 분류·httpx ASGI 클라이언트).

## 테스트

pytest 단위·통합 테스트다. LLM·MCP·RabbitMQ는 mock이고, DB 테스트는 `make infra-up`의 postgres에 실행마다 임시 DB(`dayzero_test_<랜덤>`)를 만들어 쓴다(postgres가 없으면 DB 테스트만 건너뛴다). `live` 표시는 실제 LLM을 부르는 테스트로 `make test`에서 빠지고 `make test-live`로 따로 돈다.

```
make test                                              # 전체 서비스 단위 테스트
uv run --directory services/api pytest -q -m "not live"   # 이 서비스만
make test-live                                         # live 테스트(OPENAI_API_KEY 필요, 비용 발생)
```

<!-- gen:tests -->
> 생성 블록이다. 코드를 바꾸면 `make docs-gen`으로 갱신한다.

총 226개(live 3개 포함, `make test`는 live를 뺀다).

<details><summary><code>tests/agent/test_assistant.py</code> (22)</summary>

- `test_model_picks_tools_and_cites`
- `test_tool_calls_capped_at_three`
- `test_locked_materials_answer_fixed_sentence`
- `test_guard_replaces_with_notice`
- `test_llm_fake_mode_assistant`
- `test_internal_material_ids_are_not_shown` ×4
- `test_hide_ids_touches_only_known_id_notation` ×12
- `test_ids_hidden_with_open_and_locked_lookups`

</details>

<details><summary><code>tests/agent/test_cards.py</code> (13)</summary>

- `test_c5_lead_lock_joint_delivery_and_observations`
- `test_c5_api_lock_is_used`
- `test_c5_dropped_card_speaker_resumes_next_run`
- `test_c5_critic_moves_after_two_counters_stubborn_holds`
- `test_critic_does_not_move_after_one_counter`
- `test_card_due_keyword_critic_pushes_own_keyword`
- `test_card_due_keyword_stubborn_when_candidate_backs_wind`
- `test_inclusion_silence_fixed_line_without_llm`
- `test_persuasion_solution_swaps_actor_not_stance`
- `test_honesty_credit_observed_on_proposal_start`
- `test_moved_critic_must_record_support_for_candidate_option`
- `test_moved_critic_support_overrides_structured_oppose` ×2

</details>

<details><summary><code>tests/agent/test_intent.py</code> (10)</summary>

- `test_fixture_20_schema_and_confidence`
- `test_catalog_option_first_mentioned_is_key`
- `test_message_phase_catalog_is_used`
- `test_failure_falls_back_to_other` ×4
- `test_message_id_is_remembered`
- `test_live_accuracy_baseline` (live)
- `test_only_one_target_field_is_kept`

</details>

<details><summary><code>tests/agent/test_live.py</code> (2)</summary>

- `test_live_persona_reply` (live)
- `test_live_intent_one` (live)

</details>

<details><summary><code>tests/agent/test_llm.py</code> (7)</summary>

- `test_role_models_and_temperature`
- `test_missing_key_is_config_error`
- `test_wrapper_passes_role_temperature`
- `test_llm_fake_needs_no_key`
- `test_agent_runner_default_is_session`
- `test_fake_intent_rules`
- `test_llm_fake_mode_is_deterministic`

</details>

<details><summary><code>tests/agent/test_min_path.py</code> (3)</summary>

- `test_named_seat_responds_with_typing_and_stance`
- `test_stale_phase_is_skipped`
- `test_debate_state_unavailable_fails_run`

</details>

<details><summary><code>tests/agent/test_openai_payload.py</code> (1)</summary>

- `test_request_shapes_per_role`

</details>

<details><summary><code>tests/agent/test_persona.py</code> (26)</summary>

- `test_offplan_regenerates_then_fallback` ×5
- `test_offplan_regenerated_ok`
- `test_guard_regenerates_then_safe_phrase` ×4
- `test_guard_allow_exact_notice_passes`
- `test_cost_cap_uses_safe_phrase_without_llm`
- `test_persona_context_snapshot`
- `test_persona_scope_has_no_assessment`
- `test_material_question_before_keyword_gets_not_released`
- `test_resolve_target_maps_catalog_label_to_key`
- `test_guard_blocks_credit_preemption`
- `test_credit_group_only_in_proposal_and_narrow`
- `test_decide_question_gets_fixed_notice_first`
- `test_repeated_line_is_regenerated_once`
- `test_decide_question_is_answered_even_if_turn_llm_picks_nobody`
- `test_long_body_is_cut_before_checks_so_notice_fits`
- `test_notice_is_attached_to_safe_phrase`
- `test_repeat_regeneration_shares_the_tool_budget`
- `test_regeneration_paths_share_the_tool_budget` ×2

</details>

<details><summary><code>tests/agent/test_script.py</code> (1)</summary>

- `test_fifteen_minute_script`

</details>

<details><summary><code>tests/agent/test_turn.py</code> (21)</summary>

- `test_phase_started_keyword_pushers_and_problem_react`
- `test_proposal_phase_started_has_no_speakers`
- `test_named_reply_first_and_at_most_two`
- `test_named_order_follows_the_message_not_the_classifier`
- `test_quiet_only_when_called_or_asked_all`
- `test_turn_llm_only_for_message`
- `test_turn_llm_failure_means_no_speakers`
- `test_silence_invite_once_by_critic`
- `test_consecutive_three_blocks_turn_llm_choice`
- `test_existing_consensus_fills_without_respond`
- `test_ai_reply_creates_consensus_in_same_run`
- `test_fill_slot_conflict_saves_nothing`
- `test_no_fill_without_consensus`
- `test_same_run_id_rerun_converges`
- `test_candidate_typing_defers_and_requeues`
- `test_generation_change_drops_remaining`
- `test_stale_message_generation_is_skipped`
- `test_persona_llm_failure_skips_speaker`
- `test_trace_and_decision`
- `test_unexpected_error_after_respond_stops_typing`
- `test_llm_fault_persona_timeout_skips_one_speaker`

</details>

<details><summary><code>tests/test_c01_confirm.py</code> (10)</summary>

- `test_keyword_confirm_frames_support_and_cumulative_deadline`
- `test_confirm_rejects_unknown_keyword_and_other_phase`
- `test_problem_confirm_links_by_key_or_label_else_null_without_llm`
- `test_concurrent_confirms_only_one_wins`
- `test_confirm_with_three_supporters_is_consensus`
- `test_outcome_is_candidate_decision_below_three_supporters_of_confirmed`
- `test_ai_message_committed_before_confirm_is_sent_before_confirm_frames`
- `test_start_committed_before_confirm_keeps_frame_and_generation_order`
- `test_slow_socket_does_not_block_writes_or_other_sessions`
- `test_hub_drop_on_send_timeout_records_connection_gap`

</details>

<details><summary><code>tests/test_c02_consensus_fill.py</code> (4)</summary>

- `test_fill_in_same_run_after_candidate_agrees`
- `test_fill_after_ai_stance_in_same_run`
- `test_withdrawn_support_is_no_consensus_and_saves_nothing`
- `test_fill_checks_phase_then_slot_then_consensus`

</details>

<details><summary><code>tests/test_c03_timeout.py</code> (2)</summary>

- `test_timeout_fills_most_supported_option_without_graph`
- `test_timeout_without_options_uses_default_keyword`

</details>

<details><summary><code>tests/test_c04_silence_invite.py</code> (2)</summary>

- `test_idle_deferred_then_reenqueued_and_invite_once`
- `test_idle_not_enqueued_while_candidate_typing_or_pending`

</details>

<details><summary><code>tests/test_c06_submit.py</code> (7)</summary>

- `test_submit_completes_and_blocks_later_writes`
- `test_auto_submit_at_ends_at`
- `test_submit_saves_blank_sections_and_warning`
- `test_facts_expose_blank_sections`
- `test_record_summary_counts_blank_sections`
- `test_manual_submit_without_flag_saves_false_and_stays_on_retry`
- `test_auto_submit_without_blanks_keeps_values_on_manual_retry`

</details>

<details><summary><code>tests/test_c08_persona_hidden.py</code> (1)</summary>

- `test_candidate_responses_and_frames_hide_persona_type`

</details>

<details><summary><code>tests/test_c09_entry.py</code> (2)</summary>

- `test_entry_consent_prestart_state_start_resume_and_debrief`
- `test_start_refused_when_position_scenario_copy_lacks_catalog`

</details>

<details><summary><code>tests/test_c10_review.py</code> (6)</summary>

- `test_review_adjusts_one_dimension_and_final_keeps_draft_hash`
- `test_candidate_list_chips_merge_adjustments_without_total`
- `test_report_get_separates_missing_generating_and_failed`
- `test_finalize_failure_regenerates_by_resuming_same_round_approve`
- `test_regenerate_hides_previous_draft_until_new_draft`
- `test_regenerate_refused_when_report_failed_for_missing_scenario_copy`

</details>

<details><summary><code>tests/test_consensus.py</code> (4)</summary>

- `test_three_of_four_with_candidate_is_consensus_immediately`
- `test_ai_only_majority_is_not_consensus`
- `test_support_for_other_option_replaces_and_later_oppose_withdraws`
- `test_timeout_selection_most_then_candidate_then_first_then_default`

</details>

<details><summary><code>tests/test_foundation.py</code> (3)</summary>

- `test_fake_runner_posts_with_typing_and_generation`
- `test_intent_stub_returns_contract_fallback`
- `test_role_models_use_new_env_names`

</details>

<details><summary><code>tests/test_health.py</code> (1)</summary>

- `test_public_and_internal_health`

</details>

<details><summary><code>tests/test_internal.py</code> (5)</summary>

- `test_session_messages_tail_and_rubric_window`
- `test_options_idempotent_and_candidate_first_proposer_kept`
- `test_observation_idempotent_and_writes_need_in_progress`
- `test_save_evaluation_validates_and_is_idempotent`
- `test_facts_and_record_refs`

</details>

<details><summary><code>tests/test_lifecycle.py</code> (8)</summary>

- `test_start_creates_slots_draft_notice_and_phase_started`
- `test_cancel_and_fail_record_ended_at`
- `test_report_failure_regenerates_with_new_round_when_evaluation_failed`
- `test_reissue_swaps_token_and_retake_reuses_alias`
- `test_admin_queries_are_company_scoped_and_catalog_has_no_selection`
- `test_materials_locked_until_keyword_and_seeded_by_catalog_version`
- `test_assistant_limit_and_cost_go_to_debate_budget`
- `test_purge_deletes_ended_sessions_after_90_days`

</details>

<details><summary><code>tests/test_mailbox.py</code> (11)</summary>

- `test_messages_run_serially_and_every_message_is_classified`
- `test_older_message_is_skipped_when_newer_saved`
- `test_message_from_previous_phase_uses_its_own_phase_catalog`
- `test_post_with_old_generation_is_stale`
- `test_finished_run_is_not_rerun_and_stale_started_run_is`
- `test_recover_requeues_queued_and_started_runs_with_same_id`
- `test_ports_enqueue_makes_new_queued_run`
- `test_candidate_typing_expires_after_six_seconds`
- `test_classifier_failure_is_other_and_low_confidence_adds_no_stance`
- `test_three_persona_llm_failures_fail_the_session`
- `test_missing_scenario_copy_fails_session_without_running`

</details>

<details><summary><code>tests/test_scenario.py</code> (6)</summary>

- `test_fixed_seats_cards_dimensions_and_neutral_avatars`
- `test_scenario_copies_evaluator_prompts_and_forbidden_patterns`
- `test_fifteen_minute_offsets_scale_with_20_second_floor`
- `test_cumulative_phase_deadlines` ×2
- `test_catalog_reads_position_snapshot_not_current_files`

</details>

<details><summary><code>tests/test_scheduler.py</code> (6)</summary>

- `test_time_warning_once_before_deadline`
- `test_card_due_reenqueued_with_new_run_until_delivered`
- `test_card_due_not_duplicated_while_pending`
- `test_problem_cards_lock_lead_and_joint_message_delivers_both`
- `test_recover_fills_missed_deadline_and_republishes`
- `test_card_due_retries_are_spaced_and_capped`

</details>

<details><summary><code>tests/test_schema.py</code> (2)</summary>

- `test_schema_matches_data_model_and_models`
- `test_debate_constraints`

</details>

<details><summary><code>tests/test_stage_seeds.py</code> (7)</summary>

- `test_seed_state_is_stage_start` ×4
- `test_ended_seed_is_completed`
- `test_advance_deadline_fills_slot_by_timeout`
- `test_script_args_parse`

</details>

<details><summary><code>tests/test_test_api.py</code> (7)</summary>

- `test_seed_replays_to_phase_start` ×4
- `test_ended_seed_submits_and_publishes`
- `test_advance_to_deadline_and_card`
- `test_test_api_is_404_when_disabled`

</details>

<details><summary><code>tests/test_voice.py</code> (22)</summary>

- `test_voice_playback_barrier_and_single_message`
- `test_voice_heard_ids_follow_playback_end`
- `test_voice_plays_first_ready_while_next_is_made`
- `test_voice_normal_turn_waits_for_press`
- `test_voice_rerecord_once_with_fresh_time`
- `test_voice_zero_speakers_stale_and_audio_validation`
- `test_voice_quota_refuses_before_provider_and_other_session`
- `test_pcm_rejects_truncated_and_long_audio`
- `test_azure_paid_sku_rejected_before_synthesis`
- `test_azure_fast_transcription_request`
- `test_live_cut_lands_in_pause`
- `test_live_segments_then_tail_in_order`
- `test_live_gap_or_length_mismatch_falls_back`
- `test_voice_stream_chunks_used_for_transcript`
- `test_voice_playback_end_after_phase_change_counts_as_heard`
- `test_voice_hand_raise_takes_turn_after_current`
- `test_voice_hand_raise_keeps_card_utterance`
- `test_voice_hand_turn_not_stolen_and_stale_not_replayed`
- `test_voice_forced_turn_before_phase_ends`
- `test_voice_short_turn_near_deadline_is_announced`
- `test_voice_recording_cut_by_deadline_still_submits`
- `test_voice_no_forced_turn_after_candidate_spoke`

</details>

<details><summary><code>tests/test_ws_close.py</code> (4)</summary>

- `test_rejected_socket_gets_close_code_after_accept` ×2
- `test_replacing_stuck_socket_lets_new_socket_receive_frames`
- `test_disconnect_and_send_timeout_clear_writer_queue_and_socket`

</details>
<!-- /gen:tests -->

## 관련 문서

- [`docs/services/api.md`](../../docs/services/api.md): 모듈 구성과 처리 흐름
- [`docs/architecture/session-lifecycle.md`](../../docs/architecture/session-lifecycle.md): 세션 상태와 전이
- [`docs/architecture/agents.md`](../../docs/architecture/agents.md): 턴 매니저·페르소나·어시스턴트
- [`docs/architecture/security.md`](../../docs/architecture/security.md): 인증과 권한 경계
- [`docs/guides/configuration.md`](../../docs/guides/configuration.md): env와 실행 모드
- [`docs/guides/testing.md`](../../docs/guides/testing.md): 테스트 종류와 실행법
