"use client";

// 토론방: iMessage식 말풍선(지원자 오른쪽 강조색, AI 동료 왼쪽 자리별 틴트 + 이름 + 프로필 이미지),
// 가운데 회색 시스템 안내, 입력 중 표시, 입력창. 오피스 시각은 메시지 타임스탬프에만 쓴다(R7).
import { ArrowUp } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { Avatar, AvatarGroup, cn } from "@/components/ui";

import type { Pending } from "./reducer";
import { nameOf, officeTime, roughDuration } from "./state";
import type { Message, Seat, StateSnapshot } from "./types";

const BUBBLE: Record<Seat, string> = {
  p1: "bg-bubble-p1",
  p2: "bg-bubble-p2",
  p3: "bg-bubble-p3",
};

export const TYPING_REFRESH_MS = 5000;

type Line =
  | { kind: "message"; message: Message; pending?: "sending" | "acked" | "failed" }
  | { kind: "notice"; id: string; text: string; tMin: number }
  | { kind: "time"; id: string; text: string };

type Group = { sender: Message["sender"]; lines: Extract<Line, { kind: "message" }>[] };

/** 메시지·끊김 안내·보내는 중 메시지를 시간순 줄로 만들고, 단계 경계·5분(회의 시각) 간격에 시각 줄을 넣는다 */
export function buildLines(snap: StateSnapshot, pending: Pending[]): Line[] {
  const minutes = snap.session.session_minutes;
  const gapMin = (5 * minutes) / 60;
  const items: Line[] = [
    ...snap.messages.map((message) => ({ kind: "message" as const, message })),
    ...snap.connection_gaps.map((gap, i) => ({
      kind: "notice" as const,
      id: `gap-${i}`,
      text: `${roughDuration((gap.to_t_min - gap.from_t_min) * 60)} 동안 연결이 끊겼어요`,
      tMin: gap.to_t_min,
    })),
  ];
  const t = (line: Line) =>
    line.kind === "message" ? line.message.t_min : line.kind === "notice" ? line.tMin : 0;
  items.sort((a, b) => t(a) - t(b));
  const last = snap.messages[snap.messages.length - 1];
  for (const p of pending) {
    items.push({
      kind: "message",
      pending: p.failed ? "failed" : p.messageId ? "acked" : "sending",
      message: {
        id: p.clientMsgId,
        sender: "candidate",
        content: p.content,
        phase: snap.session.phase ?? last?.phase ?? "keyword",
        is_invite: false,
        t_min: p.tMin,
        created_at: "",
      },
    });
  }
  const out: Line[] = [];
  let prev: Message | null = null;
  for (const line of items) {
    if (line.kind === "message") {
      const m = line.message;
      if (!prev || prev.phase !== m.phase || m.t_min - prev.t_min >= gapMin)
        out.push({
          kind: "time",
          id: `time-${m.id}`,
          text: officeTime(m.t_min, minutes, snap.session.office_start),
        });
      prev = m;
    }
    out.push(line);
  }
  return out;
}

function groupLines(lines: Line[]) {
  const out: (Line | Group)[] = [];
  for (const line of lines) {
    const tail = out[out.length - 1];
    if (line.kind === "message" && line.message.sender !== "system") {
      if (tail && "lines" in tail && tail.sender === line.message.sender) tail.lines.push(line);
      else out.push({ sender: line.message.sender, lines: [line] });
    } else out.push(line);
  }
  return out;
}

function SystemNotice({ text, time }: { text: string; time?: string }) {
  return (
    <p
      className="mx-auto max-w-[40rem] px-6 py-1 text-center text-footnote break-keep text-balance text-system-notice"
      title={time}
    >
      {text}
    </p>
  );
}

