"use client";

// 토론방에 들어가기 전 화면: 링크 안내(만료·취소·없음), 입장, 동의(표시 이름·형식 안내), 대기(팀 소개).
// 문구는 docs/product/user-flows.md "지원자: 입장·동의·대기". 페르소나 유형·성향 설명은 여기 없다.
// 화면은 FlowFrame(상단 체크포인트) 안에서 오른쪽에서 미끄러져 들어오고, 안의 정보는 한 번에 펼치지 않고
// 차례로 떠오른다. 동의 항목은 하나씩 열어 확인하면 체크가 그려지고 다음 항목이 열린다.
import { ArrowRight, Clock, MessageSquareText, Mic, Users } from "lucide-react";
import { useState, useSyncExternalStore, type ReactNode } from "react";

import {
  Avatar,
  Button,
  Checkpoints,
  Collapse,
  DrawnCheck,
  Reveal,
  Wordmark,
  cn,
  useSequence,
  type Checkpoint,
} from "@/components/ui";

import { PHASE_LABEL } from "./state";
import type { Entry, Participant, StateSnapshot } from "./types";

/** R1 형식 안내(고정 문구) */
export const FORMAT_NOTICE =
  "토론 결과가 내 제안과 다를 수 있어요. 안의 질은 평가하지 않으며, 그 과정의 태도를 봅니다.";
/** R7 어시스턴트 안내(고정 문구) */
export const ASSISTANT_NOTICE =
  "AI 어시스턴트는 평가에 관여하지 않으며, 요청·응답 기록은 담당자에게 사실로 제공됩니다.";

export const VOICE_ENABLED = !["0", "false"].includes(
  (process.env.NEXT_PUBLIC_VOICE_ENABLED ?? "1").toLowerCase(),
);

/** 음성을 처리하는 곳(동의 화면). 빌드 시점의 VOICE_STT·TTS_PROVIDER(없으면 VOICE_PROVIDER)를 따른다 */
const PROCESSOR_NAMES: Record<string, string> = {
  openai: "OpenAI",
  gemini: "Google Gemini",
  azure: "Azure Speech",
};
const VOICE_STT =
  process.env.NEXT_PUBLIC_VOICE_STT_PROVIDER || process.env.NEXT_PUBLIC_VOICE_PROVIDER;
const VOICE_TTS =
  process.env.NEXT_PUBLIC_VOICE_TTS_PROVIDER || process.env.NEXT_PUBLIC_VOICE_PROVIDER;
/** 이 서비스 서버에서 돌리는 로컬 공급자(받아 적기 whisper, 읽기 supertonic) */
const LOCAL_SERVER = "이 서비스 서버(외부 전송 없음)";
const LOCAL_STT = VOICE_STT === "whisper";
const LOCAL_TTS = VOICE_TTS === "supertonic";
const processorName = (name: string | undefined) => PROCESSOR_NAMES[name ?? ""] ?? "Azure Speech";
const VOICE_PROCESSOR =
  LOCAL_STT && LOCAL_TTS
    ? LOCAL_SERVER
    : LOCAL_STT
      ? `${LOCAL_SERVER}에서 받아 적고, AI 동료 음성은 ${processorName(VOICE_TTS)}`
      : LOCAL_TTS
        ? `${processorName(VOICE_STT)}에서 받아 적고, AI 동료 음성은 ${LOCAL_SERVER}`
        : processorName(VOICE_STT);

export const NAME_MAX = 20;

/** 토론 화면의 최소 폭(debate-view의 min-w-[1024px])과 같다 */
export const DESKTOP_MIN_WIDTH = 1024;
const NARROW_QUERY = `(max-width: ${DESKTOP_MIN_WIDTH - 0.02}px)`;

/** 화면 폭이 토론 화면 최소 폭보다 좁은지. 서버 렌더에서는 false */
export function useNarrowViewport() {
  return useSyncExternalStore(
    (notify) => {
      const query = window.matchMedia(NARROW_QUERY);
      query.addEventListener("change", notify);
      return () => query.removeEventListener("change", notify);
    },
    () => window.matchMedia(NARROW_QUERY).matches,
    () => false,
  );
}

