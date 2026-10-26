"use client";

// 리포트·전체 기록을 불러오는 동안의 자리표시와 "만드는 중" 안내.
// 자리표시는 실제 화면의 뼈대(머리 카드·개요 숫자·요약표·타임라인)와 같은 모양이라 내용이 들어올 때 덜 흔들린다.
import { Checkpoints, Skeleton } from "@/components/ui";

/** 리포트 화면 뼈대 */
export function ReportSkeleton() {
  return (
    <div
      role="status"
      aria-label="리포트를 불러오는 중"
      className="mx-auto flex w-full max-w-5xl flex-col gap-6 lg:pl-[13.5rem]"
    >
      <div className="flex flex-col gap-4 rounded-xl border border-line bg-surface p-6">
        <Skeleton className="h-5 w-24" />
        <Skeleton className="h-8 w-56" />
        <div className="flex gap-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-9 w-28 rounded-full" />
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24 rounded-xl" />
        ))}
      </div>
      <div className="flex flex-col gap-2 rounded-xl border border-line bg-surface p-4">
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-10" />
        ))}
      </div>
    </div>
  );
}

/** 리포트 생성 중(report_not_ready 폴링). 단계 체크포인트로 지금 어디쯤인지 보인다 */
export function ReportGenerating() {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-5">
      <div className="pop-in flex flex-col gap-5 rounded-xl border border-line bg-surface p-6">
        <p role="status" className="flex items-center gap-2 text-muted">
          <span aria-hidden className="relative flex size-2.5">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-accent opacity-60 motion-reduce:animate-none" />
            <span className="relative inline-flex size-2.5 rounded-full bg-accent-fill" />
          </span>
          리포트를 만드는 중입니다. 준비되면 이 화면에 바로 나타납니다.
        </p>
        <Checkpoints
          steps={[
            { id: "ended", label: "세션 종료" },
            { id: "evaluate", label: "AI 평가" },
            { id: "review", label: "담당자 검토" },
          ]}
          current="evaluate"
          label="리포트 준비 단계"
          className="mx-auto w-full max-w-sm"
        />
      </div>
      <div aria-hidden className="flex flex-col gap-2 opacity-70">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-12 rounded-xl" />
        ))}
      </div>
    </div>
  );
}

/** 전체 기록 뼈대 */
export function RecordSkeleton() {
  return (
    <div role="status" aria-label="기록을 불러오는 중" className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-16 rounded-xl" />
        ))}
      </div>
      <div className="flex flex-col gap-4">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="grid grid-cols-[28px_1fr] gap-3">
            <Skeleton className="size-7 rounded-full" />
            <div className="flex flex-col gap-1.5">
              <Skeleton className="h-3 w-32" />
              <Skeleton className={i % 2 ? "h-8 w-2/3" : "h-8 w-1/2"} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
