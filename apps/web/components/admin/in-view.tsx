"use client";

// 리포트·전체 기록 화면의 스크롤 등장과 목차 따라가기.
// InView는 첫 렌더(서버 포함)에 늘 보인다. 사용자가 처음 스크롤한 순간 아직 화면 밖에 있는 것만 감췄다가
// 화면에 들어올 때 떠오르게 한다. 그래서 하이드레이션 불일치가 없고, 스크롤 없이 찍는 전체 화면 캡처·인쇄에서도 다 보인다.
import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ElementType,
  type ReactNode,
} from "react";

import { cn, useReducedMotion } from "@/components/ui";

type Mode = "static" | "pending" | "hidden" | "shown";

export function InView({
  as: Tag = "div",
  className,
  style,
  children,
  ...rest
}: {
  as?: ElementType;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
} & Record<string, unknown>) {
  const ref = useRef<HTMLElement>(null);
  const reduced = useReducedMotion();
  const [mode, setMode] = useState<Mode>("static");
  useEffect(() => {
    const el = ref.current;
    if (!el || reduced || typeof IntersectionObserver === "undefined") return;
    let first = true;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.some((e) => e.isIntersecting);
        if (first) {
          first = false;
          // 처음부터 화면 안이면 움직이지 않는다
          if (visible) observer.disconnect();
          else setMode("pending");
          return;
        }
        if (visible) {
          setMode((m) => (m === "hidden" ? "shown" : "static"));
          observer.disconnect();
        }
      },
      { rootMargin: "0px 0px -6% 0px" },
    );
    observer.observe(el);
    const arm = () => setMode((m) => (m === "pending" ? "hidden" : m));
    window.addEventListener("scroll", arm, { passive: true, once: true });
    return () => {
      observer.disconnect();
      window.removeEventListener("scroll", arm);
    };
  }, [reduced]);
  return (
    <Tag
      ref={ref}
      className={cn(
        mode === "hidden" && "opacity-0 print:opacity-100",
        mode === "shown" && "reveal",
        className,
      )}
      style={style}
      {...rest}
    >
      {children}
    </Tag>
  );
}

/**
 * 목차 따라가기: 위쪽 기준선(화면 높이 × line)을 이미 지난 구역 중 마지막 id. 페이지 끝에 닿으면 마지막 id.
 * 서버 렌더에서는 첫 id 그대로다
 */
export function useScrollSpy(ids: string[], line = 0.3) {
  const key = ids.join("|");
  const [active, setActive] = useState(ids[0] ?? "");
  useEffect(() => {
    const list = key ? key.split("|") : [];
    if (!list.length) return;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const doc = document.documentElement;
      if (window.innerHeight + window.scrollY >= doc.scrollHeight - 4) {
        setActive(list[list.length - 1]);
        return;
      }
      let now = list[0];
      for (const id of list) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top <= window.innerHeight * line) now = id;
      }
      setActive(now);
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [key, line]);
  return active;
}
