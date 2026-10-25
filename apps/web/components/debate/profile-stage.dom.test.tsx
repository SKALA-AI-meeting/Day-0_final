// @vitest-environment happy-dom
// 동료 발언 자막은 늘 붙어 있는 화면 읽기 영역 하나로 알리고, 카드 아래 보이는 자막과 프로필 버튼 이름에는 섞지 않는다.
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test } from "vitest";

import { ProfileStage } from "./profile-stage";
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
  const trigger = host.querySelector('[aria-label="강한결 프로필 보기"]')!;
  expect(trigger.querySelector("[aria-live]")).toBeNull();
  const visible = [...trigger.querySelectorAll("span")].find(
    (el) => el.textContent === "물이 먼저예요.",
  )!;
  expect(visible.getAttribute("aria-hidden")).toBe("true");
  act(() => root.unmount());
  host.remove();
});
