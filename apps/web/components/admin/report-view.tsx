"use client";

// 리포트 화면(한 열 문서): 머리(용어 설명 포함) → 요약표 → ① 토론 흐름 → ② 요소 섹션(차원 접힘) → ③ 사실 지표 → ④ 제출 기획안 → ⑤ AI 활용 → 검토.
// 총점·합계는 어디에도 없다. 인쇄(printing)에서는 차원을 전부 펼치고 근거 앞뒤 맥락은 근거 메시지만 보인다.
// 화면에 보이는 말은 "차원" 대신 "세부 행동"이고(결정 A4), 기록 없음은 상태(장면 없음·반응 없음·판단 보류)와
// 사유를 함께 보인다(결정 A3). 코드의 이름은 계약 그대로 dimension이다.
import { CircleCheck, Clock3, ExternalLink } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { flushSync } from "react-dom";

import {
  Avatar,
  Button,
  Chip,
  cn,
  ConfidenceTag,
  ConfirmDialog,
  Input,
  LevelChip,
  Textarea,
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

const BUBBLE: Record<Sender, string> = {
  candidate: "bg-bubble-candidate text-on-bubble-candidate",
  p1: "bg-bubble-p1 text-on-bubble",
  p2: "bg-bubble-p2 text-on-bubble",
  p3: "bg-bubble-p3 text-on-bubble",
  system: "text-system-notice",
};

type Ctx = {
  report: Report;
  names: Partial<Record<Sender, string>>;
  avatars: Partial<Record<Sender, string | undefined>>;
  clock: (t: number) => string;
  recordHref: (ref: RecordRef) => string;
  printing: boolean;
};

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

/** 발동하지 않은 카드 표시: 선결정이면 "선결정", 아니면 "장면 없음" */
const unfiredText = (card: ReportCard) => (card.preempted ? PREEMPTED_CARD : "장면 없음");

/** 머리의 용어 설명. 기본은 접힘, 인쇄에서는 펼친다 */
function TermsGuide({ printing }: { printing: boolean }) {
  return (
    <details
      id="terms"
      open={printing || undefined}
      className="group rounded-lg border border-line bg-surface px-4 py-3 text-sm"
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 font-medium">
        <span className="text-subtle transition-transform group-open:rotate-90" aria-hidden>
          ›
        </span>
        용어 설명
      </summary>
      <dl className="mt-3 grid gap-x-4 gap-y-2 sm:grid-cols-[6.5rem_1fr]">
        {REPORT_TERMS.map((t) => (
          <div key={t.term} className="contents">
            <dt className="font-medium">{t.term}</dt>
            <dd className="text-muted">{t.text}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

function DesignedChip() {
  return <Chip tone="outline">설계된 장면</Chip>;
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="flex scroll-mt-6 flex-col gap-4">
      <h2 id={`${id}-title`} className="text-title2 font-semibold">
        {title}
      </h2>
      {children}
    </section>
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

/** 맥락 펼침의 메시지 한 줄. 근거 메시지는 accent-soft로 강조한다 */
function ContextLine({ ctx, m, focus }: { ctx: Ctx; m: ContextMessage; focus?: boolean }) {
  return (
    <li className={cn("flex flex-col gap-1 rounded-md px-2 py-1.5", focus && "bg-accent-soft")}>
      <span className="flex items-center gap-2">
        <Who ctx={ctx} who={m.sender} />
        <span className="text-xs text-muted tabular-nums">{ctx.clock(m.t_min)}</span>
        {focus && <span className="text-xs font-medium text-accent">근거</span>}
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

function MessageEvidenceItem({ ctx, e }: { ctx: Ctx; e: MessageEvidence }) {
  const [open, setOpen] = useState(false);
  const context = !ctx.printing && open && e.context;
  const self: ContextMessage = {
    id: e.record_ref.id,
    sender: e.speaker,
    content: e.quote,
    t_min: e.t_min,
  };
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted tabular-nums">
        <span>{ctx.clock(e.t_min)}</span>
        <span>{PHASE_TEXT[e.phase]}</span>
        {e.origin && <span>{ORIGIN_TEXT[e.origin]}</span>}
      </div>
      {context ? (
        <ol className="flex flex-col gap-1 rounded-lg border border-line p-2">
          {e.context!.before.map((m) => (
            <ContextLine key={m.id} ctx={ctx} m={m} />
          ))}
          <ContextLine ctx={ctx} m={self} focus />
          {e.context!.after.map((m) => (
            <ContextLine key={m.id} ctx={ctx} m={m} />
          ))}
        </ol>
      ) : (
        <button
          type="button"
          disabled={ctx.printing || !e.context}
          onClick={() => setOpen(true)}
          aria-label="앞뒤 맥락 펼치기"
          className="flex w-full flex-col items-start gap-1 text-left disabled:cursor-default"
        >
          <Who ctx={ctx} who={e.speaker} />
          <span
            className={cn(
              "max-w-[85%] rounded-bubble px-3 py-1.5 text-sm break-words",
              BUBBLE[e.speaker],
            )}
          >
            {e.quote}
          </span>
        </button>
      )}
      <div className="no-print flex items-center gap-3">
        {e.context && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
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
        <blockquote className="border-l-2 border-line pl-3 break-words">{e.quote}</blockquote>
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
      <div className="no-print flex flex-col gap-3 rounded-lg border border-line p-4">
        <span className="text-sm font-medium">담당자 조정</span>
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
        {draft.level == null && (
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium">기록 없음 사유</span>
            <select
              value={draft.reason ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, reason: (e.target.value || null) as NoRecordReason | null })
              }
              className="w-fit rounded-md border border-line bg-surface px-3 py-2 text-ink"
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
        )}
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
        <div className="grid gap-2 rounded-lg bg-surface-sunken p-3 text-sm sm:grid-cols-2">
          <div className="flex flex-col gap-1 text-muted">
            <span className="text-xs">AI 판단</span>
            {levelChip(dimension.level, dimension.no_record_reason)}
          </div>
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
}: {
  ctx: Ctx;
  d: Dimension;
  edit: Edit | undefined;
  editable: boolean;
  onEdit: (id: string, edit: Edit | null) => void;
}) {
  const final = finalOf(d);
  const cards = ctx.report.cards.filter((c) => c.element === d.element);
  return (
    <details
      id={`dim-${d.dimension_id}`}
      open={ctx.printing || undefined}
      className="group border-b border-line py-3 last:border-b-0"
    >
      <summary className="print-break flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-subtle transition-transform group-open:rotate-90" aria-hidden>
          ›
        </span>
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
      <div className="mt-3 flex flex-col gap-4 pl-5">
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
          <div>
            <h4 className="text-sm font-medium">근거</h4>
            <ul className="divide-y divide-line">
              {d.evidence.map((e) => (
                <EvidenceItem key={e.ref} ctx={ctx} e={e} />
              ))}
            </ul>
          </div>
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
    <table className="w-full border-y border-line text-left text-sm">
      <tbody className="divide-y divide-line">
        {rows.map(([k, v]) => (
          <tr key={k}>
            <th className="w-56 py-2 pr-4 align-top font-normal text-muted">{k}</th>
            <td className="py-2 tabular-nums">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
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
  const printing = !!forcedPrint || printMode;

  // 브라우저 인쇄(⌘P 포함) 직전에 차원을 모두 펼치고 끝나면 되돌린다
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

  return (
    <article className="mx-auto flex w-full max-w-3xl flex-col gap-12">
      {/* 머리 */}
      <header className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
          <Chip tone={phase === "final" ? "success" : phase === "approving" ? "accent" : "warning"}>
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
            인쇄
          </Button>
        </div>
        <div>
          <p className="text-sm text-muted">{report.position.name}</p>
          <h1 className="text-title1 font-bold break-all">{report.alias} 리포트</h1>
        </div>
        {sample && (
          <p className="rounded-lg bg-surface-sunken px-4 py-3 text-sm">{SAMPLE_NOTICE}</p>
        )}
        <p className="text-sm leading-relaxed">{REPORT_NOTICE}</p>
        <p className="flex items-start gap-2 text-sm leading-relaxed text-muted">
          <span className="shrink-0">
            <DesignedChip />
          </span>
          {DESIGNED_SCENE_NOTICE}
        </p>
        <TermsGuide printing={printing} />
        <ul className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
          <li className="inline-flex items-center gap-1.5">
            <Avatar seat="candidate" name={candidate?.display_name ?? report.alias} size={20} />
            {report.alias}
            {candidate && candidate.display_name !== report.alias && (
              <span className="text-muted">· 표시 이름 {candidate.display_name}</span>
            )}
          </li>
          {report.seats
            .filter((s) => s.seat !== "candidate")
            .map((s) => (
              <li key={s.seat} className="inline-flex items-center gap-1.5">
                <Avatar seat={s.seat} src={s.avatar_url} name={s.display_name} size={20} />
                {s.display_name}
                {s.persona_type && (
                  <span className="text-muted">
                    · {PERSONA_TEXT[s.persona_type] ?? s.persona_type}
                  </span>
                )}
              </li>
            ))}
        </ul>
      </header>

      {/* 요약표 */}
      <Section id="summary" title="요소 요약">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] border-y border-line text-left text-sm">
            <thead className="text-xs text-muted">
              <tr className="border-b border-line">
                <th className="py-2 pr-3 font-medium">요소</th>
                <th className="w-48 py-2 pr-3 font-medium">수준</th>
                <th className="min-w-48 py-2 pr-3 font-medium">서술</th>
                <th className="py-2 pr-3 text-right font-medium whitespace-nowrap">기록 없음</th>
                <th className="py-2 text-right font-medium whitespace-nowrap">근거 약함</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {report.elements.map((el) => {
                const adjusted = elementAdjusted(el.element);
                const level = adjusted ? elementLevel(dims, el.element) : el.level;
                const noRecord = dims.filter(
                  (d) => d.element === el.element && finalOf(d).level == null,
                );
                const byState = (state: RecordState) =>
                  noRecord.filter((d) => {
                    const reason = finalOf(d).reason;
                    return reason != null && RECORD_STATE[reason] === state;
                  }).length;
                return (
                  <tr key={el.element} className="align-top">
                    <td className="py-3 pr-3 font-medium whitespace-nowrap">
                      <a
                        href={`#element-${el.element}`}
                        className="underline-offset-4 hover:underline"
                      >
                        {el.name}
                      </a>
                    </td>
                    <td className="py-3 pr-3">
                      <div className="flex flex-col gap-1">
                        {level != null ? (
                          <LevelChip level={level} adjusted={adjusted} />
                        ) : (
                          <LevelChip
                            level={null}
                            reason="모든 세부 행동 기록 없음"
                            adjusted={adjusted}
                          />
                        )}
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
                    <td className="py-3 text-right tabular-nums whitespace-nowrap">
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
        <p className="mt-2 text-xs text-muted">
          기록 없음은 수준을 매기지 않은 세부 행동 수이고 장면 없음·반응 없음으로 나눠 보입니다.
          근거 약함은 확신도 low로 판단한 세부 행동 수, 판단 보류는 AI 평가가 실패해 수준을 정하지
          못한 세부 행동 수예요. 둘 다 원문 기록을 확인해 주세요.
        </p>
      </Section>

      {/* ① 토론 흐름 */}
      <Section id="timeline" title="① 토론 흐름">
        <ol className="grid gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-4">
          {(["keyword", "problem", "solution", "proposal"] as const).map((p) => {
            const slot = report.slots.find((s) => s.phase === p);
            return (
              <li key={p} className="flex flex-col gap-1.5 bg-surface p-3 text-sm">
                <span className="text-xs font-medium text-muted">{PHASE_TEXT[p]}</span>
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
                    <span className="text-xs text-muted">
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
          <ul className="divide-y divide-line border-y border-line">
            {[...report.cards]
              .sort((a, b) => (a.fired_at ?? 999) - (b.fired_at ?? 999))
              .map((card) => {
                const element = report.elements.find((e) => e.element === card.element);
                const dc = report.debrief.cards.find((c) => c.card_id === card.card_id);
                return (
                  <li key={card.card_id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
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
                  </li>
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

      {/* ② 요소 섹션 */}
      <Section id="elements" title="② 요소별 판단">
        {report.elements.map((el) => {
          const adjusted = elementAdjusted(el.element);
          const level = adjusted ? elementLevel(dims, el.element) : el.level;
          return (
            <section
              key={el.element}
              id={`element-${el.element}`}
              className="flex scroll-mt-6 flex-col gap-2"
              aria-labelledby={`element-${el.element}-title`}
            >
              <div className="flex flex-wrap items-center gap-3">
                <h3 id={`element-${el.element}-title`} className="text-title3 font-semibold">
                  {el.name}
                </h3>
                {level != null ? (
                  <LevelChip level={level} adjusted={adjusted} />
                ) : (
                  <LevelChip level={null} reason="모든 세부 행동 기록 없음" adjusted={adjusted} />
                )}
              </div>
              <p className="text-sm">{displayTerms(el.summary)}</p>
              <div className="rounded-lg border border-line bg-surface px-4">
                {dims
                  .filter((d) => d.element === el.element)
                  .map((d) => (
                    <DimensionRow
                      key={d.dimension_id}
                      ctx={ctx}
                      d={d}
                      edit={edits[d.dimension_id]}
                      editable={editable}
                      onEdit={setEdit}
                    />
                  ))}
              </div>
              {el.element === "honesty" && report.contribution_check.length > 0 && (
                <div className="mt-2 flex flex-col gap-2">
                  <h4 className="text-sm font-medium">기여 대조표</h4>
                  <table className="w-full border-y border-line text-left text-sm">
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
                              ? row.credited_in_proposal.map((s) => nameOf(ctx.names, s)).join(", ")
                              : "적히지 않음"}
                            {row.credit_quote && (
                              <span className="block text-xs text-muted">“{row.credit_quote}”</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {report.proposal?.sections.idea_credit != null && (
                    <p className="text-sm">
                      <span className="text-muted">아이디어 기여 칸 원문 · </span>
                      {report.proposal.sections.idea_credit || "비어 있음"}
                    </p>
                  )}
                </div>
              )}
            </section>
          );
        })}
      </Section>

      {/* ③ 사실 지표 */}
      <Section id="facts" title="③ 사실 지표">
        <p className="text-sm text-muted">{FACTS_NOTE}</p>
        <FactsTable facts={report.facts} ctx={ctx} />
      </Section>

      {/* ④ 제출 기획안 */}
      <Section id="proposal" title="④ 제출 기획안">
        {report.proposal ? (
          <>
            <p className="text-sm text-muted">
              {formatDateTime(report.proposal.submitted_at)} 제출
              {report.proposal.auto_submitted && " · 시간 종료로 자동 제출"} · 기획안의 품질은
              평가하지 않습니다
            </p>
            <dl className="divide-y divide-line border-y border-line">
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
        )}
      </Section>

      {/* ⑤ AI 활용 */}
      <Section id="ai" title="⑤ AI 활용">
        <p className="text-sm text-muted">{AI_HEADER} · 시간순 사실만 보입니다</p>
        {report.ai_usage.used && report.ai_usage.events.length > 0 ? (
          <ol className="divide-y divide-line border-y border-line">
            {[...report.ai_usage.events]
              .sort((a, b) => a.t_min - b.t_min)
              .map((ev) => (
                <li key={ev.call_id} className="flex flex-col gap-1.5 py-3 text-sm">
                  <span className="text-xs text-muted tabular-nums">{ctx.clock(ev.t_min)}</span>
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
                      refValue={{ type: "assistant_call", id: ev.call_id, t_min: ev.t_min }}
                    />
                  </span>
                </li>
              ))}
          </ol>
        ) : (
          <p className="text-sm">사용하지 않음</p>
        )}
      </Section>

      {/* 검토 */}
      {!sample && (
        <section
          aria-labelledby="review-title"
          className="flex flex-col gap-4 border-t border-line pt-8"
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
            <div className="flex flex-col gap-2 text-sm">
              <p>확정 중… 검토자 {report.review?.reviewer_name}</p>
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
            <p className="text-sm">
              {formatDateTime(report.reviewed_at)} 검토 완료 · 검토자 {report.review?.reviewer_name}
              <span className="text-muted">
                {" "}
                · 수준은 더 바꿀 수 없고 메모만 추가할 수 있습니다.
              </span>
            </p>
          )}
          {report.notes.length > 0 && (
            <ul className="divide-y divide-line border-y border-line">
              {report.notes.map((n) => (
                <li key={n.id} className="flex flex-col gap-0.5 py-2 text-sm">
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
        </section>
      )}
      <p className="text-xs text-muted">
        평가 기준 {report.evaluation.rubric_version} · {formatDateTime(report.generated_at)} 생성
      </p>
    </article>
  );
}
