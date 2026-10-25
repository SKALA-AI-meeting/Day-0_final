"""완성 파일 기반 음성 어댑터(Azure F0·OpenAI). 부분 인식 결과는 외부로 전달하지 않는다.

VOICE_PROVIDER가 공급자를 고른다: azure(F0, 유료 폴백 없음), openai(제한 키, 사용량 과금),
gemini(GEMINI_API_KEY, 사용량 과금), fake(개발·테스트). VOICE_STT_PROVIDER·VOICE_TTS_PROVIDER로
받아 적기와 읽기를 서로 다른 공급자에 맡길 수 있다(예: 받아 적기 whisper(로컬), 읽기 gemini).
"""

import asyncio
import base64
import io
import json
import logging
import os
import threading
import wave
from collections.abc import Sequence
from typing import Protocol
from xml.sax.saxutils import escape

import httpx

from app.services.common import ServiceError

log = logging.getLogger(__name__)
VOICES = {"p1": "ko-KR-InJoonNeural", "p2": "ko-KR-SunHiNeural", "p3": "ko-KR-BongJinNeural"}
# OpenAI 목소리. 자리(p1~p3)에 고정하고 페르소나 성향과 연결하지 않는다
OPENAI_VOICES = {"p1": "ash", "p2": "coral", "p3": "sage"}
OPENAI_URL = "https://api.openai.com/v1"
OPENAI_STT_MODEL = "gpt-4o-mini-transcribe"
OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
OPENAI_TTS_RATE = 24000  # response_format=pcm: 24kHz 16-bit mono little-endian
# 말투 지시는 세 자리 모두 같다(성향이 목소리로 드러나지 않게). gpt-4o 계열 TTS만 받는다
OPENAI_SPEECH_INSTRUCTIONS = (
    "회의에서 동료에게 말하듯 자연스럽고 차분한 한국어로, 또박또박 읽어 주세요."
)


# Gemini 목소리(prebuilt). 자리(p1~p3)에 고정하고 페르소나 성향과 연결하지 않는다
GEMINI_VOICES = {"p1": "Charon", "p2": "Kore", "p3": "Orus"}
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_STT_MODEL = "gemini-3.5-flash-lite"
GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_TTS_RATE = 24000  # mimeType에 rate가 없을 때(audio/L16;codec=pcm;rate=24000이 기본)
# 받아 적기 지시. 생성 모델이라 다듬기·요약·지어내기를 막는다(전사가 평가 근거가 된다)
GEMINI_STT_PROMPT = (
    "이 녹음을 한국어로 들리는 그대로 받아 적어라. 말한 내용만 출력하고 설명·따옴표·요약을 붙이지"
    " 마라. 말을 고치거나 매끄럽게 다듬지 말고, 말소리가 없으면 빈 문자열을 출력하라."
)
# 사용량 과금 공급자(F0 무료 등급이 아님): 정책 예산·과금 표시가 다르다
METERED_PROVIDERS = frozenset({"openai", "gemini"})


def voices_for(provider: str) -> dict[str, str]:
    """채용 건 정책에 복사할 자리별 목소리"""
    if provider == "gemini":
        return dict(GEMINI_VOICES)
    return dict(OPENAI_VOICES if provider == "openai" else VOICES)


# 빠른 받아 적기 API 버전. 구문 목록(phraseList)은 이 버전부터 지원한다(MS 문서)
FAST_TRANSCRIPTION_API = "2025-10-15"


class TtsService(Protocol):
    async def synthesize(self, text: str, voice: str) -> bytes: ...


class SttService(Protocol):
    async def transcribe(self, audio: bytes, phrases: Sequence[str] = ()) -> str: ...


