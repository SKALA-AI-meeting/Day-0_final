"use client";

// 지원자 메모장: 음성 토론에는 채팅 기록을 두지 않고, 지원자가 들은 내용을 직접 적어 두는 칸만 준다(개정 V14).
// 이 브라우저에만 세션별로 저장한다. 서버로 보내지 않고 평가에도 쓰지 않는다.
// 저장소를 못 쓰는 브라우저(사생활 보호 모드 등)에서도 입력은 되고, 새로고침하면 사라질 뿐이다.
import { useState } from "react";

// 메모 최대 글자 수. 크면 오래 적을 수 있지만 브라우저 저장 공간을 더 쓴다
export const MEMO_MAX_CHARS = 10000;

export function memoKey(sessionId: string): string {
  return `dayzero:memo:${sessionId}`;
}

function load(sessionId: string): string {
  try {
    return window.localStorage.getItem(memoKey(sessionId)) ?? "";
  } catch {
    return ""; // 저장소를 못 쓰면 빈 메모로 시작한다
  }
}

export function MemoPad({ sessionId, locked }: { sessionId: string; locked: boolean }) {
  const [text, setText] = useState(() => load(sessionId));
  const [saved, setSaved] = useState(true);

  const change = (value: string) => {
    setText(value);
    try {
      window.localStorage.setItem(memoKey(sessionId), value);
      setSaved(true);
    } catch {
      setSaved(false); // 입력은 계속 되고, 저장 안 됨만 알린다
    }
  };

  return (
    <section
      aria-label="메모"
      className="flex min-h-0 min-w-0 flex-1 flex-col gap-2 bg-surface p-4"
    >
      <header className="flex items-baseline gap-2">
        <h2 className="text-callout font-semibold">메모</h2>
        <span className="text-footnote text-muted">
          {saved
            ? "이 브라우저에만 저장돼요 · 평가에 쓰이지 않아요"
            : "저장할 수 없는 브라우저예요"}
        </span>
      </header>
      <textarea
        aria-label="메모 입력"
        value={text}
        maxLength={MEMO_MAX_CHARS}
        readOnly={locked}
        placeholder="동료 의견이나 떠오른 생각을 적어 두세요"
        onChange={(event) => change(event.target.value)}
        className="min-h-0 w-full flex-1 resize-none rounded-md border border-line bg-surface px-3 py-2 text-base text-ink placeholder:text-subtle read-only:bg-surface-sunken focus:border-accent focus:ring-1 focus:ring-accent focus-visible:outline-none"
      />
    </section>
  );
}
