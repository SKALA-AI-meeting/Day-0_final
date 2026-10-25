// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test, vi } from "vitest";

import type { Message } from "../types";
import {
  candidateMessageSaved,
  newSystemNotice,
  scheduleNotice,
  shouldResetAwaitingSaved,
  VoiceNotices,
} from "./voice-room";
import type { VoiceSnapshot } from "./voice-room";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const message = (id: string, sender: Message["sender"], content: string, client_msg_id?: string) =>
  ({ id, sender, content, client_msg_id }) as Message;

test("새 시스템 안내만 한 줄로 보이고 동료 합의 발언은 중복 안내하지 않는다", () => {
  const seen = new Set(["old"]);
  const messages = [
    message("old", "system", "이전 안내"),
    message("fill", "p3", "그럼 물로 정리할게요."),
    message("new", "system", "자료 탭이 열렸어요."),
  ];
  const notice = newSystemNotice(messages, seen);
  expect(notice).toBe("자료 탭이 열렸어요.");
  expect(newSystemNotice(messages, seen)).toBe("");
  const host = document.createElement("div");
  const root = createRoot(host);
  act(() => root.render(<VoiceNotices notice="" systemNotice={notice} />));
  expect(host.querySelectorAll("p")).toHaveLength(1);
  expect(host.textContent).toBe("자료 탭이 열렸어요.");
  act(() => root.unmount());
});

test("내 발언 저장 뒤 내용 없이 전달 확인 한 줄을 보인다", () => {
  const messages = [message("m1", "candidate", "비공개 발언", "u1")];
  expect(candidateMessageSaved(messages, "u1")).toBe(true);
  expect(candidateMessageSaved(messages, "u2")).toBe(false);
  const host = document.createElement("div");
  const root = createRoot(host);
  act(() => root.render(<VoiceNotices notice="전달했어요" systemNotice="" />));
  expect(host.textContent).toBe("전달했어요");
  expect(host.textContent).not.toContain("비공개 발언");
  act(() => root.unmount());
});

test("전달 안내는 상태 변화와 무관하게 3초 유지된다", () => {
  vi.useFakeTimers();
  let notice = "전달했어요";
  const set = (update: (now: string) => string) => {
    notice = update(notice);
  };
  scheduleNotice(notice, 3000, set);
  const state = "AI_GENERATING";
  expect(state).toBe("AI_GENERATING");
  vi.advanceTimersByTime(2999);
  expect(notice).toBe("전달했어요");
  vi.advanceTimersByTime(1);
  expect(notice).toBe("");
  vi.useRealTimers();
});

test("저장 대기 표시는 턴·epoch·상태·단계 변화에서만 리셋되고 발언이 먼저 사라져도 유지된다", () => {
  const previous = {
    turn_id: "t1",
    epoch: "e1",
    state: "PROCESSING",
    utterance: { id: "u1" },
  } as VoiceSnapshot;
  const next = (change: Partial<VoiceSnapshot>) => ({ ...previous, ...change }) as VoiceSnapshot;
  expect(shouldResetAwaitingSaved(previous, next({}), false)).toBe(false);
  // 전달 직후 발언이 먼저 사라지고 메시지가 아직 오지 않은 순간: 내 칸을 유지한다(깜빡임 방지)
  expect(
    shouldResetAwaitingSaved(
      previous,
      next({ utterance: undefined, state: "AI_GENERATING" }),
      false,
    ),
  ).toBe(false);
  for (const change of [
    { turn_id: "t2" },
    { epoch: "e2" },
    { state: "AI_SPEAKING" },
    { state: "USER_READY" },
  ])
    expect(shouldResetAwaitingSaved(previous, next(change), false)).toBe(true);
  expect(shouldResetAwaitingSaved(previous, next({}), true)).toBe(true);
});
