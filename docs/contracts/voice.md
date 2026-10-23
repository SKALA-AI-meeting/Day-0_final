# 음성 REST·메시지 계약

공개 API 8000, 모든 경로 `/sessions/{session_id}/voice`에 지원자 세션 JWT를 Bearer 헤더로 보낸다. 다른 세션 소속이면 403이다. 실제 미디어와 공급자 호출은 서버 내부이며 브라우저에 Azure 키를 보내지 않는다. JSON 시간은 UTC ISO 8601이다.

| 메서드·접미 경로 | 요청 | 응답 |
|---|---|---|
| GET (접미 없음) | 없음 | 아래 VoiceState. 750ms polling. 재연결 시 먼저 일반 `/state`와 이 상태를 다시 조회 |
| POST `/capture` | `{turn_id, epoch, restart?:bool}` | 발언권 상태: utterance_id, speech_started_at, speech_deadline_at 포함. 동일 활성 발언의 중복 시작은 같은 ID |
| POST `/playback` | `{turn_id, epoch, job_id, outcome: start∣ended∣error∣retry}` | `{ok:true}`. 첫 미완료 항목만 처리. start 뒤 실제 ended 확인. heard 항목 ended 중복은 성공 |
| POST `/audio` | `{turn_id, epoch, utterance_id, audio_base64, stop_reason: button∣timeout∣disconnect}` | `{utterance_id,status}`. PCM16 mono 16kHz WAV, 최대 60초·base64 2,600,000자. 동일 파일 해시로 멱등 |
| POST `/abort` | `{turn_id,epoch}` | 장치/파일 실패를 기록하고 남은 발언권으로 복귀 |
| POST `/retry` | `{turn_id, epoch, job_id}` | `{ok:true}`. 같은 완성 파일 STT를 총 2시도 이내 재처리 |
| POST `/rerecord` | `{turn_id, epoch, job_id}` | 발언권 상태(state USER_READY, `rerecorded:true`). 받아 적기가 끝난(ready) 현재 발언을 제출하지 않고 버린다. 같은 차례에서 1회만, 두 번째는 409 `voice_retry_exhausted`. 다음 `/capture`는 발언 시간을 새로 받는다 |
| GET `/assets/{job_id}` | 없음 | 같은 세션의 TTS `audio/wav`, `Cache-Control:no-store`. 없거나 만료면 404 |

VoiceState는 `{turn_id, epoch, generation, phase, state, ready_at, server_now, items}`다. GET(접미 없음) 응답에는 `heard_message_ids`(재생이 끝난 동료 발언의 메시지 id 목록, 전 단계 포함)가 더 있고, 화면은 이 목록에 있는 동료 발언만 대화 기록에 글로 남긴다. 재생 중인 발언은 items의 `message_id`로 본문을 찾아 재생 위치에 맞춰 대화 기록 말풍선에 한 문장씩 더한다. state가 USER_READY면 `think_deadline_at`(ready_at + 채용 건의 `voice_policy.think_seconds`)도 있다. 화면은 이 시각까지 생각 시간을 보여 주고 "지금 발언하기"를 누르거나 시각이 되면 `/capture`를 부른다. 서버는 think_seconds + 10초 동안 발언권을 반납하지 않는다. state는 AI_GENERATING·AI_SPEAKING·USER_READY·USER_SPEAKING·TRANSCRIBING·PROCESSING이다. items는 저장 순서의 `{id,message_id,seat,status,duration_ms,error}`이며 본문/평가 정보는 없다. 조건부 필드는 `blocked_reason`, `release_ready`, `utterance_id`, `speech_started_at`, `speech_deadline_at`, `utterance:{id,status,transcript,message_id,error,confirm_deadline_at?}`, `rerecorded`다. utterance가 ready면 `confirm_deadline_at`(받아 적기 완료 + 채용 건의 `voice_policy.confirm_seconds`)이 있고, 화면은 그때까지 받아 적은 글과 [이대로 제출]·[다시 녹음]을 보인 뒤 시각이 되면 자동으로 `message.send`한다. 작업 상태는 queued·processing·ready·heard·error·empty·submitted·recording·canceled 중 해당 단계의 값이다. epoch는 WS 연결마다 바뀌며 연결 전에는 null일 수 있다.

오류 봉투는 기존 `{error:{code,message}}`다. 409 `voice_turn_conflict`·`voice_turn_expired`·`voice_payload_mismatch`·`voice_retry_exhausted`·`voice_quota_exhausted`·`voice_capacity`·`voice_policy_mismatch`, 422 `voice_audio_invalid`, 429 `voice_rate_limited`, 503 `voice_unavailable`·`voice_quota_unverified`를 사용한다. 생성/전사 실패는 작업의 error로 기록한다. 마감 뒤 STT를 새 phase에 저장하지 않는다.

## 최종 전사 제출

기존 WS `message.send`의 payload에 음성 세션은 `{client_msg_id:utterance_id, content:확정전사, voice_turn_id, epoch}`를 보낸다. 서버는 전사와 본문이 일치하는지 확인한 뒤 기존 메시지·generation·agent_run 저장과 발언권 소비를 한 트랜잭션으로 커밋한다. `message.ack`·`message.new` 의미와 봉투는 같다. ack 유실은 같은 ID로 재전송하며 저장 메시지/자극은 한 건이다. 같은 ID의 다른 본문은 거부한다. 일반 텍스트 message.send는 음성 세션에서 거부한다.

음성 제어는 REST이고 새로운 WS 음성 이벤트는 없다. 기존 typing은 음성 화면과 녹음 보호에 사용하지 않는다. API 단위 테스트의 명시적인 비음성 context만 기존 텍스트 경로를 검증한다.

## 내부 평가·테스트

음성 세션의 내부 observations.delivered는 메시지 게시와 별도로 담당 자리의 실제 TTS ended 확인이 모두 있어야 true다. SessionRecord `facts.voice_usage`는 문자·초·미청취 메시지·기술 실패·청구 불확실 상태를 제공한다. 기술 실패는 행동 근거가 아니다. 큐 본문·MCP 도구 허용 목록·평가 출력 형식은 유지한다.

`POST /internal/test/voice-transcript {content}`는 기존 내부 인증+ENABLE_TEST_API+FakeSpeech 조건에서만 고정 전사를 지정한다. 실제 Azure 어댑터에는 사용할 수 없다. 브라우저 테스트에서도 녹음 파일을 업로드해야 한다.
