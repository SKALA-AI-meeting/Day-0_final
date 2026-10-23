"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_URL, ApiError, api } from "@/lib/api";
import { Button } from "@/components/ui";
import type { Seat, StateSnapshot } from "../types";
import type { SessionAuth } from "../use-session";
import { sentences, spokenCount } from "./caption";
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
/** 단계 마감으로 발언이 들어가지 못했을 때 안내를 보여 주는 시간(ms). 오류와 달리 진행을 막지 않는다 */
const NOTICE_MS = 6000;
/** 단계(또는 세션)가 끝나 서버가 이 차례의 녹음·제출을 받지 않는 경우 */
function expired(e: unknown): boolean {
  return e instanceof ApiError && e.code === "voice_turn_expired";
}
/** 서버가 이미 다음 차례(단계 전환 등)로 넘어가 이 요청을 받지 않는 경우(409). 다음 조회에서 새 차례를 받는다 */
function turnMoved(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409;
}
const CANDIDATE_STATES = new Set(["USER_READY", "USER_SPEAKING", "TRANSCRIBING", "PROCESSING"]);
// 음성 상태 확인 주기(ms). 평소 750(계약 docs/contracts/voice.md), 기다리는 상태에서는 250.
// 작게 하면 준비된 발언을 더 빨리 알지만 서버 요청이 늘어난다(세션당 초당 4회).
const POLL_MS = 750;
const WAITING_POLL_MS = 250;
const WAITING_STATES = new Set(["AI_GENERATING", "TRANSCRIBING", "PROCESSING"]);
type Props = {
  auth: SessionAuth;
  snap: StateSnapshot;
  connected: boolean;
  locked: boolean;
  onSend: (id: string, text: string, turn: string, epoch: string) => void;
  onBusy: (busy: boolean) => void;
};
const LABEL: Record<string, string> = {
  AI_GENERATING: "동료가 발언을 준비하고 있어요",
  AI_SPEAKING: "동료의 발언을 듣고 있어요",
  USER_READY: "내 차례예요",
  USER_SPEAKING: "자유롭게 말씀해 주세요",
  TRANSCRIBING: "발언을 글로 옮기고 있어요",
  PROCESSING: "발언을 제출하고 있어요",
};

