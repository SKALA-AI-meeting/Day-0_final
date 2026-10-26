"use client";

// 채용 건 상세: 제목·요약 → 지원자 초대(늘 펼쳐 둠, 새 링크는 카드로 떠오름) → 탭(지원자 목록 | 채용 건 정보).
// 지원자 목록은 상태 묶음·요소별 수준 칩과 행 액션(링크 재발급·재응시 초대·리포트 다시 생성·취소)이다.
import { CalendarDays, Clock, Users } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { CandidateTable, InviteForm, InviteResultBox } from "@/components/admin/candidates";
import { adminApi, errorMessage, useLoad, useToken } from "@/components/admin/client";
import { formatDateTime } from "@/components/admin/format";
import { SAME_SCALE_NOTICE } from "@/components/admin/position-form";
import type { Candidate, InviteResult, Position } from "@/components/admin/types";
import { Reveal, Skeleton, Tabs, Toaster } from "@/components/ui";

/** 채용 건 정보 탭: 세션 길이·만든 시각·보는 요소(공통 척도) */
function PositionInfo({ position }: { position: Position | undefined }) {
  const { data: catalog, error } = useLoad(adminApi.catalog);
  return (
    <div className="flex flex-col gap-4 pt-4">
      {position && (
        <dl className="grid gap-3 sm:grid-cols-3">
          {[
            { icon: Clock, label: "세션 길이", value: `${position.session_minutes}분` },
            { icon: Users, label: "초대한 지원자", value: `${position.candidate_count}명` },
            { icon: CalendarDays, label: "만든 시각", value: formatDateTime(position.created_at) },
          ].map(({ icon: Icon, label, value }, i) => (
            <Reveal
              key={label}
              index={i}
              className="flex flex-col gap-1 rounded-xl border border-line bg-surface px-4 py-3"
            >
              <dt className="inline-flex items-center gap-1.5 text-footnote text-muted">
                <Icon aria-hidden className="size-3.5" />
                {label}
              </dt>
              <dd className="font-semibold tabular-nums">{value}</dd>
            </Reveal>
          ))}
        </dl>
      )}
      <section aria-labelledby="info-elements" className="flex flex-col gap-2">
        <h3 id="info-elements" className="text-callout font-semibold">
          보는 요소
        </h3>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        {!catalog && !error && <Skeleton className="h-40 rounded-xl" />}
        {catalog && (
          <ul className="divide-y divide-line rounded-xl border border-line bg-surface">
            {catalog.elements.map((e, i) => (
              <Reveal
                as="li"
                key={e.id}
                delay={150 + i * 70}
                className="flex flex-col gap-0.5 px-4 py-3"
              >
                <span className="font-semibold">{e.name}</span>
                <span className="text-sm">{e.meaning}</span>
                <span className="text-sm text-muted">이렇게 봅니다 · {e.how_we_see}</span>
              </Reveal>
            ))}
          </ul>
        )}
        <p className="text-sm text-muted">
          {SAME_SCALE_NOTICE}{" "}
          <Link
            href="/admin/sample-report"
            className="text-accent underline-offset-4 hover:underline"
          >
            샘플 리포트 보기
          </Link>
        </p>
      </section>
    </div>
  );
}

