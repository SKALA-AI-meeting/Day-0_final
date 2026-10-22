# web (Next.js)

음성 토론의 상태·전사·오류·비용은 [음성 구현](../architecture/voice.md)을 따른다.

지원자·관리자 화면의 구현 구조다. 화면 흐름과 화면 규칙은 [product/user-flows.md](../product/user-flows.md), 토큰·컴포넌트·라이브러리·모션은 [architecture/design-system.md](../architecture/design-system.md), 실행 명령은 `apps/web/README.md`. Next.js App Router + Tailwind. 상태 관리는 React 상태와 fetch로 충분하다. UI 라이브러리는 [architecture/design-system.md](../architecture/design-system.md)의 "라이브러리" 표에 있는 것만 쓴다.

## 구조

```
apps/web/
  app/                  라우트: /admin/*(login·signup·positions·sessions·sample-report), /s/[token], /api/health
  components/debate/    지원자 화면(candidate-flow·debate-view·chat·decision-panel·side-panels·proposal-editor·debrief-screen·link-screens), reducer·use-session·saver
  components/admin/     관리자 화면(채용 건·지원자 목록·리포트·전체 기록·검토)
  components/ui/        공용 컴포넌트(디자인 시스템)
  components/dev/       mock 조작 패널(NEXT_PUBLIC_MOCK=1에서만)
  lib/api.ts, lib/ws.ts REST 래퍼(ApiError)와 WS 연결·재연결
  mocks/                계약 예시 JSON 사본과 WS 재생 대본(NEXT_PUBLIC_MOCK=1)
  fixtures/             샘플 리포트(sample-report.json)
  e2e/                  Playwright 단계별 화면 테스트(stages/)와 mock 캡처 스크립트
```

- 상태는 React 상태와 fetch로 다룬다. `/state` 스냅샷이 전체 상태이고 WS 프레임은 증분이다(`seq`가 비면 `/state` 재조회).
- `NEXT_PUBLIC_API_URL`·`NEXT_PUBLIC_WS_URL`·`NEXT_PUBLIC_MOCK`은 **빌드 시점**에 번들에 들어간다(`apps/web/Dockerfile` ARG). 컨테이너 런타임 env만 바꿔서는 바뀌지 않으므로 이미지를 다시 빌드한다. 값이 없으면 `http://localhost:8000`·`ws://localhost:8000`(`lib/api.ts`). 실행 모드별 값은 [guides/configuration.md](../guides/configuration.md).

## 이름의 정본

- API·WS 이름은 [contracts/README.md](../contracts/README.md), 요소·카드·차원·기록 없음 사유 이름은 [product/rubric.md](../product/rubric.md)·[product/scenario.md](../product/scenario.md)가 정본이고, 이 문서의 이름이 정본과 다르면 정본이 맞다. 칸 결과 필드는 `resolution: consensus | timeout`, 채운 사람은 자리 id `filled_by: candidate | p1 | p2 | p3`이다(시간 초과도 공지한 자리 id, `system` 값은 없다).

## mock

`apps/web/mocks/`에 계약 예시 JSON과 WS 재생 스크립트를 두고 `NEXT_PUBLIC_MOCK=1`이면 사용한다(`mocks/README.md`). 형태는 전부 [contracts/README.md](../contracts/README.md)와 같다.

