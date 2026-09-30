"use client";

import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { Character, type CharacterKind, type CharacterState } from "@/components/character";
import { ChipIcon, MicIcon, ReturnIcon, StopIcon } from "@/components/icons";
import { Transcript } from "@/components/transcript";
import {
  type VoiceState,
  type VoiceStatus,
  health,
  listenForEvents,
  listenOnce,
  session,
  setWakeWord,
  stopVoice,
  voiceStatus,
  warmup,
} from "@/lib/backend";
import { useAgent } from "@/lib/use-agent";

type Status =
  | { kind: "starting" }
  | { kind: "ready"; model: string }
  | { kind: "problem"; message: string };

const SUGGESTIONS: { who: CharacterKind; text: string }[] = [
  { who: "ember", text: "Open Calculator" },
  { who: "fern", text: "Find my resume" },
  { who: "chime", text: "Remind me in 10 minutes to stretch" },
  { who: "nova", text: "What do you remember about me?" },
];

const BACKEND_START_ATTEMPTS = 40;
/** Opening the launcher after this long idle starts a fresh conversation. Memory carries over. */
const FRESH_CONVERSATION_AFTER_MS = 20 * 60 * 1000;
/** How long a voice note ("Didn't catch that") stays under the input. */
const VOICE_NOTE_MS = 4000;

/** Voice states in which the mic button means "stop" rather than "listen". */
const VOICE_ACTIVE: VoiceState[] = ["listening", "transcribing", "speaking"];

