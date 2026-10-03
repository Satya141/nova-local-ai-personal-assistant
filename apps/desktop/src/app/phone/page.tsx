"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { Character, type CharacterKind, type CharacterState } from "@/components/character";
import { MicIcon, ReturnIcon, StopIcon } from "@/components/icons";
import { KnownPanel } from "@/components/known-panel";
import { PhoneAlertsCard } from "@/components/phone-alerts-card";
import { PocketModelCard } from "@/components/pocket-model-card";
import { PocketPanel } from "@/components/pocket-panel";
import { ReminderCard, SNOOZE_MINUTES } from "@/components/reminder-card";
import { TimelinePanel } from "@/components/timeline-panel";
import { Transcript } from "@/components/transcript";
import { NotPaired, dismissReminder, health, listenForEvents, pairPhone, snoozeReminder, transcribeClip } from "@/lib/backend";
import { type PocketReminder, type PocketView, pocket, syncNow, watchPocket, wipePocket } from "@/lib/pocket";
import { chime, primeChime } from "@/lib/chime";
import { type Recording, micSupported, record } from "@/lib/phone-mic";
import { refreshPush } from "@/lib/push";
import { type PocketMessage, pocketTurn } from "@/lib/pocket-agent";
import { pocketModelReady, pocketSupport, rememberedInfo, unloadPocketModel } from "@/lib/pocket-model";
import { useAgent } from "@/lib/use-agent";

/** Cards on screen: from the PC, or rung by the phone itself while the PC is away. */
type Card = PocketReminder & { offline?: boolean };
const cardKey = (card: Card) => (card.madeBy ? `m-${card.madeBy}` : `r-${card.id}`);

type Link = { kind: "checking" } | { kind: "unpaired" } | { kind: "offline"; message: string } | { kind: "ready"; model: string; hearing: boolean };

const SUGGESTIONS: { who: CharacterKind; text: string }[] = [
  { who: "chime", text: "What's on my calendar today?" },
  { who: "nova", text: "Do I have any unread email?" },
  { who: "wren", text: "What's the weather in Hyderabad today?" },
  { who: "chime", text: "Remind me in 30 minutes to call home" },
  { who: "nova", text: "What do you remember about me?" },
];

function Pairing({ onPaired }: { onPaired: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("My phone");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const pair = useCallback(
    async (withCode: string) => {
      setBusy(true);
      setProblem(null);
      try {
        await pairPhone(withCode, name);
        onPaired();
      } catch (error) {
        setProblem(error instanceof Error ? error.message : String(error));
      } finally {
        setBusy(false);
      }
    },
    [name, onPaired],
  );
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void pair(code);
  };

  // Scanning the QR code on the PC brings the code along (#code=123456): pair straight away.
  const scanned = useRef(false);
  useEffect(() => {
    const fromQr = /^#code=(\d{6})$/.exec(window.location.hash)?.[1];
    if (!fromQr || scanned.current) return;
    scanned.current = true;
    history.replaceState(null, "", window.location.pathname);
    setCode(fromQr);
    void pair(fromQr);
  }, [pair]);

  return (
    <form onSubmit={submit} className="mx-auto flex w-full max-w-sm flex-1 flex-col items-center justify-center gap-5 px-6">
      <Character kind="nova" state={busy ? "thinking" : "idle"} size={88} />
      <div className="text-center">
        <h1 className="text-[22px] font-semibold">Pair with NOVA</h1>
        <p className="mt-1.5 text-[14px] leading-relaxed text-text-muted">
          On your PC, open NOVA, press <kbd className="keycap-inline">Ctrl M</kbd> and choose <b>Pair a phone</b>. Scan
          its QR code with your camera, or type the six-digit code here.
        </p>
      </div>
      <input
        value={code}
        onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
        inputMode="numeric"
        autoComplete="one-time-code"
        placeholder="000000"
        aria-label="Pairing code"
        className="selectable w-full rounded-xl border border-line bg-surface-raised py-3 text-center text-[28px] tracking-[0.4em] outline-none focus:border-accent"
      />
      <input
        value={name}
        onChange={(event) => setName(event.target.value)}
        aria-label="This phone's name"
        className="selectable w-full rounded-xl border border-line bg-surface px-3 py-2.5 text-[15px] outline-none focus:border-accent"
      />
      <button
        type="submit"
        disabled={code.length !== 6 || busy}
        className="w-full rounded-xl bg-accent py-3 text-[15px] font-semibold text-surface disabled:opacity-40"
      >
        Pair this phone
      </button>
      {problem && <p className="text-center text-[13px] text-danger">{problem}</p>}
      <p className="text-center text-[12px] leading-relaxed text-text-muted">
        Works while your phone and PC are on the same Wi-Fi.
      </p>
    </form>
  );
}

