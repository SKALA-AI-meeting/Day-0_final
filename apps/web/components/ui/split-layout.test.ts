import { expect, test } from "vitest";

import {
  dragPair,
  resolveLayout,
  resolveWeights,
  visibleKey,
  type SplitNode,
} from "./split-layout";

const TREE: SplitNode = {
  kind: "split",
  id: "root",
  dir: "row",
  children: [
    { kind: "pane", id: "a" },
    { kind: "pane", id: "b" },
    { kind: "pane", id: "c" },
  ],
};
const ROOT = TREE as Extract<SplitNode, { kind: "split" }>;

test("사용자 비율은 보이는 칸 구성과 구간(epoch)이 같을 때만 기본 비율을 덮는다", () => {
  const preset = { a: 1, b: 2, c: 0 };
  const overrides = { root: { key: visibleKey(ROOT, preset, "talk"), weights: { a: 2, b: 1 } } };
  expect(resolveWeights(TREE, preset, overrides, "talk")).toMatchObject({ a: 2, b: 1, c: 0 });
  // 칸이 새로 생기면 기본 비율로
  expect(resolveWeights(TREE, { a: 1, b: 2, c: 1 }, overrides, "talk")).toMatchObject({
    a: 1,
    b: 2,
    c: 1,
  });
  // 구간이 바뀌어도 기본 비율로
  expect(resolveWeights(TREE, preset, overrides, "proposal")).toMatchObject({ a: 1, b: 2 });
});

test("경계를 끌면 두 칸의 합은 그대로이고 최소 크기 밑으로 줄지 않는다", () => {
  // 비율 1 = 100px, 두 칸 300px
  expect(dragPair([1, 2], 50, 100, [80, 80])).toEqual([1.5, 1.5]);
  const [a, b] = dragPair([1, 2], 500, 100, [80, 80]);
  expect(a + b).toBeCloseTo(3);
  expect(b * 100).toBeCloseTo(80);
  expect(dragPair([1, 2], -500, 100, [80, 80])[0] * 100).toBeCloseTo(80);
});

test("사용자가 끈 분할의 칸은 fit·square 대신 비율로 나뉜다", () => {
  const preset = { a: 1, b: 1, c: 0 };
  const modes = { a: "fit" as const };
  expect(resolveLayout(TREE, preset, {}, "", modes).modes).toEqual({ a: "fit" });
  const overrides = { root: { key: visibleKey(ROOT, preset), weights: { a: 0.4, b: 1.6 } } };
  expect(resolveLayout(TREE, preset, overrides, "", modes)).toEqual({
    weights: { a: 0.4, b: 1.6, c: 0 },
    modes: {},
  });
});
