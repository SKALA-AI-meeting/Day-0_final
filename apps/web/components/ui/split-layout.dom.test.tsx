// @vitest-environment happy-dom
// 고정 크기 칸(px)은 늘거나 줄지 않고 옆 경계를 끌 수 없으며(간격은 둔다), 숨어 있던 칸이 생기면 잠깐 떠오른다.
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test } from "vitest";

import { SplitLayout, type SplitNode } from "./split-layout";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const TREE: SplitNode = {
  kind: "split",
  id: "root",
  dir: "row",
  children: [
    { kind: "pane", id: "memo" },
    { kind: "pane", id: "main" },
    { kind: "pane", id: "side" },
  ],
};

test("고정 칸 옆 경계는 끌 수 없고, 가변 칸끼리만 끌 수 있으며, 새로 생긴 칸은 떠오른다", () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  const render = (side: number) =>
    root.render(
      <SplitLayout
        tree={TREE}
        weights={{ memo: 1, main: 2, side }}
        modes={{ memo: 272 }}
        panes={{ memo: "메모", main: "가운데", side: "오른쪽" }}
        onResize={() => {}}
        onReset={() => {}}
      />,
    );
  act(() => render(0));
  const memo = host.querySelector<HTMLElement>('[data-split-item="memo"]')!;
  expect(memo.style.flexBasis).toBe("272px");
  expect(memo.style.flexGrow).toBe("0");
  expect(host.querySelectorAll('[role="separator"]')).toHaveLength(0);
  act(() => render(1));
  // main|side 경계만 끌 수 있다
  expect(host.querySelectorAll('[role="separator"]')).toHaveLength(1);
  expect(host.querySelector('[data-split-item="side"]')?.classList.contains("split-enter")).toBe(
    true,
  );
  act(() => root.unmount());
});
