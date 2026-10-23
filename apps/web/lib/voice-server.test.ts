import { expect, test } from "vitest";

import { POST as speechPOST } from "@/app/api/voice/speech/route";

import { voiceEnabled, voiceKey } from "./voice-server";

test("음성 모델 키 규칙: 음성 두 모델과 gpt-4o-mini는 제한 키, 그 밖의 gpt-*는 개인 키, 비면 멈춘다", () => {
  const env = { OPENAI_API_KEY: "restricted", OPENAI_API_KEY_PERSONAL: "personal" };
  expect(voiceKey("gpt-4o-mini-transcribe", env)).toBe("restricted");
  expect(voiceKey("gpt-4o-mini-tts", env)).toBe("restricted");
  expect(voiceKey("gpt-4o-transcribe", env)).toBe("personal");
  expect(() => voiceKey("whisper-1", env)).toThrow(/no key rule/);
  expect(() => voiceKey("gpt-4o-mini-tts", {})).toThrow(/OPENAI_API_KEY is required/);
});

test("VOICE_API=1이 아니면 음성 라우트는 닫혀 있다", async () => {
  expect(voiceEnabled({})).toBe(false);
  const res = await speechPOST(
    new Request("http://localhost/api/voice/speech", {
      method: "POST",
      body: JSON.stringify({ text: "안녕하세요", seat: "p1" }),
    }),
  );
  expect(res.status).toBe(404);
});
