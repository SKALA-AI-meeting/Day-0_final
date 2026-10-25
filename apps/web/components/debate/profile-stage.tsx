"use client";

// 프로필 카드와 왼쪽 아래 단계 안내.
// 음성 회의에서는 현재 화자를 크게, 나머지 세 명을 아래 가로 줄에 놓는다. 다른 화면은 2x2다.
// 말하는 사람 강조는 세 동료 모두 같은 모양이다(성향을 드러내지 않음).
// 지원자 자신은 말하기(녹음) 중에 소리 크기만큼 빛난다.
import { Popover } from "@base-ui/react/popover";
import { Hand } from "lucide-react";
import { useState } from "react";

import { Avatar, Button, cn } from "@/components/ui";
import { resolutionLabel } from "@/lib/resolution";

import { VOICE_ENABLED } from "./link-screens";
import { PHASE_LABEL, SLOT_LABEL, officeTime } from "./state";
import type { Participant, Phase, Seat, StateSnapshot } from "./types";
import { LevelGlow, useVoiceState, type VoiceSource } from "./voice-ui";

export function ProfileStage({
  snap,
  speaking,
  intro,
  onSkipIntro,
  voice = null,
  candidateTalking = false,
  caption = "",
  recentCaption = "",
  fill = false,
  featuredSeat = null,
  hand = false,
  onJump,
}: {
  snap: StateSnapshot;
  /** 지금 말하는(입력 중인) 자리 */
  speaking: Seat[];
  intro: boolean;
  onSkipIntro: () => void;
  voice?: VoiceSource | null;
  /** 음성 토론에서 지원자가 녹음 중인지(서버 발언권 기준). 지원자 칸을 발언 중으로 강조한다 */
  candidateTalking?: boolean;
  caption?: string;
  recentCaption?: string;
  /** 칸을 가득 채운다(음성 토론 가운데 칸). 정사각형으로 줄이지 않아 아래 조작부와 사이에 빈 띠가 생기지 않는다 */
  fill?: boolean;
  /** 음성 회의에서 크게 보여 줄 자리 */
  featuredSeat?: Seat | "candidate" | null;
  /** 동료 차례에 지원자가 든 손 */
  hand?: boolean;
  /** 프로필 안 메시지를 누르면 토론방의 그 말풍선으로. 토론방이 숨어 있으면 없음 */
  onJump?: (messageId: string) => void;
}) {
  const voiceState = useVoiceState(voice);
  const talking = voiceState.status === "recording" || candidateTalking;
  // AI 동료 셋 다음에 지원자(2x2)
  const people = snap.participants
    .filter((p) => p.id !== "candidate")
    .concat(snap.participants.filter((p) => p.id === "candidate"));
  // 화면 읽기 도구용 자막: 늘 붙어 있는 한 곳에서 내용만 바꿔 첫 문장부터 읽히게 한다.
  // 카드 아래 보이는 자막은 aria-hidden이라 두 번 읽지 않고, 프로필 버튼 이름에도 섞이지 않는다.
  const captionSpeaker = caption
    ? people.find((p) => p.id !== "candidate" && speaking.includes(p.id as Seat))
    : undefined;
  return (
    <div className="flex size-full justify-center [container-type:size]">
      <section
        aria-label="참여자"
        className="@container flex flex-col gap-[3cqmin] rounded-xl border border-line bg-surface p-[3cqmin]"
        style={
          fill
            ? { width: "100%", height: "100%" }
            : { width: "min(100cqw, 100cqh, 760px)", height: "min(100cqw, 100cqh, 760px)" }
        }
      >
        {intro && (
          <header className="flex shrink-0 items-center gap-3 px-1 @max-sm:hidden">
            <div className="flex flex-col">
              <h2 className="text-title3 font-semibold">새 프로젝트 팀</h2>
              <p className="text-footnote text-muted">
                팀원들이 먼저 인사하고 있어요. 곧 대화가 열려요.
              </p>
            </div>
            <Button size="sm" variant="secondary" className="ml-auto" onClick={onSkipIntro}>
              바로 대화하기
            </Button>
          </header>
        )}
        <p aria-live="polite" aria-atomic="true" className="sr-only" data-testid="caption-live">
          {captionSpeaker ? `${captionSpeaker.display_name}: ${caption}` : ""}
        </p>
        <ul
          className={cn(
            "min-h-0 flex-1",
            fill && featuredSeat ? "relative" : "grid grid-cols-2 grid-rows-2 gap-[3cqmin]",
          )}
        >
          {people.map((person) => (
            <ProfileTile
              key={person.id}
              position={
                fill && featuredSeat
                  ? person.id === featuredSeat
                    ? -1
                    : people
                        .filter((p) => p.id !== featuredSeat)
                        .findIndex((p) => p.id === person.id)
                  : undefined
              }
              person={person}
              snap={snap}
              speaking={person.id === "candidate" ? talking : speaking.includes(person.id as Seat)}
              voice={person.id === "candidate" && talking ? voice : null}
              caption={speaking.includes(person.id as Seat) ? caption : ""}
              recentCaption={
                person.id === featuredSeat && !speaking.includes(person.id as Seat)
                  ? recentCaption
                  : ""
              }
              hand={hand && person.id === "candidate"}
              voiceMode={VOICE_ENABLED}
              onJump={onJump}
            />
          ))}
        </ul>
      </section>
    </div>
  );
}

