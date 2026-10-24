import { describe, expect, it, vi } from "vitest";
import { downsample, encodeWav, finishOnce, pcm16Base64 } from "./recorder";

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

describe("조각 전송용 소리 변환", () => {
  it("48kHz를 16kHz로 줄이면 길이가 3분의 1이고 구간 평균을 쓴다", () => {
    const input = new Float32Array([0.3, 0.3, 0.3, -0.6, -0.6, -0.6]);
    expect(Array.from(downsample(input, 48000))).toEqual([
      expect.closeTo(0.3),
      expect.closeTo(-0.6),
    ]);
    expect(downsample(input, 16000)).toBe(input);
  });

  it("PCM16 리틀 엔디언 원본으로 바꾸고 범위를 넘는 값은 자른다", () => {
    const bytes = Uint8Array.from(atob(pcm16Base64(new Float32Array([1, -1, 2, 0]))), (c) =>
      c.charCodeAt(0),
    );
    const view = new DataView(bytes.buffer);
    expect([0, 2, 4, 6].map((i) => view.getInt16(i, true))).toEqual([32767, -32768, 32767, 0]);
  });
});
