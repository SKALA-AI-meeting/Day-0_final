# 공개 REST (포트 8000)

음성 턴·최종 파일·재생 확인·message.send 확장은 [음성 계약](voice.md)을 따른다.

공통 규칙(오류 형식·인증 헤더·멱등)은 [계약 README](README.md). WebSocket은 [ws.md](ws.md).

## 관리자 REST (`/admin`, 포트 8000)

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| POST | /admin/auth/signup | {email, password, company_name} | {token}. 이미 있는 이메일 409 `conflict` |
| POST | /admin/auth/login | {email, password} | {token}. 실패 401 `unauthorized` |
| GET | /admin/catalog | | {elements: [{id, name, meaning, how_we_see}], session_minutes_options: [60, 15], default_session_minutes: 60, candidate_notice_template, candidate_faq: [{q, a}]}. 요소 카드 5장(이름·한 줄 뜻·"이렇게 봅니다")은 **안내용**이다. 모든 채용 건이 5요소를 본다(선택 기능 없음). 페르소나 유형·카드·평가표 차원은 보여 주지 않는다 |
| GET | /admin/positions | | [{id, name, session_minutes, candidate_count, created_at}] |
| POST | /admin/positions | {name, session_minutes?: 60∣15, mission_id?: "debate"} | {id, name, session_minutes, seats: [{seat, name, persona_type, avatar_url}], keywords: [string], catalog_version}. 요소 선택 입력은 없다. 자리 3개는 고정(`p1` 고집형·`p2` 소극형·`p3` 비판형, 이름·아바타는 [product/scenario.md](../product/scenario.md))이고 5요소·카드 6장을 포함한 시나리오 전체를 `positions.scenario`에 복사·고정. 수정·삭제 없음 |
| GET | /admin/positions/{id}/candidates | | [{session_id, alias, email, status, display_status, display_group, phase?, invited_at, started_at?, ends_at?, ended_at?, last_activity_at?, message_count, report_ready_at?, purge_at?, failed_stage?, failed_reason?, next_action?, previous_session_id?, reissued_at?, elements?: [{element, level: 1~5∣null, adjusted: bool}], low_confidence_count?, level_source?: "auto"∣"reviewer"}]. display_group ∈ waiting∣in_progress∣needs_review∣done∣problem. purge_at = ended_at + 90일(세션이 끝나기 전 null). `elements`는 요소별 수준 칩(조정 병합 후 값), `low_confidence_count`는 확신도 low 차원 수, `level_source`는 조정 행이 하나라도 있으면 reviewer. report 없으면 이 필드들은 null. **총점 없음, 수준 정렬 없음**. 정렬은 초대순 |
| POST | /admin/positions/{id}/invites | {alias?, email, previous_session_id?} | {session_id, invite_url, expires_at, notice_text}. 발송하지 않음(링크·안내 문안 복사). 새 초대는 alias 필수이고 같은 채용 건 안 **유효한** 같은 별칭이 있으면 409 `conflict`. **재응시**: previous_session_id는 그 세션이 failed(session)·시작 전 만료·cancelled일 때만(아니면 422 `invalid_previous_session`)이고, alias를 생략하면 이전 별칭을 그대로 쓴다. 이전 초대는 `superseded_at`이 찍혀 별칭 유일성 검사에서 빠진다 |
| POST | /admin/sessions/{id}/reissue | | {session_id, invite_url, expires_at, notice_text}. **초대 재발급(연장)**: status invited(만료 포함)인 세션만, 아니면 409 `conflict`. 같은 세션·같은 별칭을 유지하고 토큰 해시와 `expires_at`(now + 72h)만 교체, `reissued_at` 기록. 옛 링크는 404 |
| POST | /admin/sessions/{id}/cancel | | {status: "cancelled"}. invited·in_progress에서만(WS `session.ended{cancelled}`, `ended_at` 기록), 그 외 409 |
| GET | /admin/sessions/{id}/report | | Report(아래). 회사 소속 세션이 없으면 404 `not_found`. 리포트 생성 중(status completed, 재생성 직후 포함)이면 409 `report_not_ready`. 생성 실패(failed_stage=report)면 409 `report_failed`이고 error에 `failed_stage: "report"`와 `failed_step: "draft"∣"finalize"`(finalize = 담당자 승인 뒤 확정 실패)를 함께 준다. 그 밖에 리포트가 없는 상태는 404 `not_found` |
| POST | /admin/sessions/{id}/report/regenerate | | {status: "completed"∣"approving"}. failed_stage=report만, 그 외 409. 채용 건 복사본에 평가 프롬프트가 없어 실패한 리포트(`failed_reason`에 `PinnedCopyMissing`)는 다시 해도 같으므로 409 `position_outdated`(다시 만들 수 없다는 담당자 안내)로 거절한다. 초안 전 실패(failed_step draft)는 남은 draft를 지우고 completed로 돌려 report.generate를 재발행한다(최신 평가에 실패 차원이 있으면 eval_round +1). 새 draft가 저장될 때까지 리포트 조회는 409 `report_not_ready`, 지원자 목록의 리포트 필드는 null이다. 확정 실패(failed_step finalize)는 approving으로 돌려 같은 eval_round의 report.approve를 재발행하고, 워커는 그 회차의 finalize만 재개한다(LLM 재호출 없음) |
| POST | /admin/sessions/{id}/review | {adjustments: [{dimension_id, level: 1~5∣null, no_record_reason?, memo}], reviewer_name} | {status: "approving"}. 승인(검토 완료)과 차원 단위 조정을 한 트랜잭션으로 저장한다(`rubric_adjustments`). `reviewer_name` 필수(1~40자). `dimension_id`는 평가표 19개 차원 중 하나([product/rubric.md](../product/rubric.md) 3장). `level`이 null이면 `no_record_reason`(기록 없음 사유 코드) 필수, level이 있으면 사유 없음. **memo 필수**(빈 문자열 불가). 요소 수준은 조정된 차원으로 다시 계산한다. 생략 차원은 자동 값. adjustments가 빈 배열이면 조정 없이 승인. 검증 오류는 모두 422: 없는 dimension_id `invalid_dimension`, memo 없음 `memo_required`, 중복 `duplicate_dimension`, level 범위 밖·level과 사유 동시·둘 다 없음·사유 코드 밖·reviewer_name 없음 `invalid_input`. 판정 순서: 상태(409) → 검증(422). 이미 approving·reviewed: 본문 같으면 200 멱등, 다르면 409 `already_reviewed`. report 없거나 상태가 report_ready·approving·reviewed가 아니면 409 `conflict` |
| POST | /admin/sessions/{id}/report/notes | {text, reviewer_name} | {notes: [{id, text, reviewer_name, created_at}]}. approving·reviewed에서만 |
| GET | /admin/sessions/{id}/record | | [{t_min, type: message∣slot∣phase∣assistant_call∣material_view∣proposal_submit∣connection_gap, by, summary, ref: {type, id, t_min}}]. 전체 기록 읽기 전용, t_min 순. `proposal_submit` 줄의 `summary`에는 빈 칸 수와 빈칸 경고 확인 여부를 항상 적는다: 수동 제출은 "제출 · 빈 칸 N개(경고 확인 후 제출)"(`blank_warning_shown` true) 또는 "제출 · 빈 칸 N개(경고 없이 제출)"(false), 자동 제출은 "자동 제출 · 빈 칸 N개"(null). N이 0이어도 "빈 칸 0개"로 적는다. `ref`는 리포트 근거의 `record_ref`와 같은 값 |

