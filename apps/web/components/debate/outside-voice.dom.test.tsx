// @vitest-environment happy-dom
// 완료 기준: 좁은 화면 안내, 동의 화면 어시스턴트 문구, 디브리핑 접힘 문구, 음성/글 토론의 말하는 중 문구.
import { Popover } from "@base-ui/react/popover";
import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test } from "vitest";

import { ASSISTANT_NOTICE, DesktopNotice, useNarrowViewport } from "./link-screens";
import { ProfileCard } from "./profile-stage";
import type { Participant, StateSnapshot } from "./types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function stubWidth(width: number) {
  window.matchMedia = ((query: string) => {
    const max = Number(/max-width:\s*([\d.]+)px/.exec(query)?.[1]);
    return {
      matches: width <= max,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    };
  }) as unknown as typeof window.matchMedia;
}

function Probe() {
  return useNarrowViewport() ? <DesktopNotice /> : <p>입장 화면</p>;
}

test("가로 1024px 미만이면 노트북·PC Chrome 안내가 시작 전 화면을 막는다", () => {
  stubWidth(800);
  act(() => root.render(<Probe />));
  expect(container.textContent).toContain("노트북이나 PC의 Chrome으로 열어 주세요");
  expect(container.textContent).not.toContain("입장 화면");
});

test("가로 1024px 이상이면 안내 없이 화면이 보인다", () => {
  stubWidth(1024);
  act(() => root.render(<Probe />));
  expect(container.textContent).toContain("입장 화면");
  expect(container.textContent).not.toContain("Chrome");
});

test("어시스턴트 안내는 주어가 어시스턴트이고 평가 방식 문구와 모순되지 않는다", () => {
  expect(ASSISTANT_NOTICE).toContain("AI 어시스턴트는 평가에 관여하지 않으며");
  expect(ASSISTANT_NOTICE).not.toContain("평가 수준을 매기지");
});

const person = { id: "p1", display_name: "강한결", avatar_url: null } as unknown as Participant;
const snap = { messages: [], session: { phase: "keyword" } } as unknown as StateSnapshot;
const card = (voiceMode: boolean) =>
  renderToStaticMarkup(
    <Popover.Root open>
      <ProfileCard person={person} snap={snap} speaking voiceMode={voiceMode} />
    </Popover.Root>,
  );

test("프로필 카드: 음성 토론의 AI 동료는 말하는 중, 글 토론은 입력 중", () => {
  expect(card(true)).toContain("말하는 중이에요");
  expect(card(true)).not.toContain("입력 중");
  expect(card(false)).toContain("지금 입력 중이에요");
});
