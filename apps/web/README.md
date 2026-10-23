# web

현재 음성 회의·Azure F0 설정·오류 복구는 [음성 구현](../../docs/architecture/voice.md), 공개 인터페이스는 [음성 계약](../../docs/contracts/voice.md)을 따른다. 공용 채팅 입력창과 말풍선은 음성 회의 UI로 대체한다. 내부 메시지 기록과 기존 합의·평가 경로는 유지한다.

지원자 토론방·기획안·디브리핑 화면과 담당자 관리자 화면(채용 건·지원자 목록·리포트·전체 기록)을 내는 Next.js 앱.

## 한눈에

| 항목 | 내용 |
|---|---|
| 역할 | 지원자: 초대 링크 입장 → 동의 → 토론방(채팅·결정 사항·참고 자료·어시스턴트·기획안) → 디브리핑. 담당자: 가입·로그인, 채용 건 생성, 초대, 리포트 검토, 전체 기록 |
| 사용 기술 | Next.js App Router(standalone 빌드), React, Tailwind v4, base-ui + cva + clsx, vitest, Playwright, pnpm, Node 22 |
| 포트 | 3000 (헬스 `/api/health`) |
| 필요한 env | `NEXT_PUBLIC_API_URL`(기본 `http://localhost:8000`) `NEXT_PUBLIC_WS_URL`(기본 `ws://localhost:8000`) `NEXT_PUBLIC_MOCK`(`1`이면 api 없이 mock). 테스트용: `WEB_URL` `E2E_INTERNAL_URL` `INTERNAL_TOKEN` |
| 의존 서비스 | api 공개 앱(8000, REST + WebSocket). mock 모드에서는 없음 |

`NEXT_PUBLIC_*`는 **빌드 시점**에 번들에 들어간다. `pnpm dev`는 셸 환경변수나 `apps/web/.env.local`을 읽고(Python 서비스와 달리 `infra/.env`를 읽지 않는다), compose 빌드는 `apps/web/Dockerfile`의 `ARG`로 받으므로 컨테이너 실행 중에 바꿔도 반영되지 않는다. 단계별 화면 테스트의 `INTERNAL_TOKEN`은 환경변수 또는 `infra/.env.e2e`에서 읽는다.

## 구현된 기능

| 기능 | 코드 | PRD |
|---|---|---|
| 가입·로그인 | `app/admin/signup`, `app/admin/login`, `components/admin/auth-form.tsx` | [F-01](../../PRD.md) |
| 채용 건 생성, 샘플 리포트, 초대, 지원자 목록 | `components/admin/position-form.tsx`, `candidates.tsx`, `app/admin/sample-report` | [F-02, F-03, F-04b, F-08](../../PRD.md) |
| 리포트 보기·차원 조정·검토 완료·인쇄 | `components/admin/report-view.tsx`, `review.ts` | [F-04, F-04a, F-04c, F-09](../../PRD.md) |
| 전체 기록과 근거 이동 | `components/admin/record-view.tsx` | [F-05](../../PRD.md) |
| 초대 링크 입장·동의·이어하기 | `components/debate/link-screens.tsx`, `candidate-flow.tsx` | [F-10, F-11, F-19](../../PRD.md) |
| 토론방(말풍선·입력 중·새 메시지 표시) | `components/debate/chat.tsx`, `reducer.ts`, `lib/ws.ts` | [F-12](../../PRD.md) |
| 결정 사항 패널과 확인 창 | `components/debate/decision-panel.tsx` | [F-13](../../PRD.md) |
| 단계 진행바·남은 시간 | `components/debate/debate-view.tsx` | [F-14](../../PRD.md) |
| 참고 자료 뷰어(Markdown), 어시스턴트 | `components/debate/side-panels.tsx`, `markdown.tsx` | [F-15, F-16](../../PRD.md) |
| 기획안 작성(디바운스 자동 저장)·제출 | `components/debate/proposal-editor.tsx`, `saver.ts` | [F-17](../../PRD.md) |
| 디브리핑·설문 | `components/debate/debrief-screen.tsx` | [F-18](../../PRD.md) |