`session_minutes`가 60·15 밖이면 422 `invalid_input`. 요청에 `elements`가 있으면 422 `invalid_input`(요소 선택 기능 없음). 모든 조회는 JWT의 company_id로 필터. 샘플 리포트는 web 정적 픽스처(`apps/web/fixtures/sample-report.json`, Report 형식). 인쇄·PDF는 web 인쇄 CSS.

### Report

예시는 발췌다(실제 본문은 `elements` 5행, `dimensions` 19행, 각 요소의 모든 차원). `position.name`은 내부 `GET /internal/sessions/{id}`의 `position.name`(`positions.name`)이다.

```json
{"session_id":"...","alias":"지원자A","stage":"draft",
 "position":{"name":"...","session_minutes":60},
 "seats":[{"seat":"candidate","display_name":"김지원"},{"seat":"p1","display_name":"강한결","avatar_url":"/assets/missions/debate/avatars/avatar-01.png","persona_type":"stubborn"},{"seat":"p2","display_name":"윤하람","avatar_url":"/assets/missions/debate/avatars/avatar-02.png","persona_type":"quiet"},{"seat":"p3","display_name":"서지안","avatar_url":"/assets/missions/debate/avatars/avatar-03.png","persona_type":"critic"}],
 "evaluation":{"run_id":"...","rubric_version":"...","prompt_versions":{"common":"<hash>","listening":"<hash>"},"model":"..."},
 "elements":[{"element":"listening","name":"경청/존중","level":3,"summary":"압박에 매번 내용으로 응답했으나 상대 근거 인정은 형식적이었습니다.",
              "dimension_count":4,"no_record_count":1,"low_confidence_count":0,"adjusted_level":null}],
 "dimensions":[{"element":"listening","dimension_id":"listen_engage","name":"응답성","level":4,"no_record_reason":null,
                "rationale":"세 번의 압박 중 두 번은 상대 근거를 받아 답함(앵커 4). 보조 장면과 같은 방향.","confidence":"high",
                "reviewer_hint":"상대 발언에 대체로 내용으로 응답했습니다","adjusted_level":null,"adjusted_no_record_reason":null,"adjust_memo":null,
                "evidence_refs":["msg:<m1>","msg:<m2>"],
                "evidence":[{"ref":"msg:<m1>","kind":"message","counted":true,"speaker":"candidate","phase":"problem","t_min":14.2,"origin":"spontaneous",
                             "quote":"침수 피해는 매년 반복돼서 우선순위가 높다고 봐요.",
                             "context":{"before":[{"id":"<m0>","sender":"p1","content":"...","t_min":13.9}],"after":[{"id":"...","sender":"p3","content":"...","t_min":14.5}]},
                             "record_ref":{"type":"message","id":"<m1>","t_min":14.2}},
                            {"ref":"msg:<m2>","kind":"message","counted":true,"speaker":"candidate","phase":"problem","t_min":18.0,"origin":"spontaneous",
                             "quote":"...","context":{"before":[],"after":[]},"record_ref":{"type":"message","id":"<m2>","t_min":18.0}}]},
               {"element":"listening","dimension_id":"listen_civility","name":"존중하는 반대","level":null,"no_record_reason":"no_activity",
                "rationale":"핵심 창 안 지원자 메시지 없음.","confidence":null,"evidence_refs":[],"evidence":[],
                "adjusted_level":null,"adjusted_no_record_reason":null,"adjust_memo":null}],
 "facts":{"message_count":{"total":24,"keyword":5,"problem":9,"solution":8,"proposal":2},"spontaneous_count":19,"invited_count":5,"spontaneous_ratio":0.79,
          "response_latency_median_sec":21,"intent_counts":{"propose":4,"agree":8,"oppose":3,"question":5,"other":4}},
 "contribution_check":[{"phase":"problem","option_id":"...","label":"도심 폭우·침수 피해 증가","first_proposer":"p3","credited_in_proposal":["p3"],"credit_quote":"문제 상황: 서지안 제안"}],
 "slots":[{"phase":"keyword","value":"물","option_id":"...","filled_by":"candidate","resolution":"consensus","outcome":"consensus","t_min":8.2,
           "first_proposer":"p3","support":[{"option_id":"...","label":"물","supporters":["candidate","p2","p3"]}]}],
 "cards":[{"card_id":"listening_pressure","element":"listening","fired_at":13.0,"delivered":true,"scene":"고집형 동료가 문제 상황 단계에서 자기 안을 강하게 밀어붙인 장면"}],
 "debrief":{"cards":[{"card_id":"listening_pressure","element":"listening","phase":"problem","fired":true,"fired_t_min":13.0,"window":{"start_min":13.0,"end_min":28.0}}],
            "survey":{"natural":4,"fair":3,"free_comment":null}},
 "proposal":{"submitted_at":"...","auto_submitted":false,"sections":{"keyword":"...","problem":"...","solution":"...","rationale":"...","expected_effect":"...",
             "idea_credit":"문제 상황: 서지안 제안. 해결책: 강한결 아이디어에 알림 기능을 제가 보탬"}},
 "ai_usage":{"used":true,"events":[{"call_id":"...","t_min":31.0,"request_quote":"...","response_quote":"...","cited_material_ids":["water_stats"]}]},
 "review":{"reviewer_name":"박담당","reviewed_at":"..."},"notes":[],"connection_gaps":[{"from_t_min":12.0,"to_t_min":13.5}],
 "generated_at":"...","reviewed_at":null,"purge_at":"..."}
```

