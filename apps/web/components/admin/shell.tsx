"use client";

import { LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useSyncExternalStore, type ReactNode } from "react";

import { Button, cn, Wordmark } from "@/components/ui";

import { setToken, useToken } from "./client";

const OPEN_PATHS = ["/admin/login", "/admin/signup"];
// 샘플 리포트는 로그인 없이도 볼 수 있다(정적 픽스처)
const PUBLIC_PREFIX = "/admin/sample-report";

const NAV = [
  { href: "/admin/positions", label: "채용 건" },
  { href: "/admin/sample-report", label: "샘플 리포트" },
];

/**
 * /admin 공통 틀. 토큰이 없으면 로그인 화면으로 보내고, 그 전에는 본문을 그리지 않는다.
 * 토론 화면과 같은 결로 반투명 상단 바(고정)와 옅은 번짐 배경 위에 본문을 둔다.
 */
export function AdminShell({ children }: { children: ReactNode }) {
  const token = useToken();
  const path = usePathname();
  const router = useRouter();
  const open = OPEN_PATHS.includes(path);
  const isPublic = path.startsWith(PUBLIC_PREFIX);
  // 하이드레이션 전에는 token이 서버 스냅샷(null)이므로, 브라우저에서 값을 읽은 뒤에만 로그인 여부를 따진다
  const hydrated = useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );

  useEffect(() => {
    if (hydrated && !token && !open && !isPublic) router.replace("/admin/login");
  }, [hydrated, token, open, isPublic, router]);

  if (open) return <>{children}</>;
  if (!token && !isPublic) return null;

  return (
    <div className="relative isolate min-h-dvh bg-canvas">
      {/* 배경 레이어. 무늬는 globals.css의 .page-backdrop만 고친다 */}
      <div aria-hidden className="page-backdrop no-print pointer-events-none fixed inset-0 -z-10" />
      <header className="vibrancy no-print sticky top-0 z-30 border-b border-line">
        <div className="mx-auto flex h-13 max-w-5xl items-center gap-3 px-4 sm:gap-6 sm:px-6">
          <Link
            href="/admin/positions"
            className="flex shrink-0 items-center gap-2 rounded-sm"
            aria-label="채용 건 목록으로"
          >
            <Wordmark className="h-4" />
            <span className="hidden text-footnote text-muted sm:inline">채용 담당</span>
          </Link>
          {token && (
            <nav aria-label="관리 메뉴" className="flex min-w-0 items-center gap-1">
              {NAV.map((item) => {
                const on = path === item.href || path.startsWith(`${item.href}/`);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    aria-current={on ? "page" : undefined}
                    className={cn(
                      "relative rounded-md px-2.5 py-1 text-footnote whitespace-nowrap transition-colors duration-150",
                      on
                        ? "bg-surface-sunken font-semibold text-ink"
                        : "text-muted hover:bg-surface-sunken hover:text-ink",
                    )}
                  >
                    {item.label}
                  </Link>
                );
              })}
            </nav>
          )}
          <div className="ml-auto flex items-center">
            {token ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setToken(null);
                  router.replace("/admin/login");
                }}
              >
                <LogOut aria-hidden className="size-3.5" />
                로그아웃
              </Button>
            ) : (
              <Link href="/admin/login" className="text-sm text-muted hover:underline">
                관리자 로그인
              </Link>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-8 sm:px-6">{children}</main>
    </div>
  );
}
