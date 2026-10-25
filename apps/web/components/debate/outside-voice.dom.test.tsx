// @vitest-environment happy-dom
// 완료 기준: 좁은 화면 안내, 동의 화면 어시스턴트 문구, 음성 프로필의 이름·역할 표시.
import { Popover } from "@base-ui/react/popover";
import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test } from "vitest";

import { ASSISTANT_NOTICE, DesktopNotice, SessionGuide, useNarrowViewport } from "./link-screens";
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

test("시작 전 안내는 발언 시간과 손들기 차례를 설명한다", () => {
  const guide = renderToStaticMarkup(
    <SessionGuide
      snap={
        {
          participants: [{ id: "candidate", display_name: "김지원" }],
          phases: [
            { phase: "keyword" },
            { phase: "problem" },
            { phase: "solution" },
            { phase: "proposal", budget_min: 14 },
          ],
          session: {},
        } as unknown as StateSnapshot
      }
    />,
  );
  expect(guide).toContain("말하는 사람이 크게 보여요");
  expect(guide).toContain("정해진 시간 안에 [발언하기]");
  expect(guide).toContain("최대 60초");
  expect(guide).toContain("[발언 완료]");
  expect(guide).toContain("내 말은 글로 보이지 않고 바로 전달돼요");
  expect(guide).toContain("손들기는 다음 차례를 예약해요");
});

const person = { id: "p1", display_name: "강한결", avatar_url: null } as unknown as Participant;
const snap = { messages: [], session: { phase: "keyword" } } as unknown as StateSnapshot;
const card = (voiceMode: boolean) =>
  renderToStaticMarkup(
    <Popover.Root open>
      <ProfileCard
        person={person}
        snap={
          voiceMode
            ? ({
                ...snap,
                messages: [{ sender: "p1", phase: "keyword", content: "숨길 발언" }],
              } as StateSnapshot)
            : snap
        }
        speaking
        voiceMode={voiceMode}
      />
    </Popover.Root>,
  );

test("프로필 카드: 음성 토론은 이름·역할만, 글 토론은 입력 상태를 보인다", () => {
  expect(card(true)).toContain("AI 동료 · 사람 팀원 역할을 맡은 AI");
  expect(card(true)).not.toContain("단계에서 한 말");
  expect(card(true)).not.toContain("숨길 발언");
  expect(card(true)).not.toContain("발언 중이에요");
  expect(card(false)).toContain("지금 입력 중이에요");
});
