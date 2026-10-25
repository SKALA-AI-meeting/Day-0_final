// @vitest-environment happy-dom
// 완료 기준: 접히는 구역은 닫혀 있으면 "눌러서 펼치기", 열리면 "접기"를 보이고 화살표는 열림에 맞춰 돈다.
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";

import { Section } from "./debrief-screen";

test("접힘 구역: 두 문구를 열림 상태 CSS로 번갈아 보이고 ChevronRight를 쓴다", () => {
  const html = renderToStaticMarkup(
    <Section title="내 발언" collapsed>
      본문
    </Section>,
  );
  expect(html).toContain('group-open:hidden">눌러서 펼치기');
  expect(html).toContain('hidden group-open:inline">접기');
  expect(html).toContain("group-open:rotate-90");
  expect(html).toContain("lucide-chevron-right");
});
