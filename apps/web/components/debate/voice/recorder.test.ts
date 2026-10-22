import { describe, expect, it, vi } from "vitest";
import { encodeWav, finishOnce } from "./recorder";

describe("최종 음성 제출", () => {
  it("버튼과 타이머가 겹쳐도 녹음 종료는 한 번만 실행한다", () => {
    const submit = vi.fn();
    const finish = finishOnce(submit);
    finish();
    finish();
    expect(submit).toHaveBeenCalledTimes(1);
  });
  it("완성 오디오를 mono 16kHz PCM WAV로 만들고 클리핑을 제한한다", () => {
    const data = new DataView(encodeWav(new Float32Array([-2, 0, 2])));
    expect(data.getUint32(24, true)).toBe(16000);
    expect(data.getUint16(22, true)).toBe(1);
    expect(data.getUint32(40, true)).toBe(6);
    expect(data.getInt16(44, true)).toBe(-32768);
    expect(data.getInt16(48, true)).toBe(32767);
  });
});
