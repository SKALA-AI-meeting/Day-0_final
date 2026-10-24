"use client";

// 토론방에 들어가기 전 화면: 링크 안내(만료·취소·없음), 입장, 동의(표시 이름·형식 안내), 대기(팀 소개).
// 문구는 docs/product/user-flows.md "지원자: 입장·동의·대기". 페르소나 유형·성향 설명은 여기 없다.
import { useState, type ReactNode } from "react";

import { Avatar, Button, Wordmark } from "@/components/ui";

import { PHASE_LABEL } from "./state";
import type { Entry, Participant, StateSnapshot } from "./types";

/** R1 형식 안내(고정 문구) */
export const FORMAT_NOTICE =
  "토론 결과가 내 제안과 다를 수 있어요. 안의 질은 평가하지 않으며, 그 과정의 태도를 봅니다.";
/** R7 어시스턴트 안내(고정 문구) */
export const ASSISTANT_NOTICE =
  "AI가 평가 수준을 매기지 않으며, 요청·응답 기록은 담당자에게 사실로 제공됩니다.";

export const NAME_MAX = 20;

function Page({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-xl flex-col justify-center gap-8 px-6 py-16">
      <Wordmark className="h-5 self-start" />
      {children}
    </main>
  );
}

function Rows({ items }: { items: { title?: string; body: ReactNode }[] }) {
  return (
    <ul className="flex flex-col divide-y divide-line rounded-lg border border-line bg-surface">
      {items.map((item, i) => (
        <li key={i} className="flex flex-col gap-1 px-5 py-4">
          {item.title && <p className="text-footnote font-semibold text-muted">{item.title}</p>}
          <div className="text-body">{item.body}</div>
        </li>
      ))}
    </ul>
  );
}

export function LinkNotice({
  title,
  description,
  onRetry,
}: {
  title: string;
  description: string;
  onRetry?: () => void;
}) {
  return (
    <Page>
      <h1 className="text-title1 font-bold">{title}</h1>
      <p className="text-muted">{description}</p>
      {onRetry && (
        <div>
          <Button variant="secondary" onClick={onRetry}>
            다시 시도
          </Button>
        </div>
      )}
    </Page>
  );
}

function Mail({ to }: { to: string }) {
  return (
    <a className="text-accent underline-offset-4 hover:underline" href={`mailto:${to}`}>
      {to}
    </a>
  );
}

export function EntryScreen({ entry, onNext }: { entry: Entry; onNext: () => void }) {
  return (
    <Page>
      <div className="flex flex-col gap-3">
        <p className="text-footnote font-semibold text-muted">{entry.company_name}</p>
        <h1 className="text-large font-bold">새 프로젝트 팀 토론</h1>
        <p className="text-callout text-muted">
          {entry.duration_minutes}분 · AI 동료 3명과 음성 토론
        </p>
      </div>
      <ul className="flex flex-col gap-2 text-callout leading-relaxed">
        {entry.entry_notice.split(/(?<=\.)\s+/).map((sentence) => (
          <li key={sentence}>{sentence}</li>
        ))}
      </ul>
      <div>
        <Button onClick={onNext}>다음</Button>
      </div>
    </Page>
  );
}

