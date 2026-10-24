import { expect, test } from "vitest";

import keyword from "@/mocks/fixtures/state_keyword.json";
import proposal from "@/mocks/fixtures/state_proposal.json";

import { stageEpoch, stageFlags, stageModes, stageWeights, type StageInput } from "./stage-layout";
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
  expect(talk).toMatchObject({ intro: false, decisions: true, library: false, voice: true });
  const w = stageWeights(talk);
  expect(w.chat).toBeGreaterThan(w.right);
  expect(w.right).toBeGreaterThan(w.left);
  // 프로필은 왼쪽 열 폭만큼의 정사각형, 결정 사항은 내용 높이만큼
  expect(stageModes(talk)).toEqual({ profiles: "square", decisions: "fit" });
  // 지원자가 결정 사항을 끄면 오른쪽이 비지만 왼쪽과 같은 폭을 남겨 토론방이 가운데에 온다
  const empty = stageWeights(stageFlags(input({ introDone: true, pinned: { decisions: false } })));
  expect(empty.decisions).toBe(0);
  expect(empty.right).toBe(empty.left);
});

test("자료가 열리면 결정 사항 아래에 자료·AI 어시스턴트 묶음이 생긴다", () => {
  const snap = fresh();
  snap.materials = { ...snap.materials, locked: false };
  const flags = stageFlags(input({ snap, candidateSpoke: true }));
  expect(flags).toMatchObject({ decisions: true, library: true, proposal: false });
  expect(stageWeights(flags).library).toBeGreaterThan(0);
});

test("기획안 단계는 오른쪽 위가 기획안으로 바뀌고 단계 안내가 왼쪽 아래에 남는다", () => {
  const flags = stageFlags(input({ snap: proposal as StateSnapshot }));
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
