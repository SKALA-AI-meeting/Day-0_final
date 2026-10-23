// 관리자 화면 표기 규칙: 시각(오피스 시각), 단계·유형·사유 문장(rubric.md 5장), 차원 정의, 삭제 예정일.
import { resolutionLabel, type Outcome } from "@/lib/resolution";

import type { NoRecordReason, PhaseId, RecordState, Sender } from "./types";

const dateTime = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const dateOnly = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  month: "long",
  day: "numeric",
});

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "–" : dateTime.format(date);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "–";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "–" : dateOnly.format(date);
}

/** "1월 19일 삭제 예정"(세션 종료 + 90일) */
export function purgeText(iso: string | null | undefined) {
  return iso ? `${formatDate(iso)} 삭제 예정` : null;
}

/** 오피스 시각 = 09:00 + t_min × 60 / session_minutes, 분 단위 내림 "HH:MM"(docs/contracts/README.md 공통) */
export function officeClock(tMin: number, sessionMinutes: number) {
  const minutes = Math.floor(9 * 60 + (tMin * 60) / sessionMinutes);
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

/** 기록 없음 사유 → 화면 문장(rubric.md 5장, design.md 표) */
export const NO_RECORD_TEXT: Record<NoRecordReason, string> = {
  no_activity: "지원자 활동 없음",
  evaluate_failed: "판단 보류 · 근거 부족 — 원문 기록 확인 권장",
  not_applicable: "해당 없음",
  preempted: "선결정 · 카드 전에 지원자가 칸을 확정",
  event_not_fired: "이벤트 미발생",
  auto_submitted_blank: "자동 제출로 기여 칸 미작성",
};

/** 기록 없음 사유 → 상태(rubric.md 5장 "상태" 열, rubric.yaml no_record_reasons[].state) */
export const RECORD_STATE: Record<NoRecordReason, RecordState> = {
  no_activity: "no_response",
  evaluate_failed: "failed",
  not_applicable: "no_scene",
  preempted: "no_scene",
  event_not_fired: "no_scene",
  auto_submitted_blank: "no_response",
};

/** 상태 이름(rubric.yaml record_states). 수준이 있는 "반응 있음"은 수준 칩이 대신한다 */
export const RECORD_STATE_TEXT: Record<RecordState, string> = {
  no_scene: "장면 없음",
  no_response: "반응 없음",
  failed: "판단 보류",
};

export const stateOf = (reason: NoRecordReason) => RECORD_STATE_TEXT[RECORD_STATE[reason]];

/**
 * 평가 문장(판단 이유·요약)의 내부 용어 "차원"을 화면 용어 "세부 행동"으로(결정 A4).
 * 평가 단위로 쓰인 낱말만 바꾼다: 앞에 한글이 붙은 말("다차원")과 "차원적"은 그대로 둔다.
 * 두 말 모두 받침으로 끝나 뒤 조사("은·이·을·별" 등)가 그대로 맞는다
 */
export const displayTerms = (text: string) => text.replace(/(?<![가-힣])차원(?!적)/g, "세부 행동");

/** 리포트 머리의 용어 설명(rubric.md 8장) */
export const REPORT_TERMS: { term: string; text: string }[] = [
  {
    term: "세부 행동",
    text: "요소(설득·경청·배려·수용·정직) 하나를 나눠 보는 관찰 단위입니다. 요소마다 3~4개이고 각각 따로 판단합니다.",
  },
  {
    term: "수준 1~5",
    text: "공개된 행동 기준표에서 지원자 행동에 가장 가까운 단계입니다. 요소 수준은 그 요소 세부 행동 수준의 평균을 반올림한 값이고 요소끼리 더하지 않습니다.",
  },
  {
    term: "확신도",
    text: "근거가 얼마나 분명한지입니다. 낮음이면 근거가 약하니 원문 기록을 확인해 주세요.",
  },
  {
    term: "장면 없음",
    text: "판단할 장면이 생기지 않아 수준을 매기지 않았습니다(해당 없음, 이벤트 미발생, 선결정).",
  },
  {
    term: "선결정",
    text: "지원자가 카드 예정 시각 전에 그 단계 칸을 직접 확정해 설계된 장면이 생기지 않은 경우입니다. 장면 없음의 한 종류입니다.",
  },
  {
    term: "반응 없음",
    text: "장면은 있었지만 지원자 반응이 없었습니다. 반응이 없다는 것만으로 수준을 낮추지 않습니다.",
  },
  {
    term: "판단 보류",
    text: "AI 평가가 근거 있는 판단을 내지 못한 경우입니다. 원문 기록을 보고 직접 판단해 주세요.",
  },
  {
    term: "설계된 장면",
    text: "같은 채용 건의 모든 지원자가 같은 시각·같은 방식으로 겪도록 설계한 장면(동료의 반대·압박·머뭇거림 등)입니다.",
  },
];

export const PHASE_TEXT: Record<PhaseId, string> = {
  keyword: "키워드",
  problem: "문제 상황",
  solution: "해결책",
  proposal: "기획안 작성",
};

export const ORIGIN_TEXT = { spontaneous: "자발", invited: "초대 후" } as const;

/** 페르소나 유형의 한국어 이름(관리자 리포트에서만 보인다) */
export const PERSONA_TEXT: Record<string, string> = {
  stubborn: "고집형",
  quiet: "소극형",
  critic: "비판형",
};

/** 지원자 목록 칩의 요소 짧은 이름("설득 3") */
export const ELEMENT_SHORT: Record<string, string> = {
  persuasion: "설득",
  listening: "경청",
  inclusion: "배려",
  flexibility: "수용",
  honesty: "정직",
};

/** 기획안 6칸(docs/product/scenario.md 템플릿) */
export const PROPOSAL_FIELDS: { id: string; label: string }[] = [
  { id: "keyword", label: "키워드" },
  { id: "problem", label: "문제 상황" },
  { id: "solution", label: "해결책" },
  { id: "rationale", label: "근거" },
  { id: "expected_effect", label: "기대효과" },
  { id: "idea_credit", label: "아이디어 기여" },
];

export const proposalFieldLabel = (id: string) =>
  PROPOSAL_FIELDS.find((f) => f.id === id)?.label ?? id;

/** 자료 제목(Report에는 id만 온다. 원천은 scenarios/missions/debate/materials/의 제목) */
export const MATERIAL_TITLES: Record<string, string> = {
  water_stats: "지역별 물 부족 통계",
  water_flood_cases: "도심 침수 사례",
  water_rain_reuse: "빗물 재이용 사례",
  water_tap_survey: "수돗물 음용률 조사",
};

export const materialTitle = (id: string) => MATERIAL_TITLES[id] ?? id;

/** 차원 정의 한 줄(rubric.md 3장, scenarios/missions/debate/rubric.yaml) */
export const DIMENSION_DEFINITION: Record<string, string> = {
  pers_reasoning: "자기 안·의견에 이유·사례·수치·자료를 붙이고 그 이유가 주장과 실제로 연결되는가",
  pers_counter_handling:
    "자기 안에 반론·약점 질문이 왔을 때 그 논점에 직접 답하고 근거를 보강하거나 안을 조건부로 조정하는가",
  pers_comparison: "상대 안의 논점을 정확히 되짚고 장점을 인정하면서 기준을 세워 안들을 비교하는가",
  pers_convergence:
    "흩어진 의견을 정리하고 절충안이나 비교 결론을 제시해 근거를 바탕으로 결정 쪽으로 논의를 모으는가",
  listen_engage: "상대가 한 말(특히 지원자 안을 일축·반대한 말)에 실제로 응답하며 대화를 잇는가",
  listen_understand:
    "상대의 주장·근거를 정확히 이해하려 하는가(요약·되짚기·의도와 근거 질문). 동의 여부와 무관",
  listen_acknowledge:
    "반대하는 상대 안·의견에서도 타당한 점을 인정하는가. 인정 후 반대를 이어가도 된다",
  listen_civility:
    "반대를 사람이 아니라 안에 향하게 하고, 반복 압박에도 비난·조롱·짜증 없이 말하는가",
  incl_notice_invite:
    "머뭇거림·짧은 맞장구 같은 약한 신호를 알아차리고 이름을 부르거나 열린 질문으로 말할 자리를 만드는가",
  incl_follow_through:
    "덜 말한 동료가 의견을 말한 뒤 그 내용을 되짚고, 묻고, 덧붙이거나 근거를 들어 검토해 논의에 올리는가",
  incl_shared_decision:
    "칸을 확정하거나 방향을 정할 때 아직 의견을 내지 않은 참여자가 있는지 확인하고, 특정인이 대화를 독점하지 않게 하는가(세 단계 전체)",
  incl_safe_tone:
    "덜 말한 사람에게 압박·평가·재촉 없이 말을 걸고, 짧은 대답도 받아들여 다시 말할 수 있게 하는가",
  flex_openness:
    "결정 전, 다른 안의 근거를 실제로 다루고 타당하면 입장을 조정·통합하는가. 입장을 바꿨는지가 아니라 상대 근거에 반응했는지를 본다",
  flex_decision_respect:
    "의견이 갈린 상태에서 결정이 내려지는 방식을 존중하는가. 단독 확정·강행 없이 동의를 확인하는가",
  flex_acceptance: "자기 안이 채택되지 않은 결정을 재론·이탈·비난 없이 받아들이는가",
  flex_forward:
    "자기 안이 채택되지 않은 뒤 다음 단계에서 결정된 결과를 바탕으로 실질적으로 기여하는가. 양이 아니라 기여의 내용을 본다",
  hon_credit_record:
    "최종 기획안 아이디어 기여 칸(과 본문의 기여 서술)에 채택된 아이디어의 실제 제안자를 맞게 적고 자기 원안·보완·동의를 구분하는가",
  hon_source_in_talk:
    "토론 채팅에서 다른 참여자의 아이디어를 언급·지지·확장할 때 그 사람의 것으로 다루고 자기 것처럼 다시 내놓지 않는가",
  hon_process_truth:
    "칸 확정과 기획안에서 합의 여부·지지 상황·시간 초과 같은 결정 과정을 사실대로 말하고 적는가",
};

/** 사람 이름 표기: 자리 id → 표시 이름. 시스템은 "시스템" */
export function nameOf(names: Partial<Record<Sender, string>>, who: Sender | null | undefined) {
  if (!who) return "–";
  if (who === "system") return "시스템";
  return names[who] ?? who;
}

/** 전체 기록 요약 문장의 코드 값(단계·결과·자료 id)을 사람 말로 바꾼다 */
export function readableSummary(summary: string) {
  return summary
    .replace(
      /^(keyword|problem|solution|proposal) 단계 시작$/,
      (_, p: PhaseId) => `${PHASE_TEXT[p]} 단계 시작`,
    )
    .replace(
      /^(keyword|problem|solution) 확정: (.*) \((consensus|candidate_decision|timeout)\)$/,
      (_, p: PhaseId, v: string, r: Outcome) =>
        `${PHASE_TEXT[p]} 확정: ${v} · ${resolutionLabel({ resolution: "consensus", outcome: r }) ?? ""}`,
    )
    .replace(/^자료 열람: (\w+)$/, (_, id: string) => `자료 열람: ${materialTitle(id)}`);
}
