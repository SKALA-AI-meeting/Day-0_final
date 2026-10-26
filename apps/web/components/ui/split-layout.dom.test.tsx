// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test } from "vitest";

import { SplitLayout, useSplitLayout, type SplitNode } from "./split-layout";

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
const PRESET = { memo: 1, main: 2, side: 1 };
const MODES = { memo: { px: 272 } };

function Layout({ side = 1 }: { side?: number }) {
  const layout = useSplitLayout(TREE, { ...PRESET, side }, "talk", MODES);
  return (
    <SplitLayout
      tree={TREE}
      weights={layout.weights}
      modes={layout.modes}
      panes={{ memo: "메모", main: "가운데", side: "오른쪽" }}
      onResize={layout.onResize}
      onReset={layout.onReset}
    />
  );
}

test("{px} 칸 옆 경계를 끌 수 있고 숨어 있던 칸이 생기면 떠오른다", () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  act(() => root.render(<Layout side={0} />));
  const memo = host.querySelector<HTMLElement>('[data-split-item="memo"]')!;
  expect(memo.style.flexBasis).toBe("272px");
  expect(memo.style.flexGrow).toBe("0");
  expect(host.querySelectorAll('[role="separator"]')).toHaveLength(1);
  act(() => root.render(<Layout />));
  expect(host.querySelectorAll('[role="separator"]')).toHaveLength(2);
  expect(host.querySelector('[data-split-item="side"]')?.classList.contains("split-enter")).toBe(
    true,
  );
  act(() => root.unmount());
});

test("{px} 모드에서 다른 경계를 끌거나 키보드로 조절해도 왼쪽 칸의 실제 폭을 유지한다", () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  act(() => root.render(<Layout />));
  const sizes: Record<string, number> = { memo: 272, main: 400, side: 200 };
  for (const [id, width] of Object.entries(sizes)) {
    const item = host.querySelector<HTMLElement>(`[data-split-item="${id}"]`)!;
    item.getBoundingClientRect = () => ({ width, height: 400 }) as DOMRect;
  }
  const gutters = host.querySelectorAll<HTMLElement>('[role="separator"]');
  const target = gutters[1];
  target.setPointerCapture = () => {};
  act(() =>
    target.dispatchEvent(
      new PointerEvent("pointerdown", { bubbles: true, button: 0, pointerId: 1, clientX: 0 }),
    ),
  );
  act(() =>
    target.dispatchEvent(
      new PointerEvent("pointermove", { bubbles: true, pointerId: 1, clientX: 30 }),
    ),
  );
  const memo = host.querySelector<HTMLElement>('[data-split-item="memo"]')!;
  const main = host.querySelector<HTMLElement>('[data-split-item="main"]')!;
  const side = host.querySelector<HTMLElement>('[data-split-item="side"]')!;
  const pxPerWeight = (272 + 400 + 200) / 4;
  expect(memo.dataset.mode).toBeUndefined();
  expect(Number(memo.style.flexGrow) * pxPerWeight).toBeCloseTo(272);
  expect(Number(main.style.flexGrow) * pxPerWeight).toBeCloseTo(430);
  expect(Number(side.style.flexGrow) * pxPerWeight).toBeCloseTo(170);
  act(() => target.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, pointerId: 1 })));

  act(() => target.dispatchEvent(new MouseEvent("dblclick", { bubbles: true })));
  expect(memo.dataset.mode).toBe("px");
  act(() =>
    target.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "ArrowRight" })),
  );
  expect(memo.dataset.mode).toBeUndefined();
  expect(Number(memo.style.flexGrow) * pxPerWeight).toBeCloseTo(272);
  act(() => root.unmount());
});
