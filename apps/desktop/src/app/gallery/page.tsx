"use client";

import { useEffect, useState } from "react";

import { Character, type CharacterKind, type CharacterState } from "@/components/character";
import { ReminderCard } from "@/components/reminder-card";
import { Transcript } from "@/components/transcript";
import type { Reminder } from "@/lib/backend";
import type { Item } from "@/lib/use-agent";

/**
 * Design reference: every character in every state, every kind of transcript
 * line, and the reminder pop-up. Open http://localhost:3000/gallery while
 * `pnpm dev` runs.
 */

const KINDS: CharacterKind[] = ["nova", "ember", "fern", "plum", "chime"];
const STATES: CharacterState[] = [
  "idle", "listening", "hearing", "thinking", "working", "speaking", "confirm", "done", "error", "asleep", "ringing",
];

const SAMPLE: Item[] = [
  { kind: "user", key: 0, text: "Open Calculator, then find my resume", spoken: true },
  { kind: "tool", key: 1, callId: "a", name: "open_application", summary: "Open Calculator", state: "ok" },
  { kind: "tool", key: 2, callId: "b", name: "search_files", summary: "Search files for 'resume'", state: "running" },
  { kind: "tool", key: 3, callId: "c", name: "open_path", summary: "Open C:\\Users\\you\\Documents\\missing.pdf", state: "failed" },
  { kind: "tool", key: 4, callId: "d", name: "close_application", summary: "Close Notepad. Unsaved work in it could be lost.", state: "awaiting", risk: "medium" },
  { kind: "tool", key: 5, callId: "e", name: "close_application", summary: "Close Paint. Unsaved work in it could be lost.", state: "declined" },
  { kind: "tool", key: 6, callId: "f", name: "create_reminder", summary: "Remind you tomorrow at 10:00 AM: Review the budget", state: "ok" },
  { kind: "assistant", key: 7, text: "Calculator is open. I found **2 files** that look like your resume:\n\n1. `Resume 2026.pdf` in Documents\n2. `resume-final.docx` in Downloads\n\nWhich one should I open?" },
  { kind: "memory", key: 8, memoryId: 1, content: "The user has a Cognizant interview on Friday 9 October 2026.", forgotten: false },
  { kind: "memory", key: 9, memoryId: 2, content: "The user prefers short answers.", forgotten: true },
  { kind: "error", key: 10, text: "Cannot reach Ollama at http://127.0.0.1:11434. Start Ollama and try again." },
];

// Built after mount: times depend on the clock and locale, which differ between the prerender and the webview.
function sampleReminders(): Reminder[] {
  const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
  return [
    { id: 1, text: "Call mom", due_at: minutesAgo(0), repeat: "none", status: "done", pending_ack: true, fired_at: minutesAgo(0) },
    { id: 2, text: "Stand up and stretch", due_at: minutesAgo(-1440), repeat: "weekdays", status: "active", pending_ack: true, fired_at: minutesAgo(0) },
    { id: 3, text: "Submit the assignment", due_at: minutesAgo(95), repeat: "none", status: "done", pending_ack: true, fired_at: minutesAgo(95) },
  ];
}

export default function Gallery() {
  const [reminders, setReminders] = useState<Reminder[]>([]);
  useEffect(() => setReminders(sampleReminders()), []);

  return (
    <main className="h-screen overflow-y-auto p-8">
      <h1 className="text-lg font-semibold">NOVA characters</h1>
      <table className="mt-4 border-separate border-spacing-x-5 border-spacing-y-3 text-[11px] text-text-muted">
        <thead>
          <tr>
            <th />
            {STATES.map((state) => (
              <th key={state} className="font-medium">{state}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {KINDS.map((kind) => (
            <tr key={kind}>
              <th className="pr-2 text-left font-medium capitalize">{kind}</th>
              {STATES.map((state) => (
                <td key={state} className="text-center">
                  <Character kind={kind} state={state} size={72} />
                </td>
              ))}
            </tr>
          ))}
          <tr>
            <th className="pr-2 text-left font-medium">tiny</th>
            {STATES.map((state, index) => (
              <td key={state} className="text-center">
                <Character kind={KINDS[index % KINDS.length]} state={state} size={22} />
              </td>
            ))}
          </tr>
        </tbody>
      </table>

      <div className="mt-8 flex flex-wrap items-start gap-10">
        <section>
          <h2 className="text-lg font-semibold">Transcript</h2>
          <div className="mt-4 w-[688px] rounded-xl border border-line p-4">
            <Transcript items={SAMPLE} onAnswer={() => {}} onForget={() => {}} />
          </div>
        </section>
        <section>
          <h2 className="text-lg font-semibold">Reminder pop-up</h2>
          <div className="mt-4 flex w-[380px] flex-col divide-y divide-line rounded-xl border border-line">
            {reminders.map((reminder) => (
              <ReminderCard key={reminder.id} reminder={reminder} onDone={() => {}} onSnooze={() => {}} />
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
