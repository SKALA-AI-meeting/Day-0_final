import { expect, test } from "@playwright/test";
import { VOICE_ENABLED, internalGet, say, seed, shot } from "./helpers";

test.skip(!VOICE_ENABLED, "음성 프로필 전용");

test("음성 회의: 채팅 UI 없음·직접 발언 시작과 제출 1회", async ({ page }) => {
  const s = await seed("keyword");
  await page.goto(s.link);
  await expect(page.getByRole("region", { name: "음성 회의" })).toBeVisible();
  await expect(page.getByLabel("메시지 입력")).toHaveCount(0);
  await expect(page.getByRole("log")).toHaveCount(0);
  await expect(page.getByText("최근 발언")).toHaveCount(0);
  await expect(page.locator("#voice-transcript")).toHaveCount(0);
  // 숨은 프로필 칸(인사 화면용)은 빼고 보이는 회의 칸만 센다
  await expect(page.locator("[data-seat]:visible")).toHaveCount(4);
  await say(page, "물에 대한 발언입니다");
  await shot(page, "voice", "01-내 발언");
  await expect(page.getByRole("button", { name: "발언 제출" })).toHaveCount(0);
  await expect
    .poll(
      async () =>
        (await internalGet<{ sender: string; content: string }[]>(s.sessionId, "/messages")).filter(
          (m) => m.sender === "candidate" && m.content === "물에 대한 발언입니다",
        ).length,
    )
    .toBe(1);
  await shot(page, "voice", "02-자동 제출");
});
