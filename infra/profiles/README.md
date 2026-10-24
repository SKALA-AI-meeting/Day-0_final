# 실행 프로필

비밀 아닌 선택값만 둔다(비밀값은 `infra/.env`). `scripts/profile.py`가 기본값 → `infra/.env` → 이 파일 순으로 겹쳐 유효 모드를 만들고, `make up/seed/eval/measure/e2e/stage PROFILE=<이름>`이 그 값을 compose와 호스트 러너에 같이 준다. 설명은 [docs/guides/configuration.md](../../docs/guides/configuration.md) "실행 프로필".

| 프로필 | 목적 | LLM | 테스트 API | compose | 외부 사용 |
|---|---|---|---|---|---|
| `dev-fake`(기본) | dev | fake | 1 | `infra`, web 3000·api 8000 | 없음 |
| `test-text` | test | fake | 1 | `dayzero-debate`, web 3100·api 8100 | 없음 |
| `eval-text` | test·측정 | 실제(제한 키) | 1 | `dayzero-debate`, web 3100·api 8100 | LLM(`ALLOW_API=1`) |
| `dev-real` | dev | 실제(제한 키) | 0 | `infra`, web 3000·api 8000 | LLM(`ALLOW_API=1`) |
| `deploy` | deploy | 실제(제한 키) | 0 | `infra`, web 3000·api 8000 | LLM(`ALLOW_API=1`) |

음성(voice) 열은 음성 기능이 합쳐질 때 더한다. 어느 프로필도 개인 키를 쓰지 않는다.
