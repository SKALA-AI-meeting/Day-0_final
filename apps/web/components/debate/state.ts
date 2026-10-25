// State 스냅샷에 WS 프레임 하나를 더하는 순수 함수와 화면 공용 표기.
// 화면(reducer)과 mock 서버가 같은 함수로 상태를 굴려, mock의 /state 재조회가 화면과 어긋나지 않는다.
import type { Phase, ServerFrame, SlotPhase, StateSnapshot } from "./types";

/** 지원자 발언 한 건의 최대 글자 수(텍스트·음성 공통). 서버 debate.CANDIDATE_MESSAGE_MAX_CHARS와 같은 값 */
export const CANDIDATE_MESSAGE_MAX_CHARS = 500;

/** 지원자 화면 단계 이름(R7 지원자 말) */
export const PHASE_LABEL: Record<Phase, string> = {
  keyword: "키워드",
  problem: "문제 상황",
  solution: "해결책",
  proposal: "기획안 작성",
};

export const SLOT_LABEL: Record<SlotPhase, string> = {
  keyword: "키워드",
  problem: "문제 상황",
  solution: "해결책",
};

/** 오피스 시각 = 09:00 + t_min × 60 / session_minutes (분 단위 내림, "HH:MM"). 메시지 타임스탬프에만 쓴다 */
export function officeTime(tMin: number, sessionMinutes: number, start = "09:00") {
  const [h, m] = start.split(":").map(Number);
  const total = h * 60 + m + Math.floor((tMin * 60) / sessionMinutes);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

/** 남은 시간 "12:40" */
export function clock(seconds: number) {
  const s = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** 마감 경고 기준: 60분 세션의 2분을 세션 길이 비율로 줄인 초 */
export function warnSeconds(sessionMinutes: number) {
  return (120 * sessionMinutes) / 60;
}

/** "약 2분", "약 30초" */
export function roughDuration(seconds: number) {
  if (seconds >= 60) return `약 ${Math.round(seconds / 60)}분`;
  return `약 ${Math.max(1, Math.round(seconds))}초`;
}

/**
 * 프레임 하나를 스냅샷에 반영한 새 스냅샷. seq 검사는 호출하는 쪽(reducer)이 한다.
 * message.ack·error·session.replaced는 스냅샷을 바꾸지 않는다.
 */
export function applyFrame(snap: StateSnapshot, frame: ServerFrame): StateSnapshot {
  const next: StateSnapshot = { ...snap, ws_seq: Math.max(snap.ws_seq, frame.seq) };
  switch (frame.type) {
    case "message.new": {
      if (snap.messages.some((m) => m.id === frame.payload.id)) return next;
      next.messages = [...snap.messages, frame.payload];
      // 메시지를 게시한 자리는 입력 중이 아니다
      next.typing = snap.typing.filter((seat) => seat !== frame.payload.sender);
      return next;
    }
    case "typing": {
      const { participant, state } = frame.payload;
      const rest = snap.typing.filter((seat) => seat !== participant);
      next.typing = state === "start" ? [...rest, participant] : rest;
      return next;
    }
    case "slot.confirmed": {
      const p = frame.payload;
      next.slots = snap.slots.map((slot) =>
        slot.phase === p.phase
          ? {
              ...slot,
              status: "confirmed",
              value: p.value,
              filled_by: p.filled_by,
              resolution: p.resolution,
              outcome: p.outcome,
              t_min: p.t_min,
            }
          : slot,
      );
      return next;
    }
    case "phase.changed": {
      next.session = {
        ...snap.session,
        status: "in_progress",
        phase: frame.payload.phase,
        phase_started_at: frame.payload.phase_started_at,
        phase_deadline_at: frame.payload.phase_deadline_at,
      };
      return next;
    }
    case "timer": {
      next.session = {
        ...snap.session,
        phase_deadline_at: frame.payload.phase_deadline_at,
        phase_remaining_seconds: frame.payload.phase_remaining_seconds,
        session_remaining_seconds: frame.payload.session_remaining_seconds,
      };
      return next;
    }
    case "materials.opened": {
      next.materials = {
        locked: false,
        keyword: frame.payload.keyword,
        materials: frame.payload.materials,
      };
      return next;
    }
    case "session.ended": {
      const reason = frame.payload.reason;
      const status =
        reason === "failed" ? "failed" : reason === "cancelled" ? "cancelled" : "completed";
      next.session = { ...snap.session, status };
      next.typing = [];
      return next;
    }
    default:
      return next;
  }
}

/** 한 단계의 정할 항목이 확정됐는지 */
export function slotOf(snap: StateSnapshot, phase: SlotPhase) {
  return snap.slots.find((slot) => slot.phase === phase);
}

/** 참여자 표시 이름(자리 id → 이름). 모르는 자리는 id 그대로 */
export function nameOf(snap: Pick<StateSnapshot, "participants">, id: string) {
  return snap.participants.find((p) => p.id === id)?.display_name ?? id;
}
