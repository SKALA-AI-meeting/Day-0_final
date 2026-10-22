// problem: 수용 카드 발언 표시, advance{to: deadline} 뒤 시간 초과 결과(R7 문구)와 입력 잠김
import { expect, test } from "@playwright/test";

import { type Seed, advance, internalGet, seed, shot } from "./helpers";

let s: Seed;
test.beforeAll(async () => {
  s = await seed("problem");
});

test("수용 카드 발언 → 시간 초과 채우기 → 입력 닫힘", async ({ page }) => {
  await page.goto(s.link);
  await expect(page.locator('[data-slot-input="problem"]')).toBeVisible();
  await shot(page, "problem", "01-시작");

  await expect(page.getByRole("button", { name: "발언 완료" })).toBeVisible();
  await page.waitForTimeout(400);
  await page.getByRole("button", { name: "발언 완료" }).click();
  // 카드 예정 시각(flexibility_majority·listening_pressure)을 당기면 고집형의 기준안 발언이 온다
  await advance(s.sessionId, "card:flexibility_majority");
  // FakeLLM 고정 입장 문장: 기준안(가뭄)을 민다. 고집형·비판형 둘 다 같은 기준안을 밝힌다
  await expect
    .poll(async () =>
      (await internalGet<{ sender: string; content: string }[]>(s.sessionId, "/messages")).some(
        (m) => m.sender === "p1" && m.content.includes("가뭄"),
      ),
    )
    .toBeTruthy();
  // 관찰(record_observation)은 카드 발언을 게시한 뒤에 기록되므로 잠시 기다린다
  await expect
    .poll(async () =>
      (await internalGet<{ card_id: string }[]>(s.sessionId, "/observations"))
        .map((c) => c.card_id)
        .sort(),
    )
    .toEqual(expect.arrayContaining(["flexibility_majority", "listening_pressure"]));
  const cardMessage = (
    await internalGet<{ sender: string; card_ids: string[] }[]>(s.sessionId, "/messages")
  ).find((m) => m.sender === "p1" && m.card_ids.includes("flexibility_majority"));
  expect(cardMessage).toBeTruthy();
  await shot(page, "problem", "02-수용 카드");

  await advance(s.sessionId, "deadline");
  const result = page.locator('[data-slot="problem"]');
  await expect(result).toContainText("시간 초과");
  await expect(page.locator('[data-slot-input="problem"]')).toHaveCount(0);
  await expect(page.locator('[data-slot-input="solution"]')).toBeVisible();
  await shot(page, "problem", "03-시간 초과");

  const slots = await internalGet<{ phase: string; resolution: string; filled_by: string }[]>(
    s.sessionId,
    "/slots",
  );
  const problem = slots.find((x) => x.phase === "problem");
  expect(problem).toMatchObject({ resolution: "timeout" });
  expect(["p1", "p2", "p3"]).toContain(problem?.filled_by);
});
