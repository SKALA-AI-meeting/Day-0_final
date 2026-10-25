# ASSETS — Day Zero 발표 덱

## 컨셉
캐릭터: 성별·나이를 특정하지 않는 단순한 사람 실루엣형 인물. 실존 인물·사진 느낌 금지. AI 동료는 같은 스타일의 인물 3명으로, 성향을 과장하지 않는 중립 표정.
톤: 플랫 에디토리얼 일러스트, 얇은 선 + 면 채색, 배경은 따뜻한 미색(#FAF9F5) 단색, 잉크(#141413)·회색(#6B6A64)·연회색(#DEDCD3) 위주에 클레이(#D97757) 포인트 한 곳.
금지: 이미지 안 글자·숫자·로고, 사실적 명암, 복잡한 배경, 보라·파랑 그라데이션, 로봇·반짝이 아이콘, 네온.

## 자산
| 파일 | 쓰는 장 | 무엇을 보여주는가 | 프롬프트 요지 | 출처 |
|---|---|---|---|---|
| assets/cover-hook.png | 1 표지 | 지원자 한 명이 노트북 앞에서 화면 속 AI 동료 3명과 토론하는 장면 | 노트북 화면에 인물 아바타 3개와 말풍선(글자 없음), 앞에 앉은 지원자 뒷모습, 클레이 포인트는 지원자 말풍선 | 생성 이미지 |
| assets/problem-mask.png | 2 문제 | 인성검사 답안지 앞에서 웃는 가면을 얼굴에 대고 있는 지원자 (꾸민 응답의 은유) | 책상 위 체크형 답안지(글자 없음), 손에 든 웃는 가면, 클레이 포인트는 가면 | 생성 이미지 |
| assets/clip-voice-turn.mp4 · clip-voice-turn-poster.jpg | 3 해결책 | 음성 회의에서 동료 발언 → 지원자 차례 → 발언 중 → 손들기 (22초 반복) | walkthrough.mp4 15.5~37.5초를 무대 부분(680×680, x260 y60)만 잘라 냄. 정지 화면은 41초 | docs/share/screen-guide/walkthrough.mp4 |
| assets/flow-*.jpg | 5 서비스 흐름 | 단계별 실제 화면 일부 | ③은 walkthrough.mp4 41초 무대 부분(680×510), 나머지는 e2e·제품 가이드 캡처 | walkthrough.mp4, tests/e2e/records/stages, docs/share/product-guide/media |
| assets/scene-pressure.png | 6 지원자 경험 | 음성 회의에서 한 동료가 지원자 안을 세게 밀고, 지원자는 마이크 버튼 앞에서 들으며 답을 준비하는 장면 | 노트북 화면 속 큰 칸의 말하는 동료(글자 없는 말풍선)와 아래 작은 칸 셋, 이어폰 낀 지원자 어깨 너머 시점, 클레이 포인트는 마이크 버튼 | 생성 이미지 |
| assets/report-crop.jpg | 7 담당자 경험 | 실제 LLM 리포트의 세부 행동 하나(수준·확신도·판단 이유·설계된 장면) | 로컬 스택 관리자 리포트 화면 캡처 | 가상 지원자 실제 LLM 세션(2026-10-23) |
| assets/flow-4-report.jpg | 5 서비스 흐름 | 실제 LLM 리포트 요소 요약 | 같은 세션 캡처 | 같은 세션 |
| assets/diagram-*.svg | 8~11 설계 | 아키텍처·에이전트·MCP·하네스 | 덱 크기(1172×400, 글자 16px 이상)로 새로 그린 SVG. 다시 그릴 때는 `python3 tools/diagrams.py assets` 를 고쳐 재생성 | docs/architecture 기준 |
| assets/demo.mp4 · demo-poster.jpg | 12 시연 | 입장 → 음성 차례·손들기 → 확정 → 문제 상황 → 기획안 제출 → 마무리, 이어서 리포트 열기 → 조정 입력 → 반영 | walkthrough.mp4 구간 2~14·16~58·60~72·92~110·110~118초를 잇고, 리포트 캡처 3장(admin-report-01·03·04)을 4초씩 붙임. 정지 화면은 walkthrough 26초 | walkthrough.mp4, docs/share/product-guide/media |
| assets/closing-review.png | 13 마무리 | 담당자가 강조 표시된 리포트를 읽고, 뒤로 면접 자리가 준비된 장면 | 책상 위 한 장짜리 리포트(글자 대신 회색 막대), 뒤편 면접 탁자와 의자 둘, 클레이 포인트는 리포트 강조 표시 | 생성 이미지 |