def pcm(audio: bytes) -> tuple[bytes, float]:
    """서버가 확인한 PCM16 mono 16kHz WAV. 파일 길이·프레임 길이도 함께 검사한다."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as src:
            if (src.getnchannels(), src.getsampwidth(), src.getframerate()) != (1, 2, 16000):
                raise ValueError("format")
            frames = src.readframes(src.getnframes())
            if len(frames) != src.getnframes() * 2:
                raise ValueError("truncated")
            seconds = src.getnframes() / 16000
            if not 0 < seconds <= 60:
                raise ValueError("duration")
            return frames, seconds
    except (wave.Error, EOFError, ValueError) as exc:
        raise ServiceError(422, "voice_audio_invalid", "PCM16 mono 16kHz WAV, 최대 60초") from exc


def silence(seconds: float = 0.1) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(16000)
        dst.writeframes(b"\0\0" * int(seconds * 16000))
    return out.getvalue()


def wav_from_pcm(frames: bytes, rate: int) -> bytes:
    """PCM16 mono 원본에 길이가 맞는 WAV 머리를 붙인다.

    재생 길이 계산(controller)이 머리의 프레임 수를 읽는다.
    """
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(rate)
        dst.writeframes(frames[: len(frames) // 2 * 2])
    return out.getvalue()


class FakeSpeech:
    """개발·테스트 전용. 실제 음성 인식인 것처럼 가장하지 않는다."""

    def __init__(self, transcript: str = "") -> None:
        self.transcript = transcript
        self.calls: list[tuple[str, str]] = []
        self.phrases: list[str] = []  # 마지막 받아 적기에 넘어온 구문 목록(테스트 확인용)

    async def verify(self) -> None:
        return None

    async def synthesize(self, text: str, voice: str) -> bytes:
        self.calls.append((text, voice))
        return silence()

    async def transcribe(self, audio: bytes, phrases: Sequence[str] = ()) -> str:
        pcm(audio)
        self.phrases = list(phrases)
        return self.transcript


class AzureSpeech:
    """관리 API로 F0·지역·키 소속을 확인한 뒤에만 호출한다. 유료 폴백 없음."""

    def __init__(self) -> None:
        self.key = os.getenv("AZURE_SPEECH_KEY", "")
        self.region = os.getenv("AZURE_SPEECH_REGION", "")
        self.resource = os.getenv("AZURE_SPEECH_RESOURCE_ID", "")
        self._verify_lock = asyncio.Lock()
        self._verified_until = 0.0

    async def verify(self) -> None:
        import hmac
        import time

        from azure.identity import DefaultAzureCredential

        async with self._verify_lock:
            if time.monotonic() < self._verified_until:
                return
            if not self.key or not self.region or not self.resource.startswith("/subscriptions/"):
                raise ServiceError(503, "voice_unavailable", "Azure F0 음성 설정이 필요합니다")

            def token():
                with DefaultAzureCredential(exclude_interactive_browser_credential=True) as cred:
                    return cred.get_token("https://management.azure.com/.default").token

            try:
                access = await asyncio.to_thread(token)
                url = f"https://management.azure.com{self.resource}"
                async with httpx.AsyncClient(timeout=15) as client:
                    headers = {"Authorization": f"Bearer {access}"}
                    res = await client.get(
                        url, params={"api-version": "2023-05-01"}, headers=headers
                    )
                    res.raise_for_status()
                    data = res.json()
                    keys = await client.post(
                        url + "/listKeys", params={"api-version": "2023-05-01"}, headers=headers
                    )
                    keys.raise_for_status()
                    owned = any(hmac.compare_digest(self.key, str(k)) for k in keys.json().values())
                    if data["sku"]["name"] != "F0" or data["location"] != self.region or not owned:
                        raise ValueError("not the configured F0 resource")
                self._verified_until = time.monotonic() + 60
            except Exception as exc:
                raise ServiceError(503, "voice_unavailable", "Azure F0 소속 확인 실패") from exc

    async def synthesize(self, text: str, voice: str) -> bytes:
        if voice not in VOICES.values():
            raise ServiceError(422, "voice_policy_mismatch", "허용되지 않은 voice")
        await self.verify()
        ssml = (
            f'<speak version="1.0" xml:lang="ko-KR"><voice name="{voice}">'
            f"{escape(text)}</voice></speak>"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1",
                headers={
                    "Ocp-Apim-Subscription-Key": self.key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "riff-16khz-16bit-mono-pcm",
                    "User-Agent": "DayZero",
                },
                content=ssml.encode(),
            )
            response.raise_for_status()
            return response.content

    async def transcribe(self, audio: bytes, phrases: Sequence[str] = ()) -> str:
        """빠른 받아 적기(fast transcription): 완성 파일을 요청 한 번으로 보내 결과를 바로 받는다.

        10/23 측정에서 말한 길이의 0.05~0.08배가 걸렸다(연속 인식은 약 0.4배). 이름도 더 정확했다.
        Azure 요금표는 이 기능이 F0 무료량에 드는지 밝히지 않으므로, 거절되면 같은 파일을 기존
        연속 인식으로 받아 적는다(둘 다 F0 안의 경로라 유료 자동 전환은 없다).
        phrases: 자주 나올 단어(동료 이름 등). 구문 목록으로 보내 그 단어로 알아듣게 돕는다.
        """
        frames, seconds = pcm(audio)
        await self.verify()
        try:
            return await self._transcribe_fast(audio, phrases)
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "-")
            log.warning(
                "fast transcription 실패(%s %s), 연속 인식으로 대체", type(exc).__name__, status
            )
            return await self._transcribe_continuous(frames, seconds, phrases)

    async def _transcribe_fast(self, audio: bytes, phrases: Sequence[str]) -> str:
        definition: dict = {"locales": ["ko-KR"]}
        if phrases:
            definition["phraseList"] = {"phrases": list(phrases)}
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"https://{self.region}.api.cognitive.microsoft.com"
                "/speechtotext/transcriptions:transcribe",
                params={"api-version": FAST_TRANSCRIPTION_API},
                headers={"Ocp-Apim-Subscription-Key": self.key},
                files={
                    "audio": ("speech.wav", audio, "audio/wav"),
                    "definition": (None, json.dumps(definition), "application/json"),
                },
            )
            response.raise_for_status()
        return " ".join(p["text"] for p in response.json().get("combinedPhrases", [])).strip()

    async def _transcribe_continuous(
        self, frames: bytes, seconds: float, phrases: Sequence[str]
    ) -> str:
        """예비 경로: 완성 파일을 SDK 연속 인식으로 끝까지 읽고 최종 구간들을 합친다."""

        def recognize() -> str:
            import azure.cognitiveservices.speech as sdk

            stream = sdk.audio.PushAudioInputStream(
                stream_format=sdk.audio.AudioStreamFormat(
                    samples_per_second=16000, bits_per_sample=16, channels=1
                )
            )
            config = sdk.SpeechConfig(subscription=self.key, region=self.region)
            config.speech_recognition_language = "ko-KR"
            recognizer = sdk.SpeechRecognizer(config, sdk.audio.AudioConfig(stream=stream))
            if phrases:
                grammar = sdk.PhraseListGrammar.from_recognizer(recognizer)
                for phrase in phrases:
                    grammar.addPhrase(phrase)
            done = threading.Event()
            chunks, errors = [], []

            def recognized(event):
                if event.result.reason == sdk.ResultReason.RecognizedSpeech:
                    chunks.append(event.result.text)

            def canceled(event):
                if event.reason == sdk.CancellationReason.Error:
                    errors.append("recognition failed")
                done.set()

            recognizer.recognized.connect(recognized)
            recognizer.session_stopped.connect(lambda _: done.set())
            recognizer.canceled.connect(canceled)
            recognizer.start_continuous_recognition_async().get()
            try:
                stream.write(frames)
                stream.close()
                if not done.wait(seconds + 30):
                    raise TimeoutError("STT timeout")
                if errors:
                    raise RuntimeError("STT canceled")
                return " ".join(chunks).strip()
            finally:
                recognizer.stop_continuous_recognition_async().get()

        return await asyncio.to_thread(recognize)


class OpenAISpeech:
    """OpenAI 음성: 받아 적기 /audio/transcriptions, 읽기 /audio/speech.

    키는 제한 키 허용 목록 규칙(app/agent/llm/roles.py speech_key)을 따르고,
    모델은 MODEL_STT·MODEL_TTS로 바꾼다. 사용량 과금이다.
    """

    def __init__(self) -> None:
        self.stt_model = os.getenv("MODEL_STT") or OPENAI_STT_MODEL
        self.tts_model = os.getenv("MODEL_TTS") or OPENAI_TTS_MODEL

    def _key(self, model: str) -> str:
        from app.agent.llm.roles import speech_key

        try:
            return speech_key(model)
        except ValueError as exc:
            raise ServiceError(503, "voice_unavailable", "OpenAI 음성 설정이 필요합니다") from exc

    async def verify(self) -> None:
        """키와 모델 설정만 확인한다(외부 호출 없음). 모델 권한(403)은 실제 호출에서 드러난다."""
        self._key(self.stt_model)
        self._key(self.tts_model)

    async def synthesize(self, text: str, voice: str) -> bytes:
        if voice not in OPENAI_VOICES.values():
            raise ServiceError(422, "voice_policy_mismatch", "허용되지 않은 voice")
        body = {"model": self.tts_model, "voice": voice, "input": text, "response_format": "pcm"}
        if self.tts_model.startswith("gpt-4o"):
            body["instructions"] = OPENAI_SPEECH_INSTRUCTIONS
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{OPENAI_URL}/audio/speech",
                headers={"Authorization": f"Bearer {self._key(self.tts_model)}"},
                json=body,
            )
            response.raise_for_status()
        return wav_from_pcm(response.content, OPENAI_TTS_RATE)

    async def transcribe(self, audio: bytes, phrases: Sequence[str] = ()) -> str:
        """완성 WAV를 한 번에 받아 적는다.

        phrases(동료 이름 등)는 prompt로 넘겨 그 단어로 알아듣게 돕는다.
        """
        pcm(audio)
        data = {"model": self.stt_model, "language": "ko", "response_format": "json"}
        if phrases:
            data["prompt"] = "회의 참여자와 자주 나오는 말: " + ", ".join(phrases)
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{OPENAI_URL}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._key(self.stt_model)}"},
                data=data,
                files={"file": ("speech.wav", audio, "audio/wav")},
            )
            response.raise_for_status()
        return str(response.json().get("text", "")).strip()


class GeminiSpeech:
    """Gemini 음성: 받아 적기는 오디오 입력 generateContent, 읽기는 TTS 모델(AUDIO 응답).

    키는 기존 규칙(gemini-* → GEMINI_API_KEY, app/agent/llm/roles.py key_for)을 따르고,
    모델은 MODEL_STT·MODEL_TTS로 바꾼다. 사용량 과금이다.
    """

    def __init__(self) -> None:
        self.stt_model = os.getenv("MODEL_STT") or GEMINI_STT_MODEL
        self.tts_model = os.getenv("MODEL_TTS") or GEMINI_TTS_MODEL

    def _key(self, model: str) -> str:
        from app.agent.llm.roles import key_for

        if not model.startswith("gemini-"):
            raise ServiceError(503, "voice_unavailable", "Gemini 음성 모델이 아닙니다")
        try:
            return key_for(model)[0]
        except ValueError as exc:
            raise ServiceError(503, "voice_unavailable", "Gemini 음성 설정이 필요합니다") from exc

    async def verify(self) -> None:
        """키와 모델 설정만 확인한다(외부 호출 없음)."""
        self._key(self.stt_model)
        self._key(self.tts_model)

    async def _generate(self, model: str, body: dict, timeout: float) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{GEMINI_URL}/{model}:generateContent",
                headers={"x-goog-api-key": self._key(model)},
                json=body,
            )
            response.raise_for_status()
        return response.json()

    async def synthesize(self, text: str, voice: str) -> bytes:
        if voice not in GEMINI_VOICES.values():
            raise ServiceError(422, "voice_policy_mismatch", "허용되지 않은 voice")
        body = {
            # 말투 지시는 세 자리 모두 같다(성향이 목소리로 드러나지 않게)
            "contents": [{"parts": [{"text": f"{OPENAI_SPEECH_INSTRUCTIONS}\n\n{text}"}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
            },
        }
        data = await self._generate(self.tts_model, body, timeout=60)
        part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        audio = base64.b64decode(part["data"])
        # 모델에 따라 WAV(audio/wav)나 원시 PCM(audio/L16;rate=…)으로 온다
        if audio[:4] == b"RIFF":
            return audio
        mime = part.get("mimeType", "")
        rate = int(mime.split("rate=")[-1].split(";")[0]) if "rate=" in mime else GEMINI_TTS_RATE
        return wav_from_pcm(audio, rate)

    async def transcribe(self, audio: bytes, phrases: Sequence[str] = ()) -> str:
        """완성 WAV를 한 번에 받아 적는다.

        phrases(동료 이름 등)는 지시에 붙여 그 단어로 알아듣게 돕는다.
        """
        pcm(audio)
        prompt = GEMINI_STT_PROMPT
        if phrases:
            prompt += " 나올 수 있는 이름·단어: " + ", ".join(phrases) + "."
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "audio/wav",
                                "data": base64.b64encode(audio).decode(),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {"temperature": 0},
        }
        data = await self._generate(self.stt_model, body, timeout=60)
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts).strip().strip('"').strip()


# 로컬 받아 적기(faster-whisper, CPU int8). 모델·스레드·빔은 env로 바꾼다
WHISPER_MODEL = "large-v3-turbo"


class WhisperSpeech:
    """로컬 받아 적기 전용(faster-whisper). 소리가 이 서버 밖으로 나가지 않고 과금이 없다.

    모델은 처음 쓸 때 WHISPER_CACHE(기본 /models/whisper)에 내려받아 프로세스에 하나만 둔다.
    CPU를 많이 쓰므로 동시에 WHISPER_CONCURRENCY개(기본 1)까지만 받아 적는다. 읽기는 하지 않는다.
    """

    _models: dict = {}
    _load_lock = threading.Lock()

    def __init__(self) -> None:
        self.model_name = os.getenv("WHISPER_MODEL") or WHISPER_MODEL
        self.threads = int(os.getenv("WHISPER_THREADS", "4"))
        self.beam = int(os.getenv("WHISPER_BEAM", "1"))
        self.cache = os.getenv("WHISPER_CACHE", "/models/whisper")
        self._slots = asyncio.Semaphore(int(os.getenv("WHISPER_CONCURRENCY", "1")))

    def _model(self):
        key = (self.model_name, self.threads)
        with self._load_lock:
            if key not in self._models:
                from faster_whisper import WhisperModel

                self._models[key] = WhisperModel(
                    self.model_name,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=self.threads,
                    download_root=self.cache,
                )
            return self._models[key]

    async def verify(self) -> None:
        """모델을 적재해 둔다(처음 한 번은 내려받기 때문에 오래 걸린다)."""
        try:
            await asyncio.to_thread(self._model)
        except Exception as exc:
            raise ServiceError(
                503, "voice_unavailable", "로컬 받아 적기 모델을 쓸 수 없습니다"
            ) from exc

    async def synthesize(self, text: str, voice: str) -> bytes:
        raise ServiceError(503, "voice_unavailable", "로컬 받아 적기 공급자는 읽기를 하지 않습니다")

    async def transcribe(self, audio: bytes, phrases: Sequence[str] = ()) -> str:
        """완성 WAV(PCM16 mono 16kHz)를 받아 적는다. phrases는 initial_prompt로 넘긴다."""
        frames, _ = pcm(audio)
        prompt = ("참여자와 자주 나오는 말: " + ", ".join(phrases) + ".") if phrases else None

        def run() -> str:
            import numpy as np

            wav = np.frombuffer(frames, np.int16).astype(np.float32) / 32768
            segments, _ = self._model().transcribe(
                wav,
                language="ko",
                beam_size=self.beam,
                initial_prompt=prompt,
                condition_on_previous_text=False,
                vad_filter=False,
            )
            return "".join(s.text for s in segments).strip()

        async with self._slots:
            return await asyncio.to_thread(run)


class SplitSpeech:
    """받아 적기와 읽기를 서로 다른 공급자에 맡긴다."""

    def __init__(self, stt, tts) -> None:
        self.stt = stt
        self.tts = tts

    async def verify(self) -> None:
        await self.stt.verify()
        await self.tts.verify()

    async def synthesize(self, text: str, voice: str) -> bytes:
        return await self.tts.synthesize(text, voice)

    async def transcribe(self, audio: bytes, phrases: Sequence[str] = ()) -> str:
        return await self.stt.transcribe(audio, phrases)


def voice_providers() -> tuple[str, str]:
    """(받아 적기, 읽기) 공급자 이름. 따로 정하지 않으면 VOICE_PROVIDER 하나를 쓴다."""
    name = os.getenv("VOICE_PROVIDER", "azure")
    return os.getenv("VOICE_STT_PROVIDER") or name, os.getenv("VOICE_TTS_PROVIDER") or name


def provider_label() -> str:
    """채용 건 정책·사용량 원장에 쓰는 이름. 둘이 같으면 그 이름, 다르면 "받아적기+읽기"."""
    stt, tts = voice_providers()
    return stt if stt == tts else f"{stt}+{tts}"


def provider():
    stt, tts = voice_providers()
    if stt == tts:
        return _provider(stt)
    if tts == "whisper":
        raise ServiceError(503, "voice_unavailable", "whisper는 받아 적기 전용입니다")
    return SplitSpeech(_provider(stt), _provider(tts))


def _provider(name: str):
    if name == "whisper":
        return WhisperSpeech()
    if name == "fake":
        if os.getenv("LLM_FAKE") != "1" and os.getenv("ENABLE_TEST_API") != "1":
            raise ServiceError(503, "voice_unavailable", "FakeSpeech는 개발 모드 전용입니다")
        return FakeSpeech()
    if name == "openai":
        return OpenAISpeech()
    if name == "gemini":
        return GeminiSpeech()
    if name != "azure":
        raise ServiceError(503, "voice_unavailable", "지원하지 않는 음성 공급자")
    return AzureSpeech()
