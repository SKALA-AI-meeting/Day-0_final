import { describe, expect, it } from "vitest";

import { sentences, spokenCount } from "./caption";

describe("한 문장씩 보이기", () => {
  it("문장 끝 뒤 공백에서 나누고 빈 문장은 버린다", () => {
    expect(sentences("화재가 핵심이에요. 근거는요? 좋아요!  ")).toEqual([
      "화재가 핵심이에요.",
      "근거는요?",
      "좋아요!",
    ]);
  });

  it("재생 위치까지 말한 문장 수를 센다(글자 수 비율)", () => {
    const parts = ["가나다라.", "마바."]; // 5자 + 3자 → 첫 문장이 0~62.5%
    expect(spokenCount(parts, 0)).toBe(1);
    expect(spokenCount(parts, 0.6)).toBe(1);
    expect(spokenCount(parts, 0.7)).toBe(2);
    expect(spokenCount(parts, 1)).toBe(2);
    expect(spokenCount([], 0.5)).toBe(0);
  });
});
