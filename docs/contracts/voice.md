# 음성 REST·메시지 계약

공개 API 8000, 모든 경로 `/sessions/{session_id}/voice`에 지원자 세션 JWT를 Bearer 헤더로 보낸다. 다른 세션 소속이면 403이다. 실제 미디어와 공급자 호출은 서버 내부이며 브라우저에 Azure 키를 보내지 않는다. JSON 시간은 UTC ISO 8601이다.

| 메서드·접미 경로 | 요청 | 응답 |
|---|---|---|
| GET (접미 없음) | 없음 | 아래 VoiceState. 750ms polling(AI_GENERATING·TRANSCRIBING·PROCESSING 동안은 250ms). 재연결 시 먼저 일반 `/state`와 이 상태를 다시 조회 |
| POST `/capture` | `{turn_id, epoch, restart?:bool}` | 발언권 상태: utterance_id, speech_started_at, speech_deadline_at 포함. 동일 활성 발언의 중복 시작은 같은 ID. 차례가 이미 넘어간 뒤 이전 turn_id는 409 `voice_turn_conflict`, 단계·세션 마감 또는 녹음 시간이 남지 않으면 409 `voice_turn_expired` |
| POST `/playback` | `{turn_id, epoch, job_id, outcome: start∣ended∣error∣retry}` | `{ok:true}`. 첫 미완료 항목만 처리. start 뒤 실제 ended 확인. heard 항목 ended 중복은 성공. start한 항목의 ended는 그사이 단계·차례가 바뀌어도 heard로 기록하고 성공(재생 중 단계 전환) |
| POST `/audio` | `{turn_id, epoch, utterance_id, audio_base64, stop_reason: button∣timeout∣disconnect}` | `{utterance_id,status}`. PCM16 mono 16kHz WAV, 최대 60초·base64 2,600,000자. 동일 파일 해시로 멱등 |
| POST `/hand` | `{turn_id, epoch, raised?:bool(기본 true), during_message_id?, offset_ms?}` | 발언권 상태. AI_GENERATING·AI_SPEAKING에서만(아니면 409 `voice_turn_conflict`). 손을 들면 재생 중인 동료 발언은 끝까지 두고, 그 뒤 보호 발언(카드 발언·합의/시간 초과 공지)만 남기고 줄 선 동료 발언을 취소(`error: hand_raised`)한 뒤 `state: USER_READY`, `priority: "hand"`가 된다. 이 차례는 에이전트 실행 중이어도 주어지고, 일반 차례처럼 지원자가 [발언하기]로 `/capture`할 때까지 유지 시간(`turn_hold_seconds`) 동안 새 동료 발언이 빼앗지 않는다. `raised:false`는 손 내리기. 손들기 정보와 차례 종류(`turn_kind: turn∣hand∣forced`)는 다음 녹음의 STT 작업 기록에 남는다 |
| POST `/stream` | `{turn_id, epoch, utterance_id, seq, pcm_base64}` | `{ok:true}`. 녹음 중(USER_SPEAKING) 그 발언의 소리 조각(PCM16 mono 16kHz 원본, WAV 머리 없음, 약 0.5초)을 0부터 순번대로 보낸다. 서버는 약 10초마다 쉬는 곳에서 잘라 미리 받아 적고, `/audio` 뒤에는 남은 꼬리만 받아 적어 잇는다. 순번이 빠지거나 받은 길이가 완성 파일과 1초 넘게 다르면 완성 파일 전체로 받아 적는다. 선택 사항이며 보내지 않아도 `/audio`만으로 동작한다 |
| POST `/abort` | `{turn_id,epoch}` | 장치/파일 실패를 기록하고 남은 발언권으로 복귀 |
| POST `/retry` | `{turn_id, epoch, job_id}` | `{ok:true}`. 같은 완성 파일 STT를 총 2시도 이내 재처리 |
| POST `/rerecord` | `{turn_id, epoch, job_id}` | 발언권 상태(state USER_READY). 받아 적기가 끝난(ready) 현재 발언을 제출하지 않고 버린다. 같은 차례에서 1회만 허용하고(`rerecorded:true`), 두 번째는 409 `voice_retry_exhausted`. 받아 적은 글이 500자(지원자 발언 상한)를 넘어 제출할 수 없는 발언을 버리는 경우는 이 1회에 세지 않고 따로 차례당 2회까지 허용하며(`long_rerecords`, 넘으면 409 `voice_retry_exhausted`) `rerecorded`를 바꾸지 않는다. 다음 `/capture`는 발언 시간을 새로 받는다 |
| GET `/assets/{job_id}` | 없음 | 같은 세션의 TTS `audio/wav`, `Cache-Control:no-store`. 없거나 만료면 404 |

