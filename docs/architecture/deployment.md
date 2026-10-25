# 배포 (Docker Compose)

서비스 8개의 compose 구성과 RabbitMQ 설정이다. 실행 모드별 포트·DB·큐·env는 [guides/configuration.md](../guides/configuration.md), 처음 띄우는 순서는 [guides/quickstart.md](../guides/quickstart.md).

## 서비스와 의존

서비스: postgres, rabbitmq, api(8000 공개, 8001 내부), debate-mcp(9101), rag-mcp(9102), assessment-mcp(9103), report-worker, web(3000 공개).

- healthcheck: api·MCP 3개는 `/health`, web은 `/api/health`, report-worker는 30초마다 갱신하는 `/tmp/heartbeat`(90초 이상 오래되면 unhealthy).
- 의존(`service_healthy`): api ← postgres·rabbitmq·debate-mcp·rag-mcp·assessment-mcp. report-worker ← postgres·rabbitmq·api·assessment-mcp. rag-mcp ← postgres. web ← api.
- 공개 포트는 web 3000·api 8000·rabbitmq 관리(로컬 바인딩)뿐. MCP 3개는 `expose`만.
- `../scenarios`를 api·rag-mcp에 읽기 전용 마운트(카탈로그·자료·아바타 에셋).
- `docker-compose.dev.yml`(로컬 개발 override): postgres 5432·rabbitmq 5672·rag-mcp 9102를 127.0.0.1에 열고 rag-mcp가 호스트의 로컬 api 내부 포트(`host.docker.internal:8001`)를 보게 한다. 나머지 앱은 로컬에서 띄운다.
- `docker-compose.e2e.yml`(격리 e2e override): 프로젝트 이름 `dayzero-debate`로 별도 스택을 띄운다. env는 프로필(`infra/profiles/<이름>.env`)을 `scripts/profile.py`가 `infra/.env`와 합쳐 만든 `infra/.env.resolved-<프로필>`이고([guides/configuration.md](../guides/configuration.md)), 호스트 포트는 web 3100·api 8100·내부 127.0.0.1:8101·postgres 5442·rag-mcp 9112·rabbitmq UI 15674. web은 `NEXT_PUBLIC_API_URL=http://localhost:8100`으로 빌드한다. 모델 캐시는 외부 볼륨 `infra_hfcache`를 재사용한다.
- postgres는 첫 기동 때만 `infra/db/schema.sql`을 `/docker-entrypoint-initdb.d`로 적용한다. 볼륨이 이미 있으면 다시 적용하지 않는다(스키마를 바꾼 뒤 절차는 [guides/database.md](../guides/database.md)).
- 운영에 가까운 실행에서는 `ENABLE_TEST_API`·`LLM_FAKE`·`LLM_FAULT`를 비운다.

## RabbitMQ

- exchange `dayzero`(direct). 큐 `report.generate`, `report.approve`(durable, DLX → `report.dlq`), `report.dlq`. 본문은 계약 "큐 메시지" 표.
- persistent, publisher confirm 뒤 api가 `*_enqueued_at` 기록, manual ack, 같은 session_id 중복은 stage로 멱등. 미발행은 api 스케줄러가 60초마다 재발행.
- 관리 UI는 `127.0.0.1:15672`에만 바인딩.