function ProfileTile({
  person,
  snap,
  speaking,
  voice,
  caption,
  recentCaption,
  position,
  hand,
  voiceMode,
  onJump,
}: {
  person: Participant;
  snap: StateSnapshot;
  speaking: boolean;
  /** 지원자가 발언 중일 때만: 소리 크기만큼 빛난다 */
  voice: VoiceSource | null;
  caption: string;
  recentCaption: string;
  position?: number;
  hand: boolean;
  voiceMode: boolean;
  onJump?: (messageId: string) => void;
}) {
  const me = person.id === "candidate";
  const [open, setOpen] = useState(false);
  return (
    <li
      className={cn("min-h-0 min-w-0", position !== undefined && "absolute")}
      data-seat={person.id}
      data-featured={position === -1 || undefined}
      style={
        position === undefined
          ? undefined
          : position === -1
            ? { top: 0, left: 0, width: "100%", height: "calc(100% - max(100px, 25%) - 3cqmin)" }
            : {
                top: "calc(100% - max(100px, 25%))",
                left: `calc(${position} * (100% + 3cqmin) / 3)`,
                width: "calc((100% - 6cqmin) / 3)",
                height: "max(100px, 25%)",
              }
      }
    >
      <Popover.Root open={open} onOpenChange={setOpen}>
        <Popover.Trigger
          aria-label={`${person.display_name} 프로필 보기${hand ? " · 손 듦" : ""}`}
          data-speaking={speaking || undefined}
          className={cn(
            "press relative flex size-full flex-col items-center justify-center gap-[5cqmin] overflow-visible rounded-lg bg-surface-sunken [container-type:size]",
            "transition-shadow duration-200 hover:bg-line/40 data-[popup-open]:ring-2 data-[popup-open]:ring-accent/50",
            speaking && (me ? "ring-2 ring-danger" : "ring-2 ring-accent"),
          )}
        >
          {voice && <LevelGlow voice={voice} />}
          <span
            className="block"
            style={{ width: "44cqmin", height: "44cqmin", fontSize: "18cqmin" }}
          >
            <Avatar
              size="fill"
              seat={person.id}
              src={person.avatar_url}
              name={person.display_name}
            />
          </span>
          {hand && (
            <span className="absolute right-2 top-2 flex items-center gap-1 rounded-md bg-accent-soft px-2 py-1 text-footnote text-accent">
              <Hand size={14} aria-hidden />손 듦
            </span>
          )}
          <span
            className="flex max-w-full flex-col items-center px-1 text-center leading-tight"
            style={{ fontSize: "max(11px, 9cqmin)" }}
          >
            <span className="max-w-full truncate font-semibold">{person.display_name}</span>
            <span className={cn("text-[0.8em]", speaking && me ? "text-danger" : "text-muted")}>
              {speaking ? "발언 중" : me ? "나" : "AI 동료"}
            </span>
          </span>
          {!me && (speaking || recentCaption) && (
            <span
              aria-hidden="true"
              className={cn(
                "max-h-[5em] w-full overflow-y-auto px-2 text-center text-footnote",
                !speaking && "text-muted opacity-65",
              )}
            >
              {speaking ? caption : recentCaption}
            </span>
          )}
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Positioner side="right" align="start" sideOffset={10} className="z-40">
            <Popover.Popup className="w-80 max-w-[calc(100vw-2rem)] rounded-xl border border-line bg-surface p-4 shadow-[var(--shadow-float)] outline-none">
              <ProfileCard
                person={person}
                snap={snap}
                speaking={speaking}
                voiceMode={voiceMode}
                onJump={
                  onJump &&
                  ((id) => {
                    setOpen(false);
                    onJump(id);
                  })
                }
              />
            </Popover.Popup>
          </Popover.Positioner>
        </Popover.Portal>
      </Popover.Root>
    </li>
  );
}

