"""말하는 동안 받아 적기: 녹음 중 받은 소리 조각을 쉬는 곳에서 끊어 빠른 받아 적기로 미리 보낸다.

녹음이 끝나면 마지막 조각만 남아 기다림이 짧다. 같은 빠른 받아 적기를 쓰므로 정확도는 완성 파일과
같은 모델이다. 조각 순번이 빠졌거나 받은 길이가 완성 파일과 맞지 않으면 Fallback을 내고, 호출자는
완성 파일 전체를 받아 적는다(예비 경로). API 프로세스가 하나라(app/run.py) 메모리에 들고 있는다.
"""

import array
import asyncio
import io
import logging
import os
import sys
import wave
from collections.abc import Sequence

log = logging.getLogger(__name__)

RATE = 16000
BYTES_PER_SECOND = RATE * 2  # PCM16 mono
FRAME_BYTES = BYTES_PER_SECOND // 50  # 20ms 단위로 소리 크기를 잰다
MAX_SECONDS = 60.5  # 발언 한도 60초 + 여유
# 스트리밍 길이와 완성 파일 길이의 허용 차이(초). 녹음기·조각 수집 시작 시점 차이를 감안한다.
LENGTH_TOLERANCE_SECONDS = 1.0
TAIL_MIN_BYTES = BYTES_PER_SECOND // 10  # 0.1초보다 짧은 꼬리는 보내지 않는다
FINISH_TIMEOUT_SECONDS = 30


class Fallback(Exception):
    """조각 받아 적기를 쓸 수 없음. 완성 파일 전체로 받아 적어야 한다."""


def settings() -> tuple[float, float, float]:
    """(자르기 시작 길이, 자를 곳을 찾는 구간 길이, 쉼 길이) 초. 범위 밖이면 설정 오류로 멈춘다."""
    segment = float(os.getenv("VOICE_SEGMENT_SECONDS", "10"))
    search = float(os.getenv("VOICE_SEGMENT_SEARCH_SECONDS", "4"))
    pause = int(os.getenv("VOICE_SEGMENT_PAUSE_MS", "300")) / 1000
    if not 4 <= segment <= 30 or not 1 <= search < segment or not 0.1 <= pause <= 1:
        raise ValueError(
            "VOICE_SEGMENT_* 범위: SECONDS 4~30, SEARCH 1~SECONDS 미만, PAUSE 100~1000"
        )
    return segment, search, pause


def wav(frames: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(RATE)
        dst.writeframes(frames)
    return out.getvalue()


def quietest_cut(frames: bytes | bytearray, start: int, end: int, pause: float) -> int:
    """frames[start:end] 안에서 pause초 동안 소리가 가장 작은 곳의 가운데 바이트 위치.

    말이 잠깐 멈춘 곳을 고르면 단어 중간에서 자르지 않는다. 짝수 위치(샘플 경계)를 돌려준다.
    """
    samples = array.array("h")
    samples.frombytes(bytes(frames[start:end]))
    if sys.byteorder != "little":  # WAV PCM16은 리틀 엔디언
        samples.byteswap()
    per_frame = FRAME_BYTES // 2
    energy = [
        sum(s * s for s in samples[i : i + per_frame]) for i in range(0, len(samples), per_frame)
    ]
    width = max(1, round(pause * 50))
    if len(energy) <= width:
        return start + (end - start) // 2 // 2 * 2
    window = sum(energy[:width])
    best, best_at = window, 0
    for i in range(1, len(energy) - width + 1):
        window += energy[i + width - 1] - energy[i - 1]
        if window < best:
            best, best_at = window, i
    return start + (best_at + width // 2) * FRAME_BYTES


class Segmenter:
    """한 발언의 소리 조각을 순서대로 받아 쉬는 곳에서 잘라 미리 받아 적는다."""

    def __init__(self, session_id: str, speech, phrases: Sequence[str]) -> None:
        self.session_id = session_id
        self.speech = speech
        self.phrases = list(phrases)
        self.expected = 0
        self.buffer = bytearray()
        self.total = 0
        self.tasks: list[asyncio.Task] = []
        self.broken: str | None = None

    def feed(self, seq: int, chunk: bytes) -> None:
        if self.broken:
            return
        if seq != self.expected or len(chunk) % 2:
            self.broken = f"조각 순번 {seq}(기대 {self.expected}) 또는 길이 오류"
            return
        self.expected += 1
        self.buffer += chunk
        self.total += len(chunk)
        if self.total > MAX_SECONDS * BYTES_PER_SECOND:
            self.broken = "발언 한도 초과"
            return
        segment, search, pause = settings()
        if len(self.buffer) >= segment * BYTES_PER_SECOND:
            start = int((segment - search) * BYTES_PER_SECOND) // 2 * 2
            cut = quietest_cut(self.buffer, start, len(self.buffer), pause)
            self._send(bytes(self.buffer[:cut]))
            del self.buffer[:cut]

    def _send(self, frames: bytes) -> None:
        self.tasks.append(asyncio.create_task(self.speech.transcribe(wav(frames), self.phrases)))

    async def finish(self, seconds: float) -> str:
        """완성 파일 길이(초)와 맞춰 본 뒤 남은 꼬리를 보내고 모든 조각의 글을 순서대로 잇는다."""
        streamed = self.total / BYTES_PER_SECOND
        if self.broken or abs(streamed - seconds) > LENGTH_TOLERANCE_SECONDS:
            self.cancel()
            raise Fallback(
                self.broken or f"길이 불일치(조각 {streamed:.1f}초, 파일 {seconds:.1f}초)"
            )
        if len(self.buffer) >= TAIL_MIN_BYTES:
            self._send(bytes(self.buffer))
            self.buffer.clear()
        try:
            texts = await asyncio.wait_for(asyncio.gather(*self.tasks), FINISH_TIMEOUT_SECONDS)
        except Exception as exc:
            self.cancel()
            raise Fallback(f"조각 받아 적기 실패: {type(exc).__name__}") from exc
        return " ".join(t.strip() for t in texts if t and t.strip())

    def cancel(self) -> None:
        for task in self.tasks:
            task.cancel()


_live: dict[str, Segmenter] = {}


def open_segmenter(utterance_id: str, session_id: str, speech, phrases: Sequence[str]) -> Segmenter:
    """발언마다 하나. 같은 세션의 다른(지난) 발언 것은 버린다."""
    for other, seg in list(_live.items()):
        if other != utterance_id and seg.session_id == session_id:
            discard(other)
    if utterance_id not in _live:
        _live[utterance_id] = Segmenter(session_id, speech, phrases)
    return _live[utterance_id]


def pop(utterance_id: str) -> Segmenter | None:
    return _live.pop(utterance_id, None)


def discard(utterance_id: str) -> None:
    seg = _live.pop(utterance_id, None)
    if seg:
        seg.cancel()
