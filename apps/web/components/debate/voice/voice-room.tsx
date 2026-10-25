"use client";

import { Info, Square, TriangleAlert, WifiOff } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { API_URL, ApiError, api } from "@/lib/api";
import { Button, cn } from "@/components/ui";
import { CANDIDATE_MESSAGE_MAX_CHARS } from "../state";
import type { Seat, StateSnapshot } from "../types";
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
import { HandSymbol, type SeatStatus } from "./status-symbols";
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
    /** 이 발언을 제출할 수 있는 마지막 시각(ready·empty). 지나면 발언이 버려진다 */
    submit_deadline_at?: string;
  };
  /** 이 차례에서 다시 녹음을 이미 썼는지 */
  rerecorded?: boolean;
  /** 재생이 끝난 동료 발언 id. 상태 조회(GET)에만 있다 */
  heard_message_ids?: string[];
  /** 녹음을 자동으로 시작할 시각(손들기·강제 차례는 생각 시간 뒤). 일반 차례는 없음(직접 누름) */
  think_deadline_at?: string;
  /** 내 차례를 지켜 주는 끝 시각. 지나면 동료가 이어 말한다 */
  turn_until_at?: string;
  /** 마감이 가까워 이번 녹음이 발언 한도보다 짧을 때만: 말할 수 있는 초 */
  speech_limit_seconds?: number;
  /** 손들기·강제 차례 */
  priority?: "hand" | "forced";
  /** 동료 차례 중 든 손(내리면 없음) */
  hand?: unknown;
};
/** 받아 적은 글 확인 시간이 남은 초(서버 시각 기준). 확인 시간이 없으면 0 */
function confirmLeft(snap: VoiceSnapshot): number {
  const deadline = snap.utterance?.confirm_deadline_at;
  if (!deadline) return 0;
  return Math.max(0, (Date.parse(deadline) - Date.parse(snap.server_now)) / 1000);
}
/** 지금부터 서버 시각 기준으로 남은 초 */
function secondsUntil(snap: VoiceSnapshot, at?: string): number {
  if (!at) return 0;
  return Math.max(0, (Date.parse(at) - Date.parse(snap.server_now)) / 1000);
}
/** 자동 녹음까지 남은 초. 일반 차례(직접 누름)면 0 */
function thinkLeft(snap: VoiceSnapshot): number {
  if (!snap.think_deadline_at) return 0;
  return Math.max(0, (Date.parse(snap.think_deadline_at) - Date.parse(snap.server_now)) / 1000);
}
const SEATS = new Set(["p1", "p2", "p3"]);
/** 최근 발언 칸에 두는 발언 수(오래된 것은 위로 흐려지며 스크롤로 본다) */
const TALK_LIMIT = 30;
/** 안내(진행을 막지 않는 정보)를 보여 주는 시간(ms). 오류와 달리 저절로 사라진다 */
const NOTICE_MS = 6000;
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
  /** 보낸 발언(id = client_msg_id)의 저장 확인(message.ack)을 받았는지 */
  wasSaved?: (id: string) => boolean;
  /** 마지막 /state 동기화 시각(ms). 재연결 뒤 따라잡기 전에는 미제출로 판단하지 않는다 */
  lastSyncedAt?: () => number;
  onBusy: (busy: boolean) => void;
  onFocusProposal: () => void;
  /** 연결이 끊겨 다시 잇는 중(처음 연결 중과 구분해 끊김 상태를 보인다) */
  reconnecting?: boolean;
};
const LABEL: Record<string, string> = {
  AI_GENERATING: "동료가 발언을 준비하고 있어요",
  AI_SPEAKING: "동료의 발언을 듣고 있어요",
  USER_READY: "내 차례예요",
  USER_SPEAKING: "녹음 중",
  TRANSCRIBING: "발언을 글로 옮기고 있어요",
  PROCESSING: "발언을 제출하고 있어요",
};
/** 차례 유지·자동 녹음까지 남은 시간을 크게 줄이는 막대. 5초 이하면 경고 색 */
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

/**
 * 받아 적은 글 확인. 수정하지 않은 인식 글만 확인 시간이 끝나면 자동 제출한다. 한 글자라도 고치거나
 * 인식 글이 비어 직접 쓰는 경우에는 자동 제출을 멈추고 [발언 제출]을 기다린다(되살리는 카운트다운 없음).
 * 빈 인식 결과는 서버가 다시 녹음(rerecord)을 받지 않으므로 확인 칸 밖의 [다시 녹음](새 녹음)으로 다시 말한다.
 */
