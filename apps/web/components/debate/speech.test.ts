// @vitest-environment happy-dom
// 완료 기준: AI 동료 음성을 받지 못해도 화면은 글로 계속되고, 연달아 실패하면 이번 화면에서 끈다.
import { afterEach, expect, test, vi } from "vitest";

import { SpeechPlayer } from "./speech";
import { FAIL_LIMIT } from "./voice";

afterEach(() => vi.unstubAllGlobals());

test(`음성을 ${FAIL_LIMIT}번 연달아 받지 못하면 AI 동료 음성을 끄고 더 요청하지 않는다`, async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("forbidden", { status: 502 }));
  vi.stubGlobal("fetch", fetch);
  const speech = new SpeechPlayer();
  speech.setEnabled(true);
  for (let i = 0; i < FAIL_LIMIT; i += 1) speech.enqueue("p1", `안녕하세요 ${i}`);
  await vi.waitFor(() => expect(speech.getSnapshot().unavailable).toBe(true));
  expect(speech.getSnapshot().playing).toBeNull();
  const calls = fetch.mock.calls.length;
  speech.enqueue("p2", "한 번 더");
  expect(fetch.mock.calls).toHaveLength(calls);
});
