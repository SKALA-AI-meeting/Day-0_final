"use client";

import { useState } from "react";

import { assetUrl } from "@/lib/api";

import { cn } from "./cn";

/** 옛 화면의 사람 종류 색. 새 화면은 seat를 쓴다 */
export type AvatarTone = "manager" | "colleague" | "candidate" | "system";
/** 자리 색(D13-1). AI 동료 틴트는 페르소나 유형이 아니라 자리(p1~p3)에 붙는다 */
export type AvatarSeat = "p1" | "p2" | "p3" | "candidate";

const TONE: Record<AvatarTone, string> = {
  manager: "bg-person-manager",
  colleague: "bg-person-colleague",
  candidate: "bg-person-candidate",
  system: "bg-person-system",
};

const SEAT: Record<AvatarSeat, string> = {
  p1: "bg-seat-p1",
  p2: "bg-seat-p2",
  p3: "bg-seat-p3",
  candidate: "bg-seat-candidate",
};

const SIZE = {
  20: "size-5 text-[10px]",
  28: "size-7 text-xs",
  36: "size-9 text-sm",
  // 부모 크기를 채운다(크기가 바뀌는 프로필 카드). 글자 크기는 부모가 정한다
  fill: "size-full",
} as const;

/** 이름의 첫 글자(문자 단위). 별칭이 비어 있으면 "?" */
export function initial(name: string) {
  const first = name.trim();
  if (!first) return "?";
  const Segmenter = (Intl as { Segmenter?: typeof Intl.Segmenter }).Segmenter;
  if (Segmenter) {
    const [segment] = new Segmenter("ko", { granularity: "grapheme" }).segment(first);
    return segment?.segment ?? "?";
  }
  return first[0];
}

/** 그릴 이미지 주소. 비었거나 이미 로드에 실패한 주소면 null(이니셜만 보인다) */
export function visibleSrc(src: string | null | undefined, failedSrc: string | null | undefined) {
  if (!src) return null;
  return src === failedSrc ? null : src;
}

export type AvatarProps = {
  /** 이니셜로 쓸 글자. 없으면 name의 첫 글자 */
  label?: string;
  /** 옛 사람 종류 색. seat가 있으면 seat가 우선 */
  tone?: AvatarTone;
  /** 자리 색(p1·p2·p3·candidate) */
  seat?: AvatarSeat;
  /** 프로필 이미지(avatar_url). 없거나 로드 실패면 같은 크기 이니셜 */
  src?: string | null;
  size?: keyof typeof SIZE;
  online?: boolean;
  /** 화면 낭독용 이름 */
  name?: string;
  /** 마우스를 올리면 보이는 이름(AvatarGroup이 채운다) */
  title?: string;
  className?: string;
};

/**
 * 원형 크롭·고정 크기 아바타(D13-3). 이미지는 교체될 임시 에셋이라 비율·화풍에 기대지 않는다:
 * 이니셜을 늘 아래에 깔고 이미지를 cover로 덮어, 실패하면 이미지만 걷어낸다.
 */
export function Avatar({
  label,
  tone,
  seat,
  src,
  size = 28,
  online,
  name,
  title,
  className,
}: AvatarProps) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const text = label ?? initial(name ?? "");
  const image = visibleSrc(src, failedSrc);
  const color = seat ? SEAT[seat] : TONE[tone ?? "system"];
  return (
    <span
      role="img"
      aria-label={name ?? text}
      title={title}
      className={cn(
        "relative inline-flex shrink-0 rounded-full font-semibold text-on-seat select-none",
        SIZE[size],
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "relative inline-flex size-full items-center justify-center overflow-hidden rounded-full",
          color,
        )}
      >
        {text}
        {image && (
          // 임의 주소(avatar_url)를 고정 크기로만 그리므로 next/image 최적화가 필요 없다
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={assetUrl(image)}
            alt=""
            draggable={false}
            onError={() => setFailedSrc(image)}
            className="absolute inset-0 size-full rounded-full object-cover"
          />
        )}
      </span>
      {online && (
        <span className="absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full border-2 border-surface bg-success" />
      )}
    </span>
  );
}

export type AvatarGroupProps = {
  people: Omit<AvatarProps, "size">[];
  size?: 20 | 28;
  className?: string;
};

/** 참여자 묶음. 겹침 -6px, 각 아바타에 이름 툴팁(design-system.md) */
export function AvatarGroup({ people, size = 20, className }: AvatarGroupProps) {
  return (
    <span className={cn("inline-flex items-center", className)}>
      {people.map((person, i) => (
        <Avatar
          key={`${person.seat ?? person.tone ?? "p"}-${i}`}
          title={person.name}
          {...person}
          size={size}
          className={cn("ring-2 ring-surface", i > 0 && "-ml-1.5", person.className)}
        />
      ))}
    </span>
  );
}
