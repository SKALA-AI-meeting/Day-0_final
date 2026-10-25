"use client";

// 관리자 로그인·가입. 옅은 번짐 배경 위 가운데 카드이고, 칸은 순서대로 떠오른다.
// 로그인과 가입은 다른 주소지만 위쪽 전환 막대로 오가고, 가입은 오른쪽에서·로그인은 왼쪽에서 들어온다.
import { MessagesSquare, ScanSearch, UserCheck } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button, cn, Input, Reveal, useReducedMotion, Wordmark } from "@/components/ui";

export type AuthValues = { email: string; password: string; company_name: string };

export type AuthFormProps = {
  mode: "login" | "signup";
  pending?: boolean;
  /** 서버가 돌려준 오류 문구 */
  error?: string | null;
  onSubmit: (values: AuthValues) => void;
};

// 넓은 화면 왼쪽 소개. 서비스가 하는 일만 말하고 판정 표현은 쓰지 않는다
const INTRO = [
  {
    icon: MessagesSquare,
    title: "AI 동료 셋과의 토론",
    text: "지원자가 실제 협업 장면에서 말하고 맞춥니다.",
  },
  {
    icon: ScanSearch,
    title: "행동 근거 리포트",
    text: "발언과 결정 기록을 근거로 요소별 수준을 정리합니다.",
  },
  {
    icon: UserCheck,
    title: "판단은 담당자가",
    text: "근거를 보고 세부 행동 단위로 조정해 확정합니다.",
  },
];

const MODES = [
  { id: "login", label: "로그인", href: "/admin/login" },
  { id: "signup", label: "가입", href: "/admin/signup" },
] as const;

// 클라이언트 이동 사이에 남는 직전 화면. 로그인 ↔ 가입으로 바뀌었을 때만 미끄러지는 전환을 쓴다
let lastMode: AuthFormProps["mode"] | null = null;

