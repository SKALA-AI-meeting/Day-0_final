// keyword: 후보 버튼 확정 → 결정 사항 칸 → 단계 전환 → 자료 탭 열림
import { expect, test } from "@playwright/test";

import { type Seed, enterRoom, internalGet, seed, shot, tab } from "./helpers";

let s: Seed;
test.beforeAll(async () => {
  s = await seed("keyword");
});

test("후보 확정 → 결정 사항 → 문제 상황 단계 → 자료 열림", async ({ page }) => {
  await page.goto(s.link);
  await enterRoom(page);
  await tab(page, "결정 사항");
  const keywords = page.getByRole("radiogroup", { name: "키워드 후보" });
  await expect(keywords.getByRole("radio")).toHaveCount(4);
  await tab(page, "자료");
  await expect(page.getByText("지역별 물 부족 통계")).toHaveCount(0); // 확정 전에는 자료가 닫혀 있다
  await tab(page, "결정 사항");
  await shot(page, "keyword", "01-확정 전");

  await keywords.getByRole("radio", { name: "물" }).click();
  await page.getByRole("button", { name: "이걸로 정하기" }).click();
  await shot(page, "keyword", "02-확인 창");
  await page.getByRole("dialog").getByRole("button", { name: "네, 정할게요" }).click();

  const result = page.locator('[data-slot="keyword"]');
  await expect(result).toContainText("물");
  await expect(result).toContainText("지원자 결정");
  await expect(result).toContainText("정한 사람 · 나");
  await expect(page.locator('[data-slot-input="problem"]')).toBeVisible();
  await shot(page, "keyword", "03-확정");

  await tab(page, "자료");
  await expect(page.getByText("지역별 물 부족 통계")).toBeVisible();
  await shot(page, "keyword", "04-자료");
  const session = await internalGet<{ phase: string }>(s.sessionId);
  expect(session.phase).toBe("problem");
});
