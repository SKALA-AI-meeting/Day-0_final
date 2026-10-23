import { describe, expect, it } from "vitest";

import { captionAt, sentences } from "./caption";

describe("자막 문장", () => {
  it("문장 끝 뒤 공백에서 나누고 빈 문장은 버린다", () => {
    expect(sentences("화재가 핵심이에요. 근거는요? 좋아요!  ")).toEqual([
      "화재가 핵심이에요.",
      "근거는요?",
      "좋아요!",
    ]);
  });

  it("재생 위치에 맞는 문장을 고른다(글자 수 비율)", () => {
    const parts = ["가나다라.", "마바."]; // 5자 + 3자 → 첫 문장이 0~62.5%
    expect(captionAt(parts, 0)).toBe("가나다라.");
    expect(captionAt(parts, 0.6)).toBe("가나다라.");
    expect(captionAt(parts, 0.7)).toBe("마바.");
    expect(captionAt(parts, 1)).toBe("마바.");
    expect(captionAt([], 0.5)).toBe("");
  });
});
