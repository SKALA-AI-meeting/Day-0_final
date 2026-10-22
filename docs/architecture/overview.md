# 아키텍처

음성 토론의 현재 구현은 [음성 구현](voice.md)을 따른다.

과제는 **지원자 + AI 동료 3명이 한 토론방에서 키워드 → 문제 상황 → 해결책을 합의하고, 지원자가 최종 기획안을 써서 제출**하는 60분 세션이다. 서비스 목적(행동 관찰로 인성을 보고, AI는 기록하고 사람이 판단)은 그대로다. 모든 세션이 5요소·카드 6장·페르소나 3명(고집형 강한결·소극형 윤하람·비판형 서지안) 고정이고 세션 길이는 60분 또는 데모 15분이다(결정 이유는 [decisions.md](../decisions.md) "2026-10-21 토론형 전환").

MVP 원칙: **평가 항목이 요구하는 조각만 분리하고 나머지는 합친다.** 분리하는 것은 MCP 서버 3개, 큐와 워커, 프론트뿐이다. 토론 그래프·어시스턴트·의도 분류는 api 프로세스 안의 모듈이다.

평가표 차원(BARS)·카드·페르소나·요소의 구체 목록과 규칙은 [product/scenario.md](../product/scenario.md)·[product/rubric.md](../product/rubric.md)가 정본이다. 이 문서와 [contracts/README.md](../contracts/README.md)·[architecture/data-model.md](data-model.md)는 그 값을 **문자열 id로 받는 구조**만 정한다.

## 구성 요소

| 구성 요소 | 책임 | 기술 |
|---|---|---|
| web | 기업 관리자 화면(채용 건·초대·리포트·조정), 지원자 토론 화면(가운데 토론방, 우측 탭 합의·자료·AI 어시스턴트·기획안, 상단 단계 진행바·남은 시간) | Next.js App Router, Tailwind. `/admin/*`, `/s/[token]` |
| api | 인증, 화면 REST, WebSocket(세션별 단일 송신 큐·`seq`), DB 쓰기, 세션 시계(스케줄러: 단계 마감 시 **시간 초과 직접 채우기**·침묵 초대·카드 예정 시각·세션 만료), 세션 메일박스, 카탈로그 아바타 정적 서빙(`/assets/missions/<mission_id>/avatars/`), 의도 분류, 토론 그래프, 어시스턴트, 금지 문구 검사 | FastAPI + LangGraph(토론 그래프), 한 프로세스 |
| 토론 그래프 (api 모듈) | **턴 매니저**(결정적 노드 + LLM 화자 선택) + **페르소나 에이전트 3**(자리 `p1`·`p2`·`p3`, 각자 입장·기억·도구 허용 목록) | LangGraph `StateGraph`, 체크포인터 없음(상태는 매 실행 DB에서 다시 읽음) |
| 어시스턴트 (api 모듈) | 지원자 전용 검색·요약. 도구 호출 루프(`search_materials`·`get_material`, 요청당 최대 3회, 인용 포함)로 모델이 도구를 고른다. 열린 자료만 | Python 도구 호출 루프(LangGraph 아님). 대화 이력은 세션별 프로세스 메모리 |
| debate-mcp (손) | 토론 상태 조회·변경: 상태·대화 조회, 메시지 게시, 안 제안, 입장 기록, 합의 칸 채우기, 기획안 초안 조회 | FastMCP, Streamable HTTP `:9101/mcp` |
| rag-mcp (눈) | 확정된 키워드의 참고 자료만 검색·조회(채용 건 `catalog_version`의 청크·원문). 키워드 확정 전에는 차단 | FastMCP, Streamable HTTP `:9102/mcp`, BGE-M3, pgvector |
| assessment-mcp (기록자) | 카드 전달 기록, 평가표(BARS 척도) 조회, 평가 입력 묶음(세션 기록 전체) 조회, 근거 앞뒤 맥락 조회, 아이디어 최초 제안자 조회, 평가 결과 저장 | FastMCP, Streamable HTTP `:9103/mcp` |
| report-worker | 세션 종료 후 큐 작업: 세션 기록 수집 → 사실 지표 → 평가 에이전트(BARS 차원별 수준·근거) → 리포트 초안 → 담당자 승인 대기 → 확정. 재시도, DLQ | Python, RabbitMQ consumer, LangGraph thread `report:{session_id}:{eval_round}` |
| infra | 컨테이너, 큐, DB, 관측 | Docker Compose, RabbitMQ, PostgreSQL 16 + pgvector, LangSmith(env) |

