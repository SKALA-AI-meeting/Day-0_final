// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test, vi } from "vitest";

import { CANDIDATE_MESSAGE_MAX_CHARS } from "../state";
import { TranscriptConfirm, UnsentSpeech } from "./voice-room";

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

test("수정 중에는 남은 제출 시간과 글자 수를 보이고, 상한을 넘으면 제출을 막는다", () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const render = (text: string) =>
    root.render(
      <TranscriptConfirm
        text={text}
        changed
        empty={false}
        seconds={0}
        submitSeconds={12.2}
        rerecorded
        onChange={vi.fn()}
        onSubmit={vi.fn()}
        onRerecord={vi.fn()}
      />,
    );
  act(() => render("고친 발언"));
  expect(host.textContent).toContain("13초 안에 보내지 않으면 이번 발언은 들어가지 않아요");
  expect(host.textContent).toContain(`5/${CANDIDATE_MESSAGE_MAX_CHARS}자`);
  expect(host.querySelector("textarea")!.maxLength).toBe(CANDIDATE_MESSAGE_MAX_CHARS);
  act(() => render("가".repeat(CANDIDATE_MESSAGE_MAX_CHARS + 1)));
  expect(host.textContent).toContain("줄여야 제출할 수 있어요");
  expect(host.querySelector<HTMLButtonElement>("button")!.disabled).toBe(true);
  act(() => root.unmount());
  host.remove();
});

test("제출하지 못한 발언은 읽기 전용으로 남고 닫을 수 있다", () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const dismiss = vi.fn();
  act(() => root.render(<UnsentSpeech text="못 보낸 발언" onDismiss={dismiss} />));
  expect(host.querySelector('[role="alert"]')!.textContent).toContain(
    "제출하지 못한 발언이 있어요",
  );
  const area = host.querySelector("textarea")!;
  expect(area.readOnly).toBe(true);
  expect(area.value).toBe("못 보낸 발언");
  const close = [...host.querySelectorAll("button")].find((b) => b.textContent === "닫기")!;
  act(() => close.click());
  expect(dismiss).toHaveBeenCalledOnce();
  act(() => root.unmount());
  host.remove();
});
