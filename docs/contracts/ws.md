# WebSocket

음성 턴·최종 파일·재생 확인·message.send 확장은 [음성 계약](voice.md)을 따른다.

공통 규칙은 [계약 README](README.md). 재연결 뒤 상태는 [State 스냅샷](api.md)으로 다시 읽는다.

## WebSocket `/ws/sessions/{id}?token=<세션 JWT>`

프레임 봉투: `{"type": <이름>, "seq": <int>, "payload": {...}}`. `seq`는 세션 단위로 1씩 늘고(서버 → 클라이언트 프레임만), 모든 서버 프레임은 **세션별 단일 송신 큐** 하나로만 나간다(순서 보장). api는 같은 세션의 행 잠금·커밋·seq 부여·송신 큐 넣기를 세션별 순서 잠금 하나 안에서 하므로 seq 순서가 커밋 순서와 같고, 실제 소켓 전송은 잠금 밖의 세션별 송신 작업이 순서대로 한다. 예외: 연결 교체의 `session.replaced`(seq 0)와 페르소나 `typing`(저장되지 않는 표시). 프레임 전송이 10초 안에 끝나지 않거나 실패하면 서버가 그 연결을 1011로 닫고 대기 프레임을 버린다. 클라이언트는 재연결해 `/state`의 `ws_seq`부터 잇는다. 클라이언트가 끊은 경우와 서버가 끊은 경우 모두 `connection_gaps`에 한 번 기록된다. 클라이언트는 받은 seq가 마지막 seq + 1이 아니면(빠짐) 프레임 적용을 멈추고 `/state`를 다시 읽어 `ws_seq`부터 이어 간다. 카운터는 프로세스 메모리 값이라 api 재시작 뒤 0부터 다시 시작하며, 재시작은 연결이 끊기므로 클라이언트는 재연결 뒤 항상 `/state`를 먼저 읽는다.

서버 → 클라이언트:

| type | payload |
|---|---|
| message.ack | {client_msg_id, message_id, run_id}. 메시지 저장 커밋 직후, 의도 분류 전에 보낸다 |
| message.new | Message = {id, sender: candidate∣p1∣p2∣p3∣system, content, phase, is_invite, t_min, created_at}. 합의·시간 초과 채우기 공지는 `slot.confirmed` 바로 앞에 온다 |
| typing | {participant: p1∣p2∣p3, state: "start"∣"stop"}. 화면 "OO님이 입력 중…". start 뒤 30초 안에 stop이 없으면 화면이 끈다 |
| slot.confirmed | {phase, value, filled_by, resolution: "consensus"∣"timeout", outcome: "consensus"∣"candidate_decision"∣"timeout", t_min} |
| phase.changed | {phase, previous_phase, reason: "start"∣"confirmed"∣"timeout", phase_started_at, phase_deadline_at, t_min} |
| timer | {phase, phase_deadline_at, phase_remaining_seconds, session_remaining_seconds}. 단계 전환 때와 60초마다(시계 보정). 화면은 로컬로 카운트다운 |
| materials.opened | {keyword, materials: [{material_id, title}]} |
| session.ended | {reason: "submitted"∣"timeout"∣"failed"∣"cancelled"}. submitted·timeout이면 이미 completed. 화면은 입력을 잠그고 [나가기]로 디브리핑(API 호출 없음) |
| session.replaced | {} 뒤 close 4000 |
| error | {code: `session_ending`∣`conflict`∣`invalid_input`, message} |

클라이언트 → 서버:

| type | payload |
|---|---|
| message.send | {client_msg_id, content}. 토론방 하나라 채널 없음. 같은 client_msg_id 재전송은 기존 메시지로 ack. in_progress의 모든 단계에서 허용(proposal 단계 포함) |
| typing | {state: "start"∣"stop"}. 지원자 입력 중 여부. 입력창에 글자가 있는 동안 **5초마다 `start`를 다시 보내 갱신**하고, 비거나 전송하면 `stop`. 서버는 저장하지 않고 메모리에 `candidate_typing_until = 마지막 start + 6초`를 둔다. `stop`이나 6초 동안 갱신 없음이면 입력 중이 아니다. 끼어들기 대기(최대 10초 뒤 미게시·재큐)와 스케줄러 enqueue 보류에만 쓴다 |

close code: 4401(토큰 문제 → resume 후 재연결), 4403(다른 세션, 재연결 안 함), 4000(교체됨, 재연결 안 함). 같은 세션에 WS가 둘이면 마지막만 유효. 연결 없는 구간은 `connection_gaps`에 기록(평가하지 않음. 사실 지표·확신도 참고).
