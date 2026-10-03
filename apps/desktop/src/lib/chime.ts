// The phone app's reminder sound: two soft bell notes, three times, made with Web Audio (no
// sound file), and a buzz. Browsers only let a page make sound after the user has touched it,
// so `primeChime` runs on the first touch and later reminders can ring.

let context: AudioContext | null = null;

export function primeChime(): void {
  try {
    context ??= new AudioContext();
    void context.resume();
  } catch {
    // No Web Audio: reminders still show, and buzz where the phone allows it.
  }
}

function note(audio: AudioContext, frequency: number, start: number): void {
  const tone = audio.createOscillator();
  const level = audio.createGain();
  tone.type = "sine";
  tone.frequency.value = frequency;
  level.gain.setValueAtTime(0.0001, start);
  level.gain.exponentialRampToValueAtTime(0.35, start + 0.02);
  level.gain.exponentialRampToValueAtTime(0.0001, start + 0.9);
  tone.connect(level).connect(audio.destination);
  tone.start(start);
  tone.stop(start + 1);
}

export function chime(): void {
  try {
    navigator.vibrate?.([300, 150, 300, 150, 300]);
  } catch {
    // Not allowed yet (no touch so far) or no vibration motor.
  }
  primeChime();
  if (!context) return;
  const now = context.currentTime + 0.05;
  for (let round = 0; round < 3; round += 1) {
    note(context, 880, now + round * 0.8);
    note(context, 1318.5, now + round * 0.8 + 0.18);
  }
}
