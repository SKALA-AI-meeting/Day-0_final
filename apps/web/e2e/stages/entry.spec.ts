// entry: 입장 → 동의(표시 이름) → 대기 화면 참여자 이름·아바타 → 시작
import { expect, test } from "@playwright/test";

import { type Seed, enterRoom, internalGet, seed, shot } from "./helpers";

let s: Seed;
test.beforeAll(async () => {
  s = await seed("entry");
});

test("입장 → 동의 → 대기(참여자) → 시작", async ({ page }) => {
  await page.goto(s.link);
  await expect(page.getByRole("heading", { name: "새 프로젝트 팀 토론" })).toBeVisible();
  await shot(page, "entry", "01-입장");
  await page.getByRole("button", { name: "다음" }).click();

  await expect(page.getByRole("heading", { name: "시작 전에 확인해 주세요" })).toBeVisible();
  await page.getByLabel("토론방에서 보일 이름").fill("하늘");
  await page.getByLabel("위 내용을 읽었고 동의합니다").check();
  await shot(page, "entry", "02-동의");
  await page.getByRole("button", { name: "동의하고 계속" }).click();

  await expect(page.getByRole("heading", { name: "시작할 준비가 됐어요" })).toBeVisible();
  for (const name of ["강한결", "윤하람", "서지안"]) {
    await expect(page.getByText(name).first()).toBeVisible();
  }
  await expect(page.getByText("하늘")).toBeVisible();
  await expect(page.getByRole("img", { name: /강한결|윤하람|서지안/ })).toHaveCount(3);
  await shot(page, "entry", "03-대기");
  await page.getByRole("button", { name: "시작하기" }).click();

  // 시작하면 프로필 카드만 크게 보이고(인사 차례), 지원자 차례가 오면 토론방이 열린다
  await expect(page.getByRole("region", { name: "참여자" })).toBeVisible();
  await enterRoom(page);
  await shot(page, "entry", "04-시작");
  const session = await internalGet<{ status: string; display_name: string; phase: string }>(
    s.sessionId,
  );
  expect(session).toMatchObject({ status: "in_progress", display_name: "하늘", phase: "keyword" });
});