기능별 검증 테스트와 상태는 [`docs/features.md`](../../docs/features.md)에 있다.

## 화면 경로

| 경로 | 화면 |
|---|---|
| `/` | 안내(mock에서는 데모 링크) |
| `/s/[token]` | 지원자: 입장 → 동의 → 토론방 → 디브리핑 |
| `/admin/signup`, `/admin/login` | 담당자 가입·로그인 |
| `/admin/positions`, `/admin/positions/new`, `/admin/positions/[id]` | 채용 건 목록·생성·지원자 목록 |
| `/admin/sessions/[id]/report`, `/admin/sessions/[id]/record` | 리포트, 전체 기록 |
| `/admin/sample-report`, `/admin/sample-report/record` | 샘플 리포트와 기록 |
| `/api/health` | compose 헬스 체크 |

## 실행

저장소 루트에서 실행한다.

```
pnpm --dir apps/web install --frozen-lockfile        # 처음 한 번
pnpm --dir apps/web dev                              # http://localhost:3000 (api는 NEXT_PUBLIC_API_URL)
NEXT_PUBLIC_MOCK=1 pnpm --dir apps/web dev           # api 없이 mocks/의 계약 예시로 동작
make up                                              # 전체 스택(컨테이너 web 포함)
```

mock 모드의 데이터 구성은 `mocks/README.md`에 있다. `?data=worst`를 주소에 붙이면 최악 데이터로 화면을 본다.

## 구조

- 토큰: `app/globals.css`의 `@theme`(색·타이포·간격·모서리)
- 공용 컴포넌트: `components/ui/`(base-ui 프리미티브 + cva). props는 고정, 내부 모양은 디자인 다듬기에서 바꾼다
- API·WebSocket 클라이언트: `lib/api.ts`, `lib/ws.ts`(mock 분기 포함)
- 라이브러리 선택은 `emilkowalski/skills`의 pick-ui-library 기준: 프리미티브 base-ui, 클래스 clsx, 변형 cva

## 테스트

| 종류 | 도구 | 실행 | 필요한 것 |
|---|---|---|---|
| 단위·컴포넌트 | vitest | `pnpm --dir apps/web test` 또는 `make test` | 없음 |
| 단계별 화면 | Playwright | `make e2e-stack-up` → `make e2e-stack-seed` → `make e2e-stages [STAGE=…]` → `make e2e-stack-down` | 도커 스택(별도 compose 프로젝트, web 3100), `infra/.env.e2e` |
| 린트 | eslint + prettier | `make lint` | 없음 |

단계별 화면 테스트는 내부 테스트 API로 단계 시드 세션을 만들어 실제 화면을 조작하고 스크린샷을 `tests/e2e/records/stages/`에 남긴다. 스택 준비는 [`docs/guides/testing.md`](../../docs/guides/testing.md)를 따른다.

<!-- gen:tests -->
> 생성 블록이다. 코드를 바꾸면 `make docs-gen`으로 갱신한다.

**단위·컴포넌트 테스트(vitest, `pnpm test`)**: 테스트 제목 85개(정적 파싱)

<details><summary><code>app/api/health/route.test.ts</code> (1)</summary>

- GET /api/health returns ok

</details>

<details><summary><code>components/admin/auth-form.test.tsx</code> (1)</summary>

- 가입 화면은 회사 이름·이메일·비밀번호를 받고 로그인 화면은 회사 이름이 없다

</details>

<details><summary><code>components/admin/candidates.test.tsx</code> (2)</summary>

- 지원자 목록: 묶음 5종, 수준 칩, 근거 약함, 출처, 실패 안내, 삭제 예정일
- 링크 재발급은 시작 전·만료 행, 재응시 초대는 실패·취소 행에서만

</details>

<details><summary><code>components/admin/format.test.ts</code> (2)</summary>

- evaluate_failed는 판단 보류 문구, 나머지 사유는 기존 문구
- 기록 요약 끝의 outcome 토큰을 표시 글자로 바꾼다

