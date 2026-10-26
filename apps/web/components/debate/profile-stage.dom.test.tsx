// @vitest-environment happy-dom
// 동료 발언 자막은 늘 붙어 있는 화면 읽기 영역 하나로 알리고, 카드 아래 보이는 자막과 프로필 버튼 이름에는 섞지 않는다.
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test } from "vitest";

import { ProfileStage } from "./profile-stage";
import { displayedSeat, showRaisedHand } from "./voice/voice-room";
import type { StateSnapshot } from "./types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const snap = {
  participants: [
    { id: "p1", display_name: "강한결" },
    { id: "p2", display_name: "윤하람" },
    { id: "p3", display_name: "서지안" },
    { id: "candidate", display_name: "지원자" },
  ],
  messages: [],
  session: { phase: "keyword" },
} as unknown as StateSnapshot;

test("자막 영역은 발언 전후로 유지되고 내용만 바뀐다", () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const render = (caption: string) =>
    root.render(
      <ProfileStage
        snap={snap}
        speaking={caption ? ["p1"] : []}
        caption={caption}
        intro={false}
        onSkipIntro={() => {}}
      />,
    );
  act(() => render(""));
  const live = host.querySelector('[data-testid="caption-live"]')!;
  expect(live.getAttribute("aria-live")).toBe("polite");
  expect(live.textContent).toBe("");
  act(() => render("물이 먼저예요."));
  expect(host.querySelector('[data-testid="caption-live"]')).toBe(live);
  expect(live.textContent).toBe("강한결: 물이 먼저예요.");
  // 보이는 자막은 버튼 밖 읽기 영역과 겹치지 않게 숨기고, 버튼 안에는 live 영역이 없다
  const trigger = host.querySelector('[aria-label="강한결 프로필 보기 · 발언 중"]')!;
  expect(trigger.textContent).toContain("발언 중");
  expect(trigger.querySelector("[aria-live]")).toBeNull();
  const visible = [...trigger.querySelectorAll("span")].find(
    (el) => el.textContent === "물이 먼저예요.",
  )!;
  expect(visible.getAttribute("aria-hidden")).toBe("true");
  act(() => root.unmount());
  host.remove();
});

test("상태별 큰 칸은 실제 재생 동료와 마지막 동료만 기억한다", () => {
  expect(displayedSeat(undefined, null, null)).toBeNull();
  expect(displayedSeat("AI_GENERATING", null, null)).toBeNull();
  expect(displayedSeat("AI_GENERATING", null, "p1")).toBe("p1");
  expect(displayedSeat("AI_SPEAKING", "p2", "p1")).toBe("p2");
  expect(displayedSeat("USER_READY", null, "p2")).toBe("p2");
  for (const state of ["USER_SPEAKING", "TRANSCRIBING", "PROCESSING"])
    expect(displayedSeat(state, null, "p2")).toBe("candidate");
  expect(displayedSeat("AI_GENERATING", null, "p2", true)).toBe("candidate");
  expect(displayedSeat("AI_GENERATING", null, "p2")).toBe("p2");
});

test("처음 네 칸은 균등하고, 방금 한 말은 큰 칸에만 흐리게 남긴다", () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const render = (featuredSeat: "p2" | null, recentCaption = "") =>
    root.render(
      <ProfileStage
        snap={snap}
        speaking={[]}
        intro={false}
        onSkipIntro={() => {}}
        fill
        featuredSeat={featuredSeat}
        recentCaption={recentCaption}
      />,
    );
  act(() => render(null));
  expect(host.querySelectorAll("[data-featured]")).toHaveLength(0);
  expect(host.querySelector("ul")?.className).toContain("grid-cols-2");
  act(() => render("p2", "물이 먼저예요."));
  expect(host.querySelector('[data-seat="p2"]')?.textContent).toContain("물이 먼저예요.");
  expect(host.querySelector('[data-seat="p2"] .text-muted.opacity-65')).not.toBeNull();
  expect(host.querySelector('[data-testid="caption-live"]')?.textContent).toBe("");
  act(() => render("p2"));
  expect(host.textContent).not.toContain("물이 먼저예요.");
  act(() => root.unmount());
  host.remove();
});

test("발표자 위치를 바꿔도 프로필 칸을 유지하고 손든 지원자를 표시한다", () => {
  expect(showRaisedHand({ raised: true }, "hand")).toBe(true);
  expect(showRaisedHand({ raised: true }, "forced")).toBe(false);
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const render = (featuredSeat: "p1" | "p2" | "candidate", hand: boolean) =>
    root.render(
      <ProfileStage
        snap={snap}
        speaking={[]}
        intro={false}
        onSkipIntro={() => {}}
        fill
        featuredSeat={featuredSeat}
        hand={hand}
      />,
    );
  act(() => render("p1", false));
  const first = host.querySelector('[data-seat="p1"]')!;
  const candidate = host.querySelector('[data-seat="candidate"]')!;
  expect(first.getAttribute("data-featured")).toBe("true");
  act(() => render("p2", true));
  expect(host.querySelector('[data-seat="p1"]')).toBe(first);
  expect(host.querySelector('[data-seat="candidate"]')).toBe(candidate);
  expect(host.querySelector('[data-seat="p2"]')!.getAttribute("data-featured")).toBe("true");
  expect(candidate.textContent).toContain("손 듦");
  expect(candidate.querySelector("button")?.getAttribute("aria-label")).toContain("손 듦");
  act(() => render("candidate", false));
  expect(candidate.getAttribute("data-featured")).toBe("true");
  expect(candidate.textContent).not.toContain("손 듦");
  act(() => root.unmount());
  host.remove();
});
