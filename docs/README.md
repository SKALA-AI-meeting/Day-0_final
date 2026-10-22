# Day Zero 설계 문서

Day Zero는 지원자가 AI 동료 3명과 턴 기반 음성으로 토론해 키워드 → 문제 상황 → 해결책을 합의하고 기획안을 제출하면, 그 과정의 행동으로 인성을 보는 채용 서비스다. 기획 정본은 루트 [`PRD.md`](../PRD.md)이고 이 폴더는 그것을 구현 수준으로 구체화한다. 이 파일이 문서 구조의 색인이다.

## 목적별 색인

| 하고 싶은 일 | 먼저 읽을 문서 |
|---|---|
| 제품을 한 페이지로 훑기(화면·규칙·동작·확장 출발점) | [share/product-guide/index.html](share/product-guide/index.html) |
| 처음 받아서 실행해 보기 | [guides/quickstart.md](guides/quickstart.md) → [guides/configuration.md](guides/configuration.md) |
| 테스트를 돌리고 범위를 알기 | [guides/testing.md](guides/testing.md) |
| 기여 규칙(브랜치·PR·필수 검사·계약·DB 변경) | [guides/contributing.md](guides/contributing.md) |
| 제품 규칙(단계·시간·페르소나·카드·합의) | [product/scenario.md](product/scenario.md) |
| 평가 척도(BARS)·기록 없음 사유·사실 지표 | [product/rubric.md](product/rubric.md) |
| 화면 흐름과 화면 규칙 | [product/user-flows.md](product/user-flows.md) |
| 전체 구조와 원칙 | [architecture/overview.md](architecture/overview.md) |
| 세션 상태·실행 순서·메일박스·복구 | [architecture/session-lifecycle.md](architecture/session-lifecycle.md) |
| 에이전트(토론 그래프·의도 분류·어시스턴트·평가 에이전트) | [architecture/agents.md](architecture/agents.md), [architecture/harness.md](architecture/harness.md) |
| 음성 토론 현재 구현·설정·복구 | [architecture/voice.md](architecture/voice.md) |
| 턴 기반 음성 토론 구조 | [architecture/voice-turns.md](architecture/voice-turns.md) |
| 음성 모델 후보·무료 티어·비용 정책 | [architecture/voice-services.md](architecture/voice-services.md) |
| 권한 경계·비밀값 | [architecture/security.md](architecture/security.md) |
| 테이블과 컬럼 | [architecture/data-model.md](architecture/data-model.md), 스키마 변경 [guides/database.md](guides/database.md) |
| 디자인 토큰·컴포넌트 | [architecture/design-system.md](architecture/design-system.md) |
| compose 구성 | [architecture/deployment.md](architecture/deployment.md) |
| API·WS·내부 API·MCP·큐 계약 | [contracts/README.md](contracts/README.md) |
| 서비스별 구현 | [services/api.md](services/api.md), [services/web.md](services/web.md), [services/report-worker.md](services/report-worker.md), [services/mcp.md](services/mcp.md) |
| 시나리오·척도·프롬프트 바꾸기 | [guides/scenario-changes.md](guides/scenario-changes.md) |
| 결정과 그 이유 | [decisions.md](decisions.md) |

서비스 폴더의 `README.md`(`services/*/README.md`, `apps/web/README.md`)는 그 서비스에서 바로 실행·검증하는 명령의 입구이고, `docs/services/`는 모듈을 잇는 상세 설계다.

## 정본 관계

| 무엇 | 정본 | 따라 쓰는 곳 |
|---|---|---|
| 기획(목적·기능·원칙) | `PRD.md`(`PRD.html`은 `sh build/build.sh`로 생성) | 이 폴더 전체 |
| 결정 이유와 대안 | [decisions.md](decisions.md) | 각 문서는 결정 ID(D·R)로 가리킨다 |
| 단계·시간·카드·페르소나·합의 규칙 | [product/scenario.md](product/scenario.md) | 계약은 필드만, 서비스 문서는 구현 위치만 |
| 차원 id·앵커·기록 없음 사유·사실 지표·평가 판단 규칙 | [product/rubric.md](product/rubric.md) | 저장 형식은 contracts, 호출·복구는 services/report-worker |
| 경로·필드·오류 코드·이벤트·도구 | [contracts/](contracts/README.md) | 예시 JSON은 `tests/fixtures/contracts/`(사본 `apps/web/mocks/fixtures/`) |
| 상태 전이·실행 순서·메일박스 | [architecture/session-lifecycle.md](architecture/session-lifecycle.md) | data-model은 저장 컬럼만 |
| 테이블과 컬럼 | [architecture/data-model.md](architecture/data-model.md) | DDL `infra/db/schema.sql` |
| 하네스 8요소·역할별 모델 설정 | [architecture/harness.md](architecture/harness.md) | 코드 `roles.py` |
| env 이름과 기본값 | `infra/.env.example`·각 서비스 `config.py` | [guides/configuration.md](guides/configuration.md)는 모드별 차이 |
| 카탈로그 데이터 | `scenarios/missions/debate/` | 문서 표와 같은지 `make test`가 검사 |

이름이 충돌하면 contracts(도구·이벤트·상태·경로) > data-model(테이블) > scenario(페르소나·카드) > rubric(평가) 순으로 따른다.

## 구조

```
docs/
├── README.md        이 색인
├── decisions.md     결정 기록(날짜·결정·이유·대안)
├── product/         scenario · rubric · user-flows
├── architecture/    overview · session-lifecycle · security · harness · agents · data-model · design-system · deployment
├── contracts/       README(공통·인증) · api · ws · internal · mcp · queue
├── services/        api · web · report-worker · mcp
├── guides/          quickstart · configuration · testing · database · scenario-changes · contributing
└── share/           공유용 산출물(아키텍처 페이지·보고서·시각 보고서·제품 가이드). 정본이 아니다
```