export function AuthForm({ mode, pending, error, onSubmit }: AuthFormProps) {
  const reduced = useReducedMotion();
  const indicator = useRef<HTMLSpanElement>(null);
  // 처음 그릴 때 한 번만 정한다(직전 화면이 다른 모드면 전환으로 들어온 것)
  const [switched] = useState(() => lastMode !== null && lastMode !== mode);
  useEffect(() => {
    lastMode = mode;
    // 선택 표시가 직전 칸에서 지금 칸으로 미끄러진다(주소가 바뀌어 다시 그려지므로 직접 움직인다)
    if (!switched || reduced || !indicator.current?.animate) return;
    indicator.current.animate(
      [{ transform: mode === "signup" ? "translateX(0)" : "translateX(100%)" }, {}],
      { duration: 320, easing: "cubic-bezier(0.23, 1, 0.32, 1)" },
    );
  }, [mode, switched, reduced]);
  const [values, setValues] = useState<AuthValues>({ email: "", password: "", company_name: "" });
  const signup = mode === "signup";
  const set = (key: keyof AuthValues) => (event: { target: { value: string } }) =>
    setValues((prev) => ({ ...prev, [key]: event.target.value }));

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(values);
  }

  // 칸이 하나씩 떠오른다. 가입은 회사 이름이 먼저다
  const fields = [
    signup && (
      <Input
        key="company"
        label="회사 이름"
        value={values.company_name}
        onChange={set("company_name")}
        autoComplete="organization"
        required
      />
    ),
    <Input
      key="email"
      label="이메일"
      type="email"
      autoComplete="email"
      value={values.email}
      onChange={set("email")}
      required
    />,
    <Input
      key="password"
      label="비밀번호"
      type="password"
      autoComplete={signup ? "new-password" : "current-password"}
      value={values.password}
      onChange={set("password")}
      required
    />,
  ].filter(Boolean);

  return (
    <div className="relative isolate flex min-h-dvh flex-col bg-canvas">
      <div aria-hidden className="page-backdrop pointer-events-none fixed inset-0 -z-10" />
      <main className="mx-auto grid w-full max-w-5xl flex-1 items-center gap-10 px-4 py-12 sm:px-6 lg:grid-cols-[1fr_minmax(0,26rem)] lg:gap-16">
        <section aria-label="Day Zero 소개" className="hidden flex-col gap-8 lg:flex">
          <Reveal>
            <Wordmark className="h-6" />
          </Reveal>
          <Reveal delay={80}>
            <p className="text-title1 leading-snug font-bold">
              말로 설명하는 모습보다
              <br />
              협업에서 보이는 행동을 봅니다.
            </p>
          </Reveal>
          <ul className="flex flex-col gap-5">
            {INTRO.map(({ icon: Icon, title, text }, i) => (
              <Reveal as="li" key={title} delay={240 + i * 140} className="flex gap-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-line bg-surface text-accent">
                  <Icon aria-hidden className="size-4.5" />
                </span>
                <span className="flex flex-col">
                  <span className="font-semibold">{title}</span>
                  <span className="text-sm text-muted">{text}</span>
                </span>
              </Reveal>
            ))}
          </ul>
        </section>

        <div className="mx-auto flex w-full max-w-md flex-col gap-5 lg:max-w-none">
          <Reveal className="lg:hidden">
            <Wordmark className="h-6" />
          </Reveal>
          <nav
            aria-label="계정"
            className="relative grid grid-cols-2 rounded-md bg-surface-sunken p-0.5 text-sm"
          >
            {/* 선택 표시가 로그인 ↔ 가입 사이를 미끄러진다 */}
            <span
              ref={indicator}
              aria-hidden
              className={cn(
                "absolute top-0.5 bottom-0.5 left-0.5 w-[calc(50%-2px)] rounded-[calc(var(--radius-md)-2px)] border border-line bg-surface",
                signup && "translate-x-full",
              )}
            />
            {MODES.map((m) => (
              <Link
                key={m.id}
                href={m.href}
                replace
                aria-current={m.id === mode ? "page" : undefined}
                className={cn(
                  "relative z-10 rounded-[calc(var(--radius-md)-2px)] py-1.5 text-center transition-colors duration-150",
                  m.id === mode ? "font-semibold text-ink" : "text-muted hover:text-ink",
                )}
              >
                {m.label}
              </Link>
            ))}
          </nav>

          <section
            key={mode}
            className={cn(
              "flex flex-col gap-5 rounded-xl border border-line bg-surface p-6 shadow-[var(--shadow-float)] sm:p-7",
              !switched ? "reveal" : signup ? "screen-forward" : "screen-back",
            )}
          >
            <header className="flex flex-col gap-1">
              <h1 className="text-2xl font-semibold">{signup ? "관리자 가입" : "관리자 로그인"}</h1>
              <p className="text-sm text-muted">
                {signup
                  ? "회사 계정을 만들면 바로 채용 건을 만들 수 있습니다."
                  : "채용 건과 지원자 리포트를 보려면 로그인하세요."}
              </p>
            </header>
            <form className="flex flex-col gap-4" onSubmit={submit}>
              {fields.map((field, i) => (
                <Reveal key={(field as { key: string }).key} delay={120 + i * 90}>
                  {field}
                </Reveal>
              ))}
              {error && (
                <p
                  role="alert"
                  className="pop-in rounded-md bg-danger-soft px-3 py-2 text-sm text-danger"
                >
                  {error}
                </p>
              )}
              <Reveal delay={120 + fields.length * 90} className="flex flex-col">
                <Button type="submit" loading={pending}>
                  {signup ? "가입하고 시작" : "로그인"}
                </Button>
              </Reveal>
            </form>
          </section>

          <Reveal delay={480} as="p" className="text-center text-sm text-muted">
            {signup ? "이미 계정이 있나요? " : "계정이 없나요? "}
            <Link
              className="text-accent underline-offset-4 hover:underline"
              href={signup ? "/admin/login" : "/admin/signup"}
              replace
            >
              {signup ? "로그인" : "가입"}
            </Link>
            <span aria-hidden> · </span>
            <Link
              className="text-accent underline-offset-4 hover:underline"
              href="/admin/sample-report"
            >
              샘플 리포트 보기
            </Link>
          </Reveal>
        </div>
      </main>
    </div>
  );
}
