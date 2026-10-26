// 완료 기준: 고른 키워드가 테마가 되고, 그 뒤 단계는 같은 테마 안의 톤으로 바뀐다
import { expect, test } from "vitest";

import { moodFor, themeKey } from "./theme";

const options = [
  { option_key: "water", label: "물" },
  { option_key: "fire", label: "불" },
  { option_key: "tree", label: "나무" },
  { option_key: "wind", label: "바람" },
];
const slot = (value: string | null, status: "empty" | "confirmed" = "confirmed") => ({
  phase: "keyword" as const,
  status,
  value,
  filled_by: null,
  resolution: null,
  t_min: null,
});

test("확정된 키워드 라벨을 테마 키로 바꾸고, 미확정·자유 키워드는 테마가 없다", () => {
  expect(themeKey({ slots: [slot("물")], keyword_options: options })).toBe("water");
  expect(themeKey({ slots: [slot("바람")], keyword_options: options })).toBe("wind");
  expect(themeKey({ slots: [slot("물", "empty")], keyword_options: options })).toBeNull();
  expect(themeKey({ slots: [slot("우주")], keyword_options: options })).toBeNull();
});

test("인사 동안은 dawn, 키워드 단계는 keyword, 그 뒤는 테마-단계, 끝나면 테마-debrief", () => {
  const base = { intro: false, ended: false, theme: "fire" as const };
  expect(moodFor({ ...base, intro: true, phase: "keyword" })).toBe("dawn");
  expect(moodFor({ ...base, phase: "keyword" })).toBe("keyword");
  expect(moodFor({ ...base, phase: "problem" })).toBe("fire-problem");
  expect(moodFor({ ...base, phase: "proposal" })).toBe("fire-proposal");
  expect(moodFor({ ...base, ended: true, phase: "proposal" })).toBe("fire-debrief");
  expect(moodFor({ ...base, theme: null, phase: "solution" })).toBe("solution");
});
