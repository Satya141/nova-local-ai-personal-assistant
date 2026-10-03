// The phone app's mic button: records one spoken message and stops by itself when the user
// stops talking. The recording goes only to the user's PC, whose own Whisper turns it into
// text (POST /api/transcribe); it is never stored, on the phone or the PC.

const SPEECH_LEVEL = 0.03; // louder than this counts as talking
const QUIET_LEVEL = 0.015; // quieter than this counts as a pause
const END_AFTER_PAUSE_MS = 1400;
const GIVE_UP_MS = 7000; // nothing said at all
const LONGEST_MS = 20000;

export type Recording = {
  /** Resolves with the recording when the user stops talking (or taps stop), or null if nothing was said. */
  done: Promise<Blob | null>;
  stop: () => void;
  cancel: () => void;
};

export function micSupported(): boolean {
  return typeof window !== "undefined" && window.isSecureContext && !!navigator.mediaDevices?.getUserMedia && "MediaRecorder" in window;
}

export async function record(): Promise<Recording> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
  });
  const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((t) => MediaRecorder.isTypeSupported(t));
  const recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  const chunks: Blob[] = [];
  recorder.ondataavailable = (event) => event.data.size && chunks.push(event.data);

  // Listen to the level to notice the end of the sentence.
  const audio = new AudioContext();
  const analyser = audio.createAnalyser();
  analyser.fftSize = 1024;
  audio.createMediaStreamSource(stream).connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  const started = Date.now();
  let spoke = false;
  let quietSince = 0;
  let cancelled = false;

  const finish = () => {
    clearInterval(timer);
    if (recorder.state !== "inactive") recorder.stop();
  };
  const timer = setInterval(() => {
    analyser.getFloatTimeDomainData(samples);
    const level = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
    const now = Date.now();
    if (level > SPEECH_LEVEL) {
      spoke = true;
      quietSince = 0;
    } else if (spoke && level < QUIET_LEVEL) {
      quietSince ||= now;
    }
    if ((spoke && quietSince && now - quietSince > END_AFTER_PAUSE_MS) || (!spoke && now - started > GIVE_UP_MS) || now - started > LONGEST_MS) {
      finish();
    }
  }, 100);

  const done = new Promise<Blob | null>((resolve) => {
    recorder.onstop = () => {
      // Release the microphone at once: Android's mic indicator goes off with it.
      stream.getTracks().forEach((track) => track.stop());
      void audio.close().catch(() => {});
      resolve(cancelled || !spoke ? null : new Blob(chunks, { type: recorder.mimeType || type || "audio/webm" }));
    };
  });
  recorder.start(250);
  return {
    done,
    stop: () => {
      spoke = true; // a tap on stop means "that was it", even if it was quiet
      finish();
    },
    cancel: () => {
      cancelled = true;
      finish();
    },
  };
}
