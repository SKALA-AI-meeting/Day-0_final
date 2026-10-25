// @vitest-environment happy-dom
import { afterEach, expect, test } from "vitest";

import { spaceShortcutBlocked } from "./control-state";

afterEach(() => {
  document.body.innerHTML = "";
});

test("Space 단축키는 상호작용 요소에 포커스가 있거나 대화상자가 열려 있으면 쓰지 않는다", () => {
  document.body.innerHTML = `
    <main id="plain"><p id="text">회의</p></main>
    <textarea id="memo"></textarea>
    <details><summary id="recent">최근 발언</summary></details>
    <a id="link" href="#x">링크</a>
    <div id="tab" role="tab">자료</div>
    <div id="fake-button" role="button">누르기</div>
    <div id="editable" contenteditable="true">편집</div>`;
  const el = (id: string) => document.getElementById(id);
  expect(spaceShortcutBlocked(el("text"))).toBe(false);
  expect(spaceShortcutBlocked(document.body)).toBe(false);
  for (const id of ["memo", "recent", "link", "tab", "fake-button", "editable"])
    expect(spaceShortcutBlocked(el(id)), id).toBe(true);

  // 화면 설명·확정 확인 같은 대화상자가 열려 있으면 포커스가 어디든 막는다
  const dialog = document.createElement("div");
  dialog.setAttribute("role", "dialog");
  document.body.appendChild(dialog);
  expect(spaceShortcutBlocked(dialog)).toBe(true);
  expect(spaceShortcutBlocked(el("text"))).toBe(true);
  dialog.remove();
  const popup = document.createElement("div");
  popup.dataset.state = "open";
  document.body.appendChild(popup);
  expect(spaceShortcutBlocked(el("text"))).toBe(true);
});
