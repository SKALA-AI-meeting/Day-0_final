// 관리자 화면 mock 서버. 오류 코드와 우선순위는 docs/contracts/api.md "관리자 REST"를 따르고 상태는 모듈 안에만 둔다.
//
// 데이터: 채용 건 pos-1(admin_positions.json), 지원자 sess-1~6(admin_candidates.json) + 만료된 초대 1행(sess-x1).
// 리포트는 sess-1(draft, report.json)과 sess-6(final, sample-report.json에 검토를 더함).
// 검토자 이름에 "지연"을 넣으면 확정이 끝나지 않아 "확정 다시 시도" 화면을 볼 수 있다(같은 본문으로 다시 보내면 끝난다).
import type {
  Adjustment,
  Candidate,
  Catalog,
  Position,
  RecordEntry,
  Report,
  ReviewBody,
  SessionMinutes,
} from "@/components/admin/types";
import sampleFixture from "@/fixtures/sample-report.json";

import candidatesFixture from "./fixtures/admin_candidates.json";
import catalogFixture from "./fixtures/admin_catalog.json";
import createdFixture from "./fixtures/admin_position_created.json";
import inviteFixture from "./fixtures/admin_invite.json";
import notesFixture from "./fixtures/admin_report_notes.json";
import positionsFixture from "./fixtures/admin_positions.json";
import recordFixture from "./fixtures/admin_record.json";
import reissueFixture from "./fixtures/admin_reissue.json";
import reportFixture from "./fixtures/report.json";
import type { MockRoute } from "./types";

class MockApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message?: string,
    /** 오류 객체의 추가 필드(failed_step 등) */
    readonly extra: Record<string, unknown> = {},
  ) {
    super(message ?? code);
  }
}

const catalog = catalogFixture as unknown as Catalog;
const INVITE_MS = 72 * 3_600_000;
// 확정 중(approving) 리포트가 final로 바뀌기까지 GET 횟수
const POLLS_UNTIL_FINAL = 2;
const REASONS = [
  "no_activity",
  "evaluate_failed",
  "not_applicable",
  "preempted",
  "event_not_fired",
  "auto_submitted_blank",
];

type ReportState = {
  report: Report;
  polls: number;
  submitted: string | null;
  stuck: boolean;
  /** 생성 실패 지점(실패 상태면). 재생성하면 비워지고 generating만큼 생성 중으로 응답한다 */
  failedStep?: "draft" | "finalize" | null;
  generating?: number;
};

type Store = {
  positions: Position[];
  candidates: Record<string, Candidate[]>;
  reports: Record<string, ReportState>;
  counter: number;
};

/** 검토가 끝난 상태의 리포트(sess-6): 샘플 리포트(조정 1건 포함)에 검토자·메모를 더한다 */
function reviewedReport(): Report {
  const report = structuredClone(sampleFixture) as unknown as Report;
  report.session_id = "sess-6";
  report.alias = "지원자E";
  report.position.name = positionsFixture[0].name;
  report.stage = "final";
  report.review = { reviewer_name: "김담당", reviewed_at: "2026-10-21T03:00:00Z" };
  report.reviewed_at = "2026-10-21T03:00:00Z";
  report.notes = structuredClone(notesFixture.notes);
  return report;
}

/** 시작 전 만료된 초대(링크 재발급 대상) */
const EXPIRED: Candidate = {
  session_id: "sess-x1",
  alias: "지원자F",
  email: "f@example.com",
  status: "invited",
  display_status: "expired",
  display_group: "waiting",
  phase: null,
  invited_at: "2026-10-17T00:10:00Z",
  message_count: 0,
  next_action: "링크 재발급",
  elements: null,
  low_confidence_count: null,
  level_source: null,
};

function fresh(): Store {
  return {
    positions: structuredClone(positionsFixture) as Position[],
    candidates: {
      "pos-1": [...(structuredClone(candidatesFixture) as Candidate[]), structuredClone(EXPIRED)],
    },
    reports: {
      "sess-1": {
        report: structuredClone(reportFixture) as unknown as Report,
        polls: 0,
        submitted: null,
        stuck: false,
      },
      "sess-6": { report: reviewedReport(), polls: 0, submitted: null, stuck: false },
      // 생성 실패 리포트(조회 → 409 report_failed, 다시 만들기 → 생성 중 409 두 번 뒤 리포트)
      "sess-7": {
        report: structuredClone(reportFixture) as unknown as Report,
        polls: 0,
        submitted: null,
        stuck: false,
        failedStep: "draft",
      },
    },
    counter: 0,
  };
}

