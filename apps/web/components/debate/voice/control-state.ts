export function canStartVoice(phase: string | null): boolean {
  return phase !== "proposal";
}

export function turnHoldNotice(secondsLeft: number): string {
  return secondsLeft <= 0
    ? "곧 동료가 이어서 말해요…"
    : `${Math.ceil(secondsLeft)}초 안에 시작하지 않으면 동료가 이어서 말해요`;
}
