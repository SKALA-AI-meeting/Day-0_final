# 데이터베이스 초기화와 스키마 변경

테이블과 컬럼의 뜻은 [architecture/data-model.md](../architecture/data-model.md), DDL은 `infra/db/schema.sql`이다. 지금은 공유·운영 DB가 없고 DB는 사람마다 로컬 개발용이므로 마이그레이션 도구를 두지 않는다.

## 새 DB

- compose의 postgres는 볼륨이 비어 있을 때만 `infra/db/schema.sql`을 `/docker-entrypoint-initdb.d`로 한 번 적용한다. 볼륨 이름은 compose 프로젝트 이름을 앞에 붙인다: `make up`(dev-fake·dev-real·deploy, `SCOPE=infra` 포함)은 `infra_pgdata`, 격리 스택(test-text·eval-text)은 `dayzero-debate_pgdata`.
- api 단위 테스트는 실행마다 임시 DB(`dayzero_test_<랜덤>`)를 만들어 `schema.sql`을 적용하고 끝나면 지운다(`services/api/tests/conftest.py`). 그래서 `schema.sql` 수정은 다른 사람의 개발 DB를 건드리지 않는다.
- 자료 원문(`materials`)과 검색 청크(`material_chunks`)는 스키마가 아니라 시드가 채운다(`make seed PROFILE=… [SCOPE=infra]`).

## schema.sql을 바꾼 뒤

볼륨이 이미 있으면 `schema.sql`을 고쳐도 다시 적용되지 않는다. 로컬 DB는 개발용이므로 볼륨을 지우고 다시 만든다.

```
make down [SCOPE=infra]         # 격리 스택이면 make down PROFILE=test-text
docker volume rm infra_pgdata   # 격리 스택이면 dayzero-debate_pgdata
make up [SCOPE=infra]           # 빈 볼륨에 schema.sql이 적용된다
make seed [SCOPE=infra]         # 자료 원문·채용 건·재색인 다시 만들기
```

## 데이터를 남겨야 할 때

지우기 전에 `pg_dump`로 백업한다. 이 백업은 **바꾸기 전 스키마 그대로** 되돌리는 용도다. 새 스키마로 데이터를 옮기는 것은 마이그레이션이 필요하므로 지금은 지원하지 않는다.

```
# 백업(스택이 떠 있는 상태)
docker compose -f infra/docker-compose.yml --env-file infra/.env exec -T postgres pg_dump -U dayzero -Fc dayzero > backup.dump

# 새 볼륨에 복원: 볼륨을 지우고 postgres만 띄운 뒤 백업으로 덮어쓴다
docker compose -f infra/docker-compose.yml --env-file infra/.env down
docker volume rm infra_pgdata
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d --wait postgres
docker compose -f infra/docker-compose.yml --env-file infra/.env exec -T postgres pg_restore -U dayzero -d dayzero --clean --if-exists --no-owner < backup.dump
```

복원한 DB는 백업 당시 스키마이므로 그 스키마를 기대하는 코드(백업 시점의 커밋)로 띄운다.

## 나중에

공유 DB나 운영 DB가 생기면 볼륨을 지울 수 없으므로 마이그레이션 규칙을 도입한다. 그때 정할 것: 변경 SQL 위치와 이름 규칙(예: `infra/db/migrations/NNNN_<설명>.sql`), 적용 이력 테이블, 적용 도구, `schema.sql`과의 관계, 백필·되돌리기 절차. 도입하면 이 문서와 [contributing.md](contributing.md) "DB 변경"을 함께 고친다.
