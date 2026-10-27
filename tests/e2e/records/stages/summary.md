# 단계별 화면 테스트 결과 (stage-e2e, LLM_FAKE=1)

## 최신 재실행 (2026-10-26 11:47 KST)

`test-text` 화면 테스트 10건 중 8건 통과, 1건 실패, 음성 프로필 전용 1건 건너뜀. 실패한 `solution.spec.ts`의 "AI 합의 채우기: 공지 메시지 → 결정 사항 칸 확정 → 기획안 단계"는 합의 공지 메시지를 20초 기다린 뒤 시간 초과됐다. 현재 해결책 AI 합의 채우기의 최신 검증 결과다. 영상·trace·HTML 리포트는 `html-report/` 등 Git에서 제외한 로컬 산출물에 남는다.

## 이전 기록 (2026-10-22)

실행: `make e2e-stages` (스택 `make e2e-stack-up`, 자료 시드 `make e2e-stack-seed`). 7개 spec 9개 테스트 모두 통과(약 45초). 아래 표와 기준 캡처는 이 실행의 기록이다.

| spec | 테스트 | 스크린샷 |
|---|---|---|
| entry | 입장 → 동의(표시 이름 "하늘") → 대기(참여자 3명 이름·아바타) → 시작, 내부 조회로 display_name·in_progress 확인 | `entry/01~04` |
| keyword | 후보 4개 버튼, 확정 전 자료 닫힘 → 물 확정 → 결정 사항 줄(합의·정한 사람 나) → 문제 상황 단계 → 자료 4건 | `keyword/01~04` |
| problem | `advance card:flexibility_majority` 뒤 기준안 발언·관찰 2건(수용·경청 카드), `advance deadline` 뒤 시간 초과 공지·결과 줄("시간 초과")·해결책 입력으로 전환 | `problem/01~03` |
| solution | 소극형 지명 + 서지안 설득 2회 → AI 합의 공지·칸 확정 · `advance idle` 뒤 비판형 초대 1건(`silent`) · 반대가 남은 혼자 확정(`solo_confirm`, filled_by candidate) | `solution/01~04` |
| proposal | 앞 세 칸 자동 채움, 디바운스 저장(내부 조회로 확인) → 제출 확인 창 → 입력 잠김(`제출했어요`, 메시지 입력 비활성) → 나가기 → 디브리핑 | `proposal/01~05` |
| debrief | R1 첫 문장, 내 발언 1~3개, 설문 제출 후 새로고침해도 제출됨 | `debrief/01~03` |
| admin-report | 관리자 로그인 → ended 시드 리포트 → 경청 차원 수준 5 조정(메모) → 검토 완료 → 요소 수준 3→4 재계산 | `admin-report/01~04` |