</details>

<details><summary><code>components/admin/position-form.test.tsx</code> (2)</summary>

- 생성 화면은 이름·세션 길이만 받고 요소는 안내로만 보인다
- mock 생성은 elements를 거부하고 세션 길이 60·15만 받는다

</details>

<details><summary><code>components/admin/report-failed.test.tsx</code> (1)</summary>

- 실패 화면은 안내 문장과 [다시 만들기] 버튼을 보이고, 요청 중에는 버튼이 잠긴다

</details>

<details><summary><code>components/admin/report-states.test.ts</code> (2)</summary>

- 조회 응답을 리포트·없음·실패로 나누고 실패 지점을 읽는다
- 다시 만들기가 성공하면 생성 중 409를 거쳐 리포트가 열린다

</details>

<details><summary><code>components/admin/report-view.test.tsx</code> (7)</summary>

- 샘플 리포트 픽스처: 5요소·19차원, 사유 5종, 미발생 카드 1장, 조정 1개, 확신도 3종, 근거 4종
- 샘플 리포트 화면: 사유 문장 5종, 미발생 카드, 담당자 조정 나란히, 조정·검토 UI 없음
- 카드 장면: 발동했지만 일부 동료만 전달한 카드에 표시, 전원 전달·미발생 카드에는 없음
- 리포트 draft 렌더: 안내·요약·타임라인·차원·근거·사실·기획안·AI 활용
- 차원 조정은 메모 필수, 요소 수준 재계산, 검토 완료 → 확정 중 → final → 메모 추가
- 전체 기록: 근거 앵커 줄 하이라이트, 오피스 시각, 단계·결과 한국어
- 사실 지표에 제출 때 빈 칸과 빈칸 경고 확인이 나오고, 칸 결과는 outcome으로 보인다

</details>

<details><summary><code>components/admin/review.pending.test.ts</code> (4)</summary>

- 리포트 409 report_not_ready는 만드는 중으로 보고 5초마다 다시 읽는다
- 리포트 조회 오류 코드를 생성 중·실패·없음으로 나눈다
- 확정 중 report_failed·report_not_ready가 와도 이전 리포트에 가려지지 않는다
- 생성 중 폴링은 일시 오류(503·네트워크)에서 이어지고 성공·실패·없음에서 끝난다

</details>

<details><summary><code>components/debate/composer.test.tsx</code> (1)</summary>

- 글자가 있는 동안 5초마다 start를 갱신하고, 비우면 stop을 보낸다

</details>

<details><summary><code>components/debate/flow.test.ts</code> (4)</summary>

- 15분 데모: 표시 이름이 토론방에 쓰이고, 정하기·자료·기획안·제출·디브리핑이 계약대로 이어진다
- 링크 상태: 만료·취소 410, 모르는 토큰 404, 진행 중 스냅샷은 이어하기
- WS error 프레임: ack 못 받은 내 메시지는 실패로 표시하고, session_ending이면 입력을 잠근다
- WS error 프레임이 seq를 쓰면 마지막 seq를 올려 다음 프레임이 재조회 없이 적용된다

</details>

<details><summary><code>components/debate/markdown.test.tsx</code> (2)</summary>

- 자료 Markdown을 제목·목록·표·강조로 그리고 원문 HTML은 버린다
- 어시스턴트 답은 굵게·목록·코드만 서식으로 그리고 링크·이미지·HTML·제목은 글자로 둔다

</details>

<details><summary><code>components/debate/proposal-editor.dom.test.tsx</code> (2)</summary>

- 제출 요청 중에는 입력이 잠기고, 실패해 끝나면 풀린다
- 확인 전·요청 중 제출 버튼이 비활성이고 onSubmit은 한 번만 불리며, 취소하면 잠금이 풀린다

</details>

<details><summary><code>components/debate/proposal-editor.test.ts</code> (6)</summary>

