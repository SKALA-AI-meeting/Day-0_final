"use client";

// 캡처·점검용 조작 패널(NEXT_PUBLIC_MOCK=1에서만, 개발 표시). design.md "개발 순서: 목업 먼저"의 목록:
// 단계 건너뛰기, 입력 중 켜기, 칸 확정(합의·시간 초과), seq 건너뛰기, 자료 열림, 제출·종료 사유, 연결 끊기·다른 창,
// 라이트·다크, 데모 속도, Demo/Worst 데이터, 링크 토큰 이동.
import { useState, useSyncExternalStore, type ReactNode } from "react";

import { setWorst, useWorst } from "@/components/dev/data-toggle";
import { mockControls, subscribeMock } from "@/mocks/debate";

import type { EndReason, Phase, Seat } from "./types";

const TOKENS = ["demo", "keyword", "problem", "solution", "proposal", "done", "failed", "expired"];

function useMockVersion() {
  // 서버 상태가 바뀔 때마다 다시 그린다(값은 바뀐 횟수)
  return useSyncExternalStore(
    (fn) => subscribeMock(fn),
    () => versionCounter.value,
    () => 0,
  );
}
const versionCounter = { value: 0 };
subscribeMock(() => {
  versionCounter.value += 1;
});

function Btn({ children, onClick }: { children: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-sm border border-[#d1d5db] bg-white px-1.5 py-0.5 hover:bg-[#f3f4f6]"
    >
      {children}
    </button>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="w-14 shrink-0 text-[#6b7280]">{label}</span>
      {children}
    </div>
  );
}

function setTheme(theme: "light" | "dark" | "system") {
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  if (theme !== "system") root.classList.add(theme);
}

export function MockControls({ token }: { token: string }) {
  useMockVersion();
  const worst = useWorst();
  const [open, setOpen] = useState(false);
  const c = mockControls();
  const go = (next: string) => {
    const url = new URL(window.location.href);
    url.pathname = `/s/${next}`;
    window.location.assign(url.toString());
  };
  return (
    // Next.js 개발 표시(왼쪽 아래 N, 20px 띄움·36px)와 겹치지 않게 그 오른쪽에 둔다
    <div
      data-mock-controls
      className="no-print fixed bottom-5 left-16 z-[70] max-w-[340px] rounded-md border border-[#d1d5db] bg-[#f9fafb]/95 p-1.5 font-sans text-[11px] text-[#111827] shadow-sm"
    >
      <button type="button" onClick={() => setOpen((v) => !v)} className="px-1 font-semibold">
        mock 조작 {open ? "▾" : "▸"}
        {c.elapsed && (
          <span className="ml-2 font-normal text-[#6b7280]">
            경과 {c.elapsed} · ×{c.speed}
          </span>
        )}
      </button>
      {open && (
        <div className="mt-1 flex flex-col gap-1">
          <Row label="링크">
            {TOKENS.map((t) => (
              <Btn key={t} onClick={() => go(t)}>
                {t === token ? `[${t}]` : t}
              </Btn>
            ))}
          </Row>
          {c.available && c.status === "in_progress" && (
            <>
              <Row label="단계">
                {(["keyword", "problem", "solution", "proposal"] as Phase[]).map((p) => (
                  <Btn key={p} onClick={() => c.skipTo(p)}>
                    {p}
                  </Btn>
                ))}
              </Row>
              <Row label="입력 중">
                {(["p1", "p2", "p3"] as Seat[]).map((seat) => (
                  <Btn key={seat} onClick={() => c.typing(seat)}>
                    {seat}
                  </Btn>
                ))}
              </Row>
              <Row label="칸 확정">
                <Btn onClick={() => c.fill("consensus")}>consensus</Btn>
                <Btn onClick={() => c.fill("timeout")}>timeout</Btn>
                <Btn onClick={c.openMaterials}>자료 열림</Btn>
              </Row>
              <Row label="연결">
                <Btn onClick={c.skipSeq}>seq 건너뛰기</Btn>
                <Btn onClick={c.disconnect}>끊기</Btn>
                <Btn onClick={c.replace}>다른 창</Btn>
                <Btn onClick={c.conflict}>기획안 충돌</Btn>
                <Btn onClick={c.exhaustAssistant}>어시스턴트 한도</Btn>
              </Row>
              <Row label="종료">
                {(["submitted", "timeout", "failed", "cancelled"] as EndReason[]).map((r) => (
                  <Btn key={r} onClick={() => c.end(r)}>
                    {r}
                  </Btn>
                ))}
              </Row>
              <Row label="속도">
                {[1, 5, 20, 60].map((v) => (
                  <Btn key={v} onClick={() => c.setSpeed(v)}>
                    {`×${v}`}
                  </Btn>
                ))}
              </Row>
            </>
          )}
          <Row label="테마">
            <Btn onClick={() => setTheme("light")}>라이트</Btn>
            <Btn onClick={() => setTheme("dark")}>다크</Btn>
            <Btn onClick={() => setTheme("system")}>시스템</Btn>
          </Row>
          <Row label="데이터">
            <Btn onClick={() => setWorst(false)}>{worst ? "Demo" : "[Demo]"}</Btn>
            <Btn onClick={() => setWorst(true)}>{worst ? "[Worst case]" : "Worst case"}</Btn>
          </Row>
        </div>
      )}
    </div>
  );
}
