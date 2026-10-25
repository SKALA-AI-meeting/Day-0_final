// problem: 수용 카드 발언 표시, advance{to: deadline} 뒤 시간 초과 결과(적용 규칙별 시스템 안내)와 입력 잠김
import { expect, test } from "@playwright/test";

import { type Seed, VOICE_ENABLED, advance, internalGet, say, seed, shot } from "./helpers";

let s: Seed;
test.beforeAll(async () => {
  s = await seed("problem");
});

test("수용 카드 발언 → 시간 초과 채우기 → 입력 닫힘", async ({ page }) => {
  await page.goto(s.link);
  await expect(page.locator('[data-slot-input="problem"]')).toBeVisible();
  await shot(page, "problem", "01-시작");

  if (VOICE_ENABLED) {
    await say(page, "가뭄 문제도 함께 살펴봐요.");
  }
  // 카드 예정 시각(flexibility_majority·listening_pressure)을 당기면 고집형의 기준안 발언이 온다
  await advance(s.sessionId, "card:flexibility_majority");
  // FakeLLM 고정 입장 문장: 기준안(가뭄)을 민다. 고집형·비판형 둘 다 같은 기준안을 밝힌다.
  // 카드는 다음 스케줄러 틱(5초)에 들어가고, 음성에서는 앞 동료 발언의 생성·재생이 끝난 뒤 게시돼
  // 기본 15초보다 오래 걸릴 수 있다(관측 약 20초)
  await expect
    .poll(
      async () =>
        (await internalGet<{ sender: string; content: string }[]>(s.sessionId, "/messages")).some(
          (m) => m.sender === "p1" && m.content.includes("가뭄"),
        ),
      { timeout: VOICE_ENABLED ? 45000 : 15000 },
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
  expect(problem).toMatchObject({ resolution: "timeout", filled_by: "system" });
});
