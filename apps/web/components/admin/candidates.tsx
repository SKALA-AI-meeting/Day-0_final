"use client";

// 채용 건 상세의 지원자 목록과 초대 폼. 상태 묶음 5종(필터 겸용), 요소별 수준 칩(합계·정렬 없음),
// 확신도 low 차원 수, 출처, 실패 안내, 삭제 예정일, 링크 재발급과 재응시 초대를 구분한 행 액션.
import { CircleAlert } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";

import { Button, Chip, cn, ConfirmDialog, EmptyState, Input, toast } from "@/components/ui";

import { ELEMENT_SHORT, formatDateTime, PHASE_TEXT, purgeText } from "./format";
import type { Candidate, DisplayGroup, InviteResult, PhaseId } from "./types";

export const GROUPS: { id: DisplayGroup; label: string }[] = [
  { id: "waiting", label: "대기" },
  { id: "in_progress", label: "진행" },
  { id: "needs_review", label: "검토 필요" },
  { id: "done", label: "완료" },
  { id: "problem", label: "문제" },
];

const STATUS_TEXT: Record<string, string> = {
  invited: "초대됨",
  in_progress: "진행 중",
  completed: "완료",
  report_ready: "리포트 준비됨",
  approving: "확정 중",
  reviewed: "검토 완료",
  failed: "실패",
  cancelled: "취소",
};

export const REISSUE_HELP =
  "링크 재발급: 시작 전·만료된 초대의 링크만 새로 만듭니다(같은 세션·같은 별칭).";
export const RETAKE_HELP =
  "재응시 초대: 세션 실패·취소된 지원자에게 새 세션으로 다시 응시할 링크를 만듭니다.";

export function statusText(c: Candidate) {
  if (c.display_status === "expired") return "초대됨(만료)";
  if (c.status === "failed") return c.failed_stage === "report" ? "실패(리포트)" : "실패(세션)";
  if (c.status === "in_progress" && c.phase && c.phase in PHASE_TEXT) {
    return `진행 중 · ${PHASE_TEXT[c.phase as PhaseId]}`;
  }
  return STATUS_TEXT[c.status] ?? c.status;
}

/** 링크 재발급: 시작 전(invited, 만료 포함) 세션만 */
export const canReissue = (c: Candidate) => c.status === "invited";
/** 재응시 초대: 세션 실패·취소만(새 세션) */
export const canRetake = (c: Candidate) =>
  (c.status === "failed" && c.failed_stage === "session") || c.status === "cancelled";
export const canRegenerate = (c: Candidate) => c.status === "failed" && c.failed_stage === "report";
export const canCancel = (c: Candidate) =>
  c.display_status !== "expired" && ["invited", "in_progress"].includes(c.status);
const hasReport = (c: Candidate) => ["report_ready", "approving", "reviewed"].includes(c.status);

const GROUP_TONE: Record<DisplayGroup, "neutral" | "accent" | "warning" | "success" | "danger"> = {
  waiting: "neutral",
  in_progress: "accent",
  needs_review: "warning",
  done: "success",
  problem: "danger",
};

