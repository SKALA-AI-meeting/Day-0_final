"use client";

// 지원자 화면 흐름: 링크 입장 → 동의 → 대기 → 토론 세션 → 디브리핑. 단계는 상태로 바꾸고 경로는 /s/[token] 하나다.
// 재입장은 GET /s/{token}의 status로 갈린다: invited → 입장, in_progress → resume, 종료 뒤 → 디브리핑.
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Toaster } from "@/components/ui";
import { api, ApiError, MOCK } from "@/lib/api";

import { DebateView } from "./debate-view";
import { DebriefScreen } from "./debrief-screen";
import { ConsentScreen, EntryScreen, LinkNotice, WaitingScreen } from "./link-screens";
import { MockControls } from "./mock-controls";
import type { Debrief, EndReason, Entry, StateSnapshot, TokenResponse } from "./types";

type Stage =
  | { kind: "loading" }
  | { kind: "notice"; title: string; description: string; retry?: boolean }
  | { kind: "entry"; entry: Entry }
  | { kind: "consent"; entry: Entry; pending?: boolean; error?: string | null }
  | { kind: "waiting"; entry: Entry; snap: StateSnapshot; pending?: boolean; error?: string | null }
  | { kind: "session"; entry: Entry; snap: StateSnapshot; sessionId: string }
  | {
      kind: "debrief";
      entry: Entry;
      debrief: Debrief;
      endReason: EndReason | null;
      minutes: number;
    };

const ENDED = new Set(["completed", "report_ready", "approving", "reviewed", "failed"]);

function noticeFor(error: unknown): Stage {
  if (error instanceof ApiError && error.code === "expired")
    return {
      kind: "notice",
      title: "링크가 만료되었습니다",
      description: "채용 담당자에게 문의해 주세요.",
    };
  if (error instanceof ApiError && error.code === "cancelled")
    return {
      kind: "notice",
      title: "취소된 평가입니다",
      description: "자세한 내용은 채용 담당자에게 문의해 주세요.",
    };
  if (error instanceof ApiError && error.status === 404)
    return {
      kind: "notice",
      title: "링크를 찾을 수 없어요",
      description: "받은 링크 주소를 다시 확인해 주세요.",
    };
  return {
    kind: "notice",
    title: "연결하지 못했어요",
    description: "잠시 뒤 다시 시도해 주세요.",
    retry: true,
  };
}