VoiceState는 `{turn_id, epoch, generation, phase, state, ready_at, server_now, items}`다. proposal 단계에는 `USER_READY`를 만들지 않고 새 발언권 요청은 409 `voice_turn_expired`다. GET(접미 없음) 응답에는 `heard_message_ids`(재생이 끝난 동료 발언의 메시지 id 목록, 전 단계 포함)가 더 있고, 화면은 이 목록으로 청취 여부를 확인한다. 재생 중인 동료의 문장을 큰 칸에 자막으로 보이고, 끝난 뒤 방금 한 말 한 문장만 남긴다. 기록 목록은 없다. 재생 중인 발언은 items의 `message_id`로 본문을 찾아 재생 위치까지의 문장만 보인다. state가 USER_READY면 `turn_until_at`(내 차례를 지켜 주는 끝 시각)이 있고, 단계 마감·세션 종료가 가까워 녹음을 `user_turn_seconds`만큼 못 하면 `speech_limit_seconds`(지금 녹음을 시작하면 말할 수 있는 초, 0이면 이번 단계에서 더 녹음할 수 없음)도 있다(개정 V18, 마감은 늘리지 않음). 녹음 마감(`speech_deadline_at`)은 단계 마감·세션 종료보다 10초(받아 적기·확인·제출 여유) 앞을 넘지 않아서, 마감에 맞춰 잘린 녹음도 단계가 끝나기 전에 제출된다. 그 10초 안에서는 `/capture`가 409 `voice_turn_expired`다. 녹음은 자동으로 시작하지 않는다. 일반·손들기·강제 차례 모두 지원자가 [발언하기]로 `/capture`하고, 지켜 주는 시간은 모두 ready_at부터 `voice_policy.turn_hold_seconds`(없던 예전 채용 건은 20초)다. 예전 채용 건 정책에 남은 `think_seconds`는 읽지 않는다. state는 AI_GENERATING·AI_SPEAKING·USER_READY·USER_SPEAKING·TRANSCRIBING·PROCESSING이다. items는 저장 순서의 `{id,message_id,seat,status,duration_ms,error}`이며 본문/평가 정보는 없다. 조건부 필드는 `blocked_reason`, `release_ready`, `utterance_id`, `speech_started_at`, `speech_deadline_at`, `utterance:{id,status,transcript,message_id,error,confirm_deadline_at?,submit_deadline_at?}`, `rerecorded`, `priority`(손들기·강제 차례), `hand`(든 손), `hand_turn`, `forced_phase`(그 단계 강제 차례를 이미 줌)이다. 강제 차례: 키워드·문제 상황·해결책 단계에서 그 단계 지원자 발언이 없고 단계 마감까지 남은 시간이 `min(forced_wait_seconds + user_turn_seconds + 10, 단계 길이 × 0.5)`초 이하가 되면(단계 길이는 `phase_started_at`부터 단계 마감까지, 기본 정책은 100초와 단계 절반 중 짧은 쪽), 동료 차례면 손들기와 같이(지금 발언 뒤 보호 발언만 듣고) 일반 내 차례면 그 자리에서 `priority: "forced"` 차례가 된다. 단계당 한 번이다. 지원자 메시지보다 먼저 만들어졌는데 재생이 시작되지 않은 동료 발언은 보호 발언이 아니면 재생하지 않는다(`error: superseded`). utterance가 ready 또는 empty면 `confirm_deadline_at`(받아 적기 완료 + 채용 건의 `voice_policy.confirm_seconds`)이 있고, ready면 `submit_deadline_at`도 있다. 음성 화면은 확인 시각을 기다리지 않고 ready 전사를 바로 `message.send`로 보낸다. 인식 글을 지원자에게 보이거나 고치는 입력 칸은 없다. empty 결과는 제출할 수 없고 [다시 말하기]로 같은 차례에서 다시 녹음한다(`/capture` restart). 500자를 넘는 ready 전사도 제출하지 않고 `/rerecord`로 짧게 다시 말하게 하며, 이 다시 말하기는 일반 다시 말하기 1회와 따로 차례당 2회까지다. `submit_deadline_at`은 단계·세션 마감과 녹음 마감 + 100초(서버 PROCESSING 유지 한도) 중 이른 시각이다. 저장 확인은 `client_msg_id`(= 발언 id)의 지원자 메시지로 판별하고 "전달했어요"를 3초 동안 보인다. 작업 상태는 queued·processing·ready·heard·error·empty·submitted·recording·canceled 중 해당 단계의 값이다. epoch는 WS 연결마다 바뀌며 연결 전에는 null일 수 있다.

오류 봉투는 기존 `{error:{code,message}}`다. 409 `voice_turn_conflict`·`voice_turn_expired`·`voice_payload_mismatch`·`voice_retry_exhausted`·`voice_quota_exhausted`·`voice_capacity`·`voice_policy_mismatch`, 422 `voice_audio_invalid`, 429 `voice_rate_limited`, 503 `voice_unavailable`·`voice_quota_unverified`를 사용한다. 생성/전사 실패는 작업의 error로 기록한다. 마감 뒤 STT를 새 phase에 저장하지 않는다.

