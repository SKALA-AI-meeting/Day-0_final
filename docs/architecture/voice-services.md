# 음성 서비스 후보 선정과 비용 통제 설계

현재 동작·설계 대비 구현 범위는 [음성 구현](voice.md)과 [계약](../contracts/voice.md)이 기준이다. 본문의 제안·비교는 선정 근거이며 미구현 제안을 현재 기능으로 해석하지 않는다.

2026-10-22 공식 문서 확인 · **1차 설계 / 미구현 / API 미연결**

상위 설계는 [음성 턴 설계](voice-turns.md), 결정 근거는 [V6·V7](../decisions.md#2026-10-22-턴-기반-음성-토론-전환-설계)다. 이 문서는 공급자·요금제·언어 지원의 공개 근거와 설계 권장안을 구분한다. 계정·리소스를 만들거나 음성 API를 호출하거나 모델을 다운로드하지 않았다. 가격은 확인 시점 기준 USD이며 세금·환율·계약 할인·별도 컴퓨팅 비용은 포함하지 않는다.

## 1. 권장 조합과 선택 이유

**초기 시연 권장: Azure AI Speech F0의 일반 Neural TTS + 표준 ko-KR STT. 일상 개발·자동 테스트는 FakeSpeech.** Azure는 월 TTS 50만 문자·STT 5시간 무료량을 명시한다. 다만 F0의 STT 동시 요청 1개 제약 때문에 이 조합은 우선 **동시 음성 세션 1개**로 운영하는 설계다. 기존 전체 서비스의 동시 세션 목표와 별개다. [Azure 가격](https://azure.microsoft.com/en-us/pricing/details/speech/), [동시성·속도 제한](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-services-quotas-and-limits)

한국어 3개 voice를 고정할 수 있고 클라우드 음성 모델을 직접 운영할 필요가 없다는 점을 우선했다. **한국어 품질이나 지연에서 Azure가 더 우수하다는 실측 결론은 아니다.** 가격표·기능 적합성으로 선택한 1차 권장안이며, 실제 시연 채택은 후속 품질·지연 검증을 통과해야 한다.

Google Cloud TTS는 더 큰 무료 문자량을 제공하는 유효한 교체 후보지만, 무료량을 넘으면 자동 과금되는 구조다. 로컬 TTS/STT는 API 사용료가 없지만 장비·모델 실행·콜드 스타트와 유지 비용이 있다. 무료 크레딧·한시적 체험 혜택을 반복 가능한 월 무료량으로 계산하지 않는다. [Google 가격·과금 조건](https://cloud.google.com/text-to-speech/pricing)

## 2. TTS 후보 비교

| 후보 | 월 무료량 / 초과 구조 | 한국어·3개 목소리 | 품질·지연 판단 | 1차 선택 |
|---|---|---|---|---|
| Azure Speech **F0 / 일반 Neural** | 500,000 문자. F0만 허용하고 S0 전환 금지. 유료 S0 단가는 지역·계약별 확인 필요; 이번 문서에서는 수치를 추정하지 않음 | ko-KR 일반 Neural voice 여러 개 중 3개 고정 가능 | 한국어 지원 확인. 실제 자연스러움·첫 파일 준비 시간 미측정. 네트워크·지역 용량 영향 | **시연 우선** |
| Google **Standard / WaveNet** | 가격표상 각각 4,000,000 문자, 초과 $4/100만 문자. 같은 SKU로 표시되므로 둘을 더해 800만 무료라고 계산하지 않음 | ko-KR A/B/C/D 등 서로 다른 ID 존재 | Standard와 신경망 WaveNet을 동일 품질로 취급하지 않음. 한국어 비교·지연 미측정 | WaveNet 교체 후보 |
| Google **Neural2** | 1,000,000 문자, 초과 $16/100만 문자 | ko-KR Neural2 A/B/C, 3개 고정 가능 | 명칭만으로 한국어 품질 우위 단정 불가. 미측정 | 비교 후보 |
| 로컬 **MeloTTS + OpenVoice V2** | API 요금·월 무료 티어 없음. 자체 장비 연산·전력·설치·저장 비용 | 한국어 합성 + 서로 다른 참조 음색 3개 구성 가능. 사용 권한이 있는 참조만 사용 | Melo 기본 합성 뒤 음색 변환 단계 추가. CPU/GPU·캐시·모델 워밍에 따라 지연. 이 프로젝트 장비에서는 미측정 | 오프라인 교체 후보 |

무료량·초과 단가는 [Azure 가격표](https://azure.microsoft.com/en-us/pricing/details/speech/)와 [Google 전용 가격표](https://cloud.google.com/text-to-speech/pricing)를 따른다. Google의 제품 소개에는 WaveNet 100만 문자라는 표기도 남아 있어, 이 설계는 현재 전용 가격표의 400만 문자를 기준으로 삼는다. 연결 전 실제 계정의 SKU·무료 적용 범위를 다시 확인하며 불일치하면 작은 한도를 적용한다. [제품 소개의 다른 표기](https://cloud.google.com/text-to-speech)

목소리 목록 근거는 [Azure 언어·voice 목록](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts), [Google 한국어 voice 목록](https://docs.cloud.google.com/text-to-speech/docs/list-voices-and-types)이다. 로컬 구성은 [MeloTTS](https://github.com/myshell-ai/MeloTTS), [OpenVoice V2](https://github.com/myshell-ai/OpenVoice)와 [공식 연결 예제](https://github.com/myshell-ai/OpenVoice/blob/main/demo_part3.ipynb)를 근거로 한다. 두 프로젝트는 MIT 라이선스를 명시하지만 실제 배포 시 체크포인트와 참조 음성의 조건도 함께 확인한다. MeloTTS 단독으로 한국어 목소리 3개가 확보된 것으로 간주하지 않는다.

최신 HD·커스텀 음성·음성 복제 클라우드 SKU는 초기 허용 목록에 넣지 않는다. Azure의 Neural 무료 항목을 모든 HD 음성에 적용하거나, Google의 무료 문자 수를 다른 음성 SKU에 그대로 적용하지 않는다. 피치만 바꾼 같은 음성 3개를 서로 다른 voice 요구의 충족으로 보지 않는다.

### 2.1 Azure 자리별 목소리 매핑 초안

| 자리 | 이름 | voice ID 후보 |
|---|---|---|
| p1 | 강한결 | `ko-KR-InJoonNeural` |
| p2 | 윤하람 | `ko-KR-SunHiNeural` |
| p3 | 서지안 | `ko-KR-BongJinNeural` |

서로 다른 ID를 쓰되 말투·입장·압박 강도는 기존 텍스트 프롬프트·결정적 규칙이 담당한다. 감정 스타일로 인물 성향을 과장하지 않는다. 세 voice가 선택 지역·F0에서 실제 사용 가능한지, 청취자가 서로 구분 가능한지는 후속 검사다. 불가능하면 새 채용 건의 매핑을 다시 정하고 기존 세션에서 몰래 교체하지 않는다.

Google 교체안은 `ko-KR-Wavenet-C/A/D`처럼 서로 다른 ID를 동일 자리 키에 연결한다. 로컬 교체안은 승인된 참조 음성 3개의 embedding·체크섬을 자리별 논리 voice 키에 고정한다. 지원자 음성을 동료 목소리 생성에 사용하지 않는다.

## 3. STT 후보와 60초 발언의 구현 경계

| 후보 | 무료량·초과 구조 | 턴 기반 적합성·제약 | 권장 |
|---|---|---|---|
| Azure Speech **F0 표준 ko-KR** | 월 5 audio hours. Standard/Custom 무료량 공유, F0 Batch 미지원 | 완성 파일을 빠른 받아 적기(fast transcription) REST 한 번으로 처리하고, 거절되면 SDK continuous recognition으로 끝까지 읽어 최종 구간들을 합침(개정 V11). F0 동시 인식 1개 | 시연 우선 |
| Google STT **V1 / Standard / without data logging** | 계정당 월 첫 60분 무료, 기본 소비 모델 초과 $0.024/분 | 파일 전체 전사에 적합하지만 동기 요청 약 1분 한도 경계 확인 필요. 녹음 컨테이너·인코딩까지 포함해 검증 | 비교 후보 |
| Google STT **V2** | 이 문서가 확인한 표준 기본 요금 구간은 $0.016/분부터. V1의 월 60분 무료를 적용하지 않음 | 비동기 할인 작업은 짧은 사용자 턴의 지연 요구와 따로 검토해야 함 | 초기 무료 전용 허용 목록 제외 |
| 로컬 **faster-whisper / multilingual small, CPU int8** | 외부 API 비용 없음. 자체 CPU/GPU·메모리·전력 비용 | 완성 파일 입력·한국어 지정·최종 전사에 적합한 후보. CPU/GPU에 따른 처리 시간·오인식 검증 필요 | 개발/오프라인 교체 후보 |

요금·버전·빈 응답 과금·채널별 계산은 [Google STT 가격표](https://cloud.google.com/speech-to-text/pricing), 동기 길이 제약은 [V1 인식 문서](https://docs.cloud.google.com/speech-to-text/docs/v1/speech-to-text-requests)를 따른다. 로컬 엔진은 [faster-whisper 공식 저장소](https://github.com/SYSTRAN/faster-whisper)에 CPU/GPU int8 지원이 명시되어 있다. `.en` 전용 모델을 한국어 기본 모델로 고르지 않고 다국어 Whisper 체크포인트를 사용한다. [Whisper 모델 구분](https://github.com/openai/whisper)

기본 경로는 빠른 받아 적기다. 업로드가 끝난 WAV를 `POST https://{region}.api.cognitive.microsoft.com/speechtotext/transcriptions:transcribe?api-version=2025-10-15`에 여러 부분 폼(`audio` + `definition` JSON `{"locales":["ko-KR"], "phraseList":{"phrases":[동료 이름…]}}`)으로 보내고 `combinedPhrases[].text`를 합친다. 10/23 F0 측정에서 소리 길이의 0.05~0.08배가 걸렸고(continuous는 약 0.4배) 동료 이름 8개를 모두 맞게 적었다. 사용한 날 요금은 0원이었다(리소스 주인이 포털 비용 관리에서 확인). Azure 요금표는 이 기능이 F0 무료량에 드는지 밝히지 않으므로, HTTP 오류로 거절되면 같은 파일을 아래 continuous 경로로 받아 적는다(둘 다 F0 안의 경로). [빠른 받아 적기](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/fast-transcription-create)

예비 경로 설명: Azure의 짧은 예제에 있는 `recognizeOnce`는 발언 중 무음이나 단일 구간에서 종료될 수 있어 60초 자유 발언 전체 처리에 쓰지 않는다. 서버가 업로드 완료된 파일을 continuous recognition으로 읽고 EOF까지 모은 결과만 한 전사로 확정한다. 부분 전사로 `message.send`를 여러 번 보내는 구조는 아니다. 브라우저는 녹음 중 소리 조각을 `/voice/stream`으로 보내고 서버가 쉬는 곳에서 끊어 빠른 받아 적기로 미리 받아 적지만(개정 V13), 제출되는 전사는 여전히 발언 하나에 하나다. 서비스 상품명에 real-time이 있어도 사용자 경험은 완성 파일 기반의 턴 방식이다. [단일 인식 예제의 제한](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/get-started-speech-to-text), [파일 continuous recognition](https://learn.microsoft.com/azure/ai-services/speech-service/how-to-recognize-speech?pivots=programming-language-csharp)

**기존 설계의 STT 호출당 30초 timeout은 Azure 파일 continuous 경로에 그대로 적용하지 않는다.** 초기 제안은 `timeout = 입력 오디오 길이 + 30초`(최대 60초 발언이면 90초), 재시도는 남은 단계 마감과 quota를 다시 검사해 최대 1회다. 네트워크 I/O가 비동기여도 인식이 오디오 길이에 비례하면 대기 시간이 길 수 있다. 지연 목표를 못 맞추면 로컬 faster-whisper 또는 다른 완성 파일 STT를 다음 정책 버전으로 선택한다. 유료(S0) 리소스로 자동 변경하지 않는다. 빠른 받아 적기는 F0 리소스에서 동작함을 10/23에 확인해 기본 경로로 쓴다(위, 개정 V11). F0 확인(`verify`)은 그대로라 유료 리소스에는 호출하지 않는다.

지원자는 한 명이므로 입력은 단일 채널로 정규화한다. AI TTS를 다시 STT에 넣지 않고, 4명 화자 분리·번역·발음 평가 기능도 사용하지 않는다. TTS 목소리 3개는 3배의 STT 채널이나 3배의 무료량을 뜻하지 않는다.

## 4. 비용 추정: 문자와 발언 길이로 계산

TTS는 **보낸 최종 본문의 과금 문자**, STT는 **보낸 오디오의 과금 길이**가 단위다. 60분 세션 전체를 STT 60분으로 계산하지 않는다. 반대로 무음·빈 전사여도 공급자가 처리했다면 무료라고 가정하지 않는다. 재시도는 새 공급자 호출이므로 별도 사용량이다.

```text
U_tts = 모든 TTS 시도의 공급자 규칙별 과금 문자 합계
U_stt = 모든 STT 시도의 ceil(오디오 초 / 과금 단위) × 과금 단위 × 채널 수 합계
N = min(floor(남은 TTS 문자 / 세션 예상 문자),
        floor(남은 STT 초 / 세션 예상 초))
```

한국어 UTF-8 byte 수를 그대로 문자 수로 간주하지 않는다. 공급자별 문자·SSML 계산기를 둔다. 임의 SSML은 받지 않고 고정 템플릿으로 escape한다. 계산 규칙이 불명확하면 보수적인 상계량을 예약하거나 호출을 차단한다.

다음은 **실측이 아닌 용량 가정**이다. Azure 무료량의 80%인 TTS 400,000 문자·STT 14,400초(240분)를 개발/시연용 월 운영 한도로 둔다. 남은 20%는 외부 사용·집계 지연·단위 차이 여유이며 더 쓸 수 있는 예산으로 표시하지 않는다.

| 가정 | 세션당 TTS | 세션당 STT | 재시도 10% 포함 월 예상 세션 수 |
|---|---|---|---|
| 60분 토론: AI 40발언×150문자 + 공지 1,000문자, 지원자 20발언×30초 | 7,700문자 | 660초 | min(51, 21) = **약 21회** |
| 15분 시연: AI 8발언×150문자 + 공지 300문자, 지원자 4발언×30초 | 1,650문자 | 132초 | min(242, 109) = **약 109회** |

세션 수는 발언 횟수·무음·재시도에 따라 달라지고, TTS/STT 두 경우를 같은 월 무료량에 중복 배정할 수 없다. 예시는 기존에 사용한 무료량이 0이라는 가정이다. 마감시간 안에 이 발언량을 모두 처리할 수 있는지도 별도 실측한다. 무료량이 남아도 요청 속도·동시성 제한으로 대기하거나 중단될 수 있다.

유료 비교가 필요할 때만 `max(0, U − 해당 무료량) × 해당 SKU 단가`를 사용한다. 실제 무료 적용은 계정/리소스/SKU·기간 범위에 따라 확인한다. 하나의 무료량을 프로젝트·목소리마다 복제해 계산하지 않는다. Azure S0의 무료 적용 여부나 현재 단가를 F0 표에서 추론하지 않는다. 로컬 비용은 API 비용 0과 별도로 실행시간·장비 사용량을 기록한다.

## 5. LLM·음성 분리 인터페이스

TTS와 STT는 서로 다른 포트와 어댑터를 갖는다. **독립 서비스 인터페이스**이며 처음부터 별도 네트워크 배포를 요구하지 않는다. API 내부 포트로 연결하고 로컬 추론은 필요할 때 제한된 작업 프로세스로 실행한다. 이벤트 루프·기존 메일박스를 무거운 추론으로 막지 않는다.

| 포트 | 입력 | 출력 |
|---|---|---|
| `TtsService.synthesize` | job_id, final_message_id/body_hash, 검증된 텍스트, locale, 논리 voice_key, policy_version, deadline | asset_ref, duration_ms, format, provider_request_id, usage, 비용 상태 |
| `SttService.transcribe` | job_id, utterance_id, 비공개 audio_ref/hash, 검증된 길이·채널·형식, locale, policy_version, deadline | 최종 transcript, transcript_id, confidence nullable, provider_request_id, usage, 비용 상태 |
| `SpeechQuota.reserve/settle` | 공급자 billing_scope·SKU·기간, 단위·수량·요청 상계비용, session_id·job_id·attempt | 예약 ID·허용/차단 사유, 사용량 정산 상태 |

예외는 `quota_exhausted`, `rate_limited`, `unavailable`, `invalid_audio`, `policy_mismatch`, `timeout_unknown`으로 정규화한다. 공급자 원문 오류는 민감정보를 제거하고 운영 로그에만 남긴다. 프론트는 클라우드 키·SDK를 받지 않고 기존 음성 턴 계약만 사용한다.

Controller → quota 예약 → 독립 Speech Service → 확정 자산/전사 순서이며, LangGraph·Persona LLM은 Azure/Google/Whisper 타입을 import하지 않는다. TTS에는 guard·stance_check와 게시를 거친 최종 본문만, STT에는 최종 완성 파일만 전달한다. MCP 3개·허용 목록·평가 척도·화자 선택은 유지한다.

`tts_provider`와 `stt_provider`는 독립 설정이다. 논리 voice 키(p1/p2/p3)는 공급자별 ID에 매핑하고 모델/engine revision·지역·음성 매핑·속도·정책 버전을 채용 건 생성 때 고정한다. Azure 일반 Neural은 별도 고정 weight 버전이 공개되지 않을 수 있으므로 voice ID·설정과 조회시각을 기록하고 외부 모델의 완전한 비트 재현성을 약속하지 않는다. 로컬은 체크포인트 해시도 고정한다.

공급자 교체는 어댑터·설정·새 정책 검증으로 끝나도록 한다. 정상화·속도·오디오 형식·비용 계산은 어댑터 내부 책임이다. 기존 채용 건에 다른 공급자를 자동 주입하지 않는다. 캐시 키는 `세션 범위 + body_hash + provider + model + voice + locale + 속도/형식 + policy_version`이며, **같은 화자·본문의 재생 실패는 캐시 파일을 다시 재생**한다. 자산이 있는데 TTS부터 다시 호출하지 않는다.

## 6. 의도하지 않은 과금 방지

### 6.1 무료 전용 기본 정책

초기 정책 제안은 `VOICE_BILLING_MODE=free_only`, `VOICE_ALLOW_PAID=false`다. Azure F0의 허용 기능·voice만 사용하고 키가 S0/유료 리소스로 바뀌었거나 SKU 확인이 만료/불가하면 실제 호출을 막는다. 앱 런타임 자격증명에는 요금제 변경 권한을 주지 않는다. 배포·리소스 변경 때와 주기적인 상태 확인에서 실제 SKU를 검증하는 운영 절차가 필요하다. 설정 문자열 `F0` 하나만 믿지 않는다.

무료 한도 소진은 중단 조건이고 자동 S0 업그레이드·유료 모델 전환·다른 공급자 키로 우회하지 않는다. 한도를 늘리는 변경은 운영자가 별도 정책 버전으로 명시적으로 수행한다. 같은 채용 건의 음성 조건을 중간에 바꾸지 않는다.

Google 후보는 결제 활성화·초과 자동 과금 구조이므로 요청당 사전 예약·계정 범위 제한과 실제 계정 상태 확인 없이는 활성화하지 않는다. 일반 예산 알림은 사용·비용을 자동 차단하지 않는다. 서비스에서 지원되는 공급자 측 차단 정책도 함께 검토하되, 이를 확인하지 않고 '예산을 0으로 두면 무료'라고 설명하지 않는다. [Google 과금 조건](https://cloud.google.com/text-to-speech/pricing), [일반 예산 알림의 한계](https://docs.cloud.google.com/billing/docs/how-to/budgets)

### 6.2 다층 한도 제안

| 범위 | 초기 한도·역할 |
|---|---|
| 공급자 월 공용 | Azure 전용 F0의 TTS 400,000 문자·STT 14,400초. 목소리·기업·API 프로세스별로 나누어 한도를 복제하지 않음 |
| 초기 시연 | 동시에 진행하는 음성 세션 1개. Azure STT 전역 semaphore 1개, TTS는 공급자 공용 sliding-window **최대 15회/60초**, burst 2 제안. 재시도 포함, 공식 F0 20회/60초보다 낮게 설정 |
| 60분 세션 | TTS 12,000 문자·STT 1,200초·TTS 공급자 호출 100회·STT 40회 상한 제안. 공지·재시도 포함 |
| 15분 시연 | TTS 3,000 문자·STT 300초·TTS 30회·STT 12회 상한 제안. 가짜 테스트는 별도 모드 |
| 작업 | 기존 최대 400자 AI 본문, 최종 사용자 발언 최대 60초, 업로드 길이/채널 검증. 공급자 호출 추가 재시도 최대 1회, 매 시도 quota 재검사 |
| 전역 차단 | 원장·가격/무료 규칙·SKU 검증을 읽을 수 없으면 fail closed. 초과·실제 집계 불일치가 있으면 해당 scope 신규 호출 차단 |

월 운영량·시연 한도는 프로젝트가 정한 보수적 제한이며 공급자 보장 무료량이 아니다. 외부 도구에서 같은 리소스를 쓰지 않는 전용 자격증명을 권장하고 수동 포털 테스트도 원장에 반영한다. 계정 범위 무료량을 공유하는 다른 리소스가 있으면 잔여량을 합산해 확인하거나 그만큼 제외한다. 청구 기간 경계·리셋 시각을 공급자 기준으로 저장하며 KST 월초라고 임의 리셋하지 않는다.

세션 시작 전 해당 길이의 최대 사용량을 공용 quota에서 예약한다. 남은 월 quota가 부족하면 **시작 전에** 안내한다. 실제 사용분만 소모로 바꾸고 종료 때 미사용 예약을 반환한다. 작업별 예약은 세션 예약의 하위 할당이므로 공용 quota에서 이중 차감하지 않는다. 이미 진행하는 세션의 예약량을 새 세션에 빌려주지 않는다. 시연 세션 수 추정치와 시작 허용 여부는 다른 값이다.

### 6.3 원자적 예약·불확실한 과금

1. 캐시 적중인지 먼저 확인한다. 적중이면 새 공급자 사용량 0으로 기록하고 이미 처리한 job 결과를 재사용한다.
2. `provider + resource/billing_account + SKU + billing_period` 범위의 DB 원장에서 `used + reserved + 새 요청 상계량 ≤ cap`을 원자적으로 확인한다. 돈 한도·호출 횟수·초/문자 한도를 모두 검사한다.
3. 작업 시도 ID로 예약 행과 lease를 커밋한 뒤 호출한다. 장시간 네트워크 동안 DB 락을 유지하지 않는다. 성공이면 실제 수량으로 정산하고 단위 불명확 시 상계량을 유지한다.
4. timeout·연결 유실은 공급자가 처리했을 수 있으므로 `unknown`으로 남기고 예약량을 사용 상계로 유지한다. lease 만료만으로 전액 반환하지 않는다. 재시도는 별도 시도 예약이 필요하다. 공급자 과금의 정확히 1회는 보장하지 않는다.
5. 확실히 전송하지 않은 실패만 반환한다. 429의 원인이 월 초과인지 순간 제한인지 분류하고, 불명확하면 중단한다. 무한 재시도·모델 변경으로 해결하지 않는다.
6. 재시작 때 원장을 DB에서 읽고 불확실 시도를 먼저 복구한다. 새 프로세스 메모리 초기화로 월 quota가 리셋되지 않는다. 실제 공급자 집계와 대조해 잔여량을 더 보수적으로 보정한다.

한도 경고는 운영 한도의 70%·90%에서 기록하고 차단은 100% 전에 다음 요청의 상계량까지 포함해 판단한다. 관리자 대시보드는 `사용·예약·불확실·남은 양·리셋 시각`을 표시한다. 외부 서비스 알림 발송은 별도 운영 범위다.

### 6.4 초과·차단 시 토론 동작

- TTS 한도 초과: 캐시 자산 재생은 허용한다. 필수 신규 자산을 만들 수 없으면 `blocked_reason=voice_quota_exhausted`. AI 본문만 게시됐다고 사용자 마이크를 열지 않는다.
- STT 한도 초과: 시작 예약으로 예방하며, 예외적으로 발생하면 파일·attempt를 종료 정책대로 보존하고 발언 제출 실패를 표시한다. 빈 메시지나 추정 전사를 보내지 않는다.
- quota 때문에 전달 못 한 카드는 미청취로 남기고, 장애를 지원자 태도로 해석하지 않는다. 기존 단계 마감·세션 자동 제출은 계속한다.
- 허가된 로컬 정책이 있더라도 진행 중 채용 건을 자동 로컬 전환하지 않는다. 로컬·캐시 시연은 새 정책/세션으로 시작한다. 참가자가 말할 수 없는 음성 기능을 새 세션에서 계속 열어두지 않는다.

## 7. 기존 LLM 제한과 전체 비용의 관계

현재 `services/api/app/services/budget.py`의 `debate_cost_usd`는 의도 분류·턴·페르소나·어시스턴트 LLM 비용이다. `SESSION_COST_HARD_USD` 기본 $2는 이를 기준으로 페르소나를 고정 문구로 전환하며 리포트 비용은 포함하지 않는다. `sessions.cost_usd`는 토론+리포트 관측 합계다. **현재 코드에는 음성 비용이 없으며 이 문서가 코드를 바꾸지는 않는다.**

후속 구현은 다음처럼 범위를 구분한다.

| 항목 | 관리 범위 | 소진 시 |
|---|---|---|
| 기존 `SESSION_COST_HARD_USD` | 토론 LLM. 기존 의미·폴백 유지 | 페르소나 고정 안전 문구. 그 문구의 TTS도 별도 음성 quota 필요 |
| 음성 quota·`VOICE_API_PAID_BUDGET_USD=0` 제안 | TTS/STT의 월·세션 사용량, 유료 API 예상 추가비용 | 요청 전 차단. 금액 0이어도 무료 단위 quota는 차감 |
| 세션 전체 비용 상한 `SESSION_TOTAL_COST_HARD_USD` 제안 | 토론 LLM + 리포트 LLM + TTS + STT의 예약 포함 총액 | 다음 선택적 LLM/음성 호출 전 검사. 무제한 전체 비용을 허용하는 단순 관측값으로 두지 않음 |
| 리포트 예산 | 세션 시작 시 기존 재시도 횟수·호출 상계량으로 확보 | 보고서 생성 예약을 진행 중 발언에 소모하지 않음. 실패·재생성에도 별도 예약 검사 |

전체 상한의 구체 금액은 LLM 입력 상한·리포트 재시도 비용을 검증한 뒤 정한다. 기존 $2를 전체 상한으로 조용히 재해석하지 않는다. 유효한 전체 상한·리포트 예약을 설정하지 못한 구성은 음성 시연 시작 준비 실패로 표시한다. 이는 기존 LLM 폴백을 보완할 **후속 설계 제안**이며 현재 구현을 엄격한 전체 비용 보장으로 설명하지 않는다.

```text
llm_cost = debate_llm_cost + report_llm_cost
voice_cost = tts_net_cost + stt_net_cost
sessions.cost_usd = llm_cost + voice_cost
호출 허용 = 기존 LLM 역할 정책 충족
          AND 해당 음성 단위/호출/돈 한도 충족(음성 호출일 때)
          AND 총 사용 비용 + 예약 비용 + 다음 상계비용 <= 세션 전체 상한
```

원장은 `session_id, turn_id, run_id?, job_id, attempt, provider, resource_scope, SKU, policy_version, price_version, unit, quantity, reserved_quantity, list_price_cost, estimated_net_cost, confirmed_net_cost(nullable), billing_status`를 기록한다. 원문·키는 기록하지 않는다. 무료 사용은 `net_cost=0`이어도 quantity와 정상가 환산값이 남는다. 미확인 청구액을 확정 0으로 표시하지 않는다.

요청/작업 시도 단위 unique 키로 비용을 1회 합산하고 재처리·반복 정산으로 `sessions.cost_usd`를 이중 증가시키지 않는다. 현재의 `agent_runs + reports` 합계 검사·API/관리자 비용 표시·e2e summary를 음성 원장까지 포함하도록 같이 바꾼다. 실제 공급자 월 청구를 세션에 나눌 때는 무료량 배정·환불/정산을 버전 기록으로 남긴다. API 요금 외 로컬 연산·호스팅/저장/네트워크 비용은 운영 비용 항목으로 따로 표시한다.

## 8. 변경 지도·검증 계획

후속 파일 계획은 `app/voice/ports.py`, `providers/{azure,google,local,fake}.py`, `quota.py`, `usage.py`다. 상위 설계의 controller가 포트만 호출하며 `config.py`, 채용 건 voice policy, `infra/.env.example`, DDL·데이터 모델, `services/budget.py`, 관리자 비용/준비 상태, 계약 픽스처를 함께 맞춘다. 실제 env·키 파일을 이번 설계에서 생성하지 않는다.

| 검증 ID | Fake 기반 필수 시나리오 |
|---|---|
| VC01 | 같은 본문을 서로 다른 p1/p2/p3 voice로 합성하고 매핑 고정. 공급자 교체 뒤 Controller·그래프 테스트 재사용 |
| VC02 | 전역 quota 마지막 잔여량에 두 요청 경합 → 한 요청만 승인. 세션 예약과 작업 하위 할당 이중 차감 없음 |
| VC03 | quota·호출 횟수·paid budget 초과 시 공급자 호출 0회. F0 SKU 검증 실패·원장 장애에서도 차단 |
| VC04 | timeout/재시작 후 unknown 예약 유지, 재시도 별도 계상. 같은 작업 정산·cache hit의 비용 중복 없음 |
| VC05 | 무료 순비용 0도 문자/초 누적. 토론 LLM·리포트·음성 합계와 `sessions.cost_usd` 일치 |
| VC06 | LLM 고정 문구 폴백도 TTS quota 검사. 음성 차단 시 미청취 카드·STT 실패를 행동 근거로 편입하지 않음 |
| VC07 | 60초 파일 안 여러 문장·중간 무음이 있어도 EOF까지 전사, 하나의 최종 message.send. 30초 timeout 고정값을 공급자별 정책으로 덮어씀 |
| VC08 | 리셋 기간·계정/SKU 범위·수동 사용 대조·원장 불일치, 공급자 429를 월 quota 초과와 구분 |

실제 품질 검증은 다음 구현 단계에서 별도 `live`로 수행한다. 동일 한국어 문장 20개(이름·숫자·단위·영문 약어·반대/동의·긴 문장)와 10/30/60초 지원자 녹음을 사용한다. TTS는 본문 누락·발음 이해·세 화자 구별, STT는 CER와 이름/부정·동의 의미 보존을 본다. 기술·발성의 차이를 지원자 인성 평가에 반영하지 않는다.

지연은 TTS 요청→완성 파일, STT 업로드 완료→최종 전사, 큐 대기, 콜드/웜을 각각 p50/p95로 남긴다. 로컬은 CPU/GPU·RAM/VRAM·모델 revision·동시 작업 수를 함께 기록한다. 이번 1차 설계에는 수치를 측정한 것처럼 쓰지 않는다. 통과 기준·성능 목표를 정하고 결과에 따라 권장안을 재검토한다.

본 문서 작성 후에도 현재 기능·계약은 바뀌지 않는다. 후속 구현 PR은 상위 음성 턴 설계의 필수 검사와 함께 위 quota·합계·교체 가능성 테스트를 추가한다.
