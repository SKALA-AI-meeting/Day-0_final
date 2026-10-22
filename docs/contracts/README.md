# 인터페이스 계약

음성 턴·최종 파일·재생 확인·message.send 확장은 [음성 계약](voice.md)을 따른다.

서비스끼리 주고받는 경로·필드·오류 코드·이벤트의 정본이다. 이 폴더의 예시 JSON은 mock 서버와 테스트 픽스처(`tests/fixtures/contracts/`, 사본 `apps/web/mocks/fixtures/`)의 정본이다. 단, **요소·카드·페르소나 유형·평가표 차원(`dimension_id`)의 id는 문자열로만 받고 실제 목록은 [평가표](../product/rubric.md)(BARS 척도, 차원 19개)·[시나리오 모델](../product/scenario.md)이 정본**이다. 예시의 `persuasion`·`stubborn`·`listen_engage` 같은 값은 자리표시다.

| 문서 | 내용 |
|---|---|
| 이 문서 | 공통 규칙(베이스 URL·오류·CORS·인증 헤더·시각·멱등), 칸 객체와 표시 규칙, 인증 생명주기 |
| [api.md](api.md) | 공개 REST: 관리자(`/admin`)·지원자(`/s`, `/sessions`), Report·Proposal·State 스냅샷 |
| [ws.md](ws.md) | WebSocket 프레임 |
| [internal.md](internal.md) | 내부 REST(`/internal`), DebateState·Contributions·SessionRecord·EvaluationSave, 의도 분류·턴 매니저 결정 스키마 |
| [mcp.md](mcp.md) | MCP 도구 3서버와 도구 허용 목록 |
| [queue.md](queue.md) | RabbitMQ 큐 메시지 |

세션 상태 머신, 합의 판정·시간 초과·합의 채우기의 트랜잭션, 그래프 자극과 세션 메일박스는 [세션 생명주기](../architecture/session-lifecycle.md)가 정본이다. 계약을 바꾸는 절차는 [기여 가이드](../guides/contributing.md) "계약 변경".

## 공통

- 공개 베이스: `http://api:8000` (web이 호출). 내부 베이스: `http://api:8001` (`/internal/*`만, compose 네트워크 안에서만 열림, 포트 미공개).
- 응답 JSON. 오류는 `{"error": {"code": "...", "message": "..."}}`와 HTTP 4xx/5xx. **입력 검증 실패는 모두 422**이고 `code`는 `invalid_input` 또는 그 세부 코드(`invalid_keyword`·`invalid_dimension`·`memo_required`·`duplicate_dimension`·`invalid_previous_session`)다(400은 쓰지 않는다). 상태 충돌은 409이고 `code`는 `conflict` 또는 세부 코드다: `phase_not_open`(현재 단계가 아님), `slot_confirmed`(칸이 이미 확정), `no_consensus`(합의 판정이 그 안이 아님), `stale_generation`(게시 시점 generation 불일치), `session_ending`(제출·종료 뒤 쓰기), `already_reviewed`(검토 뒤 다른 본문), `draft_mismatch`(final 해시가 draft와 다름). 그 밖의 상태 충돌은 `conflict`. 리포트 조회 전용으로 `report_not_ready`(생성 중)·`report_failed`(생성 실패)가 있다([api.md](api.md)). 권한 401·403, 없음 404, 만료·취소 410, 잠김 423. error 객체에는 code·message 외에 엔드포인트가 정한 추가 필드가 올 수 있다(예: `report_failed`의 `failed_stage`·`failed_step`).
- CORS: 공개 앱(8000)만 `WEB_ORIGIN`(쉼표 구분, 기본 `http://localhost:3000`)을 허용한다. 메서드 전부, 헤더 `Authorization`·`Content-Type`. 내부 앱(8001)에는 두지 않는다.
- 인증 헤더: `Authorization: Bearer <jwt>`. 내부: `X-Internal-Token: <secret>`. assessment-mcp: `X-Assessment-Token: <secret>`.
- 시각은 ISO 8601 UTC. 세션 상대 시각은 `t_min`(세션 시작 기준 분, 소수 허용)으로도 함께 준다.
- 오피스 시각: `오피스 시각 = 09:00 + t_min × 60 / session_minutes`(분 단위 내림, "HH:MM"). 60분 세션은 09:00~10:00, 데모 15분도 같은 비율. API는 `t_min`과 `session_minutes`를 주고 표시 변환은 web이 한다. 지원자 화면은 남은 시간을 중심으로 보이고 오피스 시각은 메시지 타임스탬프에만 쓴다(같은 줄에 함께 쓰지 않음).
- 세션 길이 `session_minutes` ∈ {60, 15}. 15분은 데모·e2e용 압축이고 모든 시각 값은 같은 비율로 환산한다(카드·유휴 오프셋은 최소 20초).
- 참여자 id: `candidate`, `p1`·`p2`·`p3`(AI 동료 자리), `system`. **공개 API와 WS에는 자리 id와 표시 이름만 나가고 페르소나 유형은 나가지 않는다.** 자리 → 유형 대응은 `positions.scenario.seats`에 있고 관리자 리포트·디브리핑·내부 API만 본다.
- 표시 이름·프로필(D13-3): 참여자·자리 객체는 `display_name`과 `avatar_url`을 가진다. 지원자는 `display_name`만(`sessions.display_name`, 동의 때 입력, 비우면 초대 별칭)이고 화면은 이니셜 아바타를 쓴다. AI 자리의 `display_name`은 `positions.scenario.seats[].name`, `avatar_url`은 같은 자리의 `avatar`(카탈로그 `personas.yaml`의 `avatar`, 파일은 `scenarios/missions/<id>/assets/avatars/`)로 만든 `/assets/missions/<mission_id>/avatars/<file>`이다. 이 경로는 공개 앱(8000)이 읽기 전용으로 서빙한다. 이미지는 임시이며 교체는 에셋 파일과 `avatar` 경로만 바꾼다. 관리자 리포트·목록은 별칭(`alias`)을 기본으로 쓴다.
- 단계 id: `keyword`, `problem`, `solution`, `proposal`. 앞 셋이 칸(slot)을 가진 토론 단계다.
- 멱등: `Idempotency-Key` 헤더는 쓰지 않는다. 도메인 규칙과 아래 저장 키로만 보장하며 같은 키의 재요청은 기존 행을 돌려준다.

