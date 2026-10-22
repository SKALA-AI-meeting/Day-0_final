// 브라우저 최종 녹음 파일을 공급자 독립 PCM16 mono 16kHz WAV로 정규화한다.
export function encodeWav(samples: Float32Array): ArrayBuffer {
  const out = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(out);
  const text = (at: number, value: string) => {
    for (let i = 0; i < value.length; i++) view.setUint8(at + i, value.charCodeAt(i));
  };
  text(0, "RIFF");
  view.setUint32(4, out.byteLength - 8, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true);
  view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, i) =>
    view.setInt16(
      44 + i * 2,
      Math.round(Math.max(-1, Math.min(1, sample)) * (sample < 0 ? 32768 : 32767)),
      true,
    ),
  );
  return out;
}

export async function normalize(blob: Blob, maxSeconds: number): Promise<string> {
  const context = new AudioContext();
  try {
    const decoded = await context.decodeAudioData(await blob.arrayBuffer());
    const frames = Math.min(
      Math.floor(maxSeconds * 16000),
      Math.ceil(decoded.duration * 16000),
      960000,
    );
    if (frames <= 0) throw new Error("녹음된 발언이 없습니다");
    const offline = new OfflineAudioContext(1, frames, 16000);
    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination);
    source.start();
    const wav = new Uint8Array(encodeWav((await offline.startRendering()).getChannelData(0)));
    let binary = "";
    for (let i = 0; i < wav.length; i += 8192)
      binary += String.fromCharCode(...wav.subarray(i, i + 8192));
    return btoa(binary);
  } finally {
    await context.close();
  }
}

export function finishOnce(finish: () => void) {
  let finished = false;
  return () => {
    if (!finished) {
      finished = true;
      finish();
    }
  };
}