/** 좁은 화면에서는 시작 전에 막는다(토론 화면은 가로 1024px 이상 데스크톱 전용) */
export function DesktopNotice() {
  return (
    <LinkNotice
      title="노트북이나 PC의 Chrome으로 열어 주세요"
      description="이 토론은 화면 가로 폭이 넓은 데스크톱 브라우저에서 진행됩니다. 같은 링크를 노트북이나 PC의 Chrome으로 열어 주세요."
    />
  );
}

/** 지원자 흐름의 체크포인트(상단). 토론 화면에 들어가면 사라지고 끝나면 마무리가 켜진다 */
export const FLOW_STEPS: Checkpoint[] = [
  { id: "entry", label: "입장" },
  { id: "consent", label: "확인" },
  { id: "waiting", label: "준비" },
  { id: "session", label: "토론" },
  { id: "debrief", label: "마무리" },
];

/**
 * 시작 전·마무리 화면의 틀: 토론 화면과 같은 번짐 배경, 반투명 상단 바(워드마크·체크포인트).
 * 틀은 단계가 바뀌어도 남고(체크와 진행선이 이어서 움직임) 본문만 바뀐다.
 */
export function FlowFrame({
  current,
  done = false,
  children,
}: {
  /** 지금 체크포인트 id. 없으면 체크포인트를 숨긴다(링크 안내) */
  current?: string;
  done?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="page-backdrop relative isolate flex min-h-dvh flex-col">
      <header className="vibrancy sticky top-0 z-20 border-b border-line">
        <div className="mx-auto flex h-14 w-full max-w-4xl items-center gap-6 px-4 sm:px-6">
          <Wordmark className="h-4 shrink-0" />
          {current && (
            <Checkpoints
              steps={FLOW_STEPS}
              current={current}
              done={done}
              className="ml-auto w-full max-w-md pt-1 [&_span.text-caption]:max-sm:hidden"
            />
          )}
        </div>
      </header>
      <main className="flex flex-1 flex-col">{children}</main>
    </div>
  );
}

function Page({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <div
      className={cn(
        "screen-forward mx-auto flex w-full flex-1 flex-col justify-center gap-8 px-4 py-12 sm:px-6",
        wide ? "max-w-3xl" : "max-w-xl",
      )}
    >
      {children}
    </div>
  );
}

export function LinkNotice({
  title,
  description,
  onRetry,
}: {
  title: string;
  description: string;
  onRetry?: () => void;
}) {
  return (
    <FlowFrame>
      <Page>
        <Reveal as="h1" className="text-title1 font-bold">
          {title}
        </Reveal>
        <Reveal as="p" index={1} className="text-muted">
          {description}
        </Reveal>
        {onRetry && (
          <Reveal index={2}>
            <Button variant="secondary" onClick={onRetry}>
              다시 시도
            </Button>
          </Reveal>
        )}
      </Page>
    </FlowFrame>
  );
}

function Mail({ to }: { to: string }) {
  return (
    <a className="text-accent underline-offset-4 hover:underline" href={`mailto:${to}`}>
      {to}
    </a>
  );
}

function Fact({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-footnote text-muted">
      <span className="text-accent">{icon}</span>
      {children}
    </span>
  );
}

export function EntryScreen({ entry, onNext }: { entry: Entry; onNext: () => void }) {
  const sentences = entry.entry_notice.split(/(?<=\.)\s+/).filter(Boolean);
  // 안내 문장은 하나씩 떠오르고, 다 나온 뒤 [다음]이 떠오른다(버튼은 처음부터 눌러도 된다)
  const base = 420;
  // 문장이 많아도 다 떠오르는 데 2초를 넘기지 않는다
  const step = Math.min(260, Math.round(1600 / Math.max(1, sentences.length)));
  return (
    <Page>
      <div className="flex flex-col gap-3">
        <Reveal as="p" className="text-footnote font-semibold text-accent">
          {entry.company_name}
        </Reveal>
        <Reveal as="h1" index={1} className="text-large font-bold tracking-tight">
          새 프로젝트 팀 토론
        </Reveal>
        <Reveal index={2} className="flex flex-wrap gap-2">
          <Fact icon={<Clock size={14} aria-hidden />}>{entry.duration_minutes}분</Fact>
          <Fact icon={<Users size={14} aria-hidden />}>AI 동료 3명</Fact>
          <Fact
            icon={
              VOICE_ENABLED ? (
                <Mic size={14} aria-hidden />
              ) : (
                <MessageSquareText size={14} aria-hidden />
              )
            }
          >
            {VOICE_ENABLED ? "음성 토론" : "글로 토론"}
          </Fact>
        </Reveal>
      </div>
      <ol className="flex flex-col gap-2">
        {sentences.map((sentence, i) => (
          <Reveal
            as="li"
            key={sentence}
            delay={base + i * step}
            className="flex items-start gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-callout leading-relaxed"
          >
            <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-caption font-semibold text-accent tabular-nums">
              {i + 1}
            </span>
            {sentence}
          </Reveal>
        ))}
      </ol>
      <Reveal delay={base + sentences.length * step}>
        <Button onClick={onNext}>
          다음
          <ArrowRight size={16} aria-hidden />
        </Button>
      </Reveal>
    </Page>
  );
}

