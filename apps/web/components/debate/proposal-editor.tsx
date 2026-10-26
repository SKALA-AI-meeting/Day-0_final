"use client";

// 기획안(ProposalEditor): 우측 탭 안 6칸 세로 폼(UX 결정 6). 예시 문장 없이 칸이 무엇인지만 한 줄로 쓴다.
// idea_credit 안내는 R7 고정 문구. 제출은 기획안 작성 단계에서만, 확인 창 하나를 거친다.
import { CircleCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button, ConfirmDialog, cn } from "@/components/ui";

import { SECTION_IDS, type SectionId, type Sections } from "./types";

export const SECTION_META: Record<SectionId, { title: string; hint: string }> = {
  keyword: { title: "키워드", hint: "정한 키워드" },
  problem: { title: "문제 상황", hint: "정한 문제 상황" },
  solution: { title: "해결책", hint: "정한 해결 방향" },
  rationale: { title: "근거", hint: "이 기획을 뒷받침하는 근거" },
  expected_effect: { title: "기대효과", hint: "이 기획으로 기대하는 효과" },
  idea_credit: { title: "아이디어 기여", hint: "이 기획에 반영된 의견과 그 의견을 낸 사람" },
};

/** 공백뿐인 칸의 이름 목록(제출 직전 빈칸 경고용) */
export function blankSections(sections: Sections): string[] {
  return SECTION_IDS.filter((id) => sections[id].trim() === "").map((id) => SECTION_META[id].title);
}

/** 제출 본문: 빈칸 경고 창을 거쳤을 때만 blank_warning_shown을 보낸다 */
export function submitBody(blankWarningShown: boolean): { blank_warning_shown?: true } {
  return blankWarningShown ? { blank_warning_shown: true } : {};
}

/**
 * 제출 흐름: 저장 완료(409면 최신 초안으로 바뀜) → 그 최신 초안으로 빈 칸 검사 → 빈 칸이 있으면 경고 창 →
 * 제출 확인 창 → submit. 어느 창에서든 취소하면 submit을 부르지 않는다.
 */
export async function submitFlow(steps: {
  prepare: () => Promise<Sections | null>;
  askBlank: (names: string[]) => Promise<boolean>;
  askConfirm: () => Promise<boolean>;
  submit: (blankWarningShown: boolean) => Promise<void>;
}) {
  const latest = await steps.prepare();
  if (!latest) return;
  const blank = blankSections(latest);
  if (blank.length > 0 && !(await steps.askBlank(blank))) return;
  if (!(await steps.askConfirm())) return;
  await steps.submit(blank.length > 0);
}

/** 예/아니오 창 하나를 Promise로 묻는다 */
function useAsk() {
  const [open, setOpen] = useState(false);
  const resolver = useRef<((answer: boolean) => void) | null>(null);
  const ask = () =>
    new Promise<boolean>((resolve) => {
      resolver.current = resolve;
      setOpen(true);
    });
  const answer = (value: boolean) => {
    setOpen(false);
    resolver.current?.(value);
    resolver.current = null;
  };
  return { open, ask, answer };
}

/** 저장 표시는 상대 표기만 쓴다(실제 시계 금지, R7): 방금 / n분 전 / 저장됨 */
export function savedLabel(savedAt: number, now: number): string {
  const minutes = Math.floor((now - savedAt) / 60_000);
  if (minutes < 1) return "방금 저장됨";
  if (minutes < 60) return `${minutes}분 전 저장됨`;
  return "저장됨";
}

