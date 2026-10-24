// AI 동료 메시지 → 음성(TTS). 개발용 시험 라우트(VOICE_API=1). 목소리는 자리별로 고정한다.
import { MAX_SPEECH_CHARS, SEAT_VOICE, speech, voiceEnabled, voiceError } from "@/lib/voice-server";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  if (!voiceEnabled()) return voiceError(404, "voice_disabled", "음성 기능이 꺼져 있습니다.");
  const body = (await request.json().catch(() => null)) as {
    text?: unknown;
    seat?: unknown;
  } | null;
  const text = typeof body?.text === "string" ? body.text.trim() : "";
  const seat = typeof body?.seat === "string" ? body.seat : "";
  if (!text || text.length > MAX_SPEECH_CHARS || !(seat in SEAT_VOICE))
    return voiceError(400, "invalid_speech", "읽을 글이나 자리가 올바르지 않습니다.");
  try {
    const res = await speech(text, seat);
    return new Response(res.body, {
      headers: { "Content-Type": "audio/mpeg", "Cache-Control": "no-store" },
    });
  } catch (e) {
    console.error("[voice] speech failed:", (e as Error).message);
    return voiceError(502, "speech_failed", "음성을 만들지 못했습니다.");
  }
}
