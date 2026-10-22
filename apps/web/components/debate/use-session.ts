"use client";

// 토론 세션 연결: /state 스냅샷 + WS 증분, 재연결·seq 공백 시 /state 재조회, 보내기·정하기·자료·어시스턴트·기획안 저장.
// 데이터 원천은 lib/api·lib/ws뿐이라 NEXT_PUBLIC_MOCK=1(mock)과 실 api가 같은 코드로 돈다.
import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { toast } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { SessionSocket, type ConnectionStatus } from "@/lib/ws";

import { submitBody } from "./proposal-editor";
import { initialView, reduce, type PanelTab } from "./reducer";
import { createSaver } from "./saver";
import type {
  AssistantResponse,
  EndReason,
  Material,
  Proposal,
  SectionId,
  Sections,
  SlotConfirmResponse,
  SlotPhase,
  StateSnapshot,
} from "./types";

export type SessionAuth = {
  sessionId: string;
  getToken: () => string;
  /** 4401 재발급(/s/{token}/resume). 세션이 끝났으면 null */
  refreshToken: () => Promise<string | null>;
};

const ERROR_TEXT: Record<string, string> = {
  slot_confirmed: "이미 정해진 항목이에요",
  phase_not_open: "이미 정해진 항목이에요",
  invalid_keyword: "후보 4개 중에서 골라 주세요",
  invalid_input: "내용을 입력해 주세요",
  session_ending: "세션이 끝났어요",
  conflict: "지금은 처리할 수 없어요. 잠시 뒤 다시 시도해 주세요",
  assistant_limit: "이 세션의 요청 한도를 다 썼어요",
};

const errorText = (error: unknown) =>
  error instanceof ApiError
    ? (ERROR_TEXT[error.code] ?? "잠시 뒤 다시 시도해 주세요")
    : "잠시 뒤 다시 시도해 주세요";

/** 저장 시각은 실제 시계로 보여 주지 않고(R7) 경과 시간 계산에만 쓴다 */
const savedMs = (iso: string | null) => (iso ? Date.parse(iso) : null);