| 저장 | 멱등 키 |
|---|---|
| 지원자 메시지 | (session_id, client_msg_id) |
| AI·시스템 메시지 | (session_id, run_id, seq) |
| 의도 분류 | message_id |
| 안(option) | 시나리오 안은 (session_id, phase, scenario_option_key), 자유 안은 (session_id, phase, source_message_id). 같은 시나리오 안을 누가 다시 제안해도 기존 행을 돌려준다(최초 제안자 보존) |
| 입장(stance) | (session_id, source_message_id, participant, option_id) |
| 칸 확정 | (session_id, phase). 이미 confirmed면 같은 run_id 재요청은 기존 칸, 다르면 409 `slot_confirmed` |
| 합의 채우기(`fill_slot`) | 칸 확정과 공지 메시지가 한 트랜잭션. 공지는 (session_id, run_id, seq 5), `meta.kind = "consensus_fill"`. 같은 run_id 재호출은 단계가 이미 넘어갔어도 기존 칸·공지를 돌려준다 |
| 시간 초과 채우기 | (session_id, phase). 공지 메시지는 run_id = `uuid5(session_id, "timeout:"+phase)`, seq 0 |
| 카드 관찰 | (session_id, card_id) |
| 카드 진행(`session_cards`) | (session_id, card_id). `delivered_seats`는 자리 집합(중복 추가 무시), `locked_option_id`는 처음 한 번만 |
| 평가 실행(`save_evaluation`) | (session_id, run_id). 같은 run_id의 `evaluation_runs` 행이 있으면 저장하지 않고 기존 결과 반환(재시도 이어하기·리포트 재생성 재사용). 차원 행 unique는 (session_id, dimension_id, run_id) |
| 담당자 조정(review) | 세션 상태. approving 이후 같은 본문 재요청은 200, 다르면 409 |
| 기획안 제출 | 세션 상태. completed 이후 재호출은 저장된 값 |

### 칸(slot) 상태

```
empty ──지원자 확정 / AI 합의 채우기 / api 시간 초과 채우기──▶ confirmed
```

칸 상태는 두 개뿐이다. 지원자 초안 저장(`PUT slots`)·`filled` 상태·WS `slot.filled`는 없다(입력 중 값은 화면에만 있다).

| 필드 | 값 |
|---|---|
| status | `empty` ∣ `confirmed` |
| value | 확정 값(짧은 문장) |
| option_id | 확정 값과 같은 안. 지원자 확정 값이 어느 안과도 연결되지 않으면 null |
| filled_by | `candidate` ∣ `p1`∣`p2`∣`p3` (시간 초과 채우기도 공지한 자리 id. `system` 값은 없다) |
| resolution | `consensus` ∣ `timeout` |
| support | 확정 순간의 지지 현황 스냅샷 `[{option_id, supporters: [participant]}]` |
| confirmed_at, t_min | 확정 시각 |

- 지원자는 언제든 확정할 수 있다(혼자 확정 포함). 확정 순간의 지지 현황이 `support`에 남아 분석에서 보인다.
- keyword 칸은 버튼으로 후보 키(`option_key`, 예: `water`)를 보내 확정한다. 후보 밖 키면 422 `invalid_keyword`. 그 키의 안 행이 없으면 api가 만든다(proposer = candidate, source_message_id null).
- 문제 상황·해결책 칸의 지원자 확정 값은 **결정적 규칙으로만** 안에 연결한다(확정 경로에 LLM 없음): ① 요청의 `option_key`가 그 단계의 기존 안 행의 `scenario_option_key`와 같거나 ② `value`를 정규화(위치와 관계없이 구두점 전부 제거, 연속 공백을 하나로, 앞뒤 공백 제거, 소문자. 예: `도심 폭우·침수` = `도심 폭우침수`)한 문자열이 그 단계 기존 안 라벨의 정규화 값과 같으면 그 안. 둘 다 아니면 `option_id=null`이고 새 안을 만들지 않는다(최초 제안자를 잘못 귀속하지 않기 위해).

