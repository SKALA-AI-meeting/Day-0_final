import { expect, test } from "vitest";
import { frameErrorText } from "../use-session";
import {
  ACTION_LABEL,
  blockedTrouble,
  canStartVoice,
  isTypingTarget,
  turnHoldNotice,
} from "./control-state";

test("스케줄러가 차례를 넘기기 전에는 유지 시간 이후에도 녹음을 시작할 수 있다", () => {
  expect(canStartVoice("proposal")).toBe(false);
  expect(canStartVoice("keyword")).toBe(true);
});

test("발언권 유지 시간이 끝나면 0초 대신 동료가 이어 말한다는 안내를 보인다", () => {
  expect(turnHoldNotice(0)).toBe("곧 동료가 이어서 말해요…");
  expect(turnHoldNotice(1.1)).toContain("2초 안에");
});

test("막힌 차례는 이유마다 제목·할 일과 그 일을 하는 버튼 하나로 보이고, 내 차례가 아니면 버튼이 없다", () => {
  const failed = blockedTrouble("error", true)!;
  expect(failed.title).toBe("발언을 글로 옮기지 못했어요");
  expect(ACTION_LABEL[failed.action!]).toBe("다시 받아 적기");
  const dropped = blockedTrouble("voice_interrupted", true)!;
  expect(dropped.detail).toContain("끊긴 동안의 발언은 저장되지 않았어요");
  expect(ACTION_LABEL[dropped.action!]).toBe("다시 녹음");
  expect(blockedTrouble("voice_interrupted", false)!.action).toBeNull();
  // 빈 인식 결과는 오류가 아니라 직접 입력 칸으로 보인다
  expect(blockedTrouble("empty", true)).toBeNull();
  expect(Object.values(ACTION_LABEL).some((label) => label.includes("/"))).toBe(false);
});

test("Space 단축키는 글을 쓰는 곳·버튼에 포커스가 있으면 쓰지 않는다", () => {
  const closest = (match: boolean) =>
    ({ closest: () => (match ? {} : null) }) as unknown as Element;
  expect(isTypingTarget(closest(true))).toBe(true);
  expect(isTypingTarget(closest(false))).toBe(false);
  expect(isTypingTarget(null)).toBe(false);
});

test("너무 긴 발언 오류는 비었다는 안내 대신 글자 수 안내를 보인다", () => {
  expect(frameErrorText({ code: "invalid_input", message: "too long" })).toContain("자 이내로");
  expect(frameErrorText({ code: "invalid_input", message: "empty" })).toBe("내용을 입력해 주세요");
});
