# 음성 토론 구현 검증 (2026-10-23)

- API: 206 passed, 3 live deselected. 음성 장벽·최종 메시지 1회·마감·원장·Azure S0 차단 포함.
- Web: 23 files / 67 passed. TypeScript·ESLint·Prettier 및 프로덕션 빌드 통과.
- 루트 계약·카탈로그·문서: 128 passed. docs-gen 및 docs-check --features 통과.
- MCP: debate 15 passed, assessment 20 passed, rag 7 passed / 3 환경 의존 skipped / 1 live deselected.
- report-worker: 53 passed / 2 live deselected. 실제 평가 규칙은 유지하고 FakeEvaluator의 기여 기록 차원 근거만 idea_credit로 보완.
- E2E 러너·평가 mock: 34 passed / 4 live deselected.
- Playwright 단계별 10 passed. 안내 화면 수정 후 entry·voice 2 passed 재확인.
- 15분 설정 all_five 대본: real 시계·FakeSpeech·FakeLLM, 단계·턴·카드·기획안·draft·담당자 검토·final·비용/지연 단언 전부 통과. 조기 합의·제출로 실제 벽시계 실행은 약 5분에 종료된다.
- 전체 대본 API 러너는 WAV 길이만큼 기다린 합성 재생 확인을 사용한다. 브라우저 실제 MediaRecorder·WAV 업로드·ended·60초 자동 제출은 Playwright에서 별도 통과.
- Windows에 make가 없어 Makefile의 각 명령을 직접 실행했다. 실제 Azure 한국어 합성·인식은 인증 및 F0 확인 완료 전 호출하지 않았다.

로컬 상세 실행 기록은 git 제외된 output/voice-e2e와 tests/e2e/records/stages/.artifacts에 남긴다. 비밀값·음성 원본은 이 문서에 포함하지 않는다.
