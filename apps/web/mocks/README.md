# mocks

`NEXT_PUBLIC_MOCK=1 pnpm dev`이면 `lib/api.ts`가 네트워크 대신 `mockRequest()`를, `lib/ws.ts`가 실제 소켓 대신 `createMockSocket()`을 쓴다. 형태는 전부 `docs/contracts/README.md`와 같다.

| 파일                                   | 내용                                                                                                   |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `index.ts`, `types.ts`, `ws-replay.ts` | 경로 매칭(경로 앞부분으로 표를 골라 필요할 때 불러옴), WS 봉투 `{type, seq, payload}` 재생             |
| `admin.ts`                             | 관리자 경로 표(`/admin/*`)와 메모리 상태(mock-admin 소유)                                              |
| `debate.ts`                            | 지원자 경로 표와 메모리 세션, MockSocket, 15분 데모 재생, 조작 패널용 `mockControls()`(mock-candidate) |
| `worst.ts`                             | break-ui 최악 데이터. 주소에 `?data=worst`                                                             |
| `fixtures/*.json`                      | `tests/fixtures/contracts/`에서 같은 이름으로 복사한 계약 예시(직접 고치지 않음)                       |
| `fixtures/debate_script.json`          | web 전용 15분 데모 대본(아래)                                                                          |

계약 예시가 바뀌면 `tests/fixtures/contracts/`에서 다시 복사한다. 화면만 쓰는 데이터는 `debate_script.json`에만 더한다.

## 15분 데모 대본 (`debate_script.json`)

계약 `ws_events.json`(60분 한 세션)을 15분 비율(t_min × 15/60)로 줄인 재생 대본이다. 모든 프레임에 세션 단위 `seq`가 있고, `message.new`(강한결·윤하람·서지안·system, 침묵 초대 `is_invite`, 시간 초과 시스템 안내), `typing` start/stop(두 명 동시 포함), `slot.confirmed`(키워드: 지원자 확정, 문제 상황: 합의 채우기, 해결책: 시간 초과), `phase.changed`·`timer`, `materials.opened`, 끝의 `session.ended{timeout}`(제출하지 않으면 15:00 자동 제출)이 들어 있다. 시스템 안내의 남은 시간은 15분 기준으로 고쳤다.

- `phase`: 그 프레임이 나가기 전 진행 중인 단계. mock은 지금 단계의 프레임만 내고, 단계가 바뀌면 지난 단계 프레임을 버린다.
- `anchor: "deadline"`: 단계 마감(누적 고정)에 묶인 프레임(마감 30초 전 안내, 시간 초과 채우기, 자동 제출). 그 밖의 프레임은 단계 시작 기준 상대 시각으로 재생해, 지원자가 일찍 정하면 다음 단계 대본이 그 시점부터 이어진다.
- 지원자가 직접 정하면 mock이 `slot.confirmed`·`phase.changed`·`timer`·`materials.opened`·단계 안내를 만들고, 대본의 같은 전환 프레임은 건너뛴다.
- 대본의 지원자 발언(ack + `message.new` candidate)도 재생된다(한 세션 전체를 보여 주는 데모). 직접 보낸 메시지는 ack 뒤 같은 모양으로 쌓인다.
- `keywords`: 카탈로그(`scenarios/missions/debate/keywords.yaml`·`materials/`)의 후보 4개와 자료 본문 복사본. **State 계약에 키워드 후보 목록이 없어** 화면의 키워드 버튼도 여기서 읽는다(열린 문제: 계약에 후보 목록 필드가 필요). `default_problem`·`default_solution`은 키워드별 첫 문제 상황·해결책 안(P1·S1)이다. mock은 안·지지를 기록하지 않으므로 대본 밖 시간 초과(재입장 링크·조작 패널)는 늘 "나온 안 없음" 규칙으로 단계 기준안을 고르고 그 규칙의 시스템 안내를 낸다.
- 프로필 이미지: 계약의 `/assets/missions/<id>/avatars/<file>`을 `public/mock-avatars/<file>`로 바꿔 보인다. 파일마다 가로·세로 비율이 달라 원형 크롭을 검증한다.

## 링크 토큰(지원자)

| 토큰                                      | 상태                                                                    |
| ----------------------------------------- | ----------------------------------------------------------------------- |
| `demo`                                    | 처음부터: 입장 → 동의 → 대기 → 15분 데모 재생                           |
| `keyword`·`problem`·`solution`·`proposal` | 진행 중 재입장(`state_*.json` 60분 스냅샷, 대본 없음·마감 시 시간 초과) |
| `done`                                    | 완료 → 디브리핑 다시 보기                                               |
| `failed`                                  | 세션 중단 안내                                                          |
| `expired`·`cancelled`                     | 410 안내. 그 밖의 토큰은 404                                            |

주소에 `?speed=20`을 붙이면 데모 시계를 20배로 돌린다(캡처 스크립트 `e2e/capture-mock.mjs`가 쓴다). 화면 왼쪽 아래 "mock 조작"에서 단계 건너뛰기, 입력 중 켜기, 칸 확정(consensus·timeout), seq 건너뛰기(재조회 확인), 자료 열림, 연결 끊기·다른 창, 기획안 충돌, 어시스턴트 한도, 종료 사유, 속도, 라이트·다크, Demo/Worst를 누른다. mock 서버 상태는 모듈 메모리라 새로고침하면 처음으로 돌아간다.