| 위치 | 필드 | 규칙 |
|---|---|---|
| evaluation | | 이 리포트가 읽은 평가 실행(`evaluation_runs` 행)의 `run_id`·`rubric_version`·`prompt_versions`(`{common: 해시, <element>: 해시}`)·`model` |
| elements[] | `level`, `summary` | 5요소마다 한 행(`evaluation_runs.elements`). level = 수준이 있는 차원 평균을 반올림(.5 올림), 모두 기록 없음이면 null. 워커가 결정적으로 계산. summary는 평가 에이전트가 쓴 1~2줄(기록 없음 차원이 있으면 함께 밝힘). `dimension_count`·`no_record_count`·`low_confidence_count`. **요소 수준끼리 더한 총점·합계는 없다** |
| elements[] | `adjusted_level` | api가 GET 때 조정을 병합해 다시 계산한 요소 수준. 조정이 없으면 null. 자동 summary는 그대로 두고 화면이 "담당자 조정됨"을 표시 |
| dimensions[] | `level`, `no_record_reason`, `confidence`, `rationale`, `evidence_refs` | `evaluations` 행 그대로(`save_evaluation` 입력 형식). level과 no_record_reason은 둘 중 하나만. 기록 없음이면 confidence null. 사유 표시 문장은 [product/rubric.md](../product/rubric.md) 5장. `confidence = low`는 화면에 "근거 약함, 원문 확인 권장" |
| dimensions[] | `name`, `reviewer_hint` | 차원 이름과 그 수준 행의 담당자 참고 문구(`positions.scenario`의 척도 복사본). 기록 없음이면 reviewer_hint 없음 |
| dimensions[] | `evidence[]` | `evidence_refs`마다 한 항목. `kind`: `message`(quote·speaker·origin·phase·t_min) ∣ `slot`(phase·value·resolution·filled_by·support) ∣ `proposal`(field·발췌) ∣ `contribution`(Contributions 행). `counted`는 근거 개수에 드는지(지원자 행동 단위만 true, AI 동료 메시지·contribution은 false, `hon_credit_record` 예외는 [product/rubric.md](../product/rubric.md) 3.5). message 근거의 `context`는 `get_transcript_span`(앞 5·뒤 3)으로 만든 **앞뒤 맥락 표시용** 메시지다. 모두 `record_ref {type, id, t_min}`. 오피스 시각은 web이 `t_min`으로 변환 |
| dimensions[] | `adjusted_level`, `adjusted_no_record_reason`, `adjust_memo` | api가 GET 때 `rubric_adjustments`의 그 차원 마지막 행을 병합. 없으면 null. 최종값 = 조정 값이 있으면 그것, 없으면 자동 값 |
| facts | | 사실 지표. 키와 정의는 [product/rubric.md](../product/rubric.md) 6장 표 그대로(`{<지표 id>: 값}`). 사실만으로 수준을 정하지 않는다 |
| contribution_check | | 채택된 칸 중 최초 제안자가 AI 동료인 안(사실 지표 `ai_adopted_units`)마다 실제 최초 제안자와 기획안 `idea_credit`에 적힌 기여자(`credited_in_proposal`, 워커 추출)·발췌. 정직 요소의 대조표이며 판단은 `hon_credit_record` 차원이 한다 |
| seats | | 첫 항목은 지원자 `{seat: "candidate", display_name}`, 나머지는 AI 자리 `{seat, display_name, avatar_url, persona_type}` |
| slots | `first_proposer` | 확정 안의 최초 제안자(`get_contributions`). option_id null이면 null |
| cards[] | `card_id`, `element`, `fired_at`, `delivered`, `scene` | 카드 6장마다 한 행. `fired_at`은 첫 카드 발언 게시 시각(세션 시작 기준 분, web이 오피스 시각으로 변환), 미발생이면 null. `delivered`는 담당 화자 전원이 게시했는지. false면 평가는 그 카드를 핵심 장면으로 세지 않고([product/rubric.md](../product/rubric.md) 2.4), 화면은 발동한 카드에 "일부 동료만 전달됨"을 표시한다. 화면은 이 절에 담당자용 "설계된 장면" 표시를 붙인다(같은 채용 건 모든 지원자가 겪는 장면). `scene`은 카드의 고정 문장 `report_template`(`positions.scenario` 복사본) 그대로이며 **LLM이 만들지 않는다**. 판단이 아닌 장면 설명이라 화면의 "관련 카드 장면"·타임라인 카드 표시에 쓴다 |
| debrief | `cards`, `survey` | 발동 카드(`observations`)와 카드의 `window` 규칙([product/scenario.md](../product/scenario.md))으로 워커가 계산한 관찰 창(미발생 카드는 `fired: false`), 종료 설문 답(없으면 null) |
| ai_usage | | 판단 기준 없음, 면접 확인용. 시간순 사실만 |

