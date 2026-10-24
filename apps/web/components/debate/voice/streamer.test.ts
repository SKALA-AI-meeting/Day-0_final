import { afterEach, describe, expect, it, vi } from "vitest";

import { startPcmStream } from "./streamer";

// 브라우저 오디오 노드 흉내: 연결되지 않은 노드를 끊으면 실제 브라우저처럼 예외를 던진다
class FakeNode {
  targets = new Set<unknown>();
  port = { onmessage: null as unknown };
  connect(target: unknown) {
    this.targets.add(target);
  }
  disconnect(target?: unknown) {
    if (target !== undefined && !this.targets.has(target))
      throw new Error("InvalidAccessError: the given destination is not connected");
    if (target === undefined) this.targets.clear();
    else this.targets.delete(target);
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("조각 보내기 정리", () => {
  it("정리를 두 번 불러도 예외 없이 끝난다(녹음 종료 뒤 메시지 제출 때 다시 불림)", async () => {
    vi.stubGlobal("AudioWorkletNode", FakeNode);
    const context = {
      sampleRate: 48000,
      destination: {},
      audioWorklet: { addModule: vi.fn().mockResolvedValue(undefined) },
    } as unknown as AudioContext;
    const source = new FakeNode();
    const live = await startPcmStream(context, source as unknown as AudioNode, vi.fn());
    live.stop();
    expect(() => live.stop()).not.toThrow();
  });
});
