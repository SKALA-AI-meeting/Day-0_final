"use client";

// 전체 기록(읽기 전용 타임라인). 리포트의 "기록에서 보기"로 들어오면 #<type>-<id> 줄로 스크롤하고 하이라이트한다.
// 위에서부터 개요 숫자 → 붙어 있는 도구 줄(돌아가기·필터·단계 체크포인트) → 세로 타임라인 순서다.
// 단계 시작 줄은 체크포인트 구분선이 되고, 도구 줄의 체크포인트는 지금 읽는 단계를 따라간다.
// 줄들은 화면에 들어올 때 떠오른다(InView). 필터 칩은 한 종류씩 고르는 토글이다(filterRecord와 같은 의미).
import { ArrowLeft, Filter, Printer, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import {
  Avatar,
  Button,
  Checkpoints,
  Chip,
  cn,
  Collapse,
  CountUp,
  DrawnCheck,
  Reveal,
} from "@/components/ui";

import { nameOf, officeClock, PHASE_TEXT, readableSummary } from "./format";
import { InView, useScrollSpy } from "./in-view";
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

const PHASE_IDS = Object.keys(PHASE_TEXT) as PhaseId[];

export type RecordFilter = {
  type: RecordEntry["type"] | "all";
  by: Sender | "all";
  phase: PhaseId | "all";
};

const NO_FILTER: RecordFilter = { type: "all", by: "all", phase: "all" };

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

/** 한 묶음의 토글 칩(하나만 고름). 이미 고른 칩을 다시 누르면 전체로 돌아간다 */
function ChipGroup<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T | "all";
  options: { value: T; label: ReactNode; count?: number }[];
  onChange: (v: T | "all") => void;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-1.5">
      <span className="w-10 shrink-0 text-xs text-muted">{label}</span>
      <button
        type="button"
        aria-pressed={value === "all"}
        onClick={() => onChange("all")}
        className={cn(
          "press rounded-full border px-2.5 py-0.5 text-xs transition-colors duration-150",
          value === "all"
            ? "border-accent bg-accent-soft font-medium text-accent-strong"
            : "border-line text-muted hover:text-ink",
        )}
      >
        전체
      </button>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(on ? "all" : o.value)}
            className={cn(
              "press inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs transition-colors duration-150",
              on
                ? "border-accent bg-accent-soft font-medium text-accent-strong"
                : "border-line text-muted hover:text-ink",
            )}
          >
            {o.label}
            {o.count != null && <span className="text-subtle tabular-nums">{o.count}</span>}
          </button>
        );
      })}
    </div>
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
  const [filter, setFilter] = useState<RecordFilter>(NO_FILTER);
  const [filtersOpen, setFiltersOpen] = useState(false);
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
  const filtered = filter.type !== "all" || filter.by !== "all" || filter.phase !== "all";
  const countOf = (type: RecordEntry["type"]) => entries.filter((e) => e.type === type).length;
  const types = (Object.keys(TYPE_LABEL) as RecordEntry["type"][]).filter((t) => countOf(t) > 0);
  const people = (["candidate", "p1", "p2", "p3", "system"] as Sender[]).filter((s) =>
    entries.some((e) => e.by === s),
  );
  const personLabel = (s: Sender) =>
    s === "candidate" ? `지원자 ${names.candidate ?? ""}`.trim() : nameOf(names, s);

  // 지금 읽는 단계: 화면에 보이는 단계 시작 줄을 따라간다
  const phaseAnchors = rows
    .filter((e) => e.type === "phase" && e.ref.id in PHASE_TEXT)
    .map((e) => recordAnchor(e.ref));
  const spied = useScrollSpy(phaseAnchors);
  const phaseNow = filter.phase !== "all" ? filter.phase : spied.replace(/^phase-/, "");
  const highlighted = highlight ? entries.find((e) => recordAnchor(e.ref) === highlight) : null;

  return (
    <div className="flex flex-col gap-5">
      {/* 개요 숫자 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(
          [
            ["message", "메시지"],
            ["slot", "칸 확정"],
            ["material_view", "자료 열람"],
            ["assistant_call", "어시스턴트 호출"],
          ] as const
        ).map(([type, label], i) => (
          <Reveal
            key={type}
            delay={80 + i * 80}
            className="flex flex-col gap-0.5 rounded-xl border border-line bg-surface px-4 py-3"
          >
            <span className="text-xs text-muted">{label}</span>
            <span className="text-title3 font-semibold">
              <CountUp value={countOf(type)} />
            </span>
          </Reveal>
        ))}
      </div>

      {/* 도구 줄 */}
      <div className="vibrancy no-print sticky top-[53px] z-20 -mx-4 flex flex-col gap-3 border-b border-line px-4 py-2.5 sm:-mx-6 sm:px-6">
        <div className="flex flex-wrap items-center gap-2">
          <Link href={backHref}>
            <Button variant="secondary" size="sm">
              <ArrowLeft aria-hidden className="size-4" />
              리포트로 돌아가기
            </Button>
          </Link>
          <Button
            variant={filtered ? "secondary" : "ghost"}
            size="sm"
            aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen((v) => !v)}
          >
            <Filter aria-hidden className="size-4" />
            필터
            {filtered && <span className="size-1.5 rounded-full bg-accent-fill" aria-hidden />}
          </Button>
          {filtered && (
            <Button variant="ghost" size="sm" onClick={() => setFilter(NO_FILTER)}>
              <RotateCcw aria-hidden className="size-3.5" />
              필터 지우기
            </Button>
          )}
          <span className="text-xs text-muted tabular-nums">
            {rows.length}/{entries.length}줄
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto"
            aria-label="인쇄"
            onClick={() => window.print()}
          >
            <Printer aria-hidden className="size-4" />
            <span className="hidden sm:inline">인쇄</span>
          </Button>
        </div>
        <Collapse open={filtersOpen}>
          <div className="flex flex-col gap-2 pb-1">
            <ChipGroup
              label="종류"
              value={filter.type}
              onChange={(v) => setFilter((f) => ({ ...f, type: v }))}
              options={types.map((t) => ({ value: t, label: TYPE_LABEL[t], count: countOf(t) }))}
            />
            <ChipGroup
              label="주체"
              value={filter.by}
              onChange={(v) => setFilter((f) => ({ ...f, by: v }))}
              options={people.map((s) => ({
                value: s,
                label: (
                  <>
                    {s !== "system" && (
                      <Avatar seat={s} src={avatars[s]} name={personLabel(s)} size={20} />
                    )}
                    {personLabel(s)}
                  </>
                ),
              }))}
            />
            <ChipGroup
              label="단계"
              value={filter.phase}
              onChange={(v) => setFilter((f) => ({ ...f, phase: v }))}
              options={PHASE_IDS.map((p) => ({ value: p, label: PHASE_TEXT[p] }))}
            />
          </div>
        </Collapse>
        <Checkpoints
          steps={PHASE_IDS.map((p) => ({ id: p, label: PHASE_TEXT[p] }))}
          current={phaseNow in PHASE_TEXT ? phaseNow : "keyword"}
          label="지금 읽는 단계"
          className="mx-auto w-full max-w-md"
        />
      </div>

      <p className="text-sm text-muted">세션 전체 시간순 기록 · 읽기 전용 · 시각은 회의 속 시각</p>
      {highlighted && (
        <div className="pop-in no-print flex flex-wrap items-center gap-2 rounded-lg bg-accent-soft px-3 py-2 text-sm">
          <span className="font-medium text-accent-strong">리포트에서 가리킨 기록</span>
          <span className="text-muted tabular-nums">
            {officeClock(highlighted.t_min, sessionMinutes)} · {TYPE_LABEL[highlighted.type]}
          </span>
          <button
            type="button"
            onClick={() =>
              document
                .getElementById(highlight!)
                ?.scrollIntoView({ block: "center", behavior: "smooth" })
            }
            className="ml-auto text-xs text-accent underline-offset-4 hover:underline"
          >
            그 줄로 이동
          </button>
        </div>
      )}
      {rows.length === 0 ? (
        <p className="reveal py-10 text-center text-sm text-muted">조건에 맞는 기록 없음</p>
      ) : (
        <ol key={JSON.stringify(filter)} className="relative flex flex-col">
          <span aria-hidden className="absolute top-2 bottom-2 left-[13px] w-px bg-line" />
          {rows.map((e) => {
            const anchor = recordAnchor(e.ref);
            const on = anchor === highlight;
            const clock = officeClock(e.t_min, sessionMinutes);
            if (e.type === "phase") {
              return (
                <InView
                  as="li"
                  key={anchor}
                  id={anchor}
                  aria-current={on || undefined}
                  className={cn(
                    "relative grid scroll-mt-40 grid-cols-[28px_1fr] items-center gap-3 py-3",
                    on && "pop-in rounded-lg bg-accent-soft",
                  )}
                >
                  <span className="relative z-[1] flex size-7 items-center justify-center rounded-full border-2 border-accent bg-accent-fill text-on-accent">
                    <DrawnCheck size={13} />
                  </span>
                  <span className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line pb-2">
                    <span className="text-callout font-semibold">{readableSummary(e.summary)}</span>
                    <span className="text-xs text-muted tabular-nums">{clock}</span>
                  </span>
                </InView>
              );
            }
            return (
              <InView
                as="li"
                key={anchor}
                id={anchor}
                aria-current={on || undefined}
                className={cn(
                  "relative grid scroll-mt-40 grid-cols-[28px_1fr] gap-3 rounded-lg py-2 text-sm transition-colors duration-300",
                  on && "pop-in bg-accent-soft",
                )}
              >
                <span className="relative z-[1] flex justify-center pt-0.5">
                  {e.by !== "system" ? (
                    <Avatar seat={e.by} src={avatars[e.by]} name={nameOf(names, e.by)} size={28} />
                  ) : (
                    <span
                      className={cn(
                        "mt-2 size-2.5 rounded-full border-2 border-canvas",
                        e.type === "connection_gap" ? "bg-danger" : "bg-subtle",
                      )}
                    />
                  )}
                </span>
                <span className="flex min-w-0 flex-col gap-1 pr-2">
                  <span className="flex flex-wrap items-center gap-2 text-xs text-muted">
                    <span className="tabular-nums">{clock}</span>
                    <Chip tone={e.type === "connection_gap" ? "danger" : "outline"}>
                      {TYPE_LABEL[e.type]}
                    </Chip>
                    <span>{nameOf(names, e.by)}</span>
                  </span>
                  {e.type === "message" && e.by !== "system" ? (
                    <span className="flex flex-col gap-1">
                      <span
                        className={cn(
                          "w-fit max-w-[85%] rounded-bubble px-3 py-1.5 break-words",
                          BUBBLE[e.by],
                        )}
                      >
                        {e.summary}
                      </span>
                      {e.stt_transcript !== undefined && (
                        <small className="text-muted">
                          인식 원문: {e.stt_transcript || "(빈 내용)"}
                        </small>
                      )}
                    </span>
                  ) : (
                    <span className={cn("break-words", e.by === "system" && "text-muted")}>
                      {readableSummary(e.summary)}
                    </span>
                  )}
                </span>
              </InView>
            );
          })}
        </ol>
      )}
    </div>
  );
}
