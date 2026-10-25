"use client";

// 채용 건 목록. 제목 → 개요 숫자 → 카드 순서로 들어온다. 불러오는 동안은 자리표시를 둔다.
import { Plus } from "lucide-react";
import Link from "next/link";

import { adminApi, useLoad } from "@/components/admin/client";
import {
  PositionList,
  PositionListSkeleton,
  PositionOverview,
} from "@/components/admin/position-list";
import { Button, Reveal } from "@/components/ui";

export default function PositionsPage() {
  const { data, error } = useLoad(adminApi.positions);

  return (
    <>
      <Reveal className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-title1 font-bold">채용 건</h1>
          <p className="text-sm text-muted">
            같은 채용 건의 지원자는 모두 같은 장면을 겪고, 같은 척도로 기록됩니다.
          </p>
        </div>
        <Link href="/admin/positions/new">
          <Button>
            <Plus aria-hidden className="size-4" />
            채용 건 만들기
          </Button>
        </Link>
      </Reveal>
      {error && (
        <p role="alert" className="pop-in rounded-md bg-danger-soft px-3 py-2 text-danger">
          {error}
        </p>
      )}
      {!data && !error && <PositionListSkeleton />}
      {data && (
        <>
          {data.length > 0 && <PositionOverview positions={data} />}
          <PositionList positions={data} />
        </>
      )}
    </>
  );
}
