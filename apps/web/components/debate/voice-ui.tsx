"use client";

// 음성 입력 화면 조각: 소리 크기 파형·경과 시간·말하는 사람 빛(프로필)과 왼쪽 음성 패널.
// 소리 크기는 이 조각들만 구독해 다시 그린다(토론 화면 전체는 다시 그리지 않음).
import { Mic, MicOff, Volume2 } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";

import { Button, cn } from "@/components/ui";

import type { SpeechPlayer, SpeechSnapshot } from "./speech";
import { elapsedText, type VoiceInput, type VoiceState } from "./voice";

const NOOP = () => () => {};
const IDLE: VoiceState = { status: "idle", startedAt: null, error: null, disabled: false };
const FLAT: number[] = [];

export function useVoiceState(voice: VoiceInput | null): VoiceState {
  return useSyncExternalStore(
    voice?.subscribe ?? NOOP,
    voice?.getState ?? (() => IDLE),
    () => IDLE,
  );
}

function useVoiceLevel(voice: VoiceInput | null): number {
  return useSyncExternalStore(voice?.subscribeLevel ?? NOOP, voice?.getLevel ?? (() => 0), () => 0);
}

function useVoiceHistory(voice: VoiceInput | null): number[] {
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
  voice: VoiceInput | null;
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
export function LevelGlow({ voice, className }: { voice: VoiceInput | null; className?: string }) {
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

const NO_SPEECH: SpeechSnapshot = {
  settings: { enabled: false, volume: 0.8 },
  playing: null,
  unavailable: false,
};

export function useSpeech(speech: SpeechPlayer | null) {
  return useSyncExternalStore(
    speech?.subscribe ?? NOOP,
    speech?.getSnapshot ?? (() => NO_SPEECH),
    () => NO_SPEECH,
  );
}

/**
 * 왼쪽 음성 패널: 마이크 상태와 소리 크기, 마이크 테스트(말하기가 있을 때), AI 동료 음성 켜기·크기(TTS가 있을 때).
 */
export function VoicePanel({
  voice,
  speech,
}: {
  voice: VoiceInput | null;
  speech: SpeechPlayer | null;
}) {
  const voiceDisabled = useVoiceState(voice).disabled;
  return (
    <section
      aria-label="음성"
      className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto rounded-xl border border-line bg-surface p-4"
    >
      <h2 className="text-callout font-semibold">음성</h2>
      {voice && !voiceDisabled && <MicSection voice={voice} />}
      <SpeechSection speech={speech} divided={!!voice && !voiceDisabled} />
    </section>
  );
}

function MicSection({ voice }: { voice: VoiceInput }) {
  const state = useVoiceState(voice);
  const listening = isListening(state);
  const busy = state.status === "starting" || state.status === "transcribing";
  const status =
    state.status === "recording"
      ? "듣는 중"
      : state.status === "testing"
        ? "마이크 테스트 중"
        : state.status === "starting"
          ? "마이크를 켜는 중…"
          : state.status === "transcribing"
            ? "글자로 바꾸는 중…"
            : state.error === "denied"
              ? "마이크 권한이 필요해요"
              : "입력창의 마이크 버튼을 누르면 말할 수 있어요";
  return (
    <div className="flex flex-col gap-2">
      <p className="flex items-center gap-1.5 text-footnote">
        {state.error === "denied" ? (
          <MicOff size={14} className="shrink-0 text-danger" aria-hidden />
        ) : (
          <Mic
            size={14}
            className={cn("shrink-0", listening ? "text-danger" : "text-muted")}
            aria-hidden
          />
        )}
        <span className={cn(listening && "font-medium text-danger")}>{status}</span>
        {listening && <Elapsed startedAt={state.startedAt} className="ml-auto text-muted" />}
      </p>
      <div
        className={cn(
          "flex h-12 items-center rounded-lg px-3 transition-colors duration-200",
          listening ? "bg-danger-soft" : "bg-surface-sunken",
        )}
      >
        <LevelBars
          voice={voice}
          tone={state.status === "testing" ? "accent" : "danger"}
          className={cn("h-8 w-full justify-between", !listening && "opacity-30")}
        />
      </div>
      <Button
        size="sm"
        variant="secondary"
        disabled={busy || state.status === "recording"}
        onClick={() => (state.status === "testing" ? voice.stop() : voice.start("test"))}
      >
        {state.status === "testing" ? "테스트 끝내기" : "마이크 테스트"}
      </Button>
    </div>
  );
}

function SpeechSection({ speech, divided }: { speech: SpeechPlayer | null; divided: boolean }) {
  const { settings, playing, unavailable } = useSpeech(speech);
  const ready = !!speech && !unavailable;
  return (
    <div className={cn("flex flex-col gap-2", divided && "border-t border-line pt-4")}>
      <div className="flex items-center gap-2">
        <Volume2
          size={14}
          className={cn("shrink-0", playing ? "text-accent" : "text-muted")}
          aria-hidden
        />
        <span className="text-footnote">AI 동료 음성</span>
        <button
          type="button"
          role="switch"
          aria-checked={ready && settings.enabled}
          aria-label="AI 동료 음성 듣기"
          disabled={!ready}
          onClick={() => speech?.setEnabled(!settings.enabled)}
          className={cn(
            "ml-auto flex h-5 w-9 items-center rounded-full p-0.5 transition-colors duration-150 disabled:opacity-60",
            settings.enabled ? "bg-accent-fill" : "bg-line",
          )}
        >
          <span
            className={cn(
              "block size-4 rounded-full bg-surface shadow-sm transition-transform duration-150",
              settings.enabled && "translate-x-4",
            )}
          />
        </button>
      </div>
      <input
        type="range"
        aria-label="AI 동료 음성 크기"
        min={0}
        max={1}
        step={0.05}
        disabled={!ready || !settings.enabled}
        value={settings.volume}
        onChange={(event) => speech?.setVolume(Number(event.target.value))}
        className="w-full accent-[var(--color-accent)] disabled:opacity-40"
      />
      <p className="text-caption text-muted">
        {unavailable
          ? "지금은 AI 동료 음성을 쓸 수 없어요. 메시지는 글로 보여요."
          : !ready
            ? "AI 동료 음성은 준비 중이에요."
            : playing
              ? "AI 동료 메시지를 읽는 중이에요."
              : settings.enabled
                ? "새로 온 AI 동료 메시지를 읽어 줘요."
                : "켜면 새로 온 AI 동료 메시지를 소리로 들을 수 있어요."}
      </p>
    </div>
  );
}
