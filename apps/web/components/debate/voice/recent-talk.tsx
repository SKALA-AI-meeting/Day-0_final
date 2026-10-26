"use client";

// 최근 발언(음성 토론 가운데 집중 칸). 실시간 자막처럼 들은 동료 발언과 지원자 발언을 오래된 것부터 쌓고,
// 새 줄이 생기거나 재생 중인 발언이 길어지면 부드럽게 위로 밀려 올라간다. 모든 줄은 비슷한 크기이고 가장 새
// 발언만 살짝 강조한다(글자 굵기·바탕). 재생 중인 발언은 말한 문장까지만 보이고(목소리보다 글이 앞서지 않음,
// caption.ts) 끝에 커서가 깜빡인다. 흐림은 나이가 아니라 보이는 창 기준이다: 칸 위 가장자리를 지나 밀려
// 나가는 줄만 서서히 사라진다(슬라이딩 창 마스크). 스크롤로 지난 발언을 보고, 위를 보는 동안 새 발언이 오면
// [새 발언 n개]로 내려간다.
// 낭독은 프로필 칸의 자막 영역이 맡으므로 이 목록은 바뀔 때 읽지 않는다(aria-live="off").
import { ArrowDown } from "lucide-react";
import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";

import { Avatar, cn } from "@/components/ui";

import type { Participant, Seat } from "../types";
import { SpeakingSymbol } from "./status-symbols";

type TalkMessage = { id: string; sender: string; content: string };

/** 맨 아래에서 이만큼(px) 안이면 새 발언을 따라 내려간다 */
const FOLLOW_PX = 64;
/** 칸 위 가장자리에서 줄이 사라지는 구간(px). 아래는 짧게(새 발언이 가리지 않게) */
const FADE_TOP_PX = 72;
const FADE_BOTTOM_PX = 12;

export function RecentTalk({
  messages,
  participants,
  liveId,
  className,
}: {
  messages: TalkMessage[];
  participants: Participant[];
  /** 지금 재생 중인 발언 id(말한 데까지만 보임) */
  liveId: string | null;
  className?: string;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLOListElement>(null);
  const atBottom = useRef(true);
  // 사용자가 직접 스크롤했는지(휠·터치·키·끌기). 칸 크기가 바뀌며 생긴 스크롤로는 따라가기를 멈추지 않는다
  const userScroll = useRef(0);
  const [unread, setUnread] = useState(0);
  const last = messages.at(-1);
  const count = messages.length;
  const seen = useRef(count);

  // 새 발언이 오거나 재생 중인 발언이 길어지면 맨 아래를 보고 있을 때만 따라간다
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const added = count - seen.current;
    seen.current = count;
    // 맨 아래를 보고 있으면 부드럽게 따라 내려간다(자막처럼 위로 밀려 올라감)
    if (atBottom.current) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    else if (added > 0) setUnread((n) => n + added);
  }, [count, last?.content]);

  // 아바타 이미지가 늦게 뜨거나 칸 높이가 바뀌어 내용 높이가 달라져도 맨 아래를 보고 있으면 계속 붙어 있는다
  useLayoutEffect(() => {
    const el = scroller.current;
    const inner = list.current;
    if (!el || !inner || typeof ResizeObserver === "undefined") return;
    const follow = new ResizeObserver(() => {
      if (atBottom.current) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    });
    follow.observe(inner);
    follow.observe(el);
    return () => follow.disconnect();
  }, []);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_PX;
    if (near) {
      atBottom.current = true;
      setUnread(0);
    } else if (Date.now() - userScroll.current < 800) {
      atBottom.current = false;
    }
  };
  const markUser = () => {
    userScroll.current = Date.now();
  };

  const person = (id: string) => participants.find((p) => p.id === id);
  // 보이는 창의 위·아래 가장자리만 서서히 지운다. 나이와 상관없이 창 안의 줄은 모두 또렷하다
  const edge = `linear-gradient(to bottom, transparent 0, #000 ${FADE_TOP_PX}px, #000 calc(100% - ${FADE_BOTTOM_PX}px), transparent 100%)`;
  const fade: CSSProperties = { maskImage: edge, WebkitMaskImage: edge };

  return (
    <section
      aria-label="최근 발언"
      className={cn(
        "relative flex min-h-0 flex-col overflow-hidden rounded-xl border border-line bg-surface",
        className,
      )}
    >
      <header className="flex shrink-0 items-center gap-2 px-4 pt-3 pb-1">
        <h2 className="text-footnote font-semibold text-muted">최근 발언</h2>
        {liveId && (
          <span className="inline-flex items-center gap-1 text-caption font-medium text-accent">
            <SpeakingSymbol size={12} />
            지금 말하는 중
          </span>
        )}
      </header>
      <div
        ref={scroller}
        onScroll={onScroll}
        onWheel={markUser}
        onTouchMove={markUser}
        onPointerDown={markUser}
        onKeyDown={markUser}
        tabIndex={0}
        style={fade}
        className="min-h-0 flex-1 overflow-y-auto px-3 pt-16 pb-4 outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
      >
        {/* 발언이 적을 때도 새 발언이 조작부 가까이(아래)에 오도록 아래로 붙인다 */}
        <ol ref={list} aria-live="off" className="flex min-h-full flex-col justify-end gap-1.5">
          {messages.length === 0 && (
            <li className="m-auto text-footnote text-muted">아직 발언이 없어요.</li>
          )}
          {messages.map((m, i) => {
            const age = messages.length - 1 - i;
            const newest = age === 0;
            const live = m.id === liveId;
            const who = person(m.sender);
            const me = m.sender === "candidate";
            return (
              <li
                key={m.id}
                data-age={age}
                data-live={live || undefined}
                className={cn(
                  "talk-row flex items-start gap-2.5 rounded-lg px-2 py-1.5 transition-colors duration-500",
                  newest && "talk-new bg-accent-soft/60",
                )}
              >
                <Avatar
                  size={28}
                  seat={(who?.id ?? m.sender) as Seat | "candidate"}
                  src={who?.avatar_url}
                  name={who?.display_name ?? m.sender}
                  className="mt-0.5"
                />
                <div className="flex min-w-0 flex-col">
                  <span
                    className={cn("text-caption font-semibold", me ? "text-accent" : "text-muted")}
                  >
                    {me ? "나" : (who?.display_name ?? m.sender)}
                  </span>
                  <p
                    className={cn(
                      "text-callout leading-relaxed break-words whitespace-pre-wrap transition-colors duration-500",
                      newest ? "font-medium text-ink" : "text-ink/80",
                      live && "talk-live",
                    )}
                  >
                    {m.content}
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
      {unread > 0 && (
        <button
          type="button"
          onClick={() => {
            const el = scroller.current;
            if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
            setUnread(0);
          }}
          className="press absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1 rounded-full border border-line bg-surface px-3 py-1 text-footnote shadow-[var(--shadow-float)]"
        >
          <ArrowDown size={14} aria-hidden />새 발언 {unread}개
        </button>
      )}
    </section>
  );
}
