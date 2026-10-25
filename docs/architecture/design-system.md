# 디자인 시스템 (web)

컨셉은 **Apple스러운 디자인**: 넓은 여백과 분명한 위계, 흰색·#F5F5F7 바탕, 시스템 블루 강조색 하나, 시스템 폰트 + Pretendard, 일관된 둥근 모서리와 얇은 구분선, 최소한의 반투명, 절제된 전환. 채팅(테스트 텍스트 모드와 관리자 전체 기록)은 iMessage식 말풍선이고 음성 회의 화면은 프로필 4칸·자막으로 구성한다. 참여자는 이름+아바타로 구분하고 색만으로 구분하지 않으며, 지원자는 표시 이름, AI 동료는 프로필 이미지를 쓴다. 라이트·다크 모두 지원한다.

토큰은 `apps/web/app/globals.css`가 정본이고 이 문서는 뜻·쓰임과 현재 값을 적는다. 라이트는 `@theme`, 다크는 `@media (prefers-color-scheme: dark)` 안의 `:root:not(.light)` 재정의다(시스템 설정만 따름). `:root.dark`·`.light` 클래스는 캡처·점검에서 테마를 강제로 켤 때만 쓴다. Tailwind 유틸리티 이름은 토큰 이름 그대로(`bg-accent-fill`, `text-on-accent`, `rounded-bubble-tail` 등)다. 값을 바꾸면 globals.css와 이 문서를 함께 고치고 대비 표를 다시 잰다. 관찰 목적·레이아웃과 화면별 규칙은 [product/user-flows.md](../product/user-flows.md).

## 디자인 컨셉: Apple스러운 디자인

기준은 Apple의 메시지·메모·설정 앱이 주는 차분함이다. 아래 절의 토큰·컴포넌트·라이브러리·모션이 이 컨셉을 구체화한다.

