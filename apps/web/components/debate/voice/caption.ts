// 동료 발언 자막: 발언을 문장으로 나누고, 재생 위치(0~1)에 해당하는 문장 하나를 고른다.
// 합성 음성은 말 빠르기가 고르므로 글자 수 비율로 문장 경계를 어림한다.

/** 문장 끝(. ? ! …) 뒤 공백에서 나눈다. 빈 문장은 버린다 */
export function sentences(text: string): string[] {
  return text
    .split(/(?<=[.?!…])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** 재생 위치(0~1)에 해당하는 문장. 앞 문장들의 글자 수 합이 위치를 넘는 첫 문장을 고른다 */
export function captionAt(parts: string[], progress: number): string {
  if (!parts.length) return "";
  const total = parts.reduce((sum, s) => sum + s.length, 0);
  let passed = 0;
  for (const part of parts) {
    passed += part.length;
    if (passed / total > progress) return part;
  }
  return parts[parts.length - 1];
}
