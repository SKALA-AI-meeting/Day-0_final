# 실행 프로필

비밀 아닌 선택값만 둔다(비밀값은 `infra/.env`). `scripts/profile.py`가 기본값 → `infra/.env` → 이 파일 순으로 겹쳐 유효 모드를 만들고, `make up/seed/eval/measure/e2e/stage PROFILE=<이름>`이 그 값을 compose와 호스트 러너에 같이 준다. 설명은 [docs/guides/configuration.md](../../docs/guides/configuration.md) "실행 프로필".

| 프로필 | LLM | 음성 | 테스트 API | compose | 외부 사용 |
|---|---|---|---|---|---|
| `dev-fake`(기본) | fake | FakeSpeech | 1 | `infra`, web 3000·api 8000 | 없음 |
| `test-text` | fake | 끔 | 1 | `dayzero-debate`, web 3100·api 8100 | 없음 |
| `test-voice` | fake | FakeSpeech | 1 | `dayzero-debate`, web 3100·api 8100 | 없음 |
| `eval-text` | 실제(제한 키) | 끔 | 1 | `dayzero-debate`, web 3100·api 8100 | LLM(`ALLOW_API=1`) |
| `eval-voice` | 실제(제한 키) | FakeSpeech | 1 | `dayzero-debate`, web 3100·api 8100 | LLM(`ALLOW_API=1`) |
| `dev-real` | 실제(제한 키) | 받아 적기 로컬 Whisper·읽기 Gemini | 0 | `infra`, web 3000·api 8000 | LLM·Gemini TTS |
| `deploy` | 실제(제한 키) | Azure만 허용 | 0 | `infra`, web 3000·api 8000 | LLM·Azure |

`VOICE_ENABLED=0`(`false`도 허용)은 테스트 API가 켜진 프로필에서만 허용한다. `VOICE_PROVIDER=fake`에서 만든 채용 건에는 fake 정책이 복사된다. 예전 Azure 정책 채용 건은 fake 음성 프로필에서 시작할 수 없으므로 새 채용 건을 시드한다. 어느 프로필도 개인 키를 쓰지 않는다.

FakeSpeech 프로필(`dev-fake`·`test-text`·`test-voice`·`eval-text`·`eval-voice`)은 테스트 원장을 위해 `VOICE_QUOTA_PERIOD=isolated-fake`를 설정한다. Azure 프로필(`dev-real`·`deploy`)은 이 값을 프로필에 두지 않고 `infra/.env`의 `VOICE_QUOTA_PERIOD`(`infra/.env.example` 기본 `auto`: UTC 달마다 새 원장)를 쓴다. 값이 비면 음성이 차단된다. 웹 빌드는 compose가 프로필의 `VOICE_ENABLED`를 `NEXT_PUBLIC_VOICE_ENABLED` 빌드 인자로 전달하므로 모드를 바꾼 뒤 다시 빌드한다.

`dev-real`은 음성을 Gemini(`VOICE_PROVIDER=gemini`, STT `gemini-3.5-flash-lite`·TTS `gemini-2.5-flash-preview-tts`, `MODEL_STT`·`MODEL_TTS`로 바꿈, 키 `GEMINI_API_KEY`)로 돌리고 `VOICE_QUOTA_PERIOD=auto`를 프로필에 둔다. OpenAI 음성(`VOICE_PROVIDER=openai`)은 제한 키(`OPENAI_API_KEY`) 프로젝트에 음성 모델 권한이 있어야 한다(없으면 403). `deploy`는 그대로 Azure다.

`dev-real`은 지금 받아 적기를 로컬 Whisper(`VOICE_STT_PROVIDER=whisper`, 지원자 소리가 서버 밖으로 나가지 않음)로, 읽기를 Gemini TTS(`VOICE_TTS_PROVIDER=gemini`, AI 동료 문장만 보냄)로 나눠 쓴다. Whisper 모델은 첫 사용 때 `hfcache` 볼륨에 내려받는다.