/** 음성 토론의 차례·녹음·재생·확인 동작. 화면 조각과 상태를 돌려주고, 배치는 토론 화면(debate-view)이 정한다. */
export function useVoiceTurn(props: Props) {
  const latest = useRef(props);
  useEffect(() => {
    latest.current = props;
  }, [props]);
  const [voice, setVoice] = useState<VoiceSnapshot | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [active, setActive] = useState<string | null>(null);
  const [remaining, setRemaining] = useState(60);
  const [level, setLevel] = useState(0);
  const [heardIds, setHeardIds] = useState<string[]>([]);
  // 지금 재생 중인 동료 발언과, 대화 기록에 보여 줄 문장들(재생 위치까지)
  const [speaking, setSpeaking] = useState<{ id: string; text: string } | null>(null);
  // [이대로 제출]을 누른 발언 id. 확인 시간이 남아도 바로 제출한다(ref는 polling용, state는 화면용)
  const confirmed = useRef("");
  const [confirmedId, setConfirmedId] = useState("");
  const stream = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const player = useRef<HTMLAudioElement | null>(null);
  const busy = useRef(false);
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
  }, []);

  useEffect(() => {
    if (!props.connected || props.locked) stopMedia();
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
      const op = operation.current;
      try {
        const mic = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true },
          video: false,
        });
        if (op !== operation.current) {
          mic.getTracks().forEach((t) => t.stop());
          return;
        }
        stream.current = mic;
        const start = await api<VoiceSnapshot>(`${path}/capture`, {
          method: "POST",
          token: token(),
          body: { turn_id: snap.turn_id, epoch: snap.epoch, restart: Boolean(snap.blocked_reason) },
        });
        if (op !== operation.current) {
          mic.getTracks().forEach((t) => t.stop());
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
        const r = new MediaRecorder(mic);
        recorder.current = r;
        const chunks: Blob[] = [];
        let reason = "button";
        r.ondataavailable = (event) => {
          if (event.data.size) chunks.push(event.data);
        };
        const started = performance.now();
        const timer = setInterval(
          () =>
            setRemaining(Math.max(0, Math.ceil(duration - (performance.now() - started) / 1000))),
          200,
        );
        const end = setTimeout(() => {
          reason = "timeout";
          finish.current();
        }, duration * 1000);
        const audioContext = new AudioContext();
        const analyser = audioContext.createAnalyser();
        const source = audioContext.createMediaStreamSource(mic);
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
        const meter = setInterval(() => {
          analyser.getByteTimeDomainData(samples);
          setLevel(Math.min(100, Math.max(...samples.map((s) => Math.abs(s - 128))) * 3));
        }, 150);
        cleanupRecorder.current = () => {
          clearInterval(timer);
          clearInterval(meter);
          clearTimeout(end);
          live?.stop();
          if (audioContext.state !== "closed") void audioContext.close();
        };
        r.onstop = async () => {
          // 처리기를 닫기 전에 남은 소리를 마지막 조각으로 보낸다(완성 파일보다 먼저 도착해야 함)
          const flushed = live?.flush() ?? Promise.resolve();
          cleanupRecorder.current();
          mic.getTracks().forEach((t) => t.stop());
          recorder.current = null;
          setLevel(0);
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
            if (!pendingAudio.current)
              await api(`${path}/abort`, {
                method: "POST",
                token: token(),
                body: {
                  turn_id: start.turn_id,
                  epoch: start.epoch,
                },
              }).catch(() => {});
            setError("발언을 제출하지 못했어요. 연결과 마이크를 확인해 주세요.");
          } finally {
            busy.current = false;
          }
        };
        finish.current = finishOnce(() => {
          if (r.state === "recording") r.stop();
        });
        r.onerror = () => {
          setError("녹음 오류가 발생했어요");
          finish.current();
        };
        r.start();
      } catch (e) {
        stream.current?.getTracks().forEach((t) => t.stop());
        busy.current = false;
        if (expired(e)) showNotice("단계 마감이 가까워 이번 단계에서는 더 말할 수 없어요.");
        else setError("마이크를 사용할 수 없어요. 권한과 장치를 확인한 뒤 다시 시작해 주세요.");
      }
    },
    [path, showNotice],
  );

  const play = useCallback(
    async (snap: VoiceSnapshot) => {
      if (busy.current || !snap.items.length) return;
      const item = snap.items[0];
      // 서버는 맨 앞 발언이 준비되면 재생 상태가 된다. 다음 발언을 아직 만드는 중이면 기다린다.
      if (item.status === "queued" || item.status === "processing") return;
      if (item.status !== "ready") {
        setError("음성 재생을 준비하지 못했어요. 다시 시도해 주세요.");
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
        // 재생 위치에 맞춰 대화 기록의 말풍선에 문장을 하나씩 더한다
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
        show(1);
        sound.ontimeupdate = () => {
          if (sound.duration) show(spokenCount(parts, sound.currentTime / sound.duration));
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
          sound.play().catch((e) => {
            clearTimeout(timeout);
            reject(e);
          });
        });
        if (op === operation.current) await result("ended");
      } catch (e) {
        // 재생 중 단계가 바뀌어 서버가 이 차례를 닫았으면 오류가 아니다. 멈추지 않고 새 차례로 넘어간다
        if (op === operation.current && !turnMoved(e)) {
          await result("error").catch(() => {});
          setError("음성을 재생하지 못했어요. 다시 재생을 눌러 주세요.");
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
    if (!props.connected || props.locked) return;
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
        const nextFence = `${next.generation}:${next.epoch}`;
        if (fence.current && nextFence !== fence.current) stopMedia();
        fence.current = nextFence;
        const ids = next.heard_message_ids ?? [];
        setHeardIds((prev) => (prev.join() === ids.join() ? prev : ids));
        // 녹음 종료/업로드 중에는 서버의 직전 USER_SPEAKING 스냅샷을 표시하지 않는다.
        if (!busy.current || player.current) setVoice(next);
        const u = next.utterance;
        // 받아 적은 글은 확인 시간 동안 보여 주고, [이대로 제출]을 누르거나 시간이 끝나면 제출한다
        const reviewed = confirmed.current === u?.id || confirmLeft(next) <= 0;
        if (u?.status === "ready" && u.transcript && reviewed) {
          pending.current = {
            id: u.id,
            text: u.transcript,
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
          latest.current.onSend(p.id, p.text, p.turn, p.epoch);
        }
        if (enabled && !error && !busy.current && next.epoch) {
          if (next.state === "AI_SPEAKING") void play(next);
          // 생각 시간 동안은 마이크를 켜지 않는다. "지금 발언하기"를 누르면 바로 녹음한다.
          // 자동 녹음은 서버가 시각을 줄 때만(손들기·강제 차례). 일반 차례는 [발언하기]를 눌러야 시작한다.
          if (
            next.state === "USER_READY" &&
            !next.blocked_reason &&
            next.speech_limit_seconds !== 0 &&
            next.think_deadline_at &&
            thinkLeft(next) <= 0
          )
            void record(next);
        }
      } catch {
        if (!disposed) setError("음성 연결을 확인하지 못했어요. 다시 시도해 주세요.");
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
  }, [path, props.connected, props.locked, enabled, error, play, record, stopMedia]);

  const resume = async () => {
    setError("");
    setEnabled(true);
    lastTurn.current = "";
    if (pendingAudio.current && voice) {
      if (pendingAudio.current.turn_id !== voice.turn_id) {
        pendingAudio.current = null;
        setError("발언 차례가 바뀌어 이전 녹음은 제출할 수 없어요.");
        return;
      }
      try {
        await api(`${path}/audio`, {
          method: "POST",
          token: token(),
          body: {
            ...pendingAudio.current,
            epoch: voice.epoch,
          },
        });
        pendingAudio.current = null;
      } catch {
        setError("녹음 파일 재전송을 완료하지 못했어요. 다시 시도해 주세요.");
      }
      return;
    }
    if (voice?.state === "AI_SPEAKING" && voice.items[0]) {
      await api(`${path}/playback`, {
        method: "POST",
        token: token(),
        body: {
          turn_id: voice.turn_id,
          epoch: voice.epoch,
          job_id: voice.items[0].id,
          outcome: "retry",
        },
      }).catch(() => setError("재시도 한도 또는 연결을 확인해 주세요."));
    }
    if (voice?.utterance?.status === "error") {
      await api(`${path}/retry`, {
        method: "POST",
        token: token(),
        body: {
          turn_id: voice.turn_id,
          epoch: voice.epoch,
          job_id: voice.utterance.id,
        },
      }).catch(() => setError("전사 재시도 한도 또는 단계 마감시간을 확인해 주세요."));
    } else if (voice?.state === "USER_READY") void record(voice);
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
      setError("손들기를 전하지 못했어요. 다시 눌러 주세요.");
    }
  };

  const submitNow = (id: string) => {
    confirmed.current = id;
    setConfirmedId(id);
  };

  const rerecord = async (snap: VoiceSnapshot, id: string) => {
    try {
      const next = await api<VoiceSnapshot>(`${path}/rerecord`, {
        method: "POST",
        token: token(),
        body: { turn_id: snap.turn_id, epoch: snap.epoch, job_id: id },
      });
      lastTurn.current = "";
      void record({ ...snap, ...next });
    } catch {
      setError("다시 녹음하지 못했어요. 단계 마감시간을 확인해 주세요.");
    }
  };

  // 받아 적기가 끝나 확인을 기다리는 발언(자동 제출 전)
  const review =
    !props.locked &&
    voice?.utterance?.status === "ready" &&
    voice.utterance.transcript &&
    !voice.utterance.message_id &&
    confirmedId !== voice.utterance.id &&
    confirmLeft(voice) > 0
      ? voice.utterance
      : null;

  // 동료 발언은 재생이 끝난 것만 대화 기록에 전부 보이고, 재생 중인 발언은 말한 문장까지만
  // 보인다(목소리보다 글이 앞서지 않게, V5 들은 범위). 지원자 발언과 시스템 안내는 바로 보인다.
  const heard = useMemo(() => {
    const ids = new Set(heardIds);
    return {
      ...props.snap,
      messages: props.snap.messages
        .filter((m) => !SEATS.has(m.sender) || ids.has(m.id) || m.id === speaking?.id)
        .map((m) =>
          m.id === speaking?.id && !ids.has(m.id) ? { ...m, content: speaking.text } : m,
        ),
    };
  }, [props.snap, heardIds, speaking]);

  // 음성 칸(중범 님 분할 레이아웃의 voice 칸)에 들어갈 조작부. 프로필 강조·대화 기록은 토론 화면이 그린다.
  const controls = (
    <section aria-label="음성 회의" className="flex flex-col items-stretch gap-3">
      <p role="status" className="text-callout font-semibold">
        {props.locked
          ? "토론이 종료됐어요"
          : review
            ? "받아 적은 내용을 확인해 주세요"
            : LABEL[voice?.state ?? "AI_GENERATING"]}
      </p>
      {notice && (
        <p role="status" className="text-footnote font-semibold text-warning">
          {notice}
        </p>
      )}
      {!props.locked &&
        !error &&
        voice?.state === "USER_READY" &&
        !voice.blocked_reason &&
        voice.speech_limit_seconds === 0 && (
          <p className="text-footnote font-semibold text-warning">
            단계 마감이 가까워 이번 단계에서는 더 말할 수 없어요
          </p>
        )}
      {!props.locked &&
        !error &&
        voice?.state === "USER_READY" &&
        !voice.blocked_reason &&
        voice.speech_limit_seconds !== 0 && (
          <div className="flex flex-col items-center gap-2">
            {voice.priority === "forced" && (
              <p className="font-semibold text-accent">
                이번 단계에서 아직 의견을 말하지 않았어요. 지금 말해 주세요
              </p>
            )}
            <Button disabled={!props.connected} onClick={() => void record(voice)}>
              {voice.think_deadline_at ? "지금 발언하기" : "발언하기"}
            </Button>
            <p className="text-footnote text-muted tabular-nums">
              {voice.think_deadline_at
                ? `${Math.ceil(thinkLeft(voice))}초 뒤 녹음이 자동으로 시작돼요`
                : `${Math.ceil(secondsUntil(voice, voice.turn_until_at))}초 안에 시작하지 않으면 동료가 이어서 말해요`}
            </p>
            {voice.speech_limit_seconds !== undefined && (
              <p className="text-footnote font-semibold text-warning tabular-nums">
                단계 마감이 가까워 이번 발언은 최대 {voice.speech_limit_seconds}초예요
              </p>
            )}
          </div>
        )}
      {!props.locked &&
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
                ✋ 손들기
              </Button>
            )}
          </div>
        )}
      {review && voice && (
        <div className="flex flex-col gap-2">
          <p className="rounded-xl border border-line bg-surface p-3">{review.transcript}</p>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => submitNow(review.id)}>이대로 제출</Button>
            {!voice.rerecorded && (
              <Button variant="secondary" onClick={() => void rerecord(voice, review.id)}>
                다시 녹음 (1회)
              </Button>
            )}
          </div>
          <p className="text-footnote text-muted tabular-nums">
            {Math.ceil(confirmLeft(voice))}초 뒤 자동으로 제출돼요
          </p>
        </div>
      )}
      {!props.locked && voice?.state === "USER_SPEAKING" && (
        <div className="flex flex-col gap-2">
          <p className="tabular-nums">내 발언 {remaining}초 남음</p>
          <meter min={0} max={100} value={level} aria-label="마이크 입력 음량" />
          <Button onClick={() => finish.current()}>발언 완료</Button>
          <p className="text-footnote text-muted">시간이 끝나면 자동으로 제출해요</p>
        </div>
      )}
      {error && (
        <p role="alert" className="text-warning">
          {error}
        </p>
      )}
      {(!enabled || error || voice?.blocked_reason) && !props.locked && (
        <Button disabled={!props.connected} onClick={() => void resume()}>
          {enabled ? "다시 시도 / 발언 시작" : "음성 참여 시작"}
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
    /** 지원자 차례가 한 번이라도 왔는지·지금 녹음 중인지(칸 펼치기·프로필 강조용) */
    candidateTurn: !!voice && CANDIDATE_STATES.has(voice.state),
    recording: voice?.state === "USER_SPEAKING",
  };
}