- 넓은 여백과 분명한 위계: 크고 굵은 제목, 차분한 본문. 요소를 줄이고 여백으로 묶는다.
- 바탕은 흰색과 연회색(#F5F5F7), 강조색은 시스템 블루 계열 **하나**. 글꼴은 시스템 폰트 스택(-apple-system, SF Pro 계열) + 한글 Pretendard.
- 일관된 둥근 모서리와 얇은 구분선. 구분은 그림자보다 선과 여백으로. 반투명(vibrancy)은 상단 바·떠 있는 패널에만. 애니메이션은 절제된 부드러운 전환만. 라이트·다크 모두 지원.
- 채팅: iMessage식 말풍선. 지원자 본인은 오른쪽 강조색 바탕에 흰 글자, AI 동료 3명은 왼쪽 자리별 연한 틴트(아래 "색")로 사람별 구분. 시스템 안내는 가운데 작은 회색 텍스트. 이름·아바타를 늘 함께 보이고 색만으로 구분하지 않는다.
- 피하는 것은 아래 "하지 않는 것"이 정본이다.
- 관리자 화면도 같은 컨셉이다. 리포트는 카드 격자가 아니라 한 열로 읽히는 문서(큰 제목 → 요약표 → 섹션), 목록은 얇은 행 구분선의 표.

## 색

### 바탕·글자·강조

| 토큰 | 라이트 | 다크 | 쓰임 |
|---|---|---|---|
| canvas | #F5F5F7 | #000000 | 페이지 바탕 |
| surface | #FFFFFF | #1C1C1E | 패널·입력·대화 상자 |
| surface-sunken | #F2F2F7 | #2C2C2E | 선택된 항목, 인용·코드, 분절 컨트롤 바탕 |
| line | #D2D2D7 | #38383A | 1px(또는 0.5px 레티나) 구분선. 그림자 대신 선 |
| ink | #1D1D1F | #F5F5F7 | 본문·제목 |
| muted | #6E6E73 | #A1A1A6 | 보조 글, 시스템 안내 |
| subtle | #86868B | #8E8E93 | 메타(아이콘·자리표시)와 입력 테두리(1px, 비텍스트 3:1). 본문 글자에는 쓰지 않음 |
| accent | #0066CC | #2997FF | **글자·아이콘**용 강조: 링크·선택 표시·포커스 링·현재 단계 글자. 강조색 계열은 이것 하나(시스템 블루) |
| accent-strong | #004F9E | #6CB4FF | accent 글자의 눌림·hover |
| accent-soft | #EEF5FE | #0A2A4D | 하이라이트(기록 화면·근거 메시지), 선택 배경, 결과 줄 강조 |
| accent-fill | #0071E3 | #0060DF | **흰 글자를 얹는 면**: 주 버튼, 지원자 말풍선과 같은 계열. #0071E3은 canvas 위 글자로는 4.3:1이라 글자에는 쓰지 않고 면에만 쓴다 |
| accent-fill-hover | #0066CC | #0050C0 | accent-fill 면의 hover·눌림 |
| on-accent | #FFFFFF | #FFFFFF | accent-fill·danger-fill 위 글자 |
| success / warning / danger | #1E7A35 / #B25000 / #D70015 | #30D158 / #FF9F0A / #FF453A | 합의 확정·마감 임박·오류. 아이콘과 글자에만, 넓은 면에는 soft |
| success-soft / warning-soft / danger-soft | #E7F5EA / #FDF0E3 / #FCE8EA | #0F2E18 / #3A2508 / #3D1412 | 상태의 넓은 면(배너·칩 바탕) |
| danger-fill | #D70015 | #D70015 | 흰 글자를 얹는 위험 동작 버튼 면(취소 확인 등). 다크도 같은 값 |

### 참여자

AI 동료 틴트는 **자리**(`p1`·`p2`·`p3`)에 붙고 페르소나 유형과 연결하지 않는다. Apple 시스템 색(오렌지·그린·퍼플)의 연한 틴트다.

| 토큰 | 라이트 | 다크 | 쓰임 |
|---|---|---|---|
| bubble-candidate / on-bubble-candidate | #0071E3 / #FFFFFF | #0060DF / #FFFFFF | 지원자 말풍선(오른쪽). accent-fill과 같은 값 |
| bubble-p1 | #FFF1DE | #3A2A14 | p1(강한결) 말풍선 |
| bubble-p2 | #E3F6E8 | #17331F | p2(윤하람) 말풍선 |
| bubble-p3 | #F3E6FA | #33213D | p3(서지안) 말풍선 |
| on-bubble | #1D1D1F | #F5F5F7 | AI 동료 말풍선 글자(ink와 같은 값) |
| system-notice | #6E6E73 | #A1A1A6 | 가운데 작은 회색 안내 글자(바탕 없음, muted와 같은 값) |
| seat-p1 / seat-p2 / seat-p3 | #A85A00 / #1E7A35 / #8E3FBF | #FF9F0A / #30D158 / #BF5AF2 | 프로필 이미지 대체 이니셜 바탕·이름 옆 점·리포트 근거 표식 |
| seat-candidate | #0071E3 | #0A84FF | 지원자 이니셜 아바타 |
| on-seat | #FFFFFF | #000000 | 이니셜 아바타 글자(라이트 흰 글자, 다크 검은 글자) |

자리 이름은 MVP 고정 배정(p1 강한결·p2 윤하람·p3 서지안)의 예시다.

### 대비 기준

| 대상 | 기준 | 확인 |
|---|---|---|
| 말풍선 본문(모든 참여자, 라이트·다크) | 4.5:1 이상 | 지원자 흰 글자/#0071E3 ≈ 4.7, 다크 흰 글자/#0060DF ≈ 5.6, 틴트 위 on-bubble은 12 이상 |
| accent 글자 | 4.5:1 이상 | #0066CC/#F5F5F7 ≈ 5.1, #0066CC/#FFFFFF ≈ 5.6, #0066CC/#EEF5FE ≈ 5.1, 다크 #2997FF/#000 ≈ 7.0, #2997FF/#1C1C1E ≈ 5.6 |
| on-accent 면 | 4.5:1 이상 | 흰 글자/#0071E3 ≈ 4.7, /#0066CC(hover) ≈ 5.6, 다크 /#0060DF ≈ 5.6, /#0050C0 ≈ 7.2, 흰 글자/danger-fill #D70015 ≈ 5.4 |
| 상태 글자 | 4.5:1 이상 | success #1E7A35/#FFFFFF ≈ 5.4, /#F5F5F7 ≈ 5.0 |
| 시스템 안내 system-notice/canvas | 4.5:1 이상 | #6E6E73/#F5F5F7 ≈ 4.7, #A1A1A6/#000 ≈ 8 |
| 아이콘·구분선·포커스 링 | 3:1 이상(구분선은 장식이면 예외) | |
| 이니셜 아바타 on-seat | 4.5:1 이상 | 라이트 흰 글자/seat-p1·p2·p3·candidate ≈ 5.1·5.4·5.8·4.7, 다크 검은 글자/seat-p1·p2·p3·candidate ≈ 10.2·10.4·6.0·5.8 |

- 색만으로 구분하지 않는다: AI 동료 말풍선 묶음에는 이름이 항상 보이고, 지원자는 오른쪽 정렬로 구분된다. 칸 결과·수준·확신도·상태도 아이콘 + 글자로 쓴다.
- 대비 검사는 화면 검증 항목에 들어간다([services/web.md](../services/web.md)). 값을 바꾸면 이 표를 다시 잰다.

## 타이포·간격·모서리

- 글꼴: `-apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Pretendard Variable", Pretendard, system-ui, sans-serif`. 한글은 Pretendard가 맡는다. 숫자 타이머는 `font-variant-numeric: tabular-nums`.
- 크기·굵기: caption 12 / footnote 13 / body 15(본문·말풍선) / callout 16 / title3 20 semibold / title2 22 semibold / title1 28 bold / large 34 bold(입장·디브리핑 머리). 줄 간격 본문 1.5, 제목 1.2. 위계는 크기와 굵기로 만들고 색은 ink·muted 두 단계만.
- 간격 4px 단위. 넓게 쓴다: 패널 안 24, 섹션 사이 32~48, 말풍선 사이 2(같은 사람)·12(다른 사람).
- 모서리 토큰: `radius-sm` 6(칩·배지) / `radius-md` 10(버튼·입력) / `radius-lg` 14(패널·팝오버) / `radius-xl` 20(대화 상자) / `radius-bubble` 18(말풍선) / `radius-bubble-tail` 6(묶음에서 다음 말풍선과 붙는 쪽 모서리). 아바타 원형.
- 그림자는 떠 있는 것(팝오버·대화 상자·토스트)에만 `0 4px 16px rgba(0,0,0,.08)`, 다크는 그림자 대신 line 테두리.
- 반투명(vibrancy): 상단 바와 떠 있는 패널만 `backdrop-filter: saturate(180%) blur(20px)` + surface 80% 불투명. 그 외 반투명·글래스 효과 없음. `prefers-reduced-transparency`면 불투명.

## 컴포넌트

공용 요소는 `apps/web/components/ui/`(Button, Card, Badge, StatusBadge, StatusPill, Chip, Avatar·AvatarGroup, Tabs, Input·Select·Textarea(`field.tsx`), ConfirmDialog, EmptyState, DropMenu(`menu.tsx`), Toaster, Wordmark, LevelChip, ConfidenceTag, SplitLayout)에 두고 토큰만 쓴다. 화면 컴포넌트는 지원자 쪽 `components/debate/`(음성 회의는 `components/debate/voice/`), 관리자 쪽 `components/admin/`이다.

**공용(`components/ui/`)**

| 컴포넌트 | 쓰임 | 비고 |
|---|---|---|
| Avatar·AvatarGroup | AI 동료: `avatar_url` 프로필 이미지. 지원자: 표시 이름 첫 글자 이니셜(seat-candidate 바탕, on-seat 글자). 그룹은 대기 화면 참여자 목록 | **원형 크롭·고정 크기·`object-fit: cover`**. 특정 비율·화풍·배경에 의존하는 스타일 금지. 이미지 없음·로드 실패면 같은 크기 이니셜. 이미지는 교체해도 코드 변경 없음. 그룹은 겹침 -6px, 이름 툴팁 |
| Tabs | 오른쪽 패널 탭(자료·AI 어시스턴트 등) | 분절 컨트롤 모양, 배지는 작은 accent 점 + 숫자 |
| LevelChip | 요소·차원 수준 "수준 N (1~5)" / 목록용 "설득 3", 기록 없음은 사유 문장 | 숫자 tabular, 수준별 색 구분 없이 중립 톤(높고 낮음을 색으로 서열화하지 않음). 담당자 조정 값은 "담당자 조정됨" 표시 동반 |
| ConfidenceTag | 차원 확신도 높음/보통/낮음 | 아이콘 + 글자. 낮음은 warning 톤 글자 "근거 약함, 원문 확인 권장" |
| SplitLayout | 토론 화면의 칸 나누기(왼쪽 메모·가운데 회의·오른쪽 결정 사항과 자료) | 칸 사이 끌어서 폭 조절, 칸 숨기기 |
| Toaster | 저장·충돌·제출 안내 | Sonner, 하단 가운데, 그림자 최소 |

**지원자 화면(`components/debate/`)**

| 컴포넌트 | 쓰임 | 비고 |
|---|---|---|
| ProfileStage·ProfileCard(`profile-stage.tsx`) | 음성 회의의 발표자 보기: 현재 화자를 큰 칸, 나머지 세 명을 아래 줄. 다른 화면은 2x2 | 말하는 사람 강조는 세 동료가 같은 모양(성향을 드러내지 않음). 재생 중 문장 자막과 방금 한 말 한 문장을 큰 칸에. 지원자는 녹음 중 음량만큼 빛남(LevelGlow) |
| 음성 조작부(`voice/voice-room.tsx`: VoiceAlert·VoiceNotices·Countdown) | 상태 한 줄, [발언하기]·[발언 완료]·손들기·다시 말하기, 내 차례 유지 시간 | 유지 시간은 큰 숫자 + 줄어드는 막대. 진행을 막는 오류는 경고 아이콘·제목·할 일 문장·버튼 하나, 막지 않는 안내는 회색 한 줄 6초. 음량은 LevelBars(`voice-ui.tsx`) |
| MemoPad(`voice/memo-pad.tsx`) | 왼쪽 칸 메모장 | 이 브라우저에만 저장, 평가에 쓰지 않음 |
| PhaseBar·Timer(`debate-view.tsx`) | 상단 4단계 진행, "이 단계 12:40 남음" | 현재 단계 accent 글자 semibold, 지난 단계는 결과 아이콘(✓ 합의 / 시계 시간 초과) + 글자. Timer는 tabular-nums, 2분 전 warning 색 전환 1회. 오피스 시각을 같은 줄에 붙이지 않는다 |
| PhaseSummary(`profile-stage.tsx`) | 왼쪽 아래 단계 안내 | |
| DecisionPanel·SlotInput(`decision-panel.tsx`) | 화면 이름 "결정 사항": 결과 줄 목록과 현재 정할 항목 입력·[이걸로 정하기] | 결과 줄은 구분선 목록(카드 아님), 시각 없음. keyword는 분절 버튼 4개(고른 버튼은 accent-soft 면·accent 글자·체크 아이콘) + 주 버튼(accent-fill + on-accent). 현재 정할 항목은 "지금 정할 항목" accent 칩과 왼쪽 2px accent 막대. 비활성은 surface-sunken 면·muted 글자, 처리 중은 면 유지 + 회전 아이콘 |
| MaterialsPanel·Markdown(`side-panels.tsx`·`markdown.tsx`) | 자료 목록·잠금 상태, 읽기 전용 Markdown | 본문 폭 최대 68자, 제목 위계는 타이포 표 |
| AssistantPanel·AssistantMarkdown | 요청·응답·인용 칩·복사 | 말풍선 아님. 반짝이·로봇 아이콘 금지 |
| ProposalEditor(`proposal-editor.tsx`) | 기획안 6칸·저장 시각·[제출] | 칸 제목 callout semibold, 칸 설명 한 줄(footnote muted), textarea는 placeholder "여기에 적어요"와 1px subtle 테두리(포커스 시 accent). 제출 확인·제출 중에는 readOnly로만 막고 모양 유지, 제출 뒤에는 테두리 없는 sunken 바탕·ink 글자(빈 칸은 "비어 있음")와 "제출했어요" |
| EntryScreen·ConsentScreen·WaitingScreen·SessionGuide(`link-screens.tsx`), DebriefScreen | 입장·동의·대기·안내, 디브리핑 | 좁은 화면은 DesktopNotice |
| ChatRoom·Composer·TypingIndicator(`chat.tsx`) | **텍스트 시험 모드(`VOICE_ENABLED=0`) 전용** 말풍선·입력창·"OO님이 입력 중" | 지원자 오른쪽 bubble-candidate, AI 동료 왼쪽 bubble-p1~p3. 묶음 첫 메시지 위 이름, 끝 메시지 옆 Avatar 28, 최대 폭 70%, radius-bubble. 보내는 중(ack 전) 60% 불투명. SystemNotice는 가운데 footnote muted. 음성 회의 화면에는 쓰지 않는다 |

**관리자 화면(`components/admin/`)**

| 컴포넌트 | 쓰임 | 비고 |
|---|---|---|
| PositionForm·PositionList | 채용 건 생성(세션 길이 Segmented 60·15분)·목록 | 요소 선택 UI 없음 |
| CandidateTable·InviteForm·LevelChips | 지원자 목록·초대·수준 칩 | StatusPill(목록 상태 표시) |
| ReportView(`report-view.tsx`: Section·DimensionRow·EvidenceItem·MessageEvidenceItem·CardScene·FactsTable·AdjustPanel) | 리포트 요약·칸 결과·차원 판단·근거·카드 장면("설계된 장면" 칩)·사실 지표·조정 | 차원은 접힘 기본, 구분선 목록. 자동 값과 담당자 값·메모를 나란히. 메시지 근거는 이름·아바타와 함께, 누르면 원문·앞 5·뒤 3 맥락 펼침(근거 메시지 accent-soft). 맥락 참조는 "맥락" 칩과 보조색 |
| RecordView·ReportFailed·AuthForm·AdminShell | 전체 기록, 리포트 실패 안내, 가입·로그인, 관리자 틀 | |

`globals.css`의 `part1~5`·`part*-ink`·`person-*` 토큰은 `components/ui/chip.tsx`·`avatar.tsx`가 참조한다. StatusPill은 관리자 목록 상태 표시로만 쓴다.

## 아이콘

- 선 굵기가 고른 단색 아이콘 한 벌(lucide-react, 새로 설치). 크기 16/20, 색은 ink·muted·accent.
- 쓰지 않는 아이콘은 아래 "하지 않는 것"을 따른다.

## 라이브러리 (`pick-ui-library` 기준)

| 용도 | 선택 |
|---|---|
| 접근성 기본 요소(메뉴·팝오버·대화 상자·선택) | base-ui (이미 설치) |
| 토스트 | Sonner |
| 전환 애니메이션(단계 전환·탭·말풍선 도착) | motion. 단순 hover·fade는 CSS |
| 변형 스타일 | cva + clsx (clsx 설치됨) |
| 읽기 전용 Markdown(자료) | react-markdown + remark-gfm(표·목록). `skipHtml`로 원문 HTML은 그리지 않음 |
| 아이콘 | lucide-react |

## 모션 (`animate`·`review-animations` 기준)

- 원칙: 짧고 부드럽게, 위치보다 불투명도. 튀는 스프링·과장된 확대 없음.
- 말풍선 도착: 160ms ease-out, opacity 0→1 + translateY 4px→0. 스크롤은 사용자가 맨 아래에 있을 때만.
- 입력 중 점: 1.2s 주기 opacity 순환.
- 단계 전환: 진행바 현재 표시 이동 200ms, 결정 사항 결과 줄 accent-soft 배경 600ms 페이드아웃 1회.
- 음성 회의 프로필 칸 자리 이동(발표자 바뀜·2x2 ↔ 발표자 보기): 280ms ease-out, transform(translate+scale)만(FLIP, 칸 DOM 유지). 칸 안 내용은 균일 비율로만 늘고 줄고, 커지는 칸이 위에 온다. 이어서 또 바뀌면 지금 보이는 자리에서 새 자리로 잇는다.
- 탭 전환: 분절 선택 표시 이동 180ms, 내용은 즉시(페이드 120ms).
- 대화 상자: 180ms opacity + scale 0.98→1. 닫기 120ms.
- 버튼 `:active` scale 0.98. 전환은 `all` 대신 속성 지정.
- 타이머 경고: 색 전환 1회, 깜빡임 없음.
- `prefers-reduced-motion`이면 이동 없이 opacity만. 그 밖의 모션 없음.

## 하지 않는 것

보라·파랑 그라데이션, 반짝이(✨)·로봇·AI 상징·그라데이션 아이콘, 이모지 장식, 글래스모피즘 남용(상단 바·떠 있는 패널 밖의 반투명), 네온·형광색, 과한 그림자, 카드만 반복되는 대시보드, 실존 인물 사진 아바타, 이모지 리액션, 스켈레톤 과다(목록 로딩만), 색만으로 구분하는 표시.
