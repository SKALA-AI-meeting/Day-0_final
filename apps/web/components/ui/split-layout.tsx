"use client";

// 분할 레이아웃: 행·열 분할 트리의 칸을 비율(weight)로 나눈다. 비율이 바뀌면 칸이 부드럽게 늘고 줄며,
// 이웃한 두 칸 사이 경계를 끌거나(포인터) 화살표 키로 크기를 바꾼다. weight 0은 숨김(접힘)이다.
// 트리(칸 배치)는 고정이고 화면 상태에 따라 weight만 바꾼다. 사용자가 바꾼 크기는 그 분할의
// 보이는 칸 구성이 같은 동안만 유지하고, 구성이 바뀌면(칸이 새로 생기거나 사라지면) 기본 비율로 돌아간다.
// 칸마다 크기 모드(mode)를 줄 수 있다: fit은 내용 높이만큼, square는 정사각형(폭 = 높이). 사용자가 그 분할의
// 경계를 끌면 그 분할 안의 칸은 모두 비율로 바뀐다.
import {
  Fragment,
  useCallback,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";

import { cn } from "./cn";

export type SplitNode =
  | { kind: "pane"; id: string; /** 끌어서 줄일 수 있는 최소 크기(px). 기본 160 */ minPx?: number }
  | { kind: "split"; id: string; dir: "row" | "col"; children: SplitNode[] };

/** 노드 id → 부모 분할 안의 비율. 없거나 0이면 숨김 */
export type Weights = Record<string, number>;

/** 분할 id → 사용자가 바꾼 비율과, 그때의 보이는 칸 구성(key) */
export type Overrides = Record<string, { key: string; weights: Weights }>;

/** 칸 id → 크기 모드. 없으면 비율(weight) */
export type PaneModes = Record<string, "fit" | "square">;

const DEFAULT_MIN_PX = 160;
/** 키보드로 한 번에 옮기는 양(두 칸 합의 비율) */
const KEY_STEP = 0.05;

/** 분할의 보이는 칸 구성(+ 구간 epoch). 이 값이 같을 때만 사용자 비율을 쓴다 */
export function visibleKey(
  split: Extract<SplitNode, { kind: "split" }>,
  weights: Weights,
  epoch = "",
) {
  const ids = split.children
    .filter((child) => (weights[child.id] ?? 0) > 0)
    .map((child) => child.id);
  return `${epoch}/${ids.join("|")}`;
}

/** 기본 비율(preset)에 구성이 맞는 사용자 비율을 덮은 최종 비율과 크기 모드 */
export function resolveLayout(
  tree: SplitNode,
  preset: Weights,
  overrides: Overrides,
  epoch = "",
  modes: PaneModes = {},
): { weights: Weights; modes: PaneModes } {
  const weights: Weights = { ...preset };
  const outModes: PaneModes = { ...modes };
  const walk = (node: SplitNode) => {
    if (node.kind === "pane") return;
    const saved = overrides[node.id];
    if (saved && saved.key === visibleKey(node, preset, epoch)) {
      for (const [id, weight] of Object.entries(saved.weights)) weights[id] = weight;
      // 사용자가 끈 분할은 비율로만 나눈다
      for (const child of node.children) delete outModes[child.id];
    }
    node.children.forEach(walk);
  };
  walk(tree);
  return { weights, modes: outModes };
}

/** resolveLayout의 비율만 */
export function resolveWeights(
  tree: SplitNode,
  preset: Weights,
  overrides: Overrides,
  epoch = "",
): Weights {
  return resolveLayout(tree, preset, overrides, epoch).weights;
}

/**
 * 경계를 delta px 옮긴 뒤 두 칸의 비율. 두 칸의 합은 그대로이고 각 칸은 최소 크기 밑으로 줄지 않는다.
 * pxPerWeight는 지금 두 칸의 실제 크기 합 / 비율 합.
 */
export function dragPair(
  [a, b]: [number, number],
  deltaPx: number,
  pxPerWeight: number,
  [minA, minB]: [number, number],
): [number, number] {
  const total = a + b;
  if (!(pxPerWeight > 0)) return [a, b];
  const lo = Math.min(minA / pxPerWeight, total / 2);
  const hi = Math.max(total - minB / pxPerWeight, total / 2);
  const next = Math.min(hi, Math.max(lo, a + deltaPx / pxPerWeight));
  return [next, total - next];
}

function minPx(node: SplitNode, dir: "row" | "col"): number {
  if (node.kind === "pane") return node.minPx ?? DEFAULT_MIN_PX;
  // 같은 방향 분할은 보이는 자식 최소의 합, 다른 방향은 가장 큰 자식 최소
  const mins = node.children.map((child) => minPx(child, dir));
  return node.dir === dir ? mins.reduce((s, v) => s + v, 0) : Math.max(...mins);
}

/**
 * 기본 비율이 바뀌어도 사용자 비율을 구성 단위로 지키는 훅.
 * epoch는 화면 구간(예: 인사·토론·기획안). 구간이 바뀌면 사용자 비율을 모두 버린다.
 */
export function useSplitLayout(
  tree: SplitNode,
  preset: Weights,
  epoch = "",
  presetModes?: PaneModes,
) {
  const [overrides, setOverrides] = useState<Overrides>({});
  const { weights, modes } = useMemo(
    () => resolveLayout(tree, preset, overrides, epoch, presetModes),
    [tree, preset, overrides, epoch, presetModes],
  );
  const onResize = useCallback(
    (split: Extract<SplitNode, { kind: "split" }>, next: Weights) => {
      setOverrides((prev) => ({
        ...prev,
        [split.id]: { key: visibleKey(split, preset, epoch), weights: next },
      }));
    },
    [preset, epoch],
  );
  const onReset = useCallback((splitId: string) => {
    setOverrides((prev) => {
      const next = { ...prev };
      delete next[splitId];
      return next;
    });
  }, []);
  return { weights, modes, onResize, onReset };
}

export type SplitLayoutProps = {
  tree: SplitNode;
  weights: Weights;
  modes?: PaneModes;
  /** pane id → 그 칸에 그릴 내용 */
  panes: Record<string, ReactNode>;
  onResize: (split: Extract<SplitNode, { kind: "split" }>, weights: Weights) => void;
  /** 경계를 두 번 누르면 그 분할을 기본 비율로 */
  onReset: (splitId: string) => void;
  className?: string;
};

export function SplitLayout({ tree, className, ...rest }: SplitLayoutProps) {
  return (
    <div className={cn("flex min-h-0 min-w-0", className)}>
      <Item node={tree} weight={1} {...rest} />
    </div>
  );
}

type ItemProps = Omit<SplitLayoutProps, "tree" | "className"> & { node: SplitNode; weight: number };

function itemStyle(hidden: boolean, weight: number, mode: PaneModes[string] | undefined) {
  if (hidden) return { flexGrow: 0, flexShrink: 1, flexBasis: 0 };
  // fit·square는 늘지 않고 내용(또는 폭)만큼 차지하며, 자리가 모자라면 줄어든다
  if (mode === "fit") return { flexGrow: 0, flexShrink: 1, flexBasis: "auto" };
  if (mode === "square")
    return { flexGrow: 0, flexShrink: 1, flexBasis: "auto", aspectRatio: "1 / 1" };
  return { flexGrow: weight, flexShrink: 1, flexBasis: 0 };
}

function Item({ node, weight, ...rest }: ItemProps) {
  const hidden = !(weight > 0);
  const mode = rest.modes?.[node.id];
  return (
    <div
      data-split-item={node.id}
      data-mode={(!hidden && mode) || undefined}
      // 숨긴 칸은 키보드·낭독에서 빠진다. 내용은 남겨 다시 보일 때 스크롤·입력이 그대로다
      inert={hidden}
      aria-hidden={hidden || undefined}
      style={itemStyle(hidden, weight, mode)}
      className={cn(
        "split-item flex min-h-0 min-w-0 overflow-hidden",
        hidden ? "pointer-events-none opacity-0" : "opacity-100",
      )}
    >
      {node.kind === "pane" ? (
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">{rest.panes[node.id]}</div>
      ) : (
        <Split node={node} {...rest} />
      )}
    </div>
  );
}

function Split({
  node,
  ...rest
}: Omit<ItemProps, "node" | "weight"> & { node: Extract<SplitNode, { kind: "split" }> }) {
  const { weights } = rest;
  const visible = node.children.map((child) => (weights[child.id] ?? 0) > 0);
  // 경계 i는 i번째 칸과 그다음 보이는 칸 사이. 둘 다 보일 때만 자리를 차지한다
  const nextVisible = (i: number) => visible.findIndex((v, j) => j > i && v);
  return (
    <div
      className={cn("flex min-h-0 min-w-0 flex-1", node.dir === "row" ? "flex-row" : "flex-col")}
    >
      {node.children.map((child, i) => {
        const j = nextVisible(i);
        const gutter = i < node.children.length - 1;
        return (
          <Fragment key={child.id}>
            <Item node={child} weight={weights[child.id] ?? 0} {...rest} />
            {gutter && (
              <Gutter
                split={node}
                a={child}
                b={j >= 0 ? node.children[j] : null}
                active={visible[i] && j >= 0}
                {...rest}
              />
            )}
          </Fragment>
        );
      })}
    </div>
  );
}

function Gutter({
  split,
  a,
  b,
  active,
  weights,
  onResize,
  onReset,
}: Omit<ItemProps, "node" | "weight" | "panes" | "modes"> & {
  split: Extract<SplitNode, { kind: "split" }>;
  a: SplitNode;
  b: SplitNode | null;
  active: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const row = split.dir === "row";
  const wa = weights[a.id] ?? 0;
  const wb = b ? (weights[b.id] ?? 0) : 0;

  const visibleWeights = (): Weights =>
    Object.fromEntries(
      split.children
        .filter((child) => (weights[child.id] ?? 0) > 0)
        .map((child) => [child.id, weights[child.id]]),
    );

  // 두 칸의 실제 크기(px). 경계 폭·중첩 분할·fit/square 모드와 상관없이 지금 보이는 대로다
  const pairPx = (): [number, number] => {
    const parent = ref.current?.parentElement;
    if (!parent || !b) return [0, 0];
    const size = (id: string) => {
      const el = parent.querySelector<HTMLElement>(`:scope > [data-split-item="${id}"]`);
      const rect = el?.getBoundingClientRect();
      return rect ? (row ? rect.width : rect.height) : 0;
    };
    return [size(a.id), size(b.id)];
  };

  // 끌기 시작 비율은 지금 보이는 크기 비로 정한다(fit/square 칸도 그 크기에서 이어서 움직인다)
  const startFromPx = (): { start: [number, number]; pxPerWeight: number } => {
    const [pa, pb] = pairPx();
    const total = wa + wb;
    const sum = pa + pb;
    if (!(sum > 0)) return { start: [wa, wb], pxPerWeight: 0 };
    return { start: [(total * pa) / sum, (total * pb) / sum], pxPerWeight: sum / total };
  };

  const apply = (start: [number, number], deltaPx: number, pxPerWeight: number) => {
    if (!b) return;
    const [na, nb] = dragPair(start, deltaPx, pxPerWeight, [
      minPx(a, split.dir),
      minPx(b, split.dir),
    ]);
    onResize(split, { ...visibleWeights(), [a.id]: na, [b.id]: nb });
  };

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!active || !b || e.button !== 0) return;
    e.preventDefault();
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    const origin = row ? e.clientX : e.clientY;
    const { start, pxPerWeight } = startFromPx();
    // 끄는 동안은 칸 전환 애니메이션을 끈다(globals.css [data-split-dragging])
    document.documentElement.dataset.splitDragging = row ? "row" : "col";
    const move = (ev: PointerEvent) =>
      apply(start, (row ? ev.clientX : ev.clientY) - origin, pxPerWeight);
    const up = () => {
      delete document.documentElement.dataset.splitDragging;
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!active || !b) return;
    const back = row ? "ArrowLeft" : "ArrowUp";
    const fwd = row ? "ArrowRight" : "ArrowDown";
    if (e.key !== back && e.key !== fwd) return;
    e.preventDefault();
    const { start, pxPerWeight } = startFromPx();
    apply(start, (e.key === fwd ? 1 : -1) * KEY_STEP * pxPerWeight * (wa + wb), pxPerWeight);
  };

  const percent = active ? Math.round((wa / (wa + wb)) * 100) : undefined;
  return (
    <div
      ref={ref}
      role={active ? "separator" : undefined}
      aria-orientation={row ? "vertical" : "horizontal"}
      aria-label={active ? "영역 크기 조절" : undefined}
      aria-valuenow={percent}
      aria-valuemin={active ? 0 : undefined}
      aria-valuemax={active ? 100 : undefined}
      tabIndex={active ? 0 : -1}
      inert={!active}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={() => active && onReset(split.id)}
      data-active={active || undefined}
      className={cn(
        "split-gutter group relative flex shrink-0 touch-none items-center justify-center outline-none",
        row ? "cursor-col-resize" : "cursor-row-resize",
        active ? (row ? "w-3" : "h-3") : row ? "w-0" : "h-0",
      )}
    >
      {/* 평소에는 보이지 않고, 올리거나 끌거나 포커스하면 가는 선이 나타난다 */}
      <span
        aria-hidden
        className={cn(
          "rounded-full bg-accent opacity-0 transition-opacity duration-150 group-hover:opacity-60 group-focus-visible:opacity-100 group-active:opacity-100",
          row ? "h-10 w-1" : "h-1 w-10",
        )}
      />
    </div>
  );
}