type ConsentItem = { id: string; title: string; body: ReactNode };

/**
 * 동의 항목을 하나씩 확인한다. 지금 항목만 펼쳐지고 [확인했어요]를 누르면 체크가 그려지며 접히고
 * 다음 항목이 열린다. 제목을 눌러 언제든 다시 펼친다. 확인하지 않아도 아래 동의는 할 수 있다(읽기 돕기용).
 */
function ConsentChecklist({ items, onAllRead }: { items: ConsentItem[]; onAllRead: () => void }) {
  const [read, setRead] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(items[0]?.id ?? null);
  const confirm = (id: string) => {
    const next = new Set(read).add(id);
    setRead(next);
    const following = items.find((item) => !next.has(item.id));
    setOpen(following?.id ?? null);
    if (!following) onAllRead();
  };
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-footnote font-semibold text-muted">
          확인한 항목 <span className="text-ink tabular-nums">{read.size}</span> / {items.length}
        </p>
        <div aria-hidden className="h-1 w-32 overflow-hidden rounded-full bg-line">
          <div
            className="progress-fill h-full rounded-full bg-accent"
            style={{ width: `${(read.size / items.length) * 100}%` }}
          />
        </div>
      </div>
      <ul className="flex flex-col gap-2">
        {items.map((item, i) => {
          const isOpen = open === item.id;
          const done = read.has(item.id);
          return (
            <Reveal
              as="li"
              key={item.id}
              delay={160 + i * 90}
              className={cn(
                "rounded-xl border bg-surface transition-colors duration-300",
                isOpen ? "border-accent/50 shadow-[var(--shadow-float)]" : "border-line",
              )}
            >
              <button
                type="button"
                aria-expanded={isOpen}
                onClick={() => setOpen(isOpen ? null : item.id)}
                className="flex w-full items-center gap-3 px-4 py-3 text-left"
              >
                <span
                  className={cn(
                    "flex size-6 shrink-0 items-center justify-center rounded-full border-2 text-caption font-semibold transition-colors duration-300",
                    done
                      ? "border-success bg-success text-on-accent"
                      : isOpen
                        ? "border-accent text-accent"
                        : "border-line text-subtle",
                  )}
                >
                  {done ? <DrawnCheck size={13} /> : i + 1}
                </span>
                <span className={cn("text-body font-semibold", !isOpen && done && "text-muted")}>
                  {item.title}
                </span>
                {done && <span className="sr-only">(확인함)</span>}
              </button>
              <Collapse open={isOpen}>
                <div className="flex flex-col gap-3 px-4 pb-4 pl-13 text-body">
                  <div>{item.body}</div>
                  {!done && (
                    <div>
                      <Button size="sm" variant="secondary" onClick={() => confirm(item.id)}>
                        확인했어요
                      </Button>
                    </div>
                  )}
                </div>
              </Collapse>
            </Reveal>
          );
        })}
      </ul>
    </div>
  );
}

