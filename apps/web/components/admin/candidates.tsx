"use client";

// 채용 건 상세의 지원자 목록과 초대 폼. 상태 묶음 5종(필터 겸용), 요소별 수준 칩(합계·정렬 없음),
// 확신도 low 차원 수, 출처, 실패 안내, 삭제 예정일, 링크 재발급과 재응시 초대를 구분한 행 액션.
import { ChevronDown, CircleAlert, CircleHelp, Copy, Link2 } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";

import {
  Button,
  Chip,
  cn,
  Collapse,
  ConfirmDialog,
  CountUp,
  DrawnCheck,
  EmptyState,
  Input,
  Reveal,
  ScreenTransition,
  toast,
} from "@/components/ui";

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

// 카드 행의 칸 배치. 넓은 화면은 표처럼 한 줄, 좁은 화면은 위아래로 쌓인다
const ROW_GRID =
  "grid gap-x-4 gap-y-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.35fr)_minmax(0,0.9fr)_8.5rem]";

const GROUP_DOT: Record<DisplayGroup, string> = {
  waiting: "bg-subtle",
  in_progress: "bg-accent",
  needs_review: "bg-warning",
  done: "bg-success",
  problem: "bg-danger",
};

/** 넓은 화면에서는 시각적으로 숨기되 행의 칸 이름은 낭독한다. */
function CellLabel({ children }: { children: string }) {
  return <span className="mb-1 block text-caption text-subtle lg:sr-only">{children}</span>;
}

function Row({ c, onAsk, onRegenerate }: { c: Candidate } & RowActions) {
  const purge = purgeText(c.purge_at);
  return (
    <div className={cn(ROW_GRID, "rounded-xl border border-line bg-surface p-4 text-sm")}>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-semibold break-all">{c.alias}</span>
          {c.previous_session_id && <Chip tone="outline">재응시</Chip>}
        </div>
        <div className="text-sm break-all text-muted">{c.email}</div>
      </div>
      <div className="min-w-0">
        <CellLabel>상태</CellLabel>
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
      </div>
      <div className="min-w-0">
        <CellLabel>요소별 수준</CellLabel>
        <LevelChips c={c} />
      </div>
      <div className="min-w-0 text-xs text-muted tabular-nums">
        <CellLabel>기록</CellLabel>
        <div>메시지 {c.message_count}개</div>
        {c.last_activity_at && <div>마지막 활동 {formatDateTime(c.last_activity_at)}</div>}
        {purge && <div>{purge}</div>}
      </div>
      <div className="min-w-0 sm:col-span-2 lg:col-span-1">
        <div className="flex flex-wrap gap-1.5 lg:flex-col lg:items-end">
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
          <p className="mt-2 text-xs text-danger lg:text-right">할 일 · {c.next_action}</p>
        )}
      </div>
    </div>
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

const FILTERS: { id: DisplayGroup | "all"; label: string }[] = [
  { id: "all", label: "전체" },
  ...GROUPS,
];

/**
 * 지원자 목록. 위 상태 칸(전체·대기·진행·검토 필요·완료·문제)이 개요 숫자이자 필터 토글이고,
 * 필터를 바꾸면 목록이 그 방향으로 미끄러져 바뀌며 행이 차례로 떠오른다. 새로 초대한 행은 테두리가 한 번 빛난다.
 */
