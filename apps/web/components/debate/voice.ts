// 지원자 음성 입력(말하기). 마이크 소리를 브라우저 안에서만 듣고(소리 크기 애니메이션용), 녹음을 마치면
// transcribe로 글자를 받아 입력창에 넣는다. 지원자는 그 글을 고쳐서 직접 보낸다(녹음은 저장하지 않음).
// 글자 변환은 대체 순서를 따른다: 웹 서버 라우트(/api/voice/transcribe, NEXT_PUBLIC_VOICE_API=1) → mock 예시 문장
// (NEXT_PUBLIC_MOCK=1) → 없음. 없으면 말하기를 숨기고, 쓰다가 FAIL_LIMIT번 연달아 실패하면 이번 화면에서 말하기를 꺼
// 글자 입력만 남긴다. 어느 경우든 기존 글자 입력은 그대로 동작한다(prefixcon PC-14).
// 소리 크기는 화면 전체를 다시 그리지 않도록 따로 구독한다(subscribeLevel).
import { MOCK } from "@/lib/api";

/** 웹 서버의 음성 시험 라우트(STT·TTS)를 쓴다 */
export const VOICE_API = process.env.NEXT_PUBLIC_VOICE_API === "1";

export type VoiceStatus = "idle" | "starting" | "recording" | "testing" | "transcribing";
export type VoiceError = "denied" | "unavailable" | "failed" | "off";
export type VoiceState = {
  status: VoiceStatus;
  startedAt: number | null;
  error: VoiceError | null;
  /** 이번 화면에서 말하기를 끔(쓸 수 없거나 연달아 실패). 글자 입력만 남는다 */
  disabled: boolean;
};
export type Transcribe = (audio: Blob) => Promise<string>;

/** 파형 막대 수 */
export const LEVEL_BARS = 24;
const IDLE = { status: "idle", startedAt: null, error: null } satisfies Partial<VoiceState>;
/** 글자 변환이 이만큼 연달아 실패하면 말하기를 끈다 */
export const FAIL_LIMIT = 2;

/** 시간 영역 샘플(0~255, 128이 0)의 RMS를 0~1 소리 크기로. 말소리는 RMS 0.05~0.2라 4배로 늘려 자른다 */
export function rmsLevel(samples: Uint8Array): number {
  if (!samples.length) return 0;
  let sum = 0;
  for (const v of samples) {
    const x = (v - 128) / 128;
    sum += x * x;
  }
  return Math.min(1, Math.sqrt(sum / samples.length) * 4);
}

/** 빠르게 오르고 천천히 내려오는 소리 크기(막대가 떨리지 않게) */
export function smoothLevel(prev: number, next: number): number {
  const k = next > prev ? 0.6 : 0.15;
  return prev + (next - prev) * k;
}