| 데이터 | 형태 |
|---|---|
| `entry.json`·`entry_completed.json` | GET /s/{token} 응답(진행 전·완료 후) |
| `state_keyword`·`state_problem`·`state_solution`·`state_proposal.json` | State 스냅샷 4벌. 캡처 지점을 바로 연다 |
| `debate_script.json` | 15분 데모 재생 대본(R3): 모든 프레임에 세션 단위 `seq`, `message.new`(강한결·윤하람·서지안·system, 침묵 초대 `is_invite` 포함, R7 시간 초과 문구 1건), `typing` start/stop, `slot.confirmed`(consensus 1·timeout 1), `phase.changed`·`timer`, `materials.opened`, `session.ended`. 시각은 `t_min`. `slot.filled`는 없다 |
| `materials.json`·`material.json` | 자료 목록(잠김·열림)과 Markdown 본문 |
| `assistant_response.json` | 어시스턴트 응답(인용 자료 포함) |
| `proposal.json`·`proposal_submit.json` | Proposal과 제출 응답 |
| `debrief.json`·`debrief_failed.json` | 디브리핑(R1 첫 문장·이의 제기 경로·카드 설명·요소 이름과 "이렇게 봅니다" 한 줄·내 발언 최대 3개(단계별 최장)·자리 성향·기획안·설문 2문항+자유 의견) |
| `admin_catalog.json` | 요소 5개 읽기용 설명·세션 길이 옵션(60·15). 선택 필드·`if_unselected` 없음 |
| `admin_candidates.json` | 상태 묶음 5종 각 1행 이상, 요소별 수준 칩(1~5·기록 없음·담당자 조정 1행 이상)과 low 차원 수, 실패 행. 합계 필드 없음 |
| `fixtures/sample-report.json` | Report(계약 형태): `elements[]` 5개(element·level·summary·dimension_count·no_record_count), `dimensions[]` 19개([product/rubric.md](../product/rubric.md) 3장 id, level·no_record_reason·evidence_refs·rationale·confidence, 확신도 high·medium·low 모두, 수준 4·5는 근거 2개 이상), 근거 참조 4종(`msg:`·`slot:`·`proposal:`·`contribution:`)과 메시지 근거의 앞 5·뒤 3 맥락, 기록 없음 사유 5종, 담당자 조정 차원 1개(memo 포함), 카드 6장(미발생 1), contribution_check, facts, ai_usage. 총점·합계 필드 없음 |
| `admin_record.json` | 전체 기록. `record_ref`가 sample-report와 맞음 |
| `public/mock-avatars/avatar-01~05.png` | 임시 프로필 이미지(가로·세로 비율이 서로 다른 파일을 섞어 원형 크롭 검증). 실제 앱은 api의 `avatar_url` |
| `worst.ts` | break-ui용: 긴 별칭·긴 표시 이름, 300자 메시지, 빈 자료, 모든 칸 timeout, 모든 차원 기록 없음인 요소, 깨진 `avatar_url` |

샘플 리포트(/admin/sample-report)는 sample-report·record 픽스처를 그대로 렌더한 정적 페이지다.

## 화면 검증 항목

