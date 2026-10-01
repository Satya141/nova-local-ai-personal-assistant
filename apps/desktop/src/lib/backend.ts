import { invoke, isTauri } from "@tauri-apps/api/core";

/** What the desktop shell tells the UI on startup. */
export type Session = {
  url: string;
  token: string;
  /** Why the backend could not be started, if it could not. */
  error: string | null;
  /** The global shortcut that is registered, e.g. "Alt+Space". Empty if none. */
  shortcut: string;
};

export type Health = {
  model: string;
  model_ready: boolean;
  detail: string | null;
  /** Whether NOVA can look at the screen. */
  vision_ready: boolean;
};

export type Risk = "low" | "medium" | "high";

/** Mirrors the events documented in backend/nova/agent/loop.py. */
export type AgentEvent =
  | { type: "conversation"; id: string }
  | { type: "token"; text: string }
  | { type: "tool_call"; id: string; name: string; summary: string }
  // `warning`: NOVA read a web page or the screen during this request, which may be behind it.
  | { type: "confirm_request"; id: string; name: string; summary: string; risk: Risk; warning?: string }
  // `blocked`: refused outright for safety, without asking.
  | { type: "tool_result"; id: string; name: string; ok: boolean; blocked?: boolean }
  | { type: "error"; message: string }
  | { type: "done" };

export type Memory = {
  id: number;
  category: string;
  content: string;
  source: "explicit" | "extracted";
  created_at: string;
  updated_at: string;
};

export type Reminder = {
  id: number;
  text: string;
  due_at: string;
  repeat: "none" | "daily" | "weekdays" | "weekly";
  status: "active" | "done" | "cancelled";
  pending_ack: boolean;
  /** When it was due, for a reminder that has fired. */
  fired_at: string | null;
  /** Set for a scheduled task: the request NOVA carried out by itself. */
  task: string | null;
  /** What NOVA found the last time it ran the task. */
  result: string | null;
};

/** Mirrors VoiceState in backend/nova/voice/service.py. */
export type VoiceState = "off" | "waiting" | "listening" | "transcribing" | "speaking";

export type VoiceStatus = {
  available: boolean;
  detail: string | null;
  wake_word: boolean;
  state: VoiceState;
  can_speak: boolean;
};

/** Things the backend says outside a chat turn (GET /api/events). */
export type ServerEvent =
  | { type: "reminder"; reminder: Reminder }
  | { type: "memory_saved"; conversation_id: string; memory: Memory }
  | { type: "voice_state"; state: VoiceState; wake_word: boolean }
  // An empty text means NOVA was listening but caught nothing.
  | { type: "voice_command"; text: string; source: "wake" | "button" }
  | { type: "voice_error"; message: string }
  // Phone access changed by itself (the PC moved to another network). Only sent to the PC.
  | { type: "phones"; status: PhoneAccessStatus }
  | { type: "ping" };

let cached: Promise<Session> | null = null;

// --- the phone app ----------------------------------------------------------------------
// On a phone the UI is served by NOVA itself, so the backend is this page's own origin, and
// the key is the one this phone received when it was paired.

const PHONE_KEY = "nova-phone-key";

/** Running as the phone app (served by NOVA over the home network), not inside the desktop shell. */
export function isPhone(): boolean {
  return !isTauri() && typeof window !== "undefined" && window.location.pathname.startsWith("/phone");
}

export class NotPaired extends Error {
  constructor() {
    super("This phone is not paired with NOVA.");
  }
}

function phoneKey(): string | null {
  try {
    return localStorage.getItem(PHONE_KEY);
  } catch {
    return null;
  }
}

export function forgetPhoneKey(): void {
  try {
    localStorage.removeItem(PHONE_KEY);
  } catch {
    // Nothing stored, or storage blocked: the phone simply asks to pair again.
  }
}

