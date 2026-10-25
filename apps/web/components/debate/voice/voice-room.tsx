"use client";

import { Hand, Info, Square, TriangleAlert, WifiOff } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { API_URL, ApiError, api } from "@/lib/api";
import { Button, cn } from "@/components/ui";
import { CANDIDATE_MESSAGE_MAX_CHARS } from "../state";
import type { Seat, StateSnapshot } from "../types";
import type { Message } from "../types";
import type { SessionAuth } from "../use-session";
import { elapsedText } from "../voice";
import { LevelBars } from "../voice-ui";
import { heardMessages, sentences, spokenCount } from "./caption";
import {
  ACTION_LABEL,
  blockedTrouble,
  canStartVoice,
  spaceShortcutBlocked,
  turnHoldNotice,
  type Action,
  type Trouble,
} from "./control-state";
import { MicLevel } from "./mic-level";
import { MICROPHONE_HELP, isMicrophoneError, requestMicrophone } from "./microphone";
import { finishOnce, normalize } from "./recorder";
import { startPcmStream, type PcmStream } from "./streamer";

type Item = {
  id: string;
  message_id: string;
  seat: string;
  status: string;
  duration_ms: number;
  error?: string;
};
export type VoiceSnapshot = {
  turn_id: string;
  epoch: string;
  state: string;
  generation: number;
  server_now: string;
  /** 이 상태가 된 시각. 내 차례 유지 시간 막대의 시작점 */
  ready_at?: string;
  blocked_reason?: string;
  items: Item[];
  utterance_id?: string;
  speech_deadline_at?: string;
  speech_started_at?: string;
  utterance?: {
    id: string;
    status: string;
    transcript?: string;
    message_id?: string;
    /** 받아 적은 글을 자동 제출하는 시각. 예전 채용 건이면 없음(바로 제출) */
    confirm_deadline_at?: string;
    /** 이 발언을 제출할 수 있는 마지막 시각(ready). 지나면 발언이 버려진다 */
    submit_deadline_at?: string;
  };
  /** 이 차례에서 다시 녹음을 이미 썼는지 */
  rerecorded?: boolean;
  /** 재생이 끝난 동료 발언 id. 상태 조회(GET)에만 있다 */
  heard_message_ids?: string[];
  /** 내 차례를 지켜 주는 끝 시각. 지나면 동료가 이어 말한다 */
  turn_until_at?: string;
  /** 마감이 가까워 이번 녹음이 발언 한도보다 짧을 때만: 말할 수 있는 초 */
  speech_limit_seconds?: number;
  /** 손들기·강제 차례 */
  priority?: "hand" | "forced";
  /** 동료 차례 중 든 손(내리면 없음) */
  hand?: unknown;
};
/** 지금부터 서버 시각 기준으로 남은 초 */
function secondsUntil(snap: VoiceSnapshot, at?: string): number {
  if (!at) return 0;
  return Math.max(0, (Date.parse(at) - Date.parse(snap.server_now)) / 1000);
}
/** 안내(진행을 막지 않는 정보)를 보여 주는 시간(ms). 오류와 달리 저절로 사라진다 */
const NOTICE_MS = 6000;
const DELIVERED_NOTICE_MS = 3000;
export function scheduleNotice(
  text: string,
  duration: number,
  set: (update: (now: string) => string) => void,
): ReturnType<typeof setTimeout> {
  return setTimeout(() => set((now) => (now === text ? "" : now)), duration);
}
/** 단계(또는 세션)가 끝나 서버가 이 차례의 녹음·제출을 받지 않는 경우 */
function expired(e: unknown): boolean {
  return e instanceof ApiError && e.code === "voice_turn_expired";
}
/** 서버가 이미 다음 차례(단계 전환 등)로 넘어가 이 요청을 받지 않는 경우(409). 다음 조회에서 새 차례를 받는다 */
function turnMoved(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409;
}
/** 서버가 정한 다시 시도 횟수를 다 쓴 경우 */
function exhausted(e: unknown): boolean {
  return e instanceof ApiError && e.code === "voice_retry_exhausted";
}
const CANDIDATE_STATES = new Set(["USER_READY", "USER_SPEAKING", "TRANSCRIBING", "PROCESSING"]);
/** 현재 재생과 마지막 표시 화자를 분리해 재생 사이에도 큰 칸을 유지한다. */
export function displayedSeat(
  state: string | undefined,
  active: Seat | null,
  lastColleague: Seat | null,
  awaitingSaved = false,
): Seat | "candidate" | null {
  if (["USER_SPEAKING", "TRANSCRIBING", "PROCESSING"].includes(state ?? "") || awaitingSaved)
    return "candidate";
  return active ?? lastColleague;
}
/** 전달 대기(내 칸 유지)를 풀 때: 차례·연결·단계가 바뀌었거나 동료 재생·내 차례로 넘어감.
 * 전달 직후 발언(utterance)이 먼저 사라지고 메시지가 아직 안 온 순간에는 풀지 않는다(큰 칸 깜빡임). */
