// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test, vi } from "vitest";

import { EmptyRecognition } from "./voice-room";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

test("빈 인식 결과에는 전사 대신 다시 말하기를 보여 준다", () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const rerecord = vi.fn();
  act(() => root.render(<EmptyRecognition onRerecord={rerecord} />));
  expect(host.textContent).toContain("잘 들리지 않았어요. 다시 말해 주세요");
  expect(host.querySelector("textarea")).toBeNull();
  expect(host.textContent).not.toContain("발언 제출");
  act(() => host.querySelector("button")!.click());
  expect(rerecord).toHaveBeenCalledOnce();
  act(() => root.unmount());
  host.remove();
});

test("500자 초과 인식은 내용을 숨기고 짧게 다시 말하도록 안내한다", () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  const rerecord = vi.fn();
  act(() => root.render(<EmptyRecognition tooLong onRerecord={rerecord} />));
  expect(host.textContent).toContain("말이 길어 전하지 못했어요. 짧게 다시 말해 주세요");
  expect(host.querySelector("textarea")).toBeNull();
  act(() => host.querySelector("button")!.click());
  expect(rerecord).toHaveBeenCalledOnce();
  act(() => root.unmount());
});
