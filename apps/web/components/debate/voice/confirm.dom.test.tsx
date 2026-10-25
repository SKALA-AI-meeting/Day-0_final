// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test, vi } from "vitest";

import { TranscriptConfirm } from "./voice-room";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

test("받아 적은 글을 고쳐 제출하고 빈 인식 결과에도 직접 입력할 수 있다", () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let text = "인식 원문";
  const submit = vi.fn();
  const render = (empty = false, changed = false) =>
    root.render(
      <TranscriptConfirm
        text={text}
        changed={changed}
        empty={empty}
        seconds={8}
        rerecorded={false}
        onChange={(value) => {
          text = value;
          render(empty, true);
        }}
        onSubmit={submit}
        onRerecord={vi.fn()}
      />,
    );
  act(() => render());
  const area = host.querySelector("textarea")!;
  expect(area.value).toBe("인식 원문");
  expect(host.textContent).toContain("8초 뒤 자동으로 제출");
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
  act(() => {
    setter.call(area, "고친 발언");
    area.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(host.textContent).toContain("발언 제출]을 눌러 보내세요");
  act(() => host.querySelector<HTMLButtonElement>("button")!.click());
  expect(submit).toHaveBeenCalledOnce();
  text = "";
  act(() => render(true));
  expect(host.querySelector<HTMLButtonElement>("button")!.disabled).toBe(true);
  // 빈 인식 결과는 서버가 다시 녹음을 받지 않으므로 그 버튼을 보이지 않고, 자동 제출 안내도 없다
  expect(host.textContent).not.toContain("다시 녹음");
  expect(host.textContent).not.toContain("자동으로 제출");
  act(() => {
    setter.call(area, "직접 쓴 발언");
    area.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(host.querySelector<HTMLButtonElement>("button")!.disabled).toBe(false);
  act(() => root.unmount());
  host.remove();
});