export function CandidateTable({
  candidates,
  onCancel,
  onReissue,
  onRetake,
  onRegenerate,
}: CandidateTableProps) {
  const [filter, setFilter] = useState<DisplayGroup | "all">("all");
  const [pending, setPending] = useState<Pending | null>(null);
  const [help, setHelp] = useState(false);
  // 처음 그릴 때 있던 행. 그 뒤에 생긴 행(새 초대·재응시)은 새 카드로 들어온다
  const [initial] = useState(() => new Set(candidates.map((c) => c.session_id)));

  if (candidates.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-line bg-surface">
        <EmptyState
          title="아직 초대한 지원자가 없습니다"
          description="위에서 별칭과 이메일을 넣어 초대 링크를 만들어 보세요."
        />
      </div>
    );
  }
  const count = (g: DisplayGroup | "all") =>
    g === "all" ? candidates.length : candidates.filter((c) => c.display_group === g).length;
  const groups = GROUPS.filter((g) => filter === "all" || g.id === filter);
  // 보이는 행의 차례(묶음을 넘어 이어서 센다)
  const order = new Map(
    groups
      .flatMap((g) => candidates.filter((c) => c.display_group === g.id))
      .map((c, i) => [c.session_id, i]),
  );

  return (
    <div className="flex flex-col gap-4">
      <div role="group" aria-label="상태 필터" className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        {FILTERS.map((g, i) => {
          const on = filter === g.id;
          return (
            <Reveal key={g.id} index={i} step={60}>
              <button
                type="button"
                aria-pressed={on}
                onClick={() => setFilter(g.id)}
                className={cn(
                  "press flex w-full flex-col items-start gap-0.5 rounded-xl border px-3 py-2 text-left transition-[background-color,border-color,color] duration-200",
                  on
                    ? "border-accent bg-accent-soft text-accent-strong"
                    : "border-line bg-surface text-muted hover:border-accent/40 hover:text-ink",
                )}
              >
                <span className="inline-flex items-center gap-1.5 text-footnote">
                  {g.id !== "all" && (
                    <span aria-hidden className={cn("size-1.5 rounded-full", GROUP_DOT[g.id])} />
                  )}
                  {g.label}
                </span>
                <span className="text-title3 font-semibold text-ink">
                  <CountUp value={count(g.id)} />
                </span>
              </button>
            </Reveal>
          );
        })}
      </div>
      <div className="flex flex-col gap-1 text-sm text-muted">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p>요소별 수준은 1~5 요약입니다. 목록은 수준이 아니라 초대한 순서로 보입니다.</p>
          <button
            type="button"
            aria-expanded={help}
            onClick={() => setHelp((v) => !v)}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-footnote text-accent hover:bg-accent-soft"
          >
            <CircleHelp aria-hidden className="size-3.5" />
            버튼 안내
            <ChevronDown
              aria-hidden
              className={cn(
                "size-3.5 transition-transform duration-300 motion-reduce:transition-none",
                help && "rotate-180",
              )}
            />
          </button>
        </div>
        <Collapse open={help}>
          <div className="mt-1 flex flex-col gap-1 rounded-lg bg-surface-sunken px-3 py-2">
            <p>{REISSUE_HELP}</p>
            <p>{RETAKE_HELP}</p>
          </div>
        </Collapse>
      </div>
      <ScreenTransition
        id={filter}
        order={FILTERS.findIndex((f) => f.id === filter)}
        className="flex flex-col gap-5"
      >
        {/* 넓은 화면의 머리줄 */}
        <div aria-hidden className={cn(ROW_GRID, "hidden px-4 text-xs text-muted lg:grid")}>
          <span>지원자</span>
          <span>상태</span>
          <span>요소별 수준</span>
          <span>기록</span>
          <span className="text-right">할 수 있는 일</span>
        </div>
        {groups.map((g) => {
          const rows = candidates.filter((c) => c.display_group === g.id);
          if (rows.length === 0) {
            return filter === "all" ? null : (
              <div key={g.id} className="rounded-xl border border-dashed border-line bg-surface">
                <EmptyState title={`${g.label} 지원자 없음`} />
              </div>
            );
          }
          return (
            <section key={g.id} aria-label={`${g.label} 지원자`} className="flex flex-col gap-2">
              <h3 className="inline-flex items-center gap-1.5 text-sm font-semibold text-muted">
                <span aria-hidden className={cn("size-1.5 rounded-full", GROUP_DOT[g.id])} />
                {g.label} <span className="tabular-nums">{rows.length}</span>
              </h3>
              <ul className="flex flex-col gap-2">
                {rows.map((c) => (
                  <Reveal
                    as="li"
                    key={c.session_id}
                    kind={initial.has(c.session_id) ? "reveal" : "pop"}
                    delay={
                      initial.has(c.session_id) ? 120 + (order.get(c.session_id) ?? 0) * 60 : 0
                    }
                    className="rounded-xl"
                  >
                    <Row c={c} onAsk={setPending} onRegenerate={onRegenerate} />
                  </Reveal>
                ))}
              </ul>
            </section>
          );
        })}
      </ScreenTransition>
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

/** 초대·재발급·재응시 응답의 링크와 안내 문안(발송하지 않고 복사). 새로 생기면 테두리가 한 번 빛난다 */
export function InviteResultBox({ title, result }: { title: string; result: InviteResult }) {
  return (
    <div
      key={result.invite_url + result.expires_at}
      className="pop-in flex flex-col gap-3 rounded-xl border border-line bg-surface p-4"
    >
      <p className="flex items-start gap-2 text-sm">
        <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-success-soft text-success">
          <DrawnCheck size={12} delay={200} />
        </span>
        <span>
          <b className="font-semibold">{title}</b>
          <span className="text-muted">
            {" "}
            · 자동 발송 없음, 복사해서 전달 · {formatDateTime(result.expires_at)} 만료
          </span>
        </span>
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 basis-60 rounded-md bg-surface-sunken px-3 py-2 text-sm break-all">
          {result.invite_url}
        </code>
        <Button size="sm" variant="secondary" onClick={() => void copy(result.invite_url, "링크")}>
          <Copy aria-hidden className="size-3.5" />
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
    <form className="flex flex-col gap-3" onSubmit={submit}>
      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-start">
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
        {/* 라벨 줄 높이만큼 내려 입력 칸과 줄을 맞춘다 */}
        <Button type="submit" loading={pending} disabled={duplicate} className="md:mt-[1.6rem]">
          <Link2 aria-hidden className="size-4" />
          초대 링크 만들기
        </Button>
      </div>
      {error && (
        <p role="alert" className="pop-in rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
    </form>
  );
}