/** Trade the six-digit code shown on the PC for this phone's own key. */
export async function pairPhone(code: string, name: string): Promise<void> {
  const response = await fetch("/api/pair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, name }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.detail ?? `HTTP ${response.status}`);
  localStorage.setItem(PHONE_KEY, body.token);
}

export function session(): Promise<Session> {
  if (isTauri()) return (cached ??= invoke<Session>("session"));
  if (isPhone()) {
    const token = phoneKey();
    return token ? Promise.resolve({ url: "", token, error: null, shortcut: "" }) : Promise.reject(new NotPaired());
  }
  return Promise.reject(new Error("NOVA's interface only works inside the desktop app or the phone app."));
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const { url, token } = await session();
  const response = await fetch(url + path, {
    ...init,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
  });
  // The PC removed this phone: drop the key, so the app asks to pair again.
  if (response.status === 401 && isPhone()) {
    forgetPhoneKey();
    throw new NotPaired();
  }
  return response;
}

// --- phone access, managed on the PC -------------------------------------------------------

export type PhoneDevice = { id: number; name: string; created_at: string; last_seen: string | null };
export type PhoneAccessStatus = {
  enabled: boolean;
  running: boolean;
  /** The setup page a phone opens first (plain HTTP); it hands over to `app_url`. */
  url: string | null;
  /** The phone app itself, over HTTPS with NOVA's own certificate, at this network's address. */
  app_url: string | null;
  /** The phone app at NOVA's .local name: the same on every Wi-Fi, so a phone pairs once. */
  name_url: string | null;
  /** The network the PC is on, as Windows classifies it ("Public" blocks phones). */
  network: { name: string; category: string } | null;
  /** NOVA's certificate authority, as the phone shows it: its name and the start of its fingerprint. */
  certificate: { name: string; fingerprint: string } | null;
  error: string | null;
  devices: PhoneDevice[];
};

async function phoneCall<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await request(path, init);
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.detail ?? `HTTP ${response.status}`);
  return body;
}

export const phoneStatus = () => phoneCall<PhoneAccessStatus>("/api/phone");
export const setPhoneAccess = (enabled: boolean) =>
  phoneCall<PhoneAccessStatus>("/api/phone", { method: "POST", body: JSON.stringify({ enabled }) });
export const newPairingCode = () =>
  phoneCall<{ code: string; expires_in: number; url: string; qr: string | null }>("/api/phone/code", { method: "POST" });
export const removePhone = (id: number) => phoneCall<PhoneAccessStatus>(`/api/phone/devices/${id}`, { method: "DELETE" });

/** Parse a newline-delimited JSON body. A network chunk can end mid-line. */
async function* readLines<T>(body: NonNullable<Response["body"]>): AsyncGenerator<T> {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  let buffered = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffered += value;
    const lines = buffered.split("\n");
    buffered = lines.pop() ?? "";
    for (const line of lines) {
      if (line.trim()) yield JSON.parse(line) as T;
    }
  }
  if (buffered.trim()) yield JSON.parse(buffered) as T;
}

export async function health(): Promise<Health> {
  const response = await request("/api/health");
  if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
  return response.json();
}

/** Start loading the chat model, or the vision model when a screen question is coming. Fire and forget. */
export function warmup(vision = false): void {
  request(`/api/warmup${vision ? "?vision=true" : ""}`, { method: "POST" }).catch(() => {});
}

export async function answerConfirmation(callId: string, approved: boolean): Promise<void> {
  await request(`/api/confirmations/${encodeURIComponent(callId)}`, {
    method: "POST",
    body: JSON.stringify({ approved }),
  });
}

/** Send one message and stream back the agent's events. */
export type TurnOptions = {
  /** Spoken by the user; the reply is read aloud. */
  voice?: boolean;
  /** The user asked NOVA to look at their screen for this message. */
  screen?: boolean;
};

export async function* chat(
  message: string,
  conversationId: string | null,
  signal: AbortSignal,
  { voice = false, screen = false }: TurnOptions = {},
): AsyncGenerator<AgentEvent> {
  const response = await request("/api/chat", {
    method: "POST",
    body: JSON.stringify({ message, conversation_id: conversationId, voice, screen }),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`Backend returned HTTP ${response.status}`);
  }
  yield* readLines<AgentEvent>(response.body);
}

export async function forgetMemory(id: number): Promise<boolean> {
  const response = await request(`/api/memories/${id}`, { method: "DELETE" });
  return response.ok;
}

export async function listMemories(): Promise<Memory[]> {
  const response = await request("/api/memories");
  if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
  return (await response.json()).memories;
}