export function useDebateSession(auth: SessionAuth, initial: StateSnapshot) {
  const [view, dispatch] = useReducer(reduce, initial, (snap) => initialView(snap, Date.now()));
  const [now, setNow] = useState(() => Date.now());
  const [connection, setConnection] = useState<ConnectionStatus>("connecting");
  const socketRef = useRef<SessionSocket | null>(null);
  const authRef = useRef(auth);
  const path = `/sessions/${auth.sessionId}`;

  const reload = useCallback(async () => {
    try {
      const snap = await api<StateSnapshot>(`${path}/state`, { token: authRef.current.getToken() });
      dispatch({ type: "snapshot", snap, now: Date.now() });
    } catch {
      // 다음 연결·공백 때 다시 읽는다
    }
  }, [path]);

  // WS: 열릴 때마다(처음 포함) /state로 맞춘 뒤 증분을 적용한다
  useEffect(() => {
    const socket = new SessionSocket({
      sessionId: authRef.current.sessionId,
      getToken: () => authRef.current.getToken(),
      refreshToken: () => authRef.current.refreshToken(),
      onFrame: (frame) => {
        if (frame.type === "error") toast(ERROR_TEXT[frame.payload.code] ?? errorText(null));
        dispatch({ type: "frame", frame, now: Date.now() });
      },
      onStatus: setConnection,
      onOpen: () => void reload(),
    });
    socketRef.current = socket;
    socket.connect();
    return () => socket.close();
  }, [reload]);

  // 기획안 단계가 되면 서버가 앞 3칸을 확정 값으로 채우므로 /state로 받는다(WS 프레임 없음)
  const phase = view.snap.session.phase;
  useEffect(() => {
    if (phase === "proposal") void reload();
  }, [phase, reload]);

  // seq가 비면 /state로 다시 맞춘다(R5-10)
  useEffect(() => {
    if (view.needsResync) void reload();
  }, [view.needsResync, reload]);

  // 1초마다: 로컬 카운트다운·입력 중 30초 안전장치
  useEffect(() => {
    const timer = setInterval(() => {
      const t = Date.now();
      setNow(t);
      dispatch({ type: "tick", now: t });
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const sendMessage = useCallback(
    (content: string) => {
      const clientMsgId = `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
      const last = view.snap.messages[view.snap.messages.length - 1];
      dispatch({ type: "send", pending: { clientMsgId, content, tMin: last?.t_min ?? 0 } });
      socketRef.current?.send({
        type: "message.send",
        payload: { client_msg_id: clientMsgId, content },
      });
    },
    [view.snap.messages],
  );

  const sendTyping = useCallback((state: "start" | "stop") => {
    socketRef.current?.send({ type: "typing", payload: { state } });
  }, []);

  const sendVoice = useCallback((id: string, content: string, turn: string, epoch: string) => {
    socketRef.current?.send({
      type: "message.send",
      payload: {
        client_msg_id: id,
        content,
        voice_turn_id: turn,
        epoch,
      },
    });
  }, []);

  const confirmSlot = useCallback(
    async (phase: SlotPhase, value: string, optionKey?: string) => {
      try {
        await api<SlotConfirmResponse>(`${path}/slots/${phase}/confirm`, {
          method: "POST",
          token: authRef.current.getToken(),
          body: optionKey ? { value, option_key: optionKey } : { value },
        });
        return null;
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) void reload();
        return errorText(error);
      }
    },
    [path, reload],
  );

  const openMaterial = useCallback(
    async (materialId: string) => {
      try {
        return await api<Material>(`${path}/materials/${materialId}`, {
          token: authRef.current.getToken(),
        });
      } catch (error) {
        if (error instanceof ApiError && error.status === 423) void reload();
        else toast(errorText(error));
        return null;
      }
    },
    [path, reload],
  );

  const ask = useCallback(
    async (prompt: string) => {
      try {
        await api<AssistantResponse>(`${path}/assistant`, {
          method: "POST",
          token: authRef.current.getToken(),
          body: { prompt },
        });
        await reload();
        return null;
      } catch (error) {
        return errorText(error);
      }
    },
    [path, reload],
  );

  // ── 기획안: 화면의 초안을 1초 디바운스로 저장, 409면 최신으로 바꾼다 ──
  const [draft, setDraft] = useState<Sections>(initial.proposal.sections);
  const [savedAt, setSavedAt] = useState<number | null>(savedMs(initial.proposal.updated_at));
  const [saving, setSaving] = useState(false);
  const revision = useRef(initial.proposal.revision);
  const draftRef = useRef(draft);
  const dirty = useRef(false);
  const saver = useRef(createSaver(1000));

  useEffect(() => {
    // 서버 쪽 기획안이 바뀌었고(단계 시작 때 앞 3칸 채움 등) 화면에 저장 안 된 편집이 없으면 받아들인다
    const server = view.snap.proposal;
    if (server.revision > revision.current && !dirty.current) {
      revision.current = server.revision;
      draftRef.current = server.sections;
      setDraft(server.sections);
    }
  }, [view.snap.proposal]);

  const adoptLatest = useCallback(async () => {
    const latest = await api<Proposal>(`${path}/proposal`, { token: authRef.current.getToken() });
    revision.current = latest.revision;
    draftRef.current = latest.sections;
    dirty.current = false;
    setDraft(latest.sections);
    setSavedAt(Date.now());
  }, [path]);

  const saveJob = useCallback(async () => {
    setSaving(true);
    try {
      const res = await api<{ revision: number; updated_at: string }>(`${path}/proposal`, {
        method: "PUT",
        token: authRef.current.getToken(),
        body: { sections: draftRef.current, revision: revision.current },
      });
      revision.current = res.revision;
      dirty.current = false;
      setSavedAt(Date.now());
    } catch (error) {
      if (error instanceof ApiError && error.code === "conflict") {
        await adoptLatest();
        toast("다른 창에서 고친 내용으로 바꿨어요");
        return;
      }
      throw error;
    } finally {
      setSaving(false);
    }
  }, [path, adoptLatest]);

  const editSection = useCallback(
    (id: SectionId, value: string) => {
      const next = { ...draftRef.current, [id]: value };
      draftRef.current = next;
      dirty.current = true;
      setDraft(next);
      saver.current.schedule("proposal", saveJob);
    },
    [saveJob],
  );

  /** 저장 대기분을 먼저 PUT한다(409면 최신 초안으로 바뀜). 검사할 최신 초안을 돌려주고, 저장 못 하면 null */
  const prepareSubmit = useCallback(async (): Promise<Sections | null> => {
    if (!(await saver.current.flush())) {
      toast("저장하지 못했어요. 잠시 뒤 다시 제출해 주세요");
      return null;
    }
    return draftRef.current;
  }, []);

  const submit = useCallback(
    async (blankWarningShown = false) => {
      try {
        await api(`${path}/proposal/submit`, {
          method: "POST",
          token: authRef.current.getToken(),
          body: submitBody(blankWarningShown),
        });
        dispatch({ type: "ended", reason: "submitted" });
      } catch (error) {
        toast(errorText(error));
      }
    },
    [path],
  );

  useEffect(() => {
    // 세션이 끝나면 예약 저장을 멈춘다
    if (view.ended || view.replaced) saver.current.stop();
  }, [view.ended, view.replaced]);

  return {
    view,
    now,
    connection,
    setTab: (tab: PanelTab) => dispatch({ type: "tab", tab }),
    sendMessage,
    sendVoice,
    sendTyping,
    confirmSlot,
    openMaterial,
    ask,
    proposal: { draft, savedAt, saving, editSection, prepareSubmit, submit },
    endReason: view.ended as EndReason | null,
  };
}