export function TranscriptConfirm({
  text,
  changed,
  empty,
  seconds,
  submitSeconds,
  rerecorded,
  onChange,
  onSubmit,
  onRerecord,
}: {
  text: string;
  changed: boolean;
  empty: boolean;
  /** 자동 제출까지 남은 초 */
  seconds: number;
  /** 이 발언을 제출할 수 있는 남은 초(서버 submit_deadline_at 기준). 지나면 들어가지 않는다 */
  submitSeconds?: number;
  rerecorded: boolean;
  onChange: (text: string) => void;
  onSubmit: () => void;
  onRerecord: () => void;
}) {
  const left = submitSeconds !== undefined ? Math.ceil(submitSeconds) : undefined;
  const length = text.trim().length;
  const tooLong = length > CANDIDATE_MESSAGE_MAX_CHARS;
  const hint = empty
    ? left !== undefined
      ? `직접 입력해 [발언 제출]을 누르세요. ${left}초 안에 제출하지 않으면 동료가 이어서 말해요`
      : "직접 입력해 [발언 제출]을 누르세요"
    : changed
      ? left !== undefined
        ? `수정한 내용은 [발언 제출]을 눌러 보내세요. ${left}초 안에 보내지 않으면 이번 발언은 들어가지 않아요`
        : "수정한 내용은 [발언 제출]을 눌러 보내세요"
      : `${Math.ceil(seconds)}초 뒤 자동으로 제출돼요. 수정하면 자동 제출이 멈춰요`;
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor="voice-transcript" className="text-footnote font-medium">
        받아 적은 내용 (수정 가능)
      </label>
      <textarea
        id="voice-transcript"
        rows={3}
        value={text}
        maxLength={CANDIDATE_MESSAGE_MAX_CHARS}
        onChange={(event) => onChange(event.target.value)}
        aria-describedby="voice-transcript-hint voice-transcript-count"
        className="w-full rounded-xl border border-line bg-surface p-3"
        placeholder="인식된 말이 없어요. 직접 입력해 제출할 수 있어요."
      />
      <p
        id="voice-transcript-count"
        className={`self-end text-caption tabular-nums ${tooLong ? "font-semibold text-warning" : "text-muted"}`}
      >
        {length}/{CANDIDATE_MESSAGE_MAX_CHARS}자{tooLong ? " · 줄여야 제출할 수 있어요" : ""}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button disabled={!text.trim() || tooLong} onClick={onSubmit}>
          발언 제출
        </Button>
        {!rerecorded && !empty && (
          <Button variant="secondary" onClick={onRerecord}>
            다시 녹음 (1회)
          </Button>
        )}
      </div>
      <p id="voice-transcript-hint" className="text-footnote text-muted tabular-nums">
        {hint}
      </p>
    </div>
  );
}

/**
 * 차례가 끝나 제출하지 못한 발언. 고치거나 직접 쓴 글이 말없이 사라지지 않게 읽기 전용으로 남기고
 * 복사할 수 있게 한다. 지원자가 닫을 때까지 보인다.
 */
export function UnsentSpeech({ text, onDismiss }: { text: string; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div role="alert" className="flex flex-col gap-2 rounded-xl border border-warning/40 p-3">
      <p className="text-footnote font-semibold text-warning">제출하지 못한 발언이 있어요</p>
      <p className="text-footnote text-muted">
        차례가 끝나 아래 글은 토론에 들어가지 않았어요. 필요하면 복사해 다음 발언에 써 주세요.
      </p>
      <textarea
        readOnly
        rows={3}
        value={text}
        aria-label="제출하지 못한 발언"
        className="w-full rounded-xl border border-line bg-surface-sunken p-3"
      />
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => void copy()}>
          {copied ? "복사했어요" : "복사"}
        </Button>
        <Button variant="secondary" onClick={onDismiss}>
          닫기
        </Button>
      </div>
    </div>
  );
}

/**
 * 제출을 보낸 뒤 저장 확인을 기다리는 시간(ms). 지나도 확인이 없으면 제출하지 못한 것으로 본다.
 * 늘리면 느린 연결·재연결에서 잘못된 "제출하지 못한 발언" 안내가 줄지만, 정말 못 보낸 글을 늦게 알린다.
 */
