// @vitest-environment happy-dom
// 최근 발언 칸: 오래된 것부터 쌓고 가장 새 발언을 크게, 재생 중인 발언에는 커서를 단다. 위로 갈수록 흐려진다.
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test } from "vitest";

import type { Participant } from "../types";
import { RecentTalk } from "./recent-talk";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const people = [
  { id: "p1", display_name: "강한결" },
  { id: "candidate", display_name: "지원자" },
] as Participant[];

test("가장 새 발언이 맨 아래에 살짝 강조되고, 재생 중인 발언에는 커서가 붙는다", () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const messages = [
    { id: "m1", sender: "candidate", content: "저는 물이 좋아요." },
    { id: "m2", sender: "p1", content: "물이 먼저예요." },
  ];
  act(() => root.render(<RecentTalk messages={messages} participants={people} liveId="m2" />));
  const rows = [...host.querySelectorAll("li[data-age]")];
  expect(rows.map((r) => r.getAttribute("data-age"))).toEqual(["1", "0"]);
  expect(rows[0].textContent).toContain("나");
  expect(rows[1].textContent).toContain("강한결");
  expect(rows[1].classList.contains("talk-new")).toBe(true);
  expect(rows[1].querySelector(".talk-live")?.textContent).toBe("물이 먼저예요.");
  expect(host.textContent).toContain("지금 말하는 중");
  act(() => root.unmount());
  host.remove();
});

test("모든 발언은 같은 크기이고 나이에 따라 흐려지지 않는다(흐림은 보이는 창 가장자리만)", () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const messages = ["m1", "m2", "m3", "m4"].map((id) => ({ id, sender: "p2", content: id }));
  act(() => root.render(<RecentTalk messages={messages} participants={people} liveId={null} />));
  const rows = [...host.querySelectorAll<HTMLLIElement>("li[data-age]")];
  expect(rows.every((r) => !r.style.opacity)).toBe(true);
  expect(rows.every((r) => r.querySelector("p")?.classList.contains("text-callout"))).toBe(true);
  expect(rows.filter((r) => r.classList.contains("talk-new"))).toHaveLength(1);
  act(() => root.unmount());
  host.remove();
});
