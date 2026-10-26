"use client";

// 채용 건 생성: 이름과 세션 길이만 입력한다. 보는 요소 5개는 읽기 전용 안내이고 선택 UI가 없다(R2).
// 칸은 한 화면에 모두 있고(막지 않음) 위 체크포인트가 이름 → 세션 길이 → 확인으로 따라가며,
// 이름을 적으면 만들 내용 요약이 펼쳐진다. 요소는 눌러서 "이렇게 봅니다"를 펼쳐 본다.
import { ChevronDown } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";

import { Button, Checkpoints, cn, Collapse, Input, Reveal } from "@/components/ui";

import { Segmented } from "./segmented";
import type { Catalog, SessionMinutes } from "./types";

export const SAME_SCALE_NOTICE = "모든 채용 건이 같은 요소와 같은 척도로 기록됩니다.";

const MINUTES_LABEL: Record<SessionMinutes, string> = { 60: "60분", 15: "15분 (데모)" };

export const CREATE_STEPS = [
  { id: "name", label: "이름" },
  { id: "minutes", label: "세션 길이" },
  { id: "confirm", label: "확인" },
];

export type PositionFormProps = {
  catalog: Catalog;
  pending?: boolean;
  error?: string | null;
  onSubmit: (value: { name: string; session_minutes: SessionMinutes }) => void;
};

export function PositionForm({ catalog, pending, error, onSubmit }: PositionFormProps) {
  const [name, setName] = useState("");
  const [minutes, setMinutes] = useState<SessionMinutes>(catalog.default_session_minutes);
  // 세션 길이를 고르거나 이름 칸을 떠나면 확인 단계로 본다(기본값을 그대로 써도 된다)
  const [moved, setMoved] = useState(false);
  const [openElement, setOpenElement] = useState<string | "all" | null>(null);
  const named = !!name.trim();
  const step = !named ? "name" : !moved ? "minutes" : "confirm";

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    onSubmit({ name: name.trim(), session_minutes: minutes });
  };
  const toggle = (id: string) => setOpenElement((prev) => (prev === id ? null : id));
  const isOpen = (id: string) => openElement === "all" || openElement === id;

  return (
    <form className="flex flex-col gap-6" onSubmit={submit}>
      <Reveal className="rounded-xl border border-line bg-surface px-4 py-4 sm:px-8">
        <Checkpoints steps={CREATE_STEPS} current={step} label="만들기 단계" />
      </Reveal>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:items-start">
        <Reveal
          delay={100}
          className="flex flex-col gap-6 rounded-xl border border-line bg-surface p-5 sm:p-6 lg:sticky lg:top-20"
        >
          <Input
            label="채용 건 이름"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => named && setMoved(true)}
            placeholder="예: 2026 하반기 서비스기획 신입"
            maxLength={80}
            required
          />
          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">세션 길이</span>
            <Segmented
              label="세션 길이"
              value={minutes}
              onChange={(m) => {
                setMinutes(m);
                setMoved(true);
              }}
              className="self-start"
              options={catalog.session_minutes_options.map((m) => ({
                value: m,
                label: MINUTES_LABEL[m],
              }))}
            />
            <p className="text-sm text-muted">
              15분은 시연용으로 모든 시간을 같은 비율로 줄입니다.
            </p>
          </div>

          {/* 이름을 적으면 만들 내용이 펼쳐진다 */}
          <Collapse open={named}>
            <dl
              aria-label="만들 내용"
              className="flex flex-col gap-2 rounded-lg bg-surface-sunken px-4 py-3 text-sm"
            >
              <div className="flex gap-3">
                <dt className="w-16 shrink-0 text-muted">이름</dt>
                <dd className="min-w-0 font-semibold break-all">{name.trim() || "—"}</dd>
              </div>
              <div className="flex gap-3">
                <dt className="w-16 shrink-0 text-muted">세션</dt>
                <dd className="tabular-nums">{MINUTES_LABEL[minutes]}</dd>
              </div>
              <div className="flex gap-3">
                <dt className="w-16 shrink-0 text-muted">장면</dt>
                <dd>AI 동료 3명 · 보는 요소 {catalog.elements.length}개 · 모든 지원자 같은 장면</dd>
              </div>
            </dl>
          </Collapse>

          {error && (
            <p
              role="alert"
              className="pop-in rounded-md bg-danger-soft px-3 py-2 text-sm text-danger"
            >
              {error}
            </p>
          )}
          <Button type="submit" loading={pending} disabled={!named} className="self-start">
            채용 건 만들기
          </Button>
        </Reveal>

        <section aria-labelledby="elements-title" className="flex flex-col gap-3">
          <Reveal delay={180} className="flex items-center justify-between gap-3">
            <h2 id="elements-title" className="text-title3 font-semibold">
              보는 요소
            </h2>
            <button
              type="button"
              aria-pressed={openElement === "all"}
              onClick={() => setOpenElement((prev) => (prev === "all" ? null : "all"))}
              className="rounded-md px-2 py-1 text-footnote text-accent hover:bg-accent-soft"
            >
              {openElement === "all" ? "모두 접기" : "모두 펼치기"}
            </button>
          </Reveal>
          <ul className="flex flex-col gap-2">
            {catalog.elements.map((element, i) => {
              const open = isOpen(element.id);
              const panel = `element-${element.id}`;
              return (
                <Reveal
                  as="li"
                  key={element.id}
                  delay={260 + i * 80}
                  className={cn(
                    "rounded-xl border bg-surface transition-colors duration-200",
                    open ? "border-accent/40" : "border-line",
                  )}
                >
                  <button
                    type="button"
                    aria-expanded={open}
                    aria-controls={panel}
                    onClick={() => toggle(element.id)}
                    className="flex w-full items-start gap-3 rounded-xl px-4 py-3 text-left"
                  >
                    <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-surface-sunken text-caption font-semibold text-muted tabular-nums">
                      {i + 1}
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="font-semibold">{element.name}</span>
                      <span className="text-sm text-ink">{element.meaning}</span>
                    </span>
                    <ChevronDown
                      aria-hidden
                      className={cn(
                        "mt-1 size-4 shrink-0 text-subtle transition-transform duration-300 motion-reduce:transition-none",
                        open && "rotate-180",
                      )}
                    />
                  </button>
                  <Collapse open={open}>
                    <p id={panel} className="px-4 pb-3 pl-13 text-sm text-muted">
                      이렇게 봅니다 · {element.how_we_see}
                    </p>
                  </Collapse>
                </Reveal>
              );
            })}
          </ul>
          <Reveal delay={260 + catalog.elements.length * 80} as="p" className="text-sm text-muted">
            {SAME_SCALE_NOTICE}{" "}
            <Link
              href="/admin/sample-report"
              className="text-accent underline-offset-4 hover:underline"
            >
              샘플 리포트 보기
            </Link>
          </Reveal>
        </section>
      </div>
    </form>
  );
}