/** The conversation with Nova on this phone, while the PC is away. */
function PocketTranscript({ messages, status }: { messages: PocketMessage[]; status: string | null }) {
  return (
    <div className="flex flex-col gap-3">
      {messages.map((message, index) =>
        message.role === "user" ? (
          <p key={index} data-pocket="user" className="selectable max-w-[85%] self-end rounded-2xl bg-accent-soft px-3.5 py-2 text-[15px]">
            {message.text}
          </p>
        ) : (
          <div key={index} data-pocket="nova" className="flex items-start gap-2.5">
            <Character kind="nova" state="idle" size={26} />
            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
              <p data-pocket="reply" className="selectable text-[15px] leading-relaxed">{message.text}</p>
              {message.actions?.map((action) => (
                <p key={action} data-pocket="action" className="rounded-lg border border-line bg-surface-raised px-2.5 py-1.5 text-[12.500px] text-text-muted">
                  {action}
                </p>
              ))}
            </div>
          </div>
        ),
      )}
      {status && <p className="px-1 text-[13px] text-text-muted">{status}</p>}
    </div>
  );
}

/** NOVA on the phone: a thin client to the agent on the PC, over the home network. */
export default function PhoneApp() {
  const [link, setLink] = useState<Link>({ kind: "checking" });
  const agent = useAgent();
  const [input, setInput] = useState("");
  const [reminders, setReminders] = useState<Card[]>([]);
  const [side, setSide] = useState<"known" | "timeline" | null>(null);
  const showKnown = side !== null;
  const scroller = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLInputElement>(null);
  // Pocket mode: Nova on this phone, while the PC is away (src/lib/pocket-agent.ts).
  const [pocketReady, setPocketReady] = useState(false);
  const [pocketChat, setPocketChat] = useState<PocketMessage[]>([]);
  const [pocketStatus, setPocketStatus] = useState<string | null>(null);
  const pocketView = useRef<PocketView | null>(null);

  const connect = useCallback((quiet = false) => {
    if (!quiet) setLink({ kind: "checking" });
    health()
      .then((report) =>
        setLink(
          report.model_ready
            ? { kind: "ready", model: report.model, hearing: !!report.hearing_ready }
            : { kind: "offline", message: report.detail ?? "NOVA's model is not ready on the PC." },
        ),
      )
      .catch((error) =>
        setLink(
          error instanceof NotPaired
            ? { kind: "unpaired" }
            : {
                kind: "offline",
                message:
                  "Can't find your PC. It needs to be on with NOVA running, and this phone on the same Wi-Fi or on Tailscale. This page reconnects by itself.",
              },
        ),
      );
  }, []);
  useEffect(() => connect(), [connect]);

  // Walk into a place where phone and PC share a Wi-Fi and NOVA comes back by itself: retry
  // while it is unreachable, when the phone gets a connection, and when the app is opened again.
  const offline = link.kind === "offline";
  useEffect(() => {
    const retry = () => {
      if (document.visibilityState === "visible") connect(true);
    };
    window.addEventListener("online", retry);
    document.addEventListener("visibilitychange", retry);
    // Unreachable: try again every 5 s. Connected: check every 15 s that the PC is still there,
    // so the phone switches to pocket mode by itself when the PC goes away mid-use.
    const timer = setInterval(offline ? retry : () => connect(true), offline ? 5000 : 15000);
    return () => {
      window.removeEventListener("online", retry);
      document.removeEventListener("visibilitychange", retry);
      clearInterval(timer);
    };
  }, [offline, connect]);

  // Reminders that fire while the app is open, and memories NOVA notes. Each one also refreshes
  // the phone's copy, as does reaching the PC at all: that is when offline changes go across.
  const { noteMemory } = agent;
  const ready = link.kind === "ready";
  const rungOnline = useRef(new Set<string>());
  useEffect(() => {
    if (!ready) return;
    void syncNow();
    refreshPush().catch(() => {}); // where to ring this phone while it is locked
    const timer = setInterval(() => void syncNow(), 120_000);
    const stop = listenForEvents((event) => {
      if (event.type === "reminder" && event.reminder.pending_ack) {
        // Ring once per firing: reconnecting replays the reminders still waiting.
        const key = `${event.reminder.id}@${event.reminder.fired_at}`;
        if (!rungOnline.current.has(key)) {
          rungOnline.current.add(key);
          chime();
        }
        setReminders((shown) => [...shown.filter((r) => r.madeBy || r.id !== event.reminder.id), event.reminder]);
        void syncNow();
      } else if (event.type === "memory_saved") {
        noteMemory(event.conversation_id, event.memory);
        void syncNow();
      }
    });
    return () => {
      clearInterval(timer);
      stop();
    };
  }, [ready, noteMemory]);

  // Browsers let a page make sound only after a touch: get the reminder chime ready on the first one.
  useEffect(() => {
    window.addEventListener("pointerdown", primeChime, { once: true });
    return () => window.removeEventListener("pointerdown", primeChime);
  }, []);

  // Keep the app itself on the phone, so it opens while the PC is away (HTTPS only).
  useEffect(() => {
    if ("serviceWorker" in navigator && window.isSecureContext) {
      navigator.serviceWorker.register("/phone-sw.js", { scope: "/" }).catch(() => {});
    }
  }, []);

  // The PC removed this phone: drop the copy too.
  useEffect(() => {
    if (link.kind === "unpaired") void wipePocket();
  }, [link.kind]);

  // While the PC is away, the phone rings reminders from its copy itself (while the app is open).
  const rung = useRef(new Set<string>());
  useEffect(() => {
    if (!offline) return;
    let current: PocketReminder[] = [];
    const ring = () => {
      const now = Date.now();
      for (const reminder of current) {
        const key = `${cardKey(reminder)}@${reminder.due_at}`;
        if (reminder.status !== "active" || new Date(reminder.due_at).getTime() > now || rung.current.has(key)) continue;
        rung.current.add(key);
        chime();
        setReminders((shown) => [...shown.filter((r) => cardKey(r) !== cardKey(reminder)), { ...reminder, offline: true }]);
      }
    };
    const stop = watchPocket((view) => {
      pocketView.current = view;
      current = view.reminders;
      ring();
    });
    const timer = setInterval(ring, 10_000);
    return () => {
      stop();
      clearInterval(timer);
    };
  }, [offline]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [agent.items]);

  // While the PC is away: is Nova on this phone? When the PC is back, free the phone's graphics memory.
  useEffect(() => {
    if (!offline) {
      void unloadPocketModel();
      setPocketChat([]);
      return;
    }
    let live = true;
    void (async () => {
      const info = rememberedInfo();
      const ok = !!info && (await pocketSupport()).ok && (await pocketModelReady(info));
      if (live) setPocketReady(ok);
    })();
    return () => {
      live = false;
    };
  }, [offline]);

  const pocketSend = async (message: string) => {
    const info = rememberedInfo();
    if (!info || !pocketView.current || pocketStatus) return;
    setInput("");
    setSide(null);
    const history = pocketChat;
    setPocketChat([...history, { role: "user", text: message }]);
    setPocketStatus("Thinking…");
    try {
      const reply = await pocketTurn(info, pocketView.current, history, message, (share) =>
        setPocketStatus(share < 1 ? `Waking Nova on this phone… ${Math.round(share * 100)}%` : "Thinking…"),
      );
      setPocketChat((chat) => [...chat, reply]);
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      setPocketChat((chat) => [...chat, { role: "assistant", text: `Nova could not run on this phone: ${reason}` }]);
    } finally {
      setPocketStatus(null);
    }
  };

  const send = (text: string) => {
    const message = text.trim();
    if (message && offline && pocketReady) {
      void pocketSend(message);
      return;
    }
    if (!message || agent.busy || !ready) return;
    setInput("");
    setSide(null);
    agent.send(message);
  };

  // The mic button: one spoken message, heard by the PC's own Whisper, then sent like typing.
  const [mic, setMic] = useState<"idle" | "recording" | "hearing">("idle");
  const [micNote, setMicNote] = useState<string | null>(null);
  const recording = useRef<Recording | null>(null);
  useEffect(() => () => recording.current?.cancel(), []);
  const canTalk = link.kind === "ready" && link.hearing && micSupported();
  const onMic = async () => {
    if (mic === "recording") return recording.current?.stop();
    if (mic !== "idle") return;
    setMicNote(null);
    try {
      const current = await record();
      recording.current = current;
      setMic("recording");
      const clip = await current.done;
      recording.current = null;
      if (!clip) {
        setMic("idle");
        setMicNote("Didn't hear anything. Tap the mic and speak.");
        return;
      }
      setMic("hearing");
      const text = (await transcribeClip(clip)).trim();
      setMic("idle");
      if (text) send(text);
      else setMicNote("Couldn't make that out. Try again, a little closer.");
    } catch (error) {
      recording.current = null;
      setMic("idle");
      setMicNote(
        error instanceof DOMException && error.name === "NotAllowedError"
          ? "The microphone isn't allowed. Tap the icon left of the address → Permissions → Microphone → Allow."
          : error instanceof Error ? error.message : String(error),
      );
    }
  };

  const removeCard = (card: Card) => setReminders((shown) => shown.filter((r) => cardKey(r) !== cardKey(card)));

  const face: CharacterState =
    link.kind === "checking" ? "asleep"
    : pocketStatus ? "thinking"
    : link.kind === "offline" ? (pocketReady ? "idle" : "asleep")
    : agent.phase !== "idle" ? agent.phase
    : input.trim() ? "listening"
    : agent.outcome === "error" ? "error"
    : "idle";
  const runningTool = agent.items.findLast((item) => item.kind === "tool" && item.state === "running");
  const activity =
    agent.phase === "thinking" ? "Thinking…"
    : agent.phase === "working" ? (runningTool?.kind === "tool" ? runningTool.summary : "Working…")
    : agent.phase === "confirm" ? "Waiting for your OK"
    : agent.phase === "speaking" ? "Replying…"
    : link.kind === "ready" ? `On your PC · ${link.model}`
    : link.kind === "checking" ? "Connecting…"
    : pocketStatus ? pocketStatus
    : link.kind === "offline" ? (pocketReady ? "Your PC is away · Nova on this phone" : "Your PC is away · on this phone")
    : null;

  return (
    <main className="phone-shell flex flex-col">
      {link.kind === "unpaired" ? (
        <Pairing onPaired={connect} />
      ) : (
        <>
          <header className="flex flex-none items-center gap-3 border-b border-line px-4 py-2.5">
            <Character kind="nova" state={face} size={40} />
            <div className="flex min-w-0 flex-1 flex-col">
              <h1 className="text-[17px] font-semibold leading-tight">Nova</h1>
              <p className="truncate text-[12px] text-text-muted" aria-live="polite">
                {activity}
              </p>
            </div>
            {offline && pocketChat.length > 0 && (
              <button
                type="button"
                onClick={() => setSide(side === "known" ? null : "known")}
                className="rounded-full border border-line px-3 py-1.5 text-[12.500px] text-text-muted"
              >
                {side === "known" ? "Chat" : "Memory"}
              </button>
            )}
            {ready && (
              <>
                {side !== null ? (
                  <button
                    type="button"
                    onClick={() => setSide(null)}
                    className="rounded-full border border-line px-3 py-1.5 text-[12.500px] text-text-muted"
                  >
                    Chat
                  </button>
                ) : null}
                {(["known", "timeline"] as const)
                  .filter((which) => which !== side)
                  .map((which) => (
                    <button
                      key={which}
                      type="button"
                      onClick={() => setSide(which)}
                      className="rounded-full border border-line px-3 py-1.5 text-[12.500px] text-text-muted"
                    >
                      {which === "known" ? "Memory" : "Timeline"}
                    </button>
                  ))}
                {agent.items.length > 0 && !showKnown && (
                  <button
                    type="button"
                    onClick={() => agent.reset()}
                    className="rounded-full border border-line px-3 py-1.5 text-[12.500px] text-text-muted"
                  >
                    New
                  </button>
                )}
              </>
            )}
          </header>

          {reminders.length > 0 && (
            <div className="flex-none divide-y divide-line border-b border-line bg-surface-raised">
              {reminders.map((reminder) => (
                <ReminderCard
                  key={cardKey(reminder)}
                  reminder={reminder}
                  onDone={() => {
                    removeCard(reminder);
                    if (reminder.offline) void pocket.dismiss(reminder);
                    else dismissReminder(reminder.id).catch(() => {});
                  }}
                  onSnooze={() => {
                    removeCard(reminder);
                    if (reminder.offline) void pocket.snooze(reminder, SNOOZE_MINUTES);
                    else snoozeReminder(reminder.id, SNOOZE_MINUTES).catch(() => {});
                  }}
                />
              ))}
            </div>
          )}

          <div ref={scroller} className="transcript min-h-0 flex-1 overflow-y-auto px-4 py-3">
            {link.kind === "offline" ? (
              <div className="flex flex-col gap-4 pt-1">
                <div className="flex items-start gap-3 px-1">
                  <p className="flex-1 text-[12.500px] leading-snug text-text-muted">{link.message}</p>
                  <button
                    type="button"
                    onClick={() => connect()}
                    className="flex-none rounded-full border border-line px-3 py-1.5 text-[12.500px] text-text-muted"
                  >
                    Try again
                  </button>
                </div>
                {side !== "known" && pocketChat.length > 0 ? (
                  <PocketTranscript messages={pocketChat} status={pocketStatus} />
                ) : (
                  <>
                    {pocketReady ? (
                      <p className="px-1 text-[13px] text-text-muted">
                        Nova runs on this phone now: ask below, or use the lists. What you add reaches your PC when it is back.
                      </p>
                    ) : (
                      <PocketModelCard online={false} />
                    )}
                    <PocketPanel />
                  </>
                )}
              </div>
            ) : side === "known" ? (
              <KnownPanel phone />
            ) : side === "timeline" ? (
              <TimelinePanel />
            ) : agent.items.length > 0 ? (
              <Transcript items={agent.items} onAnswer={agent.answer} onForget={agent.forget} />
            ) : (
              ready && (
                <div className="flex flex-col gap-2 pt-2">
                  <PhoneAlertsCard onlyWhenOff />
                  <p className="px-1 pb-1 text-[13px] text-text-muted">Ask anything, or try:</p>
                  {SUGGESTIONS.map((suggestion, index) => (
                    <button
                      key={suggestion.text}
                      type="button"
                      onClick={() => send(suggestion.text)}
                      style={{ animationDelay: `${index * 45}ms` }}
                      className="suggestion rise flex items-center gap-2.5 rounded-2xl border border-line bg-surface-raised px-3 py-2.5 text-left text-[14px]"
                    >
                      <Character kind={suggestion.who} state="idle" size={26} />
                      {suggestion.text}
                    </button>
                  ))}
                </div>
              )
            )}
          </div>

          {micNote && <p className="flex-none px-4 pt-2 text-[12.500px] text-text-muted">{micNote}</p>}
          <form
            className="phone-composer flex flex-none items-center gap-2 border-t border-line px-3 pt-2.5"
            onSubmit={(event) => {
              event.preventDefault();
              send(input);
            }}
          >
            <input
              ref={field}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder={
                mic === "recording" ? "Listening… tap ■ when done"
                : mic === "hearing" ? "Hearing you…"
                : ready ? "Ask Nova anything…" : offline && pocketReady ? "Ask Nova on this phone…" : "Not connected"
              }
              disabled={!ready && !(offline && pocketReady)}
              aria-label="Message Nova"
              enterKeyHint="send"
              autoComplete="off"
              className="selectable min-w-0 flex-1 rounded-full border border-line bg-surface-raised px-4 py-2.5 text-[16px] outline-none focus:border-accent"
            />
            {canTalk && (
              <button
                type="button"
                onClick={() => void onMic()}
                disabled={mic === "hearing" || (mic === "idle" && agent.busy)}
                aria-label={mic === "recording" ? "Stop and send" : "Speak to Nova"}
                data-active={mic !== "idle"}
                className="phone-mic flex h-11 w-11 flex-none items-center justify-center rounded-full border border-line text-text-muted disabled:opacity-35"
              >
                {mic === "recording" ? <StopIcon size={15} /> : <MicIcon size={18} />}
              </button>
            )}
            <button
              type="submit"
              disabled={!input.trim() || agent.busy || pocketStatus !== null || (!ready && !(offline && pocketReady))}
              aria-label="Send"
              className="flex h-11 w-11 flex-none items-center justify-center rounded-full bg-accent text-surface disabled:opacity-35"
            >
              <ReturnIcon size={16} />
            </button>
          </form>
        </>
      )}
    </main>
  );
}