- 지원자 전 화면·전 상태가 mock으로 돈다: 입장(만료·취소·완료 링크), 동의 화면의 R1 형식 안내·R7 어시스턴트 문구·표시 이름 입력(기본값 입장 응답의 초대 이름)과 그 이름이 토론방·AI 동료 호칭에 쓰임, 동의 전 시작 불가, 대기 화면 레이아웃 그림과 시작 전 `/state`로 그린 이름만 보이는 팀 소개, 토론 세션, 디브리핑(R1 첫 문장·이의 제기 경로·카드 설명·내 발언 2~3개·성향 공개·기획안 열람·설문 2문항+자유 의견·문의), 재연결과 `seq` 누락 시 `/state` 복원.
- 지원자 화면 용어: "칸"·"합의 패널"·"확정"이 지원자 화면 문구에 없고 "정할 항목"·"결정 사항"·"이걸로 정하기"를 쓴다(R7).
- 토론 세션: 상단 4단계 진행바와 단계 남은 시간(로컬 카운트다운·`timer` 보정·2분 전 warning), 남은 시간과 오피스 시각이 한 줄에 함께 나오지 않고 오피스 시각은 메시지 타임스탬프에만, iMessage식 말풍선(지원자 오른쪽 강조색, AI 동료 3명 자리별 연한 틴트와 이름+프로필 이미지, 시스템 안내는 가운데 작은 회색). 프로필 이미지는 비율이 다른 임시 파일에서도 원형 고정 크기로 보이고, 깨진 URL이면 이니셜로 대체, 입력 중 표시(여러 명·30초 자동 끔), `message.ack` 확정 표시, 침묵 초대 메시지가 일반 메시지와 같은 모양.
- 우측 4탭: 결정 사항에 결과 줄만(후보안·지지 현황·오피스 시각 없음), 현재 항목만 입력·[이걸로 정하기], `slot.confirmed` timeout 결과 표시(R7 문구)와 입력 값 잠김; 입력 중 typing 5초 갱신; 자료 잠김 → `materials.opened`로 열림 → Markdown 뷰어; 어시스턴트 요청·인용·복사·한도 안내; 기획안 6칸 디바운스 저장, `idea_credit` 중립 안내 한 줄, 토론 단계 제출 비활성, 제출 확인 창 → 잠김 → [나가기] → 디브리핑, 자동 제출 한 줄.
- 페르소나 유형이 **디브리핑 전** 지원자 화면과 응답에 없다: 디브리핑 컴포넌트·`debrief*.json`을 뺀 지원자 컴포넌트와 지원자 mock(`entry*`·`state_*`·`debate_script`·`materials`·`material`·`assistant_response`·`proposal*`)에서 `stubborn|quiet|critic|agreeable|divergent` grep 0건. 디브리핑은 성향 공개가 정상이다.
- 관리자: 생성 화면은 이름·세션 길이(60·15)·요소 안내(읽기 전용, 선택 UI 없음)·샘플 리포트 링크, 링크 재발급과 재응시 초대가 구분된 행 액션, 지원자 목록 상태 묶음·요소별 수준 칩·low 차원 수(합계·정렬 없음)·실패 안내·삭제 예정일.
- 리포트 draft가 mock으로: 머리의 "설계된 장면" 안내와 카드 칩(R1), 요약표의 요소 "수준 N (1~5)"·서술(합계 없음), 타임라인(칸 결과·지지 현황·최초 제안자·카드 발동), 요소 섹션의 차원별 수준·확신도·판단 이유·근거, low 차원의 "근거 약함, 원문 확인 권장", 근거 클릭 시 원문·앞뒤 맥락, 맥락 참조 구분, 사유 5종 문장, 기여 대조표, 사실 지표, 제출 기획안, 시간순 AI 활용. 차원 단위 조정(메모 없으면 저장 불가)과 요소 수준 재계산·"담당자 조정됨"·검토 완료 확인 창(`reviewer_name` 포함, 422 표시)·final 메모 추가. "기록에서 보기"가 기록 화면의 해당 줄을 하이라이트. 인쇄 미리보기에서 차원 전부 펼침. 화면·mock 어디에도 총점·합계·점수 표기가 없다.
- D13-2: 라이트·다크 둘 다 동작, 말풍선·본문 대비 4.5:1 이상([architecture/design-system.md](../architecture/design-system.md) 대비 표), 이름이 항상 보여 색 없이도 화자 구분 가능, 그라데이션·반짝이·로봇 아이콘·네온·카드 반복 대시보드 없음, 그림자는 떠 있는 요소에만.
- 행동 유도 문구 없음, 보드·분업·파트 문서·팀장·개인 알림 흔적 없음: 지원자 컴포넌트·mock에서 `board.tsx|BoardColumn|division|my-docs|my_document|doc-viewer|manager|notices.test|office_script|slot.filled` grep 0건. 현행 이름 `SystemNotice`·`entry_notice`·`scoring_notice`·`retention_notice`와 "대시보드" 같은 일반 단어는 검사 대상이 아니다.
- UX 결정대로 캡처 6장(진행 중 토론방·입력 중, 단계 전환, 자료 탭, 기획안 작성, 디브리핑, 리포트 요약+근거)을 라이트로 뜰 수 있고 다크 1장(토론방)을 함께 뜬다.
- `review-animations`·`break-ui` 통과.

같은 항목을 실제 api(`NEXT_PUBLIC_MOCK=0`)에서는 단계별 화면 테스트(`make e2e-stages`, `LLM_FAKE=1`)로 확인한다([guides/testing.md](../guides/testing.md) "단계별 테스트").