### 지원자 문구 (R7)

지원자 화면 문구는 내부 용어 대신 지원자 말을 쓴다: 칸 → "정할 항목", 합의 패널 → "결정 사항", 확정 → "이걸로 정하기". API 필드 이름(`slot`·`confirm`)은 그대로다.

칸 결과 표시(지원자 화면·관리자 리포트 공통): api가 칸마다 표시용 `outcome`(`consensus`∣`candidate_decision`∣`timeout`∣확정 전 null)을 계산해 `/state`의 `slots`, 칸 확정 응답의 `slot`, WS `slot.confirmed`, 리포트 `slots`에 함께 내린다(열로 저장하지 않고 확정 값에서 계산). `resolution: timeout`이면 `timeout`("시간 초과"). `resolution: consensus`이면 확정 시점 `support` 스냅샷에서 확정 안 지지자가 참여자 4명 중 3명 이상이면 `consensus`("합의"), 아니면(지원자 혼자 확정 등) `candidate_decision`("지원자 결정"). AI 합의 채우기(`fill_slot`)는 항상 `consensus`다. 화면은 `outcome`만 보고 지지 현황(`support`)은 계속 지원자 화면에 내려가지 않는다. 데이터 값(`resolution`)은 바꾸지 않는다.

## 인증 생명주기

| 단계 | 호출 | 상태 | 토큰 |
|---|---|---|---|
| 초대 생성 | 관리자 POST /admin/positions/{id}/invites | sessions 행이 `invited`로 함께 생성됨 | 초대 토큰(링크에만, DB에는 해시) |
| 링크 입장 | GET /s/{token} | 응답에 `session_id`·`default_display_name`(초대 별칭, 표시 이름 기본값) 포함. 시작 전 만료(72h) → 410 `expired`, 취소 → 410 `cancelled`. completed 이후·failed → 200에 `status`·`session_id`·`debrief_token`(디브리핑·제출본 열람·설문만, `ended_at` 후 7일). 입장 화면 고정 안내(`entry_notice`, 문구는 PRD): 60분 연속 진행·중간 휴식 없음·PC 권장·AI 동료 3명과 채팅으로 키워드 → 문제 상황 → 해결책을 정하고 기획안을 혼자 써서 제출·**토론 결과가 내 제안과 다를 수 있고 안의 질이 아니라 과정의 태도를 본다**·끊겨도 시간은 흐름·외부 도구는 막지 않되 안의 AI 어시스턴트 요청·응답은 사실로 기록되고 AI가 평가 수준을 매기지 않음·문의 창구 | 없음 |
| 동의 | POST /s/{token}/consent {display_name?} | `consented_at`·`sessions.display_name` 기록(비우면 별칭, 시작 뒤에는 바꾸지 않음) | `{session_token, session_id}`. 세션 JWT 만료 = now + session_minutes + 120분 |
| 시작 전 조회 | GET /sessions/{id}/state (Bearer) | 동의 뒤 시작 전(status invited)에도 허용: 참여자 이름·아바타, 단계 예산, 빈 칸·빈 메시지(시계 값은 null) | 같은 JWT |
| 시작 | POST /s/{token}/start (Bearer) | `in_progress`, `phase=keyword`, started_at·ends_at·phase_deadline_at 확정, 칸 3행(empty)·기획안 초안 행 생성, 첫 시스템 안내 메시지 저장, `phase_started` 자극 enqueue. 시작 시점에 초대가 만료됐으면 410 `expired` | 같은 JWT |
| 재입장 | GET /s/{token} → invited면 consent 재호출, in_progress면 POST /s/{token}/resume, completed 이후면 응답의 `session_id`·`debrief_token`으로 `/sessions/{id}/debrief` | 변화 없음 | resume 응답 `{session_token, session_id}` |
| 재발급 | 관리자 POST /admin/sessions/{id}/reissue | 시작 전(invited, 만료 포함) 세션의 초대 토큰·만료만 교체. 같은 세션·같은 별칭. 옛 링크는 404 `not_found` | 새 초대 토큰 |
| 종료 | 제출 또는 세션 만료 자동 제출 → 같은 요청 안에서 `completed`(+`ended_at`). 자동 제출이면 `proposals.auto_submitted=true`, WS `session.ended{reason: timeout}` | `completed` | JWT는 디브리핑 조회까지 유효 |

검증: 다른 세션 id 접근 403, 동의 중복은 멱등, 시작 후 동의 409, 시작 전 JWT 만료는 consent 재호출, 시작 후 만료는 resume. 재응시(세션 실패·취소·시작 전 만료 뒤 다시 보게 함)는 새 초대·새 세션이고(`previous_session_id`), 재발급은 같은 세션이다.
