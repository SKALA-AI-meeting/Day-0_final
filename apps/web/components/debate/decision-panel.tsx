"use client";

// 결정 사항(ConsensusPanel): 정한 결과 줄만 쌓이고 현재 정할 항목에만 입력과 [이걸로 정하기].
// 후보안·지지 현황·오피스 시각은 보이지 않는다(D14·R7). 정하기는 늘 확인 창 하나(UX 결정 1·2).
import { Check, CircleCheck, Clock3 } from "lucide-react";
import { useState } from "react";

import { Button, ConfirmDialog, cn } from "@/components/ui";
import { resolutionLabel } from "@/lib/resolution";

import { SLOT_LABEL, nameOf } from "./state";
import { SLOT_PHASES, type Slot, type SlotPhase, type StateSnapshot } from "./types";

export type KeywordOption = { key: string; label: string };

// 입력칸 안내(평가·점수 암시 없이 무엇을 적는지만). 채팅 입력창과 헷갈리지 않게 "여기서 정한다"를 분명히 쓴다.
const INTRO =
  "이 토론은 키워드 → 문제 상황 → 해결책을 정해 기획안을 씁니다. 키워드는 여기서 후보 중 하나를 골라 정해요.";
const PLACEHOLDER: Record<Exclude<SlotPhase, "keyword">, string> = {
  problem: "누가 어떤 상황에서 무엇이 어려운지 한두 문장으로 적어요",
  solution: "누가 무엇을 어떻게 하는지 한두 문장으로 적어요",
};

function Result({ slot, snap, flash }: { slot: Slot; snap: StateSnapshot; flash: boolean }) {
  const { filled_by: filledBy } = slot;
  const label = resolutionLabel({
    resolution: slot.resolution ?? "consensus",
    outcome: slot.outcome,
  });
  const timeout = label === "시간 초과";
  return (
    <li className={cn("flex flex-col gap-1 px-6 py-4", flash && "settle")} data-slot={slot.phase}>
      <div className="flex items-baseline gap-2">
        <span className="text-footnote text-muted">{SLOT_LABEL[slot.phase]}</span>
        <span
          className={cn(
            "ml-auto inline-flex items-center gap-1 text-footnote",
            timeout ? "text-muted" : label === "합의" ? "text-success" : "text-ink",
          )}
        >
          {label &&
            (timeout ? <Clock3 size={14} aria-hidden /> : <CircleCheck size={14} aria-hidden />)}
          {label}
        </span>
      </div>
      <p className="text-callout font-semibold break-words">{slot.value}</p>
      {filledBy && filledBy !== "system" && (
        <p className="text-caption text-muted">
          정한 사람 · {filledBy === "candidate" ? "나" : nameOf(snap, filledBy)}
        </p>
      )}
    </li>
  );
}

function SlotInput({
  phase,
  keywordOptions,
  locked,
  onConfirm,
}: {
  phase: SlotPhase;
  keywordOptions: KeywordOption[];
  locked: boolean;
  onConfirm: (phase: SlotPhase, value: string, optionKey?: string) => Promise<string | null>;
}) {
  const [value, setValue] = useState("");
  const [keyword, setKeyword] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isKeyword = phase === "keyword";
  const chosen = keywordOptions.find((k) => k.key === keyword);
  const ready = isKeyword ? !!chosen : value.trim().length > 0;

  const confirm = async () => {
    setPending(true);
    const result = await onConfirm(
      phase,
      isKeyword ? (chosen?.label ?? "") : value.trim(),
      isKeyword ? (chosen?.key ?? undefined) : undefined,
    );
    setPending(false);
    setOpen(false);
    setError(result);
  };

  return (
    <li
      className="flex flex-col gap-3 border-l-2 border-l-accent py-5 pr-6 pl-[22px]"
      data-slot-input={phase}
    >
      <div className="flex items-baseline gap-2">
        <span className="text-callout font-semibold text-ink">{SLOT_LABEL[phase]}</span>
        <span className="rounded-sm bg-accent-soft px-1.5 py-px text-caption font-medium text-accent">
          지금 정할 항목
        </span>
      </div>
      {isKeyword && <p className="text-footnote leading-relaxed text-muted">{INTRO}</p>}
      {isKeyword ? (
        <div
          role="radiogroup"
          aria-label="키워드 후보"
          className="flex rounded-md bg-surface-sunken p-0.5"
        >
          {keywordOptions.map((option) => (
            <button
              key={option.key}
              type="button"
              role="radio"
              aria-checked={keyword === option.key}
              disabled={locked}
              onClick={() => setKeyword(option.key)}
              className={cn(
                "flex-1 rounded-[calc(var(--radius-md)-2px)] px-3 py-1.5 text-body transition-colors duration-150",
                "inline-flex items-center justify-center gap-1",
                keyword === option.key
                  ? "border border-accent bg-accent-soft font-semibold text-accent"
                  : "border border-transparent text-ink hover:bg-surface",
              )}
            >
              {keyword === option.key && <Check size={14} aria-hidden />}
              {option.label}
            </button>
          ))}
        </div>
      ) : (
        <textarea
          rows={2}
          value={value}
          disabled={locked}
          maxLength={200}
          aria-label={`${SLOT_LABEL[phase]} 입력`}
          placeholder={PLACEHOLDER[phase]}
          onChange={(event) => setValue(event.target.value)}
          className="resize-none rounded-md border border-subtle bg-surface px-3 py-2 text-body outline-none placeholder:text-muted focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30 disabled:bg-surface-sunken"
        />
      )}
      {error && (
        <p role="alert" className="text-footnote text-danger">
          {error}
        </p>
      )}
      <div className="flex items-center justify-end gap-3">
        {!ready && !locked && (
          <span className="text-footnote text-muted">
            {isKeyword ? "후보를 고르면 정할 수 있어요" : "내용을 적으면 정할 수 있어요"}
          </span>
        )}
        <Button size="sm" disabled={!ready || locked} onClick={() => setOpen(true)}>
          이걸로 정하기
        </Button>
      </div>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="이걸로 정하면 다음 단계로 넘어가요"
        description={`${SLOT_LABEL[phase]}: ${isKeyword ? (chosen?.label ?? "") : value.trim()}`}
        confirmLabel="네, 정할게요"
        pending={pending}
        onConfirm={confirm}
      />
    </li>
  );
}

export function DecisionPanel({
  snap,
  flash,
  locked,
  keywordOptions,
  onConfirm,
}: {
  snap: StateSnapshot;
  flash: SlotPhase | null;
  locked: boolean;
  keywordOptions: KeywordOption[];
  onConfirm: (phase: SlotPhase, value: string, optionKey?: string) => Promise<string | null>;
}) {
  const current = snap.session.phase;
  return (
    <ul className="flex flex-col divide-y divide-line" aria-label="결정 사항">
      {SLOT_PHASES.map((phase) => {
        const slot = snap.slots.find((s) => s.phase === phase);
        if (slot?.status === "confirmed")
          return <Result key={phase} slot={slot} snap={snap} flash={flash === phase} />;
        if (phase === current)
          return (
            <SlotInput
              key={phase}
              phase={phase}
              keywordOptions={keywordOptions}
              locked={locked}
              onConfirm={onConfirm}
            />
          );
        return (
          <li key={phase} className="flex items-baseline gap-2 px-6 py-4">
            <span className="text-footnote text-muted">{SLOT_LABEL[phase]}</span>
            <span className="ml-auto text-caption text-muted">다음에 정할 항목</span>
          </li>
        );
      })}
    </ul>
  );
}