const SENT_CONFIRM_MS = 15000;
const SENT_CHECK_MS = 500;
/** 손들기(차례 유지) 시간의 기본값(초). 서버가 시작 시각(ready_at)을 주지 않을 때 막대 길이에 쓴다 */
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
  const [enabled, setEnabled] = useState(true);
  const [active, setActive] = useState<string | null>(null);
  // 녹음 경과·한도(초)
  const [clock, setClock] = useState({ elapsed: 0, total: 60 });
  // 녹음 중 마이크 소리 크기(파형·내 프로필 빛). 화면 전체를 다시 그리지 않게 따로 구독한다
  const [mic] = useState(() => new MicLevel());
  // 다시 시도 횟수를 다 쓴 차례. 그 차례에는 다시 녹음 버튼 대신 다음 차례 안내만 보인다
  const [exhaustedTurn, setExhaustedTurn] = useState("");
  // 녹음 중에 연결이 끊겼다가 다시 이어졌는지(그동안의 발언은 저장되지 않음)
  const [droppedTurn, setDroppedTurn] = useState("");
  const [micPending, setMicPending] = useState(false);
  const [heardIds, setHeardIds] = useState<string[]>([]);
  // 지금 재생 중인 동료 발언과, 대화 기록에 보여 줄 문장들(재생 위치까지)
  const [speaking, setSpeaking] = useState<{ id: string; text: string } | null>(null);
  // [발언 제출]을 누른 발언 id. 확인 시간이 남아도 바로 제출한다(ref는 polling용, state는 화면용)
  const confirmed = useRef("");
  const [confirmedId, setConfirmedId] = useState("");
  // 확인 중인 발언의 글. changed는 지원자가 고치거나 직접 쓴 글인지
  const edit = useRef<{ id: string; text: string; changed: boolean }>({
    id: "",
    text: "",
    changed: false,
  });
  // message.send를 보낸 발언 id
  const sentIds = useRef(new Set<string>());
  // 서버가 저장했다고 알려 준 발언 id(음성 상태의 utterance.message_id)
  const savedIds = useRef(new Set<string>());
  // 마지막으로 연결이 끊긴 시각(ms). 그 뒤 /state 동기화가 있어야 미제출을 판단한다
  const droppedAt = useRef(0);
  // 차례가 끝나 제출하지 못한 글(읽기 전용으로 남김)
  const [unsent, setUnsent] = useState("");
  const [draft, setDraft] = useState("");
  const [draftChanged, setDraftChanged] = useState(false);
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

  /**
   * 확인 중이던 발언이 서버에서 사라졌을 때(제출·차례 종료·단계 전환·세션 종료) 한 번 부른다.
   * 지원자가 고치거나 직접 쓴 글만 본다(고치지 않은 인식 글은 다시 말하면 되므로 남기지 않는다).
   * 그 발언 id의 저장 확인(message.ack 또는 음성 상태의 message_id)이 없으면 제출하지 못한 글로 남긴다.
   * 보낸 발언은 느린 연결·재연결을 생각해 SENT_CONFIRM_MS 동안 확인을 기다린다.
   */
  const settleEdit = useCallback(() => {
    const e = edit.current;
    edit.current = { id: "", text: "", changed: false };
    if (!e.id || !e.text.trim() || !e.changed) return;
    const saved = () =>
      savedIds.current.has(e.id) ||
      !!latest.current.wasSaved?.(e.id) ||
      // ack를 놓쳐도(끊김) 다시 받은 스냅샷에 그 발언 id로 저장된 지원자 메시지가 있으면 저장된 것이다
      latest.current.snap.messages.some(
        (m) => m.sender === "candidate" && m.client_msg_id === e.id,
      );
    if (saved()) return;
    if (!sentIds.current.has(e.id)) {
      setUnsent(e.text);
      return;
    }
    const until = Date.now() + SENT_CONFIRM_MS;
    const check = () => {
      if (saved()) return;
      // 끊겨 있거나 다시 이어진 뒤 /state로 따라잡기 전에는 판단하지 않고 기다린다
      const synced =
        latest.current.connected &&
        (latest.current.lastSyncedAt?.() ?? Number.POSITIVE_INFINITY) > droppedAt.current;
      if (synced && Date.now() >= until) setUnsent(e.text);
      else setTimeout(check, SENT_CHECK_MS);
    };
    setTimeout(check, SENT_CHECK_MS);
  }, []);

  useEffect(() => {
    if (props.locked) settleEdit();
  }, [props.locked, settleEdit]);

  const showNotice = useCallback((text: string) => {
    setNotice(text);
    setTimeout(() => setNotice((now) => (now === text ? "" : now)), NOTICE_MS);
  }, []);

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
      if (!props.connected) droppedAt.current = Date.now();
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
                ? "녹음은 남아 있어요. 연결을 확인하고 다시 보내 주세요."
                : "연결과 마이크를 확인하고 다시 녹음해 주세요.",
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
            title: "녹음이 끊겼어요",
            detail: "마이크 연결을 확인하고 다시 녹음해 주세요.",
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
                  title: "녹음을 시작하지 못했어요",
                  detail: "연결을 확인하고 다시 녹음해 주세요.",
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
        setActive(item.seat);
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
              show(1);
            })
            .catch((e) => {
              clearTimeout(timeout);
              reject(e);
            });
        });
        if (op === operation.current) await result("ended");
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
        currentTurn.current = next.turn_id;
        const nextFence = `${next.generation}:${next.epoch}`;
        if (fence.current && nextFence !== fence.current) stopMedia();
        fence.current = nextFence;
        const ids = next.heard_message_ids ?? [];
        setHeardIds((prev) => (prev.join() === ids.join() ? prev : ids));
        // 녹음 종료/업로드 중에는 서버의 직전 USER_SPEAKING 스냅샷을 표시하지 않는다.
        if (!busy.current || waitingMic.current || player.current) setVoice(next);
        const u = next.utterance;
        if (u?.message_id) savedIds.current.add(u.id);
        // 확인 중이던 발언이 없어졌거나(제출·마감) 기획안 단계로 넘어갔으면 제출됐는지 확인한다
        if (
          edit.current.id &&
          (u?.id !== edit.current.id || next.blocked_reason === "proposal_phase")
        )
          settleEdit();
        if (
          u &&
          u.id !== edit.current.id &&
          (u.status === "ready" || u.status === "empty") &&
          next.blocked_reason !== "proposal_phase"
        ) {
          edit.current = { id: u.id, text: u.transcript ?? "", changed: false };
          setDraft(u.transcript ?? "");
          setDraftChanged(false);
        }
        // 수정을 시작하면 자동 제출하지 않는다. 빈 인식 결과도 직접 쓴 뒤 제출할 수 있다.
        // 글자 수 상한을 넘으면 서버가 받지 않으므로 줄일 때까지 보내지 않는다.
        const reviewed =
          confirmed.current === u?.id || (!edit.current.changed && confirmLeft(next) <= 0);
        if (
          u &&
          u.id === edit.current.id &&
          (u.status === "ready" || u.status === "empty") &&
          edit.current.text.trim() &&
          edit.current.text.trim().length <= CANDIDATE_MESSAGE_MAX_CHARS &&
          reviewed
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
          sentIds.current.add(p.id);
          latest.current.onSend(p.id, p.text, p.turn, p.epoch);
        }
        if (enabled && !error && !busy.current && next.epoch) {
          if (next.state === "AI_SPEAKING") void play(next);
          // 생각 시간 동안은 마이크를 켜지 않는다. "지금 발언하기"를 누르면 바로 녹음한다.
          // 자동 녹음은 서버가 시각을 줄 때만(손들기·강제 차례). 일반 차례는 [발언하기]를 눌러야 시작한다.
          if (
            props.snap.session.phase !== "proposal" &&
            next.state === "USER_READY" &&
            !next.blocked_reason &&
            next.speech_limit_seconds !== 0 &&
            next.think_deadline_at &&
            thinkLeft(next) <= 0
          )
            void record(next);
        }
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
    record,
    settleEdit,
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
        showNotice("발언 차례가 바뀌어 이전 녹음은 보내지 않았어요. 다음 차례에 말해 주세요.");
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
          title: "녹음을 다시 보내지 못했어요",
          detail: "녹음은 남아 있어요. 연결을 확인하고 다시 보내 주세요.",
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
                title: "더 받아 적을 수 없어요",
                detail: "같은 녹음으로 다시 시도할 수 있는 횟수를 다 썼어요. 다시 녹음해 주세요.",
                action: "rerecord",
              }
            : expired(e)
              ? null
              : {
                  title: "다시 받아 적지 못했어요",
                  detail: "연결을 확인하고 다시 녹음해 주세요.",
                  action: "rerecord",
                },
        ),
      );
      return;
    }
    if ((action === "rerecord" || action === "start") && voice?.state === "USER_READY")
      void record(voice);
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

  const submitNow = (id: string) => {
    const text = edit.current.text.trim();
    if (!text || text.length > CANDIDATE_MESSAGE_MAX_CHARS) return;
    confirmed.current = id;
    setConfirmedId(id);
  };

  const changeDraft = (id: string, text: string) => {
    edit.current = { ...edit.current, id, text, changed: true };
    setDraft(text);
    setDraftChanged(true);
  };

  const rerecord = async (snap: VoiceSnapshot, id: string) => {
    try {
      const next = await api<VoiceSnapshot>(`${path}/rerecord`, {
        method: "POST",
        token: token(),
        body: { turn_id: snap.turn_id, epoch: snap.epoch, job_id: id },
      });
      pending.current = null;
      confirmed.current = "";
      setConfirmedId("");
      edit.current = { id: "", text: "", changed: false };
      lastTurn.current = "";
      void record({ ...snap, ...next });
    } catch (e) {
      // 받아 적은 글은 그대로 남아 있으므로 막지 않고, 고쳐서 제출하도록 안내한다
      showNotice(
        exhausted(e)
          ? "다시 녹음은 한 번만 할 수 있어요. 받아 적은 내용을 고쳐 제출해 주세요."
          : "다시 녹음하지 못했어요. 받아 적은 내용을 고쳐 제출해 주세요.",
      );
    }
  };

  // 받아 적기가 끝나 확인을 기다리는 발언(자동 제출 전)
  const review =
    !props.locked &&
    (voice?.utterance?.status === "ready" || voice?.utterance?.status === "empty") &&
    !voice.utterance.message_id &&
    confirmedId !== voice.utterance.id &&
    (confirmLeft(voice) > 0 || draftChanged || voice.utterance.status === "empty")
      ? voice.utterance
      : null;

  // 동료 발언은 재생이 끝난 것만 대화 기록에 전부 보이고, 재생 중인 발언은 말한 문장까지만
  // 보인다(목소리보다 글이 앞서지 않게, V5 들은 범위). 지원자 발언과 시스템 안내는 바로 보인다.
  const heard = useMemo(
    () => ({
      ...props.snap,
      messages: heardMessages(props.snap.messages, heardIds, speaking),
    }),
    [props.snap, heardIds, speaking],
  );
  // 최근 발언(가운데 집중 칸): 들은 동료 발언과 지원자 발언, 오래된 것부터. 재생 중인 발언은 말한 데까지
  const talk = useMemo(
    () =>
      heard.messages
        .filter((m) => m.sender === "candidate" || SEATS.has(m.sender))
        .filter((m) => m.content)
        .slice(-TALK_LIMIT),
    [heard.messages],
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
  // 말하는(준비하는) 동료 이름: 재생 중인 자리, 없으면 맨 앞 발언의 자리
  const speakerName = nameOf(active) ?? nameOf(voice?.items?.[0]?.seat);
  const recording = debating && voice?.state === "USER_SPEAKING";
  // 자리별 상태 표시(프로필 카드): 말하는 동료, 준비 중인 동료, 지원자의 녹음·손 듦·내 차례
  const seatStatus = useMemo(() => {
    const out: Partial<Record<string, SeatStatus>> = {};
    const preparing = voice?.state === "AI_GENERATING" ? voice.items?.[0]?.seat : undefined;
    if (active) out[active] = "speaking";
    else if (preparing) out[preparing] = "preparing";
    if (recording) out.candidate = "recording";
    else if (debating && voice?.hand) out.candidate = "hand";
    else if (canSpeak) out.candidate = "myturn";
    return out;
  }, [active, voice, recording, debating, canSpeak]);
  const disconnected = !props.locked && !props.connected && !!props.reconnecting;
  const statusText = disconnected
    ? "연결이 끊겼어요. 다시 연결하는 중…"
    : phase === "proposal" && !props.locked
      ? "토론이 끝났어요. 정한 내용으로 기획안을 써 주세요"
      : props.locked
        ? "토론이 종료됐어요"
        : !voice
          ? "회의 상태를 불러오는 중…"
          : review
            ? "받아 적은 내용을 확인해 주세요"
            : voice.state === "AI_SPEAKING" && speakerName
              ? `${speakerName}님이 말하는 중`
              : voice.state === "AI_GENERATING" && speakerName
                ? `${speakerName}님이 발언을 준비하고 있어요`
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
  const thinkTotal =
    voice?.ready_at && voice.think_deadline_at
      ? (Date.parse(voice.think_deadline_at) - Date.parse(voice.ready_at)) / 1000
      : 5;

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
      {debating && notice && <InfoLine>{notice}</InfoLine>}
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
              이번 단계에서 아직 의견을 말하지 않았어요. 지금 말해 주세요
            </p>
          )}
          <Button
            className="h-12 px-6 text-callout"
            disabled={startDisabled}
            onClick={() => void record(voice)}
          >
            {micPending
              ? "마이크 권한 확인 중…"
              : voice.think_deadline_at
                ? "지금 발언하기"
                : "발언하기"}
          </Button>
          {voice.think_deadline_at ? (
            <Countdown
              seconds={thinkLeft(voice)}
              total={thinkTotal}
              label="뒤 녹음이 자동으로 시작돼요"
              valueText={`${Math.ceil(thinkLeft(voice))}초 뒤 녹음이 자동으로 시작돼요`}
            />
          ) : holdLeft > 0 ? (
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
              <>
                <Button variant="secondary" onClick={() => void raiseHand(voice, true)}>
                  <HandSymbol size={16} />
                  손들기
                </Button>
                <p className="text-caption text-muted">
                  동료가 말하는 중에 끼어들고 싶을 때 눌러요. 지금 발언이 끝나면 내 차례가 와요
                </p>
              </>
            )}
          </div>
        )}
      {phase !== "proposal" && review && voice && (
        <TranscriptConfirm
          text={draft}
          changed={draftChanged}
          empty={review.status === "empty"}
          seconds={confirmLeft(voice)}
          submitSeconds={
            review.submit_deadline_at
              ? secondsUntil(voice, review.submit_deadline_at)
              : review.status === "empty" && voice.turn_until_at
                ? secondsUntil(voice, voice.turn_until_at)
                : undefined
          }
          rerecorded={!!voice.rerecorded}
          onChange={(text) => changeDraft(review.id, text)}
          onSubmit={() => submitNow(review.id)}
          onRerecord={() => void rerecord(voice, review.id)}
        />
      )}
      {debating &&
        !error &&
        !exhaustedNow &&
        voice?.state === "USER_READY" &&
        voice.blocked_reason === "empty" && (
          <Button
            variant="secondary"
            className="self-start"
            disabled={!props.connected}
            onClick={() => void resume("rerecord")}
          >
            다시 녹음
          </Button>
        )}
      {unsent && <UnsentSpeech text={unsent} onDismiss={() => setUnsent("")} />}
      {recording && (
        <div className="flex flex-col items-stretch gap-3">
          <div className="flex h-12 items-center rounded-lg bg-danger-soft px-3">
            <LevelBars voice={mic} className="h-8 w-full justify-between" />
          </div>
          <button
            type="button"
            onClick={() => finish.current()}
            className="press flex h-14 items-center justify-center gap-2 rounded-xl border-2 border-danger bg-surface text-callout font-semibold text-danger hover:bg-danger-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-danger"
          >
            <Square size={16} fill="currentColor" aria-hidden />
            발언 완료
          </button>
          <p className="text-center text-footnote text-muted">시간이 끝나면 자동으로 제출해요</p>
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
    /** 들은 만큼만 보이는 대화(토론방용) */
    chatSnap: heard,
    currentCaption: speaking,
    /** 최근 발언(가운데 집중 칸, 오래된 것부터)과 지금 재생 중인 발언 id */
    talk,
    liveMessageId: speaking?.id ?? null,
    /** 자리별 상태 표시(프로필 카드) */
    seatStatus,
    /** 지원자 차례가 한 번이라도 왔는지·지금 녹음 중인지(칸 펼치기·프로필 강조용) */
    candidateTurn: !!voice && CANDIDATE_STATES.has(voice.state),
    recording: voice?.state === "USER_SPEAKING",
    /** 녹음 중 마이크 소리 크기(내 프로필 빛) */
    mic,
  };
}
