// admin-report: ended 시드의 리포트 → 용어 설명·기록 상태 표시 → 차원 1개 조정(메모) → 검토 완료 → 수준 재계산 표시
import { expect, test } from "@playwright/test";

import { ADMIN, type Seed, internalGet, seed, shot } from "./helpers";

let s: Seed;
test.beforeAll(async () => {
  s = await seed("ended");
});

test("리포트 → 차원 조정 → 검토 완료 → 재계산", async ({ page }) => {
  await page.goto("/admin/login");
  await page.getByLabel("이메일").fill(ADMIN.email);
  await page.getByLabel("비밀번호").fill(ADMIN.password);
  await page.getByRole("button", { name: /로그인/ }).click();
  await page.waitForURL(/\/admin\/positions/);

  // 워커가 draft를 만들 때까지(결정적 LLM 모드는 수 초). 화면은 만드는 중에 열면 "report not found"를 보여 준다
  await expect
    .poll(async () => (await internalGet<{ status: string }>(s.sessionId)).status, {
      timeout: 90_000,
    })
    .toBe("report_ready");
  await page.goto(s.adminReportPath!);
  const dimension = page.locator("#element-listening details").first();
  await expect(dimension).toBeVisible({ timeout: 90_000 });
  // 화면 용어는 "세부 행동"이고(결정 A4) 머리에 용어 설명이 있다
  const report = page.locator("article");
  await expect(report).not.toContainText("차원");
  await page.getByText("용어 설명").click();
  await expect(page.locator("#terms")).toContainText("선결정");
  await shot(page, "admin-report", "01-리포트");
  await dimension.locator("summary").click();
  await dimension.getByRole("button", { name: "조정", exact: true }).click();
  const summaryRow = page.getByRole("row", { name: /^경청\/존중/ });
  await expect(summaryRow).toContainText("기록 없음"); // ended 시드는 카드가 하나도 발동하지 않아 차원이 모두 기록 없음
  await expect(summaryRow).toContainText(/장면 없음 \d|반응 없음 \d/); // 기록 없음을 상태별로 센다(결정 A3)
  await expect(dimension.locator("summary")).toContainText(/장면 없음|반응 없음|판단 보류/);
  await page.getByRole("radio", { name: "수준 5" }).click(); // 기록 있는 차원이 이것 하나라 요소 수준도 5
  await page.getByLabel("메모 (필수)").fill("압박 뒤 대응을 원문에서 확인해 두 단계 올림");
  await page.getByRole("button", { name: "저장" }).click();
  await expect(page.getByText("조정한 세부 행동 1개")).toBeVisible();
  await shot(page, "admin-report", "02-조정");

  await page.getByLabel("검토자 이름").fill("박담당");
  await page.getByRole("button", { name: "검토 완료" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "검토 완료" }).click();
  await expect(page.getByText(/검토 완료 · 검토자 박담당/)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("메모 · 압박 뒤 대응을 원문에서 확인해 두 단계 올림")).toBeVisible();
  await expect(page.getByText("담당자 조정").first()).toBeVisible();
  await expect(summaryRow).toContainText("수준 5"); // 요소 수준 재계산
  await shot(page, "admin-report", "03-검토 완료");
  await summaryRow.scrollIntoViewIfNeeded();
  await shot(page, "admin-report", "04-요소 수준 재계산");
});
