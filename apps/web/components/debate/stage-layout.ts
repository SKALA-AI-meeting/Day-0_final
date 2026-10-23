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
    { kind: "pane", id: "chat", minPx: 360 },
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
  /** 인사 차례를 화면에서 끝냄(바로 대화하기·대기 시간 경과). TTS가 붙으면 인사 재생 끝이 이것을 세운다 */
  introDone: boolean;
  ended: boolean;
  /** 말하기(음성 입력)를 쓸 수 있음. 왼쪽 음성 패널을 둔다 */
  voice?: boolean;
  /** 사용자가 상단 바에서 직접 켜거나 끈 영역. 자동 판단보다 앞선다 */
  pinned?: Partial<Record<RightPane, boolean>>;
};

const SEATS: Seat[] = ["p1", "p2", "p3"];

export function stageFlags({
  snap,
  candidateSpoke,
  introDone,
  ended,
  voice = false,
  pinned = {},
}: StageInput): StageFlags {
  const phase = snap.session.phase;
  const confirmed = snap.slots.some((s) => s.status === "confirmed");
  // AI 동료 세 명이 한 번씩 말했으면 인사 차례가 끝난 것으로 본다
  const greeted = SEATS.every((seat) => snap.messages.some((m) => m.sender === seat));
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
  const show = (pane: RightPane) => !intro && (pinned[pane] ?? auto[pane]);
  return {
    intro,
    proposalPhase: !intro && proposalPhase,
    summary: !intro && confirmed,
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
  const anyRight = flags.decisions || flags.proposal || flags.library;
  return {
    left: 0.85,
    // 음성 토론은 4명 프로필을 가운데 말하기 칸에 크게 두므로 인사 뒤 왼쪽 프로필 칸은 숨긴다
    profiles: flags.voice ? 0 : 1,
    voice: flags.voice ? 1 : 0,
    summary: flags.summary ? 1 : 0,
    chat: p ? 2 : 2.4,
    // 오른쪽이 비어 있어도 같은 폭을 두어 토론방이 가운데에 오게 한다
    right: p ? 2.2 : anyRight ? 1.7 : 0.85,
    // 결정 사항은 내용 높이만큼(stageModes), 비율은 보이기 여부와 끌기 시작점에만 쓴다
    decisions: flags.decisions ? 1 : 0,
    proposal: flags.proposal ? 1.5 : 0,
    library: flags.library ? 1 : 0,
  };
}

/** 인사 화면이 아니면 프로필은 왼쪽 열 폭만큼의 정사각형, 결정 사항은 내용 높이만큼 */
export function stageModes(flags: StageFlags): PaneModes {
  return flags.intro ? {} : { profiles: "square", decisions: "fit" };
}
