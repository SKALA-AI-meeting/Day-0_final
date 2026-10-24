// 목업 캡처(G-M): NEXT_PUBLIC_MOCK=1로 띄운 web에서 15분 데모 세션(/s/demo)을 한 번 돌리며 화면을 남긴다.
// 데모 시계는 ?speed=20으로 빠르게 돌리고, 입력 중 장면만 실제 속도로 늦춰 찍는다.
// 저장 위치: 저장소 루트의 tests/e2e/records/mock-ui-<YYYYMMDD-HHMM>/ (라이트 9장 + 다크 1장)
//
//   NEXT_PUBLIC_MOCK=1 pnpm dev            (다른 터미널, 기본 3000)
//   node e2e/capture-mock.mjs              (환경변수: WEB_URL, OUT_DIR, HEADED=1)
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "..", "..", "..");
const WEB = process.env.WEB_URL ?? "http://localhost:3000";
const SPEED = 20;

const d = new Date();
const pad = (n) => String(n).padStart(2, "0");
const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
const OUT = process.env.OUT_DIR ?? join(REPO, "tests", "e2e", "records", `mock-ui-${stamp}`);
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", headless: process.env.HEADED !== "1" });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
  colorScheme: "light",
  locale: "ko-KR",
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));

const saved = [];
async function shot(name, options = {}) {
  // 조작 패널은 캡처에 넣지 않는다
  await page.addStyleTag({ content: "[data-mock-controls],nextjs-portal{display:none!important}" });
  await page.waitForTimeout(250); // 도착 모션(160ms)이 끝난 뒤
  const file = `${name}.png`;
  await page.screenshot({ path: join(OUT, file), ...options });
  saved.push(file);
  console.log(`  ✓ ${file}`);
}

/** 모의 서버 조작(조작 패널과 같은 함수) */
const mock = (fn, arg) =>
  page.evaluate(
    ([body, value]) => {
      const c = window.__dayzeroMock();
      return new Function("c", "value", body)(c, value);
    },
    [fn, arg],
  );
const minutes = () => mock("return c.minutes");
const phase = () => mock("return c.phase");

async function waitFor(check, label, timeout = 60_000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    if (await check()) return;
    await page.waitForTimeout(50);
  }
  throw new Error(`시간 초과: ${label}`);
}

// 오른쪽 영역이 숨어 있으면 상단 바에서 켠 뒤 제목(묶음은 탭)을 누른다(e2e/stages/helpers.ts tab과 같음)
const LIBRARY = "자료·AI 어시스턴트";
const tab = async (name) => {
  const inLibrary = name === "자료" || name === "AI 어시스턴트";
  const area = inLibrary ? LIBRARY : name;
  const region = page.getByRole("region", { name: area, exact: true });
  if (!(await region.isVisible())) {
    await page
      .getByRole("group", { name: "영역 보이기" })
      .getByRole("button", { name: area })
      .click();
  }
  if (inLibrary) await region.getByRole("tab", { name }).click();
  else await region.getByRole("heading", { name }).click();
};