- 저장 표시는 실제 시계 없이 상대 표기만 쓴다
- 공백뿐인 칸만 빈 칸으로 세고, 제출 본문은 경고를 거쳤을 때만 blank_warning_shown을 담는다
- 빈 칸이 없으면 경고 없이 확인 창 뒤 제출한다
- 빈 칸이 있으면 경고 → 확인 창 → blank_warning_shown 제출
- [돌아가서 쓰기]나 확인 창 취소면 submit을 부르지 않는다
- 저장(409 포함) 뒤 돌려받은 최신 초안으로 빈 칸을 다시 검사한다

</details>

<details><summary><code>components/debate/saver.test.ts</code> (1)</summary>

- flush는 진행 중인 저장이 끝날 때까지 기다리고, 실패하면 false라 제출이 멈춘다

</details>

<details><summary><code>components/debate/speech.test.ts</code> (1)</summary>

- 음성을 ${FAIL_LIMIT}번 연달아 받지 못하면 AI 동료 음성을 끄고 더 요청하지 않는다

</details>

<details><summary><code>components/debate/stage-layout.test.ts</code> (4)</summary>

- 세션 첫 화면은 프로필 카드만 크게 보이고, 지원자 차례가 오면 토론방이 결정 사항과 함께 열린다
- 자료가 열리면 결정 사항 아래에 자료·AI 어시스턴트 묶음이 생긴다
- 기획안 단계는 오른쪽 위가 기획안으로 바뀌고 단계 안내가 왼쪽 아래에 남는다
- 상단 바에서 직접 켜거나 끈 영역은 자동 판단보다 앞서고, 인사 화면에서는 무시된다

</details>

<details><summary><code>components/debate/voice/caption.test.ts</code> (2)</summary>

- 문장 끝 뒤 공백에서 나누고 빈 문장은 버린다
- 재생 위치까지 말한 문장 수를 센다(글자 수 비율)

</details>

<details><summary><code>components/debate/voice/memo-pad.dom.test.tsx</code> (3)</summary>

- 입력하면 세션별로 저장되고 다시 열면 이어진다
- 저장소가 막혀도 입력은 되고 저장 안 됨을 알린다
- 세션이 끝나면 읽기 전용이다

</details>

<details><summary><code>components/debate/voice/recorder.test.ts</code> (4)</summary>

- 버튼과 타이머가 겹쳐도 녹음 종료는 한 번만 실행한다
- 완성 오디오를 mono 16kHz PCM WAV로 만들고 클리핑을 제한한다
- 48kHz를 16kHz로 줄이면 길이가 3분의 1이고 구간 평균을 쓴다
- PCM16 리틀 엔디언 원본으로 바꾸고 범위를 넘는 값은 자른다

</details>

<details><summary><code>components/debate/voice/streamer.test.ts</code> (1)</summary>

- 정리를 두 번 불러도 예외 없이 끝난다(녹음 종료 뒤 메시지 제출 때 다시 불림)

</details>

<details><summary><code>components/debate/voice-fallback.test.ts</code> (2)</summary>

- 글자 변환은 앞이 실패하면 다음으로 넘어가고, 모두 실패하면 오류를 낸다
- 글자 변환이 ${FAIL_LIMIT}번 연달아 실패하면 말하기를 끄고 다시 시작하지 않는다

</details>

<details><summary><code>components/debate/voice.test.ts</code> (1)</summary>

- 마이크 소리 크기: 무음은 0, 큰 소리는 1로 자르고, 빠르게 오르고 천천히 내려온다

</details>

<details><summary><code>components/debate/wording.test.ts</code> (3)</summary>

- 디브리핑 전 지원자 화면·mock에 페르소나 유형 이름이 없다
- 옛 흐름 흔적(보드·분업·파트 문서·팀장·개인 알림·slot.filled)이 지원자 화면·mock에 없다
- 지원자 화면 문구에 내부 용어(칸·합의 패널·확정)가 없고 지원자 말을 쓴다

</details>

<details><summary><code>components/ui/contrast.test.ts</code> (3)</summary>

