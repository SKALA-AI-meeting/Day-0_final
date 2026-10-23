// 음성 시험 라우트를 쓸 수 있는지(VOICE_API=1이고 그 모델 키가 있는지). 화면은 이것으로 말하기·AI 동료 음성을 켜거나 숨긴다.
// 모델 권한(403)처럼 호출해 봐야 아는 실패는 화면이 연속 실패로 판단해 끈다(voice.ts·speech.ts).
import { STT_MODEL_DEFAULT, TTS_MODEL_DEFAULT, voiceEnabled, voiceKey } from "@/lib/voice-server";

export const dynamic = "force-dynamic";

function hasKey(model: string) {
  try {
    voiceKey(model);
    return true;
  } catch {
    return false;
  }
}

export function GET() {
  const on = voiceEnabled();
  return Response.json({
    stt: on && hasKey(process.env.MODEL_STT || STT_MODEL_DEFAULT),
    tts: on && hasKey(process.env.MODEL_TTS || TTS_MODEL_DEFAULT),
  });
}
