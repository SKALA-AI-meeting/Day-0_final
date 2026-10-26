// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test } from "vitest";

import { ConsentScreen } from "./link-screens";
import type { Entry } from "./types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

test("다섯 항목을 순서대로 확인하기 전에는 동의 체크박스가 비활성이다", () => {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const entry: Entry = {
    session_id: "session-1",
    company_name: "회사",
    duration_minutes: 15,
    status: "invited",
    default_display_name: "지원자",
    elements_names: ["경청"],
    entry_notice: "",
    contact_email: "help@example.com",
  };
  act(() => root.render(<ConsentScreen entry={entry} onConsent={() => {}} />));

  const checkbox = () => container.querySelector<HTMLInputElement>("#consent-agreement")!;
  const ids = ["format", "record", "evaluation", "assistant", "not-made"];
  const titles = ["형식", "기록되는 것", "평가 방식", "AI 어시스턴트", "만들지 않는 것"];
  const confirm = () =>
    [...container.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent?.trim() === "확인했어요",
    )!;
  expect(checkbox().disabled).toBe(true);
  expect(checkbox().getAttribute("aria-describedby")).toBe("consent-agreement-help");
  expect(container.textContent).toContain("항목을 모두 확인하면 동의할 수 있어요");

  for (const [i, id] of ids.entries()) {
    const heading = container.querySelector<HTMLButtonElement>(`#consent-item-${id}`)!;
    expect(heading.textContent).toContain(titles[i]);
    expect(heading.getAttribute("aria-expanded")).toBe("true");
    act(() => confirm().click());
    expect(checkbox().disabled).toBe(i < 4);
    expect(document.activeElement).toBe(
      i < 4 ? container.querySelector(`#consent-item-${ids[i + 1]}`) : checkbox(),
    );
  }
  expect(checkbox().getAttribute("aria-describedby")).toBeNull();
  act(() => root.unmount());
  container.remove();
});
