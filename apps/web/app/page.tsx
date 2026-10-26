import { ArrowRight, Link2, UserRoundCheck } from "lucide-react";
import Link from "next/link";

import { Reveal, Wordmark } from "@/components/ui";
import { MOCK } from "@/lib/api";

// 루트는 안내만 한다. 지원자는 받은 초대 링크(/s/[token]), 채용 담당자는 관리자 화면(/admin).
// mock에서는 15분 데모 링크를 함께 보인다. 안내가 먼저 떠오르고 갈 곳 카드가 차례로 들어온다.
export default function Home() {
  return (
    <main data-mood="dawn" className="page-backdrop flex min-h-dvh flex-col">
      <div className="mx-auto flex w-full max-w-xl flex-1 flex-col justify-center gap-8 px-6 py-24">
        <Reveal>
          <Wordmark className="h-6" />
        </Reveal>
        <Reveal as="p" index={1} className="text-callout text-muted">
          지원자는 받은 초대 링크로 접속합니다. 채용 담당자는 관리자 화면을 씁니다.
        </Reveal>
        <div className="flex flex-col gap-3">
          <Reveal delay={260}>
            <Link
              href="/admin/login"
              className="lift press flex items-center gap-4 rounded-xl border border-line bg-surface px-5 py-4"
            >
              <span className="flex size-10 items-center justify-center rounded-full bg-accent-soft text-accent">
                <UserRoundCheck size={20} aria-hidden />
              </span>
              <span className="flex flex-1 flex-col">
                <span className="text-body font-semibold">관리자 로그인</span>
                <span className="text-footnote text-muted">채용 건·지원자·리포트</span>
              </span>
              <ArrowRight size={18} aria-hidden className="text-muted" />
            </Link>
          </Reveal>
          {MOCK && (
            <Reveal delay={360}>
              <Link
                href="/s/demo"
                className="lift press flex items-center gap-4 rounded-xl border border-line bg-surface px-5 py-4"
              >
                <span className="flex size-10 items-center justify-center rounded-full bg-accent-soft text-accent">
                  <Link2 size={20} aria-hidden />
                </span>
                <span className="flex flex-1 flex-col">
                  <span className="text-body font-semibold">15분 데모 세션</span>
                  <span className="text-footnote text-muted">지원자 화면 미리 보기</span>
                </span>
                <ArrowRight size={18} aria-hidden className="text-muted" />
              </Link>
            </Reveal>
          )}
        </div>
      </div>
    </main>
  );
}
