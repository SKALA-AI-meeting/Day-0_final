# 테스트 픽스처

`contracts/`는 `docs/contracts/api.md`의 계약 예시를 **한 세션**으로 채운 것이다: `sess-1`, 60분, 5요소 고정, 자리 `p1` 강한결(고집형)·`p2` 윤하람(소극형)·`p3` 서지안(비판형), 지원자 표시 이름 김지원(초대 별칭 지원자A). 서비스 사이(MCP·api·워커·web mock)가 같은 예시를 공유한다. `apps/web/mocks/fixtures/`에는 같은 이름·같은 내용으로 복사한다(web 전용 재생 대본 `debate_script.json`·`office_script.json`은 제외). 계약 예시가 바뀌면 두 곳을 함께 갱신하고 `apps/web`의 prettier(`.prettierrc.json`)로 포맷한다.

형태 검사 기준은 `tests/contract_shape.py`의 `SHAPES`(필드·값 어휘)이고, `tests/test_contract_fixtures.py`가 모든 픽스처·web 복사본 일치·WS `seq` 연속·19차원·페르소나 유형 비노출을 검사한다.

```
uv run --with pytest pytest tests/test_contract_fixtures.py -q
```

생산자 테스트는 `contract_shape.py`를 파일 경로로 import해 `assert_contract(응답, "<픽스처 이름>")`으로 같은 형태를 검사한다.

## 세션 흐름(파일 사이 id가 같은 행을 가리킨다)

| 단계 | 시각(t_min) | 내용 |
|---|---|---|
| keyword | 0~8.2 | p3 바람·p1 불 제안, 카드 `persuasion_keyword`(3.0, p3), 침묵 초대(3.2, p3), 지원자 물 제안(`origin: invited`), p3는 끝까지 바람이라 합의 없음(물 지지 2/4), 지원자 버튼 확정(8.2, `consensus`·`candidate`) |
| problem | 8.2~25.4 | 지원자 P1 제안, 카드 `flexibility_majority`·`listening_pressure` 공동 전달(11.2, `m-13` `card_ids` 2개, 기준안 `opt-pb-p2` 잠금·제안자 p1), 압박과 응답, 지원자 동의로 3/4 합의 → p3 `fill_slot` 공지(`m-19`, seq 5, `consensus_fill`) |
| solution | 25.4~46 | 카드 `inclusion_silence`(27.4, p2)·`persuasion_solution`(30.4, p3), 지원자가 윤하람 초대, 시간 초과 채우기(46.0, 지원자가 지지한 S1을 `candidate_support` 규칙으로, 시스템 안내 `timeout_fill`, `timeout`·`filled_by: system`) |
| proposal | 46~57 | `honesty_credit` 관찰(46.0), 연결 끊김 51.0~51.6, 제출 57.0 |

id: 메시지 `m-01~31`(어시스턴트 채널 `ma-1·2`), 안 `opt-kw-*`·`opt-pb-*`·`opt-sl-*`, 관찰 `ob-1~6`, 지원자 `client_msg_id` `c-01~10`, run `run-*`, 평가 실행 `run-eval-sess-1-r1`, 어시스턴트 `ac-1`. 다른 세션 행(`sess-2~7`)은 관리자 목록·초대 예시에만 있다.

## 지원자 REST

| 파일 | 계약 |
|---|---|
| `entry.json`, `entry_completed.json` | GET /s/{token} (invited, completed + `debrief_token`) |
| `consent_request.json`, `consent.json`, `resume.json` | POST /s/{token}/consent 요청·응답, POST /s/{token}/resume 응답 |
| `start.json` | POST /s/{token}/start 응답 |
| `state_pre_start.json` | 시작 전 State(동의 뒤, 시계 null, 빈 칸) |
| `state_keyword.json`·`state_problem.json`·`state_solution.json`·`state_proposal.json` | 단계별 State(1.6·14.0·32.0·52.0분). `ws_seq`는 `ws_events.json`의 그 시각까지 마지막 seq |
| `message.json` | Message (`m-13`) |
| `slot_confirm_request.json`, `slot_confirm.json` | POST .../slots/keyword/confirm 요청·응답 |
| `materials_locked.json`, `materials.json`, `material.json` | GET .../materials(잠김·열림), GET .../materials/{id} |
| `assistant_request.json`, `assistant_response.json` | POST .../assistant (`tool_calls` 포함) |
| `proposal.json`, `proposal_put_request.json`, `proposal_put.json`, `proposal_submit.json` | Proposal(제출본), PUT 요청·응답, 제출 응답 |
| `debrief.json`, `debrief_failed.json` | GET .../debrief (정상, `failed_stage=session`) |
| `debrief_survey_request.json`, `debrief_survey.json` | POST .../debrief/survey 요청(`free_comment`)·응답 |
| `error.json` | 공통 오류 형식 |