`evaluation`·`elements`(adjusted_level 제외)·`dimensions`(조정 필드 제외)·`facts`·`contribution_check`·`slots`·`cards`·`debrief.cards`는 워커가 draft에 넣는다. 조정 필드·`review`·`notes`·`connection_gaps`·`purge_at`·`proposal`·`debrief.survey`는 api가 GET 때 병합한다. 저장된 `reports.content`는 불변이고 해시는 content에만 적용한다. **final은 승인된 draft content를 그대로 저장한다(해시 동일)**. 담당자 조정은 final content에 굽지 않고 GET 때 병합한다(draft·final 모두 같은 병합 규칙). 텍스트 필드(`summary`·`rationale`)에는 합격·불합격·순위·추천·평가표 밖 점수 표현이 없어야 한다(워커 금지어 검사). 모든 `evidence_refs`는 같은 세션의 실제 행이어야 한다(api가 `save_evaluation` 저장 전에 검증). 요소 수준을 더한 총점·합계는 어디에도 없다.

### 근거 참조 (`evidence_refs[]`)

문자열 하나가 근거 하나다. 형식 밖이거나 같은 세션의 실제 행이 아니면 워커 검증 실패.

| 형식 | 가리키는 것 | 개수에 듦 |
|---|---|---|
| `msg:<message_id>` | room 메시지. 지원자 메시지면 근거 단위, AI 동료·시스템 메시지면 맥락 | 지원자 메시지만 |
| `slot:<phase>` | 지원자가 확정한 칸(`filled_by = candidate`) | 예 |
| `proposal:<field>` | 제출 기획안 필드(`idea_credit` 등, 필드 id는 [product/scenario.md](../product/scenario.md) 템플릿) | 예 |
| `contribution:<option_id>` | Contributions 대조표 행(맥락) | 아니오(`hon_credit_record` 예외, [product/rubric.md](../product/rubric.md) 3.5) |

