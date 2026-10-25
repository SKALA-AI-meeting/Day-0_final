// solution: 그 단계 카드 전달 뒤 지원자의 분명한 동의로 AI 합의 공지 → 칸 확정, 침묵 시 advance{to: idle} 뒤 초대 메시지 1건(silent),
// 반대가 남은 상태의 지원자 혼자 확정(solo_confirm)
import { expect, test } from "@playwright/test";

import { type Seed, VOICE_ENABLED, advance, internalGet, say, seed, settle, shot } from "./helpers";

type Slot = {
  phase: string;
  option_id: string | null;
  status: string;
  value: string;
  filled_by: string;
  resolution: string;
  support: { option_id: string; supporters: string[] }[];
};
const slotOf = async (id: string, phase: string) =>
  (await internalGet<Slot[]>(id, "/slots")).find((x) => x.phase === phase);

const S2 = "주민 절수·보상 캠페인";
const S3 = "빗물 저금통·투수 포장 같은 생활 인프라";

let s: Seed;
test.beforeEach(async () => {
  s = await seed("solution");
});

test("AI 합의 채우기: 공지 메시지 → 결정 사항 칸 확정 → 기획안 단계", async ({ page }) => {
  // 발언 4번·카드 대기·다음 동료 차례 대기가 겹쳐 음성에서는 기본 2분을 넘을 수 있다
  test.slow();
  await page.goto(s.link);
  await expect(page.locator('[data-slot-input="solution"]')).toBeVisible();
  for (const text of [
    `저는 ${S3}가 좋다고 생각해요.`, // 지원자 안 제안(서지안 설득 1회째)
    "윤하람님은 어떻게 생각하세요?", // 소극형 지명 → 소극형이 자기 안(S3)을 밝힌다
    `${S2}은 반대예요. 효과가 오래 가지 않아요.`, // 반대만 한 발언은 설득 횟수에 세지 않는다
  ]) {
    await say(page, text);
    await settle(s.sessionId);
  }
  // 그 단계 카드(머뭇거림·반론)가 전달되기 전에는 합의 채움을 보류한다(card_pending). 반론 카드
  // 시각까지 당기면 앞선 머뭇거림 카드도 함께 온다. 카드는 다음 스케줄러 틱에 들어가고 음성에서는
  // 앞 발언 재생이 끝난 뒤 게시된다
  await advance(s.sessionId, "card:persuasion_solution");
  await expect
    .poll(
      async () =>
        (await internalGet<{ card_id: string }[]>(s.sessionId, "/observations")).map(
          (c) => c.card_id,
        ),
      { timeout: VOICE_ENABLED ? 60000 : 20000 },
    )
    .toEqual(expect.arrayContaining(["inclusion_silence", "persuasion_solution"]));
  await settle(s.sessionId);
  // S3에 대한 분명한 동의(유보 없음): 설득 2회째라 서지안이 S3로 옮기고(지원자 포함 3/4), 지원자의
  // 그 단계 최신 발언이 S3 지지이므로 다음 실행에서 AI 동료가 합의를 채운다
  await say(page, `저도 ${S3}에 동의해요. 그쪽으로 가요.`);
  await shot(page, "solution", "01-합의 직전");

  // 서지안의 이동 발언과 채움이 서로 다른 실행이라(같은 실행의 AI 이동으로는 채우지 않는다) 음성에서는
  // 다음 동료 차례(차례 유지 시간 뒤)까지 기다릴 수 있다
  await expect
    .poll(
      async () =>
        (await internalGet<{ content: string }[]>(s.sessionId, "/messages")).some((m) =>
          /그럼 .+로 정리할게요/.test(m.content),
        ),
      { timeout: VOICE_ENABLED ? 90000 : 20000 },
    )
    .toBeTruthy();
  const result = page.locator('[data-slot="solution"]');
  await expect(result).toContainText("합의");
  await expect(result).toContainText(S3);
  await expect(page.getByRole("button", { name: "이걸로 정하기" })).toHaveCount(0);
  await shot(page, "solution", "02-합의 채움");
  const slot = await slotOf(s.sessionId, "solution");
  expect(slot).toMatchObject({ status: "confirmed", resolution: "consensus" });
  expect(["p1", "p2", "p3"]).toContain(slot?.filled_by);
});

test("silent: 침묵하면 idle 뒤 비판형 초대 메시지가 1건", async ({ page }) => {
  await page.goto(s.link);
  await expect(page.locator('[data-slot-input="solution"]')).toBeVisible();
  await advance(s.sessionId, "idle");
  // 초대는 다음 스케줄러 틱(5초)에 들어가고, 음성에서는 단계 시작 동료 발언의 재생이 끝난 뒤 게시돼
  // 기본 15초를 넘길 수 있다(관측 약 16~20초)
  await expect
    .poll(
      async () =>
        (await internalGet<{ is_invite: boolean }[]>(s.sessionId, "/messages")).filter(
          (m) => m.is_invite,
        ).length,
      { timeout: VOICE_ENABLED ? 45000 : 15000 },
    )
    .toBe(1);
  await shot(page, "solution", "03-침묵 초대");
  const invites = (
    await internalGet<{ is_invite: boolean; sender: string; phase: string }[]>(
      s.sessionId,
      "/messages",
    )
  ).filter((m) => m.is_invite && m.phase === "solution");
  expect(invites).toHaveLength(1);
  expect(invites[0].sender).toBe("p3");
});

test("solo_confirm: 반대가 남아도 지원자가 혼자 확정하면 칸이 확정된다", async ({ page }) => {
  await page.goto(s.link);
  for (const text of [
    `저는 ${S3}가 좋다고 생각해요.`,
    `${S2}은 반대예요. 효과가 오래 가지 않아요.`,
  ]) {
    await say(page, text);
    await settle(s.sessionId);
  }
  await page.locator('[data-slot-input="solution"] textarea').fill(S3);
  await page.getByRole("button", { name: "이걸로 정하기" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "네, 정할게요" }).click();
  const result = page.locator('[data-slot="solution"]');
  await expect(result).toContainText(S3);
  await expect(result).toContainText("정한 사람 · 나");
  // outcome=candidate_decision: 결정 사항과 진행바 모두 "지원자 결정"
  await expect(result).toContainText("지원자 결정");
  await expect(page.getByRole("list", { name: "단계 진행" })).toContainText("지원자 결정");
  await shot(page, "solution", "04-혼자 확정");
  const slot = await slotOf(s.sessionId, "solution");
  expect(slot).toMatchObject({ filled_by: "candidate", resolution: "consensus" });
  // 확정 안(S3)은 지원자를 포함해도 합의 조건인 3명에 못 미치고, 다른 안(고집형의 S1 등)에는 지지자가 남아 있다
  const confirmed = slot?.support.find((x) => x.option_id === slot.option_id);
  expect(confirmed?.supporters).toContain("candidate");
  expect(confirmed!.supporters.length).toBeLessThan(3);
  const others = slot?.support.filter((x) => x.option_id !== slot.option_id) ?? [];
  expect(others.some((x) => x.supporters.length > 0)).toBe(true);
});