`blocked_reason: "proposal_phase"`는 기획안 단계라 새 발언권을 주지 않는다는 뜻이고, `"voice_interrupted"`는 녹음 중 WebSocket 재연결로 발언을 다시 시작해야 한다는 뜻이다. 일반 `USER_READY`의 유지 시간이 지나도 스케줄러가 실제로 차례를 넘기기 전까지 `/capture`를 누를 수 있다. STT·녹음·연결 실패 후 재시도와 다시 녹음으로 `USER_READY`에 돌아오면 유지 시간이 새로 시작된다. 유지 시간이 끝나면 동료 차례가 매번 재개되며, 침묵 초대는 기존처럼 단계당 한 번이다. 기획안 단계로 넘어가기 전 이미 대기 중이던 동료 음성은 재생을 마치고 `heard_message_ids`에 남긴다.

## 최종 전사 제출

기존 WS `message.send`의 payload에 음성 세션은 `{client_msg_id:utterance_id, content:제출할 글, voice_turn_id, epoch}`를 보낸다. `content`는 그 발언의 받아 적은 글(ready STT의 `transcript`)과 앞뒤 공백을 뺀 값이 같아야 한다. 고친 글, empty STT에 직접 쓴 글, 다른 글은 발언권을 소비하지 않고 error `voice_payload_mismatch`다. `content`는 앞뒤 공백을 뺀 1~500자([WS 계약](ws.md) message.send)이고, 넘으면 발언권을 소비하지 않고 error `invalid_input`이다(위 `/rerecord`로 다시 말한다). 서버는 해당 세션·턴·utterance의 ready STT 작업과 본문 일치를 확인한 뒤 기존 마스킹·메시지·generation·agent_run 저장과 발언권 소비를 한 트랜잭션으로 커밋한다. 저장 메시지 `meta`에는 `source:"voice"`, `utterance_id`를 둔다. 본문이 곧 받아 적은 글이라 인식 원문을 따로 두지 않으며, 토론 그래프·평가·관리자 기록은 이 본문을 사용한다. `message.ack`·`message.new` 의미와 봉투는 같다. ack 유실은 같은 ID로 재전송하며 저장 메시지/자극은 한 건이다. 같은 ID의 다른 본문은 거부한다. 다른 턴·utterance·세션의 작업은 409 `voice_turn_conflict` 또는 `voice_payload_mismatch`로 거부한다. 일반 텍스트 message.send는 음성 세션에서 거부한다.

음성 제어는 REST이고 새로운 WS 음성 이벤트는 없다. 기존 typing은 음성 화면과 녹음 보호에 사용하지 않는다. 테스트 텍스트 프로필과 API 단위 테스트의 비음성 context는 기존 텍스트 경로를 사용한다.

## 내부 평가·테스트

음성 세션의 내부 observations.delivered는 메시지 게시와 별도로 담당 자리의 실제 TTS ended 확인이 모두 있어야 true다. SessionRecord `facts.voice_usage`는 문자·초·미청취 메시지·기술 실패·청구 불확실 상태를 제공한다. 기술 실패는 행동 근거가 아니다. 큐 본문·MCP 도구 허용 목록·평가 출력 형식은 유지한다.

`POST /internal/test/voice-transcript {content}`는 기존 내부 인증+ENABLE_TEST_API+FakeSpeech 조건에서만 고정 전사를 지정한다. 실제 Azure 어댑터에는 사용할 수 없다. 브라우저 테스트에서도 녹음 파일을 업로드해야 한다.

## 실행 모드

`VOICE_ENABLED` 기본값은 `1`이다. `0`은 `ENABLE_TEST_API=1`에서만 시작할 수 있다. 이때 음성 REST 경로는 404 `voice_disabled`를 반환하고 서버는 음성 공급자를 만들거나 검증하지 않고, 음성 quota·재생 작업을 시작하지 않는다. 기존 텍스트 WS `message.send`(`client_msg_id`, `content`)와 `typing` 경로를 사용하며 시작 시 채용 건의 `voice_policy` 존재·공급자 일치를 요구하지 않는다. 웹은 같은 프로필의 `NEXT_PUBLIC_VOICE_ENABLED=0` 빌드로 채팅 입력을 보인다. `VOICE_ENABLED=1`은 위 음성 계약을 그대로 적용한다. 채용 건의 `voice_policy.provider`와 현재 `VOICE_PROVIDER`가 다르면 시작 요청은 409 `voice_policy_mismatch`다. FakeSpeech로 새 채용 건을 시드하면 fake 정책을 복사하며, 예전 Azure 정책 채용 건은 fake 음성 스택에서 시작할 수 없다.
