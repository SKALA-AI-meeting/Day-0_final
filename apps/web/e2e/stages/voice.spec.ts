import { expect, test } from "@playwright/test";
import { internalGet, seed, setTranscript, shot } from "./helpers";

test("음성 회의: 채팅 UI 없음·AI 종료 뒤 녹음·60초 자동 제출 1회", async ({ page }) => {
  const s = await seed("keyword");
  await setTranscript("물에 대한 발언입니다");
  await page.clock.install();
  await page.goto(s.link);
  await expect(page.getByRole("region", { name: "음성 회의" })).toBeVisible();
  await expect(page.getByLabel("메시지 입력")).toHaveCount(0);
  await expect(page.getByRole("log")).toHaveCount(0);
  const finish = page.getByRole("button", { name: "발언 완료" });
  await expect(finish).toBeVisible({ timeout: 30000 });
  await expect(page.getByText("내 발언 60초 남음")).toBeVisible();
  await shot(page, "voice", "01-내 발언");
  await page.waitForTimeout(500);
  const upload = page.waitForRequest((r) => r.url().endsWith("/voice/audio"));
  await page.clock.fastForward(61000);
  expect((await upload).postDataJSON().stop_reason).toBe("timeout");
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