최소 개수(수준 1~3은 1개, 4·5는 서로 다른 단위 2개)와 확신도 상한은 [product/rubric.md](../product/rubric.md) 2.2·2.4.

## 지원자 REST (`/s`, `/sessions`, 포트 8000)

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| GET | /s/{token} | | {session_id, company_name, duration_minutes, status, default_display_name, elements_names, entry_notice, contact_email, debrief_token?, failed_stage?} 또는 410 `expired`∣`cancelled`. `default_display_name`은 초대 별칭(입장 화면 이름 칸의 기본값). 모르는 토큰(재발급으로 바뀐 옛 토큰 포함) 404 `not_found` |
| POST | /s/{token}/consent | {display_name?} (1~20자, 기본값은 초대 별칭. 공백 정리·마스킹. 빈 값이면 별칭) | {session_token, session_id}. 동의 중복은 멱등(마지막 display_name, 시작 전까지만) |
| POST | /s/{token}/resume | | {session_token, session_id} (in_progress만, 아니면 409) |
| POST | /s/{token}/start | Bearer | {session_id, started_at, ends_at, phase: "keyword"} (멱등). 채용 건 시나리오 복사본(`catalog`·`prompts`)이 없으면 409 `position_outdated`(message는 담당자 문의 안내, 화면은 그대로 보인다) |
| GET | /sessions/{id}/state | Bearer | State 스냅샷(아래). 시작 전(invited, 동의 뒤)에도 200: `session.status = invited`, 시계 필드 null, `participants`·`phases`·빈 `slots`, 나머지 빈 값 |
| POST | /sessions/{id}/slots/{phase}/confirm | Bearer {value, option_key?} | {slot, phase: <다음 단계>}. 현재 단계만. keyword는 `option_key` 필수(후보 키, 밖이면 422 `invalid_keyword`, `value`는 그 후보 라벨로 저장), problem·solution은 value 필수(비면 422)이고 `option_key`는 선택. 안 연결은 [계약 README](README.md) "칸(slot) 상태"의 결정적 규칙. filled_by=candidate, resolution=consensus, support 스냅샷 저장 → 단계 전환(WS `slot.confirmed`·`phase.changed`·`timer`, keyword면 `materials.opened`). 다른 단계 409 `phase_not_open`, 이미 확정 409 `slot_confirmed`(같은 값 재요청은 200 기존 결과) |
| GET | /sessions/{id}/materials | Bearer | {locked: bool, keyword: string∣null, materials: [{material_id, title}]}. keyword 칸 확정 전이면 locked=true·빈 목록. 확정 뒤에는 그 키워드의 자료(keyword 칸은 후보 4개 중에서만 확정되므로 항상 자료 세트가 있다). 목록·본문·검색은 모두 채용 건의 `catalog_version`으로 `materials` 테이블에서 읽는다(카탈로그 파일을 직접 읽지 않음) |
| GET | /sessions/{id}/materials/{material_id} | Bearer | {material_id, title, content(Markdown)} 또는 423 `not_released`(열리지 않은 자료). 같은 `catalog_version`의 본문. 열람을 `material_views`에 기록 |
| POST | /sessions/{id}/assistant | Bearer {prompt} | {call_id, message_id, content, cited_material_ids, tool_calls: [{name, query?, material_id?}]} 또는 429 `assistant_limit`(세션 60회 초과). 어시스턴트는 tool-calling 루프(`search_materials`·`get_material`, 요청당 도구 최대 3회)로 열린 자료만 보고, 답에는 인용한 자료 id를 붙인다 |
| GET | /sessions/{id}/proposal | Bearer | Proposal {sections, revision, updated_at, submitted_at} |
| PUT | /sessions/{id}/proposal | Bearer {sections, revision} | {revision, updated_at}. 디바운스 저장. in_progress 언제든. `revision`이 현재 값과 다르면 409 `conflict`(다른 탭 덮어쓰기 방지, 화면은 최신을 다시 읽음) |
| POST | /sessions/{id}/proposal/submit | Bearer {blank_warning_shown?: bool} | {status: "completed", completed_at, submitted_at}. phase=proposal에서만(아니면 409 `phase_not_open`). 저장 대기분을 먼저 PUT한 뒤 호출. 현재 초안을 제출본으로 고정 → completed → WS `session.ended{submitted}`. 멱등(completed 이후 재호출은 저장된 값). 화면은 "제출하면 수정할 수 없어요" 확인 창을 거친다. 빈 칸(`sections` 값이 공백뿐)은 api가 제출본에서 직접 계산해 `proposals.submit_blank_sections`에 저장한다(수동·자동 제출 모두, 빈 칸이 없으면 `[]`). 클라이언트가 빈칸 경고 창을 보여 준 뒤 제출했으면 `blank_warning_shown: true`를 보내고, 수동 제출에서 생략하면 false로 저장한다(`proposals.blank_warning_shown`, 자동 제출이면 null). 두 값은 제출 전 null이고 제출 때 한 번 쓰며 멱등 재호출로 바뀌지 않는다. 빈 칸이 있어도 제출은 막지 않는다 |
| GET | /sessions/{id}/debrief | Bearer(세션 JWT 또는 debrief_token) | {intro_notice, elements: [{element, name, how_we_see}] (5요소의 이름과 "이렇게 봅니다" 한 줄, `/admin/catalog` 요소 카드와 같은 문구), cards: [{card_id, element, name, description}] (실제 발동한 카드만, 원천은 `observations`), seats: [{seat: "candidate", display_name}, {seat, display_name, avatar_url, persona_type, persona_description}] (AI 동료가 성향을 가진 AI였다는 설명), my_messages: [{id, content, phase, t_min}], scoring_notice, appeal_contact, connection_gaps, retention_notice, contact_email, proposal: {sections}∣null, view_until(`ended_at` + 7일), survey: {submitted, questions: [{q, text, kind: "scale"∣"text"}]}, failed_stage?}. `intro_notice` 첫 문장은 고정("같은 채용 건의 모든 지원자가 같은 장면을 겪었고, 정답 없이 반응을 본다"). `my_messages`는 지원자 발언 2~3개의 읽기 전용 회고: 토론 단계마다 가장 긴 지원자 메시지 하나씩(최대 3개, 결정적 선택). `appeal_contact`는 이의 제기 연락처 문자열. **척도 세부(차원·수준 앵커)와 평가 결과는 디브리핑에 내려가지 않는다.** 세션 실패면 elements·cards·seats·proposal·my_messages 없이 중단·재응시 안내만 |
| POST | /sessions/{id}/debrief/survey | Bearer(세션 JWT 또는 debrief_token) {answers: [{q: "natural"∣"fair", value: 1~5}], free_comment?: string(500자 이내)} | {submitted: true}. 한 번만 저장(멱등) |