MCP 서버 세 개의 도구 함수는 모두 `async def`다(FastMCP는 동기 도구를 워커 스레드에서 실행한다).

## 원칙

1. **DB 쓰기는 api만 한다.** MCP 3개와 워커는 api의 `/internal/*`(별도 포트 8001, 미공개)을 부른다. 예외는 report-worker의 LangGraph 체크포인트 테이블(`report:` thread만), rag-mcp의 `material_chunks`, `scripts/seed.py`의 `materials`(자료 원문 버전별 시드).
2. **시간의 주인은 api다.** 세션 시작 시각과 단계 마감 시각을 DB에 두고, 5초마다 도는 스케줄러가 단계 마감(직접 시간 초과 채우기)·침묵 초대·카드 예정 시각(자극 enqueue)·세션 만료(자동 제출)를 처리한다. 접속이 끊겨도 시계는 흐른다. 시간 로직은 시계를 주입받아 가짜 시계로 단위 테스트한다.
3. **에이전트는 자극에 반응하는 함수다.** 자극은 `message`(지원자 메시지), `phase_started`(단계 시작), `idle`(지원자 침묵만), `card_due`(시각 기반 카드 예정 시각, card_id 포함) 네 가지(정본 [architecture/session-lifecycle.md](session-lifecycle.md) "그래프 자극"). 단계 마감은 그래프가 아니라 api가 결정적으로 직접 채운다. 자극마다 토론 그래프를 한 번 실행한다. 그래프는 체크포인트를 남기지 않고 실행 기록은 `agent_runs`, 토론 상태는 DB가 원천이다. 세션 하나 = **메일박스 하나**(asyncio Task 1개가 자극 큐를 직렬 소비, 락 없음). 지원자 메시지 저장과 단계 전환이 `sessions.generation`을 올리고, 그래프는 게시 직전 generation이 바뀌었으면 남은 화자를 버린다. api는 단일 프로세스로 운영한다.
4. **시나리오는 데이터다.** 채용 건 생성 때 고정 페르소나 3자리·키워드 후보·페르소나별 단계 고정 안·카드 6장·평가표·프롬프트(평가 프롬프트·금지 패턴 포함)·자료 목록을 조립해 `positions.scenario`에 복사·고정한다(같은 채용 건 = 같은 시나리오, 요소 선택 없음). 코드는 해석만 한다. 조립 규칙은 [product/scenario.md](../product/scenario.md).
5. **결정은 결정적 코드가, 말투는 LLM이.** 누가 어떤 안을 처음 밀지, 합의 여부, 시간 초과 시 고를 안, 칸 채우기 허용, 문제 상황 기준안 잠금, 지원자 확정 값의 안 연결은 규칙으로 정한다. LLM은 의도 분류, 화자 선택 보조(규칙 결과 0명인 `message` 자극만), 발언 문장만 만든다.
6. **평가 정보는 페르소나에게 닿지 않는다.** 페르소나 respond 노드에는 assessment-mcp 클라이언트를 만들지 않고 허용 목록에도 넣지 않는다. assessment-mcp는 턴 매니저의 결정적 노드와 report-worker만 쓴다.

| 더 읽을 곳 | 문서 |
|---|---|
| 세션 상태·실행 순서·메일박스·재시작 복구 | [session-lifecycle.md](session-lifecycle.md) |
| 권한 경계·assessment 차단·기업 격리 | [security.md](security.md) |
| 하네스 8요소와 역할별 모델 설정 | [harness.md](harness.md) |
| 토론 그래프·의도 분류·어시스턴트·평가 에이전트 | [agents.md](agents.md) |
| 테이블·쓰기 주체·보관 | [data-model.md](data-model.md) |
| compose 구성 | [deployment.md](deployment.md) |

## 구성 요소별 금지 사항

| 구성 요소 | 하지 않는 것 |
|---|---|
| web | 권한·상태 판단, MCP 직접 호출 |
| agent 모듈(api 안) | DB 직접 쓰기(변경은 debate-mcp → 내부 API) |
| debate-mcp | 토론 규칙(합의·마감·잠금) 복제. 판단은 api가 한다 |
| rag-mcp | 자료 원문 변경(`material_chunks` 색인만) |
| assessment-mcp | 페르소나 노드의 접근(토큰·허용 목록으로 차단) |
| report-worker | 근거 고쳐 쓰기, 총점·순위 생성 |

## 설계 선택과 대응

