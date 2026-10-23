// 지원자 녹음 → 글자(STT). 개발용 시험 라우트(VOICE_API=1). 녹음은 저장하지 않고 OpenAI로만 넘긴다.
import { MAX_AUDIO_BYTES, transcribe, voiceEnabled, voiceError } from "@/lib/voice-server";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  if (!voiceEnabled()) return voiceError(404, "voice_disabled", "음성 기능이 꺼져 있습니다.");
  const form = await request.formData().catch(() => null);
  const file = form?.get("file");
  if (!(file instanceof File) || file.size === 0)
    return voiceError(400, "invalid_audio", "녹음 파일이 없습니다.");
  if (file.size > MAX_AUDIO_BYTES) return voiceError(413, "audio_too_large", "녹음이 너무 깁니다.");
  try {
    return Response.json({ text: await transcribe(file) });
  } catch (e) {
    console.error("[voice] transcribe failed:", (e as Error).message);
    return voiceError(502, "transcribe_failed", "말한 내용을 글자로 바꾸지 못했습니다.");
  }
}