제출 이후 쓰기(메시지·칸·기획안·어시스턴트)는 전부 409 `session_ending`(WS는 오류 프레임 `session_ending`). 디브리핑 기한 = `ended_at` + 7일, 지나면 410 `expired`.

### Proposal

```json
{"sections":{"keyword":"물","problem":"...","solution":"...","rationale":"...","expected_effect":"...",
             "idea_credit":"문제 상황: 서지안 제안. 해결책: 강한결 아이디어에 알림 기능을 제가 보탬"},
 "revision":7,"updated_at":"...","submitted_at":null}
```

- 템플릿 6칸: 키워드 · 문제 상황 · 해결책 · 근거 · 기대효과 · 아이디어 기여. 앞 셋은 proposal 단계 시작 때 비어 있으면 확정 칸 값으로 채운다(지원자가 고칠 수 있음).
- `idea_credit`: 누가 어떤 아이디어를 냈는지 지원자가 자유 문장으로 적는 칸. 화면 안내는 중립적으로 "이 기획에 반영된 의견과 그 의견을 낸 사람"만 쓴다. 평가 에이전트가 `proposal:idea_credit` 근거로 읽고, 워커가 `get_contributions`와 대조해 `contribution_check`를 만든다. 모든 필드는 문자열.

### State 스냅샷

