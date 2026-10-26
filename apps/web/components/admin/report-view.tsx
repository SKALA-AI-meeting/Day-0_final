"use client";

// 리포트 화면. 한 번에 다 펼치지 않고 순서대로 읽게 한다:
// 머리(참여자·검토 체크포인트) → 읽기 전 안내(용어 설명) → 개요(숫자·요소 요약표) → 토론 흐름(세션 띠·단계·카드 장면)
// → 요소별 판단(세부 행동은 접힘, 근거는 일부만 보이고 더 펼침) → 참고 기록(사실 지표·기획안·AI 활용 탭) → 검토.
// 넓은 화면은 왼쪽 목차가 지금 읽는 구역을 따라가고, 좁은 화면은 위에 붙은 가로 목차를 쓴다.
// 아래 구역은 화면에 들어올 때 떠오른다(InView). 총점·합계는 어디에도 없다.
// 인쇄(printing)에서는 세부 행동·근거·탭을 전부 펼치고, 근거 앞뒤 맥락은 근거 메시지만 보인다.
// 화면에 보이는 말은 "차원" 대신 "세부 행동"이고(결정 A4), 기록 없음은 상태(장면 없음·반응 없음·판단 보류)와
// 사유를 함께 보인다(결정 A3). 코드의 이름은 계약 그대로 dimension이다.
import { ChevronRight, CircleCheck, Clock3, ExternalLink, Printer } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { flushSync } from "react-dom";

import {
  Avatar,
  Button,
  Checkpoints,
  Chip,
  cn,
  Collapse,
  ConfidenceTag,
  ConfirmDialog,
  CountUp,
  DrawnCheck,
  Input,
  LevelChip,
  Textarea,
  Tabs,
  type Checkpoint,
} from "@/components/ui";

import {
  DIMENSION_DEFINITION,
  formatDateTime,
  materialTitle,
  displayTerms,
  NO_RECORD_TEXT,
  officeClock,
  ORIGIN_TEXT,
  PERSONA_TEXT,
  PHASE_TEXT,
  PROPOSAL_FIELDS,
  proposalFieldLabel,
  purgeText,
  nameOf,
  RECORD_STATE,
  RECORD_STATE_TEXT,
  REPORT_TERMS,
  stateOf,
} from "./format";
import { InView, useScrollSpy } from "./in-view";
import { resolutionLabel } from "@/lib/resolution";
import {
  adjustmentsOf,
  editProblem,
  elementLevel,
  finalOf,
  isAdjusted,
  PHASE_LABEL,
  phaseOf,
  withEdits,
  type Edit,
  type Edits,
  type Phase,
} from "./review";
import { Segmented } from "./segmented";
import type {
  ContextMessage,
  Dimension,
  Evidence,
  Level,
  MessageEvidence,
  NoRecordReason,
  RecordRef,
  RecordState,
  Report,
  ReportCard,
  ReportElement,
  ReviewBody,
  Sender,
} from "./types";

export const REPORT_NOTICE =
  "이 리포트는 담당자가 평가할 때 보는 참고 자료입니다. 수준은 세션 뒤 AI 평가 에이전트가 공개된 척도로 근거를 들어 판단한 값이며, 담당자가 원문을 보고 세부 행동 단위로 조정해 최종 평가합니다. 합격·불합격·순위는 판정하지 않습니다.";
export const DESIGNED_SCENE_NOTICE =
  "동료의 반대·압박·머뭇거림 같은 장면은 같은 채용 건의 모든 지원자에게 같게 설계된 장면입니다. 지원자 안이 채택되지 않은 것은 설계의 결과일 수 있습니다.";
export const REVIEW_CONFIRM =
  "검토 완료 후 수준은 바꿀 수 없습니다. 메모는 계속 추가할 수 있습니다.";
export const FACTS_NOTE = "수준 판단에 쓰인 참고 기록이며 이것만으로 수준을 정하지 않습니다.";
export const AI_HEADER = "판단 기준 없음, 면접 확인용";
// 발동했지만 담당 동료 전원이 카드 발언을 게시하지 못한 카드(delivered=false). 평가는 핵심 장면으로 세지 않는다
export const PARTIAL_DELIVERY = "일부 동료만 전달됨";
export const SAMPLE_NOTICE =
  "예시 데이터로 만든 샘플 리포트입니다. 화면의 모든 표시 상태(기록 상태 3종과 사유 6종, 미발생 카드, 담당자 조정)를 한 번에 보이도록 꾸민 값입니다.";
// 카드 장면이 지원자 선확정으로 생기지 않았을 때(Report cards[].preempted)
export const PREEMPTED_CARD = "선결정";

const REASONS = Object.keys(NO_RECORD_TEXT) as NoRecordReason[];
const STATES = Object.keys(RECORD_STATE_TEXT) as RecordState[];
const LEVELS: Level[] = [1, 2, 3, 4, 5];
const PHASES = ["keyword", "problem", "solution", "proposal"] as const;
// 세부 행동을 펼쳤을 때 먼저 보이는 근거 수. 나머지는 "더 보기"로 펼친다(인쇄는 전부)
const EVIDENCE_PREVIEW = 2;

/** 여러 세부 행동이 같은 원문을 인용해도 기록 한 건으로 센다. */
export function countedRecordCount(dimensions: Dimension[]) {
  return new Set(
    dimensions.flatMap((d) =>
      d.evidence.filter((e) => e.counted).map((e) => `${e.record_ref.type}:${e.record_ref.id}`),
    ),
  ).size;
}

const BUBBLE: Record<Sender, string> = {
  candidate: "bg-bubble-candidate text-on-bubble-candidate",
  p1: "bg-bubble-p1 text-on-bubble",
  p2: "bg-bubble-p2 text-on-bubble",
  p3: "bg-bubble-p3 text-on-bubble",
  system: "text-system-notice",
};

/** 검토 흐름 체크포인트(실제 상태를 따른다). 샘플에는 없다 */
const REVIEW_STEPS: Checkpoint[] = [
  { id: "read", label: "근거 읽기" },
  { id: "adjust", label: "조정 (선택)" },
  { id: "submit", label: "검토 제출" },
  { id: "final", label: "확정" },
];

type Ctx = {
  report: Report;
  names: Partial<Record<Sender, string>>;
  avatars: Partial<Record<Sender, string | undefined>>;
  clock: (t: number) => string;
  recordHref: (ref: RecordRef) => string;
  printing: boolean;
};

const delay = (ms: number): CSSProperties => ({ ["--reveal-delay" as string]: `${ms}ms` });

/** 평가 출력의 "(앵커 N)" 표기와 "차원"은 내부 용어라 화면에서 지우거나 화면 용어로 바꾼다 */
const cleanRationale = (text: string) => displayTerms(text.replace(/\s*\(앵커 \d\)/g, ""));

function levelChip(level: Level | null, reason: NoRecordReason | null, adjusted?: boolean) {
  return level != null ? (
    <LevelChip level={level} adjusted={adjusted} />
  ) : (
    <LevelChip
      level={null}
      state={reason ? stateOf(reason) : undefined}
      reason={reason ? NO_RECORD_TEXT[reason] : "기록 없음"}
      adjusted={adjusted}
    />
  );
}

function elementChip(level: Level | null, adjusted: boolean) {
  return level != null ? (
    <LevelChip level={level} adjusted={adjusted} />
  ) : (
    <LevelChip level={null} reason="모든 세부 행동 기록 없음" adjusted={adjusted} />
  );
}

/** 발동하지 않은 카드 표시: 선결정이면 "선결정", 아니면 "장면 없음" */
const unfiredText = (card: ReportCard) => (card.preempted ? PREEMPTED_CARD : "장면 없음");