export function CandidateFlow({ token }: { token: string }) {
  const [stage, setStage] = useState<Stage>({ kind: "loading" });
  const sessionToken = useRef<string>("");
  const sessionId = useRef<string>("");

  const fetchState = useCallback(
    () =>
      api<StateSnapshot>(`/sessions/${sessionId.current}/state`, { token: sessionToken.current }),
    [],
  );

  const toDebrief = useCallback(
    async (entry: Entry, endReason: EndReason | null, minutes: number) => {
      try {
        const debrief = await api<Debrief>(`/sessions/${sessionId.current}/debrief`, {
          token: sessionToken.current,
        });
        setStage({ kind: "debrief", entry, debrief, endReason, minutes });
      } catch (error) {
        setStage(noticeFor(error));
      }
    },
    [],
  );

  const load = useCallback(async () => {
    try {
      const entry = await api<Entry>(`/s/${token}`);
      sessionId.current = entry.session_id;
      if (ENDED.has(entry.status)) {
        // 종료 뒤 재입장: debrief_token으로 디브리핑(종료 후 7일)
        sessionToken.current = entry.debrief_token ?? "";
        await toDebrief(entry, null, entry.duration_minutes);
        return;
      }
      if (entry.status === "in_progress") {
        const res = await api<TokenResponse>(`/s/${token}/resume`, { method: "POST" });
        sessionToken.current = res.session_token;
        sessionId.current = res.session_id;
        setStage({
          kind: "session",
          entry,
          snap: await fetchState(),
          sessionId: sessionId.current,
        });
        return;
      }
      setStage({ kind: "entry", entry });
    } catch (error) {
      setStage(noticeFor(error));
    }
  }, [token, fetchState, toDebrief]);

  useEffect(() => {
    void load();
  }, [load]);

  const refreshToken = useCallback(async () => {
    try {
      const res = await api<TokenResponse>(`/s/${token}/resume`, { method: "POST" });
      sessionToken.current = res.session_token;
      return res.session_token;
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) return null;
      throw error;
    }
  }, [token]);

  let screen: ReactNode = null;
  switch (stage.kind) {
    case "loading":
      screen = <div className="min-h-dvh" aria-busy />;
      break;
    case "notice":
      screen = (
        <LinkNotice
          title={stage.title}
          description={stage.description}
          onRetry={
            stage.retry
              ? () => {
                  setStage({ kind: "loading" });
                  void load();
                }
              : undefined
          }
        />
      );
      break;
    case "entry":
      screen = (
        <EntryScreen
          entry={stage.entry}
          onNext={() => setStage({ kind: "consent", entry: stage.entry })}
        />
      );
      break;
    case "consent":
      screen = (
        <ConsentScreen
          entry={stage.entry}
          pending={stage.pending}
          error={stage.error}
          onConsent={async (displayName) => {
            setStage({ ...stage, pending: true, error: null });
            try {
              const res = await api<TokenResponse>(`/s/${token}/consent`, {
                method: "POST",
                body: { display_name: displayName },
              });
              sessionToken.current = res.session_token;
              sessionId.current = res.session_id;
              // 시작 전 /state로 팀 소개(이름·프로필 이미지만)
              setStage({ kind: "waiting", entry: stage.entry, snap: await fetchState() });
            } catch (error) {
              const message =
                error instanceof ApiError && error.status === 422
                  ? "표시 이름은 1~20자로 입력해 주세요."
                  : "동의를 저장하지 못했어요. 다시 시도해 주세요.";
              setStage({ ...stage, pending: false, error: message });
            }
          }}
        />
      );
      break;
    case "waiting":
      screen = (
        <WaitingScreen
          snap={stage.snap}
          pending={stage.pending}
          error={stage.error}
          onStart={async () => {
            setStage({ ...stage, pending: true, error: null });
            try {
              if (!MOCK) {
                const microphone = await navigator.mediaDevices.getUserMedia({
                  audio: true,
                  video: false,
                });
                microphone.getTracks().forEach((track) => track.stop());
              }
              await api(`/s/${token}/start`, { method: "POST", token: sessionToken.current });
              setStage({
                kind: "session",
                entry: stage.entry,
                snap: await fetchState(),
                sessionId: sessionId.current,
              });
            } catch (error) {
              if (error instanceof ApiError && error.code === "expired") setStage(noticeFor(error));
              else
                setStage({
                  ...stage,
                  pending: false,
                  // position_outdated: 다시 눌러도 안 되므로 api의 담당자 문의 안내를 그대로 보인다
                  error:
                    error instanceof ApiError && error.code === "position_outdated"
                      ? error.message
                      : "시작하지 못했어요. 다시 시도해 주세요.",
                });
            }
          }}
        />
      );
      break;
    case "session":
      screen = (
        <DebateView
          auth={{
            sessionId: stage.sessionId,
            getToken: () => sessionToken.current,
            refreshToken,
          }}
          initial={stage.snap}
          onExit={(reason) =>
            void toDebrief(stage.entry, reason, stage.snap.session.session_minutes)
          }
        />
      );
      break;
    case "debrief":
      screen = (
        <DebriefScreen
          debrief={stage.debrief}
          endReason={stage.endReason}
          sessionMinutes={stage.minutes}
          onSurvey={async (answers, comment) => {
            try {
              await api(`/sessions/${sessionId.current}/debrief/survey`, {
                method: "POST",
                token: sessionToken.current,
                body: comment ? { answers, free_comment: comment } : { answers },
              });
              return true;
            } catch {
              return false;
            }
          }}
        />
      );
      break;
  }

  return (
    <>
      {screen}
      <Toaster />
      {MOCK && <MockControls token={token} />}
    </>
  );
}
