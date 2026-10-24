"""완성 파일 기반 Azure F0 음성 어댑터. 부분 인식 결과는 외부로 전달하지 않는다."""

import asyncio
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


def provider():
    if os.getenv("VOICE_PROVIDER", "azure") == "fake":
        if os.getenv("LLM_FAKE") != "1" and os.getenv("ENABLE_TEST_API") != "1":
            raise ServiceError(503, "voice_unavailable", "FakeSpeech는 개발 모드 전용입니다")
        return FakeSpeech()
    if os.getenv("VOICE_PROVIDER", "azure") != "azure":
        raise ServiceError(503, "voice_unavailable", "지원하지 않는 음성 공급자")
    return AzureSpeech()
