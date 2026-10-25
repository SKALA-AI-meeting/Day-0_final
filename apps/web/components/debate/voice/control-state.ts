// 음성 토론 조작부의 상태 판단(화면 없이 단위 테스트한다): 녹음 시작 가능 여부, 차례 유지 안내,
// 막힌 상태(blocked_reason)별 오류 문구와 그 오류를 푸는 버튼 하나.
export function canStartVoice(phase: string | null): boolean {
  return phase !== "proposal";
}

export function turnHoldNotice(secondsLeft: number): string {
  return secondsLeft <= 0
    ? "곧 동료가 이어서 말해요…"
    : `${Math.ceil(secondsLeft)}초 안에 시작하지 않으면 동료가 이어서 말해요`;
}

/** 오류를 푸는 동작. 버튼 이름은 동작마다 하나다(겸용 이름 "다시 시도 / 발언 시작"을 쓰지 않는다) */
export type Action = "resend" | "replay" | "retranscribe" | "rerecord" | "reconnect" | "start";

export const ACTION_LABEL: Record<Action, string> = {
  resend: "다시 보내기",
  replay: "다시 재생",
  retranscribe: "다시 받아 적기",
  rerecord: "다시 녹음",
  reconnect: "다시 연결",
  start: "말하기 시작",
};

/** 진행을 막는 오류: 짧은 제목, 지원자가 할 일, 그 일을 하는 버튼(없으면 기다리면 된다) */
export type Trouble = { title: string; detail: string; action: Action | null };

/**
 * 서버가 막아 둔 내 차례(blocked_reason)를 지원자에게 보일 오류로. 빈 인식 결과는 오류가 아니라
 * 확인 칸(직접 입력)과 [다시 녹음]으로 보이므로 null. 내 차례가 아니면 버튼 없이 안내만 한다.
 */
export function blockedTrouble(reason: string | undefined, myTurn: boolean): Trouble | null {
  const act = (action: Action) => (myTurn ? action : null);
  switch (reason) {
    case "error":
      return {
        title: "발언을 글로 옮기지 못했어요",
        detail: "같은 녹음으로 다시 받아 적어 볼 수 있어요.",
        action: act("retranscribe"),
      };
    case "voice_recording_failed":
      return {
        title: "녹음을 받지 못했어요",
        detail: "마이크 연결을 확인하고 다시 녹음해 주세요.",
        action: act("rerecord"),
      };
    case "voice_interrupted":
      return {
        title: "연결이 잠시 끊겼어요",
        detail: "끊긴 동안의 발언은 저장되지 않았어요. 다시 말해 주세요.",
        action: act("rerecord"),
      };
    default:
      return null;
  }
}

/** 포커스가 여기 있으면 Space가 그 요소의 동작(입력·누르기·펼치기·탭 전환)이므로 단축키로 쓰지 않는다 */
const INTERACTIVE =
  "input, textarea, select, button, summary, a[href], [role='button'], [role='tab'], " +
  "[role='textbox'], [role='checkbox'], [role='switch'], [role='menuitem'], [role='option'], " +
  "[role='slider'], [contenteditable]:not([contenteditable='false'])";
/** 열려 있는 대화상자·팝업. 이 중 하나라도 있으면 단축키를 쓰지 않는다(뒤 화면의 녹음을 켜지 않음) */
const OPEN_LAYER =
  "[role='dialog'], [role='alertdialog'], dialog[open], [aria-modal='true'], [data-state='open']";

/** Space로 [발언하기]를 누르지 말아야 하는지: 상호작용 요소에 포커스가 있거나 대화상자가 열려 있음 */
export function spaceShortcutBlocked(
  target: EventTarget | null,
  root: Pick<ParentNode, "querySelector"> | null = typeof document === "undefined"
    ? null
    : document,
): boolean {
  if (root?.querySelector(OPEN_LAYER)) return true;
  if (!target || typeof (target as Element).closest !== "function") return false;
  const el = target as HTMLElement;
  return el.isContentEditable || !!el.closest(INTERACTIVE);
}
