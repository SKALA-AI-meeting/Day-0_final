// @vitest-environment happy-dom
// 완료 기준: 음성 토론에서 토론방이 한 번 열리면, 첫 차례를 말없이 넘겨 발언권이 동료에게 가도 인사 화면으로 돌아가지 않는다.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import keyword from "@/mocks/fixtures/state_keyword.json";

import { DebateView } from "./debate-view";
import { initialView } from "./reducer";
import type { StateSnapshot } from "./types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// 첫 동료 인사만 끝난 세션 첫 화면(아직 세 명이 다 말하지 않았고 지원자 발언도 없음)
const snap = (() => {
  const s = structuredClone(keyword) as StateSnapshot;
  s.messages = s.messages.filter((m) => m.sender === "p1").slice(0, 1);
  s.slots = s.slots.map((x) => ({ ...x, status: "empty", value: null, resolution: null }));
  s.materials = { ...s.materials, locked: true };
  s.assistant = [];
  return s;
})();

const turn = vi.hoisted(() => ({ candidate: false }));
vi.mock("./use-session", () => ({
  useDebateSession: (_auth: unknown, initial: StateSnapshot) =>
    new Proxy(
      {
        view: initialView(initial, 0),
        now: 0,
        connection: "open",
        proposal: { draft: initial.proposal.sections, savedAt: null, saving: false },
      },
      {
        get: (target, key: string) =>
          key in target ? target[key as keyof typeof target] : () => {},
      },
    ),
}));
vi.mock("./voice/voice-room", () => ({
  useVoiceTurn: ({ snap }: { snap: StateSnapshot }) => ({
    candidateTurn: turn.candidate,
    chatSnap: snap,
    controls: null,
    currentCaption: null,
    featuredSeat: null,
    hand: undefined,
    mic: undefined,
    recentCaption: undefined,
    recording: false,
    speakingSeat: null,
  }),
}));

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const auth = { sessionId: snap.session.id, getToken: () => "t", refreshToken: async () => "t" };
const render = (candidate: boolean) => {
  turn.candidate = candidate;
  act(() => root.render(<DebateView auth={auth as never} initial={snap} onExit={() => {}} />));
};
const introShown = () =>
  [...container.querySelectorAll("button")].some((b) => b.textContent?.includes("바로 대화하기"));

test("첫 내 차례를 말없이 넘겨도 열린 토론방이 인사 화면으로 돌아가지 않는다", () => {
  render(false);
  expect(introShown()).toBe(true);
  // 지원자 차례가 오면 토론방이 열린다
  render(true);
  expect(introShown()).toBe(false);
  // 차례 유지 시간이 지나 발언권이 동료에게 넘어가도 그대로 열려 있다
  render(false);
  expect(introShown()).toBe(false);
});
