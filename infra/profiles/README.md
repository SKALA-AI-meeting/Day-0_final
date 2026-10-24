# 실행 프로필

비밀 아닌 선택값만 둔다(비밀값은 `infra/.env`). `scripts/profile.py`가 기본값 → `infra/.env` → 이 파일 순으로 겹쳐 유효 모드를 만들고, `make up/seed/eval/measure/e2e/stage PROFILE=<이름>`이 그 값을 compose와 호스트 러너에 같이 준다. 설명은 [docs/guides/configuration.md](../../docs/guides/configuration.md) "실행 프로필".

| 프로필 | LLM | 음성 | 테스트 API | compose | 외부 사용 |
|---|---|---|---|---|---|
| `dev-fake`(기본) | fake | FakeSpeech | 1 | `infra`, web 3000·api 8000 | 없음 |
| `test-text` | fake | 끔 | 1 | `dayzero-debate`, web 3100·api 8100 | 없음 |
| `test-voice` | fake | FakeSpeech | 1 | `dayzero-debate`, web 3100·api 8100 | 없음 |
| `eval-text` | 실제(제한 키) | 끔 | 1 | `dayzero-debate`, web 3100·api 8100 | LLM(`ALLOW_API=1`) |
| `eval-voice` | 실제(제한 키) | FakeSpeech | 1 | `dayzero-debate`, web 3100·api 8100 | LLM(`ALLOW_API=1`) |
| `dev-real` | 실제(제한 키) | Azure | 0 | `infra`, web 3000·api 8000 | LLM·Azure |
| `deploy` | 실제(제한 키) | Azure만 허용 | 0 | `infra`, web 3000·api 8000 | LLM·Azure |

`VOICE_ENABLED=0`(`false`도 허용)은 테스트 API가 켜진 프로필에서만 허용한다. `VOICE_PROVIDER=fake`에서 만든 채용 건에는 fake 정책이 복사된다. 예전 Azure 정책 채용 건은 fake 음성 프로필에서 시작할 수 없으므로 새 채용 건을 시드한다. 어느 프로필도 개인 키를 쓰지 않는다.

FakeSpeech 프로필(`dev-fake`·`test-text`·`test-voice`·`eval-text`·`eval-voice`)은 테스트 원장을 위해 `VOICE_QUOTA_PERIOD=isolated-fake`를 설정한다. Azure 프로필(`dev-real`·`deploy`)은 이 값을 쓰지 않으며, 운영자가 Azure 자원의 실제 할당량 초기화 기간을 확인해 `infra/.env`의 `VOICE_QUOTA_PERIOD`에 설정해야 한다. 웹 빌드는 compose가 프로필의 `VOICE_ENABLED`를 `NEXT_PUBLIC_VOICE_ENABLED` 빌드 인자로 전달하므로 모드를 바꾼 뒤 다시 빌드한다.
