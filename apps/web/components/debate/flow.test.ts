// 완료 기준(mock): 15분 데모 한 세션이 계약 형태로 돈다. 입장 → 동의(표시 이름) → 시작 전 /state → 시작 →
// 대본 재생 → 키워드 정하기 → 자료 열림 → 기획안 단계(앞 3칸 채움) → 제출 → 디브리핑(성향 공개는 여기서만).
import { afterEach, expect, test, vi } from "vitest";

import { mockRequest } from "@/mocks";
import { mockControls, resetCandidateMock } from "@/mocks/debate";

import type { Debrief, Entry, StateSnapshot, TokenResponse } from "./types";

const TYPES = /stubborn|quiet|critic|agreeable|divergent|고집형|소극형|비판형/;

afterEach(() => {
  resetCandidateMock();
  vi.useRealTimers();
});

test("15분 데모: 표시 이름이 토론방에 쓰이고, 정하기·자료·기획안·제출·디브리핑이 계약대로 이어진다", async () => {
  vi.useFakeTimers();
  const responses: unknown[] = [];
  const call = async <T>(method: string, path: string, body?: unknown) => {
    const res = await mockRequest<T>(method, path, body);
    responses.push(res);
    return res;
  };

  const entry = await call<Entry>("GET", "/s/demo");
  expect(entry.status).toBe("invited");
  expect(entry.duration_minutes).toBe(15);
  const { session_id } = await call<TokenResponse>("POST", "/s/demo/consent", {
    display_name: "  박서연 ",
  });

  const pre = await call<StateSnapshot>("GET", `/sessions/${session_id}/state`);
  expect(pre.session.status).toBe("invited");
  expect(pre.participants.map((p) => p.display_name)).toEqual([
    "박서연",
    "강한결",
    "윤하람",
    "서지안",
  ]);
  // 프로필 이미지는 중립 파일 이름(유형이 URL에 나오지 않음)
  expect(pre.participants[1].avatar_url).toBe("/mock-avatars/avatar-01.png");

  await call("POST", "/s/demo/start");
  await vi.advanceTimersByTimeAsync(60_000); // 1분: 시작 안내·동료 발언·침묵 초대
  let state = await call<StateSnapshot>("GET", `/sessions/${session_id}/state`);
  expect(state.session.phase).toBe("keyword");
  expect(state.messages[0].sender).toBe("system");
  const invite = state.messages.find((m) => m.is_invite);
  expect(invite?.content).toBe("박서연님은 어떻게 생각하세요?");

  // 후보 밖 키는 422, 후보 키는 확정 → 문제 상황 단계·자료 열림
  await expect(
    mockRequest("POST", `/sessions/${session_id}/slots/keyword/confirm`, {
      value: "흙",
      option_key: "soil",
    }),
  ).rejects.toMatchObject({ status: 422, code: "invalid_keyword" });
  const confirmed = await call<{ phase: string }>(
    "POST",
    `/sessions/${session_id}/slots/keyword/confirm`,
    { value: "물", option_key: "water" },
  );
  expect(confirmed.phase).toBe("problem");
  state = await call<StateSnapshot>("GET", `/sessions/${session_id}/state`);
  expect(state.materials.locked).toBe(false);
  expect(state.materials.materials).toHaveLength(4);
  const doc = await call<{ content: string }>(
    "GET",
    `/sessions/${session_id}/materials/${state.materials.materials[1].material_id}`,
  );
  expect(doc.content).toContain("#");
  // 이미 정한 항목은 409
  await expect(
    mockRequest("POST", `/sessions/${session_id}/slots/keyword/confirm`, { option_key: "fire" }),
  ).rejects.toMatchObject({ status: 409 });

  // 기획안은 토론 단계에서 제출할 수 없다
  await expect(
    mockRequest("POST", `/sessions/${session_id}/proposal/submit`, {}),
  ).rejects.toMatchObject({
    status: 409,
    code: "phase_not_open",
  });

  // 대본으로 기획안 단계까지: 문제 상황 합의 채우기, 해결책 시간 초과(시스템 안내)
  mockControls().skipTo("proposal");
  state = await call<StateSnapshot>("GET", `/sessions/${session_id}/state`);
  expect(state.session.phase).toBe("proposal");
  expect(state.slots.map((s) => s.resolution)).toEqual(["consensus", "consensus", "timeout"]);
  expect(
    state.messages.some(
      (m) => m.sender === "system" && m.content.startsWith("시간 안에 정하지 못해"),
    ),
  ).toBe(true);
  expect(state.proposal.sections.keyword).toBe("물");
  expect(state.proposal.sections.problem).toBe("도심 폭우·침수 피해 증가");

  const put = await call<{ revision: number }>("PUT", `/sessions/${session_id}/proposal`, {
    sections: { ...state.proposal.sections, idea_credit: "문제 상황: 강한결 님 제안" },
    revision: state.proposal.revision,
  });
  await expect(
    mockRequest("PUT", `/sessions/${session_id}/proposal`, {
      sections: state.proposal.sections,
      revision: put.revision - 1,
    }),
  ).rejects.toMatchObject({ status: 409, code: "conflict" });
  await call("POST", `/sessions/${session_id}/proposal/submit`, {});

  // 종료 뒤 재입장은 디브리핑으로
  expect((await call<Entry>("GET", "/s/demo")).status).toBe("completed");

  // 디브리핑 전 응답 어디에도 페르소나 유형이 없다
  for (const res of responses) expect(JSON.stringify(res)).not.toMatch(TYPES);

  const debrief = await mockRequest<Debrief>("GET", `/sessions/${session_id}/debrief`);
  expect(debrief.seats?.find((s) => s.seat === "candidate")?.display_name).toBe("박서연");
  expect(debrief.seats?.find((s) => s.seat === "p1")?.persona_type).toBe("stubborn");
  expect(debrief.proposal?.sections.idea_credit).toBe("문제 상황: 강한결 님 제안");
  expect(debrief.my_messages?.length).toBeGreaterThanOrEqual(2);
  expect(debrief.my_messages!.length).toBeLessThanOrEqual(3);
  expect(debrief.survey.questions.map((q) => q.kind)).toEqual(["scale", "scale", "text"]);
});

