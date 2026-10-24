// AI 동료 음성(TTS) 재생. 새로 온 AI 동료 메시지를 순서대로 읽는다. 지원자가 켜야만 읽고(자동 재생 제한도 이 클릭으로 풀린다),
// 읽는 중인 자리는 프로필에서 "말하는 중"으로 보인다. 음성은 웹 서버 라우트(/api/voice/speech, 개발용 시험)에서 받는다.
// 설정(켜기·크기)은 이 브라우저에만 남긴다. 음성을 FAIL_LIMIT번 연달아 받지 못하면 이번 화면에서 끄고 글로만 본다.
import type { Seat } from "./types";
import { FAIL_LIMIT, VOICE_API } from "./voice";

const SETTINGS_KEY = "dayzero.speech";

type Item = { seat: Seat; audio: Promise<Blob | null> };

export type SpeechSettings = { enabled: boolean; volume: number };
export type SpeechSnapshot = {
  settings: SpeechSettings;
  playing: Seat | null;
  /** 이번 화면에서 쓸 수 없음(서버에 없거나 연달아 실패) */
  unavailable: boolean;
};

function loadSettings(): SpeechSettings {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "null") as SpeechSettings | null;
    if (saved && typeof saved.enabled === "boolean" && typeof saved.volume === "number")
      return saved;
  } catch {
    // 저장소를 못 쓰면 기본값
  }
  return { enabled: false, volume: 0.8 };
}

export class SpeechPlayer {
  private settings: SpeechSettings = { enabled: false, volume: 0.8 };
  private queue: Item[] = [];
  private playing: Seat | null = null;
  /** 지금 읽는(또는 음성을 기다리는) 항목. 멈춘 뒤 늦게 온 음성을 가려낸다 */
  private current: Item | null = null;
  private audio: HTMLAudioElement | null = null;
  private url: string | null = null;
  private listeners = new Set<() => void>();
  private failures = 0;
  private unavailable = false;
  private snapshot: SpeechSnapshot = {
    settings: this.settings,
    playing: null,
    unavailable: false,
  };

  /** 저장된 설정을 읽는다(브라우저에서만) */
  restore() {
    this.settings = loadSettings();
    this.emit();
  }

  getSnapshot = () => this.snapshot;
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => void this.listeners.delete(fn);
  };

  private emit() {
    this.snapshot = {
      settings: this.settings,
      playing: this.playing,
      unavailable: this.unavailable,
    };
    this.listeners.forEach((fn) => fn());
  }

  private save() {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(this.settings));
    } catch {
      // 저장소를 못 써도 이번 화면에서는 동작한다
    }
  }

  setEnabled(enabled: boolean) {
    this.settings = { ...this.settings, enabled };
    this.save();
    if (!enabled) this.stop();
    this.emit();
  }

  setVolume(volume: number) {
    this.settings = { ...this.settings, volume };
    if (this.audio) this.audio.volume = volume;
    this.save();
    this.emit();
  }

  /** 새 AI 동료 메시지를 읽기 줄에 넣는다. 음성은 바로 받아 두어 앞 메시지가 끝나면 이어서 읽는다 */
  enqueue(seat: Seat, text: string) {
    if (!this.settings.enabled || this.unavailable) return;
    const audio = fetch("/api/voice/speech", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, seat }),
    })
      .then((res) => (res.ok ? res.blob() : null))
      .catch(() => null);
    this.queue.push({ seat, audio });
    if (!this.playing) void this.next();
  }

  /** 이번 화면에서 끈다(서버에 음성 기능이 없을 때 등). 저장된 설정은 그대로 둔다 */
  disable() {
    this.unavailable = true;
    this.stop();
  }

  /** 읽는 중인 것과 읽기 줄을 모두 멈춘다(지원자가 말하기 시작할 때 등) */
  stop() {
    this.queue = [];
    this.audio?.pause();
    this.finish();
  }

  private finish() {
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = null;
    this.audio = null;
    this.current = null;
    this.playing = null;
    this.emit();
  }

  private async next() {
    const item = this.queue.shift();
    if (!item) return;
    this.current = item;
    this.playing = item.seat;
    this.emit();
    const blob = await item.audio;
    // 기다리는 사이 멈췄으면 버린다
    if (this.current !== item) return;
    if (!blob) {
      this.failures += 1;
      if (this.failures >= FAIL_LIMIT) return this.disable();
      this.finish();
      return void this.next();
    }
    this.failures = 0;
    this.url = URL.createObjectURL(blob);
    const audio = new Audio(this.url);
    audio.volume = this.settings.volume;
    this.audio = audio;
    const done = () => {
      if (this.audio !== audio) return;
      this.finish();
      void this.next();
    };
    audio.onended = done;
    audio.onerror = done;
    await audio.play().catch(done);
  }
}

/** AI 동료 음성을 쓸 수 있는지(웹 서버 음성 라우트가 켜져 있을 때만) */
export function availableSpeech() {
  return VOICE_API;
}