let store: Store = fresh();

export function resetAdminMock() {
  store = fresh();
}

function auth(body: unknown, signup: boolean) {
  const { email, password } = (body ?? {}) as { email?: string; password?: string };
  if (signup && email === "taken@example.com") {
    throw new MockApiError(409, "conflict", "이미 가입된 이메일입니다.");
  }
  if (!signup && password === "wrong") throw new MockApiError(401, "unauthorized");
  return { token: "mock-admin-token" };
}

function createPosition(body: unknown) {
  const request = (body ?? {}) as { name?: string; session_minutes?: number; elements?: unknown };
  // 요소 선택 기능 없음: elements가 오면 422
  if ("elements" in request) throw new MockApiError(422, "invalid_input");
  const minutes = request.session_minutes ?? catalog.default_session_minutes;
  if (!catalog.session_minutes_options.includes(minutes as SessionMinutes)) {
    throw new MockApiError(422, "invalid_input");
  }
  store.counter += 1;
  const id = `pos-new-${store.counter}`;
  const position: Position = {
    id,
    name: request.name?.trim() || "이름 없는 채용 건",
    session_minutes: minutes as SessionMinutes,
    candidate_count: 0,
    created_at: new Date().toISOString(),
  };
  store.positions.unshift(position);
  store.candidates[id] = [];
  return { ...createdFixture, id, name: position.name, session_minutes: position.session_minutes };
}

function candidates(id: string) {
  const list = store.candidates[id];
  if (!list) throw new MockApiError(404, "not_found");
  return list;
}

function findSession(sessionId: string) {
  for (const [positionId, list] of Object.entries(store.candidates)) {
    const candidate = list.find((c) => c.session_id === sessionId);
    if (candidate) return { positionId, candidate };
  }
  throw new MockApiError(404, "not_found");
}

function inviteUrl(token: string) {
  const origin = typeof window === "undefined" ? "http://localhost:3000" : window.location.origin;
  return `${origin}/s/${token}`;
}

function notice(url: string) {
  return inviteFixture.notice_text.replace(inviteFixture.invite_url, url);
}

function invite(positionId: string, body: unknown) {
  const list = candidates(positionId);
  const request = (body ?? {}) as { alias?: string; email?: string; previous_session_id?: string };
  const previous = request.previous_session_id
    ? list.find((c) => c.session_id === request.previous_session_id)
    : undefined;
  if (request.previous_session_id) {
    const allowed =
      previous &&
      ((previous.status === "failed" && previous.failed_stage === "session") ||
        previous.status === "cancelled" ||
        previous.display_status === "expired");
    if (!allowed) throw new MockApiError(422, "invalid_previous_session");
  }
  const alias = request.alias?.trim() || previous?.alias;
  if (!alias || !request.email?.trim()) throw new MockApiError(422, "invalid_input");
  if (!previous && list.some((c) => c.alias === alias)) {
    throw new MockApiError(409, "conflict", "같은 채용 건에 같은 별칭이 있습니다.");
  }
  store.counter += 1;
  const sessionId = `sess-new-${store.counter}`;
  list.push({
    session_id: sessionId,
    alias,
    email: request.email.trim(),
    status: "invited",
    display_status: "invited",
    display_group: "waiting",
    phase: null,
    invited_at: new Date().toISOString(),
    message_count: 0,
    next_action: "입장 대기",
    previous_session_id: previous?.session_id ?? null,
    elements: null,
    low_confidence_count: null,
    level_source: null,
  });
  const position = store.positions.find((p) => p.id === positionId);
  if (position && !previous) position.candidate_count += 1;
  const url = inviteUrl("demo");
  return {
    session_id: sessionId,
    invite_url: url,
    expires_at: new Date(Date.now() + INVITE_MS).toISOString(),
    notice_text: notice(url),
  };
}

function reissue(sessionId: string) {
  const { candidate } = findSession(sessionId);
  if (candidate.status !== "invited") throw new MockApiError(409, "conflict");
  const now = new Date();
  Object.assign(candidate, {
    display_status: "invited",
    reissued_at: now.toISOString(),
    next_action: "입장 대기",
  });
  store.counter += 1;
  const url = inviteUrl(`demo`);
  return {
    ...reissueFixture,
    session_id: sessionId,
    invite_url: url,
    expires_at: new Date(now.getTime() + INVITE_MS).toISOString(),
    notice_text: notice(url),
  };
}