test("링크 상태: 만료·취소 410, 모르는 토큰 404, 진행 중 스냅샷은 이어하기", async () => {
  await expect(mockRequest("GET", "/s/expired")).rejects.toMatchObject({
    status: 410,
    code: "expired",
  });
  await expect(mockRequest("GET", "/s/cancelled")).rejects.toMatchObject({
    status: 410,
    code: "cancelled",
  });
  await expect(mockRequest("GET", "/s/nope")).rejects.toMatchObject({ status: 404 });
  const entry = await mockRequest<Entry>("GET", "/s/solution");
  expect(entry.status).toBe("in_progress");
  const { session_id } = await mockRequest<TokenResponse>("POST", "/s/solution/resume");
  const state = await mockRequest<StateSnapshot>("GET", `/sessions/${session_id}/state`);
  expect(state.session.phase).toBe("solution");
  expect(state.typing).toEqual(["p1"]);
});

test("WS error 프레임: ack 못 받은 내 메시지는 실패로 표시하고, session_ending이면 입력을 잠근다", async () => {
  const { initialView, reduce } = await import("./reducer");
  const snap = JSON.parse(
    JSON.stringify((await import("@/mocks/fixtures/state_keyword.json")).default),
  );
  let view = initialView(snap, 0);
  view = reduce(view, { type: "send", pending: { clientMsgId: "c1", content: "안녕", tMin: 1 } });
  view = reduce(view, {
    type: "frame",
    now: 1,
    frame: { type: "error", seq: snap.ws_seq, payload: { code: "invalid_input", message: "x" } },
  });
  expect(view.pending[0].failed).toBe(true);
  expect(view.ended).toBeNull();
  view = reduce(view, {
    type: "frame",
    now: 2,
    frame: { type: "error", seq: snap.ws_seq, payload: { code: "session_ending", message: "x" } },
  });
  expect(view.ended).toBe("submitted");
});

test("WS error 프레임이 seq를 쓰면 마지막 seq를 올려 다음 프레임이 재조회 없이 적용된다", async () => {
  const { initialView, reduce } = await import("./reducer");
  const snap = JSON.parse(
    JSON.stringify((await import("@/mocks/fixtures/state_keyword.json")).default),
  );
  let view = initialView(snap, 0);
  const seq = snap.ws_seq;
  view = reduce(view, {
    type: "frame",
    now: 1,
    frame: { type: "error", seq: seq + 1, payload: { code: "invalid_input", message: "x" } },
  });
  expect(view.snap.ws_seq).toBe(seq + 1);
  view = reduce(view, {
    type: "frame",
    now: 2,
    frame: {
      type: "timer",
      seq: seq + 2,
      payload: {
        phase: "keyword",
        phase_deadline_at: "2026-01-01T00:00:00Z",
        phase_remaining_seconds: 5,
        session_remaining_seconds: 50,
      },
    },
  });
  expect(view.needsResync).toBe(false);
  expect(view.snap.ws_seq).toBe(seq + 2);
});
