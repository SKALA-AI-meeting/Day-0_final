"use client";

// 음성 회의 프로필 칸의 자리 이동 애니메이션(FLIP: 처음 자리 → 마지막 자리 → 되돌린 변환 → 재생).
// 칸 DOM은 그대로 두고 배치(2x2·발표자 보기·큰 칸 주인)가 바뀐 직후 transform(translate+scale)만 움직인다.
// 칸 상자(li)는 비율이 바뀌며 늘고 줄지만, 안의 내용([data-tile-content])은 거꾸로 늘려 찌그러지지 않게 한다.
// 재생 중에 또 바뀌면 지금 보이는 자리에서 새 자리로 이어 간다(쌓이지 않음). 움직임 줄이기면 바로 바뀐다.
import { useLayoutEffect, useRef, type RefObject } from "react";

/** 이동 시간(ms). design-system.md 모션: 자리 이동 280ms ease-out */
export const TILE_MOVE_MS = 280;
/** 키프레임 표본 수. 내용 역스케일이 상자 스케일과 같은 곡선을 따르도록 직접 나눠 찍는다 */
const STEPS = 14;

/** 목록(ul) 왼쪽 위 기준 칸 상자(px) */
export type Box = { x: number; y: number; w: number; h: number };

/** ease-out(quart). 토큰 --ease-out(cubic-bezier(0.23,1,0.32,1))과 비슷한 감속 */
export const easeOut = (t: number) => 1 - (1 - t) ** 4;

/** 마지막 자리(to)에 놓인 칸을 처음 자리(from)처럼 보이게 하는 변환. transform-origin은 왼쪽 위 */
export function invert(from: Box, to: Box) {
  return {
    dx: from.x - to.x,
    dy: from.y - to.y,
    sx: to.w > 0 ? from.w / to.w : 1,
    sy: to.h > 0 ? from.h / to.h : 1,
  };
}

/** 움직일 필요가 없을 만큼 작은 차이(1px·1%)면 건너뛴다 */
export function negligible(inv: ReturnType<typeof invert>) {
  return (
    Math.abs(inv.dx) < 1 &&
    Math.abs(inv.dy) < 1 &&
    Math.abs(inv.sx - 1) < 0.01 &&
    Math.abs(inv.sy - 1) < 0.01
  );
}

/**
 * 칸 상자와 안 내용의 키프레임. 상자는 (dx,dy,sx,sy) → 제자리로,
 * 내용은 상자 스케일을 지우고 작은 쪽 비율(u)로만 균일하게 늘어 글자·아바타가 찌그러지지 않는다.
 */
export function flipFrames(inv: ReturnType<typeof invert>, steps = STEPS) {
  const tile: Keyframe[] = [];
  const content: Keyframe[] = [];
  for (let i = 0; i <= steps; i++) {
    const p = easeOut(i / steps);
    const sx = inv.sx + (1 - inv.sx) * p;
    const sy = inv.sy + (1 - inv.sy) * p;
    const u = Math.min(sx, sy);
    const offset = i / steps;
    tile.push({
      offset,
      transform: `translate(${inv.dx * (1 - p)}px, ${inv.dy * (1 - p)}px) scale(${sx}, ${sy})`,
    });
    content.push({ offset, transform: `scale(${u / sx}, ${u / sy})` });
  }
  return { tile, content };
}

/** 지금 그려진(애니메이션 중이면 그 순간의) 자리. 배치 자리(layout)에 계산된 transform을 얹는다 */
function visualBox(el: HTMLElement, layout: Box): Box {
  const value = getComputedStyle(el).transform;
  if (!value || value === "none" || typeof DOMMatrixReadOnly === "undefined") return layout;
  const m = new DOMMatrixReadOnly(value);
  return { x: layout.x + m.e, y: layout.y + m.f, w: layout.w * m.a, h: layout.h * m.d };
}

function reducedMotion() {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * 목록 안 칸([data-seat])의 자리 이동을 layoutKey가 바뀔 때마다 FLIP으로 잇는다.
 * 자리는 목록 크기에 대한 비율로 기억해, 그사이 목록 크기가 바뀌어도(창 크기 조절·영역 전환) 처음 자리가 어긋나지 않는다.
 */
export function useTileFlip(listRef: RefObject<HTMLElement | null>, layoutKey: string) {
  // 칸별 마지막 배치 자리(목록 크기 비율)
  const boxes = useRef(new Map<string, Box>());
  const running = useRef(new Map<string, Animation[]>());

  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const frame = list.getBoundingClientRect();
    // 숨은 목록(인사 화면 등)은 재지 않는다. 다음에 보일 때 자리를 새로 기억한다
    if (frame.width === 0 || frame.height === 0) {
      boxes.current.clear();
      return;
    }
    const tiles = Array.from(list.querySelectorAll<HTMLElement>(":scope > [data-seat]"));
    const toPx = (b: Box): Box => ({
      x: b.x * frame.width,
      y: b.y * frame.height,
      w: b.w * frame.width,
      h: b.h * frame.height,
    });
    // 1) 처음 자리: 지난 배치 자리에 지금 재생 중인 변환을 얹은, 눈에 보이는 자리
    const starts = new Map<string, Box>();
    for (const el of tiles) {
      const prev = boxes.current.get(el.dataset.seat ?? "");
      if (prev) starts.set(el.dataset.seat!, visualBox(el, toPx(prev)));
    }
    // 2) 재생 중인 이동을 멈추고 새 배치 자리를 잰다
    for (const list of running.current.values()) list.forEach((a) => a.cancel());
    running.current.clear();
    const finals = new Map<string, Box>();
    for (const el of tiles) {
      const r = el.getBoundingClientRect();
      const box = { x: r.left - frame.left, y: r.top - frame.top, w: r.width, h: r.height };
      finals.set(el.dataset.seat ?? "", box);
      boxes.current.set(el.dataset.seat ?? "", {
        x: box.x / frame.width,
        y: box.y / frame.height,
        w: box.w / frame.width,
        h: box.h / frame.height,
      });
    }
    if (reducedMotion()) return;
    // 3) 되돌린 변환에서 제자리로. 커지는 칸이 위, 작아지는 칸이 그 아래, 옆으로만 밀리는 칸은 맨 아래
    for (const el of tiles) {
      const seat = el.dataset.seat ?? "";
      const from = starts.get(seat);
      const to = finals.get(seat);
      if (!from || !to || typeof el.animate !== "function") continue;
      const inv = invert(from, to);
      if (negligible(inv)) continue;
      const growth = to.w * to.h - from.w * from.h;
      el.style.zIndex = growth > 1 ? "3" : growth < -1 ? "2" : "1";
      el.style.transformOrigin = "0 0";
      const { tile, content } = flipFrames(inv);
      const options: KeyframeAnimationOptions = { duration: TILE_MOVE_MS, easing: "linear" };
      const animations = [el.animate(tile, options)];
      const inner = el.querySelector<HTMLElement>("[data-tile-content]");
      if (inner) animations.push(inner.animate(content, options));
      running.current.set(seat, animations);
      animations[0].finished
        .then(() => {
          if (running.current.get(seat) !== animations) return;
          running.current.delete(seat);
          el.style.zIndex = "";
        })
        .catch(() => {
          // 다음 전환이 취소한 경우: 그 전환이 z-index를 다시 정한다
        });
    }
  }, [listRef, layoutKey]);

  // 화면을 떠나면 남은 이동을 지운다
  useLayoutEffect(() => {
    const map = running.current;
    return () => {
      for (const list of map.values()) list.forEach((a) => a.cancel());
      map.clear();
    };
  }, []);
}
