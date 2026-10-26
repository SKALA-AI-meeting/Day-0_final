"use client";

// 화면 전환·등장 도구. 토론 화면과 같은 결로 다른 화면도 정보를 순서대로 들여보낸다(globals.css .reveal 등).
// 움직임 줄이기를 켜면 CSS가 움직임을 끄고, 순차 등장(useSequence)은 처음부터 모두 보인다.
import {
  Children,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type ElementType,
  type ReactNode,
} from "react";

import { cn } from "./cn";

const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";

/**
 * 움직임 줄이기 설정. 서버 렌더에서는 false. matchMedia가 없는 환경(테스트 DOM)은 줄인 것으로 보아
 * 순차 등장·숫자 차오름 없이 처음부터 다 보인다
 */
export function useReducedMotion() {
  return useSyncExternalStore(
    (notify) => {
      if (typeof window.matchMedia !== "function") return () => {};
      const query = window.matchMedia(REDUCED_QUERY);
      query.addEventListener("change", notify);
      return () => query.removeEventListener("change", notify);
    },
    () => typeof window.matchMedia !== "function" || window.matchMedia(REDUCED_QUERY).matches,
    () => false,
  );
}

function delayStyle(ms: number, style?: CSSProperties): CSSProperties {
  return { ...style, ["--reveal-delay" as string]: `${ms}ms` };
}

/** 떠오르며 들어온다. delay(ms) 또는 index × step으로 늦춘다 */
export function Reveal({
  as: Tag = "div",
  delay,
  index = 0,
  step = 80,
  kind = "reveal",
  className,
  style,
  children,
  ...rest
}: {
  as?: ElementType;
  delay?: number;
  index?: number;
  step?: number;
  /** reveal: 떠오름, pop: 떠오르며 테두리가 한 번 빛남(새로 생긴 카드) */
  kind?: "reveal" | "pop";
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
} & Record<string, unknown>) {
  return (
    <Tag
      className={cn(kind === "pop" ? "pop-in" : "reveal", className)}
      style={delayStyle(delay ?? index * step, style)}
      {...rest}
    >
      {children}
    </Tag>
  );
}

/** 자식들을 차례로 떠오르게 한다(start ms부터 step ms 간격) */
export function Stagger({
  children,
  start = 0,
  step = 80,
  className,
  as = "div",
  itemClassName,
}: {
  children: ReactNode;
  start?: number;
  step?: number;
  className?: string;
  as?: ElementType;
  itemClassName?: string;
}) {
  const Tag = as;
  return (
    <Tag className={className}>
      {Children.toArray(children)
        .filter(Boolean)
        .map((child, i) => (
          <Reveal key={i} delay={start + i * step} className={itemClassName}>
            {child}
          </Reveal>
        ))}
    </Tag>
  );
}

/**
 * count개 항목을 interval ms마다 하나씩 보인다. 반환값은 지금 보일 개수.
 * key가 바뀌면 처음부터 다시 센다. 움직임 줄이기면 바로 전부다.
 */
export function useSequence(count: number, interval = 420, start = 200, key: unknown = null) {
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(0);
  const [prevKey, setPrevKey] = useState(key);
  if (prevKey !== key) {
    setPrevKey(key);
    setShown(0);
  }
  useEffect(() => {
    if (reduced || shown >= count) return;
    const timer = setTimeout(() => setShown((n) => n + 1), shown === 0 ? start : interval);
    return () => clearTimeout(timer);
  }, [reduced, shown, count, interval, start]);
  return reduced ? count : Math.min(shown, count);
}

/** 화면(단계) 전환. id가 바뀌면 방향에 맞게 새 화면이 미끄러져 들어온다 */
export function ScreenTransition({
  id,
  order,
  className,
  children,
}: {
  id: string;
  /** 단계 순서. 이전보다 크면 앞으로(오른쪽에서), 작으면 뒤로(왼쪽에서) */
  order?: number;
  className?: string;
  children: ReactNode;
}) {
  const [prev, setPrev] = useState({ id, order: order ?? 0 });
  const [dir, setDir] = useState<"forward" | "back">("forward");
  if (prev.id !== id) {
    setPrev({ id, order: order ?? 0 });
    setDir((order ?? 0) < prev.order ? "back" : "forward");
  }
  return (
    <div key={id} className={cn(dir === "back" ? "screen-back" : "screen-forward", className)}>
      {children}
    </div>
  );
}