export function ConsentScreen({
  entry,
  pending,
  error,
  onConsent,
}: {
  entry: Entry;
  pending?: boolean;
  error?: string | null;
  onConsent: (displayName: string) => void;
}) {
  const [name, setName] = useState(entry.default_display_name.slice(0, NAME_MAX));
  const [agreed, setAgreed] = useState(false);
  const trimmed = name.replace(/\s+/g, " ").trim();
  const nameError =
    trimmed.length === 0
      ? "이름을 입력해 주세요."
      : trimmed.length > NAME_MAX
        ? `${NAME_MAX}자 이하로 입력해 주세요.`
        : null;
  return (
    <Page>
      <h1 className="text-title1 font-bold">시작 전에 확인해 주세요</h1>
      <Rows
        items={[
          { title: "형식", body: FORMAT_NOTICE },
          {
            title: "기록되는 것",
            body: "음성 발언의 최종 전사, 결정 사항 선택, 기획안, AI 어시스턴트 요청 원문, 자료 열람. 음성은 Azure Speech에서 처리하며 원음은 전사 성공 시 삭제하고, 처리 실패 시 최대 24시간 임시 보관합니다.",
          },
          {
            title: "평가 방식",
            body: (
              <>
                세션이 끝난 뒤 AI 평가 에이전트가 공개된 척도로 근거 메시지를 들어 요소별 수준을
                판단하고, 담당자가 그것을 참고해 최종 평가합니다.
                <span className="mt-1 block text-footnote text-muted">
                  보는 요소: {entry.elements_names.join(" · ")}
                </span>
              </>
            ),
          },
          { title: "AI 어시스턴트", body: ASSISTANT_NOTICE },
          {
            title: "만들지 않는 것",
            body: "합격·순위는 만들지 않습니다. 리포트는 담당자가 평가할 때 보는 참고 자료입니다.",
          },
        ]}
      />
      <ul className="flex flex-col gap-1.5 text-footnote text-muted">
        <li>외부 도구는 막지 않습니다. 기록되는 것은 이 화면 안의 AI 어시스턴트 요청뿐입니다.</li>
        <li>
          문의: <Mail to={entry.contact_email} />
        </li>
        <li>동의하지 않으면 세션이 시작되지 않으며, 링크는 만료 전까지 다시 열 수 있습니다.</li>
      </ul>
      <label className="flex flex-col gap-2">
        <span className="text-footnote font-semibold">토론방에서 보일 이름</span>
        <input
          value={name}
          maxLength={NAME_MAX + 5}
          onChange={(event) => setName(event.target.value)}
          aria-invalid={!!nameError}
          className="h-11 rounded-md border border-line bg-surface px-3 text-callout outline-none focus-visible:border-accent"
        />
        <span className={`text-footnote ${nameError ? "text-danger" : "text-muted"}`}>
          {nameError ??
            `AI 동료가 이 이름으로 부릅니다. 시작한 뒤에는 바꿀 수 없어요. (1~${NAME_MAX}자)`}
        </span>
      </label>
      <label className="flex items-center gap-2 text-body">
        <input
          type="checkbox"
          className="size-4 accent-[var(--color-accent)]"
          checked={agreed}
          onChange={(event) => setAgreed(event.target.checked)}
        />
        위 내용을 읽었고 동의합니다
      </label>
      {error && (
        <p role="alert" className="text-footnote text-danger">
          {error}
        </p>
      )}
      <div>
        <Button
          disabled={!agreed || !!nameError}
          loading={pending}
          onClick={() => onConsent(trimmed)}
        >
          동의하고 계속
        </Button>
      </div>
    </Page>
  );
}

/** 화면 구성 그림: 상단 진행바 / 가운데 토론방 / 우측 탭 */
export function LayoutSketch() {
  const bar = "h-1.5 rounded-full bg-line";
  return (
    <figure className="flex flex-col gap-3">
      <div
        aria-hidden
        className="flex flex-col overflow-hidden rounded-lg border border-line bg-surface"
      >
        <div className="flex items-center gap-2 border-b border-line px-3 py-2">
          <span className="text-caption font-semibold text-muted">단계 진행바</span>
          <span className={`${bar} w-24`} />
          <span className="ml-auto text-caption text-muted tabular-nums">남은 시간</span>
        </div>
        <div className="grid grid-cols-[1.7fr_1fr]">
          <div className="flex flex-col gap-2 border-r border-line p-3">
            <span className="text-caption font-semibold text-muted">음성 회의</span>
            <div className="grid grid-cols-2 gap-2">
              {["나", "강한결", "윤하람", "서지안"].map((name) => (
                <span
                  key={name}
                  className="rounded-md border border-line p-2 text-center text-caption"
                >
                  {name}
                </span>
              ))}
            </div>
            <span className="text-caption text-muted">내 발언 남은 시간 · 발언 완료</span>
          </div>
          <div className="flex flex-col gap-2 p-3">
            <span className="text-caption font-semibold text-muted">우측 탭</span>
            <span className="text-caption text-muted">
              결정 사항 · 자료 · AI 어시스턴트 · 기획안
            </span>
            <span className={`${bar} w-4/5`} />
            <span className={`${bar} w-3/5`} />
          </div>
        </div>
      </div>
      <figcaption className="flex flex-col gap-1 text-footnote text-muted">
        <span>상단: 단계 진행바와 이 단계의 남은 시간</span>
        <span>가운데: 음성 회의(AI 동료 3명과 나)</span>
        <span>오른쪽: 결정 사항 · 자료 · AI 어시스턴트 · 기획안 탭</span>
      </figcaption>
    </figure>
  );
}

