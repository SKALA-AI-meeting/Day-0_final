import { expect, test } from "vitest";

import { elapsedText, rmsLevel, smoothLevel } from "./voice";

test("마이크 소리 크기: 무음은 0, 큰 소리는 1로 자르고, 빠르게 오르고 천천히 내려온다", () => {
  expect(rmsLevel(new Uint8Array(64).fill(128))).toBe(0);
  const loud = new Uint8Array(64).map((_, i) => (i % 2 ? 255 : 0));
  expect(rmsLevel(loud)).toBe(1);
  const up = smoothLevel(0, 1);
  const down = smoothLevel(1, 0);
  expect(up).toBeGreaterThan(1 - down);
  expect(elapsedText(7_400)).toBe("0:07");
  expect(elapsedText(65_000)).toBe("1:05");
});