/** "0:07" */
export function elapsedText(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export class VoiceInput {
  private state: VoiceState = { ...IDLE, disabled: false };
  private failures = 0;
  private listeners = new Set<() => void>();
  private levelListeners = new Set<() => void>();
  private level = 0;
  private history: number[] = Array(LEVEL_BARS).fill(0);
  private stream: MediaStream | null = null;
  private audio: AudioContext | null = null;
  private raf = 0;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private frame = 0;

  constructor(private readonly transcribe: Transcribe) {}

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

  private set(next: Partial<VoiceState>) {
    this.state = { ...this.state, ...next };
    this.listeners.forEach((fn) => fn());
  }

  /** record: 녹음해서 글자로, test: 소리 크기만 본다(마이크 테스트) */
  async start(mode: "record" | "test") {
    if (this.state.status !== "idle" || this.state.disabled) return;
    this.set({ status: "starting", error: null });
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("unavailable");
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      const denied = e instanceof DOMException && e.name === "NotAllowedError";
      this.set({ status: "idle", error: denied ? "denied" : "unavailable" });
      return;
    }
    this.listenLevel(this.stream);
    if (mode === "record" && typeof MediaRecorder !== "undefined") {
      this.chunks = [];
      this.recorder = new MediaRecorder(this.stream);
      this.recorder.ondataavailable = (ev) => ev.data.size && this.chunks.push(ev.data);
      this.recorder.start();
    }
    this.set({ status: mode === "record" ? "recording" : "testing", startedAt: Date.now() });
  }

  /** 녹음을 마친다. record였으면 받은 글자를, 아니면 null */
  async stop(): Promise<string | null> {
    const { status } = this.state;
    if (status !== "recording" && status !== "testing") return null;
    if (status === "testing") {
      this.release();
      this.set({ ...IDLE });
      return null;
    }
    const audio = await this.finishRecording();
    this.release();
    this.set({ status: "transcribing", startedAt: null });
    try {
      const text = await this.transcribe(audio);
      this.failures = 0;
      this.set({ ...IDLE });
      return text.trim() || null;
    } catch {
      this.failures += 1;
      if (this.failures >= FAIL_LIMIT) this.set({ ...IDLE, error: "off", disabled: true });
      else this.set({ ...IDLE, error: "failed" });
      return null;
    }
  }

  /** 녹음을 버린다 */
  cancel() {
    this.recorder?.stop();
    this.recorder = null;
    this.release();
    this.set({ ...IDLE });
  }

  clearError() {
    if (this.state.error) this.set({ error: null });
  }

  /** 말하기를 끈다(서버에 음성 기능이 없을 때 등). 듣던 마이크도 끈다 */
  disable() {
    if (this.state.status !== "idle") this.cancel();
    this.set({ disabled: true });
  }

  private finishRecording(): Promise<Blob> {
    const recorder = this.recorder;
    this.recorder = null;
    if (!recorder || recorder.state === "inactive") return Promise.resolve(new Blob(this.chunks));
    return new Promise((resolve) => {
      recorder.onstop = () => resolve(new Blob(this.chunks, { type: recorder.mimeType }));
      recorder.stop();
    });
  }

  private listenLevel(stream: MediaStream) {
    const Ctx =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    this.audio = new Ctx();
    const analyser = this.audio.createAnalyser();
    analyser.fftSize = 1024;
    this.audio.createMediaStreamSource(stream).connect(analyser);
    const buf = new Uint8Array(analyser.fftSize);
    const tick = () => {
      analyser.getByteTimeDomainData(buf);
      this.level = smoothLevel(this.level, rmsLevel(buf));
      // 파형은 4프레임(약 15Hz)마다 한 칸씩 민다
      this.frame = (this.frame + 1) % 4;
      if (this.frame === 0) this.history = [...this.history.slice(1), this.level];
      this.levelListeners.forEach((fn) => fn());
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  private release() {
    cancelAnimationFrame(this.raf);
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    void this.audio?.close();
    this.audio = null;
    this.level = 0;
    this.history = Array(LEVEL_BARS).fill(0);
    this.levelListeners.forEach((fn) => fn());
  }
}

const MOCK_SENTENCES = [
  "제 생각에는 이 부분을 먼저 정하고 넘어가면 좋겠어요.",
  "자료를 같이 보면서 근거를 하나씩 맞춰 보면 어떨까요?",
  "다른 분들은 어떻게 생각하시는지도 듣고 싶어요.",
];

/** mock 전용 글자 변환: 녹음 길이와 상관없이 예시 문장을 돌아가며 준다 */
function mockTranscribe(): Transcribe {
  let i = 0;
  return () =>
    new Promise((resolve) =>
      setTimeout(() => resolve(MOCK_SENTENCES[i++ % MOCK_SENTENCES.length]), 700),
    );
}

/** 녹음 형식에 맞는 파일 이름(OpenAI는 확장자로 형식을 본다) */
export function audioFileName(mimeType: string) {
  if (mimeType.includes("mp4")) return "speech.mp4";
  if (mimeType.includes("ogg")) return "speech.ogg";
  if (mimeType.includes("wav")) return "speech.wav";
  return "speech.webm";
}

/** 웹 서버 라우트로 녹음을 보내 글자를 받는다 */
const serverTranscribe: Transcribe = async (audio) => {
  const form = new FormData();
  form.append("file", audio, audioFileName(audio.type));
  const res = await fetch("/api/voice/transcribe", { method: "POST", body: form });
  if (!res.ok) throw new Error(`transcribe ${res.status}`);
  return ((await res.json()) as { text: string }).text;
};

/** 앞 변환이 실패하면 다음 변환으로 넘어간다. 모두 실패하면 마지막 오류를 던진다 */
export function fallbackTranscribe(chain: Transcribe[]): Transcribe {
  return async (audio) => {
    let last: unknown = new Error("no transcriber");
    for (const transcribe of chain) {
      try {
        return await transcribe(audio);
      } catch (e) {
        last = e;
      }
    }
    throw last;
  };
}

/** 지금 쓸 수 있는 글자 변환(대체 순서: 서버 → mock). 없으면 null */
export function availableTranscribe(): Transcribe | null {
  const chain = [...(VOICE_API ? [serverTranscribe] : []), ...(MOCK ? [mockTranscribe()] : [])];
  return chain.length ? fallbackTranscribe(chain) : null;
}