| 흔한 구조 | Day Zero | 대응 |
|---|---|---|
| 변경 도구 앞에서 interrupt로 멈추고 승인 후 같은 thread 재개 | 리포트 그래프가 draft 뒤 interrupt로 멈추고, 담당자의 "검토 완료"가 큐로 같은 thread를 재개해 final을 저장 | 승인 대상은 "초안을 최종본으로 확정하는 저장"이다. 승인 전 final 저장은 api가 409로 막고, final은 승인한 draft와 해시가 같다 |
| 질문을 202로 접수하고 SSE로 진행 이벤트 | WebSocket 양방향(동료가 먼저 말을 건다) | 접수 = `message.ack{run_id}`, 진행 = `typing{participant, start∣stop}`, 완료 = `message.new`. 재접속은 `/state` 스냅샷으로 복구 |
| 화면·업무 서버·Agent 서버 분리 | Agent가 api 프로세스 안의 모듈 | 논리 계층은 나눈다(라우터 → SessionService → AgentRunner 인터페이스 → 그래프 → MCP 어댑터). 배포 단위만 합쳤고, 분리할 때의 경계는 `AgentRunner`다 |
| Vue + Spring Boot | Next.js + FastAPI | FastAPI는 LangGraph와 같은 Python 프로세스에서 비동기 WS를 처리하고, Next.js는 관리자·지원자 화면을 한 앱의 경로로 둔다. 업무 규칙의 소유자는 FastAPI뿐이다 |

## 운영 목표 (시연 환경)

| 항목 | 목표 | 확인 |
|---|---|---|
| 동시 세션 | 5 | P2 동시 실행 확인 |
| 의도 분류 지연 | p95 3초 (타임아웃 10초) | message_intents.latency_ms |
| 페르소나 응답 지연 | 지원자 메시지 → 첫 페르소나 게시 p95 8초 (페르소나 LLM 타임아웃 20초) | agent_runs, messages.meta |
| 단계 마감 처리 | 마감 → `phase.changed` 15초 안 (스케줄러 5초 + api 직접 채우기) | 가짜 시계 단위 테스트, e2e summary |
| api 재시작 복구 | 90초 안에 WS 재연결, 미처리 마감·제출 재처리 | restart 대본 |
| 세션당 비용 | 목표 $0.5(관측), 하드 상한 $2(토론 비용 기준, 페르소나 고정 문구로 전환) | `sessions.cost_usd` = agent_runs.usage + reports.usage의 `cost_usd`(관측). 상한은 `sessions.debate_cost_usd`(리포트 제외) |

## 저장소 구조

```
apps/web/                 Next.js
services/api/             FastAPI + LangGraph (app/routers, app/agent/{debate,assistant,intent,guard,llm,mcp_client}, app/mailbox.py, app/scheduler.py, app/services/{timeout,budget}.py)
services/debate-mcp/
services/rag-mcp/
services/assessment-mcp/
services/report-worker/
scenarios/                시나리오 카탈로그(구조는 docs/product/scenario.md), version.py, e2e/ 대본·단계 시드
scripts/                  seed.py, make_session.py, stage_advance.py, save_eval_fixture.py
tests/                    계약 픽스처, e2e/(러너·기록), eval/(실 모델 픽스처), playtest/
infra/                    docker-compose.yml, docker-compose.dev.yml, docker-compose.e2e.yml, .env.example, db/schema.sql
docs/                     설계 문서(docs/README.md)
```

## ISO/IEC 25010 대응

| 품질 특성 | 설계 반영 |
|---|---|
| 기능 적합성 | PRD 기능 ID별 완료 조건을 e2e 단언으로 검증 |
| 신뢰성 | DB 기록 기반 재처리(`agent_runs` 재큐, 리포트는 체크포인트 재개), 멱등 저장, 세션 메일박스와 generation, api 직접 시간 초과 채우기, 큐 persistent·manual ack·재발행·DLQ, 의도 분류 실패 시 other, WS seq 재동기화 |
| 성능 효율성 | 위 운영 목표. 자극당 화자 2명, 응답당 도구 3회, 검색 결과 3청크 |
| 보안성 | 역할 분리 JWT, 모든 관리자 조회에 company_id, 초대 토큰 해시·72시간, 내부 API 미공개 포트 + 시크릿, assessment-mcp 별도 토큰, 비밀값 env |
| 유지보수성 | 시나리오 데이터화, 서비스별 컨테이너, 계약 문서가 정본 |
| 이식성 | Docker Compose 한 번에 기동. 외부 의존은 OpenAI·LangSmith뿐 |
| 호환성 | MCP 표준 전송, REST·WebSocket 계약 문서화 |
| 사용성 | 단계 진행바·남은 시간 상시 표시, 입력 중 표시, 새로고침 없는 도착, 이어하기, 끊김 구간 표시 |
