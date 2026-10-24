// 말하는 동안 받아 적기: 녹음하는 동안 마이크 소리를 16kHz PCM16 조각으로 바꿔 서버로 보낸다.
// 보내기가 한 번이라도 실패하면 더 보내지 않는다. 서버는 빠진 조각을 알아채고 완성 파일 전체로 받아 적는다.
import { downsample, pcm16Base64 } from "./recorder";

// 조각 간격(ms). 작으면 서버가 더 빨리 받지만 요청이 늘고, 크면 녹음 끝에 남는 마지막 조각이 길어진다.
export const STREAM_CHUNK_MS = 500;

// 마이크 소리를 그대로 메인 스레드로 넘기는 오디오 처리기(출력은 무음)
const TAP = `class Tap extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor("pcm-tap", Tap);`;

export type PcmStream = { flush: () => Promise<void>; stop: () => void };

/** 조각 보내기를 시작한다. 브라우저가 오디오 처리기를 지원하지 않으면 예외를 던진다(호출자는 완성 파일만 보냄). */
export async function startPcmStream(
  context: AudioContext,
  source: AudioNode,
  send: (seq: number, pcmBase64: string) => Promise<unknown>,
): Promise<PcmStream> {
  const url = URL.createObjectURL(new Blob([TAP], { type: "text/javascript" }));
  try {
    await context.audioWorklet.addModule(url);
  } finally {
    URL.revokeObjectURL(url);
  }
  const node = new AudioWorkletNode(context, "pcm-tap");
  let pending: Float32Array[] = [];
  let seq = 0;
  let failed = false;
  let stopped = false;
  let chain: Promise<void> = Promise.resolve();
  node.port.onmessage = (event: MessageEvent<Float32Array>) => pending.push(event.data);
  source.connect(node);
  node.connect(context.destination); // 처리기가 돌도록 연결만 한다(처리기 출력은 무음)

  const ship = () => {
    if (failed || !pending.length) return;
    const merged = new Float32Array(pending.reduce((n, chunk) => n + chunk.length, 0));
    let at = 0;
    for (const chunk of pending) {
      merged.set(chunk, at);
      at += chunk.length;
    }
    pending = [];
    const body = pcm16Base64(downsample(merged, context.sampleRate));
    const n = seq++;
    // 순서대로 하나씩 보낸다. 서버는 순번이 빠지면 조각 경로를 버린다.
    chain = chain.then(async () => {
      if (failed) return;
      try {
        await send(n, body);
      } catch {
        failed = true;
      }
    });
  };
  const timer = setInterval(ship, STREAM_CHUNK_MS);
  return {
    flush: async () => {
      ship();
      await chain;
    },
    // 녹음 종료와 메시지 제출(stopMedia) 때 두 번 불린다. 이미 끊은 노드를 다시 끊으면 브라우저가
    // 예외를 던져 음성 상태 확인이 멈추므로 한 번만 정리한다.
    stop: () => {
      if (stopped) return;
      stopped = true;
      clearInterval(timer);
      node.port.onmessage = null;
      source.disconnect(node);
      node.disconnect();
    },
  };
}
