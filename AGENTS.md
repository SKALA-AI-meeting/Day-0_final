# Day Zero 작업 규칙

이 저장소에서 일하는 코딩 에이전트와 사람이 함께 따르는 규칙이다. Day Zero는 지원자가 AI 동료 3명과 턴 기반 음성으로 토론해 키워드 → 문제 상황 → 해결책을 합의하고 기획안을 제출하면, 그 과정의 행동으로 인성(토론 태도)을 보고, 기업의 인사담당자가 그 행동 근거로 면접·채용 판단을 하도록 돕는 채용 서비스의 MVP다. 평가 에이전트는 세션이 끝난 뒤 행동 근거로 수준을 판단한 참고 자료를 만들고, 최종 평가는 담당자가 한다. 사람용 소개는 [README.md](README.md).

## 읽는 순서

1. [PRD.md](PRD.md): 무엇을 왜 만드는지
2. [docs/README.md](docs/README.md): 목적별 문서 색인과 정본 관계
3. 작업할 서비스의 README: `services/<이름>/README.md`, [apps/web/README.md](apps/web/README.md), [scripts/README.md](scripts/README.md)
4. 관련 계약: [docs/contracts/](docs/contracts/README.md)(REST·WS·내부 API·MCP 도구·큐)

## 정본

| 무엇 | 정본 |
|---|---|
| 기획(목적·기능·원칙) | [PRD.md](PRD.md). `PRD.html`은 `sh build/build.sh`로 만들고 직접 고치지 않는다 |
| 서비스 간 계약 | [docs/contracts/](docs/contracts/README.md) |
| 결정과 이유 | [docs/decisions.md](docs/decisions.md)(날짜·결정·이유·대안) |
| 기능·구현 상태·검증 테스트 | [docs/features.md](docs/features.md) |

설계 문서가 PRD와 어긋나면 PRD가 맞고, 어느 쪽을 고칠지는 PR에서 정한다. 기획 방향이 바뀌는 결정은 decisions.md에 먼저 적는다.

## 고정 제약

- LLM 기본은 `gpt-4o-mini`. 역할 5개(페르소나 `MODEL_PERSONA`·턴 매니저 `MODEL_TURN`·의도 분류 `MODEL_INTENT`·어시스턴트 `MODEL_ASSISTANT`·평가 `MODEL_EVALUATOR`)는 env로 따로 정한다. 키는 모델 id로 고르는 규칙 하나뿐이고 자동 대체 모델은 없다. 역할별 타임아웃·재시도·실패 시 동작은 [docs/architecture/harness.md](docs/architecture/harness.md) "역할별 모델" 표가 정본이다. 프롬프트·규칙 수정으로 안 되는 역할만 모델을 올리고, 그 결정은 decisions.md에 남긴다.
- 평가표 필수 요소: State·Graph 기반 에이전트(턴 매니저 + 페르소나 에이전트 3), MCP 3개(debate-mcp·rag-mcp·assessment-mcp), Harness 8요소(Instructions, Tools, Context, State·Memory, Agent Loop, Guardrails, Recovery, Observability), 인증, 대기열과 재시도.
- 같은 채용 건의 지원자는 같은 시나리오(키워드 후보, 페르소나 3명, 동료별 단계 고정 입장, 카드 시각·규칙)와 같은 프롬프트(평가 프롬프트·금지 패턴 포함)를 받는다. 채용 건 생성 때 이것을 복사해 고정하고 세션 실행과 평가는 그 복사본을 읽는다. MVP는 5요소·카드 6장·페르소나 3명(고집형 강한결·소극형 윤하람·비판형 서지안) 고정이고 기업의 요소 선택은 없다(decisions.md R2).
- 시나리오는 데이터(`scenarios/missions/<id>/`)이고 코드는 해석만 한다. 누가 어떤 안을 밀지·합의 판정·시간 초과·칸 채우기 허용은 결정적 코드가, 말투만 LLM이 정한다.
- 페르소나 에이전트는 assessment-mcp에 접근하지 못한다(허용 목록과 토큰 모두에서 차단). 평가 기준을 보고 행동을 맞추지 못하게 하기 위해서다.
- 평가는 지원자의 토론 태도(설득/논리력·경청/존중·배려/포용·수용/유연성·정직/공정)만 본다. 실력(안의 질·근거의 정확성·기획안 품질)은 넣지 않는다.
- 평가 원칙: AI 평가 에이전트(report-worker, 세션 종료 후)가 공개된 행동 기준 척도(요소당 하위 차원 3~4개, 차원별 1~5 수준 앵커)로 지원자 행동 근거를 들어 수준을 판단하고, 담당자가 그것을 참고해 차원 단위로 조정하며 최종 평가한다. 지원자 행동 근거(메시지·칸 확정·기획안 필드) 없는 수준은 금지(기록 없음), 같은 척도·프롬프트·낮은 temperature, 확신도 low는 리포트에 표시한다([docs/product/rubric.md](docs/product/rubric.md)).
- 지원자의 호칭은 "지원자"(대화 속에서는 "OO님")다. AI 동료 3명과 동등한 참여자이며 진행자·팀장은 없다.
- MVP 관점: 평가 항목이 요구하는 조각(MCP 3개, 큐·워커, 프론트)만 분리하고 나머지는 합친다. MVP 이후로 둔 것은 [docs/features.md](docs/features.md).

