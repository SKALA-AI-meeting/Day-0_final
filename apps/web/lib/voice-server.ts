// 음성(STT·TTS) 서버 호출(개발용 시험). 키는 브라우저로 나가지 않고 이 라우트에서만 쓴다.
// VOICE_API=1일 때만 열린다. 키 규칙은 services/api/app/agent/llm/roles.py와 같고, 음성 모델 두 개만
// 2026-10-23 사용자 결정으로 제한 키(OPENAI_API_KEY)를 쓴다(prefixcon PC-22). 다른 키로 몰래 바꾸지 않는다.
const OPENAI = "https://api.openai.com/v1";

export const STT_MODEL_DEFAULT = "gpt-4o-mini-transcribe";
export const TTS_MODEL_DEFAULT = "gpt-4o-mini-tts";
const RESTRICTED_KEY_MODELS = new Set(["gpt-4o-mini", STT_MODEL_DEFAULT, TTS_MODEL_DEFAULT]);

/** 녹음 파일 상한(OpenAI 전사 상한 25MB보다 작게) */
export const MAX_AUDIO_BYTES = 10 * 1024 * 1024;
/** 한 번에 읽는 글자 상한 */
export const MAX_SPEECH_CHARS = 800;
/** OpenAI 호출 상한. 넘으면 실패로 보고 화면은 글자 입력·무음으로 돌아간다 */
const TIMEOUT_MS = 30_000;

type Env = Record<string, string | undefined>;

export function voiceEnabled(env: Env = process.env) {
  return env.VOICE_API === "1";
}

export function voiceKey(model: string, env: Env = process.env): string {
  let name: string;
  if (RESTRICTED_KEY_MODELS.has(model)) name = "OPENAI_API_KEY";
  else if (model.startsWith("gpt-")) name = "OPENAI_API_KEY_PERSONAL";
  else throw new Error(`no key rule for model: ${model}`);
  const key = env[name];
  if (!key) throw new Error(`${name} is required for model ${model}`);
  return key;
}

/** 자리별 목소리. 자리(p1~p3)에 고정하고 페르소나 성향과 연결하지 않는다 */
export const SEAT_VOICE: Record<string, string> = { p1: "ash", p2: "coral", p3: "sage" };
/** 말투 지시는 세 자리 모두 같다(성향이 목소리로 드러나지 않게) */
const SPEECH_INSTRUCTIONS =
  "회의에서 동료에게 말하듯 자연스럽고 차분한 한국어로, 또박또박 읽어 주세요.";

export async function transcribe(audio: File, env: Env = process.env): Promise<string> {
  const model = env.MODEL_STT || STT_MODEL_DEFAULT;
  const form = new FormData();
  form.append("file", audio);
  form.append("model", model);
  form.append("language", "ko");
  form.append("response_format", "json");
  const res = await fetch(`${OPENAI}/audio/transcriptions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${voiceKey(model, env)}` },
    body: form,
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`transcribe ${res.status}: ${(await res.text()).slice(0, 200)}`);
  const body = (await res.json()) as { text?: string };
  return body.text ?? "";
}

export async function speech(
  text: string,
  seat: string,
  env: Env = process.env,
): Promise<Response> {
  const model = env.MODEL_TTS || TTS_MODEL_DEFAULT;
  const res = await fetch(`${OPENAI}/audio/speech`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${voiceKey(model, env)}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model,
      voice: SEAT_VOICE[seat] ?? "alloy",
      input: text,
      instructions: SPEECH_INSTRUCTIONS,
      response_format: "mp3",
    }),
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`speech ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res;
}

export function voiceError(status: number, code: string, message: string) {
  return Response.json({ error: { code, message } }, { status });
}
