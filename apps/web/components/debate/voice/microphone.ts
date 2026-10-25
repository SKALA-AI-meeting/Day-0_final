export const MICROPHONE_TIMEOUT_MS = 8000;
export const MICROPHONE_HELP = "주소창이나 시스템 설정에서 마이크를 허용해 주세요";

export class MicrophoneError extends Error {}
export function isMicrophoneError(error: unknown): error is MicrophoneError {
  return error instanceof MicrophoneError;
}

/** 권한 창이 응답하지 않아도 입장과 발언 버튼을 다시 사용할 수 있게 한다. */
export async function requestMicrophone(
  constraints: MediaStreamConstraints = { audio: true, video: false },
): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) throw new MicrophoneError("microphone_unavailable");
  let timer: ReturnType<typeof setTimeout>;
  let expired = false;
  const request = navigator.mediaDevices.getUserMedia(constraints).then(
    (stream) => {
      if (expired) stream.getTracks().forEach((track) => track.stop());
      return stream;
    },
    (error: unknown) => {
      throw new MicrophoneError(String(error));
    },
  );
  try {
    return await Promise.race([
      request,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
          expired = true;
          reject(new MicrophoneError("microphone_timeout"));
        }, MICROPHONE_TIMEOUT_MS);
      }),
    ]);
  } finally {
    expired = true;
    clearTimeout(timer!);
  }
}