try {
  console.log(`캡처 → ${OUT}`);
  await page.goto(`${WEB}/s/demo?speed=${SPEED}`);

  // 1. 입장
  await page.getByRole("button", { name: "다음" }).waitFor();
  await shot("01-entry");
  await page.getByRole("button", { name: "다음" }).click();

  // 2. 동의: 표시 이름(기본값 초대 이름)을 바꾸고 동의
  const name = page.getByRole("textbox");
  await name.fill("김지원");
  await page.getByRole("checkbox").check();
  await shot("02-consent", { fullPage: true });
  await page.getByRole("button", { name: "동의하고 계속" }).click();

  // 3. 대기: 시작 전 /state로 그린 팀 소개
  await page.getByRole("button", { name: "시작하기" }).waitFor();
  await shot("03-waiting", { fullPage: true });
  await page.getByRole("button", { name: "시작하기" }).click();
  // 인사 화면(프로필 카드)을 찍고 바로 토론방을 연다
  await page.getByRole("region", { name: "참여자" }).waitFor();
  await shot("03b-intro");
  await page.getByRole("button", { name: "바로 대화하기" }).click();
  await page.getByRole("region", { name: "토론방" }).waitFor();

  // 4. 해결책 단계 시작까지 대본 재생(키워드 지원자 확정 → 자료 열림 → 문제 상황 합의 채우기)
  await waitFor(async () => (await phase()) === "solution", "해결책 단계");
  await page.waitForTimeout(400);
  await tab("결정 사항");
  await page.getByLabel("해결책 입력").fill("하수관 센서 경보에 빗물 저금통을 함께 두는 침수 대응");
  await shot("04-decisions");

  // 5. 자료 탭: 목록 → Markdown 뷰어
  await tab("자료");
  await page.getByRole("button", { name: "도심 침수 사례" }).click();
  await page.getByRole("heading", { name: "도심 침수 사례" }).waitFor();
  await shot("05-materials");

  // 6. AI 어시스턴트: 요청·인용·복사
  await tab("AI 어시스턴트");
  await page
    .getByLabel("AI 어시스턴트에게 요청")
    .fill("도심 침수 사례에서 센서 경보 효과가 있었던 곳을 알려 줘");
  await page.locator('[data-panel="assistant"]').getByRole("button", { name: "보내기" }).click();
  await page.getByText("차량 침수 피해가 줄었다").waitFor();
  await tab("결정 사항");

  // 7. 진행 중 토론방·입력 중: 두 동료가 함께 입력하는 장면만 실제 속도로
  await waitFor(async () => (await minutes()) >= 7.7, "입력 중 장면 직전");
  await mock("c.setSpeed(1)");
  await page.getByText("강한결님, 윤하람님이 입력 중").waitFor({ timeout: 20_000 });
  await shot("06-debate-typing");

  // 8. 다크(시스템 설정): 같은 토론방
  await page.emulateMedia({ colorScheme: "dark" });
  await shot("07-debate-dark");
  await page.emulateMedia({ colorScheme: "light" });

  // 9. 기획안 단계(해결책 시간 초과 → 우측 탭 자동 전환), 6칸 작성
  await mock(`c.setSpeed(${SPEED})`);
  await waitFor(async () => (await phase()) === "proposal", "기획안 단계");
  await page.locator('[data-panel="proposal"]:not([hidden])').waitFor();
  // 서버가 앞 3칸을 확정 값으로 채운 것이 화면에 들어올 때까지
  await waitFor(
    () =>
      page
        .locator('[data-section="problem"]')
        .inputValue()
        .then((v) => v.length > 0),
    "기획안 앞 3칸 채움",
  );
  const fill = (id, text) => page.locator(`[data-section="${id}"]`).fill(text);
  await mock("c.setSpeed(1)");
  await fill(
    "solution",
    "하수관 수위 센서로 침수 위험을 미리 알리는 데이터 기반 경보에 빗물 저금통·투수 포장을 함께 설치",
  );
  await fill(
    "rationale",
    "최근 3년 침수 신고와 피해액이 함께 늘었고, 센서 경보와 빗물 인프라를 함께 쓴 지역에서 피해가 줄었다.",
  );
  await fill(
    "expected_effect",
    "침수 30분 전 경보로 차량·상가 피해를 줄이고, 첫 30분 빗물 유출량을 낮춘다.",
  );
  await fill(
    "idea_credit",
    "문제 상황: 강한결 님 제안. 해결책: 제 센서 경보 안에 윤하람 님의 빗물 저금통 아이디어를 보탬",
  );
  await page.getByText("저장됨").waitFor();
  await page.locator('[data-panel="proposal"]').evaluate((el) => (el.scrollTop = el.scrollHeight));
  await shot("08-proposal");

  // 10. 제출 → 확인 창 → 잠김 → [나가기] → 디브리핑
  await page.getByRole("button", { name: "제출", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "제출" }).click();
  await page.getByRole("button", { name: "나가기" }).click();
  await page.getByRole("heading", { name: "수고하셨습니다" }).waitFor();
  await shot("09-debrief");
  await shot("10-debrief-full", { fullPage: true });

  if (errors.length) console.log("페이지 오류:", errors);
  console.log(`완료: ${saved.length}장`);
} catch (error) {
  await page.screenshot({ path: join(OUT, "fail.png") }).catch(() => null);
  console.error(error);
  process.exitCode = 1;
} finally {
  await browser.close();
}
