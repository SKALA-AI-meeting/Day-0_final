// 관리자 화면이 쓰는 계약 형태. 정본은 docs/contracts/api.md "관리자 REST"·Report·근거 참조.
// 요소·차원·카드·유형 id는 문자열로만 받는다(실제 목록은 rubric.md·docs/product/scenario.md).
import type { Confidence, Level } from "@/components/ui";
import type { Outcome } from "@/lib/resolution";

export type { Confidence, Level };

export type ElementId = string;
export type Seat = "candidate" | "p1" | "p2" | "p3";
export type Sender = Seat | "system";
export type PhaseId = "keyword" | "problem" | "solution" | "proposal";
export type SessionMinutes = 60 | 15;

export type CatalogElement = { id: ElementId; name: string; meaning: string; how_we_see: string };

export type Catalog = {
  elements: CatalogElement[];
  session_minutes_options: SessionMinutes[];
  default_session_minutes: SessionMinutes;
  candidate_notice_template: string;
  candidate_faq: { q: string; a: string }[];
};

export type Position = {
  id: string;
  name: string;
  session_minutes: SessionMinutes;
  candidate_count: number;
  created_at: string;
};

export type PositionCreated = {
  id: string;
  name: string;
  session_minutes: SessionMinutes;
  seats: { seat: Seat; name: string; persona_type: string; avatar_url: string }[];
  keywords: string[];
  catalog_version: string;
};

export type DisplayGroup = "waiting" | "in_progress" | "needs_review" | "done" | "problem";

export type Candidate = {
  session_id: string;
  alias: string;
  email: string;
  status: string;
  display_status: string;
  display_group: DisplayGroup;
  phase?: string | null;
  invited_at: string;
  started_at?: string | null;
  ends_at?: string | null;
  ended_at?: string | null;
  last_activity_at?: string | null;
  message_count: number;
  report_ready_at?: string | null;
  purge_at?: string | null;
  failed_stage?: "session" | "report" | null;
  failed_reason?: string | null;
  next_action?: string | null;
  previous_session_id?: string | null;
  reissued_at?: string | null;
  elements?: { element: ElementId; level: Level | null; adjusted: boolean }[] | null;
  low_confidence_count?: number | null;
  level_source?: "auto" | "reviewer" | null;
};

export type InviteResult = {
  session_id: string;
  invite_url: string;
  expires_at: string;
  notice_text: string;
};

export type NoRecordReason =
  | "no_activity"
  | "evaluate_failed"
  | "not_applicable"
  | "preempted"
  | "event_not_fired"
  | "auto_submitted_blank";

/** 기록 없음 사유가 속한 상태(rubric.md 2.3·5장, 결정 A3). 수준이 있으면 "반응 있음" */
export type RecordState = "no_scene" | "no_response" | "failed";

export type RecordRef = { type: string; id: string; t_min: number };

export type ContextMessage = { id: string; sender: Sender; content: string; t_min: number };

type EvidenceBase = { ref: string; counted: boolean; t_min: number; record_ref: RecordRef };

export type SupportEntry = { option_id: string; label?: string; supporters: Seat[] };

export type MessageEvidence = EvidenceBase & {
  kind: "message";
  speaker: Sender;
  phase: PhaseId;
  origin: "spontaneous" | "invited" | null;
  quote: string;
  context?: { before: ContextMessage[]; after: ContextMessage[] };
};

export type Evidence =
  | MessageEvidence
  | (EvidenceBase & {
      kind: "slot";
      phase: PhaseId;
      value: string;
      resolution: "consensus" | "timeout";
      outcome?: Outcome | null;
      filled_by: Sender;
      support: SupportEntry[];
    })
  | (EvidenceBase & { kind: "proposal"; field: string; quote: string })
  | (EvidenceBase & {
      kind: "contribution";
      option_id: string;
      phase: PhaseId;
      label: string;
      first_proposer: Sender | null;
      adopted?: boolean;
    });

export type ReportElement = {
  element: ElementId;
  name: string;
  level: Level | null;
  summary: string;
  dimension_count: number;
  no_record_count: number;
  low_confidence_count: number;
  adjusted_level: Level | null;
};

export type Dimension = {
  element: ElementId;
  dimension_id: string;
  name: string;
  level: Level | null;
  no_record_reason: NoRecordReason | null;
  rationale: string;
  confidence: Confidence | null;
  reviewer_hint?: string | null;
  adjusted_level: Level | null;
  adjusted_no_record_reason: NoRecordReason | null;
  adjust_memo: string | null;
  evidence_refs: string[];
  evidence: Evidence[];
};

export type ReportSeat = {
  seat: Seat;
  display_name: string;
  avatar_url?: string;
  persona_type?: string;
};

export type ReportSlot = {
  phase: PhaseId;
  value: string;
  option_id: string | null;
  filled_by: Sender;
  resolution: "consensus" | "timeout";
  outcome?: Outcome | null;
  t_min: number;
  first_proposer: Sender | null;
  support: SupportEntry[];
};

export type ReportCard = {
  card_id: string;
  element: ElementId;
  fired_at: number | null;
  delivered: boolean;
  /** 지원자가 예정 시각 전에 칸을 확정해 생기지 않은 카드(선결정). 이전 리포트에는 없다 */
  preempted?: boolean;
  scene: string;
};

export type Note = { id: string; text: string; reviewer_name: string; created_at: string };

export type Report = {
  session_id: string;
  alias: string;
  stage: "draft" | "final";
  position: { name: string; session_minutes: SessionMinutes };
  seats: ReportSeat[];
  evaluation: { run_id: string; rubric_version: string; model: string };
  elements: ReportElement[];
  dimensions: Dimension[];
  facts: Record<string, unknown>;
  contribution_check: {
    phase: PhaseId;
    option_id: string;
    label: string;
    first_proposer: Seat;
    credited_in_proposal: Seat[];
    credit_quote: string | null;
  }[];
  slots: ReportSlot[];
  cards: ReportCard[];
  debrief: {
    cards: {
      card_id: string;
      element: ElementId;
      phase: PhaseId;
      fired: boolean;
      fired_t_min: number | null;
      window: { start_min: number; end_min: number } | null;
    }[];
    survey: { natural: number | null; fair: number | null; free_comment: string | null } | null;
  };
  proposal: {
    submitted_at: string | null;
    auto_submitted: boolean;
    sections: Record<string, string>;
  } | null;
  ai_usage: {
    used: boolean;
    events: {
      call_id: string;
      t_min: number;
      request_quote: string;
      response_quote: string;
      cited_material_ids: string[];
    }[];
  };
  /** 계약 예시는 null만 보인다. 검토 제출 뒤에는 검토자·검토 시각으로 읽는다 */
  review: { reviewer_name: string; reviewed_at: string | null } | null;
  notes: Note[];
  connection_gaps: { from_t_min: number; to_t_min: number }[];
  generated_at: string;
  reviewed_at: string | null;
  purge_at: string | null;
};

export type Adjustment = {
  dimension_id: string;
  level: Level | null;
  no_record_reason?: NoRecordReason;
  memo: string;
};

export type ReviewBody = { adjustments: Adjustment[]; reviewer_name: string };

export type RecordEntry = {
  t_min: number;
  type:
    | "message"
    | "slot"
    | "phase"
    | "assistant_call"
    | "material_view"
    | "proposal_submit"
    | "connection_gap";
  by: Sender;
  summary: string;
  ref: RecordRef;
};
