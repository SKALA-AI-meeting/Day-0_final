"use client";

// 토론 세션 화면: 상단 바(단계 진행바·이 단계 남은 시간·?·나가기) / 가운데 토론방 / 우측 4탭.
// 남은 시간과 오피스 시각을 한 줄에 쓰지 않는다(R7). 종료되면 입력을 모두 잠그고 [나가기]로 디브리핑.
import { Dialog } from "@base-ui/react/dialog";
import { CircleCheck, Clock3, HelpCircle, X } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Button, Tabs, Wordmark, cn } from "@/components/ui";
import { resolutionLabel } from "@/lib/resolution";

import { VoiceRoom } from "./voice/voice-room";
import { DecisionPanel } from "./decision-panel";
import { SessionGuide } from "./link-screens";
import { ProposalEditor } from "./proposal-editor";
import { phaseRemaining, type PanelTab } from "./reducer";
import { AssistantPanel, MaterialsPanel } from "./side-panels";
import { PHASE_LABEL, clock, warnSeconds } from "./state";
import { PHASES, type EndReason, type StateSnapshot } from "./types";
import { useDebateSession, type SessionAuth } from "./use-session";

export function PhaseBar({ snap }: { snap: StateSnapshot }) {
  const current = snap.session.phase;
  const currentIndex = current ? PHASES.indexOf(current) : -1;
  return (
    <ol className="flex items-center gap-2" aria-label="단계 진행">
      {PHASES.map((phase, i) => {
        const slot = snap.slots.find((s) => s.phase === phase);
        const done = i < currentIndex || (slot?.status === "confirmed" && phase !== current);
        const now = phase === current;
        const label = slot?.resolution
          ? resolutionLabel({ resolution: slot.resolution, outcome: slot.outcome })
          : null;
        const timeout = label === "시간 초과";
        return (
          <li key={phase} className="flex items-center gap-2">
            {i > 0 && <span aria-hidden className="h-px w-5 bg-line" />}
            <span
              aria-current={now ? "step" : undefined}
              className={cn(
                "inline-flex items-center gap-1 text-footnote whitespace-nowrap transition-colors duration-200",
                now ? "font-semibold text-accent" : done ? "text-ink" : "text-muted",
              )}
            >
              {done &&
                (timeout ? (
                  <Clock3 size={14} className="text-muted" aria-hidden />
                ) : (
                  <CircleCheck size={14} className="text-success" aria-hidden />
                ))}
              {PHASE_LABEL[phase]}
              {/* 지난 단계는 아이콘 + 글자로 결과를 보인다(색만으로 구분하지 않음) */}
              {done && label && (
                <span className="text-caption font-normal text-muted">· {label}</span>
              )}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function Timer({ seconds, sessionMinutes }: { seconds: number | null; sessionMinutes: number }) {
  if (seconds === null) return null;
  const warn = seconds <= warnSeconds(sessionMinutes);
  return (
    <span
      className={cn(
        "text-footnote tabular-nums transition-colors duration-300",
        warn ? "font-semibold text-warning" : "text-ink",
      )}
      aria-live="off"
    >
      이 단계 {clock(seconds)} 남음
    </span>
  );
}

function HelpButton({ snap }: { snap: StateSnapshot }) {
  return (
    <Dialog.Root>
      <Dialog.Trigger
        aria-label="화면 설명 다시 보기"
        className="press flex size-8 items-center justify-center rounded-full text-muted hover:bg-surface-sunken"
      >
        <HelpCircle size={18} />
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/25" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 max-h-[85dvh] w-[min(36rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl bg-surface p-6 shadow-[var(--shadow-float)]">
          <div className="mb-4 flex items-center">
            <Dialog.Title className="text-title3 font-semibold">화면 설명</Dialog.Title>
            <Dialog.Close aria-label="닫기" className="ml-auto text-muted">
              <X size={18} />
            </Dialog.Close>
          </div>
          <SessionGuide snap={snap} />
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

const ENDED_TEXT: Record<EndReason, string> = {
  submitted: "제출했어요. 세션이 끝났습니다.",
  timeout: "시간이 끝나 지금 상태로 제출됐어요.",
  failed: "세션이 중단됐어요.",
  cancelled: "세션이 취소됐어요.",
};

export function DebateView({
  auth,
  initial,
  onExit,
}: {
  auth: SessionAuth;
  initial: StateSnapshot;
  onExit: (reason: EndReason) => void;
}) {
  const session = useDebateSession(auth, initial);
  const { view, now } = session;
  const snap = view.snap;
  const locked = !!view.ended || view.replaced;
  const [voiceBusy, setVoiceBusy] = useState(false);
  const remaining = view.ended ? null : phaseRemaining(view, now);

  const tabs: { value: PanelTab; label: string; count?: number }[] = [
    { value: "decisions", label: "결정 사항", count: view.badges.decisions },
    { value: "materials", label: "자료", count: view.badges.materials },
    { value: "assistant", label: "AI 어시스턴트" },
    { value: "proposal", label: "기획안" },
  ];

  const panels: Record<PanelTab, ReactNode> = {
    decisions: (
      <DecisionPanel
        snap={snap}
        flash={view.flash}
        locked={locked || voiceBusy}
        keywordOptions={snap.keyword_options.map((o) => ({ key: o.option_key, label: o.label }))}
        onConfirm={session.confirmSlot}
      />
    ),
    materials: <MaterialsPanel materials={snap.materials} onOpen={session.openMaterial} />,
    assistant: (
      <AssistantPanel
        entries={snap.assistant}
        materials={snap.materials}
        locked={locked}
        onAsk={session.ask}
      />
    ),
    proposal: (
      <ProposalEditor
        sections={session.proposal.draft}
        savedAt={session.proposal.savedAt}
        saving={session.proposal.saving}
        canSubmit={snap.session.phase === "proposal"}
        locked={locked}
        onChange={session.proposal.editSection}
        prepareSubmit={session.proposal.prepareSubmit}
        onSubmit={session.proposal.submit}
      />
    ),
  };

  return (
    <div className="flex h-dvh min-w-[1024px] flex-col bg-canvas">
      <header className="vibrancy sticky top-0 z-30 flex h-13 shrink-0 items-center gap-6 border-b border-line px-5">
        <Wordmark className="h-4" />
        <PhaseBar snap={snap} />
        <div className="ml-auto flex items-center gap-3">
          {session.connection === "reconnecting" && (
            <span className="text-footnote text-muted">다시 연결하는 중…</span>
          )}
          {view.ended ? (
            <>
              <span className="text-footnote text-muted">{ENDED_TEXT[view.ended]}</span>
              <Button size="sm" onClick={() => onExit(view.ended!)}>
                나가기
              </Button>
            </>
          ) : (
            <Timer seconds={remaining} sessionMinutes={snap.session.session_minutes} />
          )}
          <HelpButton snap={snap} />
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        <VoiceRoom
          auth={auth}
          snap={snap}
          connected={session.connection === "open"}
          locked={locked}
          onSend={session.sendVoice}
          onBusy={setVoiceBusy}
        />
        <aside
          className="flex w-[38%] max-w-[520px] min-w-[360px] shrink-0 flex-col border-l border-line bg-surface"
          aria-label="우측 패널"
        >
          {/* 탭을 바꿔도 각 탭의 스크롤·입력 중 내용이 남도록 패널을 모두 둔 채 숨긴다(keepMounted) */}
          <Tabs
            variant="segmented"
            className="min-h-0 flex-1"
            panelClassName="flex flex-col overflow-hidden"
            keepMounted
            value={view.tab}
            onValueChange={(value) => session.setTab(value as PanelTab)}
            items={tabs.map((tab) => ({
              ...tab,
              content: (
                <div data-panel={tab.value} className="min-h-0 flex-1 overflow-y-auto">
                  {panels[tab.value]}
                </div>
              ),
            }))}
          />
        </aside>
      </div>
      {view.replaced && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="flex max-w-sm flex-col gap-2 rounded-xl bg-surface p-6 shadow-[var(--shadow-float)]">
            <p className="text-title3 font-semibold">다른 창에서 열렸어요</p>
            <p className="text-muted">
              이 창은 더 이상 쓰지 않습니다. 마지막에 연 창에서 이어 주세요.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
