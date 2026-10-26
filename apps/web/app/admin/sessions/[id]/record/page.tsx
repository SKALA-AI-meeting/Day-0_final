"use client";

// 전체 기록 화면(읽기 전용). 리포트 근거의 "기록에서 보기"가 #앵커로 이 화면의 해당 줄을 가리킨다.
// 참여자 이름·프로필과 세션 길이는 리포트에서 읽는다(기록 응답에는 없다). 불러오는 동안은 뼈대를 보인다.
import { useParams } from "next/navigation";

import { adminApi, useLoad } from "@/components/admin/client";
import { RecordView } from "@/components/admin/record-view";
import { RecordSkeleton } from "@/components/admin/report-loading";

export default function RecordPage() {
  const { id } = useParams<{ id: string }>();
  const { data, error } = useLoad(async (t) => {
    const [entries, report] = await Promise.all([adminApi.record(t, id), adminApi.report(t, id)]);
    return { entries, report };
  }, id);
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
      <header className="reveal flex flex-col gap-1">
        {data && <p className="text-sm text-muted">{data.report.position.name}</p>}
        <h1 className="text-title1 font-bold">
          전체 기록{data && <span className="text-muted"> · {data.report.alias}</span>}
        </h1>
      </header>
      {error && (
        <p role="alert" className="pop-in rounded-xl bg-danger-soft px-4 py-3 text-danger">
          {error}
        </p>
      )}
      {!data && !error && <RecordSkeleton />}
      {data && (
        <RecordView
          entries={data.entries}
          seats={data.report.seats}
          sessionMinutes={data.report.position.session_minutes}
          backHref={`/admin/sessions/${id}/report`}
        />
      )}
    </div>
  );
}
