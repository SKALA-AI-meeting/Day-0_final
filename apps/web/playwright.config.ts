// 단계별 화면 테스트(stage-e2e) 설정. 스택은 `make e2e-stack-up`(별도 compose 프로젝트)이 띄운다.
// 영상·trace는 .artifacts(git 무시), 스크린샷·summary는 tests/e2e/records/stages/<단계>/에 남는다.
import { defineConfig } from "@playwright/test";
import { join } from "node:path";

const RECORDS = join(import.meta.dirname, "..", "..", "tests", "e2e", "records", "stages");

export default defineConfig({
  testDir: "./e2e/stages",
  outputDir: join(RECORDS, ".artifacts"),
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { outputFolder: join(RECORDS, "html-report"), open: "never" }]],
  use: {
    baseURL: process.env.WEB_URL ?? "http://localhost:3100",
    viewport: { width: 1280, height: 800 },
    video: "on",
    trace: "on",
    locale: "ko-KR",
    permissions: ["microphone"],
    launchOptions: {
      args: [
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        "--autoplay-policy=no-user-gesture-required",
      ],
    },
  },
});
