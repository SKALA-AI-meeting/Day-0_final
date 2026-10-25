// 음성 토론 녹음 중 마이크 소리 크기. 파형(LevelBars)·내 프로필 빛(LevelGlow)이 구독하고,
// 토론 화면 전체는 다시 그리지 않는다(voice.ts VoiceInput과 같은 구독 모양).
import { LEVEL_BARS, rmsLevel, smoothLevel, type VoiceState } from "../voice";

const IDLE: VoiceState = { status: "idle", startedAt: null, error: null, disabled: false };

export class MicLevel {
  private level = 0;
  private history: number[] = Array(LEVEL_BARS).fill(0);
  private state: VoiceState = IDLE;
  private listeners = new Set<() => void>();
  private levelListeners = new Set<() => void>();

  getState = () => this.state;
  getLevel = () => this.level;
  getHistory = () => this.history;

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => void this.listeners.delete(fn);
  };
  subscribeLevel = (fn: () => void) => {
    this.levelListeners.add(fn);
    return () => void this.levelListeners.delete(fn);
  };

  /** 녹음 시작·끝. 끝나면 파형을 바닥으로 내린다 */
  setRecording(on: boolean) {
    if ((this.state.status === "recording") === on) return;
    this.state = on ? { ...IDLE, status: "recording", startedAt: Date.now() } : IDLE;
    if (!on) this.reset();
    this.listeners.forEach((fn) => fn());
  }

  /** 시간 영역 샘플(0~255) 한 묶음을 반영한다 */
  push(samples: Uint8Array) {
    this.level = smoothLevel(this.level, rmsLevel(samples));
    this.history = [...this.history.slice(1), this.level];
    this.levelListeners.forEach((fn) => fn());
  }

  private reset() {
    this.level = 0;
    this.history = Array(LEVEL_BARS).fill(0);
    this.levelListeners.forEach((fn) => fn());
  }
}
