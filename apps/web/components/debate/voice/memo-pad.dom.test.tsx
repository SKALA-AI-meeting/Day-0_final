// @vitest-environment happy-dom
// 메모장: 입력하면 이 브라우저에 세션별로 저장되고 다시 열면 이어지며, 저장소가 막혀도 입력은 된다.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { MemoPad, memoKey } from "./memo-pad";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let store: Map<string, string>;

/** 테스트 환경에 브라우저 저장소가 없어 Map으로 흉내 낸다. blocked면 저장할 때 예외를 던진다 */
function fakeStorage(blocked = false) {
  store = new Map();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      if (blocked) throw new Error("blocked");
      store.set(key, value);
    },
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  fakeStorage();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

const area = () => container.querySelector("textarea")!;

function type(value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
  act(() => {
    setter.call(area(), value);
    area().dispatchEvent(new Event("input", { bubbles: true }));
  });
}

test("입력하면 세션별로 저장되고 다시 열면 이어진다", () => {
  act(() => root.render(<MemoPad sessionId="s1" locked={false} />));
  type("서지안: 비용 근거 질문");
  expect(store.get(memoKey("s1"))).toBe("서지안: 비용 근거 질문");
  act(() => root.unmount());
  root = createRoot(container);
  act(() => root.render(<MemoPad sessionId="s1" locked={false} />));
  expect(area().value).toBe("서지안: 비용 근거 질문");
  expect(store.has(memoKey("s2"))).toBe(false);
});

test("저장소가 막혀도 입력은 되고 저장 안 됨을 알린다", () => {
  fakeStorage(true);
  act(() => root.render(<MemoPad sessionId="s1" locked={false} />));
  type("메모");
  expect(area().value).toBe("메모");
  expect(container.textContent).toContain("저장할 수 없는 브라우저예요");
});

test("세션이 끝나면 읽기 전용이다", () => {
  act(() => root.render(<MemoPad sessionId="s1" locked />));
  expect(area().readOnly).toBe(true);
});