export function ProposalEditor({
  sections,
  savedAt,
  saving,
  canSubmit,
  locked,
  onChange,
  prepareSubmit,
  onSubmit,
}: {
  sections: Sections;
  /** 마지막 저장 시각(epoch ms, 경과 시간 계산용) */
  savedAt: number | null;
  saving: boolean;
  canSubmit: boolean;
  locked: boolean;
  onChange: (id: SectionId, value: string) => void;
  /** 저장을 마치고(409면 최신 초안으로 바꾸고) 최신 초안을 돌려준다. 저장하지 못하면 null */
  prepareSubmit: () => Promise<Sections | null>;
  onSubmit: (blankWarningShown: boolean) => Promise<void>;
}) {
  const blankAsk = useAsk();
  const confirmAsk = useAsk();
  const [busy, setBusy] = useState(false);
  // "제출 중…" 표시는 확인 창에서 제출을 누른 뒤 submit 요청이 진행되는 동안만(busy는 입력 잠금용)
  const [submitting, setSubmitting] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    // 새로 저장되면 savedAt이 now보다 늦어 "방금"으로 보인다(Math.max), 이후 20초마다 경과를 다시 센다
    const id = window.setInterval(() => setNow(Date.now()), 20_000);
    return () => window.clearInterval(id);
  }, [savedAt]);
  return (
    <div className="flex flex-col">
      <div className="flex flex-col gap-6 px-6 py-5">
        {SECTION_IDS.map((id) => (
          <label key={id} className="flex flex-col gap-1">
            <span className="text-callout font-semibold">{SECTION_META[id].title}</span>
            <span className="text-footnote text-muted">{SECTION_META[id].hint}</span>
            <textarea
              value={sections[id]}
              disabled={locked}
              readOnly={busy}
              rows={id === "keyword" ? 1 : 2}
              data-section={id}
              placeholder={locked ? "비어 있음" : "여기에 적어요"}
              onChange={(event) => onChange(id, event.target.value)}
              className="mt-1 resize-y rounded-md border border-subtle bg-surface px-3 py-2 text-body outline-none field-sizing-content placeholder:text-muted focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30 disabled:border-transparent disabled:bg-surface-sunken disabled:text-ink"
            />
          </label>
        ))}
      </div>
      <div className="sticky bottom-0 flex bg-surface items-center gap-3 border-t border-line px-6 py-3">
        <span
          className={cn(
            "inline-flex items-center gap-1 text-footnote tabular-nums",
            locked ? "text-ink" : "text-muted",
          )}
          aria-live="polite"
        >
          {locked && <CircleCheck size={14} className="text-success" aria-hidden />}
          {locked
            ? "제출했어요"
            : submitting
              ? "제출 중…"
              : saving
                ? "저장 중…"
                : savedAt
                  ? savedLabel(savedAt, Math.max(now, savedAt))
                  : "자동 저장"}
        </span>
        {!canSubmit && !locked && (
          <span className="text-footnote text-muted">기획안 작성 단계에서 제출할 수 있어요</span>
        )}
        {!locked && (
          <Button
            className="ml-auto"
            size="sm"
            disabled={!canSubmit || busy}
            loading={submitting}
            onClick={async () => {
              setBusy(true);
              try {
                await submitFlow({
                  prepare: prepareSubmit,
                  askBlank: blankAsk.ask,
                  askConfirm: confirmAsk.ask,
                  submit: async (blankWarningShown) => {
                    setSubmitting(true);
                    try {
                      await onSubmit(blankWarningShown);
                    } finally {
                      setSubmitting(false);
                    }
                  },
                });
              } finally {
                setBusy(false);
              }
            }}
          >
            제출
          </Button>
        )}
      </div>
      <ConfirmDialog
        open={blankAsk.open}
        onOpenChange={(next) => !next && blankAsk.answer(false)}
        title="비워 둔 칸이 있어요. 그대로 제출할까요?"
        description={blankSections(sections).join(", ")}
        confirmLabel="그대로 제출"
        cancelLabel="돌아가서 쓰기"
        onConfirm={() => blankAsk.answer(true)}
      />
      <ConfirmDialog
        open={confirmAsk.open}
        onOpenChange={(next) => !next && confirmAsk.answer(false)}
        title="제출하면 수정할 수 없어요"
        confirmLabel="제출"
        onConfirm={() => confirmAsk.answer(true)}
      />
    </div>
  );
}
