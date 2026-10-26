// 리포트 생성 실패 화면. [다시 만들기]는 재생성을 요청하고, 성공하면 부모가 "만드는 중" 폴링으로 돌아간다.
// 카드가 떠오르며 테두리가 한 번 빛나고, 경고 아이콘 둘레로 옅은 파문이 한 번 퍼진다.
import { CircleAlert, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui";

export function ReportFailed({
  failedStep,
  pending,
  error,
  onRegenerate,
}: {
  /** 실패 지점: finalize면 확정 단계 문구를 덧붙인다 */
  failedStep?: string | null;
  pending?: boolean;
  /** 재생성 요청 오류 문구 */
  error?: string | null;
  onRegenerate: () => void;
}) {
  return (
    <div
      role="alert"
      className="pop-in mx-auto flex w-full max-w-3xl flex-col items-start gap-4 rounded-xl border border-danger/30 bg-surface p-5 sm:flex-row sm:p-6"
    >
      <span className="relative flex size-10 shrink-0 items-center justify-center rounded-full bg-danger-soft">
        <span
          aria-hidden
          className="absolute inset-0 animate-ping rounded-full bg-danger/20 [animation-iteration-count:2] motion-reduce:animate-none"
        />
        <CircleAlert size={20} className="relative text-danger" aria-hidden />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <p className="text-body font-semibold text-ink">
          리포트를 만들지 못했어요.{failedStep === "finalize" && " 확정 단계에서 멈췄어요."}
        </p>
        <p className="text-sm text-muted">
          다시 만들기를 누르면 리포트를 새로 만들기 시작하고, 준비되면 이 화면에 바로 나타납니다.
        </p>
        {error && <p className="reveal text-sm text-danger">{error}</p>}
        <div className="pt-1">
          <Button size="sm" variant="secondary" loading={pending} onClick={onRegenerate}>
            {!pending && <RotateCcw aria-hidden className="size-3.5" />}
            다시 만들기
          </Button>
        </div>
      </div>
    </div>
  );
}