/** 높이를 부드럽게 접고 편다. 닫혀 있으면 키보드·낭독에서 빠진다 */
export function Collapse({
  open,
  className,
  children,
}: {
  open: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("fold", className)} data-open={open} inert={!open} aria-hidden={!open}>
      <div>{children}</div>
    </div>
  );
}

/** 그려지는 체크 표시 */
export function DrawnCheck({ size = 14, delay = 0 }: { size?: number; delay?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      aria-hidden
      className="check-draw"
      style={delayStyle(delay)}
    >
      <path
        d="M5 12.5l4.5 4.5L19 7.5"
        stroke="currentColor"
        strokeWidth={3}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export type Checkpoint = { id: string; label: string };

/**
 * 진행 체크포인트(가로). 지난 단계는 체크가 그려지고, 지금 단계는 숨 쉬듯 빛나며, 사이 선이 차오른다.
 * current는 지금 단계 id. 모두 끝났으면 done.
 */
export function Checkpoints({
  steps,
  current,
  done = false,
  className,
  label = "진행 단계",
}: {
  steps: Checkpoint[];
  current: string;
  done?: boolean;
  className?: string;
  label?: string;
}) {
  const at = done
    ? steps.length
    : Math.max(
        0,
        steps.findIndex((s) => s.id === current),
      );
  const progress = steps.length > 1 ? Math.min(1, at / (steps.length - 1)) : 1;
  return (
    <div role="group" aria-label={label} className={cn("relative", className)}>
      <div aria-hidden className="absolute top-3 right-3 left-3 h-0.5 rounded-full bg-line">
        <div
          className="progress-fill mood-fill h-full rounded-full"
          style={{ width: `${progress * 100}%` }}
        />
      </div>
      <ol className="relative flex justify-between">
        {steps.map((step, i) => {
          const passed = i < at;
          const now = i === at && !done;
          return (
            <li
              key={step.id}
              aria-current={now ? "step" : undefined}
              className="flex flex-col items-center gap-1.5"
            >
              <span
                className={cn(
                  "flex size-6 items-center justify-center rounded-full border-2 text-caption font-semibold transition-colors duration-300",
                  passed && "border-accent bg-accent-fill text-on-accent",
                  now && "checkpoint-now border-accent bg-surface text-accent",
                  !passed && !now && "border-line bg-surface text-subtle",
                )}
              >
                {passed ? <DrawnCheck size={13} /> : i + 1}
              </span>
              <span
                className={cn(
                  "text-caption whitespace-nowrap transition-colors duration-300",
                  now ? "font-semibold text-ink" : passed ? "text-muted" : "text-subtle",
                )}
              >
                {step.label}
                {passed && <span className="sr-only"> (완료)</span>}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** 0에서 value까지 숫자가 차오른다(개요 숫자) */
export function CountUp({ value, duration = 700 }: { value: number; duration?: number }) {
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (reduced) return;
    let frame = 0;
    const begin = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - begin) / duration);
      setShown(Math.round(value * (1 - Math.pow(1 - t, 3))));
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value, duration, reduced]);
  return <span className="tabular-nums">{reduced ? value : shown}</span>;
}

/** 불러오는 중 자리표시 줄 */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("shimmer rounded-md", className)} />;
}

/**
 * 화면에 들어올 때 떠오른다(스크롤하며 차례로 나타나는 구역). IntersectionObserver가 없거나 움직임 줄이기면
 * 처음부터 보인다. 인쇄에서는 globals.css가 모두 보이게 한다
 */
export function RevealOnView({
  as: Tag = "div",
  className,
  children,
  ...rest
}: {
  as?: ElementType;
  className?: string;
  children?: ReactNode;
} & Record<string, unknown>) {
  const ref = useRef<HTMLElement>(null);
  const reduced = useReducedMotion();
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || reduced || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setSeen(true);
          observer.disconnect();
        }
      },
      { rootMargin: "0px 0px -8% 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [reduced]);
  const hidden = !reduced && !seen && typeof IntersectionObserver !== "undefined";
  return (
    <Tag
      ref={ref}
      data-seen={!hidden || undefined}
      className={cn(hidden ? "reveal-wait" : "reveal", className)}
      {...rest}
    >
      {children}
    </Tag>
  );
}