export function ConsentScreen({
  entry,
  pending,
  error,
  onConsent,
}: {
  entry: Entry;
  pending?: boolean;
  error?: string | null;
  onConsent: (displayName: string) => void;
}) {
  const [name, setName] = useState(entry.default_display_name.slice(0, NAME_MAX));
  const [agreed, setAgreed] = useState(false);
  const [allRead, setAllRead] = useState(false);
  const trimmed = name.replace(/\s+/g, " ").trim();
  const nameError =
    trimmed.length === 0
      ? "이름을 입력해 주세요."
      : trimmed.length > NAME_MAX
        ? `${NAME_MAX}자 이하로 입력해 주세요.`
        : null;
  const items: ConsentItem[] = [
    { id: "format", title: "형식", body: FORMAT_NOTICE },
    {
      id: "record",
      title: "기록되는 것",
      body: VOICE_ENABLED
        ? `음성 발언의 최종 전사, 결정 사항 선택, 기획안, AI 어시스턴트 요청 원문, 자료 열람. 음성은 ${VOICE_PROCESSOR}에서 처리하며 원음은 전사 성공 시 삭제하고, 처리 실패 시 최대 24시간 임시 보관합니다.`
        : "토론 메시지, 결정 사항 선택, 기획안, AI 어시스턴트 요청 원문, 자료 열람.",
    },
    {
      id: "evaluation",
      title: "평가 방식",
      body: (
        <>
          세션이 끝난 뒤 AI 평가 에이전트가 공개된 척도로 근거 메시지를 들어 요소별 수준을 판단하고,
          담당자가 그것을 참고해 최종 평가합니다.
          <span className="mt-2 flex flex-wrap gap-1.5">
            {entry.elements_names.map((element) => (
              <span
                key={element}
                className="rounded-full bg-accent-soft px-2.5 py-0.5 text-caption font-medium text-accent"
              >
                {element}
              </span>
            ))}
          </span>
          <span className="sr-only">보는 요소: {entry.elements_names.join(" · ")}</span>
        </>
      ),
    },
    { id: "assistant", title: "AI 어시스턴트", body: ASSISTANT_NOTICE },
    {
      id: "not-made",
      title: "만들지 않는 것",
      body: "합격·순위는 만들지 않습니다. 리포트는 담당자가 평가할 때 보는 참고 자료입니다.",
    },
  ];
  return (
    <Page wide>
      <div className="flex flex-col gap-2">
        <Reveal as="h1" className="text-title1 font-bold">
          시작 전에 확인해 주세요
        </Reveal>
        <Reveal as="p" index={1} className="text-muted">
          항목을 하나씩 열어 읽고 확인해 주세요. 다 읽은 뒤 이름을 정하고 동의하면 준비 화면으로
          넘어가요.
        </Reveal>
      </div>
      <ConsentChecklist items={items} onAllRead={() => setAllRead(true)} />
      <Reveal as="ul" delay={700} className="flex flex-col gap-1.5 text-footnote text-muted">
        <li>외부 도구는 막지 않습니다. 기록되는 것은 이 화면 안의 AI 어시스턴트 요청뿐입니다.</li>
        <li>
          문의: <Mail to={entry.contact_email} />
        </li>
        <li>동의하지 않으면 세션이 시작되지 않으며, 링크는 만료 전까지 다시 열 수 있습니다.</li>
      </Reveal>
      {/* 항목을 다 확인하면 이 칸이 한 번 빛나며 다음 할 일을 알린다 */}
      <Reveal
        key={allRead ? "ready" : "idle"}
        kind={allRead ? "pop" : "reveal"}
        delay={allRead ? 0 : 820}
        className="flex flex-col gap-5 rounded-xl border border-line bg-surface p-5"
      >
        <label className="flex flex-col gap-2">
          <span className="text-footnote font-semibold">토론방에서 보일 이름</span>
          <input
            value={name}
            maxLength={NAME_MAX + 5}
            onChange={(event) => setName(event.target.value)}
            aria-invalid={!!nameError}
            className="h-11 rounded-md border border-line bg-surface px-3 text-callout transition-colors outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
          />
          <span className={`text-footnote ${nameError ? "text-danger" : "text-muted"}`}>
            {nameError ??
              `AI 동료가 이 이름으로 부릅니다. 시작한 뒤에는 바꿀 수 없어요. (1~${NAME_MAX}자)`}
          </span>
        </label>
        <label className="flex items-center gap-2 text-body">
          <input
            type="checkbox"
            className="size-4 accent-[var(--color-accent)]"
            checked={agreed}
            onChange={(event) => setAgreed(event.target.checked)}
          />
          위 내용을 읽었고 동의합니다
        </label>
        {error && (
          <p role="alert" className="text-footnote text-danger">
            {error}
          </p>
        )}
        <div>
          <Button
            disabled={!agreed || !!nameError}
            loading={pending}
            onClick={() => onConsent(trimmed)}
          >
            동의하고 계속
          </Button>
        </div>
      </Reveal>
    </Page>
  );
}