```json
{"session":{"id":"...","alias":"지원자A","status":"in_progress","phase":"problem","started_at":"...","ends_at":"...","session_minutes":60,
            "phase_started_at":"...","phase_deadline_at":"...","phase_remaining_seconds":840,"session_remaining_seconds":2700,
            "office_start":"09:00","office_end":"10:00","completed_at":null},
 "keyword_options":[{"option_key":"water","label":"물"},{"option_key":"fire","label":"불"},{"option_key":"tree","label":"나무"},{"option_key":"wind","label":"바람"}],
 "phases":[{"phase":"keyword","budget_min":10,"deadline_t_min":10},{"phase":"problem","budget_min":18,"deadline_t_min":28},
           {"phase":"solution","budget_min":18,"deadline_t_min":46},{"phase":"proposal","budget_min":14,"deadline_t_min":60}],
 "participants":[{"id":"candidate","display_name":"김지원"},{"id":"p1","display_name":"강한결","avatar_url":"/assets/missions/debate/avatars/avatar-01.png"},
                 {"id":"p2","display_name":"윤하람","avatar_url":"/assets/missions/debate/avatars/avatar-02.png"},{"id":"p3","display_name":"서지안","avatar_url":"/assets/missions/debate/avatars/avatar-03.png"}],
 "slots":[{"phase":"keyword","status":"confirmed","value":"물","filled_by":"candidate","resolution":"consensus","outcome":"consensus","t_min":8.2},
          {"phase":"problem","status":"empty","value":null,"filled_by":null,"resolution":null,"outcome":null,"t_min":null},
          {"phase":"solution","status":"empty","value":null,"filled_by":null,"resolution":null,"outcome":null,"t_min":null}],
 "messages":[{"id":"...","sender":"p1","content":"...","phase":"problem","is_invite":false,"t_min":9.0,"created_at":"..."}],
 "materials":{"locked":false,"keyword":"물","materials":[{"material_id":"water_stats","title":"물 부족 통계"}]},
 "proposal":{"sections":{"keyword":"","problem":"","solution":"","rationale":"","expected_effect":"","idea_credit":""},"revision":0,"updated_at":"...","submitted_at":null},
 "assistant":[{"call_id":"...","request":"...","response":"...","cited_material_ids":[],"t_min":4.0}],
 "typing":["p2"],
 "active_runs":[{"run_id":"...","stimulus":"message"}],
 "connection_gaps":[],
 "ws_seq":143}
```

스냅샷이 전체 상태이고 WS는 증분만 보낸다. `participants`의 지원자는 `display_name`만, AI 자리는 `display_name`·`avatar_url`(유형 없음). 칸의 `support`와 안·지지 현황은 지원자 화면에 내려가지 않는다(합의 패널은 고른 결과만). `keyword_options`는 키워드 단계 후보 버튼(카탈로그 `keyword_order` 순, 시작 전 `/state`에도 있음)이고 칸 확정의 `option_key`는 이 값 중 하나다. `typing`은 지금 입력 중 표시가 켜진 AI 자리. `ws_seq`는 스냅샷 시점까지 이 세션에 보낸 마지막 WS `seq`이고, 클라이언트는 이보다 큰 seq의 프레임만 적용한다.