/** 프로필을 누르면 뜨는 정보. 음성 회의에서는 이름·역할만 보인다. */
export function ProfileCard({
  person,
  snap,
  speaking,
  onJump,
  voiceMode = VOICE_ENABLED,
}: {
  person: Participant;
  snap: StateSnapshot;
  speaking: boolean;
  onJump?: (messageId: string) => void;
  /** 음성 토론이면 AI 동료도 "발언 중", 글 토론이면 "입력 중" */
  voiceMode?: boolean;
}) {
  const me = person.id === "candidate";
  const phase = snap.session.phase;
  const said = voiceMode
    ? []
    : snap.messages
        .filter((m) => m.sender === person.id && (!phase || m.phase === phase))
        .slice(-3);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <Avatar size={36} seat={person.id} src={person.avatar_url} name={person.display_name} />
        <div className="flex min-w-0 flex-col">
          <Popover.Title className="truncate text-callout font-semibold">
            {person.display_name}
            {me && <span className="ml-1 text-footnote font-normal text-muted">(나)</span>}
          </Popover.Title>
          <Popover.Description className="text-caption text-muted">
            {me ? "지원자" : "AI 동료 · 사람 팀원 역할을 맡은 AI"}
          </Popover.Description>
        </div>
      </div>
      {!voiceMode && speaking && (
        <p className={cn("text-footnote font-medium", me ? "text-danger" : "text-accent")}>
          {me ? "발언 중이에요" : "지금 입력 중이에요"}
        </p>
      )}
      {!voiceMode && (
        <div className="flex flex-col gap-1.5 border-t border-line pt-3">
          <p className="text-caption text-muted">
            {phase ? `${PHASE_LABEL[phase]} 단계에서 한 말` : "한 말"}
          </p>
          {said.length === 0 ? (
            <p className="text-footnote text-muted">이번 단계에서는 아직 말하지 않았어요.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {said.map((m) => {
                const time = officeTime(
                  m.t_min,
                  snap.session.session_minutes,
                  snap.session.office_start,
                );
                const body = (
                  <>
                    <span className="line-clamp-2 text-footnote">{m.content}</span>
                    <span className="text-caption text-subtle tabular-nums">{time}</span>
                  </>
                );
                return (
                  <li key={m.id}>
                    {onJump ? (
                      <button
                        type="button"
                        onClick={() => onJump(m.id)}
                        className="flex w-full flex-col items-start gap-0.5 rounded-md px-2 py-1.5 text-left hover:bg-surface-sunken"
                        title="토론방에서 보기"
                      >
                        {body}
                      </button>
                    ) : (
                      <div className="flex flex-col gap-0.5 px-2 py-1.5">{body}</div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

const PHASE_GUIDE: Record<Phase, string> = {
  keyword: "팀이 함께 다룰 키워드 하나를 정해요.",
  problem: "정한 키워드와 관련된 문제 상황 하나를 정해요.",
  solution: "문제 상황에 맞는 해결책 하나를 정해요.",
  proposal: "지금까지 정한 내용으로 기획안을 써서 제출해요.",
};

/** 왼쪽 아래 단계 안내: 지금 단계에서 할 일과 앞 단계에서 정한 것 */
export function PhaseSummary({ snap }: { snap: StateSnapshot }) {
  const phase = snap.session.phase;
  const done = snap.slots.filter((s) => s.status === "confirmed");
  return (
    <section
      aria-label="단계 안내"
      className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto rounded-xl border border-line bg-surface p-4"
    >
      {phase && (
        <div className="flex flex-col gap-1">
          <p className="text-caption text-muted">지금 단계</p>
          <h2 className="text-callout font-semibold">{PHASE_LABEL[phase]}</h2>
          <p className="text-footnote">{PHASE_GUIDE[phase]}</p>
        </div>
      )}
      {done.length > 0 && (
        <dl className="flex flex-col gap-2 border-t border-line pt-3">
          {done.map((slot) => {
            const label = slot.resolution
              ? resolutionLabel({ resolution: slot.resolution, outcome: slot.outcome })
              : null;
            return (
              <div key={slot.phase} className="flex flex-col">
                <dt className="text-caption text-muted">
                  {SLOT_LABEL[slot.phase]}
                  {label && ` · ${label}`}
                </dt>
                <dd className="text-footnote font-medium break-words">{slot.value}</dd>
              </div>
            );
          })}
        </dl>
      )}
    </section>
  );
}
