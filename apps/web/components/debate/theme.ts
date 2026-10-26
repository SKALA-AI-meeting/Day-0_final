// 토론 화면의 무드(색 계층, globals.css "무드"). 키워드 단계에서 고른 키워드(물·불·나무·바람)가 테마가 되고,
// 그 뒤 단계(문제 상황·해결책·기획안·마무리)는 같은 테마 안의 톤으로 바뀐다(예: water-problem은 짙은 폭풍빛,
// water-solution은 맑은 물빛). 키워드를 고르기 전은 네 키워드 색을 조금씩 섞은 keyword, 인사 동안은 dawn이다.
import type { StateSnapshot } from "./types";

export const THEMES = ["water", "fire", "tree", "wind"] as const;
export type ThemeKey = (typeof THEMES)[number];

/** 확정된 키워드 칸의 값(라벨)을 키워드 후보의 option_key로 바꾼다. 네 테마 밖(자유 키워드)이면 null */
export function themeKey(snap: Pick<StateSnapshot, "slots" | "keyword_options">): ThemeKey | null {
  const slot = snap.slots.find((s) => s.phase === "keyword" && s.status === "confirmed");
  if (!slot?.value) return null;
  const key = snap.keyword_options.find((o) => o.label === slot.value)?.option_key;
  return (THEMES as readonly string[]).includes(key ?? "") ? (key as ThemeKey) : null;
}

/** data-mood 값. 테마가 있으면 `<테마>-<단계>`, 없으면 단계 무드 */
export function moodFor({
  intro,
  ended,
  phase,
  theme,
}: {
  intro: boolean;
  ended: boolean;
  phase: string | null;
  theme: ThemeKey | null;
}): string {
  if (intro) return "dawn";
  const tone = ended ? "debrief" : (phase ?? "keyword");
  if (tone === "keyword") return "keyword";
  return theme ? `${theme}-${tone}` : tone;
}
