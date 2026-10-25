"use client";

// 음성 입력 화면 조각: 소리 크기 파형·경과 시간·말하는 사람 빛(프로필).
// 소리 크기는 이 조각들만 구독해 다시 그린다(토론 화면 전체는 다시 그리지 않음).
import { useEffect, useState, useSyncExternalStore } from "react";

import { cn } from "@/components/ui";

import { elapsedText, type VoiceInput, type VoiceState } from "./voice";

const NOOP = () => () => {};

/** 소리 크기 파형·빛이 구독하는 값. 말하기(VoiceInput)와 음성 토론 녹음(MicLevel)이 같이 쓴다 */
export type LevelSource = Pick<VoiceInput, "subscribeLevel" | "getLevel" | "getHistory">;
/** 프로필이 구독하는 마이크 상태 + 소리 크기 */
export type VoiceSource = LevelSource & Pick<VoiceInput, "subscribe" | "getState">;
const IDLE: VoiceState = { status: "idle", startedAt: null, error: null, disabled: false };
const FLAT: number[] = [];

export function useVoiceState(voice: VoiceSource | null): VoiceState {
  return useSyncExternalStore(
    voice?.subscribe ?? NOOP,
    voice?.getState ?? (() => IDLE),
    () => IDLE,
  );
}

function useVoiceLevel(voice: LevelSource | null): number {
  return useSyncExternalStore(voice?.subscribeLevel ?? NOOP, voice?.getLevel ?? (() => 0), () => 0);
}

function useVoiceHistory(voice: LevelSource | null): number[] {
  return useSyncExternalStore(
    voice?.subscribeLevel ?? NOOP,
    voice?.getHistory ?? (() => FLAT),
    () => FLAT,
  );
}

/** 마이크로 듣는 중인지(녹음·테스트) */
export function isListening(state: VoiceState) {
  return state.status === "recording" || state.status === "testing";
}

/** 최근 소리 크기를 막대 파형으로. 듣지 않을 때는 낮게 깔린다 */
export function LevelBars({
  voice,
  className,
  tone = "danger",
}: {
  voice: LevelSource | null;
  className?: string;
  tone?: "danger" | "accent";
}) {
  const history = useVoiceHistory(voice);
  return (
    <span aria-hidden className={cn("flex h-full items-center gap-[3px]", className)}>
      {history.map((v, i) => (
        <span
          key={i}
          className={cn(
            "w-[3px] shrink-0 rounded-full transition-[height] duration-75",
            tone === "danger" ? "bg-danger" : "bg-accent",
          )}
          style={{ height: `${Math.max(12, Math.round(v * 100))}%` }}
        />
      ))}
    </span>
  );
}

/** 녹음 시작부터 흐른 시간 "0:07" */
export function Elapsed({
  startedAt,
  className,
}: {
  startedAt: number | null;
  className?: string;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!startedAt) return;
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, [startedAt]);
  return (
    <span className={cn("tabular-nums", className)}>
      {elapsedText(startedAt ? now - startedAt : 0)}
    </span>
  );
}

/** 소리 크기만큼 퍼지는 빛(말하기 끝내기 버튼·내 프로필 둘레) */
export function LevelGlow({ voice, className }: { voice: LevelSource | null; className?: string }) {
  const level = useVoiceLevel(voice);
  return (
    <span
      aria-hidden
      className={cn("pointer-events-none absolute inset-0 rounded-[inherit]", className)}
      style={{
        boxShadow: `0 0 0 ${2 + level * 10}px color-mix(in srgb, var(--color-danger) ${18 + level * 30}%, transparent)`,
      }}
    />
  );
}

const VOICE_ERROR: Record<NonNullable<VoiceState["error"]>, string> = {
  denied: "마이크 권한이 꺼져 있어요. 브라우저 주소창의 권한 설정에서 마이크를 허용해 주세요.",
  unavailable: "이 브라우저에서는 마이크를 쓸 수 없어요.",
  failed: "말한 내용을 글자로 바꾸지 못했어요. 다시 말하거나 직접 입력해 주세요.",
  off: "지금은 음성 입력을 쓸 수 없어 글자 입력으로 바꿨어요.",
};

export function voiceErrorText(error: VoiceState["error"]) {
  return error ? VOICE_ERROR[error] : null;
}
