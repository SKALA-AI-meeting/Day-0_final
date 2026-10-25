"use client";

// 리포트 화면. 검토 제출 뒤에는 final이 될 때까지 폴링하고, 오래 끝나지 않으면 같은 본문으로 확정만 다시 시도한다.
// 주소에 ?print=1이면 인쇄 미리보기(차원 전부 펼침)로 그린다.
import { ArrowLeft, ArrowRight } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { adminApi, errorMessage, useLoad, useToken } from "@/components/admin/client";
import { ReportFailed } from "@/components/admin/report-failed";
import { ReportGenerating, ReportSkeleton } from "@/components/admin/report-loading";
import { ReportView } from "@/components/admin/report-view";
import {
  phaseOf,
  recordAnchor,
  nextWaiting,
  pollDelay,
  reportScreen,
  reportTransient,
  reviewBodyFrom,
} from "@/components/admin/review";
import type { ReviewBody } from "@/components/admin/types";
import { Button, Toaster } from "@/components/ui";

const POLL_MS = 2000;
// 이만큼 폴링해도 확정 중이면 "확정 다시 시도"를 보인다
const STALL_POLLS = 5;
const MAX_FAILURES = 5;

function ReportPageBody() {
  const { id } = useParams<{ id: string }>();
  const printing = useSearchParams().get("print") === "1";
  const token = useToken();
  const {
    data: report,
    error,
    code,
    extra,
    failures,
    reload,
  } = useLoad((t) => adminApi.report(t, id), id);
  const [pending, setPending] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [polls, setPolls] = useState(0);
  const lastBody = useRef<ReviewBody | null>(null);

  // 생성 중(report_not_ready)이면 안내를 보이고 5초마다 다시 읽는다. 일시 오류(5xx·네트워크)는 간격을 늘려 계속 읽는다
  const [waiting, setWaiting] = useState(false);
  if (waiting !== nextWaiting(waiting, error, code)) setWaiting(!waiting);
  const backoff = useRef(1);
  useEffect(() => {
    if (!waiting) return;
    const timer = setTimeout(() => {
      backoff.current = reportTransient(error, code) ? backoff.current * 2 : 1;
      reload();
    }, pollDelay(backoff.current));
    return () => clearTimeout(timer);
  }, [waiting, error, code, failures, reload]);

  // 실패 화면의 [다시 만들기]: 재생성을 요청하고 다시 읽으면 생성 중(report_not_ready) 폴링으로 이어진다
  const screen = reportScreen(report, code, waiting);
  const failed = screen === "failed";
  const missing = screen === "missing";

  const approving = screen === "report" && !!report && phaseOf(report) === "approving";
  const polling = approving && failures < MAX_FAILURES && polls < STALL_POLLS;
  useEffect(() => {
    if (!polling) return;
    const timer = setInterval(() => {
      setPolls((n) => n + 1);
      reload();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [polling, reload]);
  const [regenError, setRegenError] = useState<string | null>(null);
  async function regenerate() {
    if (!token) return;
    setPending(true);
    setRegenError(null);
    try {
      await adminApi.regenerate(token, id);
      reload();
    } catch (e) {
      setRegenError(errorMessage(e));
    } finally {
      setPending(false);
    }
  }

  async function review(body: ReviewBody) {
    if (!token) return;
    setPending(true);
    setReviewError(null);
    lastBody.current = body;
    try {
      await adminApi.review(token, id, body);
      setPolls(0);
      reload();
    } catch (e) {
      setReviewError(errorMessage(e));
      // 409 already_reviewed면 최신 리포트를 다시 읽는다
      reload();
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <div className="no-print reveal mx-auto flex w-full max-w-5xl items-center gap-3 text-sm text-muted">
        <Link
          href="/admin/positions"
          className="inline-flex items-center gap-1 rounded-md px-1 hover:text-ink"
        >
          <ArrowLeft aria-hidden className="size-4" />
          채용 건 목록
        </Link>
        <Link
          href={`/admin/sessions/${id}/record`}
          className="ml-auto inline-flex items-center gap-1 rounded-md px-1 hover:text-ink"
        >
          전체 기록 보기
          <ArrowRight aria-hidden className="size-4" />
        </Link>
      </div>
      {screen === "generating" && <ReportGenerating />}
      {missing && (
        <p
          role="status"
          className="reveal mx-auto w-full max-w-3xl rounded-xl border border-dashed border-line px-5 py-8 text-center text-muted"
        >
          이 지원자의 리포트가 아직 없습니다.
        </p>
      )}
      {failed && (
        <ReportFailed
          failedStep={typeof extra.failed_step === "string" ? extra.failed_step : null}
          pending={pending}
          error={regenError}
          onRegenerate={regenerate}
        />
      )}
      {error &&
        screen !== "failed" &&
        screen !== "missing" &&
        !(screen === "generating" && !reportTransient(error, code)) && (
          <div
            role="alert"
            className="pop-in mx-auto flex w-full max-w-3xl flex-wrap items-center gap-3 rounded-xl bg-danger-soft px-4 py-3 text-danger"
          >
            <span>{error}</span>
            <Button size="sm" variant="secondary" onClick={reload} className="ml-auto">
              다시 불러오기
            </Button>
          </div>
        )}
      {screen === "empty" && !error && <ReportSkeleton />}
      {report && screen === "report" && (
        <ReportView
          report={report}
          printing={printing}
          recordHref={(ref) => `/admin/sessions/${id}/record#${recordAnchor(ref)}`}
          pending={pending}
          error={reviewError}
          stalled={approving && polls >= STALL_POLLS}
          onReview={review}
          onRetry={() => {
            const body = lastBody.current ?? reviewBodyFrom(report);
            if (body) void review(body);
          }}
          onAddNote={async (body) => {
            if (!token) return;
            await adminApi.addNote(token, id, body);
            reload();
          }}
        />
      )}
      <Toaster />
    </>
  );
}

export default function ReportPage() {
  return (
    <Suspense>
      <ReportPageBody />
    </Suspense>
  );
}
