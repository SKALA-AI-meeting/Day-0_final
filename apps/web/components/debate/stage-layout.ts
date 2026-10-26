// 토론 화면의 칸 배치(고정 트리)와, 세션 상태에서 정하는 칸별 비율·크기 모드.
// 처음에는 프로필 카드만 크게 띄우고(intro), 지원자 차례가 오면 토론방이 가운데에 열리며 프로필은 왼쪽 위로 줄어든다.
// 왼쪽은 위에서부터 프로필(정사각형)·음성 패널·단계 안내(한 단계가 끝난 뒤), 오른쪽은 역할별 고정 영역이다:
// 위는 하는 일(토론 단계는 결정 사항을 내용 높이만큼, 기획안 단계는 기획안), 아래는 자료·AI 어시스턴트 묶음 탭.
import type { PaneModes, SplitNode, Weights } from "@/components/ui/split-layout";

import type { Seat, StateSnapshot } from "./types";

export const STAGE_TREE: SplitNode = {
  kind: "split",
  id: "root",
  dir: "row",
  children: [
    {
      kind: "split",
      id: "left",
      dir: "col",
      children: [
        { kind: "pane", id: "profiles", minPx: 140 },
        { kind: "pane", id: "voice", minPx: 120 },
        { kind: "pane", id: "summary", minPx: 120 },
      ],
    },
    {
      // 가운데 칸. 음성 토론은 프로필 줄·최근 발언·조작부를 세로로 나누고(경계를 끌어 높이 조절),
      // 글 토론은 토론방(chat) 하나다
      kind: "split",
      id: "center",
      dir: "col",
      children: [
        { kind: "pane", id: "people", minPx: 96 },
        { kind: "pane", id: "talk", minPx: 140 },
        { kind: "pane", id: "controls", minPx: 120 },
        { kind: "pane", id: "chat", minPx: 360 },
      ],
    },
    {
      kind: "split",
      id: "right",
      dir: "col",
      children: [
        { kind: "pane", id: "decisions", minPx: 120 },
        { kind: "pane", id: "proposal", minPx: 240 },
        { kind: "pane", id: "library", minPx: 200 },
      ],
    },
  ],
};

/** 상단 바에서 지원자가 켜고 끄는 오른쪽 영역 */
export type RightPane = "decisions" | "library" | "proposal";

export type StageFlags = {
  /** 프로필 카드만 크게(인사 중). 지원자 차례가 오면 끝난다 */
  intro: boolean;
  /** 기획안 단계(또는 끝남). 오른쪽 위가 기획안으로 바뀌고 오른쪽 열이 넓어진다 */
  proposalPhase: boolean;
  summary: boolean;
  voice: boolean;
} & Record<RightPane, boolean>;

export type StageInput = {
  snap: StateSnapshot;
  /** 지원자가 보낸 메시지가 있음(아직 ack 전인 것 포함) */
  candidateSpoke: boolean;
  /** 인사 차례를 화면에서 끝냄(바로 대화하기·대기 시간 경과) */
  introDone: boolean;
  /**
   * 세션 시작 인사(kind greeting)를 끝까지 다 들었는지. 주면 이것으로 인사 차례 끝을 정하고, 없으면(인사가 없는
   * 옛 채용 건·글 토론) AI 동료 셋이 한 번씩 말했는지로 정한다
   */
  greetingDone?: boolean;
  ended: boolean;
  /** 말하기(음성 입력)를 쓸 수 있음. 왼쪽 음성 패널을 둔다 */
  voice?: boolean;
  /** 사용자가 상단 바에서 직접 켜거나 끈 영역. 기획안 단계의 기획안은 항상 보인다 */
  pinned?: Partial<Record<RightPane, boolean>>;
};

const SEATS: Seat[] = ["p1", "p2", "p3"];