function MessageGroup({
  group,
  snap,
  fresh,
}: {
  group: Group;
  snap: StateSnapshot;
  fresh: Set<string>;
}) {
  const mine = group.sender === "candidate";
  const seat = group.sender as Seat;
  const person = snap.participants.find((p) => p.id === group.sender);
  const minutes = snap.session.session_minutes;
  return (
    <div className={cn("flex gap-2 pt-3", mine ? "justify-end" : "justify-start")}>
      {!mine && (
        <div className="flex w-7 shrink-0 flex-col justify-end">
          <Avatar
            size={28}
            seat={seat}
            src={person?.avatar_url}
            name={person?.display_name ?? seat}
          />
        </div>
      )}
      <div
        className={cn(
          "flex max-w-[70%] min-w-0 flex-col gap-0.5",
          mine ? "items-end" : "items-start",
        )}
      >
        {/* 묶음 머리는 이름만. 시각은 가운데 시각 줄(5분 간격·단계 경계)과 말풍선 hover로 본다 */}
        <p className="flex items-baseline gap-1.5 px-1 text-caption text-muted">
          <span className={cn("truncate", !mine && "font-medium")}>
            {mine ? "나" : nameOf(snap, seat)}
          </span>
        </p>
        {group.lines.map((line, i) => {
          const m = line.message;
          const firstLine = i === 0;
          const lastLine = i === group.lines.length - 1;
          return (
            <div
              key={m.id}
              title={officeTime(m.t_min, minutes, snap.session.office_start)}
              data-sender={m.sender}
              className={cn(
                "rounded-bubble px-3.5 py-2 text-body break-words whitespace-pre-wrap",
                mine
                  ? "bg-bubble-candidate text-on-bubble-candidate"
                  : `${BUBBLE[seat]} text-on-bubble`,
                // 묶음에서 다음 말풍선과 붙는 쪽 모서리
                mine
                  ? cn(
                      !firstLine && "rounded-tr-bubble-tail",
                      !lastLine && "rounded-br-bubble-tail",
                    )
                  : cn(
                      !firstLine && "rounded-tl-bubble-tail",
                      !lastLine && "rounded-bl-bubble-tail",
                    ),
                line.pending === "sending" && "opacity-60",
                line.pending === "failed" && "opacity-60 ring-1 ring-danger",
                fresh.has(m.id) && "arrive",
              )}
            >
              {m.content}
              {line.pending === "failed" && (
                <span className="mt-1 block text-caption text-danger">보내지 못했어요</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function TypingIndicator({ snap, seats }: { snap: StateSnapshot; seats: Seat[] }) {
  if (seats.length === 0) return null;
  const names = seats.map((seat) => `${nameOf(snap, seat)}님`).join(", ");
  const seat = seats[0];
  return (
    <div className="flex items-center gap-2 px-4 pt-3 pb-1" aria-live="polite">
      <span
        className={cn("inline-flex items-center gap-1 rounded-bubble px-3 py-2.5", BUBBLE[seat])}
      >
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="typing-dot size-1.5 rounded-full bg-on-bubble"
            style={{ animationDelay: `${i * 0.2}s` }}
          />
        ))}
      </span>
      <span className="text-footnote text-muted">{names}이 입력 중</span>
    </div>
  );
}

export function ChatRoom({
  snap,
  pending,
  typing,
  locked,
  readOnly = false,
  onSend,
  onTyping,
}: {
  snap: StateSnapshot;
  pending: Pending[];
  typing: Seat[];
  locked: boolean;
  /** 음성 세션의 대화 기록처럼 입력창 없이 보기만 할 때 */
  readOnly?: boolean;
  onSend: (content: string) => void;
  onTyping: (state: "start" | "stop") => void;
}) {
  const lines = useMemo(() => buildLines(snap, pending), [snap, pending]);
  const groups = useMemo(() => groupLines(lines), [lines]);
  const scroller = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const [unread, setUnread] = useState(0);
  // 처음 그린 메시지에는 도착 모션을 붙이지 않는다
  const [seen] = useState(() => new Set(snap.messages.map((m) => m.id)));
  const count = snap.messages.length + pending.length;
  const lastCount = useRef(count);

  const fresh = useMemo(() => {
    const set = new Set<string>();
    for (const m of snap.messages) if (!seen.has(m.id)) set.add(m.id);
    for (const p of pending) set.add(p.clientMsgId);
    return set;
  }, [snap.messages, pending, seen]);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const added = count - lastCount.current;
    lastCount.current = count;
    // 맨 아래를 보고 있을 때만 따라간다. 위를 보고 있으면 "새 메시지 n개"
    if (atBottom.current) el.scrollTop = el.scrollHeight;
    else if (added > 0) setUnread((n) => n + added);
  }, [count, typing.length]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (atBottom.current) setUnread(0);
  };

  const people = snap.participants
    .filter((p) => p.id !== "candidate")
    .concat(snap.participants.filter((p) => p.id === "candidate"));

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-surface" aria-label="토론방">
      <header className="flex items-center gap-3 border-b border-line px-5 py-3">
        <h2 className="text-callout font-semibold">새 프로젝트 팀</h2>
        <AvatarGroup
          size={20}
          people={people.map((p) => ({
            seat: p.id,
            src: p.avatar_url,
            name: p.id === "candidate" ? `${p.display_name}(나)` : p.display_name,
          }))}
        />
        <span className="truncate text-footnote text-muted">
          {people
            .map((p) => (p.id === "candidate" ? `${p.display_name}(나)` : p.display_name))
            .join(", ")}
        </span>
      </header>
      <div className="relative min-h-0 flex-1">
        <div
          ref={scroller}
          role="log"
          aria-live="polite"
          onScroll={onScroll}
          className="h-full overflow-y-auto px-4 pt-2 pb-4"
        >
          {groups.map((item) =>
            "lines" in item ? (
              <MessageGroup key={item.lines[0].message.id} group={item} snap={snap} fresh={fresh} />
            ) : item.kind === "time" ? (
              <p
                key={item.id}
                className="pt-4 pb-1 text-center text-caption text-subtle tabular-nums"
              >
                {item.text}
              </p>
            ) : item.kind === "notice" ? (
              <SystemNotice key={item.id} text={item.text} />
            ) : (
              <SystemNotice
                key={item.message.id}
                text={item.message.content}
                time={officeTime(
                  item.message.t_min,
                  snap.session.session_minutes,
                  snap.session.office_start,
                )}
              />
            ),
          )}
          <TypingIndicator snap={snap} seats={typing} />
        </div>
        {unread > 0 && (
          <button
            type="button"
            onClick={() => {
              const el = scroller.current;
              if (el) el.scrollTop = el.scrollHeight;
              setUnread(0);
            }}
            className="press absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border border-line bg-surface px-3 py-1 text-footnote shadow-[var(--shadow-float)]"
          >
            새 메시지 {unread}개
          </button>
        )}
      </div>
      {!readOnly && <Composer locked={locked} onSend={onSend} onTyping={onTyping} />}
    </section>
  );
}

export function Composer({
  locked,
  onSend,
  onTyping,
}: {
  locked: boolean;
  onSend: (content: string) => void;
  onTyping: (state: "start" | "stop") => void;
}) {
  const [text, setText] = useState("");
  const lastStart = useRef(0);

  useEffect(() => {
    // 글자가 있는 동안 5초마다 typing start를 다시 보낸다(서버는 6초 뒤 만료)
    if (!text.trim() || locked) return;
    const timer = setInterval(() => {
      lastStart.current = Date.now();
      onTyping("start");
    }, TYPING_REFRESH_MS);
    return () => clearInterval(timer);
  }, [text, locked, onTyping]);

  const change = (value: string) => {
    const had = text.trim().length > 0;
    setText(value);
    if (value.trim() && (!had || Date.now() - lastStart.current >= TYPING_REFRESH_MS)) {
      lastStart.current = Date.now();
      onTyping("start");
    } else if (!value.trim() && had) onTyping("stop");
  };

  const send = () => {
    const content = text.trim();
    if (!content || locked) return;
    onSend(content);
    onTyping("stop");
    lastStart.current = 0;
    setText("");
  };

  return (
    <form
      className="flex items-end gap-2 border-t border-line px-4 py-3"
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      <textarea
        rows={1}
        value={text}
        disabled={locked}
        aria-label="메시지 입력"
        placeholder={locked ? "세션이 끝났어요" : "메시지 입력"}
        onChange={(event) => change(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            send();
          }
        }}
        className="max-h-32 min-h-10 flex-1 resize-none rounded-[20px] border border-line bg-surface px-4 py-2 text-body outline-none field-sizing-content focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/40 disabled:bg-surface-sunken"
      />
      <button
        type="submit"
        aria-label="보내기"
        disabled={locked || !text.trim()}
        className="press flex size-10 shrink-0 items-center justify-center rounded-full bg-accent-fill text-on-accent not-disabled:hover:bg-accent-fill-hover disabled:bg-surface-sunken disabled:text-subtle"
      >
        <ArrowUp size={20} />
      </button>
    </form>
  );
}
