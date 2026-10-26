"use client";

// 채용 건 생성. 만든 뒤에는 화면이 넘어가며 체크가 그려지고, 응답의 자리 이름(AI 동료)과 키워드 후보가
// 하나씩 들어와 확인용으로 보인 뒤 지원자 목록으로 넘어간다.
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { adminApi, errorMessage, useLoad, useToken } from "@/components/admin/client";
import { CREATE_STEPS, PositionForm } from "@/components/admin/position-form";
import type { PositionCreated, SessionMinutes } from "@/components/admin/types";
import {
  Avatar,
  Button,
  Checkpoints,
  DrawnCheck,
  Reveal,
  ScreenTransition,
  Skeleton,
  useSequence,
} from "@/components/ui";

function Created({ created }: { created: PositionCreated }) {
  // 동료 → 키워드 → 다음 버튼 순서로 하나씩
  const items = created.seats.length + created.keywords.length + 1;
  const shown = useSequence(items, 160, 500, created.id);
  const seatsShown = Math.min(shown, created.seats.length);
  const keywordsShown = Math.max(
    0,
    Math.min(shown - created.seats.length, created.keywords.length),
  );
  const ready = shown >= items;

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
      <div className="rounded-xl border border-line bg-surface px-4 py-4 sm:px-8">
        <Checkpoints steps={CREATE_STEPS} current="confirm" done label="만들기 단계" />
      </div>
      <section className="flex flex-col gap-6 rounded-xl border border-line bg-surface p-5 sm:p-7">
        <Reveal className="flex items-start gap-4">
          <span className="flex size-11 shrink-0 items-center justify-center rounded-full bg-success-soft text-success">
            <DrawnCheck size={22} delay={150} />
          </span>
          <div className="flex min-w-0 flex-col gap-1">
            <h1 className="text-title1 font-bold break-all">{created.name}</h1>
            <p className="text-muted">
              채용 건을 만들었습니다 · 세션 {created.session_minutes}분. 같은 채용 건의 지원자는
              모두 같은 장면을 겪습니다.
            </p>
          </div>
        </Reveal>
        <dl className="flex flex-col gap-5">
          <Reveal delay={250} className="flex flex-col gap-2">
            <dt className="text-sm text-muted">AI 동료</dt>
            <dd className="flex flex-wrap gap-2">
              {created.seats.slice(0, seatsShown).map((s) => (
                <span
                  key={s.seat}
                  className="pop-in inline-flex items-center gap-2 rounded-full border border-line bg-surface py-1 pr-3 pl-1"
                >
                  <Avatar seat={s.seat} src={s.avatar_url} name={s.name} size={28} />
                  {s.name}
                </span>
              ))}
            </dd>
          </Reveal>
          <Reveal delay={350} className="flex flex-col gap-2">
            <dt className="text-sm text-muted">키워드 후보</dt>
            <dd className="flex flex-wrap gap-1.5">
              {created.keywords.slice(0, keywordsShown).map((k) => (
                <span key={k} className="reveal rounded-md bg-surface-sunken px-2.5 py-1 text-sm">
                  {k}
                </span>
              ))}
              {/* 낭독·복사용 원문 */}
              <span className="sr-only">{created.keywords.join(" · ")}</span>
            </dd>
          </Reveal>
        </dl>
        <div className={ready ? "reveal" : "invisible"}>
          <Link href={`/admin/positions/${created.id}`}>
            <Button>
              지원자 초대하러 가기
              <ArrowRight aria-hidden className="size-4" />
            </Button>
          </Link>
        </div>
      </section>
    </div>
  );
}

export default function NewPositionPage() {
  const token = useToken();
  const { data: catalog, error: loadError } = useLoad(adminApi.catalog);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<PositionCreated | null>(null);

  async function create(value: { name: string; session_minutes: SessionMinutes }) {
    if (!token) return;
    setPending(true);
    setError(null);
    try {
      setCreated(await adminApi.createPosition(token, value));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <ScreenTransition id={created ? "created" : "form"} order={created ? 1 : 0}>
      {created ? (
        <Created created={created} />
      ) : (
        <div className="flex w-full flex-col gap-6">
          <Reveal>
            <Link href="/admin/positions" className="text-sm text-muted hover:underline">
              ← 채용 건 목록
            </Link>
            <h1 className="mt-1 text-title1 font-bold">채용 건 만들기</h1>
          </Reveal>
          {loadError && (
            <p role="alert" className="pop-in rounded-md bg-danger-soft px-3 py-2 text-danger">
              {loadError}
            </p>
          )}
          {!catalog && !loadError && (
            <div className="flex flex-col gap-6" aria-busy aria-label="불러오는 중">
              <Skeleton className="h-18 rounded-xl" />
              <div className="grid gap-6 lg:grid-cols-2">
                <Skeleton className="h-64 rounded-xl" />
                <Skeleton className="h-64 rounded-xl" />
              </div>
            </div>
          )}
          {catalog && (
            <PositionForm catalog={catalog} pending={pending} error={error} onSubmit={create} />
          )}
        </div>
      )}
    </ScreenTransition>
  );
}