function cancel(sessionId: string) {
  const { candidate } = findSession(sessionId);
  if (
    !["invited", "in_progress"].includes(candidate.status) ||
    candidate.display_status === "expired"
  ) {
    throw new MockApiError(409, "conflict");
  }
  Object.assign(candidate, {
    status: "cancelled",
    display_status: "cancelled",
    display_group: "problem",
    ended_at: new Date().toISOString(),
    next_action: "재응시 초대",
  });
  return { status: "cancelled" };
}

function regenerate(sessionId: string) {
  const failedReport = store.reports[sessionId];
  if (failedReport?.failedStep) {
    const status = failedReport.failedStep === "finalize" ? "approving" : "completed";
    failedReport.failedStep = null;
    failedReport.generating = POLLS_UNTIL_FINAL;
    return { status };
  }
  const { candidate } = findSession(sessionId);
  if (candidate.status !== "failed" || candidate.failed_stage !== "report") {
    throw new MockApiError(409, "conflict");
  }
  Object.assign(candidate, {
    status: "completed",
    display_status: "completed",
    display_group: "in_progress",
    failed_stage: null,
    failed_reason: null,
    next_action: null,
  });
  return { status: "completed" };
}

function reportState(sessionId: string) {
  const state = store.reports[sessionId];
  if (!state) throw new MockApiError(404, "not_found");
  return state;
}

function setStatus(sessionId: string, status: string, group: Candidate["display_group"]) {
  const { candidate } = findSession(sessionId);
  Object.assign(candidate, { status, display_status: status, display_group: group });
  return candidate;
}

function getReport(sessionId: string) {
  const state = reportState(sessionId);
  if (state.failedStep) {
    throw new MockApiError(409, "report_failed", "리포트 생성에 실패했습니다.", {
      failed_stage: "report",
      failed_step: state.failedStep,
    });
  }
  if (state.generating) {
    state.generating -= 1;
    throw new MockApiError(409, "report_not_ready", "리포트를 만드는 중입니다.");
  }
  const { report } = state;
  if (report.stage === "draft" && report.review && !state.stuck) {
    state.polls += 1;
    if (state.polls >= POLLS_UNTIL_FINAL) {
      report.stage = "final";
      report.reviewed_at = new Date().toISOString();
      report.review.reviewed_at = report.reviewed_at;
      setStatus(sessionId, "reviewed", "done");
    }
  }
  return report;
}

function validate(report: Report, request: ReviewBody) {
  if (!request.reviewer_name?.trim() || request.reviewer_name.trim().length > 40) {
    throw new MockApiError(422, "invalid_input", "검토자 이름을 적어 주세요.");
  }
  const ids = new Set(report.dimensions.map((d) => d.dimension_id));
  const seen = new Set<string>();
  for (const adj of request.adjustments ?? []) {
    if (!ids.has(adj.dimension_id)) throw new MockApiError(422, "invalid_dimension");
    if (seen.has(adj.dimension_id)) throw new MockApiError(422, "duplicate_dimension");
    seen.add(adj.dimension_id);
    if (!adj.memo?.trim()) throw new MockApiError(422, "memo_required");
    const hasLevel = adj.level != null;
    const hasReason = !!adj.no_record_reason;
    if (hasLevel === hasReason) throw new MockApiError(422, "invalid_input");
    if (hasLevel && (adj.level! < 1 || adj.level! > 5))
      throw new MockApiError(422, "invalid_input");
    if (hasReason && !REASONS.includes(adj.no_record_reason!)) {
      throw new MockApiError(422, "invalid_input");
    }
  }
}

