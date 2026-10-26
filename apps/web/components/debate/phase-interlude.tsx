"use client";

// 단계 전환 장면. 단계가 바뀌면 무드 색 띠가 화면을 쓸고 지나가며 그림(선이 그려지는 SVG)과 단계 이름·시간이
// 떠올랐다 사라진다(globals.css .interlude, 2.4초). 키워드를 고르기 전(키워드 단계)은 발상 그림이고, 고른 뒤에는
// 그 키워드의 문양(물방울·불꽃·잎·바람결)에 단계 표시(문제 상황 긴장선·해결책 반짝임·기획안 체크)를 얹고
// 색도 그 테마의 단계 톤(theme.ts)으로 칠한다. 화면 낭독은 토론방의 단계 안내가 맡으므로 aria-hidden이고,
// 누르기를 막지 않는다. 움직임 줄이기면 그림만 잠깐 보였다 사라진다.
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";

import { PHASE_LABEL } from "./state";
import { moodFor, type ThemeKey } from "./theme";
import type { Phase } from "./types";

/** 장면이 보이는 시간(ms). globals.css .interlude 애니메이션 합과 같다 */
export const INTERLUDE_MS = 2400;

export const THEME_LABEL: Record<ThemeKey, string> = {
  water: "물",
  fire: "불",
  tree: "나무",
  wind: "바람",
};

function subtitle(phase: Phase, theme: ThemeKey | null) {
  const word = theme ? `'${THEME_LABEL[theme]}'` : "그 키워드";
  if (phase === "keyword") return "함께 다룰 키워드를 골라요";
  if (phase === "problem") return `${word}로 풀 문제 상황을 정해요`;
  if (phase === "solution") return "문제를 풀 해결책을 모아요";
  return "정한 것을 기획안으로 정리해요";
}

const i = (n: number) => ({ ["--i" as string]: n }) as CSSProperties;

const line = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 3,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  pathLength: 1,
  className: "draw",
};

function Spark({ x, y, n, small = false }: { x: number; y: number; n: number; small?: boolean }) {
  const s = small ? 0.7 : 1;
  return (
    <path
      className="spark accent"
      d={`M${x} ${y - 10 * s}l${3 * s} ${7 * s} ${7 * s} ${3 * s}-${7 * s} ${3 * s}-${3 * s} ${7 * s}-${3 * s}-${7 * s}-${7 * s}-${3 * s} ${7 * s}-${3 * s}z`}
      fill="currentColor"
      style={i(n)}
    />
  );
}

/** 키워드를 고르기 전: 가운데 원에서 뻗는 빛살과 반짝임 */
function KeywordMotif() {
  return (
    <>
      <circle cx="60" cy="60" r="16" {...line} style={i(0)} />
      {[0, 45, 90, 135, 180, 225, 270, 315].map((deg, n) => {
        const r = (deg * Math.PI) / 180;
        const len = n % 2 ? 40 : 48;
        return (
          <path
            key={deg}
            d={`M${60 + Math.cos(r) * 26} ${60 + Math.sin(r) * 26} L${60 + Math.cos(r) * len} ${60 + Math.sin(r) * len}`}
            {...line}
            style={i(n + 1)}
          />
        );
      })}
      <Spark x={100} y={22} n={0} />
      <Spark x={22} y={96} n={1} small />
    </>
  );
}

/** 테마 문양(선): 물방울과 물결, 불꽃, 잎과 잎맥, 바람결 */
function Emblem({ theme }: { theme: ThemeKey }) {
  if (theme === "water")
    return (
      <>
        <path
          d="M60 16C52 30 34 50 34 70a26 26 0 0 0 52 0C86 50 68 30 60 16z"
          {...line}
          style={i(0)}
        />
        <path d="M46 72c4 8 12 12 20 10" {...line} style={i(2)} />
        <path d="M22 104c8-5 16-5 24 0s16 5 24 0 16-5 24 0" {...line} style={i(3)} />
      </>
    );
  if (theme === "fire")
    return (
      <>
        <path
          d="M60 14c6 16 24 26 24 50a24 24 0 0 1-48 0c0-12 6-20 12-26 0 9 4 14 9 15-3-15 0-28 3-39z"
          {...line}
          style={i(0)}
        />
        <path d="M60 66c4 6 10 10 10 18a10 10 0 0 1-20 0c0-6 4-10 6-14z" {...line} style={i(2)} />
      </>
    );
  if (theme === "tree")
    return (
      <>
        <path d="M28 88C28 50 56 28 94 24c-2 36-24 64-62 64z" {...line} style={i(0)} />
        <path d="M30 90L80 40" {...line} style={i(1)} />
        <path d="M48 72l-8-14M60 60l-6-16M70 50l-2-14" {...line} style={i(2)} />
      </>
    );
  return (
    <>
      <path d="M14 46h58a12 12 0 1 0-12-12" {...line} style={i(0)} />
      <path d="M14 64h78a12 12 0 1 1-12 12" {...line} style={i(1)} />
      <path d="M14 82h40" {...line} style={i(2)} />
      <path d="M62 82h10" {...line} style={i(3)} />
    </>
  );
}

