// 동료 발언을 대화 기록에 한 문장씩: 발언을 문장으로 나누고, 재생 위치(0~1)까지 말한 문장 수를 구한다.
// 합성 음성은 말 빠르기가 고르므로 글자 수 비율로 문장 경계를 어림한다.

/** 문장 끝(. ? ! …) 뒤 공백에서 나눈다. 빈 문장은 버린다 */
export function sentences(text: string): string[] {
  return text
    .split(/(?<=[.?!…])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** 재생 위치(0~1)에서 보여 줄 문장 수(지금 말하는 문장까지). 문장이 없으면 0 */
export function spokenCount(parts: string[], progress: number): number {
  if (!parts.length) return 0;
  const total = parts.reduce((sum, s) => sum + s.length, 0);
  let passed = 0;
  for (let i = 0; i < parts.length; i++) {
    passed += parts[i].length;
    if (passed / total > progress) return i + 1;
  }
  return parts.length;
}

/** 동료 본문은 재생 완료 또는 현재 재생 중인 범위만 화면에 전달한다. */
export function heardMessages<T extends { id: string; sender: string; content: string }>(
  messages: T[],
  heardIds: string[],
  speaking: { id: string; text: string } | null,
): T[] {
  const heard = new Set(heardIds);
  return messages
    .filter(
      (m) => !["p1", "p2", "p3"].includes(m.sender) || heard.has(m.id) || m.id === speaking?.id,
    )
    .map((m) => (m.id === speaking?.id && !heard.has(m.id) ? { ...m, content: speaking.text } : m));
}
