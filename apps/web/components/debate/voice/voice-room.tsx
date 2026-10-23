"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_URL, api } from "@/lib/api";
import { Avatar, Button } from "@/components/ui";
import { ChatRoom } from "../chat";
import type { StateSnapshot } from "../types";
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
  /** 내 차례(USER_READY)일 때 생각 시간이 끝나는 시각. 예전 채용 건이면 ready_at과 같다 */
  think_deadline_at?: string;
};
/** 받아 적은 글 확인 시간이 남은 초(서버 시각 기준). 확인 시간이 없으면 0 */
function confirmLeft(snap: VoiceSnapshot): number {
  const deadline = snap.utterance?.confirm_deadline_at;
  if (!deadline) return 0;
  return Math.max(0, (Date.parse(deadline) - Date.parse(snap.server_now)) / 1000);
}
/** 생각 시간이 남은 초(서버 시각 기준). 생각 시간이 없으면 0 */
function thinkLeft(snap: VoiceSnapshot): number {
  if (!snap.think_deadline_at) return 0;
  return Math.max(0, (Date.parse(snap.think_deadline_at) - Date.parse(snap.server_now)) / 1000);
}
const SEATS = new Set(["p1", "p2", "p3"]);
// 음성 상태 확인 주기(ms). 평소 750(계약 docs/contracts/voice.md), 기다리는 상태에서는 250.
// 작게 하면 준비된 발언을 더 빨리 알지만 서버 요청이 늘어난다(세션당 초당 4회).
const POLL_MS = 750;
const WAITING_POLL_MS = 250;
const WAITING_STATES = new Set(["AI_GENERATING", "TRANSCRIBING", "PROCESSING"]);
const noop = () => {};
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

export function VoiceRoom(props: Props) {
  const latest = useRef(props);
  useEffect(() => {
    latest.current = props;
  }, [props]);
  const [voice, setVoice] = useState<VoiceSnapshot | null>(null);
  const [error, setError] = useState("");
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
          } catch {
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
      } catch {
        stream.current?.getTracks().forEach((t) => t.stop());
        busy.current = false;
        setError("마이크를 사용할 수 없어요. 권한과 장치를 확인한 뒤 다시 시작해 주세요.");
      }
    },
    [path],
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
      } catch {
        if (op === operation.current) {
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
          if (next.state === "USER_READY" && !next.blocked_reason && thinkLeft(next) <= 0)
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

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="음성 회의">
      <div className="flex shrink-0 flex-col items-center gap-4 p-5">
        <p role="status" className="text-title3 font-semibold">
          {props.locked
            ? "토론이 종료됐어요"
            : review
              ? "받아 적은 내용을 확인해 주세요"
              : LABEL[voice?.state ?? "AI_GENERATING"]}
        </p>
        <div className="grid w-full max-w-3xl grid-cols-4 gap-3">
          {props.snap.participants.map((person) => (
            <div
              key={person.id}
              className={`rounded-2xl border p-3 text-center ${active === person.id || (person.id === "candidate" && voice?.state === "USER_SPEAKING") ? "border-accent bg-accent/10" : "border-line bg-surface"}`}
            >
              <Avatar
                name={person.display_name}
                src={person.avatar_url}
                seat={person.id}
                size={36}
                className="mx-auto mb-2 !h-12 !w-12 text-title3"
              />
              <p className="font-semibold">
                {person.display_name}
                {person.id === "candidate" ? " (나)" : ""}
              </p>
              <p className="text-footnote text-muted">
                {active === person.id
                  ? "발언 중"
                  : person.id === "candidate" && voice?.state === "USER_SPEAKING"
                    ? "마이크 켜짐"
                    : "대기"}
              </p>
            </div>
          ))}
        </div>
        {!props.locked &&
          !error &&
          voice?.state === "USER_READY" &&
          !voice.blocked_reason &&
          thinkLeft(voice) > 0 && (
            <div className="flex flex-col items-center gap-3">
              <p className="tabular-nums">생각할 시간 {Math.ceil(thinkLeft(voice))}초</p>
              <Button disabled={!props.connected} onClick={() => void record(voice)}>
                지금 발언하기
              </Button>
              <p className="text-footnote text-muted">시간이 끝나면 녹음이 자동으로 시작돼요</p>
            </div>
          )}
        {review && voice && (
          <div className="flex w-full max-w-2xl flex-col items-center gap-3">
            <p className="w-full rounded-xl border border-line bg-surface p-4 text-center">
              {review.transcript}
            </p>
            <div className="flex gap-2">
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
          <div className="flex flex-col items-center gap-3">
            <p className="tabular-nums">내 발언 {remaining}초 남음</p>
            <meter min={0} max={100} value={level} aria-label="마이크 입력 음량" />
            <Button onClick={() => finish.current()}>발언 완료</Button>
            <p className="text-footnote text-muted">시간이 끝나면 자동으로 제출해요</p>
          </div>
        )}
        {error && (
          <p role="alert" className="max-w-md text-center text-warning">
            {error}
          </p>
        )}
        {(!enabled || error || voice?.blocked_reason) && !props.locked && (
          <Button disabled={!props.connected} onClick={() => void resume()}>
            {enabled ? "다시 시도 / 발언 시작" : "음성 참여 시작"}
          </Button>
        )}
      </div>
      <div className="flex min-h-0 flex-1 border-t border-line">
        <ChatRoom
          snap={heard}
          pending={[]}
          typing={[]}
          locked={props.locked}
          readOnly
          onSend={noop}
          onTyping={noop}
        />
      </div>
    </section>
  );
}