/** 화면 구성 그림: 상단 진행바 / 가운데 토론방 / 우측 탭. 칸이 차례로 들어온다 */
export function LayoutSketch({ animate = false }: { animate?: boolean }) {
  const bar = "h-1.5 rounded-full bg-line";
  const shown = useSequence(3, 380, 250, animate);
  const part = (i: number) =>
    cn("transition-opacity duration-500", animate && shown <= i ? "opacity-0" : "opacity-100");
  return (
    <figure className="flex flex-col gap-3">
      <div
        aria-hidden
        className="flex flex-col overflow-hidden rounded-xl border border-line bg-surface"
      >
        <div className={cn("flex items-center gap-2 border-b border-line px-3 py-2", part(0))}>
          <span className="text-caption font-semibold text-muted">단계 진행바</span>
          <span className={`${bar} w-24`} />
          <span className="ml-auto text-caption text-muted tabular-nums">남은 시간</span>
        </div>
        <div className="grid grid-cols-[1.7fr_1fr]">
          <div className={cn("flex flex-col gap-2 border-r border-line p-3", part(1))}>
            <span className="text-caption font-semibold text-muted">
              {VOICE_ENABLED ? "음성 회의" : "글로 토론"}
            </span>
            <div className="grid grid-cols-2 gap-2">
              {["나", "강한결", "윤하람", "서지안"].map((name) => (
                <span
                  key={name}
                  className="rounded-md border border-line p-2 text-center text-caption"
                >
                  {name}
                </span>
              ))}
            </div>
            <span className="text-caption text-muted">
              {VOICE_ENABLED ? "내 차례 · 발언하기 · 손들기" : "메시지 입력 · 보내기"}
            </span>
          </div>
          <div className={cn("flex flex-col gap-2 p-3", part(2))}>
            <span className="text-caption font-semibold text-muted">우측 탭</span>
            <span className="text-caption text-muted">
              결정 사항 · 자료 · AI 어시스턴트 · 기획안
            </span>
            <span className={`${bar} w-4/5`} />
            <span className={`${bar} w-3/5`} />
          </div>
        </div>
      </div>
      <figcaption className="flex flex-col gap-1 text-footnote text-muted">
        <span>상단: 단계 진행바와 이 단계의 남은 시간</span>
        <span>가운데: {VOICE_ENABLED ? "음성 회의" : "글로 토론"}(AI 동료 3명과 나)</span>
        <span>오른쪽: 결정 사항 · 자료 · AI 어시스턴트 · 기획안 탭</span>
      </figcaption>
    </figure>
  );
}

function minutesText(min: number) {
  const whole = Math.floor(min);
  const sec = Math.round((min - whole) * 60);
  return sec ? `${whole}분 ${sec}초` : `${whole}분`;
}

/** 단계 흐름: 키워드 → 문제 상황 → 해결책 → 기획안을 길이만큼의 막대로 */
function PhaseTimeline({ phases }: { phases: StateSnapshot["phases"] }) {
  const total = phases.reduce((sum, p) => sum + (p.budget_min ?? 0), 0) || 1;
  return (
    <ol className="flex w-full gap-1.5" aria-label="단계 흐름">
      {phases.map((p, i) => (
        <Reveal
          as="li"
          key={p.phase}
          delay={120 + i * 140}
          className="flex min-w-0 flex-col gap-1.5"
          style={{ flexGrow: p.budget_min ?? 1, flexBasis: 0 }}
        >
          <span
            className={cn(
              "h-2 rounded-full",
              p.phase === "proposal" ? "bg-accent-fill" : "bg-accent/35",
            )}
          />
          <span className="truncate text-caption font-semibold">{PHASE_LABEL[p.phase]}</span>
          <span className="text-caption text-muted tabular-nums">
            {minutesText(p.budget_min ?? 0)}
          </span>
        </Reveal>
      ))}
      <span className="sr-only">전체 {minutesText(total)}</span>
    </ol>
  );
}

