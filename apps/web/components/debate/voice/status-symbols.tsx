"use client";

// 음성 회의 참여자 상태 심볼(에셋): 발언 중·준비 중·녹음 중·손 듦·내 차례.
// 24x24 같은 격자·같은 선 굵기로 그리고 색은 currentColor라 라이트·다크와 자리 색을 그대로 따른다.
// 움직임(파형·점·흔들기)은 globals.css의 .sym-* 키프레임이고, 움직임 줄이기를 켜면 멈춘다.
// 상태를 색만으로 구분하지 않도록 늘 글자(라벨)와 함께 쓴다(SeatStatusBadge).
import type { SVGProps } from "react";

import { cn } from "@/components/ui";

type SymbolProps = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 16, className, children, ...rest }: SymbolProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      aria-hidden
      focusable="false"
      className={cn("shrink-0", className)}
      {...rest}
    >
      {children}
    </svg>
  );
}

/** 발언 중: 높낮이가 움직이는 막대 넷(소리 파형) */
export function SpeakingSymbol(props: SymbolProps) {
  return (
    <Svg {...props}>
      {[4, 9, 14, 19].map((x, i) => (
        <rect
          key={x}
          x={x - 1.4}
          y={6}
          width={2.8}
          height={12}
          rx={1.4}
          fill="currentColor"
          className="sym-wave"
          style={{ animationDelay: `${i * 0.12}s` }}
        />
      ))}
    </Svg>
  );
}

/** 준비 중: 차례로 떠오르는 점 셋(생각하는 중) */
export function PreparingSymbol(props: SymbolProps) {
  return (
    <Svg {...props}>
      {[6, 12, 18].map((cx, i) => (
        <circle
          key={cx}
          cx={cx}
          cy={12}
          r={2.2}
          fill="currentColor"
          className="sym-dot"
          style={{ animationDelay: `${i * 0.16}s` }}
        />
      ))}
    </Svg>
  );
}

/** 녹음 중: 마이크와 깜빡이는 녹음 점 */
export function RecordingSymbol(props: SymbolProps) {
  return (
    <Svg {...props}>
      <rect x={6.5} y={3} width={7} height={11} rx={3.5} stroke="currentColor" strokeWidth={2} />
      <path
        d="M3.5 11a6.5 6.5 0 0 0 13 0M10 17.5V21"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
      />
      <circle cx={19.5} cy={5} r={2.5} fill="currentColor" className="sym-blink" />
    </Svg>
  );
}

/** 손 듦: 든 손바닥(살짝 흔든다) */
export function HandSymbol(props: SymbolProps) {
  return (
    <Svg {...props}>
      <g className="sym-wave-hand">
        <path
          d="M8.5 12V5.5a1.5 1.5 0 0 1 3 0V11m0-6.5V4a1.5 1.5 0 0 1 3 0v7m0-5.5a1.5 1.5 0 0 1 3 0V13c0 4-2.5 7.5-6.5 7.5h-.6c-2.2 0-3.6-.9-4.9-2.7L3.6 14.3a1.5 1.5 0 0 1 2.3-1.9l2.6 2.6"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </g>
    </Svg>
  );
}

/** 내 차례: 말풍선 안의 마이크(이제 말할 수 있음) */
export function MyTurnSymbol(props: SymbolProps) {
  return (
    <Svg {...props}>
      <path
        d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H11l-4.5 4v-4h0A2.5 2.5 0 0 1 4 13.5Z"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinejoin="round"
      />
      <rect x={10.25} y={6} width={3.5} height={5.5} rx={1.75} fill="currentColor" />
      <path
        d="M8.5 10a3.5 3.5 0 0 0 7 0"
        stroke="currentColor"
        strokeWidth={1.6}
        strokeLinecap="round"
      />
    </Svg>
  );
}

export type SeatStatus = "speaking" | "preparing" | "recording" | "hand" | "myturn";

const STATUS: Record<
  SeatStatus,
  { label: string; Symbol: (props: SymbolProps) => React.JSX.Element; tone: string }
> = {
  speaking: { label: "발언 중", Symbol: SpeakingSymbol, tone: "bg-accent-fill text-on-accent" },
  preparing: {
    label: "준비 중",
    Symbol: PreparingSymbol,
    tone: "bg-surface text-muted ring-1 ring-line",
  },
  recording: { label: "녹음 중", Symbol: RecordingSymbol, tone: "bg-danger-fill text-on-accent" },
  hand: {
    label: "손 듦",
    Symbol: HandSymbol,
    tone: "bg-warning-soft text-warning ring-1 ring-warning/40",
  },
  myturn: {
    label: "내 차례",
    Symbol: MyTurnSymbol,
    tone: "bg-accent-soft text-accent ring-1 ring-accent/40",
  },
};

export function statusLabel(status: SeatStatus) {
  return STATUS[status].label;
}

/** 프로필 카드 위 상태 표시: 심볼 + 글자. 글자가 있어 색을 못 보는 사람도 구분한다 */
export function SeatStatusBadge({ status, className }: { status: SeatStatus; className?: string }) {
  const { label, Symbol, tone } = STATUS[status];
  return (
    <span
      data-status={status}
      className={cn(
        "status-badge inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-caption font-semibold whitespace-nowrap shadow-sm",
        tone,
        className,
      )}
    >
      <Symbol size={14} />
      {label}
    </span>
  );
}