/** 요소별 수준 칩. 높고 낮음을 색으로 나누지 않고 중립 톤 하나만 쓴다 */
export function LevelChips({ c }: { c: Candidate }) {
  if (!c.elements) return <span className="text-sm text-subtle">리포트 전</span>;
  return (
    <div className="flex flex-col gap-1.5">
      <ul className="flex flex-wrap gap-1" aria-label="요소별 수준">
        {c.elements.map((e) => (
          <li
            key={e.element}
            title={e.adjusted ? "담당자 조정됨" : undefined}
            className="rounded-sm bg-surface-sunken px-1.5 py-px text-xs font-medium whitespace-nowrap tabular-nums"
          >
            {ELEMENT_SHORT[e.element] ?? e.element} {e.level ?? "—"}
            {e.adjusted && <span className="font-normal text-muted"> · 조정</span>}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        {!!c.low_confidence_count && (
          <span
            className="inline-flex items-center gap-1 text-warning"
            title="확신도 low로 판단한 세부 행동 수"
          >
            <CircleAlert aria-hidden className="size-3.5" />
            근거 약함 {c.low_confidence_count}
          </span>
        )}
        <span>출처 {c.level_source === "reviewer" ? "담당자 조정" : "AI 판단"}</span>
      </div>
    </div>
  );
}

type RowActions = {
  onAsk: (action: Pending) => void;
  onRegenerate: (c: Candidate) => void;
};

type Pending = { kind: "cancel" | "reissue" | "retake"; candidate: Candidate };

function Row({ c, onAsk, onRegenerate }: { c: Candidate } & RowActions) {
  const purge = purgeText(c.purge_at);
  return (
    <tr className="align-top">
      <td className="py-3 pr-4">
        <div className="font-semibold break-all">{c.alias}</div>
        <div className="text-sm break-all text-muted">{c.email}</div>
        {c.previous_session_id && <Chip tone="outline">재응시</Chip>}
      </td>
      <td className="py-3 pr-4">
        <Chip tone={GROUP_TONE[c.display_group]}>{statusText(c)}</Chip>
        <div className="mt-1 flex flex-col text-xs text-muted tabular-nums">
          {c.started_at && <span>{formatDateTime(c.started_at)} 시작</span>}
          {c.ends_at && (
            <span>
              {formatDateTime(c.ended_at ?? c.ends_at)} {c.ended_at ? "종료" : "종료 예정"}
            </span>
          )}
          {c.reissued_at && <span>{formatDateTime(c.reissued_at)} 링크 재발급</span>}
        </div>
      </td>
      <td className="py-3 pr-4">
        <LevelChips c={c} />
      </td>
      <td className="py-3 pr-4 text-xs text-muted tabular-nums">
        <div>메시지 {c.message_count}개</div>
        {c.last_activity_at && <div>마지막 활동 {formatDateTime(c.last_activity_at)}</div>}
        {purge && <div>{purge}</div>}
      </td>
      <td className="py-3">
        <div className="flex flex-col items-end gap-1.5">
          {hasReport(c) && (
            <Link href={`/admin/sessions/${c.session_id}/report`}>
              <Button size="xs" variant={c.status === "report_ready" ? "primary" : "secondary"}>
                리포트
              </Button>
            </Link>
          )}
          {canReissue(c) && (
            <Button
              size="xs"
              variant="secondary"
              onClick={() => onAsk({ kind: "reissue", candidate: c })}
            >
              링크 재발급
            </Button>
          )}
          {canRetake(c) && (
            <Button
              size="xs"
              variant="secondary"
              onClick={() => onAsk({ kind: "retake", candidate: c })}
            >
              재응시 초대
            </Button>
          )}
          {canRegenerate(c) && (
            <Button size="xs" variant="secondary" onClick={() => onRegenerate(c)}>
              리포트 다시 생성
            </Button>
          )}
          {canCancel(c) && (
            <Button
              size="xs"
              variant="ghost"
              onClick={() => onAsk({ kind: "cancel", candidate: c })}
            >
              {c.status === "invited" ? "초대 취소" : "세션 취소"}
            </Button>
          )}
        </div>
        {c.status === "failed" && c.next_action && (
          <p className="mt-2 text-right text-xs text-danger">할 일 · {c.next_action}</p>
        )}
      </td>
    </tr>
  );
}

const DIALOG: Record<Pending["kind"], { title: string; description: string; confirm: string }> = {
  cancel: {
    title: "이 세션을 취소할까요?",
    description: "링크가 더 이상 열리지 않고 진행 중이면 바로 끝납니다. 리포트는 만들지 않습니다.",
    confirm: "취소하기",
  },
  reissue: {
    title: "링크를 다시 발급할까요?",
    description:
      "같은 세션·같은 별칭으로 새 링크를 만들고 72시간 뒤 만료됩니다. 이전 링크는 열리지 않습니다.",
    confirm: "링크 재발급",
  },
  retake: {
    title: "재응시 초대를 만들까요?",
    description:
      "이전 세션과 연결된 새 세션의 초대 링크를 만듭니다. 별칭과 이메일은 그대로 씁니다.",
    confirm: "재응시 초대",
  },
};

export type CandidateTableProps = {
  candidates: Candidate[];
  onCancel: (c: Candidate) => void;
  onReissue: (c: Candidate) => void;
  onRetake: (c: Candidate) => void;
  onRegenerate: (c: Candidate) => void;
};

export function CandidateTable({
  candidates,
  onCancel,
  onReissue,
  onRetake,
  onRegenerate,
}: CandidateTableProps) {
  const [filter, setFilter] = useState<DisplayGroup | "all">("all");
  const [pending, setPending] = useState<Pending | null>(null);

  if (candidates.length === 0) {
    return <EmptyState title="아직 초대한 지원자가 없습니다" />;
  }
  const count = (g: DisplayGroup) => candidates.filter((c) => c.display_group === g).length;
  const groups = GROUPS.filter((g) => filter === "all" || g.id === filter);

  return (
    <div className="flex flex-col gap-4">
      <div role="group" aria-label="상태 필터" className="flex flex-wrap gap-1.5">
        {[{ id: "all" as const, label: "전체" }, ...GROUPS].map((g) => (
          <button
            key={g.id}
            type="button"
            aria-pressed={filter === g.id}
            onClick={() => setFilter(g.id)}
            className={cn(
              "rounded-md border px-2.5 py-1 text-sm",
              filter === g.id
                ? "border-accent bg-accent-soft text-accent-strong"
                : "border-line bg-surface text-muted hover:bg-surface-sunken",
            )}
          >
            {g.label}{" "}
            <span className="tabular-nums">{g.id === "all" ? candidates.length : count(g.id)}</span>
          </button>
        ))}
      </div>
      <div className="text-sm text-muted">
        <p>요소별 수준은 1~5 요약입니다. 목록은 수준이 아니라 초대한 순서로 보입니다.</p>
        <p>{REISSUE_HELP}</p>
        <p>{RETAKE_HELP}</p>
      </div>
      {groups.map((g) => {
        const rows = candidates.filter((c) => c.display_group === g.id);
        if (rows.length === 0) {
          return filter === "all" ? null : (
            <EmptyState key={g.id} title={`${g.label} 지원자 없음`} />
          );
        }
        return (
          <section key={g.id} className="flex flex-col gap-1">
            <h3 className="text-sm font-semibold text-muted">
              {g.label} <span className="tabular-nums">{rows.length}</span>
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] table-fixed border-y border-line text-left text-sm">
                <thead className="text-xs text-muted">
                  <tr className="border-b border-line">
                    <th className="py-2 w-[20%] font-medium">지원자</th>
                    <th className="py-2 w-[20%] font-medium">상태</th>
                    <th className="py-2 w-[26%] font-medium">요소별 수준</th>
                    <th className="py-2 w-[18%] font-medium">기록</th>
                    <th className="py-2 text-right font-medium">할 수 있는 일</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {rows.map((c) => (
                    <Row key={c.session_id} c={c} onAsk={setPending} onRegenerate={onRegenerate} />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
      <ConfirmDialog
        open={pending != null}
        onOpenChange={(open) => !open && setPending(null)}
        title={pending ? DIALOG[pending.kind].title : ""}
        description={pending ? DIALOG[pending.kind].description : undefined}
        cancelLabel="돌아가기"
        confirmLabel={pending ? DIALOG[pending.kind].confirm : "확인"}
        tone={pending?.kind === "cancel" ? "danger" : "primary"}
        onConfirm={() => {
          if (pending?.kind === "cancel") onCancel(pending.candidate);
          if (pending?.kind === "reissue") onReissue(pending.candidate);
          if (pending?.kind === "retake") onRetake(pending.candidate);
          setPending(null);
        }}
      />
    </div>
  );
}

async function copy(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${what}를 복사했어요`);
  } catch {
    toast(`${what}를 복사하지 못했어요. 직접 선택해 주세요.`);
  }
}

/** 초대·재발급·재응시 응답의 링크와 안내 문안(발송하지 않고 복사) */
export function InviteResultBox({ title, result }: { title: string; result: InviteResult }) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-line bg-surface p-4">
      <p className="text-sm">
        <b className="font-semibold">{title}</b>
        <span className="text-muted">
          {" "}
          · 자동 발송 없음, 복사해서 전달 · {formatDateTime(result.expires_at)} 만료
        </span>
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 rounded-md bg-surface-sunken px-3 py-2 text-sm break-all">
          {result.invite_url}
        </code>
        <Button size="sm" variant="secondary" onClick={() => void copy(result.invite_url, "링크")}>
          링크 복사
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => void copy(result.notice_text, "안내 문안")}
        >
          안내 문안 복사
        </Button>
      </div>
    </div>
  );
}

export type InviteFormProps = {
  /** 같은 채용 건 안에서 이미 쓴 별칭(제출 전에 막는다) */
  takenAliases: string[];
  pending?: boolean;
  error?: string | null;
  onInvite: (value: { alias: string; email: string }) => void;
};

export function InviteForm({ takenAliases, pending, error, onInvite }: InviteFormProps) {
  const [alias, setAlias] = useState("");
  const [email, setEmail] = useState("");
  const duplicate = takenAliases.includes(alias.trim());
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (duplicate) return;
    onInvite({ alias: alias.trim(), email: email.trim() });
  };
  return (
    <form className="flex flex-col gap-4" onSubmit={submit}>
      <div className="grid gap-4 md:grid-cols-2">
        <Input
          label="별칭"
          value={alias}
          onChange={(e) => setAlias(e.target.value)}
          hint="지원자에게 이 이름으로 안내되고 세션과 리포트에 남습니다"
          error={duplicate ? "이 채용 건에 이미 있는 별칭입니다" : undefined}
          maxLength={40}
          required
        />
        <Input
          label="이메일"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
      </div>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <div>
        <Button type="submit" loading={pending} disabled={duplicate}>
          초대 링크 만들기
        </Button>
      </div>
    </form>
  );
}
