"use client";

// 분절 컨트롤(라디오 묶음). 세션 길이(60·15분)와 차원 수준(1~5·기록 없음) 선택에 쓴다.
// 칸 너비를 같게 두고 선택 표시가 고른 칸으로 미끄러진다. 화살표 키로 옮겨 고를 수 있다.
import { useRef, type KeyboardEvent } from "react";

import { cn } from "@/components/ui";

export type SegmentedProps<T extends string | number> = {
  label: string;
  value: T | null;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  disabled?: boolean;
  className?: string;
};

export function Segmented<T extends string | number>({
  label,
  value,
  options,
  onChange,
  disabled,
  className,
}: SegmentedProps<T>) {
  const at = options.findIndex((o) => o.value === value);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const delta = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!delta || disabled || options.length === 0) return;
    event.preventDefault();
    const next = ((at < 0 ? 0 : at + delta) + options.length) % options.length;
    onChange(options[next].value);
    refs.current[next]?.focus();
  }

  return (
    <div
      role="radiogroup"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={cn(
        "relative z-0 inline-grid auto-cols-fr grid-flow-col rounded-md bg-surface-sunken p-0.5",
        className,
      )}
    >
      {at >= 0 && (
        <span
          aria-hidden
          className="absolute top-0.5 bottom-0.5 left-0.5 -z-10 rounded-[calc(var(--radius-md)-2px)] border border-line bg-surface transition-transform duration-[220ms] ease-[var(--ease-out)] motion-reduce:transition-none"
          style={{
            width: `calc((100% - 4px) / ${options.length})`,
            transform: `translateX(${at * 100}%)`,
          }}
        />
      )}
      {options.map((option, i) => {
        const on = option.value === value;
        return (
          <button
            key={String(option.value)}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={on}
            // 고른 칸(없으면 첫 칸)만 Tab으로 들어온다
            tabIndex={on || (at < 0 && i === 0) ? 0 : -1}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={cn(
              "rounded-[calc(var(--radius-md)-2px)] px-3 py-1 text-sm whitespace-nowrap tabular-nums transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50",
              on ? "font-semibold text-ink" : "text-muted hover:text-ink",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
