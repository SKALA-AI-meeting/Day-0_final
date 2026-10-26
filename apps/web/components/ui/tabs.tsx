"use client";

import { Tabs as BaseTabs } from "@base-ui/react/tabs";
import type { ReactNode } from "react";

import { cn } from "./cn";

export type TabItem = {
  value: string;
  label: ReactNode;
  /** 안 읽음 수 등. 0이나 undefined면 숨김 */
  count?: number;
  content: ReactNode;
};

export type TabsProps = {
  items: TabItem[];
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  /** underline: 밑줄 탭(기본). segmented: 분절 컨트롤. reference: 리포트 참고 기록 */
  variant?: "underline" | "segmented" | "reference";
  className?: string;
  /** 숨긴 탭의 패널도 남겨 스크롤·입력 중 내용을 지킨다 */
  keepMounted?: boolean;
  panelClassName?: string;
  ariaLabel?: string;
};

export function Tabs({
  items,
  value,
  defaultValue,
  onValueChange,
  variant = "underline",
  className,
  keepMounted = false,
  panelClassName,
  ariaLabel,
}: TabsProps) {
  const segmented = variant === "segmented";
  const reference = variant === "reference";
  return (
    <BaseTabs.Root
      value={value}
      defaultValue={defaultValue ?? items[0]?.value}
      onValueChange={(next) => onValueChange?.(String(next))}
      className={cn("flex min-h-0 flex-col", className)}
    >
      <BaseTabs.List
        aria-label={ariaLabel}
        className={cn(
          "flex shrink-0",
          reference
            ? "inline-flex w-fit max-w-full overflow-x-auto rounded-lg bg-surface-sunken p-0.5"
            : segmented
              ? "relative z-0 mx-3 my-2 rounded-md bg-surface-sunken p-0.5"
              : "gap-1 border-b border-line px-3",
        )}
      >
        {items.map((item) => (
          <BaseTabs.Tab
            key={item.value}
            value={item.value}
            className={cn(
              "text-sm text-muted transition-colors duration-150 aria-selected:text-ink",
              reference
                ? "press inline-flex items-center gap-1.5 rounded-[calc(var(--radius-lg)-2px)] border border-transparent px-3 py-1.5 whitespace-nowrap hover:text-ink aria-selected:border-line aria-selected:bg-surface aria-selected:font-semibold aria-selected:shadow-float"
                : segmented
                  ? "flex flex-1 items-center justify-center rounded-[calc(var(--radius-md)-2px)] px-3 py-1 aria-selected:font-semibold"
                  : "-mb-px border-b-2 border-transparent px-3 py-2 aria-selected:border-accent aria-selected:font-medium",
            )}
          >
            {item.label}
            {((reference && item.count != null) || !!item.count) &&
              (segmented ? (
                // 분절 컨트롤의 배지: 작은 accent 점 + 숫자
                <span className="ml-1.5 inline-flex items-center gap-1 text-xs font-medium text-accent tabular-nums">
                  <span aria-hidden className="size-1.5 rounded-full bg-accent" />
                  {item.count}
                </span>
              ) : reference ? (
                <span className="text-xs font-normal text-muted tabular-nums">{item.count}</span>
              ) : (
                <span className="ml-1.5 rounded-sm bg-accent-fill px-1.5 text-xs text-on-accent tabular-nums">
                  {item.count}
                </span>
              ))}
          </BaseTabs.Tab>
        ))}
        {segmented && (
          // 선택 표시는 탭 위치를 따라 180ms 이동(design-system.md 모션)
          <BaseTabs.Indicator className="absolute top-0.5 bottom-0.5 left-[var(--active-tab-left)] -z-10 w-[var(--active-tab-width)] rounded-[calc(var(--radius-md)-2px)] border border-line bg-surface transition-[left,width] duration-[180ms] ease-[var(--ease-out)] motion-reduce:transition-none" />
        )}
      </BaseTabs.List>
      {items.map((item) => (
        <BaseTabs.Panel
          key={item.value}
          value={item.value}
          keepMounted={keepMounted}
          className={cn("min-h-0 flex-1 overflow-auto", panelClassName)}
        >
          {item.content}
        </BaseTabs.Panel>
      ))}
    </BaseTabs.Root>
  );
}
