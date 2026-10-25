// 지원자 mock 서버(NEXT_PUBLIC_MOCK=1). 경로 표(ROUTES)와 메모리 세션, MockSocket, 15분 데모 재생, 조작 패널용 mockControls().
// 형태는 전부 docs/contracts/README.md와 같고, 상태는 화면과 같은 applyFrame으로 굴려 /state 재조회가 화면과 맞는다.
// 데모 대본은 fixtures/debate_script.json(계약 ws_events를 15분 비율로 줄인 것, README 참고).
import { CANDIDATE_MESSAGE_MAX_CHARS, applyFrame, clock } from "@/components/debate/state";
import type {
  Debrief,
  EndReason,
  Entry,
  Material,
  Message,
  Phase,
  Seat,
  ServerFrame,
  ServerPayloads,
  SlotPhase,
  StateSnapshot,
} from "@/components/debate/types";
import type { SocketLike } from "@/lib/ws";

import assistantFixture from "./fixtures/assistant_response.json";
import scriptJson from "./fixtures/debate_script.json";
import debriefFixture from "./fixtures/debrief.json";
import debriefFailedFixture from "./fixtures/debrief_failed.json";
import entryFixture from "./fixtures/entry.json";
import entryCompletedFixture from "./fixtures/entry_completed.json";
import preStartFixture from "./fixtures/state_pre_start.json";
import keywordFixture from "./fixtures/state_keyword.json";
import problemFixture from "./fixtures/state_problem.json";
import proposalFixture from "./fixtures/state_proposal.json";
import solutionFixture from "./fixtures/state_solution.json";
import type { MockRoute } from "./types";
import { LONG_ALIAS, LONG_MESSAGE, WORST } from "./worst";

export class MockApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message = code,
  ) {
    super(message);
  }
}

// ── 대본 ─────────────────────────────────────────────────────

type ScriptFrame = {
  phase: Phase;
  t_min: number;
  anchor?: "deadline";
  type: ServerFrame["type"];
  seq: number;
  payload: Record<string, unknown>;
};
type Keyword = {
  key: string;
  label: string;
  default_problem: string;
  default_solution: string;
  materials: Material[];
};
type Script = {
  session_minutes: number;
  phase_deadlines: Record<Phase, number>;
  stage_start: Record<Phase, string>;
  fixed_phrases: { consensus_fill: string; timeout_fill: Record<string, string> };
  keywords: Keyword[];
  frames: ScriptFrame[];
};
export const SCRIPT = scriptJson as unknown as Script;

/** 키워드 후보 4개. State 계약에 후보 목록이 없어 web 전용 대본에서 읽는다(README "열린 문제") */
export const KEYWORD_OPTIONS = SCRIPT.keywords.map(({ key, label }) => ({ key, label }));

const ALL_MATERIALS = SCRIPT.keywords.flatMap((k) => k.materials);

const NEXT: Record<Phase, Phase | null> = {
  keyword: "problem",
  problem: "solution",
  solution: "proposal",
  proposal: null,
};

/** 대본 단계별 시작 t_min(그 단계로 바꾸는 phase.changed의 t_min) */
const SCRIPT_START: Record<Phase, number> = { keyword: 0, problem: 0, solution: 0, proposal: 0 };
for (const f of SCRIPT.frames)
  if (f.type === "phase.changed") SCRIPT_START[f.payload.phase as Phase] = f.t_min;

// ── 메모리 세션 ──────────────────────────────────────────────

type Mode = "demo" | "snapshot" | "completed" | "failed";

type Server = {
  token: string;
  mode: Mode;
  entry: Entry;
  snap: StateSnapshot;
  displayName: string;
  /** 모의 시계(분) = clockOffset + 실제 경과 × speed */
  clockBase: number;
  clockOffset: number;
  speed: number;
  startedAtMs: number;
  frames: ScriptFrame[];
  phaseBase: Partial<Record<Phase, number>>;
  timer: ReturnType<typeof setInterval> | null;
  lastTimerAt: number;
  socket: MockSocket | null;
  disconnectedAt: number | null;
  dropNext: boolean;
  assistantCalls: number;
  assistantLimit: number;
  surveySubmitted: boolean;
  endReason: EndReason | null;
  msgNo: number;
  forceConflict: boolean;
};

let server: Server | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((fn) => fn());