function mergeAdjustments(report: Report, adjustments: Adjustment[]) {
  for (const adj of adjustments) {
    const d = report.dimensions.find((x) => x.dimension_id === adj.dimension_id)!;
    Object.assign(d, {
      adjusted_level: adj.level,
      adjusted_no_record_reason: adj.level == null ? adj.no_record_reason : null,
      adjust_memo: adj.memo.trim(),
    });
  }
  for (const el of report.elements) {
    const dims = report.dimensions.filter((d) => d.element === el.element);
    if (!dims.some((d) => d.adjusted_level != null || d.adjusted_no_record_reason != null))
      continue;
    const levels = dims
      .map((d) =>
        d.adjusted_level != null || d.adjusted_no_record_reason ? d.adjusted_level : d.level,
      )
      .filter((l): l is NonNullable<typeof l> => l != null);
    el.adjusted_level = levels.length
      ? (Math.floor(
          levels.reduce((a, b) => a + b, 0) / levels.length + 0.5,
        ) as typeof el.adjusted_level)
      : null;
  }
}

function review(sessionId: string, body: unknown) {
  const state = reportState(sessionId);
  const { report } = state;
  const request = (body ?? {}) as ReviewBody;
  const text = JSON.stringify({
    adjustments: [...(request.adjustments ?? [])].sort((x, y) =>
      x.dimension_id.localeCompare(y.dimension_id),
    ),
    reviewer_name: request.reviewer_name?.trim(),
  });
  // 판정 순서: 상태(409) → 검증(422)
  if (state.submitted) {
    if (state.submitted !== text) throw new MockApiError(409, "already_reviewed");
    state.stuck = false; // 같은 본문 재요청 = "확정 다시 시도"
    return { status: "approving" };
  }
  if (report.stage !== "draft") throw new MockApiError(409, "conflict");
  validate(report, request);
  mergeAdjustments(report, request.adjustments ?? []);
  report.review = { reviewer_name: request.reviewer_name.trim(), reviewed_at: null };
  state.submitted = text;
  state.stuck = request.reviewer_name.includes("지연");
  const candidate = setStatus(sessionId, "approving", "done");
  if ((request.adjustments ?? []).length) candidate.level_source = "reviewer";
  return { status: "approving" };
}

function addNote(sessionId: string, body: unknown) {
  const { report } = reportState(sessionId);
  if (!report.review) throw new MockApiError(409, "conflict");
  const { text, reviewer_name } = (body ?? {}) as { text?: string; reviewer_name?: string };
  if (!text?.trim() || !reviewer_name?.trim()) throw new MockApiError(422, "invalid_input");
  store.counter += 1;
  report.notes.push({
    id: `note-${store.counter}`,
    text: text.trim(),
    reviewer_name: reviewer_name.trim(),
    created_at: new Date().toISOString(),
  });
  return { notes: report.notes };
}

function record(sessionId: string) {
  reportState(sessionId);
  return recordFixture as RecordEntry[];
}

export const ROUTES: MockRoute[] = [
  ["POST", "/admin/auth/signup", (_, body) => auth(body, true)],
  ["POST", "/admin/auth/login", (_, body) => auth(body, false)],
  ["GET", "/admin/catalog", () => catalog],
  ["GET", "/admin/positions", () => store.positions],
  ["POST", "/admin/positions", (_, body) => createPosition(body)],
  ["GET", "/admin/positions/:id/candidates", ({ id }) => candidates(id)],
  ["POST", "/admin/positions/:id/invites", ({ id }, body) => invite(id, body)],
  ["POST", "/admin/sessions/:id/reissue", ({ id }) => reissue(id)],
  ["POST", "/admin/sessions/:id/cancel", ({ id }) => cancel(id)],
  ["GET", "/admin/sessions/:id/report", ({ id }) => getReport(id)],
  ["POST", "/admin/sessions/:id/report/regenerate", ({ id }) => regenerate(id)],
  ["POST", "/admin/sessions/:id/review", ({ id }, body) => review(id, body)],
  ["POST", "/admin/sessions/:id/report/notes", ({ id }, body) => addNote(id, body)],
  ["GET", "/admin/sessions/:id/record", ({ id }) => record(id)],
];

/** 테스트용: 관리자 경로만 바로 부른다(mocks/index의 다른 경로 표를 거치지 않음) */
export async function adminMockRequest<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const got = path.split("?")[0].split("/");
  for (const [m, pattern, handler] of ROUTES) {
    const want = pattern.split("/");
    if (m !== method || want.length !== got.length) continue;
    const params: Record<string, string> = {};
    if (
      want.every((w, i) =>
        w.startsWith(":") ? ((params[w.slice(1)] = got[i]), true) : w === got[i],
      )
    ) {
      return structuredClone(handler(params, body)) as T;
    }
  }
  throw new MockApiError(404, "mock_missing");
}