export default function PositionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const token = useToken();
  const { data, error, reload } = useLoad(async (t) => {
    const [positions, candidates] = await Promise.all([
      adminApi.positions(t),
      adminApi.candidates(t, id),
    ]);
    return { position: positions.find((p) => p.id === id), candidates };
  }, id);
  const [pending, setPending] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [result, setResult] = useState<{ title: string; value: InviteResult } | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [tab, setTab] = useState("candidates");

  async function run(title: string, action: () => Promise<InviteResult | unknown>) {
    setActionError(null);
    try {
      const value = await action();
      if (value && typeof value === "object" && "invite_url" in value) {
        setResult({ title, value: value as InviteResult });
      }
    } catch (e) {
      setActionError(errorMessage(e));
    }
    reload();
  }

  async function invite(value: { alias: string; email: string }) {
    if (!token) return;
    setPending(true);
    setInviteError(null);
    try {
      setResult({
        title: `${value.alias} 초대 링크`,
        value: await adminApi.invite(token, id, value),
      });
      setFormKey((k) => k + 1);
      setTab("candidates");
      reload();
    } catch (e) {
      setInviteError(errorMessage(e));
    } finally {
      setPending(false);
    }
  }

  const position = data?.position;
  return (
    <>
      <Reveal>
        <Link href="/admin/positions" className="text-sm text-muted hover:underline">
          ← 채용 건 목록
        </Link>
        {data || error ? (
          <h1 className="mt-1 text-title1 font-bold break-all">{position?.name ?? "채용 건"}</h1>
        ) : (
          <Skeleton className="mt-2 h-8 w-64 max-w-full" />
        )}
        {position && (
          <p className="reveal mt-1 text-sm text-muted">
            세션 {position.session_minutes}분 · 5요소 공통 척도 ·{" "}
            <Link
              href="/admin/sample-report"
              className="text-accent underline-offset-4 hover:underline"
            >
              샘플 리포트 보기
            </Link>
          </p>
        )}
      </Reveal>
      {error && (
        <p role="alert" className="pop-in rounded-md bg-danger-soft px-3 py-2 text-danger">
          {error}
        </p>
      )}
      {actionError && (
        <p role="alert" className="pop-in rounded-md bg-danger-soft px-3 py-2 text-danger">
          {actionError}
        </p>
      )}

      <Reveal
        as="section"
        delay={100}
        aria-labelledby="invite-title"
        className="flex flex-col gap-4 rounded-xl border border-line bg-surface p-4 sm:p-5"
      >
        <h2 id="invite-title" className="text-title3 font-semibold">
          지원자 초대
        </h2>
        <InviteForm
          key={formKey}
          takenAliases={data?.candidates.map((c) => c.alias) ?? []}
          pending={pending}
          error={inviteError}
          onInvite={invite}
        />
        {result && (
          <InviteResultBox
            key={result.value.invite_url + result.value.expires_at}
            title={result.title}
            result={result.value}
          />
        )}
      </Reveal>

      <Reveal delay={200}>
        <Tabs
          value={tab}
          onValueChange={setTab}
          className="[&_[role=tablist]]:px-0"
          panelClassName="overflow-visible"
          items={[
            {
              value: "candidates",
              label: (
                <>
                  지원자
                  {data && (
                    <span className="ml-1.5 text-muted tabular-nums">{data.candidates.length}</span>
                  )}
                </>
              ),
              content: (
                <div className="pt-4">
                  {!data && !error && (
                    <div className="flex flex-col gap-3" aria-busy aria-label="불러오는 중">
                      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
                        {[0, 1, 2, 3, 4, 5].map((i) => (
                          <Skeleton key={i} className="h-16 rounded-xl" />
                        ))}
                      </div>
                      {[0, 1, 2].map((i) => (
                        <Skeleton key={i} className="h-24 rounded-xl" />
                      ))}
                    </div>
                  )}
                  {data && token && (
                    <CandidateTable
                      candidates={data.candidates}
                      onCancel={(c: Candidate) =>
                        run("취소", () => adminApi.cancel(token, c.session_id))
                      }
                      onReissue={(c) =>
                        run(`${c.alias} 새 링크(재발급)`, () =>
                          adminApi.reissue(token, c.session_id),
                        )
                      }
                      onRetake={(c) =>
                        run(`${c.alias} 재응시 초대 링크`, () =>
                          adminApi.invite(token, id, {
                            email: c.email,
                            previous_session_id: c.session_id,
                          }),
                        )
                      }
                      onRegenerate={(c) =>
                        run("리포트 다시 생성", () => adminApi.regenerate(token, c.session_id))
                      }
                    />
                  )}
                </div>
              ),
            },
            {
              value: "info",
              label: "채용 건 정보",
              content: <PositionInfo position={position} />,
            },
          ]}
        />
      </Reveal>
      <Toaster />
    </>
  );
}