## WebSocket

| 파일 | 계약 |
|---|---|
| `ws_events.json` | 서버 프레임 `{type, seq, payload}` 세션 전체(seq 1부터 연속): 시작, ack, message.new, typing, slot.confirmed(consensus 2·timeout 1), phase.changed, timer, materials.opened, session.ended |
| `ws_client_frames.json` | 클라이언트 message.send·typing |
| `ws_error.json`, `ws_session_replaced.json` | 오류 프레임, 연결 교체 |

## 관리자 REST

| 파일 | 계약 |
|---|---|
| `admin_auth_token.json` | signup·login 응답 |
| `admin_catalog.json` | GET /admin/catalog (요소 안내 5개, 선택 필드 없음) |
| `admin_positions.json`, `admin_position_request.json`, `admin_position_created.json` | 채용 건 목록, 생성 요청·응답 |
| `admin_candidates.json` | 지원자 목록(상태 묶음 5종, 수준 칩·조정 칩, 재발급 `reissued_at`, 실패, 재응시 `previous_session_id`) |
| `admin_invite_request.json`, `admin_invite.json`, `admin_reissue.json` | 초대 요청·응답, 재발급 응답 |
| `admin_cancel.json`, `admin_report_regenerate.json` | 취소, 재생성 응답 |
| `report.json` | Report draft (요소 5행·차원 19행, 근거 4종·앞 5/뒤 3 맥락) |
| `admin_review_request.json`, `admin_review_response.json` | 검토(차원 조정 2건, `reviewer_name`) |
| `admin_report_notes_request.json`, `admin_report_notes.json` | 메모 요청·응답 |
| `admin_record.json` | 전체 기록(`ref`는 Report 근거의 `record_ref`와 같은 값) |

## 내부 REST

| 파일 | 계약 |
|---|---|
| `internal_session.json` | GET /internal/sessions/{id} (14.0분, `positions.scenario` 포함) |
| `internal_debate_state.json` | DebateState (14.0분, `card_progress`·`cost_left`·`generation`) |
| `internal_messages.json` | GET /messages (problem 단계, `meta`·`origin`·`intent`·`card_ids`) |
| `internal_message_post_request.json`, `internal_message_post.json` | POST /messages (카드 공동 전달) |
| `internal_option_request.json`, `internal_option.json`, `internal_stance_request.json`, `internal_stance.json` | POST /options, /stances |
| `internal_slot_fill_request.json`, `internal_slot_fill.json` | POST /slots/fill (합의 채우기) |
| `internal_proposal.json`, `internal_materials.json`, `internal_material.json` | 제출본, 자료 목록·본문 |
| `internal_observation_request.json`, `internal_observation_created.json`, `internal_observations.json` | 카드 관찰 |
| `internal_transcript_span.json`, `internal_contributions.json`, `internal_rubric.json`, `internal_facts.json`, `internal_slots.json`, `internal_assistant_calls.json` | 근거 맥락, 기여 대조, BARS 척도(19차원), 사실 지표, 칸, 어시스턴트 |
| `session_record.json` | SessionRecord (`get_session_record`) |
| `evaluation_save.json`, `internal_evaluations_saved.json`, `internal_evaluations.json` | EvaluationSave(`prompt_versions` 공통+5요소), 저장 응답, GET /evaluations(evaluation_runs + evaluations) |
| `internal_report_draft.json`, `internal_report_final.json`, `internal_report_failed.json` | 워커 저장 응답 |
| `internal_test_session_request.json`, `internal_test_session.json` | e2e 세션 생성 |

## 의도 분류·MCP·큐

| 파일 | 계약 |
|---|---|
| `intent_classification.json`, `intent_classification_fallback.json` | 의도 분류 출력(`m-06`), 실패 폴백 |
| `mcp_ok.json`, `mcp_error.json` | MCP 공통 응답(409 `no_consensus` 전달) |
| `mcp_tools.json` | debate·rag·assessment 도구별 입력·출력 data(문자열이면 같은 폴더의 그 픽스처) |
| `queue_messages.json` | report.generate·approve·dlq 본문 |