export function shouldResetAwaitingSaved(
  previous: VoiceSnapshot | null,
  next: VoiceSnapshot,
  phaseChanged: boolean,
): boolean {
  return (
    phaseChanged ||
    (!!previous && (previous.turn_id !== next.turn_id || previous.epoch !== next.epoch)) ||
    next.state === "AI_SPEAKING" ||
    next.state === "USER_READY"
  );
}
export function showRaisedHand(hand: unknown, priority?: "hand" | "forced"): boolean {
  return !!hand && priority !== "forced";
}
// 음성 상태 확인 주기(ms). 평소 750(계약 docs/contracts/voice.md), 기다리는 상태에서는 250.
// 작게 하면 준비된 발언을 더 빨리 알지만 서버 요청이 늘어난다(세션당 초당 4회).
const POLL_MS = 750;
const WAITING_POLL_MS = 250;
const WAITING_STATES = new Set(["AI_GENERATING", "TRANSCRIBING", "PROCESSING"]);
type Props = {
  enabled?: boolean;
  auth: SessionAuth;
  snap: StateSnapshot;
  connected: boolean;
  locked: boolean;
  onSend: (id: string, text: string, turn: string, epoch: string) => void;
  onBusy: (busy: boolean) => void;
  onFocusProposal: () => void;
  /** 연결이 끊겨 다시 잇는 중(처음 연결 중과 구분해 끊김 상태를 보인다) */
  reconnecting?: boolean;
};
const LABEL: Record<string, string> = {
  // 다음에 누가 말할지는 미리 보이지 않는다(회의처럼 말을 시작해야 알 수 있다)
  AI_GENERATING: "동료 차례예요",
  AI_SPEAKING: "동료의 발언을 듣고 있어요",
  USER_READY: "내 차례예요",
  USER_SPEAKING: "발언 중",
  TRANSCRIBING: "발언을 정리하고 있어요",
  PROCESSING: "발언을 전달하고 있어요",
};
/** 차례 유지 남은 시간을 크게 줄이는 막대. 5초 이하면 경고 색 */
function Countdown({
  seconds,
  total,
  label,
  valueText,
}: {
  seconds: number;
  total: number;
  /** 큰 숫자 뒤에 붙는 문장("… 안에 시작하지 않으면 …") */
  label: string;
  /** 화면 읽기 도구용 전체 문장 */
  valueText: string;
}) {
  const left = Math.ceil(seconds);
  const ratio = total > 0 ? Math.min(1, Math.max(0, seconds / total)) : 0;
  const warn = left <= 5;
  return (
    <div className="flex w-full flex-col gap-1.5">
      <div className="flex items-baseline justify-center gap-2">
        <span
          aria-hidden
          className={cn(
            "text-title2 font-semibold tabular-nums",
            warn ? "text-warning" : "text-ink",
          )}
        >
          {left}
          <span className="ml-0.5 text-callout font-medium">초</span>
        </span>
        <span aria-hidden className="text-footnote text-muted">
          {label}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label="내 차례 남은 시간"
        aria-valuemin={0}
        aria-valuemax={Math.round(total)}
        aria-valuenow={Math.min(left, Math.round(total))}
        aria-valuetext={valueText}
        className="h-1.5 w-full overflow-hidden rounded-full bg-surface-sunken"
      >
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-700 ease-linear motion-reduce:transition-none",
            warn ? "bg-warning" : "bg-accent",
          )}
          style={{ width: `${ratio * 100}%` }}
        />
      </div>
    </div>
  );
}

/** 진행을 막는 오류: 무엇이 안 됐는지(제목)·어떻게 하면 되는지와 바로 아래 해결 버튼 */
export function VoiceAlert({
  trouble,
  disabled,
  onAction,
}: {
  trouble: Trouble;
  disabled?: boolean;
  onAction: (action: Action) => void;
}) {
  return (
    <div role="alert" className="flex flex-col gap-2 rounded-xl bg-danger-soft p-3">
      <p className="flex items-center gap-1.5 text-callout font-semibold text-danger">
        <TriangleAlert size={16} aria-hidden className="shrink-0" />
        {trouble.title}
      </p>
      <p className="text-footnote text-ink">{trouble.detail}</p>
      {trouble.action && (
        <Button
          className="self-start"
          disabled={disabled}
          onClick={() => onAction(trouble.action!)}
        >
          {ACTION_LABEL[trouble.action]}
        </Button>
      )}
    </div>
  );
}

