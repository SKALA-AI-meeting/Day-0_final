"use client";

// 전체 기록(읽기 전용 타임라인). 리포트의 "기록에서 보기"로 들어오면 #<type>-<id> 줄로 스크롤하고 하이라이트한다.
import Link from "next/link";
import { useEffect, useState } from "react";

import { Avatar, Button, Chip, cn } from "@/components/ui";

import { nameOf, officeClock, PHASE_TEXT, readableSummary } from "./format";
import { recordAnchor } from "./review";
import type { PhaseId, RecordEntry, ReportSeat, Sender } from "./types";

export const TYPE_LABEL: Record<RecordEntry["type"], string> = {
  message: "메시지",
  slot: "칸 확정",
  phase: "단계",
  assistant_call: "어시스턴트",
  material_view: "자료 열람",
  proposal_submit: "기획안 제출",
  connection_gap: "접속 끊김",
};

const BUBBLE: Record<Sender, string> = {
  candidate: "bg-bubble-candidate text-on-bubble-candidate",
  p1: "bg-bubble-p1 text-on-bubble",
  p2: "bg-bubble-p2 text-on-bubble",
  p3: "bg-bubble-p3 text-on-bubble",
  system: "",
};

export type RecordFilter = {
  type: RecordEntry["type"] | "all";
  by: Sender | "all";
  phase: PhaseId | "all";
};

/** 각 줄의 단계: 단계 시작 줄을 만날 때마다 바뀐다 */
export function phasesOf(entries: RecordEntry[]): PhaseId[] {
  let current: PhaseId = "keyword";
  return entries.map((e) => {
    if (e.type === "phase" && e.ref.id in PHASE_TEXT) current = e.ref.id as PhaseId;
    return current;
  });
}

export function filterRecord(entries: RecordEntry[], f: RecordFilter) {
  const phases = phasesOf(entries);
  return entries.filter(
    (e, i) =>
      (f.type === "all" || e.type === f.type) &&
      (f.by === "all" || e.by === f.by) &&
      (f.phase === "all" || phases[i] === f.phase),
  );
}

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: [string, string][];
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex items-center gap-1.5 text-sm text-muted">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink"
      >
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}

export function RecordView({
  entries,
  seats,
  sessionMinutes,
  backHref,
  highlight: forced,
}: {
  entries: RecordEntry[];
  seats: ReportSeat[];
  sessionMinutes: number;
  backHref: string;
  /** 테스트용. 없으면 주소의 #앵커를 읽는다 */
  highlight?: string;
}) {
  const [hash, setHash] = useState<string | null>(null);
  const [filter, setFilter] = useState<RecordFilter>({ type: "all", by: "all", phase: "all" });
  const highlight = forced ?? hash;
  const names = Object.fromEntries(seats.map((s) => [s.seat, s.display_name])) as Partial<
    Record<Sender, string>
  >;
  const avatars = Object.fromEntries(seats.map((s) => [s.seat, s.avatar_url]));

  useEffect(() => {
    const read = () => setHash(window.location.hash.slice(1) || null);
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  useEffect(() => {
    if (highlight) document.getElementById(highlight)?.scrollIntoView({ block: "center" });
  }, [highlight, entries.length]);

  const rows = filterRecord(entries, filter);
  const people: [string, string][] = (["candidate", "p1", "p2", "p3", "system"] as Sender[]).map(
    (s) => [s, s === "candidate" ? `지원자 ${names.candidate ?? ""}`.trim() : nameOf(names, s)],
  );
  return (
    <div className="flex flex-col gap-4">
      <div className="no-print sticky top-0 z-10 -mx-1 flex flex-wrap items-center gap-3 border-b border-line bg-canvas px-1 py-2">
        <Link href={backHref}>
          <Button variant="secondary" size="sm">
            ← 리포트로 돌아가기
          </Button>
        </Link>
        <Select
          label="종류"
          value={filter.type}
          onChange={(v) => setFilter((f) => ({ ...f, type: v as RecordFilter["type"] }))}
          options={[["all", "전체"], ...(Object.entries(TYPE_LABEL) as [string, string][])]}
        />
        <Select
          label="주체"
          value={filter.by}
          onChange={(v) => setFilter((f) => ({ ...f, by: v as RecordFilter["by"] }))}
          options={[["all", "전체"], ...people]}
        />
        <Select
          label="단계"
          value={filter.phase}
          onChange={(v) => setFilter((f) => ({ ...f, phase: v as RecordFilter["phase"] }))}
          options={[["all", "전체"], ...(Object.entries(PHASE_TEXT) as [string, string][])]}
        />
        <Button variant="ghost" size="sm" className="ml-auto" onClick={() => window.print()}>
          인쇄
        </Button>
      </div>
      <p className="text-sm text-muted">세션 전체 시간순 기록 · 읽기 전용 · 시각은 회의 속 시각</p>
      {rows.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted">조건에 맞는 기록 없음</p>
      ) : (
        <ol className="flex flex-col rounded-lg border border-line bg-surface">
          {rows.map((e) => {
            const anchor = recordAnchor(e.ref);
            const on = anchor === highlight;
            return (
              <li
                key={anchor}
                id={anchor}
                aria-current={on || undefined}
                className={cn(
                  "grid scroll-mt-20 grid-cols-[48px_84px_1fr] gap-3 border-b border-line px-4 py-2.5 text-sm transition-colors duration-300 last:border-b-0",
                  on && "bg-accent-soft",
                )}
              >
                <span className="text-muted tabular-nums">
                  {officeClock(e.t_min, sessionMinutes)}
                </span>
                <span>
                  <Chip tone={e.type === "connection_gap" ? "danger" : "outline"}>
                    {TYPE_LABEL[e.type]}
                  </Chip>
                </span>
                <span className="flex min-w-0 flex-col gap-1">
                  <span className="inline-flex items-center gap-1.5 text-xs text-muted">
                    {e.by !== "system" && (
                      <Avatar
                        seat={e.by}
                        src={avatars[e.by]}
                        name={nameOf(names, e.by)}
                        size={20}
                      />
                    )}
                    {nameOf(names, e.by)}
                  </span>
                  {e.type === "message" && e.by !== "system" ? (
                    <span
                      className={cn(
                        "w-fit max-w-[85%] rounded-bubble px-3 py-1.5 break-words",
                        BUBBLE[e.by],
                      )}
                    >
                      {e.summary}
                    </span>
                  ) : (
                    <span className={cn("break-words", e.by === "system" && "text-muted")}>
                      {readableSummary(e.summary)}
                    </span>
                  )}
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
