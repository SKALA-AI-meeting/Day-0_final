"use client";

// 관리자 화면 공용: 토큰 보관, API 호출 묶음, 오류 문구, 로딩 훅.
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { api } from "@/lib/api";

import type {
  Candidate,
  Catalog,
  InviteResult,
  Note,
  Position,
  PositionCreated,
  RecordEntry,
  Report,
  ReviewBody,
  SessionMinutes,
} from "./types";

const TOKEN_KEY = "dayzero.admin.token";
const TOKEN_EVENT = "dayzero-admin-token";

function readToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // 저장소를 못 쓰면 로그인 화면으로 돌아온다
  }
  window.dispatchEvent(new Event(TOKEN_EVENT));
}

function subscribe(onChange: () => void) {
  window.addEventListener(TOKEN_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(TOKEN_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** 서버 렌더에서는 null. 로그인 여부는 클라이언트에서만 안다. */
export function useToken(): string | null {
  return useSyncExternalStore(subscribe, readToken, () => null);
}

const ERROR_TEXT: Record<string, string> = {
  conflict: "이미 사용 중이거나 지금 상태에서는 할 수 없는 요청입니다.",
  unauthorized: "이메일 또는 비밀번호가 맞지 않습니다.",
  invalid_previous_session: "세션 실패·취소된 지원자만 재응시 초대할 수 있습니다.",
  invalid_input: "필수 입력을 확인해 주세요.",
  memo_required: "조정한 세부 행동에는 메모를 적어야 합니다.",
  invalid_dimension: "평가표에 없는 세부 행동입니다.",
  duplicate_dimension: "같은 세부 행동이 두 번 들어 있습니다.",
  already_reviewed:
    "이미 다른 내용으로 검토가 제출되었습니다. 화면을 새로 고쳐 확정된 값을 확인하세요.",
};

/** ApiError와 mock 오류 모두 code를 가지므로 모양으로 읽는다. */
export function errorCode(error: unknown): string | null {
  const code = (error as { code?: unknown } | null)?.code;
  return typeof code === "string" ? code : null;
}

/** 오류의 추가 필드(ApiError·mock 오류 공통). 리포트 실패의 failed_step 등 */
export function errorExtra(error: unknown): Record<string, unknown> {
  const extra = (error as { extra?: unknown } | null)?.extra;
  return extra && typeof extra === "object" ? (extra as Record<string, unknown>) : {};
}

export function errorMessage(error: unknown): string {
  const code = errorCode(error);
  if (code && ERROR_TEXT[code]) return ERROR_TEXT[code];
  return error instanceof Error ? error.message : "요청을 처리하지 못했습니다.";
}

export const adminApi = {
  signup: (body: { email: string; password: string; company_name: string }) =>
    api<{ token: string }>("/admin/auth/signup", { method: "POST", body }),
  login: (body: { email: string; password: string }) =>
    api<{ token: string }>("/admin/auth/login", { method: "POST", body }),
  catalog: (token: string) => api<Catalog>("/admin/catalog", { token }),
  positions: (token: string) => api<Position[]>("/admin/positions", { token }),
  createPosition: (token: string, body: { name: string; session_minutes: SessionMinutes }) =>
    api<PositionCreated>("/admin/positions", { method: "POST", token, body }),
  candidates: (token: string, positionId: string) =>
    api<Candidate[]>(`/admin/positions/${positionId}/candidates`, { token }),
  invite: (
    token: string,
    positionId: string,
    body: { alias?: string; email: string; previous_session_id?: string },
  ) => api<InviteResult>(`/admin/positions/${positionId}/invites`, { method: "POST", token, body }),
  reissue: (token: string, sessionId: string) =>
    api<InviteResult>(`/admin/sessions/${sessionId}/reissue`, { method: "POST", token }),
  cancel: (token: string, sessionId: string) =>
    api<{ status: string }>(`/admin/sessions/${sessionId}/cancel`, { method: "POST", token }),
  regenerate: (token: string, sessionId: string) =>
    api<{ status: "completed" | "approving" }>(`/admin/sessions/${sessionId}/report/regenerate`, {
      method: "POST",
      token,
    }),
  report: (token: string, sessionId: string) =>
    api<Report>(`/admin/sessions/${sessionId}/report`, { token }),
  review: (token: string, sessionId: string, body: ReviewBody) =>
    api<{ status: string }>(`/admin/sessions/${sessionId}/review`, {
      method: "POST",
      token,
      body,
    }),
  addNote: (token: string, sessionId: string, body: { text: string; reviewer_name: string }) =>
    api<{ notes: Note[] }>(`/admin/sessions/${sessionId}/report/notes`, {
      method: "POST",
      token,
      body,
    }),
  record: (token: string, sessionId: string) =>
    api<RecordEntry[]>(`/admin/sessions/${sessionId}/record`, { token }),
};

export type Loaded<T> = {
  data: T | null;
  error: string | null;
  /** 마지막 실패의 api 오류 코드(없으면 null) */
  code: string | null;
  /** 마지막 실패 오류의 추가 필드(없으면 빈 객체) */
  extra: Record<string, unknown>;
  /** 연속 실패 횟수(성공하면 0) */
  failures: number;
  reload: () => void;
};

/**
 * 로그인한 뒤 마운트 때와 key가 바뀌거나 reload()를 부를 때 load(token)을 부른다.
 * 조회가 실패해도 마지막 성공 데이터는 유지한다(폴링 중 일시 오류로 화면이 비지 않게).
 */
export function useLoad<T>(load: (token: string) => Promise<T>, key = ""): Loaded<T> {
  const token = useToken();
  const loadRef = useRef(load);
  const [state, setState] = useState<{
    data: T | null;
    error: string | null;
    code: string | null;
    extra: Record<string, unknown>;
    failures: number;
  }>({
    data: null,
    error: null,
    code: null,
    extra: {},
    failures: 0,
  });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    loadRef.current = load;
  });

  useEffect(() => {
    if (!token) return;
    let live = true;
    loadRef.current(token).then(
      (data) => live && setState({ data, error: null, code: null, extra: {}, failures: 0 }),
      (error) =>
        live &&
        setState((prev) => ({
          data: prev.data,
          error: errorMessage(error),
          code: errorCode(error),
          extra: errorExtra(error),
          failures: prev.failures + 1,
        })),
    );
    return () => {
      live = false;
    };
  }, [token, key, tick]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { ...state, reload };
}