/** 단계 표시(포인트색): 문제 상황 긴장선, 해결책 반짝임, 기획안 체크 */
function PhaseMark({ phase }: { phase: Phase }) {
  if (phase === "problem")
    return (
      <>
        <path
          className="spark accent"
          d="M98 14l-7 12h9l-7 12"
          fill="none"
          stroke="currentColor"
          strokeWidth={3}
          strokeLinecap="round"
          strokeLinejoin="round"
          style={i(0)}
        />
        <circle
          className="spark accent"
          cx="106"
          cy="46"
          r="3.5"
          fill="currentColor"
          style={i(1)}
        />
      </>
    );
  if (phase === "solution")
    return (
      <>
        <Spark x={102} y={26} n={0} />
        <Spark x={18} y={30} n={1} small />
        <Spark x={106} y={92} n={2} small />
      </>
    );
  return (
    <>
      <circle className="spark accent" cx="96" cy="96" r="13" fill="currentColor" style={i(0)} />
      <path
        d="M90 96l4 4 8-8"
        fill="none"
        stroke="var(--color-surface)"
        strokeWidth={3}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="spark"
        style={i(1)}
      />
    </>
  );
}

function Motif({ phase, theme }: { phase: Phase; theme: ThemeKey | null }) {
  let body: ReactNode;
  if (phase === "keyword") body = <KeywordMotif />;
  else if (!theme) body = <PhaseMark phase={phase} />;
  else
    body = (
      <>
        <Emblem theme={theme} />
        <PhaseMark phase={phase} />
      </>
    );
  return (
    <svg viewBox="0 0 120 120" width={128} height={128} aria-hidden className="interlude-motif">
      {body}
    </svg>
  );
}

/**
 * 단계 전환 장면을 한 번 띄운다. phase가 바뀔 때마다(처음 그릴 때는 아님) 보였다가 INTERLUDE_MS 뒤 사라진다.
 * 인사 화면 동안 null을 주면, 인사가 끝나 키워드 단계로 들어설 때 장면이 나온다.
 * theme은 고른 키워드(없으면 단계 무드), minutes는 그 단계에 주어진 시간(분)
 */
export function PhaseInterlude({
  phase,
  theme = null,
  minutes,
}: {
  phase: string | null;
  theme?: ThemeKey | null;
  minutes?: number;
}) {
  const [shown, setShown] = useState<Phase | null>(null);
  const [prev, setPrev] = useState(phase);
  if (prev !== phase) {
    setPrev(phase);
    // 끝난 세션(completed)처럼 그림이 없는 값은 띄우지 않는다
    if (phase && phase in PHASE_LABEL) setShown(phase as Phase);
  }
  useEffect(() => {
    if (!shown) return;
    const timer = setTimeout(() => setShown(null), INTERLUDE_MS);
    return () => clearTimeout(timer);
  }, [shown]);
  if (!shown) return null;
  const mood = moodFor({ intro: false, ended: false, phase: shown, theme });
  return (
    <div className="interlude" data-mood={mood} aria-hidden data-testid="phase-interlude">
      <div className="interlude-wash" />
      <div className="interlude-card">
        <Motif phase={shown} theme={theme} />
        {theme && shown !== "keyword" && (
          <p className="interlude-sub text-footnote font-semibold tracking-wide text-muted">
            {THEME_LABEL[theme]} 테마
          </p>
        )}
        <p className="interlude-title text-large font-bold tracking-tight">{PHASE_LABEL[shown]}</p>
        <p className="interlude-sub text-callout text-muted">
          {subtitle(shown, theme)}
          {minutes ? ` · ${Math.round(minutes)}분` : ""}
        </p>
      </div>
    </div>
  );
}