export type Action = {
  id: number;
  tool: string;
  /** What the user was shown, e.g. "Sort 7 files in Downloads into Documents (3), Images (2)". */
  summary: string;
  outcome: "ran" | "approved" | "declined" | "blocked" | "rejected";
  ok: boolean;
  created_at: string;
};

/** Every action NOVA attempted, newest first. */
export async function listActions(limit = 15): Promise<Action[]> {
  const response = await request(`/api/actions?limit=${limit}`);
  if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
  return (await response.json()).actions;
}

export type Connections = {
  google: { client: boolean; connected: boolean; account: string | null; signing_in: boolean; error: string | null };
  github: { connected: boolean; account: string | null; cli: boolean };
};

async function connectionCall(path: string, init: RequestInit = {}): Promise<Connections> {
  const response = await request(path, init);
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.detail ?? `HTTP ${response.status}`);
  return body;
}

export const connectionStatus = () => connectionCall("/api/connections");
/** The JSON file of the user's own Google "Desktop app" OAuth client. */
export const setGoogleClient = (client: string) =>
  connectionCall("/api/connections/google/client", { method: "POST", body: JSON.stringify({ client }) });
/** Opens Google's sign-in in the user's browser; poll connectionStatus for the outcome. */
export const connectGoogle = async () => {
  const response = await request("/api/connections/google/connect", { method: "POST" });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? `HTTP ${response.status}`);
};
export const connectGitHub = (options: { token?: string; fromCli?: boolean }) =>
  connectionCall("/api/connections/github", {
    method: "POST",
    body: JSON.stringify({ token: options.token ?? null, from_cli: options.fromCli ?? false }),
  });
export const disconnectService = (service: "google" | "github") =>
  connectionCall(`/api/connections/${service}`, { method: "DELETE" });

export async function listReminders(): Promise<{ upcoming: Reminder[]; pending: Reminder[] }> {
  const response = await request("/api/reminders");
  if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
  return response.json();
}

/** Cancel a reminder or scheduled task. The user's click is the confirmation. */
export async function cancelReminder(id: number): Promise<boolean> {
  const response = await request(`/api/reminders/${id}/cancel`, { method: "POST" });
  return response.ok;
}

export async function dismissReminder(id: number): Promise<void> {
  await request(`/api/reminders/${id}/dismiss`, { method: "POST" });
}

export async function snoozeReminder(id: number, minutes: number): Promise<void> {
  await request(`/api/reminders/${id}/snooze`, { method: "POST", body: JSON.stringify({ minutes }) });
}

export async function voiceStatus(): Promise<VoiceStatus> {
  const response = await request("/api/voice");
  if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
  return response.json();
}

/** Turn "Hey Nova" listening on or off. Throws with the backend's reason if voice cannot start. */
export async function setWakeWord(enabled: boolean): Promise<VoiceStatus> {
  const response = await request("/api/voice/settings", { method: "POST", body: JSON.stringify({ wake_word: enabled }) });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? `HTTP ${response.status}`);
  return response.json();
}

/** The mic button: listen for one command. */
export async function listenOnce(): Promise<void> {
  const response = await request("/api/voice/listen", { method: "POST" });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? `HTTP ${response.status}`);
}

export function speak(text: string): void {
  request("/api/voice/speak", { method: "POST", body: JSON.stringify({ text }) }).catch(() => {});
}

/** Stop talking, and stop waiting for a command. */
export function stopVoice(): void {
  request("/api/voice/stop", { method: "POST" }).catch(() => {});
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Receive server events for as long as the page lives, reconnecting with
 * backoff whenever the connection drops (the backend restarting, say).
 * Returns a function that stops listening.
 */
export function listenForEvents(onEvent: (event: ServerEvent) => void): () => void {
  let stopped = false;
  let current: AbortController | null = null;

  (async () => {
    let delay = 1000;
    while (!stopped) {
      current = new AbortController();
      try {
        const response = await request("/api/events", { signal: current.signal });
        if (response.ok && response.body) {
          delay = 1000;
          for await (const event of readLines<ServerEvent>(response.body)) onEvent(event);
        }
      } catch {
        // Dropped or refused; retry below.
      }
      if (stopped) return;
      await sleep(delay);
      delay = Math.min(delay * 2, 30_000);
    }
  })();

  return () => {
    stopped = true;
    current?.abort();
  };
}
