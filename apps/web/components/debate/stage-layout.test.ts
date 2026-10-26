import { expect, test } from "vitest";

import keyword from "@/mocks/fixtures/state_keyword.json";
import proposal from "@/mocks/fixtures/state_proposal.json";

import {
  LEFT_PX,
  SUMMARY_PX,
  stageEpoch,
  stageFlags,
  stageModes,
  stageWeights,
  type StageInput,
} from "./stage-layout";
import type { StateSnapshot } from "./types";

const fresh = (): StateSnapshot => {
  const snap = structuredClone(keyword) as StateSnapshot;
  snap.messages = [];
  snap.slots = snap.slots.map((s) => ({ ...s, status: "empty", value: null, resolution: null }));
  snap.materials = { ...snap.materials, locked: true };
  snap.assistant = [];
  return snap;
};
const input = (over: Partial<StageInput> = {}): StageInput => ({
  snap: fresh(),
  candidateSpoke: false,
  introDone: false,
  ended: false,
  ...over,
});

test("세션 첫 화면은 프로필 카드만 크게 보이고, 지원자 차례가 오면 토론방이 결정 사항과 함께 열린다", () => {
  const intro = stageFlags(input({ voice: true }));
  expect(intro).toMatchObject({ intro: true, voice: false, decisions: false });
  expect(stageWeights(intro)).toEqual({ left: 1, profiles: 1 });
  expect(stageModes(intro)).toEqual({});

  const talk = stageFlags(input({ introDone: true, voice: true }));
  expect(talk).toMatchObject({
    intro: false,
    decisions: true,
    library: false,
    voice: true,
    summary: true,
  });
  const w = stageWeights(talk);
  expect(w.center).toBeGreaterThan(w.right);
  // 음성 토론의 가운데는 프로필 줄·최근 발언·조작부 세 칸이고 최근 발언이 가장 넓다
  expect(w.talk).toBeGreaterThan(w.people);
  expect(w.chat).toBe(0);
  // 메모·진행 상황(왼쪽 열)은 크기를 고정하고, 결정 사항은 내용 높이만큼
  expect(stageModes(talk)).toEqual({
    left: { px: LEFT_PX },
    summary: { px: SUMMARY_PX },
    profiles: "square",
    decisions: "fit",
    controls: "fit",
  });
  // 오른쪽 칸을 모두 끄면 오른쪽 열이 사라지고 가운데가 그 자리를 쓴다(가운데 비율은 그대로)
  const empty = stageWeights(stageFlags(input({ introDone: true, pinned: { decisions: false } })));
  expect(empty.decisions).toBe(0);
  expect(empty.right).toBe(0);
  expect(empty.center).toBe(w.center);
});

test("자료가 열리면 결정 사항 아래에 자료·AI 어시스턴트 묶음이 생긴다", () => {
  const snap = fresh();
  snap.materials = { ...snap.materials, locked: false };
  const flags = stageFlags(input({ snap, candidateSpoke: true }));
  expect(flags).toMatchObject({ decisions: true, library: true, proposal: false });
  expect(stageWeights(flags).library).toBeGreaterThan(0);
});

test("기획안 단계는 오른쪽 위가 기획안으로 바뀌고 단계 안내가 왼쪽 아래에 남는다", () => {
  const flags = stageFlags(input({ snap: proposal as StateSnapshot, pinned: { proposal: false } }));
  expect(flags).toMatchObject({
    intro: false,
    proposalPhase: true,
    summary: true,
    decisions: false,
    proposal: true,
    library: true,
  });
  expect(stageEpoch(flags)).toBe("proposal");
  const w = stageWeights(flags);
  expect(w.proposal).toBeGreaterThan(w.library);
});

test("상단 바에서 직접 켜거나 끈 영역은 자동 판단보다 앞서고, 인사 화면에서는 무시된다", () => {
  expect(stageFlags(input({ introDone: true, pinned: { library: true } })).library).toBe(true);
  const snap = fresh();
  snap.materials = { ...snap.materials, locked: false };
  expect(stageFlags(input({ snap, introDone: true, pinned: { library: false } })).library).toBe(
    false,
  );
  expect(stageFlags(input({ pinned: { decisions: true } })).decisions).toBe(false);
});

test("인사를 끝까지 다 듣기 전에는 동료 셋이 말했어도 인사 화면이 이어진다", () => {
  const spoke = input({}).snap;
  const said = {
    ...spoke,
    messages: (["p1", "p2", "p3"] as const).map((sender, i) => ({
      id: `g${i}`,
      sender,
      content: "안녕하세요",
      phase: "keyword" as const,
      is_invite: false,
      t_min: 0,
      created_at: "",
      kind: "greeting" as const,
    })),
  };
  expect(stageFlags(input({ snap: said, greetingDone: false })).intro).toBe(true);
  expect(stageFlags(input({ snap: said, greetingDone: true })).intro).toBe(false);
  // 인사가 없는 옛 채용 건은 동료 셋이 한 번씩 말하면 끝난다
  expect(stageFlags(input({ snap: said })).intro).toBe(false);
});