## 작업 규칙

- 작업은 `main`에서 갈라진 브랜치에서 하고 PR로 합친다. 한 PR은 한 목적이다. 상세는 [docs/guides/contributing.md](docs/guides/contributing.md).
- 계약(경로·필드·오류 코드·이벤트·MCP 도구·큐 본문)을 바꾸면 [docs/contracts/](docs/contracts/README.md)를 같은 PR에서 고치고, 그 계약을 쓰는 서비스의 리뷰어를 부른다.
- DB 스키마 변경은 [docs/guides/database.md](docs/guides/database.md), 시나리오·척도·프롬프트 변경은 [docs/guides/scenario-changes.md](docs/guides/scenario-changes.md)를 따른다.
- 테스트는 happy path 중심이다. LLM은 테스트에서 mock(`FakeLLM`)이고, 실제 모델을 부르는 테스트는 `live` 마커로 분리한다.
- 비밀값은 `.env` 파일에만 두고 커밋하지 않는다. 새 env는 `infra/.env.example`에 이름을 더한다.
- 커밋 메시지는 한국어 한 문장이다.
- 문서는 현재 동작을 쓴다(이력은 git과 decisions.md). 정해지지 않은 사항은 추측하지 말고 묻는다.

## 검증 명령

작업을 끝내기 전에 반드시 돌린다. 범위와 선행 조건은 [docs/guides/testing.md](docs/guides/testing.md).

| 언제 | 명령 |
|---|---|
| 항상 | `make test`, `make lint`, `make docs-check`(README 생성 블록·features.md 테스트 참조 검사) |
| API·라우트·MCP 도구·테스트를 바꿨을 때 | `make docs-gen` 뒤 `make docs-check` |
| 화면 흐름을 바꿨을 때 | `make e2e-stages`(격리 스택) |

## 금지

- 리포트·화면·프롬프트·문서에 합격·불합격·순위·점수(총점) 표현을 만들지 않는다.
- "주담당"·"리드"를 쓰지 않는다.
- AI 동료·어시스턴트·시스템 안내가 점수·합격 가능성·연출될 상황을 말하거나, 지원자 대신 결론을 내리거나, 평가·판정 발언을 하지 않는다.
- 페르소나 에이전트에 assessment-mcp 클라이언트·도구를 주지 않는다.
- 평가에 실력(안의 질·근거의 정확성·기획안 품질)을 반영하지 않는다.
- 비밀값·`.env*`·테스트 계정 비밀번호를 커밋하거나 문서에 적지 않는다.
- `docs/share/**`는 공유용 산출물이다. 정본으로 인용하거나 근거로 쓰지 않는다.
