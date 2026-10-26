// 채용 건 목록: 위에 개요 숫자(채용 건·초대한 지원자·세션 길이별 수)가 차오르고,
// 아래 카드(이름·세션 길이·지원자 수·만든 시각)가 차례로 떠오른다. 카드를 누르면 상세로 간다.
import { ArrowRight, CalendarDays, Clock, Plus, Users } from "lucide-react";
import Link from "next/link";

import { Button, Chip, CountUp, EmptyState, Reveal, Skeleton } from "@/components/ui";

import { formatDateTime } from "./format";
import type { Position } from "./types";

/** 개요 숫자 한 칸 */
export function OverviewStat({
  label,
  value,
  unit,
  index = 0,
  hint,
}: {
  label: string;
  value: number;
  unit: string;
  index?: number;
  hint?: string;
}) {
  return (
    <Reveal
      index={index}
      step={90}
      className="flex min-w-0 flex-col gap-1 rounded-xl border border-line bg-surface px-4 py-3"
    >
      <span className="text-footnote text-muted">{label}</span>
      <span className="text-title2 font-semibold">
        <CountUp value={value} />
        <span className="ml-0.5 text-footnote font-normal text-muted">{unit}</span>
      </span>
      {hint && <span className="truncate text-caption text-subtle">{hint}</span>}
    </Reveal>
  );
}

export function PositionOverview({ positions }: { positions: Position[] }) {
  const invited = positions.reduce((n, p) => n + p.candidate_count, 0);
  const demo = positions.filter((p) => p.session_minutes === 15).length;
  const latest = [...positions].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  return (
    <section
      aria-label="채용 건 개요"
      className="grid grid-cols-2 gap-3 sm:grid-cols-3 [&>*:last-child]:col-span-2 sm:[&>*:last-child]:col-span-1"
    >
      <OverviewStat label="채용 건" value={positions.length} unit="건" index={0} />
      <OverviewStat label="초대한 지원자" value={invited} unit="명" index={1} />
      <OverviewStat
        label="60분 세션"
        value={positions.length - demo}
        unit="건"
        index={2}
        hint={`15분(데모) ${demo}건${latest ? ` · 최근 ${formatDateTime(latest.created_at)}` : ""}`}
      />
    </section>
  );
}

export function PositionList({ positions }: { positions: Position[] }) {
  if (positions.length === 0) {
    return (
      <Reveal className="rounded-xl border border-dashed border-line bg-surface">
        <EmptyState
          title="아직 채용 건이 없습니다"
          description="채용 건을 만들면 지원자를 초대할 링크를 만들 수 있습니다."
          action={
            <Link href="/admin/positions/new">
              <Button>채용 건 만들기</Button>
            </Link>
          }
        />
      </Reveal>
    );
  }
  // 최근에 만든 채용 건이 먼저(응답 순서를 따르되 만든 시각으로 맞춘다)
  const sorted = [...positions].sort((a, b) => b.created_at.localeCompare(a.created_at));
  return (
    <ul aria-label="채용 건 목록" className="grid gap-3 md:grid-cols-2">
      {sorted.map((p, i) => (
        <Reveal as="li" key={p.id} delay={260 + i * 70} className="min-w-0">
          <Link
            href={`/admin/positions/${p.id}`}
            className="lift group flex h-full flex-col gap-3 rounded-xl border border-line bg-surface p-4 focus-visible:outline-2 focus-visible:outline-accent"
          >
            <div className="flex items-start justify-between gap-3">
              <span className="min-w-0 text-callout font-semibold break-all">{p.name}</span>
              <ArrowRight
                aria-hidden
                className="mt-1 size-4 shrink-0 text-subtle transition-transform duration-200 group-hover:translate-x-0.5 group-hover:text-accent motion-reduce:transition-none"
              />
            </div>
            <div className="mt-auto flex flex-wrap items-center gap-x-4 gap-y-1.5 text-footnote text-muted tabular-nums">
              <span className="inline-flex items-center gap-1">
                <Clock aria-hidden className="size-3.5" />
                {p.session_minutes}분{p.session_minutes === 15 && <Chip tone="outline">데모</Chip>}
              </span>
              <span className="inline-flex items-center gap-1">
                <Users aria-hidden className="size-3.5" />
                지원자 {p.candidate_count}명
              </span>
              <span className="inline-flex items-center gap-1">
                <CalendarDays aria-hidden className="size-3.5" />
                {formatDateTime(p.created_at)}
              </span>
            </div>
          </Link>
        </Reveal>
      ))}
      <Reveal as="li" delay={260 + sorted.length * 70} className="min-w-0">
        <Link
          href="/admin/positions/new"
          aria-label="새 채용 건 만들기"
          className="lift flex h-full min-h-24 items-center justify-center gap-2 rounded-xl border border-dashed border-line p-4 text-footnote text-muted hover:text-accent focus-visible:outline-2 focus-visible:outline-accent"
        >
          <Plus aria-hidden className="size-4" />새 채용 건
        </Link>
      </Reveal>
    </ul>
  );
}

/** 불러오는 동안 개요·카드 자리표시 */
export function PositionListSkeleton() {
  return (
    <div className="flex flex-col gap-6" aria-busy aria-label="불러오는 중">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-20 rounded-xl" />
        ))}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-28 rounded-xl" />
        ))}
      </div>
    </div>
  );
}
