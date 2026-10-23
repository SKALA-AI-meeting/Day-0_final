"use client";

// 토론 세션 화면: 상단 바(단계 진행바·영역 켜고 끄기·이 단계 남은 시간·?·나가기) / 배경 레이어 위 분할 레이아웃.
// 칸 배치와 단계별 비율은 stage-layout.ts, 크기 조절·전환은 components/ui/split-layout.tsx,
// 음성 토론(차례·녹음·재생·받아 적은 글 확인)은 voice/voice-room.tsx의 useVoiceTurn.
// 남은 시간과 오피스 시각을 한 줄에 쓰지 않는다(R7). 종료되면 입력을 모두 잠그고 [나가기]로 디브리핑.
import { Dialog } from "@base-ui/react/dialog";
import {
  CircleCheck,
  Clock3,
  FileText,
  HelpCircle,
  ListChecks,
  NotebookPen,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { Button, Tabs, Wordmark, cn } from "@/components/ui";
import { SplitLayout, useSplitLayout } from "@/components/ui/split-layout";
import { resolutionLabel } from "@/lib/resolution";

import { DecisionPanel } from "./decision-panel";
import { SessionGuide } from "./link-screens";
import { PhaseSummary, ProfileStage } from "./profile-stage";
import { ProposalEditor } from "./proposal-editor";
import { phaseRemaining, type PanelTab, type View } from "./reducer";
import { AssistantPanel, MaterialsPanel } from "./side-panels";
import {
  STAGE_TREE,
  stageEpoch,
  stageFlags,
  stageModes,
  stageWeights,
  type RightPane,
} from "./stage-layout";
import { PHASE_LABEL, clock, warnSeconds } from "./state";
import { PHASES, type EndReason, type Seat, type StateSnapshot } from "./types";
import { useDebateSession, type SessionAuth } from "./use-session";
import { MemoPad } from "./voice/memo-pad";
import { useVoiceTurn } from "./voice/voice-room";

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

/** 오른쪽 블록 한 칸. 누르거나 포커스가 들어오면 그 블록의 안 읽음 배지를 지운다(onActivate) */
function Block({
  id,
  title,
  count,
  onActivate,
  className,
  children,
}: {
  id: PanelTab;
  title: string;
  count?: number;
  onActivate: (id: PanelTab) => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={title}
      onPointerDown={() => onActivate(id)}
      onFocusCapture={() => onActivate(id)}
      className={cn(
        "flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-xl border border-line bg-surface",
        className,
      )}
    >
      <header className="flex shrink-0 items-center gap-1.5 border-b border-line px-4 py-2.5">
        <h2 className="text-callout font-semibold">{title}</h2>
        {!!count && (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-accent tabular-nums">
            <span aria-hidden className="size-1.5 rounded-full bg-accent" />
            {count}
          </span>
        )}
      </header>
      <div data-panel={id} className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        {children}
      </div>
    </section>
  );
}

type LibraryTab = "materials" | "assistant";

/**
 * 자료·AI 어시스턴트 묶음. 어시스턴트는 열린 자료를 두고 묻고 답하므로 한 영역에서 탭으로 오간다.
 * 탭을 바꿔도 각 탭의 스크롤·입력 중 내용이 남도록 패널을 모두 둔 채 숨긴다(keepMounted).
 */
function LibraryBlock({
  tab,
  onTab,
  materialsCount,
  materials,
  assistant,
}: {
  tab: LibraryTab;
  onTab: (tab: LibraryTab) => void;
  materialsCount: number;
  materials: ReactNode;
  assistant: ReactNode;
}) {
  return (
    <section
      aria-label="자료·AI 어시스턴트"
      onPointerDown={() => onTab(tab)}
      onFocusCapture={() => onTab(tab)}
      className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-xl border border-line bg-surface"
    >
      <Tabs
        variant="segmented"
        className="min-h-0 flex-1"
        panelClassName="flex flex-col overflow-hidden"
        keepMounted
        value={tab}
        onValueChange={(value) => onTab(value as LibraryTab)}
        items={[
          {
            value: "materials",
            label: "자료",
            count: materialsCount,
            content: (
              <div data-panel="materials" className="min-h-0 flex-1 overflow-y-auto">
                {materials}
              </div>
            ),
          },
          {
            value: "assistant",
            label: "AI 어시스턴트",
            content: (
              <div data-panel="assistant" className="min-h-0 flex-1 overflow-y-auto">
                {assistant}
              </div>
            ),
          },
        ]}
      />
    </section>
  );
}

const TOGGLES: { id: RightPane; label: string; icon: LucideIcon }[] = [
  { id: "decisions", label: "결정 사항", icon: ListChecks },
  { id: "library", label: "자료·AI 어시스턴트", icon: FileText },
  { id: "proposal", label: "기획안", icon: NotebookPen },
];

/** 오른쪽 영역 켜고 끄기. 영역은 필요할 때 저절로 생기지만, 지원자가 직접 열고 닫을 수도 있다 */
function PaneToggles({
  shown,
  badges,
  onToggle,
}: {
  shown: Record<RightPane, boolean>;
  badges: View["badges"];
  onToggle: (id: RightPane, show: boolean) => void;
}) {
  return (
    <div role="group" aria-label="영역 보이기" className="flex items-center gap-1">
      {TOGGLES.map(({ id, label, icon: Icon }) => {
        const count =
          id === "decisions" ? badges.decisions : id === "library" ? badges.materials : 0;
        return (
          <button
            key={id}
            type="button"
            aria-pressed={shown[id]}
            aria-label={`${label}${count ? ` 새 소식 ${count}` : ""}`}
            title={label}
            onClick={() => onToggle(id, !shown[id])}
            className={cn(
              "press relative flex size-8 items-center justify-center rounded-md text-muted hover:bg-surface-sunken",
              shown[id] && "bg-accent-soft text-accent hover:bg-accent-soft",
            )}
          >
            <Icon size={16} aria-hidden />
            {!!count && !shown[id] && (
              <span
                aria-hidden
                className="absolute top-1 right-1 size-1.5 rounded-full bg-accent"
              />
            )}
          </button>
        );
      })}
    </div>
  );
}

/** 인사 차례를 기다리는 최대 시간. 지나면 토론방을 연다 */
const INTRO_MAX_MS = 15_000;

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
  const remaining = view.ended ? null : phaseRemaining(view, now);

  // 음성 토론: 차례·녹음·재생·확인은 서버 발언권을 따르는 useVoiceTurn이 맡고, 화면은 칸에 나눠 그린다.
  // 지원자가 말하는 동안(녹음·받아 적기·제출 중)에는 결정 사항 확정을 잠근다.
  const [voiceBusy, setVoiceBusy] = useState(false);
  const voice = useVoiceTurn({
    auth,
    snap,
    connected: session.connection === "open",
    locked,
    onSend: session.sendVoice,
    onBusy: setVoiceBusy,
  });
  const speaking: Seat[] = voice.speakingSeat ? [voice.speakingSeat] : [];

  // 인사 차례: TTS가 붙기 전에는 [바로 대화하기]나 대기 시간(INTRO_MAX_MS)으로 끝낸다
  const [introDone, setIntroDone] = useState(false);
  const [pinned, setPinned] = useState<Partial<Record<RightPane, boolean>>>({});
  const [libraryTab, setLibraryTab] = useState<LibraryTab>("materials");
  const flags = stageFlags({
    snap,
    // 음성에서는 지원자 차례가 처음 오면 토론방(대화 기록)을 연다
    candidateSpoke: voice.candidateTurn || snap.messages.some((m) => m.sender === "candidate"),
    introDone,
    ended: locked,
    voice: true,
    pinned,
  });
  useEffect(() => {
    if (!flags.intro) return;
    const timer = setTimeout(() => setIntroDone(true), INTRO_MAX_MS);
    return () => clearTimeout(timer);
  }, [flags.intro]);
  const flagKey = JSON.stringify(flags);
  /* eslint-disable react-hooks/exhaustive-deps -- flags는 매 렌더 새 객체라 값(flagKey)으로 묶는다 */
  const preset = useMemo(() => stageWeights(flags), [flagKey]);
  const presetModes = useMemo(() => stageModes(flags), [flagKey]);
  /* eslint-enable react-hooks/exhaustive-deps */
  const layout = useSplitLayout(STAGE_TREE, preset, stageEpoch(flags), presetModes);

  // 블록이 모두 보이므로 view.tab은 "마지막으로 만진 블록"이다. 같은 블록이면 다시 보내지 않는다
  const activate = (id: PanelTab) => {
    if (view.tab !== id) session.setTab(id);
  };
  const openLibraryTab = (tab: LibraryTab) => {
    setLibraryTab(tab);
    activate(tab);
  };

  return (
    <div className="relative isolate flex h-dvh min-w-[1024px] flex-col bg-canvas">
      {/* 배경은 블록과 따로 둔 레이어다. 무늬를 바꿀 때는 globals.css의 .debate-backdrop만 고친다 */}
      <div aria-hidden className="debate-backdrop pointer-events-none absolute inset-0 -z-10" />
      <header className="vibrancy sticky top-0 z-30 flex h-13 shrink-0 items-center gap-6 border-b border-line px-5">
        <Wordmark className="h-4" />
        <PhaseBar snap={snap} />
        <div className="ml-auto flex items-center gap-3">
          {!flags.intro && (
            <PaneToggles
              shown={flags}
              badges={view.badges}
              onToggle={(id, show) => {
                setPinned((prev) => ({ ...prev, [id]: show }));
                if (!show) return;
                if (id === "library") openLibraryTab(libraryTab);
                else activate(id);
              }}
            />
          )}
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
      <SplitLayout
        className="min-h-0 flex-1 p-3"
        tree={STAGE_TREE}
        weights={layout.weights}
        modes={layout.modes}
        onResize={layout.onResize}
        onReset={layout.onReset}
        panes={{
          profiles: (
            <ProfileStage
              snap={voice.chatSnap}
              speaking={speaking}
              intro={flags.intro}
              onSkipIntro={() => setIntroDone(true)}
            />
          ),
          // 왼쪽 칸: 지원자 메모장(개정 V14)
          voice: (
            <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden rounded-xl border border-line">
              <MemoPad sessionId={auth.sessionId} locked={locked} />
            </div>
          ),
          summary: <PhaseSummary snap={snap} />,
          // 가운데 칸: 4명 프로필(2x2, 말하는 사람 강조) + 차례·녹음·손들기 조작
          chat: (
            <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-hidden">
              <div className="min-h-0 flex-1">
                <ProfileStage
                  snap={voice.chatSnap}
                  speaking={speaking}
                  candidateTalking={voice.recording}
                  intro={false}
                  onSkipIntro={() => setIntroDone(true)}
                />
              </div>
              <div className="shrink-0 rounded-xl border border-line bg-surface p-4">
                {voice.controls}
              </div>
            </div>
          ),
          decisions: (
            <Block
              id="decisions"
              title="결정 사항"
              count={view.badges.decisions}
              onActivate={activate}
            >
              <DecisionPanel
                snap={snap}
                flash={view.flash}
                locked={locked || voiceBusy}
                keywordOptions={snap.keyword_options.map((o) => ({
                  key: o.option_key,
                  label: o.label,
                }))}
                onConfirm={session.confirmSlot}
              />
            </Block>
          ),
          proposal: (
            <Block id="proposal" title="기획안" onActivate={activate}>
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
            </Block>
          ),
          library: (
            <LibraryBlock
              tab={libraryTab}
              onTab={(tab) => (tab === libraryTab ? activate(tab) : openLibraryTab(tab))}
              materialsCount={view.badges.materials}
              materials={
                <MaterialsPanel materials={snap.materials} onOpen={session.openMaterial} />
              }
              assistant={
                <AssistantPanel
                  entries={snap.assistant}
                  materials={snap.materials}
                  locked={locked}
                  onAsk={session.ask}
                />
              }
            />
          ),
        }}
      />
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
