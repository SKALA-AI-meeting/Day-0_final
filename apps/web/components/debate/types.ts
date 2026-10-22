// 지원자 화면이 쓰는 계약 형태(docs/contracts/api.md "지원자 REST"·"State 스냅샷"·"WebSocket").
// 공개 API에는 자리 id와 표시 이름만 나오고 페르소나 유형은 디브리핑(Debrief.seats)에만 있다.

import type { Outcome } from "@/lib/resolution";

export type Phase = "keyword" | "problem" | "solution" | "proposal";
export type SlotPhase = Exclude<Phase, "proposal">;
export type Seat = "p1" | "p2" | "p3";
export type Sender = "candidate" | Seat | "system";
export type Resolution = "consensus" | "timeout";
export type { Outcome } from "@/lib/resolution";
export type SessionStatus =
  | "invited"
  | "in_progress"
  | "completed"
  | "report_ready"
  | "approving"
  | "reviewed"
  | "failed"
  | "cancelled";
export type EndReason = "submitted" | "timeout" | "failed" | "cancelled";

export const PHASES: Phase[] = ["keyword", "problem", "solution", "proposal"];
export const SLOT_PHASES: SlotPhase[] = ["keyword", "problem", "solution"];

export type Entry = {
  session_id: string;
  company_name: string;
  duration_minutes: number;
  status: SessionStatus;
  default_display_name: string;
  elements_names: string[];
  entry_notice: string;
  contact_email: string;
  debrief_token?: string;
  failed_stage?: string;
};

export type TokenResponse = { session_token: string; session_id: string };

export type Participant = { id: "candidate" | Seat; display_name: string; avatar_url?: string };

export type Message = {
  id: string;
  sender: Sender;
  content: string;
  phase: Phase;
  is_invite: boolean;
  t_min: number;
  created_at: string;
};

export type Slot = {
  phase: SlotPhase;
  status: "empty" | "confirmed";
  value: string | null;
  filled_by: "candidate" | Seat | null;
  resolution: Resolution | null;
  outcome?: Outcome | null;
  t_min: number | null;
};

export type MaterialRef = { material_id: string; title: string };
export type Materials = { locked: boolean; keyword: string | null; materials: MaterialRef[] };
export type Material = MaterialRef & { content: string };

export const SECTION_IDS = [
  "keyword",
  "problem",
  "solution",
  "rationale",
  "expected_effect",
  "idea_credit",
] as const;
export type SectionId = (typeof SECTION_IDS)[number];
export type Sections = Record<SectionId, string>;

export type Proposal = {
  sections: Sections;
  revision: number;
  updated_at: string | null;
  submitted_at: string | null;
};

export type AssistantEntry = {
  call_id: string;
  request: string;
  response: string;
  cited_material_ids: string[];
  t_min: number;
};

export type AssistantResponse = {
  call_id: string;
  message_id: string;
  content: string;
  cited_material_ids: string[];
  tool_calls: { name: string; query?: string; material_id?: string }[];
};

export type ConnectionGap = { from_t_min: number; to_t_min: number };

export type SessionInfo = {
  id: string;
  alias: string;
  status: SessionStatus;
  phase: Phase | null;
  started_at: string | null;
  ends_at: string | null;
  session_minutes: number;
  phase_started_at: string | null;
  phase_deadline_at: string | null;
  phase_remaining_seconds: number | null;
  session_remaining_seconds: number | null;
  office_start: string;
  office_end: string;
  completed_at: string | null;
};

export type StateSnapshot = {
  session: SessionInfo;
  phases: { phase: Phase; budget_min: number; deadline_t_min: number }[];
  keyword_options: { option_key: string; label: string }[];
  participants: Participant[];
  slots: Slot[];
  messages: Message[];
  materials: Materials;
  proposal: Proposal;
  assistant: AssistantEntry[];
  typing: Seat[];
  active_runs: { run_id: string; stimulus: string }[];
  connection_gaps: ConnectionGap[];
  ws_seq: number;
};

export type SlotConfirmResponse = { slot: Slot; phase: Phase };

export type Debrief = {
  intro_notice: string;
  elements?: { element: string; name: string; how_we_see: string }[];
  cards?: { card_id: string; element: string; name: string; description: string }[];
  seats?: {
    seat: "candidate" | Seat;
    display_name: string;
    avatar_url?: string;
    persona_type?: string;
    persona_description?: string;
  }[];
  my_messages?: { id: string; content: string; phase: Phase; t_min: number }[];
  scoring_notice: string;
  appeal_contact: string;
  connection_gaps: ConnectionGap[];
  retention_notice: string;
  contact_email: string;
  proposal?: { sections: Sections } | null;
  view_until: string;
  survey: { submitted: boolean; questions: { q: string; text: string; kind: "scale" | "text" }[] };
  failed_stage?: string;
};

// ── WebSocket ────────────────────────────────────────────────

export type ServerPayloads = {
  "message.ack": { client_msg_id: string; message_id: string; run_id: string };
  "message.new": Message;
  typing: { participant: Seat; state: "start" | "stop" };
  "slot.confirmed": {
    phase: SlotPhase;
    value: string;
    filled_by: "candidate" | Seat;
    resolution: Resolution;
    outcome?: Outcome;
    t_min: number;
  };
  "phase.changed": {
    phase: Phase;
    previous_phase: Phase | null;
    reason: "start" | "confirmed" | "timeout";
    phase_started_at: string;
    phase_deadline_at: string;
    t_min: number;
  };
  timer: {
    phase: Phase;
    phase_deadline_at: string;
    phase_remaining_seconds: number;
    session_remaining_seconds: number;
  };
  "materials.opened": { keyword: string; materials: MaterialRef[] };
  "session.ended": { reason: EndReason };
  "session.replaced": Record<string, never>;
  error: { code: string; message: string };
};

export type ServerFrame = {
  [K in keyof ServerPayloads]: { type: K; seq: number; payload: ServerPayloads[K] };
}[keyof ServerPayloads];

export type SendFrame =
  | {
      type: "message.send";
      payload: { client_msg_id: string; content: string; voice_turn_id?: string; epoch?: string };
    }
  | { type: "typing"; payload: { state: "start" | "stop" } };
