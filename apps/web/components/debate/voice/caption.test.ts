import { describe, expect, it } from "vitest";

import { heardMessages, sentences, spokenCount } from "./caption";

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

it("재생 전 본문을 숨기고 현재 들리는 범위와 청취 완료 본문만 보여 준다", () => {
  const messages = [
    { id: "a", sender: "p1", content: "첫 문장. 둘째 문장." },
    { id: "b", sender: "p2", content: "아직 안 들은 말" },
    { id: "c", sender: "candidate", content: "내 발언" },
  ];
  expect(heardMessages(messages, [], { id: "a", text: "첫 문장." })).toEqual([
    { ...messages[0], content: "첫 문장." },
    messages[2],
  ]);
  expect(heardMessages(messages, ["a"], null)).toEqual([messages[0], messages[2]]);
});