function minutesText(min: number) {
  const whole = Math.floor(min);
  const sec = Math.round((min - whole) * 60);
  return sec ? `${whole}분 ${sec}초` : `${whole}분`;
}

/** 대기 화면과 토론 중 "?" 도움말이 같이 쓰는 설명 */
export function SessionGuide({
  snap,
}: {
  snap: Pick<StateSnapshot, "participants" | "phases" | "session">;
}) {
  const proposal = snap.phases.find((p) => p.phase === "proposal");
  const order = snap.phases
    .filter((p) => p.phase !== "proposal")
    .map((p) => PHASE_LABEL[p.phase])
    .join(" → ");
  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <h2 className="text-title3 font-semibold">새 프로젝트 팀</h2>
        <ul className="flex flex-wrap gap-x-6 gap-y-3">
          {snap.participants.map((p) => (
            <TeamMember key={p.id} person={p} />
          ))}
        </ul>
        <p className="text-footnote text-muted">
          사람 팀원 역할을 맡은 AI입니다. 실제 동료처럼 대해 주세요.
        </p>
      </section>
      <p className="text-body">
        {order}을 함께 정하고, 마지막 {minutesText(proposal?.budget_min ?? 14)}에 기획안을 혼자 써서
        제출합니다.
      </p>
      <LayoutSketch />
      <p className="text-body">
        상단에는 이 단계의 남은 시간이 보입니다. 동료의 음성이 모두 끝나면 마이크가 켜집니다. 최대
        60초 동안 말할 수 있고, 발언 완료를 누르면 바로 제출합니다.
      </p>
    </div>
  );
}

function TeamMember({ person }: { person: Participant }) {
  const me = person.id === "candidate";
  return (
    <li className="flex items-center gap-2">
      <Avatar size={36} seat={person.id} src={person.avatar_url} name={person.display_name} />
      <span className="text-body">
        {person.display_name}
        {me && <span className="ml-1 text-footnote text-muted">(나)</span>}
      </span>
    </li>
  );
}

export function WaitingScreen({
  snap,
  pending,
  error,
  onStart,
}: {
  snap: StateSnapshot;
  pending?: boolean;
  error?: string | null;
  onStart: () => void;
}) {
  return (
    <Page>
      <div className="flex flex-col gap-2">
        <h1 className="text-title1 font-bold">시작할 준비가 됐어요</h1>
        <p className="text-muted">시작하면 바로 시간이 흐르고, 접속이 끊겨도 멈추지 않습니다.</p>
      </div>
      <SessionGuide snap={snap} />
      <p className="text-footnote text-muted">이 설명은 화면의 ? 버튼으로 다시 볼 수 있습니다.</p>
      {error && (
        <p role="alert" className="text-footnote text-danger">
          {error}
        </p>
      )}
      <div>
        <Button loading={pending} onClick={onStart}>
          시작하기
        </Button>
      </div>
    </Page>
  );
}
