// @vitest-environment happy-dom
// 프로필 상태 표시: 심볼과 글자를 함께 두고(색만으로 구분하지 않음), 카드에는 상태 이름을 data-status로 단다.
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test } from "vitest";

import { ProfileStage } from "../profile-stage";
import type { StateSnapshot } from "../types";
import { SeatStatusBadge, statusLabel, type SeatStatus } from "./status-symbols";

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

test("상태 표시는 네 가지이고 각각 심볼과 글자가 함께 보인다", () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  const all: SeatStatus[] = ["speaking", "preparing", "hand", "myturn"];
  act(() =>
    root.render(
      <>
        {all.map((s) => (
          <SeatStatusBadge key={s} status={s} />
        ))}
      </>,
    ),
  );
  const badges = [...host.querySelectorAll("[data-status]")];
  expect(badges.map((b) => b.textContent)).toEqual(all.map(statusLabel));
  expect(badges.every((b) => b.querySelector("svg"))).toBe(true);
  act(() => root.unmount());
});

test("발표자 보기에서 자리별 상태가 카드에 붙고, 상태가 없으면 말하는 자리만 표시한다", () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  act(() =>
    root.render(
      <ProfileStage
        snap={snap}
        speaking={["p1"]}
        status={{ p2: "preparing", candidate: "hand" }}
        fill
        featuredSeat="p1"
        intro={false}
        onSkipIntro={() => {}}
      />,
    ),
  );
  const status = (name: string) =>
    host.querySelector(`[aria-label^="${name} 프로필 보기"]`)?.getAttribute("data-status");
  expect(status("강한결")).toBe("speaking");
  expect(status("윤하람")).toBe("preparing");
  expect(status("서지안")).toBeNull();
  expect(status("지원자")).toBe("hand");
  expect(
    host.querySelector('[data-seat="candidate"] [data-status="hand"]')?.getAttribute("aria-label"),
  ).toBe("지원자 프로필 보기 · 손 듦");
  expect(host.querySelector('[data-seat="p1"]')?.getAttribute("data-featured")).toBe("true");
  act(() =>
    root.render(
      <ProfileStage
        snap={snap}
        speaking={[]}
        status={{ candidate: "speaking" }}
        candidateTalking
        fill
        featuredSeat="candidate"
        intro={false}
        onSkipIntro={() => {}}
      />,
    ),
  );
  expect(status("지원자")).toBe("speaking");
  expect(host.querySelector('[data-seat="candidate"]')?.textContent).toContain("발언 중");
  act(() => root.unmount());
});