/** 대기 화면과 토론 중 "?" 도움말이 같이 쓰는 설명. animate면 팀원이 한 명씩 들어온다 */
export function SessionGuide({
  snap,
  animate = false,
}: {
  snap: Pick<StateSnapshot, "participants" | "phases" | "session">;
  animate?: boolean;
}) {
  const proposal = snap.phases.find((p) => p.phase === "proposal");
  const order = snap.phases
    .filter((p) => p.phase !== "proposal")
    .map((p) => PHASE_LABEL[p.phase])
    .join(" → ");
  // AI 동료가 먼저 한 명씩 들어오고 내가 마지막이다
  const people = snap.participants
    .filter((p) => p.id !== "candidate")
    .concat(snap.participants.filter((p) => p.id === "candidate"));
  const joined = useSequence(people.length, 450, 350, animate);
  const count = animate ? joined : people.length;
  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-5">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-title3 font-semibold">새 프로젝트 팀</h2>
          <span className="text-footnote text-muted tabular-nums" aria-live="polite">
            {count} / {people.length}명 입장
          </span>
        </div>
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {people.slice(0, count).map((p) => (
            <TeamMember key={p.id} person={p} animate={animate} />
          ))}
          {people.slice(count).map((p) => (
            <li
              key={p.id}
              aria-hidden
              className="flex h-[108px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line"
            >
              <span className="shimmer size-9 rounded-full" />
              <span className="shimmer h-2.5 w-14 rounded-full" />
            </li>
          ))}
        </ul>
        <p className="text-footnote text-muted">
          사람 팀원 역할을 맡은 AI입니다. 실제 동료처럼 대해 주세요.
        </p>
      </section>
      <section className="flex flex-col gap-3">
        <p className="text-body">
          {order}을 함께 정하고, 마지막 {minutesText(proposal?.budget_min ?? 14)}에 기획안을 혼자
          써서 제출합니다.
        </p>
        <PhaseTimeline phases={snap.phases} />
      </section>
      <LayoutSketch animate={animate} />
      <p className="text-body">
        {VOICE_ENABLED
          ? "상단에는 이 단계의 남은 시간이 보입니다. 내 차례가 되면 발언하기를 눌러 최대 60초 동안 말하고, 받아 적은 글을 확인한 뒤 제출합니다. 동료의 말 중간에 끼어들려면 손들기를 누릅니다."
          : "상단에는 이 단계의 남은 시간이 보입니다. 토론방에 메시지를 입력하고 보내기를 눌러 의견을 전합니다."}
      </p>
    </div>
  );
}

function TeamMember({ person, animate }: { person: Participant; animate: boolean }) {
  const me = person.id === "candidate";
  return (
    <li
      className={cn(
        "flex h-[108px] flex-col items-center justify-center gap-2 rounded-xl border bg-surface",
        me ? "border-accent/50" : "border-line",
        animate && "pop-in",
      )}
    >
      <Avatar size={36} seat={person.id} src={person.avatar_url} name={person.display_name} />
      <span className="text-body">
        {person.display_name}
        {me && <span className="ml-1 text-footnote text-muted">(나)</span>}
      </span>
    </li>
  );
}

export function WaitingScreen({
  snap,
  pending,
  error,
  onStart,
}: {
  snap: StateSnapshot;
  pending?: boolean;
  error?: string | null;
  onStart: () => void;
}) {
  return (
    <Page wide>
      <div className="flex flex-col gap-2">
        <Reveal as="h1" className="text-title1 font-bold">
          시작할 준비가 됐어요
        </Reveal>
        <Reveal as="p" index={1} className="text-muted">
          시작하면 바로 시간이 흐르고, 접속이 끊겨도 멈추지 않습니다.
        </Reveal>
      </div>
      <SessionGuide snap={snap} animate />
      <Reveal as="p" delay={1600} className="text-footnote text-muted">
        이 설명은 화면의 ? 버튼으로 다시 볼 수 있습니다.
      </Reveal>
      {error && (
        <p role="alert" className="text-footnote text-danger">
          {error}
        </p>
      )}
      <Reveal delay={1700}>
        <Button loading={pending} onClick={onStart}>
          {error ? "다시 시도" : "시작하기"}
        </Button>
      </Reveal>
    </Page>
  );
}