function reportVoice(status: { state: VoiceState; wake_word: boolean }) {
  if (!isTauri()) return;
  invoke("set_voice_indicator", { wakeWord: status.wake_word, listening: status.state !== "off" }).catch(() => {});
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Ask the shell where the backend is, then wait for it to come up. */
async function connect(): Promise<{ status: Status; shortcut: string }> {
  let info;
  try {
    info = await session();
  } catch (error) {
    return { status: { kind: "problem", message: error instanceof Error ? error.message : String(error) }, shortcut: "" };
  }
  if (info.error) return { status: { kind: "problem", message: info.error }, shortcut: info.shortcut };

  for (let attempt = 0; attempt < BACKEND_START_ATTEMPTS; attempt++) {
    try {
      const report = await health();
      const status: Status = report.model_ready
        ? { kind: "ready", model: report.model }
        : { kind: "problem", message: report.detail ?? "The model is not ready." };
      return { status, shortcut: info.shortcut };
    } catch {
      await sleep(500);
    }
  }
  return {
    status: { kind: "problem", message: "NOVA's backend did not start. Check backend.log in NOVA's log folder." },
    shortcut: info.shortcut,
  };
}

export default function Launcher() {
  const agent = useAgent();
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "starting" });
  const [shortcut, setShortcut] = useState("");
  const [celebrating, setCelebrating] = useState(false);
  const [voice, setVoice] = useState<VoiceStatus | null>(null);
  const [voiceNote, setVoiceNote] = useState<string | null>(null);

  const panel = useRef<HTMLElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const ready = status.kind === "ready";

  const refresh = useCallback(async () => {
    const result = await connect();
    setStatus(result.status);
    setShortcut(result.shortcut);
  }, []);

  const startOver = useCallback(() => {
    agent.reset();
    setInput("");
    setCelebrating(false);
    field.current?.focus();
  }, [agent]);

  // What the launcher-shown handler needs to know, without re-subscribing on every render.
  const latest = useRef({ ready, busy: agent.busy, hasItems: false, startOver, lastActivity: Date.now() });
  latest.current = { ...latest.current, ready, busy: agent.busy, hasItems: agent.items.length > 0, startOver };
  useEffect(() => {
    latest.current.lastActivity = Date.now();
  }, [agent.items]);

  // Connect on start, and whenever the launcher is opened while something is wrong.
  useEffect(() => {
    refresh();
    if (!isTauri()) return;
    const stop = listen("launcher-shown", () => {
      const now = latest.current;
      if (now.hasItems && !now.busy && Date.now() - now.lastActivity > FRESH_CONVERSATION_AFTER_MS) {
        now.startOver();
      }
      field.current?.focus();
      if (now.ready) warmup();
      else refresh();
    });
    return () => {
      stop.then((unlisten) => unlisten());
    };
  }, [refresh]);

  // Voice: learn what is possible once the backend is up, and show it in the tray.
  useEffect(() => {
    if (!ready) return;
    voiceStatus()
      .then((status) => {
        setVoice(status);
        reportVoice(status);
      })
      .catch(() => {});
  }, [ready]);

  useEffect(() => {
    if (!voiceNote) return;
    const timer = setTimeout(() => setVoiceNote(null), VOICE_NOTE_MS);
    return () => clearTimeout(timer);
  }, [voiceNote]);

  // A spoken command runs as an ordinary turn, so confirmations and tool rows work as usual.
  const voiceTurn = useRef<(text: string) => void>(() => {});
  voiceTurn.current = (text: string) => {
    invoke("summon_launcher").catch(() => {});
    if (agent.busy) {
      setVoiceNote("Still working on the last request.");
      return;
    }
    setCelebrating(false);
    agent.send(text, { voice: true });
  };

  // Server events: memories NOVA saved on its own, and everything voice.
  const { noteMemory } = agent;
  useEffect(() => {
    if (!isTauri()) return;
    return listenForEvents((event) => {
      if (event.type === "memory_saved") {
        noteMemory(event.conversation_id, event.memory);
      } else if (event.type === "voice_state") {
        setVoice((current) => (current ? { ...current, state: event.state, wake_word: event.wake_word } : current));
        reportVoice(event);
      } else if (event.type === "voice_command") {
        if (event.text) voiceTurn.current(event.text);
        else setVoiceNote("I didn't catch that.");
      } else if (event.type === "voice_error") {
        setVoiceNote(event.message);
      }
    });
  }, [noteMemory]);

  // The tray's "Listen for Hey Nova" item.
  const wakeWordOn = voice?.wake_word ?? false;
  const toggleWakeWord = useRef<() => void>(() => {});
  toggleWakeWord.current = () => {
    setWakeWord(!wakeWordOn)
      .then((status) => {
        setVoice(status);
        reportVoice(status);
        setVoiceNote(status.wake_word ? "Listening for “Hey Nova”." : "Stopped listening for “Hey Nova”.");
      })
      .catch((error: Error) => {
        invoke("summon_launcher").catch(() => {});
        setVoiceNote(`Voice is not available: ${error.message}`);
        if (voice) reportVoice(voice);
      });
  };
  useEffect(() => {
    if (!isTauri()) return;
    const stop = listen("toggle-wake-word", () => toggleWakeWord.current());
    return () => {
      stop.then((unlisten) => unlisten());
    };
  }, []);

  const voiceState = voice?.state ?? "off";
  const voiceActive = VOICE_ACTIVE.includes(voiceState);
  const onMic = () => {
    if (voiceActive) {
      stopVoice();
      return;
    }
    setVoiceNote(null);
    listenOnce().catch((error: Error) => setVoiceNote(`Voice is not available: ${error.message}`));
  };

  // The window is exactly as tall as the panel.
  useEffect(() => {
    const element = panel.current;
    if (!element || !isTauri()) return;
    const observer = new ResizeObserver(() => {
      invoke("resize_launcher", { height: Math.ceil(element.getBoundingClientRect().height) });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Keep the newest line in view while a reply streams in.
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [agent.items]);

  // A short celebration when a turn ends well.
  const wasBusy = useRef(false);
  useEffect(() => {
    const finished = wasBusy.current && !agent.busy;
    wasBusy.current = agent.busy;
    if (!finished || agent.outcome !== "ok") return;
    setCelebrating(true);
    const timer = setTimeout(() => setCelebrating(false), 1800);
    return () => clearTimeout(timer);
  }, [agent.busy, agent.outcome]);

  const submit = useCallback(
    (text: string) => {
      const message = text.trim();
      if (!message || agent.busy || !ready) return;
      setInput("");
      setCelebrating(false);
      // Typing takes over from talking.
      if (voiceActive) stopVoice();
      agent.send(message);
    },
    [agent, ready, voiceActive],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // Esc interrupts NOVA mid-sentence as well as hiding the launcher.
        stopVoice();
        invoke("hide_launcher").catch(() => {});
      } else if (event.ctrlKey && event.key.toLowerCase() === "n") {
        event.preventDefault();
        startOver();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [startOver]);

  const answer = (callId: string, approved: boolean) => {
    agent.answer(callId, approved);
    field.current?.focus();
  };

  const face: CharacterState =
    status.kind === "starting" ? "asleep"
    : status.kind === "problem" ? "error"
    : voiceState === "listening" ? "hearing"
    : voiceState === "transcribing" ? "thinking"
    : agent.phase !== "idle" ? agent.phase
    : voiceState === "speaking" ? "speaking"
    : input.trim() ? "listening"
    : agent.outcome === "error" ? "error"
    : celebrating ? "done"
    : "idle";

  const runningTool = agent.items.findLast((item) => item.kind === "tool" && item.state === "running");
  const activity =
    status.kind === "starting" ? "Waking up"
    : voiceState === "listening" ? "Listening"
    : voiceState === "transcribing" ? "Catching that"
    : agent.phase === "thinking" ? "Thinking"
    : agent.phase === "working" ? (runningTool?.kind === "tool" ? runningTool.summary : "Working")
    : agent.phase === "confirm" ? "Waiting for your OK"
    : agent.phase === "speaking" ? "Replying"
    : voiceState === "speaking" ? "Speaking · Esc to stop"
    : null;

  return (
    <main ref={panel} className="flex max-h-[600px] flex-col">
      <form
        className="flex h-16 flex-none items-center gap-3 pl-3.5 pr-4"
        onSubmit={(event: FormEvent) => {
          event.preventDefault();
          submit(input);
        }}
      >
        <Character kind="nova" state={face} size={42} />
        <input
          ref={field}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Ask Nova anything..."
          aria-label="Message Nova"
          autoFocus
          spellCheck={false}
          autoComplete="off"
          className="selectable h-full min-w-0 flex-1 bg-transparent text-[17px] outline-none placeholder:text-text-muted"
        />
        {ready && voice?.available && (
          <button
            type="button"
            onClick={onMic}
            data-active={voiceActive}
            aria-label={voiceActive ? "Stop listening and speaking" : "Speak to Nova"}
            title={voiceActive ? "Stop" : "Speak to Nova"}
            className="mic-button"
          >
            {voiceActive ? <StopIcon size={14} /> : <MicIcon size={15} />}
          </button>
        )}
        {input.trim() && !agent.busy && ready ? (
          <kbd className="keycap" aria-hidden="true">
            <ReturnIcon size={12} />
          </kbd>
        ) : (
          <kbd className="keycap" aria-hidden="true">Esc</kbd>
        )}
      </form>

      {status.kind === "problem" && (
        <p role="alert" className="selectable border-t border-line px-4 py-2.5 text-[13px] text-danger">
          {status.message}
        </p>
      )}

      {voiceNote && (
        <p role="status" className="rise selectable border-t border-line px-4 py-2 text-[12.500px] text-text-muted">
          {voiceNote}
        </p>
      )}

      {agent.items.length > 0 ? (
        <div ref={scroller} className="transcript min-h-0 flex-1 overflow-y-auto border-t border-line px-4 py-3">
          <Transcript items={agent.items} onAnswer={answer} onForget={agent.forget} />
        </div>
      ) : (
        ready && (
          <div className="flex flex-none flex-wrap gap-2 border-t border-line px-4 py-2.5">
            {SUGGESTIONS.map((suggestion, index) => (
              <button
                key={suggestion.text}
                type="button"
                onClick={() => submit(suggestion.text)}
                style={{ animationDelay: `${index * 45}ms` }}
                className="suggestion rise flex items-center gap-1.5 rounded-full border border-line bg-surface-raised py-1 pl-1.5 pr-3 text-[12.500px] hover:border-accent focus-visible:outline-2 focus-visible:outline-accent"
              >
                <Character kind={suggestion.who} state="idle" size={20} />
                {suggestion.text}
              </button>
            ))}
          </div>
        )
      )}

      <footer className="flex h-8 flex-none items-center justify-between gap-4 border-t border-line px-4 text-[11px] text-text-muted">
        <span className="flex min-w-0 items-center gap-1.5" aria-live="polite">
          {activity ? (
            <>
              <span className="truncate">{activity}</span>
              <span className="ellipsis" aria-hidden="true" />
            </>
          ) : ready ? (
            <>
              <ChipIcon size={12} />
              <span className="truncate">{status.model} · runs on this device</span>
            </>
          ) : null}
        </span>
        <span className="flex flex-none items-center gap-3">
          {/* The microphone is open: say so, always. */}
          {voiceState !== "off" && voice?.wake_word && (
            <span className="flex items-center gap-1.5" title="The microphone is on, listening for your wake phrase">
              <span className="live-dot" aria-hidden="true" />
              Say &ldquo;Hey Nova&rdquo;
            </span>
          )}
          {agent.items.length > 0 && (
            <span>
              <kbd className="keycap-inline">Ctrl N</kbd> New chat
            </span>
          )}
          {shortcut && (
            <span>
              <kbd className="keycap-inline">{shortcut}</kbd> Open
            </span>
          )}
        </span>
      </footer>
    </main>
  );
}
