import { expect, test } from "vitest";
import { canStartVoice, turnHoldNotice } from "./control-state";

test("스케줄러가 차례를 넘기기 전에는 유지 시간 이후에도 녹음을 시작할 수 있다", () => {
  expect(canStartVoice("proposal")).toBe(false);
  expect(canStartVoice("keyword")).toBe(true);
});

test("발언권 유지 시간이 끝나면 0초 대신 동료가 이어 말한다는 안내를 보인다", () => {
  expect(turnHoldNotice(0)).toBe("곧 동료가 이어서 말해요…");
  expect(turnHoldNotice(1.1)).toContain("2초 안에");
});