/** 머리의 용어 설명. 기본은 접힘, 인쇄에서는 펼친다 */
function TermsGuide({ printing }: { printing: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      id="terms"
      open={printing || undefined}
      onToggle={(e) => setOpen(e.currentTarget.open)}
      className="group rounded-lg border border-line bg-surface px-4 py-3 text-sm"
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 font-medium">
        <ChevronRight
          aria-hidden
          className="size-4 text-subtle transition-transform duration-200 group-open:rotate-90"
        />
        용어 설명
      </summary>
      <dl className="mt-3 grid gap-x-4 gap-y-2 sm:grid-cols-[6.5rem_1fr]">
        {REPORT_TERMS.map((t, i) => (
          <div key={t.term} className="contents">
            <dt className={cn("font-medium", open && "reveal")} style={delay(i * 40)}>
              {t.term}
            </dt>
            <dd className={cn("text-muted", open && "reveal")} style={delay(i * 40 + 20)}>
              {t.text}
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

function DesignedChip() {
  return <Chip tone="outline">설계된 장면</Chip>;
}

function Section({
  id,
  title,
  lead,
  children,
}: {
  id: string;
  title: string;
  lead?: ReactNode;
  children: ReactNode;
}) {
  return (
    <InView
      as="section"
      id={id}
      aria-labelledby={`${id}-title`}
      className="flex scroll-mt-32 flex-col gap-4 lg:scroll-mt-20"
    >
      <div className="flex flex-col gap-1">
        <h2 id={`${id}-title`} className="text-title2 font-semibold">
          {title}
        </h2>
        {lead && <p className="text-sm text-muted">{lead}</p>}
      </div>
      {children}
    </InView>
  );
}

function RecordLink({ ctx, refValue }: { ctx: Ctx; refValue: RecordRef }) {
  return (
    <Link
      href={ctx.recordHref(refValue)}
      className="no-print inline-flex items-center gap-1 text-xs text-accent underline-offset-4 hover:underline"
    >
      기록에서 보기
      <ExternalLink aria-hidden className="size-3" />
    </Link>
  );
}

function Who({ ctx, who, size = 20 }: { ctx: Ctx; who: Sender; size?: 20 | 28 }) {
  if (who === "system") return <span className="text-xs text-muted">시스템</span>;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted">
      <Avatar seat={who} src={ctx.avatars[who]} name={nameOf(ctx.names, who)} size={size} />
      {nameOf(ctx.names, who)}
    </span>
  );
}

/** 맥락 펼침의 메시지 한 줄 */
function ContextLine({ ctx, m }: { ctx: Ctx; m: ContextMessage }) {
  return (
    <li className="flex flex-col gap-1 px-2 py-1.5 opacity-80">
      <span className="flex items-center gap-2">
        <Who ctx={ctx} who={m.sender} />
        <span className="text-xs text-muted tabular-nums">{ctx.clock(m.t_min)}</span>
      </span>
      <span
        className={cn(
          "w-fit max-w-[85%] rounded-bubble px-3 py-1.5 text-sm break-words",
          BUBBLE[m.sender],
          m.sender === "candidate" && "self-end",
        )}
      >
        {m.content}
      </span>
    </li>
  );
}

/**
 * 메시지 근거. 근거 말풍선이 늘 보이고, 앞뒤 맥락은 처음 펼칠 때 그려 위아래로 부드럽게 열린다.
 * 인쇄에서는 맥락을 그리지 않는다(근거 메시지만)
 */
function MessageEvidenceItem({ ctx, e }: { ctx: Ctx; e: MessageEvidence }) {
  const [mounted, setMounted] = useState(false);
  const [open, setOpen] = useState(false);
  const showContext = !ctx.printing && mounted && !!e.context;
  function toggle() {
    if (!e.context) return;
    if (!mounted) {
      setMounted(true);
      // 닫힌 채로 한 번 그린 뒤 열어야 높이 전환이 보인다
      requestAnimationFrame(() => requestAnimationFrame(() => setOpen(true)));
    } else setOpen((v) => !v);
  }
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted tabular-nums">
        <span>{ctx.clock(e.t_min)}</span>
        <span>{PHASE_TEXT[e.phase]}</span>
        {e.origin && <span>{ORIGIN_TEXT[e.origin]}</span>}
      </div>
      <div className={cn("flex flex-col rounded-lg", open && "border border-line p-1")}>
        {showContext && (
          <Collapse open={open} className="no-print">
            <ol className="flex flex-col gap-1">
              {e.context!.before.map((m) => (
                <ContextLine key={m.id} ctx={ctx} m={m} />
              ))}
            </ol>
          </Collapse>
        )}
        <button
          type="button"
          disabled={ctx.printing || !e.context}
          onClick={toggle}
          aria-label="앞뒤 맥락 펼치기"
          aria-expanded={e.context ? open : undefined}
          className={cn(
            "flex w-full flex-col items-start gap-1 rounded-md px-2 py-1.5 text-left transition-colors duration-300 disabled:cursor-default",
            open && "bg-accent-soft",
          )}
        >
          <span className="flex items-center gap-2">
            <Who ctx={ctx} who={e.speaker} />
            {open && <span className="text-xs font-medium text-accent">근거</span>}
          </span>
          <span
            className={cn(
              "max-w-[85%] rounded-bubble px-3 py-1.5 text-sm break-words",
              BUBBLE[e.speaker],
              e.speaker === "candidate" && open && "self-end",
            )}
          >
            {e.quote}
          </span>
        </button>
        {showContext && (
          <Collapse open={open} className="no-print">
            <ol className="flex flex-col gap-1">
              {e.context!.after.map((m) => (
                <ContextLine key={m.id} ctx={ctx} m={m} />
              ))}
            </ol>
          </Collapse>
        )}
      </div>
      <div className="no-print flex items-center gap-3">
        {e.context && (
          <button
            type="button"
            onClick={toggle}
            className="text-xs text-accent underline-offset-4 hover:underline"
          >
            {open ? "맥락 접기" : "앞뒤 맥락 보기"}
          </button>
        )}
        <RecordLink ctx={ctx} refValue={e.record_ref} />
      </div>
    </div>
  );
}

function supportText(
  ctx: Ctx,
  support: { label?: string; option_id: string; supporters: Sender[] }[],
) {
  return support
    .map(
      (s) =>
        `${s.label ?? s.option_id}: ${s.supporters.length ? s.supporters.map((p) => nameOf(ctx.names, p)).join("·") : "지지 없음"}`,
    )
    .join(" / ");
}

function EvidenceItem({ ctx, e }: { ctx: Ctx; e: Evidence }) {
  const contextOnly = !e.counted || (e.kind === "message" && e.speaker !== "candidate");
  let body: ReactNode;
  if (e.kind === "message") body = <MessageEvidenceItem ctx={ctx} e={e} />;
  else if (e.kind === "slot") {
    body = (
      <div className="flex flex-col gap-1 text-sm">
        <span>
          {PHASE_TEXT[e.phase]} 칸 확정 · <b className="font-semibold">{e.value}</b> ·{" "}
          {resolutionLabel(e) ?? "–"} · {nameOf(ctx.names, e.filled_by)} · {ctx.clock(e.t_min)}
        </span>
        <span className="text-xs text-muted">확정 시점 지지: {supportText(ctx, e.support)}</span>
        <RecordLink ctx={ctx} refValue={e.record_ref} />
      </div>
    );
  } else if (e.kind === "proposal") {
    body = (
      <div className="flex flex-col gap-1 text-sm">
        <span className="text-xs text-muted">기획안 · {proposalFieldLabel(e.field)}</span>
        <blockquote className="border-l-2 border-accent/40 pl-3 break-words">{e.quote}</blockquote>
        <RecordLink ctx={ctx} refValue={e.record_ref} />
      </div>
    );
  } else {
    body = (
      <div className="flex flex-col gap-1 text-sm">
        <span>
          기여 대조 · {PHASE_TEXT[e.phase]} 「{e.label}」 최초 제안자{" "}
          {nameOf(ctx.names, e.first_proposer)}
        </span>
        <RecordLink ctx={ctx} refValue={e.record_ref} />
      </div>
    );
  }
  return (
    <li className={cn("flex gap-2 py-3", contextOnly && "opacity-70")}>
      {contextOnly && (
        <span className="shrink-0">
          <Chip tone="neutral">맥락</Chip>
        </span>
      )}
      <div className="min-w-0 flex-1">{body}</div>
    </li>
  );
}

/** 근거 목록: 처음 몇 개만 보이고 나머지는 펼친다. 인쇄에서는 전부 */
function EvidenceList({ ctx, evidence }: { ctx: Ctx; evidence: Evidence[] }) {
  const [more, setMore] = useState(false);
  const head = ctx.printing ? evidence : evidence.slice(0, EVIDENCE_PREVIEW);
  const rest = ctx.printing ? [] : evidence.slice(EVIDENCE_PREVIEW);
  return (
    <div>
      <h4 className="flex items-center gap-2 text-sm font-medium">
        근거
        <span className="text-xs font-normal text-muted tabular-nums">{evidence.length}개</span>
      </h4>
      <ul className="divide-y divide-line">
        {head.map((e) => (
          <EvidenceItem key={e.ref} ctx={ctx} e={e} />
        ))}
      </ul>
      {rest.length > 0 && (
        <>
          <Collapse open={more}>
            <ul className="divide-y divide-line border-t border-line">
              {rest.map((e) => (
                <EvidenceItem key={e.ref} ctx={ctx} e={e} />
              ))}
            </ul>
          </Collapse>
          <button
            type="button"
            onClick={() => setMore((v) => !v)}
            aria-expanded={more}
            className="press no-print mt-1 inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-accent hover:bg-accent-soft"
          >
            <ChevronRight
              aria-hidden
              className={cn("size-3.5 transition-transform duration-200", more && "rotate-90")}
            />
            {more ? "근거 접기" : `근거 ${rest.length}개 더 보기`}
          </button>
        </>
      )}
    </div>
  );
}

function CardScene({ ctx, card }: { ctx: Ctx; card: ReportCard }) {
  return (
    <li className="flex flex-wrap items-center gap-2 text-sm">
      <DesignedChip />
      <span className="text-muted tabular-nums">
        {card.fired_at != null ? ctx.clock(card.fired_at) : unfiredText(card)}
      </span>
      {card.fired_at != null && !card.delivered && <Chip tone="warning">{PARTIAL_DELIVERY}</Chip>}
      <span className={cn(card.fired_at == null && "text-muted")}>{card.scene}</span>
    </li>
  );
}

type AdjustProps = {
  dimension: Dimension;
  edit: Edit | undefined;
  editable: boolean;
  onSave: (edit: Edit) => void;
  onClear: () => void;
};

function AdjustPanel({ dimension, edit, editable, onSave, onClear }: AdjustProps) {
  const [draft, setDraft] = useState<Edit | null>(null);
  const shown = finalOf(dimension);
  if (draft) {
    const problem = editProblem(draft);
    return (
      <div className="reveal no-print flex flex-col gap-3 rounded-xl border border-accent/40 bg-surface p-4 shadow-float">
        <span className="text-sm font-medium">담당자 조정</span>
        <div className="max-w-full overflow-x-auto">
          <Segmented<string>
            label="조정 수준"
            value={draft.level != null ? String(draft.level) : "none"}
            onChange={(v) =>
              setDraft({
                ...draft,
                level: v === "none" ? null : (Number(v) as Level),
                reason: v === "none" ? draft.reason : null,
              })
            }
            options={[
              ...LEVELS.map((l) => ({ value: String(l), label: `수준 ${l}` })),
              { value: "none", label: "기록 없음" },
            ]}
          />
        </div>
        <Collapse open={draft.level == null}>
          <label className="flex flex-col gap-1.5 pb-1 text-sm">
            <span className="font-medium">기록 없음 사유</span>
            <select
              value={draft.reason ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, reason: (e.target.value || null) as NoRecordReason | null })
              }
              className="w-fit max-w-full rounded-md border border-line bg-surface px-3 py-2 text-ink"
            >
              <option value="">사유 선택</option>
              {STATES.map((state) => (
                <optgroup key={state} label={RECORD_STATE_TEXT[state]}>
                  {REASONS.filter((r) => RECORD_STATE[r] === state).map((r) => (
                    <option key={r} value={r}>
                      {NO_RECORD_TEXT[r]}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
        </Collapse>
        <Textarea
          label="메모 (필수)"
          value={draft.memo}
          onChange={(e) => setDraft({ ...draft, memo: e.target.value })}
          hint="조정한 이유를 원문 근거와 함께 적어 주세요"
          error={problem && draft.memo.trim() ? problem : undefined}
          className="min-h-20"
        />
        <div className="flex gap-2">
          <Button
            size="sm"
            disabled={!!problem}
            onClick={() => {
              onSave(draft);
              setDraft(null);
            }}
          >
            저장
          </Button>
          <Button size="sm" variant="secondary" onClick={() => setDraft(null)}>
            취소
          </Button>
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {isAdjusted(dimension) && (
        <div
          // 값이 바뀔 때마다 새로 떠오르며 테두리가 한 번 빛난다
          key={`${shown.level}-${shown.reason}-${dimension.adjust_memo}`}
          className="pop-in grid gap-2 rounded-lg bg-surface-sunken p-3 text-sm sm:grid-cols-[1fr_auto_1fr]"
        >
          <div className="flex flex-col gap-1 text-muted">
            <span className="text-xs">AI 판단</span>
            {levelChip(dimension.level, dimension.no_record_reason)}
          </div>
          <ChevronRight aria-hidden className="hidden size-4 self-center text-subtle sm:block" />
          <div className="flex flex-col gap-1">
            <span className="text-xs text-muted">담당자 조정</span>
            {levelChip(shown.level, shown.reason, true)}
            <span className="break-words">메모 · {dimension.adjust_memo}</span>
          </div>
        </div>
      )}
      {editable && (
        <div className="no-print flex gap-2">
          <Button
            size="xs"
            variant="secondary"
            onClick={() =>
              setDraft(
                edit ?? {
                  level: shown.level,
                  reason: shown.reason,
                  memo: dimension.adjust_memo ?? "",
                },
              )
            }
          >
            {isAdjusted(dimension) ? "조정 고치기" : "조정"}
          </Button>
          {edit && (
            <Button size="xs" variant="ghost" onClick={onClear}>
              조정 되돌리기
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function DimensionRow({
  ctx,
  d,
  edit,
  editable,
  onEdit,
  onOpen,
}: {
  ctx: Ctx;
  d: Dimension;
  edit: Edit | undefined;
  editable: boolean;
  onEdit: (id: string, edit: Edit | null) => void;
  onOpen: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const final = finalOf(d);
  const cards = ctx.report.cards.filter((c) => c.element === d.element);
  return (
    <details
      id={`dim-${d.dimension_id}`}
      open={ctx.printing || undefined}
      onToggle={(e) => {
        const now = e.currentTarget.open;
        setOpen(now);
        if (now) onOpen(d.dimension_id);
      }}
      className="group scroll-mt-32 border-b border-line last:border-b-0"
    >
      <summary className="print-break flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 rounded-md px-1 py-3 transition-colors duration-150 hover:bg-surface-sunken/60">
        <ChevronRight
          aria-hidden
          className="size-4 shrink-0 text-subtle transition-transform duration-200 group-open:rotate-90"
        />
        <span className="font-medium">{d.name}</span>
        {levelChip(final.level, final.reason, isAdjusted(d))}
        {d.confidence && <ConfidenceTag confidence={d.confidence} />}
        {final.reason === "evaluate_failed" && (
          <Link
            href={ctx.recordHref({ type: "", id: "", t_min: 0 }).split("#")[0]}
            className="no-print inline-flex items-center gap-1 text-xs text-accent underline-offset-4 hover:underline"
          >
            원문 기록
            <ExternalLink aria-hidden className="size-3" />
          </Link>
        )}
      </summary>
      <div className={cn("flex flex-col gap-4 pt-1 pb-4 pl-6", open && "reveal")}>
        <p className="text-sm text-muted">{DIMENSION_DEFINITION[d.dimension_id] ?? ""}</p>
        <dl className="print-break grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[7rem_1fr]">
          <dt className="text-muted">판단 이유</dt>
          <dd className="break-words">{cleanRationale(d.rationale)}</dd>
          {d.reviewer_hint && (
            <>
              <dt className="text-muted">대표 행동</dt>
              <dd>{d.reviewer_hint}</dd>
            </>
          )}
          {cards.length > 0 && (
            <>
              <dt className="text-muted">관련 카드 장면</dt>
              <dd>
                <ul className="flex flex-col gap-1">
                  {cards.map((c) => (
                    <CardScene key={c.card_id} ctx={ctx} card={c} />
                  ))}
                </ul>
              </dd>
            </>
          )}
        </dl>
        {d.evidence.length > 0 ? (
          <EvidenceList ctx={ctx} evidence={d.evidence} />
        ) : (
          <p className="text-sm text-muted">
            근거 없음 ·{" "}
            {final.reason
              ? `${stateOf(final.reason)} · ${NO_RECORD_TEXT[final.reason]}`
              : "기록 없음"}
            {final.reason === "evaluate_failed" && (
              <>
                {" "}
                <Link
                  href={ctx.recordHref({ type: "", id: "", t_min: 0 }).split("#")[0]}
                  className="no-print inline-flex items-center gap-1 text-xs text-accent underline-offset-4 hover:underline"
                >
                  기록에서 보기
                  <ExternalLink aria-hidden className="size-3" />
                </Link>
              </>
            )}
          </p>
        )}
        <AdjustPanel
          dimension={d}
          edit={edit}
          editable={editable}
          onSave={(next) => onEdit(d.dimension_id, next)}
          onClear={() => onEdit(d.dimension_id, null)}
        />
      </div>
    </details>
  );
}

/** 세부 행동 표식(장식): 수준이 있으면 채운 점, 기록 없음이면 빈 점, 확신도 low면 주황 테. 높낮이는 그리지 않는다 */
function DimensionPips({ dims }: { dims: Dimension[] }) {
  return (
    <span aria-hidden className="inline-flex items-center gap-1">
      {dims.map((d, i) => {
        const f = finalOf(d);
        return (
          <span
            key={d.dimension_id}
            title={d.name}
            style={delay(200 + i * 60)}
            className={cn(
              "pop-in size-2.5 rounded-full border",
              f.level != null ? "border-accent bg-accent-fill" : "border-line bg-surface",
              d.confidence === "low" && "ring-2 ring-warning/50",
            )}
          />
        );
      })}
    </span>
  );
}

function FactsTable({ facts, ctx }: { facts: Record<string, unknown>; ctx: Ctx }) {
  const byPhase = (v: unknown, unit = "") => {
    const m = v as Record<string, number | null>;
    return Object.entries(m)
      .map(
        ([k, n]) =>
          `${k === "total" ? "전체" : (PHASE_TEXT[k as keyof typeof PHASE_TEXT] ?? k)} ${n ?? "–"}${unit}`,
      )
      .join(" · ");
  };
  const yesNo = (v: unknown) => (v == null ? "해당 없음" : v ? "예" : "아니오");
  const INTENT: Record<string, string> = {
    propose: "제안",
    agree: "동의",
    oppose: "반대",
    withhold: "보류",
    question: "질문",
    other: "기타",
  };
  const rows: [string, string][] = [];
  const f = facts as Record<string, unknown>;
  if (f.message_count) rows.push(["지원자 발언 수", byPhase(f.message_count, "")]);
  if (f.spontaneous_count != null)
    rows.push([
      "자발 / 초대 후",
      `${f.spontaneous_count} / ${f.invited_count} (자발 ${Math.round(Number(f.spontaneous_ratio) * 100)}%)`,
    ]);
  if (f.silence_invites_received)
    rows.push(["받은 침묵 초대", byPhase(f.silence_invites_received)]);
  if (f.first_message_sec) rows.push(["단계 시작 뒤 첫 발언", byPhase(f.first_message_sec, "초")]);
  if (f.response_latency_median_sec != null)
    rows.push(["응답 간격 중앙값", `${f.response_latency_median_sec}초`]);
  if (f.avg_message_chars != null) rows.push(["발언 평균 글자 수", `${f.avg_message_chars}자`]);
  if (f.intent_counts)
    rows.push([
      "의도별 발언",
      Object.entries(f.intent_counts as Record<string, number>)
        .map(([k, n]) => `${INTENT[k] ?? k} ${n}`)
        .join(" · "),
    ]);
  if (f.name_mentions)
    rows.push([
      "이름을 부른 횟수",
      Object.entries(f.name_mentions as Record<string, number>)
        .map(([k, n]) => `${nameOf(ctx.names, k as Sender)} ${n}`)
        .join(" · "),
    ]);
  if (f.own_options_proposed != null)
    rows.push(["낸 안 / 채택된 안", `${f.own_options_proposed} / ${f.own_options_adopted}`]);
  if ("pressure_responded" in f) rows.push(["압박에 응답", yesNo(f.pressure_responded)]);
  if ("invite_latency_sec" in f)
    rows.push([
      "머뭇거림 뒤 초대까지",
      f.invite_latency_sec == null ? "초대 없음" : `${f.invite_latency_sec}초`,
    ]);
  if ("quiet_spoke" in f) rows.push(["소극형 동료가 자기 안을 말함", yesNo(f.quiet_spoke)]);
  if ("own_option_rejected" in f)
    rows.push(["문제 상황에서 지원자 안 미채택", yesNo(f.own_option_rejected)]);
  if (f.messages_after_rejection != null)
    rows.push(["미채택 뒤 발언 수", String(f.messages_after_rejection)]);
  if (Array.isArray(f.blank_sections_at_submit))
    rows.push([
      "제출 때 빈 칸",
      f.blank_sections_at_submit.length
        ? f.blank_sections_at_submit.map((id) => proposalFieldLabel(String(id))).join(" · ")
        : "없음",
    ]);
  if ("blank_warning_acknowledged" in f)
    rows.push([
      "빈칸 경고 확인 뒤 제출",
      f.blank_warning_acknowledged == null ? "자동 제출" : yesNo(f.blank_warning_acknowledged),
    ]);
  if ("credit_field_filled" in f)
    rows.push(["아이디어 기여 칸 작성", yesNo(f.credit_field_filled)]);
  return (
    <table className="w-full text-left text-sm">
      <tbody className="divide-y divide-line">
        {rows.map(([k, v]) => (
          <tr key={k}>
            <th className="w-40 py-2 pr-4 align-top font-normal text-muted sm:w-56">{k}</th>
            <td className="py-2 break-words tabular-nums">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * 화면에서는 공용 탭의 키보드 이동을 쓰고, 인쇄에서는 세 패널을 차례로 펼친다.
 */
function ReferenceTabs({
  items,
  printing,
}: {
  items: { id: string; label: string; count?: number; content: ReactNode }[];
  printing: boolean;
}) {
  if (printing) {
    return (
      <div className="flex flex-col gap-6">
        {items.map((item) => (
          <section key={item.id} className="print-break flex flex-col gap-3">
            <h3 className="text-title3 font-semibold">{item.label}</h3>
            {item.content}
          </section>
        ))}
      </div>
    );
  }
  return (
    <Tabs
      items={items.map((item) => ({ value: item.id, ...item }))}
      variant="reference"
      ariaLabel="참고 기록"
      keepMounted
      className="gap-4"
      panelClassName="flex flex-col gap-3"
    />
  );
}

/** 세션 띠(장식): 0분~끝 사이에 칸 확정(◆)·카드 장면(●)·접속 끊김(빨간 구간)을 시간 위치에 찍는다 */
function SessionTrack({ ctx }: { ctx: Ctx }) {
  const total = ctx.report.position.session_minutes;
  const at = (t: number) => `${Math.min(100, Math.max(0, (t / total) * 100))}%`;
  const slots = ctx.report.slots;
  const fired = ctx.report.cards.filter((c) => c.fired_at != null);
  return (
    <div aria-hidden className="flex flex-col gap-1.5 pt-6 pb-1">
      <div className="relative h-1.5 rounded-full bg-surface-sunken">
        <div className="reveal absolute inset-y-0 left-0 w-full rounded-full bg-accent/15" />
        {ctx.report.connection_gaps.map((g) => (
          <span
            key={g.from_t_min}
            className="absolute inset-y-0 rounded-full bg-danger/60"
            style={{ left: at(g.from_t_min), width: at(g.to_t_min - g.from_t_min) }}
          />
        ))}
        {fired.map((c, i) => (
          <span
            key={c.card_id}
            title={c.scene}
            className={cn(
              "pop-in absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface",
              c.delivered ? "bg-warning" : "bg-warning/50",
            )}
            style={{ left: at(c.fired_at!), ...delay(300 + i * 90) }}
          />
        ))}
        {slots.map((s, i) => (
          <span
            key={s.phase}
            className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
            style={{ left: at(s.t_min) }}
          >
            <span
              className="pop-in block size-3 rotate-45 rounded-[2px] border-2 border-surface bg-accent-fill"
              style={delay(150 + i * 120)}
            />
            <span className="absolute bottom-3 left-1/2 -translate-x-1/2 text-[10px] whitespace-nowrap text-muted">
              {PHASE_TEXT[s.phase]}
            </span>
          </span>
        ))}
      </div>
      <div className="flex justify-between text-xs text-subtle tabular-nums">
        <span>{ctx.clock(0)}</span>
        <span className="inline-flex items-center gap-3">
          <span className="inline-flex items-center gap-1">
            <span className="size-2 rotate-45 bg-accent-fill" /> 칸 확정
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="size-2 rounded-full bg-warning" /> 카드 장면
          </span>
        </span>
        <span>{ctx.clock(total)}</span>
      </div>
    </div>
  );
}

function OverviewStat({
  label,
  value,
  of,
  hint,
  tone,
}: {
  label: string;
  value: number;
  of?: number;
  hint: string;
  tone?: "warning";
}) {
  return (
    <div className="flex h-full flex-col gap-1 rounded-xl border border-line bg-surface p-4">
      <span className="text-xs text-muted">{label}</span>
      <span
        className={cn(
          "text-title2 font-semibold tabular-nums",
          tone === "warning" && value > 0 && "text-warning",
        )}
      >
        <CountUp value={value} />
        {of != null && <span className="text-body font-normal text-subtle"> / {of}</span>}
      </span>
      <span className="text-xs text-subtle">{hint}</span>
    </div>
  );
}

type NavItem = { id: string; label: string };

/** 목차: 넓은 화면은 왼쪽에 붙고, 좁은 화면은 위에 붙은 가로 줄 */
function ReportNav({
  items,
  elements,
  active,
  activeElement,
}: {
  items: NavItem[];
  elements: { id: string; name: string; level: Level | null; adjusted: boolean }[];
  active: string;
  activeElement: string;
}) {
  return (
    <>
      <nav
        aria-label="리포트 목차"
        className="no-print sticky top-20 hidden max-h-[calc(100dvh-6rem)] flex-col gap-0.5 self-start overflow-y-auto text-sm lg:flex"
      >
        {items.map((it, i) => {
          const on = it.id === active;
          return (
            <div key={it.id} className="reveal flex flex-col" style={delay(120 + i * 60)}>
              <a
                href={`#${it.id}`}
                aria-current={on ? "location" : undefined}
                className={cn(
                  "relative rounded-md px-3 py-1.5 transition-colors duration-200",
                  on
                    ? "bg-accent-soft font-medium text-accent-strong"
                    : "text-muted hover:text-ink",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "absolute top-1.5 bottom-1.5 left-0 w-0.5 rounded-full bg-accent transition-opacity duration-200",
                    on ? "opacity-100" : "opacity-0",
                  )}
                />
                {it.label}
              </a>
              {it.id === "elements" && (
                <ul className="my-0.5 ml-3 flex flex-col border-l border-line">
                  {elements.map((el) => {
                    const here = on && el.id === activeElement;
                    return (
                      <li key={el.id}>
                        <a
                          href={`#element-${el.id}`}
                          className={cn(
                            "flex items-center justify-between gap-2 py-1 pr-2 pl-3 text-xs transition-colors duration-200",
                            here ? "font-medium text-ink" : "text-muted hover:text-ink",
                          )}
                        >
                          <span className="truncate">{el.name}</span>
                          <span className="shrink-0 tabular-nums text-subtle">
                            {el.level ?? "–"}
                            {el.adjusted && <span className="text-accent">*</span>}
                          </span>
                        </a>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          );
        })}
      </nav>
      <nav
        aria-label="리포트 목차 (좁은 화면)"
        className="vibrancy no-print sticky top-[53px] z-20 -mx-4 flex gap-1 overflow-x-auto border-b border-line px-4 py-2 sm:-mx-6 sm:px-6 lg:hidden"
      >
        {items.map((it) => {
          const on = it.id === active;
          return (
            <a
              key={it.id}
              href={`#${it.id}`}
              aria-current={on ? "location" : undefined}
              className={cn(
                "shrink-0 rounded-full px-3 py-1 text-sm whitespace-nowrap transition-colors duration-200",
                on ? "bg-accent-fill text-on-accent" : "text-muted hover:bg-surface-sunken",
              )}
            >
              {it.label}
            </a>
          );
        })}
      </nav>
    </>
  );
}

export type ReportViewProps = {
  report: Report;
  recordHref: (ref: RecordRef) => string;
  /** 샘플 리포트: 조정·검토 없이 읽기만 */
  sample?: boolean;
  /** 인쇄 미리보기(차원 전부 펼침) */
  printing?: boolean;
  pending?: boolean;
  /** 검토 제출 오류(422 등) */
  error?: string | null;
  /** 확정이 오래 끝나지 않음: "확정만 다시 시도" */
  stalled?: boolean;
  onReview?: (body: ReviewBody) => void;
  onRetry?: () => void;
  onAddNote?: (body: { text: string; reviewer_name: string }) => Promise<void> | void;
};

/** 검토 체크포인트의 지금 단계: 실제 상태(펼쳐 본 세부 행동·조정·검토자 이름·서버 단계)를 따른다 */
function reviewStep(phase: Phase, opened: number, adjusted: number, reviewer: string) {
  if (phase === "approving") return "final";
  if (reviewer.trim()) return "submit";
  if (opened > 0 || adjusted > 0) return "adjust";
  return "read";
}

export function ReportView({
  report,
  recordHref,
  sample,
  printing: forcedPrint,
  pending,
  error,
  stalled,
  onReview,
  onRetry,
  onAddNote,
}: ReportViewProps) {
  const [edits, setEdits] = useState<Edits>({});
  const [reviewer, setReviewer] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [noteName, setNoteName] = useState("");
  const [printMode, setPrintMode] = useState(false);
  const [opened, setOpened] = useState<string[]>([]);
  const printing = !!forcedPrint || printMode;

  // 브라우저 인쇄(⌘P 포함) 직전에 세부 행동·근거·탭을 모두 펼치고 끝나면 되돌린다
  useEffect(() => {
    const before = () => flushSync(() => setPrintMode(true));
    const after = () => setPrintMode(false);
    window.addEventListener("beforeprint", before);
    window.addEventListener("afterprint", after);
    return () => {
      window.removeEventListener("beforeprint", before);
      window.removeEventListener("afterprint", after);
    };
  }, []);

  const phase = phaseOf(report);
  const editable = !sample && phase === "draft" && !!onReview;
  const dims = withEdits(report.dimensions, edits);
  const seatNames = Object.fromEntries(
    report.seats.map((s) => [s.seat, s.display_name]),
  ) as Partial<Record<Sender, string>>;
  const ctx: Ctx = {
    report,
    names: seatNames,
    avatars: Object.fromEntries(report.seats.map((s) => [s.seat, s.avatar_url])),
    clock: (t) => officeClock(t, report.position.session_minutes),
    recordHref,
    printing,
  };
  const candidate = report.seats.find((s) => s.seat === "candidate");
  const elementAdjusted = (el: string) => dims.some((d) => d.element === el && isAdjusted(d));
  const levelOf = (el: ReportElement) =>
    elementAdjusted(el.element) ? elementLevel(dims, el.element) : el.level;
  const lowCount = (el: string) =>
    dims.filter((d) => d.element === el && d.confidence === "low").length;
  const heldCount = (el: string) =>
    dims.filter((d) => d.element === el && finalOf(d).reason === "evaluate_failed").length;
  const setEdit = (id: string, edit: Edit | null) =>
    setEdits((prev) => {
      const next = { ...prev };
      if (edit) next[id] = edit;
      else delete next[id];
      return next;
    });
  const adjustments = adjustmentsOf(edits);
  const reviewerProblem = !reviewer.trim() ? "검토자 이름을 적어 주세요." : null;
  const purge = purgeText(report.purge_at);

  // 개요 숫자(개수만, 합산 값 없음)
  const levelled = dims.filter((d) => finalOf(d).level != null).length;
  const countedEvidence = countedRecordCount(dims);
  const lowAll = dims.filter((d) => d.confidence === "low").length;
  const firedCards = report.cards.filter((c) => c.fired_at != null).length;

  const navItems: NavItem[] = [
    { id: "summary", label: "개요" },
    { id: "timeline", label: "토론 흐름" },
    { id: "elements", label: "요소별 판단" },
    { id: "reference", label: "참고 기록" },
    ...(sample ? [] : [{ id: "review", label: "검토" }]),
  ];
  const active = useScrollSpy(navItems.map((n) => n.id));
  const activeElement = useScrollSpy(report.elements.map((e) => `element-${e.element}`));

  const stepNow = reviewStep(phase, opened.length, adjustments.length, reviewer);
  const markOpened = (id: string) =>
    setOpened((prev) => (prev.includes(id) ? prev : [...prev, id]));

  return (
    <article className="mx-auto grid w-full max-w-5xl gap-x-10 gap-y-8 lg:grid-cols-[11rem_minmax(0,1fr)] print:block">
      <ReportNav
        items={navItems}
        elements={report.elements.map((el) => ({
          id: el.element,
          name: el.name,
          level: levelOf(el),
          adjusted: elementAdjusted(el.element),
        }))}
        active={active}
        activeElement={activeElement.replace(/^element-/, "")}
      />
      <div className="flex min-w-0 flex-col gap-12">
        {/* 머리 */}
        <header className="reveal flex flex-col gap-5 rounded-xl border border-line bg-surface p-5 sm:p-6">
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
            <Chip
              tone={phase === "final" ? "success" : phase === "approving" ? "accent" : "warning"}
            >
              {sample ? "샘플" : PHASE_LABEL[phase]}
            </Chip>
            {report.review && <span>검토자 {report.review.reviewer_name}</span>}
            {report.reviewed_at && <span>{formatDateTime(report.reviewed_at)} 검토</span>}
            {purge && <span>{purge}</span>}
            <Button
              size="sm"
              variant="secondary"
              className="no-print ml-auto"
              onClick={() => window.print()}
            >
              <Printer aria-hidden className="size-4" />
              인쇄
            </Button>
          </div>
          <div className="flex flex-col gap-1">
            <p className="text-sm text-muted">{report.position.name}</p>
            <h1 className="text-title1 font-bold break-all">{report.alias} 리포트</h1>
          </div>
          <ul className="flex flex-wrap gap-2 text-sm">
            <li
              className="reveal inline-flex items-center gap-2 rounded-full border border-line bg-surface py-1 pr-3 pl-1"
              style={delay(120)}
            >
              <Avatar seat="candidate" name={candidate?.display_name ?? report.alias} size={28} />
              <span className="font-medium">{report.alias}</span>
              {candidate && candidate.display_name !== report.alias && (
                <span className="text-muted">· 표시 이름 {candidate.display_name}</span>
              )}
            </li>
            {report.seats
              .filter((s) => s.seat !== "candidate")
              .map((s, i) => (
                <li
                  key={s.seat}
                  className="reveal inline-flex items-center gap-2 rounded-full border border-line bg-surface py-1 pr-3 pl-1"
                  style={delay(200 + i * 80)}
                >
                  <Avatar seat={s.seat} src={s.avatar_url} name={s.display_name} size={28} />
                  {s.display_name}
                  {s.persona_type && (
                    <span className="text-muted">
                      · {PERSONA_TEXT[s.persona_type] ?? s.persona_type}
                    </span>
                  )}
                </li>
              ))}
          </ul>
          {!sample && (
            <div className="no-print reveal border-t border-line pt-4" style={delay(360)}>
              <Checkpoints
                steps={REVIEW_STEPS}
                current={stepNow}
                done={phase === "final"}
                label="검토 진행"
                className="mx-auto max-w-md"
              />
            </div>
          )}
        </header>

        {/* 읽기 전 안내 */}
        <div className="reveal -mt-6 flex flex-col gap-3" style={delay(160)}>
          {sample && (
            <p className="rounded-lg bg-accent-soft px-4 py-3 text-sm text-accent-strong">
              {SAMPLE_NOTICE}
            </p>
          )}
          <p className="text-sm leading-relaxed">{REPORT_NOTICE}</p>
          <p className="flex items-start gap-2 text-sm leading-relaxed text-muted">
            <span className="shrink-0">
              <DesignedChip />
            </span>
            {DESIGNED_SCENE_NOTICE}
          </p>
          <TermsGuide printing={printing} />
        </div>

        {/* 개요 */}
        <Section
          id="summary"
          title="요소 요약"
          lead="먼저 요소별 수준과 기록 상태를 훑고, 아래에서 근거를 확인하세요."
        >
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              <OverviewStat
                key="lv"
                label="수준을 매긴 세부 행동"
                value={levelled}
                of={dims.length}
                hint="나머지는 기록 없음"
              />,
              <OverviewStat
                key="ev"
                label="근거로 쓴 기록"
                value={countedEvidence}
                hint="지원자 메시지·칸 확정·기획안"
              />,
              <OverviewStat
                key="low"
                label="근거 약함"
                value={lowAll}
                hint="원문 확인 권장"
                tone="warning"
              />,
              <OverviewStat
                key="card"
                label="발동한 카드 장면"
                value={firedCards}
                of={report.cards.length}
                hint="설계된 장면"
              />,
            ].map((node, i) => (
              <div key={i} className="reveal" style={delay(240 + i * 90)}>
                {node}
              </div>
            ))}
          </div>
          <div className="overflow-x-auto rounded-xl border border-line bg-surface">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="py-2 pr-3 pl-4 font-medium">요소</th>
                  <th className="w-48 py-2 pr-3 font-medium">수준</th>
                  <th className="min-w-48 py-2 pr-3 font-medium">서술</th>
                  <th className="py-2 pr-3 text-right font-medium whitespace-nowrap">기록 없음</th>
                  <th className="py-2 pr-4 text-right font-medium whitespace-nowrap">근거 약함</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {report.elements.map((el, i) => {
                  const adjusted = elementAdjusted(el.element);
                  const level = levelOf(el);
                  const noRecord = dims.filter(
                    (d) => d.element === el.element && finalOf(d).level == null,
                  );
                  const byState = (state: RecordState) =>
                    noRecord.filter((d) => {
                      const reason = finalOf(d).reason;
                      return reason != null && RECORD_STATE[reason] === state;
                    }).length;
                  return (
                    <tr
                      key={el.element}
                      className="reveal align-top transition-colors duration-150 hover:bg-surface-sunken/50"
                      style={delay(420 + i * 70)}
                    >
                      <td className="py-3 pr-3 pl-4 font-medium whitespace-nowrap">
                        <a
                          href={`#element-${el.element}`}
                          className="underline-offset-4 hover:underline"
                        >
                          {el.name}
                        </a>
                      </td>
                      <td className="py-3 pr-3">
                        <div className="flex flex-col gap-1">
                          {elementChip(level, adjusted)}
                          {adjusted && (
                            <span className="text-xs text-muted">
                              AI 판단 {el.level ?? "기록 없음"}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="py-3 pr-3">{displayTerms(el.summary)}</td>
                      <td className="py-3 pr-3 text-right text-muted tabular-nums whitespace-nowrap">
                        {noRecord.length}/{el.dimension_count} 세부 행동
                        {(["no_scene", "no_response"] as const)
                          .filter((state) => byState(state) > 0)
                          .map((state) => (
                            <span key={state} className="block text-xs">
                              {RECORD_STATE_TEXT[state]} {byState(state)}
                            </span>
                          ))}
                      </td>
                      <td className="py-3 pr-4 text-right tabular-nums whitespace-nowrap">
                        {lowCount(el.element) > 0 ? (
                          <span className="text-warning" title="확신도 low로 판단한 세부 행동 수">
                            {lowCount(el.element)}
                          </span>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                        {heldCount(el.element) > 0 && (
                          <span
                            className="block text-muted"
                            title="AI 평가가 실패해 판단을 보류한 세부 행동 수"
                          >
                            판단 보류 {heldCount(el.element)}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted">
            기록 없음은 수준을 매기지 않은 세부 행동 수이고 장면 없음·반응 없음으로 나눠 보입니다.
            근거 약함은 확신도 low로 판단한 세부 행동 수, 판단 보류는 AI 평가가 실패해 수준을 정하지
            못한 세부 행동 수예요. 둘 다 원문 기록을 확인해 주세요.
          </p>
        </Section>

        {/* 토론 흐름 */}
        <Section
          id="timeline"
          title="토론 흐름"
          lead="단계마다 정해진 칸과 설계된 카드 장면을 시간순으로 봅니다."
        >
          <SessionTrack ctx={ctx} />
          <ol className="grid gap-3 sm:grid-cols-4">
            {PHASES.map((p, i) => {
              const slot = report.slots.find((s) => s.phase === p);
              const done = !!slot || (p === "proposal" && !!report.proposal);
              return (
                <li
                  key={p}
                  className="reveal relative flex flex-col gap-1.5 rounded-xl border border-line bg-surface p-3 text-sm"
                  style={delay(i * 110)}
                >
                  <span className="flex items-center gap-2 text-xs font-medium text-muted">
                    <span
                      className={cn(
                        "flex size-5 items-center justify-center rounded-full border-2 text-[10px]",
                        done
                          ? "border-accent bg-accent-fill text-on-accent"
                          : "border-line text-subtle",
                      )}
                    >
                      {done ? <DrawnCheck size={11} delay={200 + i * 110} /> : i + 1}
                    </span>
                    {PHASE_TEXT[p]}
                  </span>
                  {slot ? (
                    <>
                      <b className="font-semibold break-words">{slot.value}</b>
                      <span className="inline-flex items-center gap-1 text-xs">
                        {slot.resolution === "consensus" ? (
                          <CircleCheck aria-hidden className="size-3.5 text-success" />
                        ) : (
                          <Clock3 aria-hidden className="size-3.5 text-warning" />
                        )}
                        {[resolutionLabel(slot), ctx.clock(slot.t_min)].filter(Boolean).join(" · ")}
                      </span>
                      <span className="text-xs text-muted">
                        정한 사람 {nameOf(ctx.names, slot.filled_by)}
                      </span>
                      <span className="text-xs text-muted">
                        최초 제안자{" "}
                        {slot.first_proposer ? nameOf(ctx.names, slot.first_proposer) : "–"}
                      </span>
                      <span className="text-xs break-words text-muted">
                        지지 {supportText(ctx, slot.support)}
                      </span>
                    </>
                  ) : p === "proposal" && report.proposal ? (
                    <span className="text-xs text-muted">
                      {report.proposal.auto_submitted ? "시간 종료로 자동 제출" : "지원자 제출"}
                    </span>
                  ) : (
                    <span className="text-xs text-muted">–</span>
                  )}
                </li>
              );
            })}
          </ol>
          <div>
            <h3 className="mb-2 text-sm font-medium">카드 장면</h3>
            <ul className="flex flex-col gap-2">
              {[...report.cards]
                .sort((a, b) => (a.fired_at ?? 999) - (b.fired_at ?? 999))
                .map((card, i) => {
                  const element = report.elements.find((e) => e.element === card.element);
                  const dc = report.debrief.cards.find((c) => c.card_id === card.card_id);
                  return (
                    <InView
                      as="li"
                      key={card.card_id}
                      style={delay(i * 60)}
                      className={cn(
                        "flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-sm",
                        card.fired_at == null && "border-dashed bg-transparent",
                      )}
                    >
                      <span className="w-12 text-muted tabular-nums">
                        {card.fired_at != null ? ctx.clock(card.fired_at) : "–"}
                      </span>
                      {dc && <span className="text-xs text-muted">{PHASE_TEXT[dc.phase]}</span>}
                      <DesignedChip />
                      {card.fired_at == null && <Chip tone="neutral">{unfiredText(card)}</Chip>}
                      {card.fired_at != null && !card.delivered && (
                        <Chip tone="warning">{PARTIAL_DELIVERY}</Chip>
                      )}
                      <span className={cn("min-w-0 flex-1", card.fired_at == null && "text-muted")}>
                        {card.scene}
                      </span>
                      {element && (
                        <a
                          href={`#element-${element.element}`}
                          className="text-xs text-accent hover:underline"
                        >
                          {element.name}
                        </a>
                      )}
                    </InView>
                  );
                })}
            </ul>
          </div>
          {report.connection_gaps.length > 0 && (
            <ul className="flex flex-col gap-1">
              {report.connection_gaps.map((g) => (
                <li
                  key={g.from_t_min}
                  className="rounded-md bg-surface-sunken px-3 py-1.5 text-sm text-muted"
                >
                  접속 끊김 {ctx.clock(g.from_t_min)}~{ctx.clock(g.to_t_min)} (약{" "}
                  {Math.max(1, Math.round((g.to_t_min - g.from_t_min) * 60))}초) · 평가 수준과 무관
                </li>
              ))}
            </ul>
          )}
        </Section>

        {/* 요소별 판단 */}
        <Section
          id="elements"
          title="요소별 판단"
          lead="세부 행동을 펼치면 판단 이유와 근거가 나옵니다. 근거 말풍선을 누르면 앞뒤 맥락이 열립니다."
        >
          {report.elements.map((el) => {
            const adjusted = elementAdjusted(el.element);
            const level = levelOf(el);
            const mine = dims.filter((d) => d.element === el.element);
            const evidenceCount = countedRecordCount(mine);
            return (
              <InView
                as="section"
                key={el.element}
                id={`element-${el.element}`}
                className="flex scroll-mt-32 flex-col gap-3 rounded-xl border border-line bg-surface p-4 sm:p-5 lg:scroll-mt-20"
                aria-labelledby={`element-${el.element}-title`}
              >
                <div className="flex flex-wrap items-center gap-3">
                  <h3 id={`element-${el.element}-title`} className="text-title3 font-semibold">
                    {el.name}
                  </h3>
                  {elementChip(level, adjusted)}
                  <span className="ml-auto inline-flex items-center gap-3 text-xs text-muted">
                    <DimensionPips dims={mine} />
                    <span className="tabular-nums">세부 행동 {mine.length}</span>
                    <span className="tabular-nums">근거 {evidenceCount}</span>
                  </span>
                </div>
                <p className="text-sm">{displayTerms(el.summary)}</p>
                <div className="rounded-lg border border-line px-3">
                  {mine.map((d) => (
                    <DimensionRow
                      key={d.dimension_id}
                      ctx={ctx}
                      d={d}
                      edit={edits[d.dimension_id]}
                      editable={editable}
                      onEdit={setEdit}
                      onOpen={markOpened}
                    />
                  ))}
                </div>
                {el.element === "honesty" && report.contribution_check.length > 0 && (
                  <div className="mt-2 flex flex-col gap-2">
                    <h4 className="text-sm font-medium">기여 대조표</h4>
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[480px] border-y border-line text-left text-sm">
                        <thead className="text-xs text-muted">
                          <tr className="border-b border-line">
                            <th className="py-2 pr-3 font-medium">채택된 안</th>
                            <th className="py-2 pr-3 font-medium">실제 최초 제안자</th>
                            <th className="py-2 font-medium">기획안에 적힌 기여자</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                          {report.contribution_check.map((row) => (
                            <tr key={row.option_id} className="align-top">
                              <td className="py-2 pr-3">
                                {PHASE_TEXT[row.phase]} · {row.label}
                              </td>
                              <td className="py-2 pr-3">{nameOf(ctx.names, row.first_proposer)}</td>
                              <td className="py-2">
                                {row.credited_in_proposal.length
                                  ? row.credited_in_proposal
                                      .map((s) => nameOf(ctx.names, s))
                                      .join(", ")
                                  : "적히지 않음"}
                                {row.credit_quote && (
                                  <span className="block text-xs text-muted">
                                    “{row.credit_quote}”
                                  </span>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {report.proposal?.sections.idea_credit != null && (
                      <p className="text-sm">
                        <span className="text-muted">아이디어 기여 칸 원문 · </span>
                        {report.proposal.sections.idea_credit || "비어 있음"}
                      </p>
                    )}
                  </div>
                )}
              </InView>
            );
          })}
        </Section>

        {/* 참고 기록: 사실 지표 · 제출 기획안 · AI 활용 */}
        <Section
          id="reference"
          title="참고 기록"
          lead="수준 판단을 돕는 사실·제출물·AI 사용 기록입니다."
        >
          <div className="rounded-xl border border-line bg-surface p-4 sm:p-5">
            <ReferenceTabs
              printing={ctx.printing}
              items={[
                {
                  id: "facts",
                  label: "사실 지표",
                  content: (
                    <>
                      <p className="text-sm text-muted">{FACTS_NOTE}</p>
                      <FactsTable facts={report.facts} ctx={ctx} />
                    </>
                  ),
                },
                {
                  id: "proposal",
                  label: "제출 기획안",
                  content: report.proposal ? (
                    <>
                      <p className="text-sm text-muted">
                        {formatDateTime(report.proposal.submitted_at)} 제출
                        {report.proposal.auto_submitted && " · 시간 종료로 자동 제출"} · 기획안의
                        품질은 평가하지 않습니다
                      </p>
                      <dl className="divide-y divide-line">
                        {PROPOSAL_FIELDS.map((f) => (
                          <div key={f.id} className="grid gap-1 py-3 sm:grid-cols-[7rem_1fr]">
                            <dt className="text-sm text-muted">{f.label}</dt>
                            <dd className="break-words whitespace-pre-wrap">
                              {report.proposal!.sections[f.id] || (
                                <span className="text-muted">비어 있음</span>
                              )}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    </>
                  ) : (
                    <p className="text-sm text-muted">제출된 기획안 없음</p>
                  ),
                },
                {
                  id: "ai",
                  label: "AI 활용",
                  count: report.ai_usage.used ? report.ai_usage.events.length : 0,
                  content: (
                    <>
                      <p className="text-sm text-muted">{AI_HEADER} · 시간순 사실만 보입니다</p>
                      {report.ai_usage.used && report.ai_usage.events.length > 0 ? (
                        <ol className="relative flex flex-col gap-4 border-l border-line pl-4">
                          {[...report.ai_usage.events]
                            .sort((a, b) => a.t_min - b.t_min)
                            .map((ev) => (
                              <li
                                key={ev.call_id}
                                className="relative flex flex-col gap-1.5 text-sm"
                              >
                                <span
                                  aria-hidden
                                  className="absolute top-1 -left-[21px] size-2.5 rounded-full border-2 border-surface bg-accent-fill"
                                />
                                <span className="text-xs text-muted tabular-nums">
                                  {ctx.clock(ev.t_min)}
                                </span>
                                <b className="font-semibold break-words">{ev.request_quote}</b>
                                <span className="break-words">{ev.response_quote}</span>
                                <span className="flex flex-wrap items-center gap-1.5">
                                  {ev.cited_material_ids.map((id) => (
                                    <Chip key={id} tone="outline">
                                      {materialTitle(id)}
                                    </Chip>
                                  ))}
                                  <RecordLink
                                    ctx={ctx}
                                    refValue={{
                                      type: "assistant_call",
                                      id: ev.call_id,
                                      t_min: ev.t_min,
                                    }}
                                  />
                                </span>
                              </li>
                            ))}
                        </ol>
                      ) : (
                        <p className="text-sm">사용하지 않음</p>
                      )}
                    </>
                  ),
                },
              ]}
            />
          </div>
        </Section>

        {/* 검토 */}
        {!sample && (
          <InView
            as="section"
            id="review"
            aria-labelledby="review-title"
            className="flex scroll-mt-32 flex-col gap-4 rounded-xl border border-line bg-surface p-5 sm:p-6 lg:scroll-mt-20"
          >
            <h2 id="review-title" className="text-title2 font-semibold">
              검토
            </h2>
            {phase === "draft" && (
              <div className="no-print flex flex-col gap-4">
                <p className="text-sm text-muted">
                  조정한 세부 행동 {adjustments.length}개 · 조정하지 않은 세부 행동은 AI 판단 값으로
                  확정됩니다.
                </p>
                {adjustments.length > 0 && (
                  <ul className="flex flex-wrap gap-2">
                    {dims
                      .filter((d) => edits[d.dimension_id])
                      .map((d) => {
                        const f = finalOf(d);
                        return (
                          <li key={d.dimension_id} className="pop-in rounded-full">
                            <a
                              href={`#dim-${d.dimension_id}`}
                              className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-sunken px-3 py-1 text-xs hover:border-accent"
                            >
                              <span className="font-medium text-ink">{d.name}</span>
                              <span className="text-muted tabular-nums">
                                {d.level ?? "–"} → {f.level ?? "–"}
                              </span>
                            </a>
                          </li>
                        );
                      })}
                  </ul>
                )}
                <div className="max-w-sm">
                  <Input
                    label="검토자 이름"
                    value={reviewer}
                    onChange={(e) => setReviewer(e.target.value)}
                    maxLength={40}
                    error={error ?? undefined}
                    required
                  />
                </div>
                <div>
                  <Button
                    disabled={!!reviewerProblem}
                    loading={pending}
                    onClick={() => setConfirm(true)}
                  >
                    검토 완료
                  </Button>
                </div>
                <ConfirmDialog
                  open={confirm}
                  onOpenChange={setConfirm}
                  title="검토를 완료할까요?"
                  description={REVIEW_CONFIRM}
                  confirmLabel="검토 완료"
                  pending={pending}
                  onConfirm={() => {
                    setConfirm(false);
                    onReview?.({ adjustments, reviewer_name: reviewer.trim() });
                  }}
                />
              </div>
            )}
            {phase === "approving" && (
              <div className="reveal flex flex-col gap-2 text-sm">
                <p role="status" className="inline-flex items-center gap-2">
                  <span aria-hidden className="relative flex size-2.5">
                    <span className="absolute inline-flex size-full animate-ping rounded-full bg-accent opacity-60 motion-reduce:animate-none" />
                    <span className="relative inline-flex size-2.5 rounded-full bg-accent-fill" />
                  </span>
                  확정 중… 검토자 {report.review?.reviewer_name}
                </p>
                {error && (
                  <p role="alert" className="text-danger">
                    {error}
                  </p>
                )}
                {stalled && (
                  <div className="no-print flex flex-wrap items-center gap-3">
                    <span className="text-muted">
                      조정은 저장되어 있습니다. 확정만 다시 시도합니다.
                    </span>
                    <Button size="sm" variant="secondary" loading={pending} onClick={onRetry}>
                      확정 다시 시도
                    </Button>
                  </div>
                )}
              </div>
            )}
            {phase === "final" && (
              <p className="pop-in flex items-center gap-2 rounded-lg bg-success-soft px-3 py-2 text-sm">
                <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-success text-on-accent">
                  <DrawnCheck size={12} delay={200} />
                </span>
                <span>
                  {formatDateTime(report.reviewed_at)} 검토 완료 · 검토자{" "}
                  {report.review?.reviewer_name}
                  <span className="text-muted">
                    {" "}
                    · 수준은 더 바꿀 수 없고 메모만 추가할 수 있습니다.
                  </span>
                </span>
              </p>
            )}
            {report.notes.length > 0 && (
              <ul className="flex flex-col gap-2">
                {report.notes.map((n) => (
                  <li
                    key={n.id}
                    className="pop-in flex flex-col gap-0.5 rounded-lg border border-line px-3 py-2 text-sm"
                  >
                    <span className="break-words">{n.text}</span>
                    <span className="text-xs text-muted">
                      {n.reviewer_name} · {formatDateTime(n.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {phase !== "draft" && onAddNote && (
              <form
                className="no-print flex flex-col gap-3"
                onSubmit={async (e) => {
                  e.preventDefault();
                  if (!noteText.trim() || !noteName.trim()) return;
                  await onAddNote({ text: noteText.trim(), reviewer_name: noteName.trim() });
                  setNoteText("");
                }}
              >
                <Textarea
                  label="메모 추가"
                  value={noteText}
                  onChange={(e) => setNoteText(e.target.value)}
                />
                <div className="flex flex-wrap items-end gap-3">
                  <div className="max-w-xs flex-1">
                    <Input
                      label="작성자"
                      value={noteName}
                      onChange={(e) => setNoteName(e.target.value)}
                      maxLength={40}
                    />
                  </div>
                  <Button
                    type="submit"
                    variant="secondary"
                    disabled={!noteText.trim() || !noteName.trim()}
                  >
                    메모 추가
                  </Button>
                </div>
              </form>
            )}
          </InView>
        )}
        <p className="text-xs text-muted">
          평가 기준 {report.evaluation.rubric_version} · {formatDateTime(report.generated_at)} 생성
        </p>
      </div>
    </article>
  );
}
