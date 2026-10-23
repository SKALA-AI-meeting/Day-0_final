// 완료 기준: 판단 실패 차원은 시스템 오류가 아니라 판단 보류로 보이고, 다른 사유 문구는 그대로다.
// 사유마다 기록 상태(장면 없음·반응 없음·판단 보류)가 척도 계약과 같다(결정 A3).
import { expect, test } from "vitest";

import rubric from "@/mocks/fixtures/internal_rubric.json";

import {
  displayTerms,
  NO_RECORD_TEXT,
  RECORD_STATE,
  RECORD_STATE_TEXT,
  readableSummary,
} from "./format";

test("evaluate_failed는 판단 보류 문구, 나머지 사유는 기존 문구", () => {
  expect(NO_RECORD_TEXT.evaluate_failed).toBe("판단 보류 · 근거 부족 — 원문 기록 확인 권장");
  expect(NO_RECORD_TEXT.no_activity).toBe("지원자 활동 없음");
  expect(Object.values(NO_RECORD_TEXT).join()).not.toContain("시스템:");
});

test("기록 요약 끝의 outcome 토큰을 표시 글자로 바꾼다", () => {
  expect(readableSummary("keyword 확정: 물 (consensus)")).toBe("키워드 확정: 물 · 합의");
  expect(readableSummary("problem 확정: 폭우 (candidate_decision)")).toBe(
    "문제 상황 확정: 폭우 · 지원자 결정",
  );
  expect(readableSummary("solution 확정: 경보 (timeout)")).toBe("해결책 확정: 경보 · 시간 초과");
});

test("사유 문장·상태는 척도 계약(get_rubric no_record_reasons·record_states)과 같다", () => {
  const states = Object.fromEntries(rubric.record_states.map((s) => [s.state, s.label]));
  expect(states).toEqual(RECORD_STATE_TEXT);
  expect(Object.keys(NO_RECORD_TEXT).sort()).toEqual(
    rubric.no_record_reasons.map((r) => r.code).sort(),
  );
  for (const r of rubric.no_record_reasons) {
    const code = r.code as keyof typeof NO_RECORD_TEXT;
    expect(NO_RECORD_TEXT[code]).toBe(r.label);
    expect(RECORD_STATE[code]).toBe(r.state);
  }
  // 선결정은 장면 없음, 자동 제출 공란은 반응 없음
  expect(RECORD_STATE.preempted).toBe("no_scene");
  expect(RECORD_STATE.auto_submitted_blank).toBe("no_response");
});

test("평가 문장의 차원은 세부 행동으로 보인다", () => {
  expect(displayTerms("이 차원은 핵심 장면이 없음. 차원별로 봄, 2개 차원 단위")).toBe(
    "이 세부 행동은 핵심 장면이 없음. 세부 행동별로 봄, 2개 세부 행동 단위",
  );
  // 평가 단위가 아닌 낱말은 그대로
  expect(displayTerms("다차원적으로 검토했고 차원적 접근, 고차원")).toBe(
    "다차원적으로 검토했고 차원적 접근, 고차원",
  );
});