/** 진행을 막지 않는 안내 한 줄(회색) */
function InfoLine({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="flex items-start gap-1.5 text-footnote text-muted">
      <Info size={14} aria-hidden className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

export function VoiceNotices({ notice, systemNotice }: { notice: string; systemNotice: string }) {
  return (
    <>
      {notice && <InfoLine>{notice}</InfoLine>}
      {systemNotice && <InfoLine>{systemNotice}</InfoLine>}
    </>
  );
}

export function newSystemNotice(messages: Message[], seen: Set<string>): string {
  let latest = "";
  for (const message of messages) {
    if (message.sender !== "system" || seen.has(message.id)) continue;
    seen.add(message.id);
    latest = message.content;
  }
  return latest;
}

export function candidateMessageSaved(messages: Message[], utteranceId: string): boolean {
  return (
    !!utteranceId &&
    messages.some((m) => m.sender === "candidate" && m.client_msg_id === utteranceId)
  );
}

/** 빈 인식 결과는 글 대신 다시 말하기를 안내한다. */
export function EmptyRecognition({
  onRerecord,
  disabled = false,
  tooLong = false,
}: {
  onRerecord: () => void;
  disabled?: boolean;
  tooLong?: boolean;
}) {
  return (
    <div role="alert" className="flex flex-col items-start gap-2">
      <p>
        {tooLong
          ? "말이 길어 전하지 못했어요. 짧게 다시 말해 주세요"
          : "잘 들리지 않았어요. 다시 말해 주세요"}
      </p>
      <Button variant="secondary" disabled={disabled} onClick={onRerecord}>
        다시 말하기
      </Button>
    </div>
  );
}

/** 차례 유지 시간의 기본값(초). 서버가 시작 시각(ready_at)을 주지 않을 때 막대 길이에 쓴다 */
const DEFAULT_HOLD_SECONDS = 20;

/** 음성 토론의 차례·녹음·재생·확인 동작. 화면 조각과 상태를 돌려주고, 배치는 토론 화면(debate-view)이 정한다. */
export function useVoiceTurn(props: Props) {
  const latest = useRef(props);
  useEffect(() => {
    latest.current = props;
  }, [props]);
  const [voice, setVoice] = useState<VoiceSnapshot | null>(null);
  const [error, setError] = useState<Trouble | null>(null);
  const [notice, setNotice] = useState("");
  const [awaitingSaved, setAwaitingSaved] = useState(false);
  const [systemNotice, setSystemNotice] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [active, setActive] = useState<string | null>(null);
  const [lastDisplayed, setLastDisplayed] = useState<Seat | null>(null);
  const [recentCaption, setRecentCaption] = useState<{ seat: Seat; text: string } | null>(null);
  const seenSystemIds = useRef(
    new Set(props.snap.messages.filter((m) => m.sender === "system").map((m) => m.id)),
  );
  const submittedId = useRef("");
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const systemNoticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const phaseRef = useRef(props.snap.session.phase);
  const voiceRef = useRef<VoiceSnapshot | null>(null);
  // 녹음 경과·한도(초)
  const [clock, setClock] = useState({ elapsed: 0, total: 60 });
  // 녹음 중 마이크 소리 크기(파형·내 프로필 빛). 화면 전체를 다시 그리지 않게 따로 구독한다
  const [mic] = useState(() => new MicLevel());
  // 다시 시도 횟수를 다 쓴 차례. 그 차례에는 다시 녹음 버튼 대신 다음 차례 안내만 보인다
  const [exhaustedTurn, setExhaustedTurn] = useState("");
  // 녹음 중에 연결이 끊겼다가 다시 이어졌는지(그동안의 발언은 저장되지 않음)
  const [droppedTurn, setDroppedTurn] = useState("");
  const [micPending, setMicPending] = useState(false);
  const finishButton = useRef<HTMLButtonElement | null>(null);
  const focusFinish = useRef(false);
  const [heardIds, setHeardIds] = useState<string[]>([]);
  // 지금 재생 중인 동료 발언과, 대화 기록에 보여 줄 문장들(재생 위치까지)
  const [speaking, setSpeaking] = useState<{ id: string; text: string } | null>(null);
  // 서버가 전사한 글은 화면에 보이지 않고 바로 제출한다.
  const edit = useRef<{ id: string; text: string }>({ id: "", text: "" });
  const stream = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const player = useRef<HTMLAudioElement | null>(null);
  const busy = useRef(false);
  const waitingMic = useRef(false);
  const currentTurn = useRef("");
  const finish = useRef<() => void>(() => {});
  const pendingAudio = useRef<Record<string, unknown> | null>(null);
  const pending = useRef<{
    id: string;
    text: string;
    turn: string;
    epoch: string;
    sent: number;
  } | null>(null);
  const operation = useRef(0);
  const fence = useRef("");
  const lastTurn = useRef("");
  const cleanupRecorder = useRef<() => void>(() => {});
  const path = `/sessions/${props.auth.sessionId}/voice`;
  const token = () => latest.current.auth.getToken();

  const showNotice = useCallback((text: string, duration = NOTICE_MS) => {
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    setNotice(text);
    noticeTimer.current = scheduleNotice(text, duration, setNotice);
  }, []);

  useEffect(() => {
    if (phaseRef.current !== props.snap.session.phase) {
      phaseRef.current = props.snap.session.phase;
      setAwaitingSaved(false);
      setSystemNotice("");
      if (systemNoticeTimer.current) clearTimeout(systemNoticeTimer.current);
    }
  }, [props.snap.session.phase]);

  useEffect(
    () => () => {
      if (noticeTimer.current) clearTimeout(noticeTimer.current);
      if (systemNoticeTimer.current) clearTimeout(systemNoticeTimer.current);
    },
    [],
  );

  useEffect(() => {
    const fresh = newSystemNotice(props.snap.messages, seenSystemIds.current);
    if (fresh) {
      if (systemNoticeTimer.current) clearTimeout(systemNoticeTimer.current);
      setSystemNotice(fresh);
      systemNoticeTimer.current = setTimeout(
        () => setSystemNotice((now) => (now === fresh ? "" : now)),
        8000,
      );
    }
    if (candidateMessageSaved(props.snap.messages, submittedId.current)) {
      submittedId.current = "";
      setAwaitingSaved(false);
      showNotice("전달했어요", DELIVERED_NOTICE_MS);
    }
  }, [props.snap.messages, showNotice]);

  const stopMedia = useCallback(() => {
    operation.current++;
    cleanupRecorder.current();
    player.current?.pause();
    player.current = null;
    if (recorder.current?.state === "recording") {
      recorder.current.onstop = null;
      recorder.current.stop();
    }
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    recorder.current = null;
    busy.current = false;
    waitingMic.current = false;
  }, []);

  useEffect(() => {
    if (!props.connected || props.locked) {
      // 녹음 중에 끊기면 그 녹음은 버려진다. 다시 이어지면 다시 말해 달라고 알린다
      const dropped = !props.locked && recorder.current?.state === "recording";
      const turn = currentTurn.current || "dropped";
      stopMedia();
      // 연결 변경은 미디어를 즉시 닫고, 버튼 상태는 다음 작업에서 갱신한다.
      const reset = window.setTimeout(() => {
        setMicPending(false);
        if (dropped) setDroppedTurn(turn);
      }, 0);
      return () => {
        window.clearTimeout(reset);
        stopMedia();
      };
    }
    return () => {
      stopMedia();
    };
  }, [props.connected, props.locked, stopMedia]);

  useEffect(() => {
    const held = !!voice && ["USER_SPEAKING", "TRANSCRIBING", "PROCESSING"].includes(voice.state);
    latest.current.onBusy(held);
  }, [voice]);

  const record = useCallback(
    async (snap: VoiceSnapshot) => {
      if (busy.current || snap.turn_id === lastTurn.current) return;
      if (noticeTimer.current) clearTimeout(noticeTimer.current);
      setNotice((now) => (now === "전달했어요" ? "" : now));
      busy.current = true;
      waitingMic.current = true;
      setMicPending(true);
      const op = operation.current;
      try {
        const input = await requestMicrophone({
          audio: { channelCount: 1, echoCancellation: true },
          video: false,
        });
        if (op !== operation.current || currentTurn.current !== snap.turn_id) {
          input.getTracks().forEach((t) => t.stop());
          if (op === operation.current) {
            busy.current = false;
            waitingMic.current = false;
            setMicPending(false);
          }
          return;
        }
        waitingMic.current = false;
        setMicPending(false);
        setDroppedTurn("");
        stream.current = input;
        const start = await api<VoiceSnapshot>(`${path}/capture`, {
          method: "POST",
          token: token(),
          body: { turn_id: snap.turn_id, epoch: snap.epoch, restart: Boolean(snap.blocked_reason) },
        });
        if (op !== operation.current) {
          input.getTracks().forEach((t) => t.stop());
          return;
        }
        lastTurn.current = start.turn_id;
        setVoice(start);
        setAwaitingSaved(false);
        setRecentCaption(null);
        const duration = Math.max(
          0,
          Math.min(
            60,
            (Date.parse(start.speech_deadline_at!) - Date.parse(start.speech_started_at!)) / 1000,
          ),
        );
        const r = new MediaRecorder(input);
        recorder.current = r;
        const chunks: Blob[] = [];
        let reason = "button";
        r.ondataavailable = (event) => {
          if (event.data.size) chunks.push(event.data);
        };
        const started = performance.now();
        setClock({ elapsed: 0, total: Math.round(duration) });
        const timer = setInterval(
          () =>
            setClock({
              elapsed: Math.min(duration, (performance.now() - started) / 1000),
              total: Math.round(duration),
            }),
          200,
        );
        const end = setTimeout(() => {
          reason = "timeout";
          finish.current();
        }, duration * 1000);
        const audioContext = new AudioContext();
        const analyser = audioContext.createAnalyser();
        const source = audioContext.createMediaStreamSource(input);
        source.connect(analyser);
        // 말하는 동안 받아 적기. 조각 보내기를 못 열면(브라우저 미지원 등) 완성 파일만 보낸다.
        let live: PcmStream | null = null;
        try {
          live = await startPcmStream(audioContext, source, (seq, pcm) =>
            api(`${path}/stream`, {
              method: "POST",
              token: token(),
              body: {
                turn_id: start.turn_id,
                epoch: start.epoch,
                utterance_id: start.utterance_id,
                seq,
                pcm_base64: pcm,
              },
            }),
          );
        } catch {
          live = null;
        }
        const samples = new Uint8Array(analyser.fftSize);
        mic.setRecording(true);
        const meter = setInterval(() => {
          analyser.getByteTimeDomainData(samples);
          mic.push(samples);
        }, 80);
        cleanupRecorder.current = () => {
          clearInterval(timer);
          clearInterval(meter);
          mic.setRecording(false);
          clearTimeout(end);
          live?.stop();
          if (audioContext.state !== "closed") void audioContext.close();
        };
        r.onstop = async () => {
          // 처리기를 닫기 전에 남은 소리를 마지막 조각으로 보낸다(완성 파일보다 먼저 도착해야 함)
          const flushed = live?.flush() ?? Promise.resolve();
          cleanupRecorder.current();
          input.getTracks().forEach((t) => t.stop());
          recorder.current = null;
          if (op !== operation.current) return;
          setVoice({ ...start, state: "TRANSCRIBING" });
          try {
            const audio = await normalize(new Blob(chunks, { type: r.mimeType }), duration);
            if (op !== operation.current) return;
            pendingAudio.current = {
              turn_id: start.turn_id,
              epoch: start.epoch,
              utterance_id: start.utterance_id,
              audio_base64: audio,
              stop_reason: reason,
            };
            await flushed;
            await api(`${path}/audio`, {
              method: "POST",
              token: token(),
              body: pendingAudio.current,
            });
            pendingAudio.current = null;
          } catch (e) {
            if (expired(e)) {
              // 단계가 이미 끝나 이 녹음은 들어갈 수 없다. 오류로 멈추지 않고 다음 진행을 이어 간다
              pendingAudio.current = null;
              showNotice("단계가 끝나 이번 발언은 제출되지 않았어요.");
              return;
            }
            if (exhausted(e)) {
              pendingAudio.current = null;
              setExhaustedTurn(start.turn_id);
              return;
            }
            if (!pendingAudio.current)
              await api(`${path}/abort`, {
                method: "POST",
                token: token(),
                body: {
                  turn_id: start.turn_id,
                  epoch: start.epoch,
                },
              }).catch(() => {});
            setError({
              title: "발언을 보내지 못했어요",
              detail: pendingAudio.current
                ? "방금 한 말은 남아 있어요. 연결을 확인하고 다시 보내 주세요."
                : "연결과 마이크를 확인하고 다시 말해 주세요.",
              action: pendingAudio.current ? "resend" : "rerecord",
            });
          } finally {
            busy.current = false;
          }
        };
        finish.current = finishOnce(() => {
          if (r.state === "recording") r.stop();
        });
        r.onerror = () => {
          setError({
            title: "발언이 끊겼어요",
            detail: "마이크 연결을 확인하고 다시 말해 주세요.",
            action: "rerecord",
          });
          finish.current();
        };
        r.start();
      } catch (e) {
        if (op !== operation.current) return;
        waitingMic.current = false;
        setMicPending(false);
        stream.current?.getTracks().forEach((t) => t.stop());
        busy.current = false;
        if (expired(e)) showNotice("단계 마감이 가까워 이번 단계에서는 더 말할 수 없어요.");
        else if (exhausted(e)) setExhaustedTurn(snap.turn_id);
        else
          setError(
            isMicrophoneError(e)
              ? { title: "마이크를 쓸 수 없어요", detail: MICROPHONE_HELP, action: "rerecord" }
              : {
                  title: "발언을 시작하지 못했어요",
                  detail: "연결을 확인하고 다시 말해 주세요.",
                  action: "rerecord",
                },
          );
      }
    },
    [path, showNotice, mic],
  );

  const play = useCallback(
    async (snap: VoiceSnapshot) => {
      if (busy.current || !snap.items.length) return;
      const item = snap.items[0];
      // 서버는 맨 앞 발언이 준비되면 재생 상태가 된다. 다음 발언을 아직 만드는 중이면 기다린다.
      if (item.status === "queued" || item.status === "processing") return;
      if (item.status !== "ready") {
        setError({
          title: "동료 음성을 준비하지 못했어요",
          detail: "다시 재생을 눌러 주세요.",
          action: "replay",
        });
        return;
      }
      busy.current = true;
      const op = operation.current;
      let url: string | null = null;
      const result = (outcome: string) =>
        api(`${path}/playback`, {
          method: "POST",
          token: token(),
          body: { turn_id: snap.turn_id, epoch: snap.epoch, job_id: item.id, outcome },
        });
      try {
        const response = await fetch(`${API_URL}${path}/assets/${item.id}`, {
          headers: { Authorization: `Bearer ${token()}` },
        });
        if (!response.ok) throw new Error("asset");
        url = URL.createObjectURL(await response.blob());
        if (op !== operation.current) return;
        const sound = new Audio(url);
        player.current = sound;
        setRecentCaption(null);
        // 재생 위치에 맞춰 현재 자막에 문장을 하나씩 더한다
        const message = latest.current.snap.messages.find((m) => m.id === item.message_id);
        const parts = sentences(message?.content ?? "");
        const show = (count: number) => {
          const text = parts.slice(0, count).join(" ");
          setSpeaking((prev) =>
            prev?.id === item.message_id && prev.text === text
              ? prev
              : { id: item.message_id, text },
          );
        };
        let started = false;
        sound.ontimeupdate = () => {
          if (started && sound.duration)
            show(spokenCount(parts, sound.currentTime / sound.duration));
        };
        await result("start");
        await new Promise<void>((resolve, reject) => {
          const timeout = setTimeout(() => {
            sound.pause();
            reject(new Error("playback timeout"));
          }, item.duration_ms + 10000);
          sound.onended = () => {
            clearTimeout(timeout);
            resolve();
          };
          sound.onerror = () => {
            clearTimeout(timeout);
            reject(new Error("playback"));
          };
          sound
            .play()
            .then(() => {
              started = true;
              setActive(item.seat);
              setLastDisplayed(item.seat as Seat);
              show(1);
            })
            .catch((e) => {
              clearTimeout(timeout);
              reject(e);
            });
        });
        if (op === operation.current) {
          await result("ended");
          if (parts.length) setRecentCaption({ seat: item.seat as Seat, text: parts.at(-1)! });
        }
      } catch (e) {
        // 재생 중 단계가 바뀌어 서버가 이 차례를 닫았으면 오류가 아니다. 멈추지 않고 새 차례로 넘어간다
        if (op === operation.current && !turnMoved(e)) {
          await result("error").catch(() => {});
          setError({
            title: "동료 음성을 재생하지 못했어요",
            detail: "스피커 연결을 확인하고 다시 재생을 눌러 주세요.",
            action: "replay",
          });
        }
      } finally {
        if (url) URL.revokeObjectURL(url);
        if (op === operation.current) {
          busy.current = false;
          setActive(null);
          setSpeaking(null);
          player.current = null;
        }
      }
    },
    [path],
  );

  useEffect(() => {
    if (props.enabled === false || !props.connected || props.locked) return;
    let disposed = false;
    let polling = false;
    let lastPoll = 0;
    let lastState = "";
    const poll = async () => {
      if (polling) return;
      polling = true;
      lastPoll = Date.now();
      try {
        const next = await api<VoiceSnapshot>(path, { token: token() });
        if (disposed) return;
        lastState = next.state;
        if (
          shouldResetAwaitingSaved(
            voiceRef.current,
            next,
            phaseRef.current !== props.snap.session.phase,
          )
        )
          setAwaitingSaved(false);
        voiceRef.current = next;
        currentTurn.current = next.turn_id;
        const nextFence = `${next.generation}:${next.epoch}`;
        if (fence.current && nextFence !== fence.current) stopMedia();
        fence.current = nextFence;
        const ids = next.heard_message_ids ?? [];
        setHeardIds((prev) => (prev.join() === ids.join() ? prev : ids));
        // 녹음 종료/업로드 중에는 서버의 직전 USER_SPEAKING 스냅샷을 표시하지 않는다.
        if (!busy.current || waitingMic.current || player.current) setVoice(next);
        const u = next.utterance;
        // 발언이 사라졌거나 단계가 끝나면 로컬 전사도 지운다.
        if (
          edit.current.id &&
          (u?.id !== edit.current.id || next.blocked_reason === "proposal_phase")
        )
          edit.current = { id: "", text: "" };
        if (
          u &&
          u.id !== edit.current.id &&
          (u.status === "ready" || u.status === "empty") &&
          next.blocked_reason !== "proposal_phase"
        ) {
          edit.current = { id: u.id, text: u.transcript ?? "" };
        }
        // 음성 화면은 확인 시간과 관계없이 인식된 글을 바로 제출한다.
        // 상한 초과·빈 인식은 다시 말하기를 안내한다.
        if (
          u &&
          u.id === edit.current.id &&
          (u.status === "ready" || u.status === "empty") &&
          edit.current.text.trim() &&
          edit.current.text.trim().length <= CANDIDATE_MESSAGE_MAX_CHARS
        ) {
          pending.current = {
            id: u.id,
            text: edit.current.text,
            turn: next.turn_id,
            epoch: next.epoch,
            sent: pending.current?.sent ?? 0,
          };
        }
        if (u?.message_id || next.state === "AI_GENERATING") pending.current = null;
        if (pending.current && Date.now() - pending.current.sent > 3000) {
          const p = pending.current;
          p.sent = Date.now();
          setVoice({ ...next, state: "PROCESSING" });
          submittedId.current = p.id;
          setAwaitingSaved(true);
          latest.current.onSend(p.id, p.text, p.turn, p.epoch);
        }
        // 녹음은 자동으로 시작하지 않는다. 일반·손들기·강제 차례 모두 [발언하기]를 눌러 시작한다.
        if (enabled && !error && !busy.current && next.epoch && next.state === "AI_SPEAKING")
          void play(next);
      } catch {
        if (!disposed)
          setError({
            title: "음성 연결을 확인하지 못했어요",
            detail: "인터넷 연결을 확인하고 다시 연결을 눌러 주세요.",
            action: "reconnect",
          });
      } finally {
        polling = false;
      }
    };
    void poll();
    // 동료 답·받아 적기를 기다리는 동안은 자주 확인해 준비되자마자 재생·제출한다(평소 주기는 그대로)
    const timer = setInterval(() => {
      const waiting = WAITING_STATES.has(lastState);
      if (waiting || Date.now() - lastPoll >= POLL_MS) void poll();
    }, WAITING_POLL_MS);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [
    path,
    props.enabled,
    props.connected,
    props.locked,
    props.snap.session.phase,
    enabled,
    error,
    play,
    stopMedia,
  ]);

  /** 오류를 푸는 버튼 하나의 동작(버튼 이름 ACTION_LABEL과 같은 일을 한다) */
  const resume = async (action: Action) => {
    setError(null);
    setEnabled(true);
    lastTurn.current = "";
    if (action === "resend") {
      if (!pendingAudio.current || !voice) return;
      if (pendingAudio.current.turn_id !== voice.turn_id) {
        pendingAudio.current = null;
        showNotice("발언 차례가 바뀌어 방금 한 말은 전하지 못했어요. 다음 차례에 말해 주세요.");
        return;
      }
      try {
        await api(`${path}/audio`, {
          method: "POST",
          token: token(),
          body: { ...pendingAudio.current, epoch: voice.epoch },
        });
        pendingAudio.current = null;
      } catch (e) {
        if (expired(e) || turnMoved(e)) {
          pendingAudio.current = null;
          showNotice("단계가 끝나 이번 발언은 제출되지 않았어요.");
          return;
        }
        setError({
          title: "발언을 다시 보내지 못했어요",
          detail: "방금 한 말은 남아 있어요. 연결을 확인하고 다시 보내 주세요.",
          action: "resend",
        });
      }
      return;
    }
    if (action === "replay" && voice?.state === "AI_SPEAKING" && voice.items?.[0]) {
      await api(`${path}/playback`, {
        method: "POST",
        token: token(),
        body: {
          turn_id: voice.turn_id,
          epoch: voice.epoch,
          job_id: voice.items[0].id,
          outcome: "retry",
        },
      }).catch((e) =>
        setError(
          exhausted(e)
            ? {
                title: "동료 음성을 더 다시 재생할 수 없어요",
                detail:
                  "다시 시도할 수 있는 횟수를 다 썼어요. 연결을 확인하고 다시 연결을 눌러 주세요.",
                action: "reconnect",
              }
            : {
                title: "동료 음성을 다시 재생하지 못했어요",
                detail: "연결을 확인하고 다시 재생을 눌러 주세요.",
                action: "replay",
              },
        ),
      );
      return;
    }
    if (action === "retranscribe" && voice?.utterance?.status === "error") {
      await api(`${path}/retry`, {
        method: "POST",
        token: token(),
        body: {
          turn_id: voice.turn_id,
          epoch: voice.epoch,
          job_id: voice.utterance.id,
        },
      }).catch((e) =>
        setError(
          exhausted(e)
            ? {
                title: "더 확인할 수 없어요",
                detail: "방금 한 말로 다시 시도할 수 있는 횟수를 다 썼어요. 다시 말해 주세요.",
                action: "rerecord",
              }
            : expired(e)
              ? null
              : {
                  title: "발언을 다시 확인하지 못했어요",
                  detail: "연결을 확인하고 다시 말해 주세요.",
                  action: "rerecord",
                },
        ),
      );
      return;
    }
    if (action === "rerecord" && voice?.utterance?.status === "ready") {
      try {
        const next = await api<VoiceSnapshot>(`${path}/rerecord`, {
          method: "POST",
          token: token(),
          body: { turn_id: voice.turn_id, epoch: voice.epoch, job_id: voice.utterance.id },
        });
        setVoice(next);
        focusFinish.current = true;
        void record(next);
      } catch (e) {
        if (exhausted(e)) setExhaustedTurn(voice.turn_id);
        else if (!expired(e) && !turnMoved(e))
          setError({
            title: "다시 말하기를 시작하지 못했어요",
            detail: "연결을 확인하고 다시 눌러 주세요.",
            action: "rerecord",
          });
      }
      return;
    }
    if ((action === "rerecord" || action === "start") && voice?.state === "USER_READY") {
      if (action === "rerecord") focusFinish.current = true;
      void record(voice);
    }
    // reconnect: 오류를 지우면 다음 확인(polling)이 이어서 재생·녹음을 맡는다
  };

  const raiseHand = async (snap: VoiceSnapshot, raised: boolean) => {
    try {
      const next = await api<VoiceSnapshot>(`${path}/hand`, {
        method: "POST",
        token: token(),
        body: {
          turn_id: snap.turn_id,
          epoch: snap.epoch,
          raised,
          during_message_id: speaking?.id ?? null,
          offset_ms: player.current ? Math.round(player.current.currentTime * 1000) : null,
        },
      });
      setVoice({ ...snap, ...next });
    } catch {
      // 손들기 버튼이 그대로 있으므로 진행을 막지 않고 알리기만 한다
      showNotice(
        raised
          ? "손들기를 전하지 못했어요. 다시 눌러 주세요."
          : "손을 내리지 못했어요. 다시 눌러 주세요.",
      );
    }
  };

  // 빈 인식과 상한 초과는 다시 말하기를 안내한다. 정상 전사는 화면에 노출하지 않는다.
  const review =
    !props.locked &&
    voice?.utterance?.status === "empty" &&
    !voice.utterance.message_id &&
    voice.state === "USER_READY"
      ? voice.utterance
      : null;
  const tooLong =
    !!voice?.utterance &&
    voice.utterance.status === "ready" &&
    !voice.utterance.message_id &&
    (voice.utterance.transcript?.trim().length ?? 0) > CANDIDATE_MESSAGE_MAX_CHARS;

  // 동료 발언은 재생이 끝난 것만 대화 기록에 전부 보이고, 재생 중인 발언은 말한 문장까지만
  // 보인다(목소리보다 글이 앞서지 않게, V5 들은 범위). 지원자 발언과 시스템 안내는 바로 보인다.
  const heard = useMemo(
    () => ({
      ...props.snap,
      messages: heardMessages(props.snap.messages, heardIds, speaking),
    }),
    [props.snap, heardIds, speaking],
  );
  const phase = props.snap.session.phase;
  // 토론 중(기획안 단계·종료 전)
  const debating = phase !== "proposal" && !props.locked;
  const exhaustedNow = !!voice && voice.turn_id === exhaustedTurn;
  // [발언하기]를 누를 수 있는 내 차례
  const canSpeak =
    debating &&
    !error &&
    voice?.state === "USER_READY" &&
    !voice.blocked_reason &&
    voice.speech_limit_seconds !== 0 &&
    !exhaustedNow;
  const startDisabled = !props.connected || micPending || !canStartVoice(phase);

  // Space로 [발언하기]. 상호작용 요소에 포커스가 있거나 대화상자가 열려 있으면 쓰지 않는다
  useEffect(() => {
    if (!canSpeak || startDisabled || !voice) return;
    const turn = voice;
    const onKey = (event: KeyboardEvent) => {
      if (event.code !== "Space" && event.key !== " ") return;
      if (event.repeat || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (spaceShortcutBlocked(event.target)) return;
      event.preventDefault();
      void record(turn);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canSpeak, startDisabled, voice, record]);

  const nameOf = (seat?: string | null) =>
    seat ? props.snap.participants.find((p) => p.id === seat)?.display_name : undefined;
  const speakerName = nameOf(active);
  const recording = debating && voice?.state === "USER_SPEAKING";
  useEffect(() => {
    if (recording && focusFinish.current && finishButton.current) {
      finishButton.current.focus();
      focusFinish.current = false;
    }
  }, [recording]);
  const disconnected = !props.locked && !props.connected && !!props.reconnecting;
  const statusText = disconnected
    ? "연결이 끊겼어요. 다시 연결하는 중…"
    : phase === "proposal" && !props.locked
      ? "토론이 끝났어요. 정한 내용으로 기획안을 써 주세요"
      : props.locked
        ? "토론이 종료됐어요"
        : !voice
          ? "회의 상태를 불러오는 중…"
          : review || tooLong
            ? "내 차례예요"
            : voice.state === "PROCESSING"
              ? "발언을 전달하는 중…"
              : voice.state === "AI_SPEAKING" && speakerName
                ? `${speakerName}님이 발언 중`
                : (LABEL[voice.state] ?? LABEL.AI_GENERATING);
  // 진행을 막는 오류: 화면에서 생긴 오류가 먼저, 없으면 서버가 막아 둔 내 차례
  const trouble =
    error ??
    (debating && voice && !exhaustedNow
      ? blockedTrouble(voice.blocked_reason, voice.state === "USER_READY")
      : null);
  // 녹음 중 끊겼다 다시 이어졌는데 서버가 막아 두지 않은 경우(이미 다음 차례 등)
  const droppedInfo =
    debating &&
    props.connected &&
    !!droppedTurn &&
    voice?.blocked_reason !== "voice_interrupted" &&
    (voice?.turn_id === droppedTurn || droppedTurn === "dropped");
  const holdLeft = voice ? secondsUntil(voice, voice.turn_until_at) : 0;
  const holdTotal =
    voice?.ready_at && voice.turn_until_at
      ? (Date.parse(voice.turn_until_at) - Date.parse(voice.ready_at)) / 1000
      : DEFAULT_HOLD_SECONDS;

  // 음성 칸에 들어갈 조작부. 프로필 강조·대화 기록은 토론 화면이 그린다.
  const controls = (
    <section aria-label="음성 회의" className="flex flex-col items-stretch gap-3">
      <p role="status" className="flex items-center gap-2 text-callout font-semibold">
        {recording && (
          <span
            aria-hidden
            className="size-2.5 shrink-0 animate-pulse rounded-full bg-danger motion-reduce:animate-none"
          />
        )}
        {disconnected && <WifiOff size={16} aria-hidden className="shrink-0 text-muted" />}
        <span className={cn(recording && "text-danger", disconnected && "text-muted")}>
          {statusText}
        </span>
        {recording && (
          <span aria-hidden className="font-medium text-danger tabular-nums">
            {elapsedText(clock.elapsed * 1000)} / {elapsedText(clock.total * 1000)}
          </span>
        )}
      </p>
      {debating && !voice && !disconnected && (
        <div aria-hidden className="flex flex-col items-center gap-2" data-testid="voice-loading">
          <div className="h-12 w-44 animate-pulse rounded-md bg-surface-sunken motion-reduce:animate-none" />
          <div className="h-3 w-60 animate-pulse rounded bg-surface-sunken motion-reduce:animate-none" />
        </div>
      )}
      {phase === "proposal" && !props.locked && (
        <Button variant="secondary" onClick={props.onFocusProposal}>
          기획안으로 이동
        </Button>
      )}
      {debating && <VoiceNotices notice={notice} systemNotice={systemNotice} />}
      {debating && exhaustedNow && (
        <InfoLine>다시 시도할 수 있는 횟수를 다 썼어요. 다음 차례에 말해 주세요.</InfoLine>
      )}
      {droppedInfo && <InfoLine>끊긴 동안의 발언은 저장되지 않았어요. 다시 말해 주세요.</InfoLine>}
      {debating &&
        !error &&
        voice?.state === "USER_READY" &&
        !voice.blocked_reason &&
        voice.speech_limit_seconds === 0 && (
          <InfoLine>단계 마감이 가까워 이번 단계에서는 더 말할 수 없어요.</InfoLine>
        )}
      {canSpeak && voice && (
        <div className="flex flex-col items-center gap-3">
          {voice.priority === "forced" && (
            <p className="font-semibold text-accent">
              이번 단계에 {nameOf("candidate") ?? "지원자"}님 의견도 들려주세요
            </p>
          )}
          <Button
            className="h-12 px-6 text-callout"
            disabled={startDisabled}
            onClick={() => void record(voice)}
          >
            {micPending ? "마이크 권한 확인 중…" : "발언하기"}
          </Button>
          {holdLeft > 0 ? (
            <Countdown
              seconds={holdLeft}
              total={holdTotal}
              label="안에 시작하지 않으면 동료가 이어서 말해요"
              valueText={turnHoldNotice(holdLeft)}
            />
          ) : (
            <p className="text-footnote text-muted">{turnHoldNotice(0)}</p>
          )}
          {voice.speech_limit_seconds !== undefined && (
            <InfoLine>
              단계 마감이 가까워 이번 발언은 최대 {voice.speech_limit_seconds}초예요.
            </InfoLine>
          )}
          {!startDisabled && (
            <p className="text-caption text-muted">
              <kbd className="rounded border border-line bg-surface-sunken px-1 font-sans">
                Space
              </kbd>{" "}
              키로도 시작할 수 있어요
            </p>
          )}
        </div>
      )}
      {debating &&
        !error &&
        (voice?.state === "AI_GENERATING" || voice?.state === "AI_SPEAKING") && (
          <div className="flex flex-col items-center gap-2">
            {voice.hand ? (
              <>
                <p className="text-footnote text-muted">
                  손 듦 · 지금 발언이 끝나면 말할 수 있어요
                </p>
                <Button variant="secondary" onClick={() => void raiseHand(voice, false)}>
                  손 내리기
                </Button>
              </>
            ) : (
              <Button variant="secondary" onClick={() => void raiseHand(voice, true)}>
                <Hand size={16} aria-hidden />
                손들기
              </Button>
            )}
          </div>
        )}
      {phase !== "proposal" && !error && (review || tooLong) && voice && !exhaustedNow && (
        <EmptyRecognition
          tooLong={tooLong}
          disabled={!props.connected}
          onRerecord={() => void resume("rerecord")}
        />
      )}
      {debating &&
        !error &&
        !exhaustedNow &&
        voice?.state === "USER_READY" &&
        voice.blocked_reason === "empty" &&
        !review && (
          <Button
            variant="secondary"
            className="self-start"
            disabled={!props.connected}
            onClick={() => void resume("rerecord")}
          >
            다시 말하기
          </Button>
        )}
      {recording && (
        <div className="flex flex-col items-stretch gap-3">
          <div className="flex h-12 items-center rounded-lg bg-danger-soft px-3">
            <LevelBars voice={mic} className="h-8 w-full justify-between" />
          </div>
          <button
            ref={finishButton}
            type="button"
            onClick={() => finish.current()}
            className="press flex h-14 items-center justify-center gap-2 rounded-xl border-2 border-danger bg-surface text-callout font-semibold text-danger hover:bg-danger-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-danger"
          >
            <Square size={16} fill="currentColor" aria-hidden />
            발언 완료
          </button>
          <p className="text-center text-footnote text-muted">시간이 끝나면 자동으로 전달해요</p>
        </div>
      )}
      {debating && trouble && (
        <VoiceAlert
          trouble={trouble}
          disabled={!props.connected}
          onAction={(action) => void resume(action)}
        />
      )}
      {!enabled && debating && (
        <Button disabled={!props.connected} onClick={() => void resume("start")}>
          음성 참여 시작
        </Button>
      )}
    </section>
  );

  return {
    /** 음성 칸에 넣을 조작부 */
    controls,
    /** 지금 목소리가 나오는 동료 자리(프로필 강조용) */
    speakingSeat: active as Seat | null,
    featuredSeat: displayedSeat(
      voice?.state,
      active as Seat | null,
      lastDisplayed,
      awaitingSaved && !active,
    ),
    hand: showRaisedHand(voice?.hand, voice?.priority),
    /** 들은 만큼만 보이는 대화(토론방용) */
    chatSnap: heard,
    currentCaption: speaking,
    recentCaption:
      recentCaption?.seat === displayedSeat(undefined, active as Seat | null, lastDisplayed)
        ? recentCaption.text
        : "",
    /** 지원자 차례가 한 번이라도 왔는지·지금 녹음 중인지(칸 펼치기·프로필 강조용) */
    candidateTurn: !!voice && CANDIDATE_STATES.has(voice.state),
    recording: voice?.state === "USER_SPEAKING",
    /** 녹음 중 마이크 소리 크기(내 프로필 빛) */
    mic,
  };
}