export function stageFlags({
  snap,
  candidateSpoke,
  introDone,
  greetingDone,
  ended,
  voice = false,
  pinned = {},
}: StageInput): StageFlags {
  const phase = snap.session.phase;
  const confirmed = snap.slots.some((s) => s.status === "confirmed");
  // 인사를 다 들었거나(음성), 인사가 없으면 AI 동료 세 명이 한 번씩 말했을 때 인사 차례가 끝난다
  const greeted =
    greetingDone ?? SEATS.every((seat) => snap.messages.some((m) => m.sender === seat));
  const intro =
    !ended &&
    !introDone &&
    !candidateSpoke &&
    !confirmed &&
    !greeted &&
    (phase === null || phase === "keyword");
  const proposalPhase = phase === "proposal" || ended;
  const auto: Record<RightPane, boolean> = {
    // 토론방이 열릴 때 같이 생긴다. 기획안 단계에는 정한 것이 왼쪽 단계 안내와 기획안 앞 칸에 있어 물러난다
    decisions: !proposalPhase,
    proposal: proposalPhase,
    // 자료가 열리거나 어시스턴트를 쓸 일이 생기면
    library:
      !snap.materials.locked || snap.assistant.length > 0 || phase === "solution" || proposalPhase,
  };
  const show = (pane: RightPane) =>
    !intro && (pane === "proposal" && proposalPhase ? true : (pinned[pane] ?? auto[pane]));
  return {
    intro,
    proposalPhase: !intro && proposalPhase,
    // 진행 상황은 크기를 고정해 두므로 토론방이 열리면 늘 보인다(한 단계가 끝나기 전에는 지금 단계 안내만)
    summary: !intro,
    voice: !intro && voice,
    decisions: show("decisions"),
    proposal: show("proposal"),
    library: show("library"),
  };
}

/** 칸 구성이 크게 바뀌는 구간. 이 값이 바뀌면 사용자가 끈 크기를 버리고 기본 비율로 돌아간다 */
export function stageEpoch(flags: StageFlags) {
  if (flags.intro) return "intro";
  return flags.proposalPhase ? "proposal" : "discussion";
}

export function stageWeights(flags: StageFlags): Weights {
  if (flags.intro) return { left: 1, profiles: 1 };
  const p = flags.proposalPhase || flags.proposal;
  // 크기 우선순위: 고정(메모·진행 상황, stageModes) → 가운데 말하기·최근 발언(가장 넓게, 늘 같은 비율)
  // → 오른쪽 하는 일(결정 사항 내용 높이·기획안) → 자료·AI 어시스턴트. 오른쪽 칸을 끄면 그만큼 가운데가 넓어진다
  const count = [flags.decisions, flags.proposal, flags.library].filter(Boolean).length;
  return {
    left: 1,
    // 음성 토론은 4명 프로필을 가운데 말하기 칸에 크게 두므로 인사 뒤 왼쪽 프로필 칸은 숨긴다
    profiles: flags.voice ? 0 : 1,
    voice: flags.voice ? 1 : 0,
    summary: flags.summary ? 1 : 0,
    center: p ? 2 : 2.4,
    // 가운데 안: 최근 발언이 가장 넓고, 프로필 줄은 낮게, 조작부는 내용 높이만큼(stageModes)
    people: flags.voice ? 0.7 : 0,
    talk: flags.voice ? 2 : 0,
    controls: flags.voice ? 1 : 0,
    chat: flags.voice ? 0 : 1,
    right: count === 0 ? 0 : p ? 2.2 : count === 1 ? 1.2 : 1.7,
    // 결정 사항은 내용 높이만큼(stageModes), 비율은 보이기 여부와 끌기 시작점에만 쓴다
    decisions: flags.decisions ? 1 : 0,
    proposal: flags.proposal ? 1.5 : 0,
    library: flags.library ? 1 : 0,
  };
}

/** 왼쪽 열(메모·진행 상황)의 처음 폭과 진행 상황의 처음 높이(px). 경계를 끌어 바꿀 수 있다 */
export const LEFT_PX = 272;
export const SUMMARY_PX = 210;

/**
 * 인사 화면이 아니면 왼쪽 열 폭과 진행 상황 높이를 처음 크기로 두고(경계를 끌어 메모 칸을 넓히거나 줄일 수 있음),
 * 프로필은 왼쪽 열 폭만큼의 정사각형, 결정 사항·조작부는 내용 높이만큼
 */
export function stageModes(flags: StageFlags): PaneModes {
  return flags.intro
    ? {}
    : {
        left: { px: LEFT_PX },
        summary: { px: SUMMARY_PX },
        profiles: "square",
        decisions: "fit",
        controls: "fit",
      };
}
