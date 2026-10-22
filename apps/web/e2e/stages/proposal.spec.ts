// proposal: 기획안 디바운스 저장 → 제출 확인 창 → 잠김 → 디브리핑 이동
import { expect, test } from "@playwright/test";

import { type Seed, internalGet, seed, shot, tab } from "./helpers";

let s: Seed;
test.beforeAll(async () => {
  s = await seed("proposal");
});

test("디바운스 저장 → 제출 확인 → 잠김 → 디브리핑", async ({ page }) => {
  await page.goto(s.link);
  await tab(page, "기획안");
  const sections = page.locator("[data-panel='proposal']");
  // 앞 세 칸은 칸 결과로 채워져 있다
  await expect(sections.locator("[data-section='keyword']")).toHaveValue("물");
  await expect(sections.locator("[data-section='problem']")).toHaveValue(/침수/);
  await shot(page, "proposal", "01-시작");

  const text = "침수는 매년 반복되고 경보로 대피 시간을 벌 수 있어요.";
  await sections.locator("[data-section='rationale']").fill(text);
  await sections.locator("[data-section='idea_credit']").fill("문제 상황은 제가 제안했어요.");
  await expect(page.getByText(/저장됨|방금 저장/)).toBeVisible();
  await expect
    .poll(
      async () =>
        (await internalGet<{ sections: Record<string, string> }>(s.sessionId, "/proposal")).sections
          .rationale,
    )
    .toBe(text);
  await shot(page, "proposal", "02-저장됨");

  await page.getByRole("button", { name: "제출", exact: true }).click();
  // 기대효과가 비어 있어 빈칸 경고가 먼저 뜬다
  await expect(page.getByRole("dialog")).toContainText("비워 둔 칸이 있어요");
  await page.getByRole("dialog").getByRole("button", { name: "그대로 제출" }).click();
  await expect(page.getByRole("dialog")).toContainText("제출하면 수정할 수 없어요");
  await shot(page, "proposal", "03-제출 확인");
  await page.getByRole("dialog").getByRole("button", { name: "제출" }).click();

  await expect(page.getByText("제출했어요. 세션이 끝났습니다.")).toBeVisible();
  await expect(sections.locator("[data-section='rationale']")).toBeDisabled();
  await expect(page.getByLabel("메시지 입력")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "발언 완료" })).toHaveCount(0);
  await shot(page, "proposal", "04-잠김");
  const session = await internalGet<{ status: string }>(s.sessionId);
  expect(["completed", "report_ready"]).toContain(session.status);

  await page.getByRole("button", { name: "나가기" }).click();
  await expect(page.getByRole("heading", { name: "수고하셨습니다" })).toBeVisible();
  await shot(page, "proposal", "05-디브리핑");
});