- 계산식 확인: 흰색·검정 21:1, 다크 accent(#2997FF) 위 흰 글자는 AA 미달
- 인쇄 블록은 다크 테마에서도 라이트 글자색(ink)을 흰 종이 위에 둔다
- 시스템 다크(:root:not(.light))와 강제 다크(:root.dark) 값이 같다

</details>

<details><summary><code>components/ui/split-layout.test.ts</code> (3)</summary>

- 사용자 비율은 보이는 칸 구성과 구간(epoch)이 같을 때만 기본 비율을 덮는다
- 경계를 끌면 두 칸의 합은 그대로이고 최소 크기 밑으로 줄지 않는다
- 사용자가 끈 분할의 칸은 fit·square 대신 비율로 나뉜다

</details>

<details><summary><code>components/ui/ui-dom.test.tsx</code> (2)</summary>

- Avatar: 이미지 onError가 나면 이미지를 걷어내고 이니셜만 남긴다
- Tabs: 방향키로 포커스를 옮기고 선택하면 연결된 패널이 보인다

</details>

<details><summary><code>components/ui/ui.test.tsx</code> (7)</summary>

- Avatar: 이미지가 없으면 이름 첫 글자 이니셜을 자리 색으로 그린다
- Avatar: 이미지는 원형 크롭·cover로 겹치고, 실패 대비 이니셜이 아래에 깔린다
- Avatar: 기존 label·tone props와 AvatarGroup이 그대로 동작한다
- LevelChip: 수준·목록용·기록 없음 사유·담당자 조정을 글자로 표기한다
- LevelChip: 타입이 수준 1~5만 받고, 기록 없음이면 사유를 요구한다
- ConfidenceTag: 확신도를 아이콘+글자로, 낮음은 원문 확인 권장 문구를 붙인다
- Tabs: segmented 모양에서도 같은 items props로 탭과 배지를 그린다

</details>

<details><summary><code>lib/api.test.ts</code> (5)</summary>

- 단위 테스트는 실 api 경로를 탄다(NEXT_PUBLIC_MOCK 꺼짐)
- GET /s/{token}·/state: 계약 응답을 파싱하고 세션 토큰을 Authorization으로 보낸다
- POST 칸 확정: 계약 요청 본문을 JSON으로 보낸다
- 오류 응답 {error:{code,message}}는 ApiError(status, code)로 바뀐다
- avatar_url의 /assets 경로는 api 주소를 붙이고 다른 주소는 그대로 둔다

</details>

<details><summary><code>lib/resolution.test.ts</code> (2)</summary>

- outcome이 표시를 정한다
- outcome이 없으면 timeout만 시간 초과이고 나머지는 라벨이 없다

</details>

<details><summary><code>lib/voice-server.test.ts</code> (2)</summary>

- 음성 모델 키 규칙: 음성 두 모델과 gpt-4o-mini는 제한 키, 그 밖의 gpt-*는 개인 키, 비면 멈춘다
- VOICE_API=1이 아니면 음성 라우트는 닫혀 있다

</details>

<details><summary><code>lib/ws.test.ts</code> (2)</summary>

- 재연결: 3초 뒤 다시 붙어 onOpen(reconnected)을 알리고, 4401은 재발급, 4000은 멈춘다
- seq 공백이면 적용을 멈추고 /state 스냅샷으로 칸·메시지·입력 중을 복원한 뒤 이어서 적용한다

</details>

**단계별 화면 테스트(Playwright, `make e2e-stages`)**: 테스트 제목 10개(정적 파싱)

<details><summary><code>e2e/admin-capture.spec.ts</code> (0)</summary>


</details>

<details><summary><code>e2e/stages/admin-report.spec.ts</code> (1)</summary>

- 리포트 → 차원 조정 → 검토 완료 → 재계산

</details>

<details><summary><code>e2e/stages/debrief.spec.ts</code> (1)</summary>

- 디브리핑: 첫 문장·내 발언·설문

</details>

<details><summary><code>e2e/stages/entry.spec.ts</code> (1)</summary>

- 입장 → 동의 → 대기(참여자) → 시작

</details>

<details><summary><code>e2e/stages/keyword.spec.ts</code> (1)</summary>

- 후보 확정 → 결정 사항 → 문제 상황 단계 → 자료 열림

</details>

<details><summary><code>e2e/stages/problem.spec.ts</code> (1)</summary>

- 수용 카드 발언 → 시간 초과 채우기 → 입력 닫힘

</details>

<details><summary><code>e2e/stages/proposal.spec.ts</code> (1)</summary>

- 디바운스 저장 → 제출 확인 → 잠김 → 디브리핑

</details>

<details><summary><code>e2e/stages/solution.spec.ts</code> (3)</summary>

- AI 합의 채우기: 공지 메시지 → 결정 사항 칸 확정 → 기획안 단계
- silent: 침묵하면 idle 뒤 비판형 초대 메시지가 1건
- solo_confirm: 반대가 남아도 지원자가 혼자 확정하면 칸이 확정된다

</details>

<details><summary><code>e2e/stages/voice.spec.ts</code> (1)</summary>

- 음성 회의: 채팅 UI 없음·AI 종료 뒤 녹음·60초 자동 제출 1회

</details>
<!-- /gen:tests -->

## 관련 문서

- [`docs/services/web.md`](../../docs/services/web.md): 화면 구성과 상태 관리
- [`docs/architecture/design-system.md`](../../docs/architecture/design-system.md): 토큰·컴포넌트
- [`docs/product/user-flows.md`](../../docs/product/user-flows.md): 화면 흐름
- [`docs/contracts/ws.md`](../../docs/contracts/ws.md): WebSocket 계약
- [`docs/guides/testing.md`](../../docs/guides/testing.md): 테스트 종류와 실행법

## 토론 화면 배치와 음성 입출력(시험)

토론 화면(`components/debate/debate-view.tsx`)은 크기를 바꿀 수 있는 분할 레이아웃이다. 위 문서들의 "우측 탭" 설명은 이 배치로 바뀌는 중이며, 바뀐 결정과 남은 문서 정리는 PR에서 다룬다.

- 배치: 시작하면 프로필 카드만 크게 보이고(인사 차례), 지원자 차례가 오면 토론방이 가운데에 열린다. 왼쪽은 프로필(누르면 이름과 이번 단계에서 한 말)·음성 패널·단계 안내, 오른쪽은 위가 결정 사항(기획안 단계는 기획안), 아래가 자료·AI 어시스턴트 탭이다. 상단 바에서 오른쪽 영역을 켜고 끈다.
- 크기 조절: 영역 사이 경계를 끌거나 화살표 키로 바꾸고, 두 번 누르면 원래대로 돌아간다. 엔진은 `components/ui/split-layout.tsx`, 단계별 규칙은 `components/debate/stage-layout.ts`.
- 말하기(STT): 입력창 옆 마이크로 녹음하면 글자로 바뀌어 입력창에 들어가고, 지원자가 고쳐서 보낸다. 녹음은 저장하지 않는다. 녹음 중에는 typing을 보내지 않는다(계약 그대로).
- AI 동료 음성(TTS): 음성 패널에서 켜면 새로 온 AI 동료 메시지를 읽고, 읽는 동료를 프로필에서 강조한다. 목소리는 자리별로 고정하고 말투 지시는 세 명 모두 같다.
- 대체 순서: 글자 변환은 웹 서버 라우트 → mock 예시 문장 → 없음 순서로 쓴다. 서버 음성 기능이 없거나(`/api/voice/status`) 2번 연달아 실패하면 그 화면에서 말하기·AI 동료 음성을 끄고 글자로만 진행한다. 플래그가 없으면 음성 UI는 보이지 않고 기존 화면과 같게 동작한다.
- 켜는 법(개발용): `NEXT_PUBLIC_VOICE_API=1`(화면), `VOICE_API=1`(서버 라우트 `/api/voice/transcribe`·`/api/voice/speech`). 모델은 `MODEL_STT`(기본 `gpt-4o-mini-transcribe`)·`MODEL_TTS`(기본 `gpt-4o-mini-tts`), 키 선택은 `lib/voice-server.ts`의 `voiceKey`.
