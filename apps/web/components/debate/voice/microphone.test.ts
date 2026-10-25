import { afterEach, expect, test, vi } from "vitest";
import { MICROPHONE_TIMEOUT_MS, requestMicrophone } from "./microphone";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("응답하지 않는 마이크 권한 요청은 8초 뒤 끝나고 늦은 스트림을 닫는다", async () => {
  vi.useFakeTimers();
  let resolveMic!: (stream: MediaStream) => void;
  const stop = vi.fn();
  const getUserMedia = vi.fn(
    () =>
      new Promise<MediaStream>((resolve) => {
        resolveMic = resolve;
      }),
  );
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
  const request = requestMicrophone();
  const failure = expect(request).rejects.toThrow("microphone_timeout");
  await vi.advanceTimersByTimeAsync(MICROPHONE_TIMEOUT_MS);
  await failure;
  resolveMic({ getTracks: () => [{ stop }] } as unknown as MediaStream);
  await Promise.resolve();
  expect(stop).toHaveBeenCalledOnce();
});
