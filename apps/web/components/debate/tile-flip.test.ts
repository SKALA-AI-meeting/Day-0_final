// 칸 자리 이동(FLIP): 처음 프레임은 처음 자리, 마지막 프레임은 제자리이고 내용은 균일하게만 늘고 준다.
import { expect, test } from "vitest";

import { flipFrames, invert, negligible } from "./tile-flip";

const scaleOf = (transform: unknown) =>
  String(transform)
    .match(/scale\(([^,]+), ([^)]+)\)/)!
    .slice(1)
    .map(Number);

test("작은 칸 자리에서 큰 칸 자리로 커지며 움직이고 끝은 제자리다", () => {
  const small = { x: 0, y: 300, w: 180, h: 100 };
  const big = { x: 0, y: 0, w: 560, h: 280 };
  const inv = invert(small, big);
  expect(inv).toEqual({ dx: 0, dy: 300, sx: 180 / 560, sy: 100 / 280 });
  expect(negligible(inv)).toBe(false);
  expect(negligible(invert(big, big))).toBe(true);
  const { tile, content } = flipFrames(inv);
  expect(tile[0].transform).toBe(`translate(0px, 300px) scale(${inv.sx}, ${inv.sy})`);
  expect(tile.at(-1)!.transform).toBe("translate(0px, 0px) scale(1, 1)");
  // 상자 스케일 × 내용 스케일은 가로세로가 같다(글자·아바타가 찌그러지지 않음)
  for (let i = 0; i < tile.length; i++) {
    const [sx, sy] = scaleOf(tile[i].transform);
    const [cx, cy] = scaleOf(content[i].transform);
    expect(sx * cx).toBeCloseTo(sy * cy, 6);
  }
  expect(content.at(-1)!.transform).toBe("scale(1, 1)");
});
