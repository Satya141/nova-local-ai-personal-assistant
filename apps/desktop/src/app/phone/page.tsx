"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { Character, type CharacterKind, type CharacterState } from "@/components/character";
import { ReturnIcon } from "@/components/icons";
import { KnownPanel } from "@/components/known-panel";
import { ReminderCard, SNOOZE_MINUTES } from "@/components/reminder-card";
import { Transcript } from "@/components/transcript";
import {
  NotPaired,
  type Reminder,
  dismissReminder,
  health,
  listenForEvents,
  pairPhone,
  snoozeReminder,
} from "@/lib/backend";
import { useAgent } from "@/lib/use-agent";

type Link = { kind: "checking" } | { kind: "unpaired" } | { kind: "offline"; message: string } | { kind: "ready"; model: string };

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

/** NOVA on the phone: a thin client to the agent on the PC, over the home network. */
export default function PhoneApp() {
  const [link, setLink] = useState<Link>({ kind: "checking" });
  const agent = useAgent();
  const [input, setInput] = useState("");
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [showKnown, setShowKnown] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLInputElement>(null);

  const connect = useCallback((quiet = false) => {
    if (!quiet) setLink({ kind: "checking" });
    health()
      .then((report) =>
        setLink(
          report.model_ready
            ? { kind: "ready", model: report.model }
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
    const timer = offline ? setInterval(retry, 5000) : undefined;
    return () => {
      window.removeEventListener("online", retry);
      document.removeEventListener("visibilitychange", retry);
      clearInterval(timer);
    };
  }, [offline, connect]);

  // Reminders that fire while the app is open, and memories NOVA notes.
  const { noteMemory } = agent;
  const ready = link.kind === "ready";
  useEffect(() => {
    if (!ready) return;
    return listenForEvents((event) => {
      if (event.type === "reminder" && event.reminder.pending_ack) {
        setReminders((shown) => [...shown.filter((r) => r.id !== event.reminder.id), event.reminder]);
      } else if (event.type === "memory_saved") {
        noteMemory(event.conversation_id, event.memory);
      }
    });
  }, [ready, noteMemory]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [agent.items]);

  const send = (text: string) => {
    const message = text.trim();
    if (!message || agent.busy || !ready) return;
    setInput("");
    setShowKnown(false);
    agent.send(message);
  };

  const removeCard = (id: number) => setReminders((shown) => shown.filter((r) => r.id !== id));

  const face: CharacterState =
    link.kind === "checking" ? "asleep"
    : link.kind === "offline" ? "error"
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
            {ready && (
              <>
                <button
                  type="button"
                  onClick={() => setShowKnown(!showKnown)}
                  aria-pressed={showKnown}
                  className="rounded-full border border-line px-3 py-1.5 text-[12.500px] text-text-muted"
                >
                  {showKnown ? "Chat" : "Memory"}
                </button>
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
                  key={reminder.id}
                  reminder={reminder}
                  onDone={(id) => {
                    removeCard(id);
                    dismissReminder(id).catch(() => {});
                  }}
                  onSnooze={(id) => {
                    removeCard(id);
                    snoozeReminder(id, SNOOZE_MINUTES).catch(() => {});
                  }}
                />
              ))}
            </div>
          )}

          <div ref={scroller} className="transcript min-h-0 flex-1 overflow-y-auto px-4 py-3">
            {link.kind === "offline" ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
                <p className="max-w-xs text-[14px] text-text-muted">{link.message}</p>
                <button type="button" onClick={() => connect()} className="rounded-xl bg-accent px-5 py-2.5 text-[14px] font-semibold text-surface">
                  Try again
                </button>
              </div>
            ) : showKnown ? (
              <KnownPanel phone />
            ) : agent.items.length > 0 ? (
              <Transcript items={agent.items} onAnswer={agent.answer} onForget={agent.forget} />
            ) : (
              ready && (
                <div className="flex flex-col gap-2 pt-2">
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
              placeholder={ready ? "Ask Nova anything…" : "Not connected"}
              disabled={!ready}
              aria-label="Message Nova"
              enterKeyHint="send"
              autoComplete="off"
              className="selectable min-w-0 flex-1 rounded-full border border-line bg-surface-raised px-4 py-2.5 text-[16px] outline-none focus:border-accent"
            />
            <button
              type="submit"
              disabled={!input.trim() || agent.busy || !ready}
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
