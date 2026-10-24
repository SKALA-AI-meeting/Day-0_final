// @vitest-environment happy-dom
// 완료 기준: 음성 기능이 없거나 실패해도 기존 글자 입력 흐름이 그대로다(대체 순서, 연속 실패 시 끄기).
import { expect, test, vi } from "vitest";

import { FAIL_LIMIT, VoiceInput, fallbackTranscribe } from "./voice";

test("글자 변환은 앞이 실패하면 다음으로 넘어가고, 모두 실패하면 오류를 낸다", async () => {
  const fail = vi.fn().mockRejectedValue(new Error("403"));
  const ok = vi.fn().mockResolvedValue("예시 문장");
  const audio = new Blob(["x"]);
  await expect(fallbackTranscribe([fail, ok])(audio)).resolves.toBe("예시 문장");
  expect(fail).toHaveBeenCalledOnce();
  await expect(fallbackTranscribe([fail])(audio)).rejects.toThrow("403");
});

test(`글자 변환이 ${FAIL_LIMIT}번 연달아 실패하면 말하기를 끄고 다시 시작하지 않는다`, async () => {
  const voice = new VoiceInput(() => Promise.reject(new Error("403")));
  // 녹음 중 상태로 만든 뒤 끝내기(마이크·MediaRecorder 없이 상태만 본다)
  const record = async () => {
    (voice as unknown as { state: { status: string } }).state.status = "recording";
    return voice.stop();
  };
  expect(await record()).toBeNull();
  expect(voice.getState()).toMatchObject({ error: "failed", disabled: false });
  expect(await record()).toBeNull();
  expect(voice.getState()).toMatchObject({ error: "off", disabled: true, status: "idle" });
  await voice.start("record");
  expect(voice.getState().status).toBe("idle");
});