/** 계약의 /assets/missions/<id>/avatars/<file>을 mock 정적 파일로 바꾼다 */
const AVATAR = (url?: string) =>
  url?.replace(/^\/assets\/missions\/[^/]+\/avatars\//, "/mock-avatars/") ?? url;

function prepare(fixture: unknown): StateSnapshot {
  const snap = structuredClone(fixture) as StateSnapshot;
  snap.participants = snap.participants.map((p) => ({ ...p, avatar_url: AVATAR(p.avatar_url) }));
  if (WORST) {
    // break-ui: 긴 표시 이름, 깨진 avatar_url, 300자 메시지, 확정 칸 전부 시간 초과
    snap.participants = snap.participants.map((p) =>
      p.id === "p1"
        ? { ...p, display_name: "강한결 Kowalczyk-Wiśniewska" }
        : p.id === "p2"
          ? { ...p, avatar_url: "/mock-avatars/missing.png" }
          : p,
    );
    snap.slots = snap.slots.map((s) =>
      s.status === "confirmed"
        ? { ...s, resolution: "timeout", outcome: "timeout", filled_by: "system" }
        : s,
    );
    if (snap.messages.length) {
      const last = snap.messages[snap.messages.length - 1];
      snap.messages = [
        ...snap.messages,
        { ...last, id: "m-long-1", sender: "p1", content: LONG_MESSAGE, is_invite: false },
        { ...last, id: "m-long-2", sender: "candidate", content: LONG_MESSAGE, is_invite: false },
      ];
    }
  }
  return snap;
}

const SNAPSHOT_TOKENS: Record<string, unknown> = {
  keyword: keywordFixture,
  problem: problemFixture,
  solution: solutionFixture,
  proposal: proposalFixture,
};

function readSpeed() {
  if (typeof window === "undefined") return 1;
  const value = Number(new URLSearchParams(window.location.search).get("speed"));
  return Number.isFinite(value) && value > 0 ? value : 1;
}

function create(token: string): Server {
  const entry = structuredClone(entryFixture) as Entry;
  let mode: Mode = "demo";
  let snap = prepare(preStartFixture);
  if (token in SNAPSHOT_TOKENS) {
    mode = "snapshot";
    snap = prepare(SNAPSHOT_TOKENS[token]);
    entry.status = "in_progress";
  } else if (token === "done") {
    mode = "completed";
    Object.assign(entry, structuredClone(entryCompletedFixture));
    snap = prepare(proposalFixture);
    snap.session.status = "completed";
  } else if (token === "failed") {
    mode = "failed";
    Object.assign(entry, {
      status: "failed",
      failed_stage: "session",
      debrief_token: "dbt-sess-1",
    });
  }
  if (mode === "demo") {
    // 데모는 15분 세션(R3). 단계 예산·마감도 같은 비율
    entry.duration_minutes = SCRIPT.session_minutes;
    snap.session.session_minutes = SCRIPT.session_minutes;
    snap.phases = snap.phases.map((p) => ({
      ...p,
      budget_min: (p.budget_min * SCRIPT.session_minutes) / 60,
      deadline_t_min: SCRIPT.phase_deadlines[p.phase],
    }));
  }
  const s: Server = {
    token,
    mode,
    entry,
    snap,
    displayName: snap.participants[0]?.display_name ?? entry.default_display_name,
    clockBase: Date.now(),
    clockOffset: 0,
    speed: readSpeed(),
    startedAtMs: Date.now(),
    frames: [],
    phaseBase: {},
    timer: null,
    lastTimerAt: 0,
    socket: null,
    disconnectedAt: null,
    dropNext: false,
    assistantCalls: snap.assistant.length,
    assistantLimit: 60,
    surveySubmitted: false,
    endReason: null,
    msgNo: 0,
    forceConflict: false,
  };
  if (mode === "snapshot") {
    // 스냅샷 시각에서 시계를 이어 간다: 현재 단계 마감 - 남은 시간
    const phase = snap.session.phase as Phase;
    const deadline = snap.phases.find((p) => p.phase === phase)?.deadline_t_min ?? 0;
    s.clockOffset = deadline - (snap.session.phase_remaining_seconds ?? 0) / 60;
    s.startedAtMs = Date.now() - s.clockOffset * 60_000;
    startTicking(s);
  }
  return s;
}

function serverFor(token: string) {
  if (!server || server.token !== token) {
    if (server?.timer) clearInterval(server.timer);
    server = create(token);
  }
  return server;
}

function bySession(id: string) {
  if (!server || server.snap.session.id !== id) throw new MockApiError(404, "not_found");
  return server;
}

const now = (s: Server) => s.clockOffset + ((Date.now() - s.clockBase) / 60_000) * s.speed;
const iso = (s: Server, t: number) => new Date(s.startedAtMs + t * 60_000).toISOString();
const minutes = (s: Server) => s.snap.session.session_minutes;
const deadlineOf = (s: Server, phase: Phase) =>
  s.snap.phases.find((p) => p.phase === phase)?.deadline_t_min ?? minutes(s);
const round = (t: number) => Math.round(t * 100) / 100;

function remainingText(seconds: number) {
  const m = Math.floor(seconds / 60);
  const sec = Math.round(seconds % 60);
  if (m === 0) return `${sec}초`;
  return sec >= 10 ? `${m}분 ${sec}초` : `${m}분`;
}

/** 시계 값(남은 초)을 지금 기준으로 채운 스냅샷 */
function liveSnapshot(s: Server): StateSnapshot {
  const snap = structuredClone(s.snap);
  if (snap.session.status === "in_progress" && snap.session.phase) {
    const t = now(s);
    snap.session.phase_remaining_seconds = Math.max(
      0,
      Math.round((deadlineOf(s, snap.session.phase) - t) * 60),
    );
    snap.session.session_remaining_seconds = Math.max(0, Math.round((minutes(s) - t) * 60));
  }
  return snap;
}

// ── 프레임 송신 ──────────────────────────────────────────────

function emit<K extends ServerFrame["type"]>(s: Server, type: K, payload: ServerPayloads[K]) {
  const frame = { type, seq: s.snap.ws_seq + 1, payload } as ServerFrame;
  s.snap = applyFrame(s.snap, frame);
  const drop = s.dropNext;
  s.dropNext = false;
  // 끊긴 동안·누락 시험 중이면 상태에만 반영하고 보내지 않는다(복귀 시 /state로 따라잡음)
  if (!drop) s.socket?.deliver(frame);
  notify();
}

function timerPayload(s: Server, phase: Phase, t: number): ServerPayloads["timer"] {
  return {
    phase,
    phase_deadline_at: iso(s, deadlineOf(s, phase)),
    phase_remaining_seconds: Math.max(0, Math.round((deadlineOf(s, phase) - t) * 60)),
    session_remaining_seconds: Math.max(0, Math.round((minutes(s) - t) * 60)),
  };
}

function message(s: Server, sender: Message["sender"], content: string) {
  const t = round(now(s));
  s.msgNo += 1;
  emit(s, "message.new", {
    id: `m-x${s.msgNo}`,
    sender,
    content,
    phase: s.snap.session.phase ?? "keyword",
    is_invite: false,
    t_min: t,
    created_at: iso(s, t),
  });
}

/** 기획안 단계 시작 때 앞 3칸이 비어 있으면 확정 칸 값으로 채운다(화면은 /state 재조회로 받는다) */
function prefillProposal(s: Server) {
  const sections = { ...s.snap.proposal.sections };
  for (const slot of s.snap.slots)
    if (!sections[slot.phase] && slot.value) sections[slot.phase] = slot.value;
  s.snap = {
    ...s.snap,
    proposal: {
      ...s.snap.proposal,
      sections,
      revision: s.snap.proposal.revision + 1,
      updated_at: iso(s, now(s)),
    },
  };
}

/** 단계 전환 공통 프레임: phase.changed → timer → (keyword였으면) materials.opened → 시스템 안내 */
function advance(s: Server, from: Phase, reason: "confirmed" | "timeout", keyword?: string) {
  const to = NEXT[from];
  const t = round(now(s));
  if (!to) return;
  emit(s, "phase.changed", {
    phase: to,
    previous_phase: from,
    reason,
    phase_started_at: iso(s, t),
    phase_deadline_at: iso(s, deadlineOf(s, to)),
    t_min: t,
  });
  emit(s, "timer", timerPayload(s, to, t));
  const kw = SCRIPT.keywords.find((k) => k.label === keyword);
  if (from === "keyword" && kw) {
    emit(s, "materials.opened", {
      keyword: kw.label,
      materials: WORST
        ? []
        : kw.materials.map(({ material_id, title }) => ({ material_id, title })),
    });
  }
  if (to === "proposal") prefillProposal(s);
  message(
    s,
    "system",
    SCRIPT.stage_start[to]
      .replace("{remaining}", remainingText((deadlineOf(s, to) - t) * 60))
      .replace("{keyword}", s.snap.slots[0]?.value ?? ""),
  );
  // 대본: 지난 단계 프레임은 버리고, 새 단계의 전환 프레임(방금 만든 것)은 건너뛴다
  s.phaseBase[to] = t;
  s.frames = s.frames.filter(
    (f) =>
      f.phase !== from &&
      !(
        f.phase === to &&
        f.t_min === SCRIPT_START[to] &&
        (f.type === "timer" ||
          f.type === "materials.opened" ||
          (f.type === "message.new" && f.payload.sender === "system"))
      ),
  );
}

function confirmSlot(
  s: Server,
  phase: SlotPhase,
  value: string,
  filledBy: "candidate" | Seat | "system",
  resolution: "consensus" | "timeout",
) {
  const t = round(now(s));
  const outcome =
    resolution === "timeout"
      ? "timeout"
      : filledBy === "candidate"
        ? "candidate_decision"
        : "consensus";
  emit(s, "slot.confirmed", { phase, value, filled_by: filledBy, resolution, outcome, t_min: t });
  advance(s, phase, resolution === "timeout" ? "timeout" : "confirmed", value);
}

function end(s: Server, reason: EndReason) {
  if (s.endReason) return;
  s.endReason = reason;
  if (reason === "submitted" || reason === "timeout") {
    s.snap = {
      ...s.snap,
      session: { ...s.snap.session, completed_at: iso(s, now(s)) },
      proposal: { ...s.snap.proposal, submitted_at: iso(s, now(s)) },
    };
  }
  s.frames = [];
  emit(s, "session.ended", { reason });
  if (s.timer) clearInterval(s.timer);
  s.timer = null;
}

// ── 데모 재생·시계 ───────────────────────────────────────────

function dueOf(s: Server, f: ScriptFrame) {
  if (f.anchor === "deadline") return f.t_min;
  const base = s.phaseBase[f.phase];
  return base === undefined ? Infinity : base + (f.t_min - SCRIPT_START[f.phase]);
}

/** 대본 프레임 하나를 지금 시각으로 다시 찍어 보낸다 */
function play(s: Server, f: ScriptFrame) {
  const t = round(now(s));
  const p = structuredClone(f.payload) as Record<string, unknown>;
  switch (f.type) {
    case "message.new": {
      const msg = p as unknown as Message;
      emit(s, "message.new", {
        ...msg,
        // 대본의 지원자 이름(김지원)은 동의 때 정한 표시 이름으로 부른다
        content:
          msg.sender === "candidate" && WORST
            ? LONG_MESSAGE
            : msg.content.replace("김지원", s.displayName),
        phase: s.snap.session.phase ?? msg.phase,
        t_min: t,
        created_at: iso(s, t),
      });
      return;
    }
    case "slot.confirmed":
      emit(s, "slot.confirmed", { ...(p as ServerPayloads["slot.confirmed"]), t_min: t });
      return;
    case "phase.changed": {
      const to = p.phase as Phase;
      s.phaseBase[to] = t;
      emit(s, "phase.changed", {
        ...(p as ServerPayloads["phase.changed"]),
        phase_started_at: iso(s, t),
        phase_deadline_at: iso(s, deadlineOf(s, to)),
        t_min: t,
      });
      if (to === "proposal") prefillProposal(s);
      return;
    }
    case "timer":
      emit(s, "timer", timerPayload(s, p.phase as Phase, t));
      return;
    case "materials.opened":
      emit(s, "materials.opened", {
        ...(p as ServerPayloads["materials.opened"]),
        ...(WORST ? { materials: [] } : {}),
      });
      return;
    case "session.ended":
      end(s, (p as ServerPayloads["session.ended"]).reason);
      return;
    default:
      emit(s, f.type, p as never);
  }
}

function tick(s: Server) {
  if (s.endReason || s.snap.session.status !== "in_progress") return;
  const t = now(s);
  if (s.mode === "demo") {
    // 진행 중 단계의 프레임 중 시각이 된 것을 순서대로. 단계가 바뀌면 지난 단계 프레임은 버린다
    for (let guard = 0; guard < 200; guard += 1) {
      // 시작 직후(phase null)는 대본의 시작 프레임(keyword)부터
      const phase = s.snap.session.phase ?? "keyword";
      const index = s.frames.findIndex((f) => f.phase === phase && dueOf(s, f) <= t);
      if (index < 0) break;
      const [f] = s.frames.splice(index, 1);
      play(s, f);
      if (s.endReason) return;
      if (s.snap.session.phase !== phase) s.frames = s.frames.filter((x) => x.phase !== phase);
    }
  } else {
    // 대본 없는 세션: 마감이 지나면 api처럼 시간 초과로 채우고, 끝 시각에 자동 제출
    const phase = s.snap.session.phase;
    if (phase && phase !== "proposal" && t >= deadlineOf(s, phase)) timeoutFill(s, phase);
    else if (phase === "proposal" && t >= minutes(s)) end(s, "timeout");
    if (s.endReason) return;
  }
  // 시계 보정 timer: 60초마다, 빠르게 돌리면 실제 1초마다
  const every = s.speed > 1 ? 1000 : 60_000;
  if (s.snap.session.phase && Date.now() - s.lastTimerAt >= every) {
    s.lastTimerAt = Date.now();
    emit(s, "timer", timerPayload(s, s.snap.session.phase, t));
  }
}

function startTicking(s: Server) {
  if (s.timer) clearInterval(s.timer);
  s.lastTimerAt = Date.now();
  s.timer = setInterval(() => tick(s), 100);
}

/** 시간 초과·조작 패널로 채울 값: 키워드는 확정 키워드(없으면 첫 후보), 나머지는 계약 예시의 확정 값 */
function fillOption(s: Server, phase: SlotPhase) {
  const kw = SCRIPT.keywords.find((k) => k.label === s.snap.slots[0]?.value) ?? SCRIPT.keywords[0];
  if (phase === "keyword") return kw.label;
  const fromFixture = (proposalFixture as unknown as StateSnapshot).slots.find(
    (x) => x.phase === phase,
  )?.value;
  return fromFixture ?? kw.label;
}

/** 조사 로/으로: 받침이 없거나 ㄹ 받침이면 로(api scenario._ro와 같은 규칙) */
function ro(word: string) {
  const code = (word.at(-1)?.charCodeAt(0) ?? 0) - 0xac00;
  return code >= 0 && code <= 11171 && code % 28 !== 0 && code % 28 !== 8 ? "으로" : "로";
}

/** mock은 안·지지를 기록하지 않으므로 시간 초과는 늘 "나온 안 없음" 규칙(default)으로 단계 기준안을
 *  고른다: 키워드는 첫 후보(default_keyword 물), 문제 상황·해결책은 확정 키워드의 첫 안(P1·S1) */
function timeoutFill(s: Server, phase: SlotPhase) {
  const kw = SCRIPT.keywords.find((k) => k.label === s.snap.slots[0]?.value) ?? SCRIPT.keywords[0];
  const option =
    phase === "keyword" ? kw.label : phase === "problem" ? kw.default_problem : kw.default_solution;
  const text = SCRIPT.fixed_phrases.timeout_fill.default.replace("{option}로", option + ro(option));
  message(s, "system", text);
  confirmSlot(s, phase, option, "system", "timeout");
}

// ── MockSocket ───────────────────────────────────────────────

export class MockSocket implements SocketLike {
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  private closed = false;

  constructor(readonly url: string) {
    const s = server;
    setTimeout(() => {
      if (this.closed || !s) return;
      s.socket = this;
      if (s.disconnectedAt !== null) {
        // 끊긴 구간을 기록한다(평가하지 않음, 화면은 시스템 안내 한 줄)
        const from = s.disconnectedAt;
        const to = round(now(s));
        s.disconnectedAt = null;
        if (to > from)
          s.snap = {
            ...s.snap,
            connection_gaps: [...s.snap.connection_gaps, { from_t_min: from, to_t_min: to }],
          };
      }
      this.onopen?.();
      notify();
    }, 30);
  }

  deliver(frame: ServerFrame) {
    if (this.closed) return;
    const data = JSON.stringify(frame);
    setTimeout(() => this.onmessage?.({ data }), 0);
  }

  send(data: string) {
    const s = server;
    if (!s || this.closed) return;
    const frame = JSON.parse(data) as { type: string; payload: Record<string, string> };
    if (frame.type !== "message.send") return; // typing은 서버 메모리에만 두고 화면에는 보내지 않는다
    if (s.endReason) {
      this.deliver({
        type: "error",
        seq: s.snap.ws_seq,
        payload: { code: "session_ending", message: "제출된 세션이에요." },
      });
      return;
    }
    const content = String(frame.payload.content ?? "").trim();
    if (!content) return;
    if (content.length > CANDIDATE_MESSAGE_MAX_CHARS) {
      this.deliver({
        type: "error",
        seq: s.snap.ws_seq,
        payload: { code: "invalid_input", message: "too long" },
      });
      return;
    }
    s.msgNo += 1;
    const messageId = `m-c${s.msgNo}`;
    // 저장 직후 ack, 이어서 같은 메시지를 message.new로(의도 분류를 기다리지 않음)
    setTimeout(() => {
      emit(s, "message.ack", {
        client_msg_id: frame.payload.client_msg_id,
        message_id: messageId,
        run_id: `run-${messageId}`,
      });
      const t = round(now(s));
      emit(s, "message.new", {
        id: messageId,
        sender: "candidate",
        content,
        phase: s.snap.session.phase ?? "keyword",
        is_invite: false,
        t_min: t,
        created_at: iso(s, t),
      });
    }, 250);
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    if (server?.socket === this) server.socket = null;
  }

  /** 서버 쪽에서 끊는다(조작 패널) */
  drop(code: number) {
    if (this.closed) return;
    this.closed = true;
    if (server?.socket === this) server.socket = null;
    setTimeout(() => this.onclose?.({ code }), 0);
  }
}

export function createMockSocket(url: string): SocketLike {
  return new MockSocket(url);
}

// ── REST ─────────────────────────────────────────────────────

function entryOf(token: string): Entry {
  if (token === "expired") throw new MockApiError(410, "expired", "링크가 만료되었습니다.");
  if (token === "cancelled") throw new MockApiError(410, "cancelled", "취소된 평가입니다.");
  const known =
    token === "demo" || token === "done" || token === "failed" || token in SNAPSHOT_TOKENS;
  if (!known) throw new MockApiError(404, "not_found", "없는 링크입니다.");
  const s = serverFor(token);
  if (s.endReason === "failed")
    return { ...s.entry, status: "failed", debrief_token: "dbt-sess-1" };
  if (s.endReason === "cancelled") throw new MockApiError(410, "cancelled", "취소된 평가입니다.");
  if (s.endReason || s.snap.session.status === "completed")
    return { ...s.entry, status: "completed", debrief_token: "dbt-sess-1" };
  if (WORST && s.entry.status === "invited")
    return { ...s.entry, default_display_name: LONG_ALIAS };
  return s.entry;
}

function consent(token: string, body: unknown) {
  const s = serverFor(token);
  if (s.snap.session.status !== "invited") throw new MockApiError(409, "conflict");
  const raw = String((body as { display_name?: string })?.display_name ?? "")
    .replace(/\s+/g, " ")
    .trim();
  if (raw.length > 20) throw new MockApiError(422, "invalid_input", "표시 이름은 20자 이하예요.");
  s.displayName = raw || s.entry.default_display_name;
  s.snap.participants = s.snap.participants.map((p) =>
    p.id === "candidate" ? { ...p, display_name: s.displayName } : p,
  );
  return { session_token: "jwt-sess-1-a", session_id: s.snap.session.id };
}

function start(token: string) {
  const s = serverFor(token);
  if (s.snap.session.status === "invited") {
    s.clockBase = Date.now();
    s.clockOffset = 0;
    s.startedAtMs = Date.now();
    s.snap.session = {
      ...s.snap.session,
      status: "in_progress",
      started_at: iso(s, 0),
      ends_at: iso(s, minutes(s)),
    };
    s.entry.status = "in_progress";
    s.frames = structuredClone(SCRIPT.frames);
    s.phaseBase = { keyword: 0 };
    // 대본의 시작 프레임(phase.changed start·timer·첫 안내)은 화면이 WS를 연 뒤 첫 tick에 나간다
    startTicking(s);
  }
  return {
    session_id: s.snap.session.id,
    started_at: s.snap.session.started_at,
    ends_at: s.snap.session.ends_at,
    phase: "keyword",
  };
}

function confirm(id: string, phase: string, body: unknown) {
  const s = bySession(id);
  if (s.endReason) throw new MockApiError(409, "session_ending");
  const slot = s.snap.slots.find((x) => x.phase === phase);
  if (slot?.status === "confirmed") throw new MockApiError(409, "slot_confirmed");
  if (phase !== s.snap.session.phase || !slot) throw new MockApiError(409, "phase_not_open");
  const req = (body ?? {}) as { value?: string; option_key?: string };
  let value = String(req.value ?? "").trim();
  if (phase === "keyword") {
    const kw = SCRIPT.keywords.find((k) => k.key === req.option_key);
    if (!kw) throw new MockApiError(422, "invalid_keyword", "후보 4개 중에서 골라 주세요.");
    value = kw.label;
  } else if (!value) throw new MockApiError(422, "invalid_input");
  confirmSlot(s, phase as SlotPhase, value, "candidate", "consensus");
  return { slot: s.snap.slots.find((x) => x.phase === phase), phase: s.snap.session.phase };
}

function material(id: string, materialId: string) {
  const s = bySession(id);
  const listed = s.snap.materials.materials.some((m) => m.material_id === materialId);
  if (s.snap.materials.locked || !listed) throw new MockApiError(423, "not_released");
  const found = ALL_MATERIALS.find((m) => m.material_id === materialId);
  if (!found) throw new MockApiError(404, "not_found");
  return found;
}

function assistant(id: string, body: unknown) {
  const s = bySession(id);
  if (s.endReason) throw new MockApiError(409, "session_ending");
  if (s.assistantCalls >= s.assistantLimit) throw new MockApiError(429, "assistant_limit");
  const prompt = String((body as { prompt?: string })?.prompt ?? "").trim();
  if (!prompt) throw new MockApiError(422, "invalid_input");
  s.assistantCalls += 1;
  let content: string;
  let cited: string[] = [];
  if (s.snap.materials.locked) content = "키워드가 정해지면 자료를 찾을 수 있어요.";
  else if (prompt === "도심 침수 사례에서 센서 경보 효과가 있었던 곳을 알려 줘") {
    content = assistantFixture.content;
    cited = assistantFixture.cited_material_ids;
  } else {
    // 열린 자료 중 질문 낱말이 가장 많이 겹치는 자료의 첫 목록 줄을 인용한다
    const words = prompt.split(/\s+/).filter((w) => w.length >= 2);
    const best = s.snap.materials.materials
      .map((ref) => ALL_MATERIALS.find((m) => m.material_id === ref.material_id))
      .filter((m): m is Material => !!m)
      .map((m) => ({ m, score: words.filter((w) => m.content.includes(w.slice(0, 2))).length }))
      .sort((a, b) => b.score - a.score)[0]?.m;
    if (best) {
      const line =
        best.content
          .split("\n")
          .find((l) => l.startsWith("- "))
          ?.slice(2) ?? best.title;
      content = `「${best.title}」 자료에 이런 내용이 있어요. ${line}`;
      cited = [best.material_id];
    } else content = "이 키워드에는 준비된 자료가 없어요.";
  }
  const t = round(now(s));
  const callId = `ac-x${s.assistantCalls}`;
  s.snap = {
    ...s.snap,
    assistant: [
      ...s.snap.assistant,
      { call_id: callId, request: prompt, response: content, cited_material_ids: cited, t_min: t },
    ],
  };
  return {
    call_id: callId,
    message_id: `ma-x${s.assistantCalls}`,
    content,
    cited_material_ids: cited,
    tool_calls: cited.length
      ? [
          { name: "search_materials", query: prompt.slice(0, 30) },
          { name: "get_material", material_id: cited[0] },
        ]
      : [],
  };
}

function putProposal(id: string, body: unknown) {
  const s = bySession(id);
  if (s.endReason) throw new MockApiError(409, "session_ending");
  const req = body as { sections: StateSnapshot["proposal"]["sections"]; revision: number };
  if (s.forceConflict) {
    // 조작 패널 "다른 창 저장": 서버 쪽 revision이 먼저 올라가 이번 저장이 409가 된다
    s.forceConflict = false;
    s.snap.proposal = { ...s.snap.proposal, revision: s.snap.proposal.revision + 1 };
  }
  if (req.revision !== s.snap.proposal.revision) throw new MockApiError(409, "conflict");
  const updated_at = iso(s, now(s));
  s.snap.proposal = {
    ...s.snap.proposal,
    sections: req.sections,
    revision: req.revision + 1,
    updated_at,
  };
  return { revision: s.snap.proposal.revision, updated_at };
}

function submit(id: string) {
  const s = bySession(id);
  const done = s.endReason === "submitted" || s.endReason === "timeout";
  if (!done) {
    if (s.snap.session.phase !== "proposal") throw new MockApiError(409, "phase_not_open");
    end(s, "submitted");
  }
  return {
    status: "completed",
    completed_at: s.snap.session.completed_at,
    submitted_at: s.snap.proposal.submitted_at,
  };
}

function debrief(id: string): Debrief {
  const s = bySession(id);
  if (s.mode === "failed" || s.endReason === "failed")
    return {
      ...(structuredClone(debriefFailedFixture) as Debrief),
      survey: { ...debriefFailedFixture.survey, submitted: s.surveySubmitted } as Debrief["survey"],
    };
  const base = structuredClone(debriefFixture) as Debrief;
  // 내 발언 돌아보기: 토론 단계마다 가장 긴 지원자 메시지 하나(최대 3개)
  const mine = s.snap.messages.filter((m) => m.sender === "candidate");
  const longest = (["keyword", "problem", "solution"] as const)
    .map(
      (phase) =>
        mine
          .filter((m) => m.phase === phase)
          .sort((a, b) => b.content.length - a.content.length)[0],
    )
    .filter((m): m is Message => !!m)
    .map(({ id: mid, content, phase, t_min }) => ({ id: mid, content, phase, t_min }));
  const live = s.mode !== "completed";
  return {
    ...base,
    seats: base.seats?.map((seat) =>
      seat.seat === "candidate"
        ? { ...seat, display_name: s.displayName }
        : { ...seat, avatar_url: AVATAR(seat.avatar_url) },
    ),
    my_messages: live && longest.length ? longest : base.my_messages,
    proposal: live ? { sections: s.snap.proposal.sections } : base.proposal,
    connection_gaps: live ? s.snap.connection_gaps : base.connection_gaps,
    survey: { ...base.survey, submitted: s.surveySubmitted },
  };
}

export const ROUTES: MockRoute[] = [
  ["GET", "/s/:token", ({ token }) => entryOf(token)],
  ["POST", "/s/:token/consent", ({ token }, body) => consent(token, body)],
  [
    "POST",
    "/s/:token/resume",
    ({ token }) => {
      const s = serverFor(token);
      if (s.snap.session.status !== "in_progress") throw new MockApiError(409, "conflict");
      return { session_token: "jwt-sess-1-b", session_id: s.snap.session.id };
    },
  ],
  ["POST", "/s/:token/start", ({ token }) => start(token)],
  ["GET", "/sessions/:id/state", ({ id }) => liveSnapshot(bySession(id))],
  ["POST", "/sessions/:id/slots/:phase/confirm", ({ id, phase }, body) => confirm(id, phase, body)],
  ["GET", "/sessions/:id/materials", ({ id }) => bySession(id).snap.materials],
  ["GET", "/sessions/:id/materials/:mid", ({ id, mid }) => material(id, mid)],
  ["POST", "/sessions/:id/assistant", ({ id }, body) => assistant(id, body)],
  ["GET", "/sessions/:id/proposal", ({ id }) => bySession(id).snap.proposal],
  ["PUT", "/sessions/:id/proposal", ({ id }, body) => putProposal(id, body)],
  ["POST", "/sessions/:id/proposal/submit", ({ id }) => submit(id)],
  ["GET", "/sessions/:id/debrief", ({ id }) => debrief(id)],
  [
    "POST",
    "/sessions/:id/debrief/survey",
    ({ id }) => {
      bySession(id).surveySubmitted = true;
      return { submitted: true };
    },
  ],
];

// ── 조작 패널(mock-controls) ─────────────────────────────────

const ORDER: Phase[] = ["keyword", "problem", "solution", "proposal"];

/** 단계 건너뛰기: 그 단계가 시작될 때까지 대본을 바로 재생한다(시계도 대본 시각으로 옮긴다) */
function skipTo(s: Server, target: Phase) {
  for (let guard = 0; guard < 400; guard += 1) {
    const phase = s.snap.session.phase;
    if (!phase || s.endReason || ORDER.indexOf(phase) >= ORDER.indexOf(target)) break;
    const index = s.mode === "demo" ? s.frames.findIndex((f) => f.phase === phase) : -1;
    if (index < 0) {
      timeoutFill(s, phase as SlotPhase);
      continue;
    }
    const [f] = s.frames.splice(index, 1);
    const due = dueOf(s, f);
    if (Number.isFinite(due) && due > now(s)) s.clockOffset += due - now(s);
    play(s, f);
    if (s.snap.session.phase !== phase) s.frames = s.frames.filter((x) => x.phase !== phase);
  }
  tick(s);
}

export function mockControls() {
  const s = server;
  return {
    available: !!s,
    status: s?.snap.session.status ?? null,
    phase: s?.snap.session.phase ?? null,
    mode: s?.mode ?? null,
    speed: s?.speed ?? 1,
    connected: !!s?.socket,
    elapsed: s && s.snap.session.status === "in_progress" ? clock(now(s) * 60) : null,
    /** 세션 시작 뒤 모의 경과(분). 캡처 스크립트가 장면 시각을 기다릴 때 쓴다 */
    minutes: s && s.snap.session.status === "in_progress" ? now(s) : null,
    skipTo: (phase: Phase) => {
      if (s) skipTo(s, phase);
    },
    typing: (seat: Seat) => {
      if (s) emit(s, "typing", { participant: seat, state: "start" });
    },
    fill: (resolution: "consensus" | "timeout") => {
      const phase = s?.snap.session.phase;
      if (!s || !phase || phase === "proposal" || s.endReason) return;
      if (resolution === "timeout") return timeoutFill(s, phase);
      const option = fillOption(s, phase);
      message(s, "p3", SCRIPT.fixed_phrases.consensus_fill.replace("{option}", option));
      confirmSlot(s, phase, option, "p3", "consensus");
    },
    skipSeq: () => {
      if (!s || s.endReason) return;
      // 다음 프레임 하나를 상태에만 반영하고 보내지 않는다 → 화면이 seq 공백을 보고 /state 재조회
      s.dropNext = true;
      message(s, "system", "연결 상태를 다시 맞췄어요.");
      emit(s, "timer", timerPayload(s, s.snap.session.phase ?? "keyword", now(s)));
    },
    openMaterials: () => {
      if (!s || !s.snap.materials.locked || s.snap.session.phase !== "keyword") return;
      message(s, "p3", SCRIPT.fixed_phrases.consensus_fill.replace("{option}", "물"));
      confirmSlot(s, "keyword", "물", "p3", "consensus");
    },
    end: (reason: EndReason) => {
      if (s) end(s, reason);
    },
    disconnect: () => {
      if (!s?.socket) return;
      s.disconnectedAt = round(now(s));
      s.socket.drop(1006);
    },
    replace: () => {
      if (!s?.socket) return;
      const socket = s.socket;
      socket.deliver({ type: "session.replaced", seq: s.snap.ws_seq, payload: {} });
      setTimeout(() => socket.drop(4000), 50);
    },
    conflict: () => {
      if (s) s.forceConflict = true;
    },
    exhaustAssistant: () => {
      if (s) s.assistantLimit = s.assistantCalls;
    },
    setSpeed: (speed: number) => {
      if (!s) return;
      s.clockOffset = now(s);
      s.clockBase = Date.now();
      s.speed = speed;
      notify();
    },
  };
}

// 캡처 스크립트(e2e/capture-mock.mjs)가 page.evaluate로 조작 패널과 같은 함수를 부른다(mock 전용)
if (typeof window !== "undefined")
  (window as unknown as { __dayzeroMock?: typeof mockControls }).__dayzeroMock = mockControls;

export function subscribeMock(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** 테스트용: 메모리 세션을 비운다 */
export function resetCandidateMock() {
  if (server?.timer) clearInterval(server.timer);
  server = null;
}
