import { type FormEvent, useEffect, useState } from "react";

import { Row, Section, when } from "@/components/known-panel";
import { type PocketView, pocket, watchPocket } from "@/lib/pocket";

const FIELD =
  "selectable w-full rounded-xl border border-line bg-surface px-3 py-2.5 text-[15px] outline-none focus:border-accent";
const CHIP = "rounded-full border border-line px-3 py-1.5 text-[12.500px] text-text-muted aria-pressed:border-accent aria-pressed:text-text";

/** A time picker's value (local time, "2026-10-01T18:30") for a date. */
function pickerValue(when: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}T${pad(when.getHours())}:${pad(when.getMinutes())}`;
}

function inMinutes(minutes: number): Date {
  return new Date(Date.now() + minutes * 60_000);
}

function tomorrowAt(hour: number): Date {
  const due = new Date();
  due.setDate(due.getDate() + 1);
  due.setHours(hour, 0, 0, 0);
  return due;
}

const QUICK: { label: string; due: () => Date }[] = [
  { label: "In 30 min", due: () => inMinutes(30) },
  { label: "In 1 hour", due: () => inMinutes(60) },
  { label: "Tonight 8 PM", due: () => {
    const due = new Date();
    due.setHours(20, 0, 0, 0);
    return due;
  } },
  { label: "Tomorrow 9 AM", due: () => tomorrowAt(9) },
];

/**
 * NOVA while the PC is out of reach: the phone's copy of the memory and reminders, and simple
 * ways to add to them. Every change waits in the phone's outbox and reaches the PC when it is
 * back. Times are picked, not typed in words, so nothing here has to understand language.
 */
export function PocketPanel() {
  const [view, setView] = useState<PocketView | null>(null);
  const [note, setNote] = useState("");
  const [reminder, setReminder] = useState("");
  const [due, setDue] = useState(() => pickerValue(inMinutes(60)));

  useEffect(() => watchPocket(setView), []);

  if (!view) return <p className="px-1 text-[13px] text-text-muted">Loading…</p>;

  const remember = (event: FormEvent) => {
    event.preventDefault();
    if (note.trim()) void pocket.remember(note.trim());
    setNote("");
  };
  const remind = (event: FormEvent) => {
    event.preventDefault();
    const at = new Date(due);
    if (!reminder.trim() || Number.isNaN(at.getTime())) return;
    void pocket.remind(reminder.trim(), at);
    setReminder("");
  };

  return (
    <div className="flex flex-col gap-4">
      <p className="rounded-xl bg-accent-soft px-3 py-2.5 text-[13px] leading-snug">
        {view.waiting > 0
          ? `${view.waiting} ${view.waiting === 1 ? "change waits" : "changes wait"} on this phone and will reach your PC when it is back.`
          : "Your PC is out of reach. You can still read and add to what NOVA holds; it reaches your PC when it is back."}
        {view.lastSync && (
          <span className="block text-[11.500px] text-text-muted">
            Copy from {new Date(view.lastSync).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}
          </span>
        )}
      </p>

      <Section title="Remind me">
        <form onSubmit={remind} className="flex flex-col gap-2 px-1">
          <input
            value={reminder}
            onChange={(event) => setReminder(event.target.value)}
            placeholder="What about? e.g. Call home"
            aria-label="Reminder"
            className={FIELD}
          />
          <div className="flex flex-wrap gap-1.5">
            {QUICK.map((quick) => (
              <button key={quick.label} type="button" className={CHIP} onClick={() => setDue(pickerValue(quick.due()))}>
                {quick.label}
              </button>
            ))}
          </div>
          <div className="flex gap-2">
            <input
              type="datetime-local"
              value={due}
              onChange={(event) => setDue(event.target.value)}
              aria-label="When"
              className={FIELD}
            />
            <button
              type="submit"
              disabled={!reminder.trim()}
              className="flex-none rounded-xl bg-accent px-4 text-[14px] font-semibold text-surface disabled:opacity-40"
            >
              Add
            </button>
          </div>
        </form>
      </Section>

      <Section title="Reminders" count={view.reminders.length}>
        {view.reminders.length === 0 && <p className="px-1 text-[12.500px] text-text-muted">Nothing coming up.</p>}
        {view.reminders.map((item) => (
          <Row
            key={item.madeBy ?? item.id}
            who="chime"
            label={item.text}
            detail={`${when(item)}${item.madeBy ? " · made on this phone" : ""}`}
            action="Cancel"
            onAction={() => void pocket.cancel(item)}
          />
        ))}
      </Section>

      <Section title="Remember">
        <form onSubmit={remember} className="flex gap-2 px-1">
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="e.g. My exam is next Friday"
            aria-label="Something to remember"
            className={FIELD}
          />
          <button
            type="submit"
            disabled={!note.trim()}
            className="flex-none rounded-xl bg-accent px-4 text-[14px] font-semibold text-surface disabled:opacity-40"
          >
            Save
          </button>
        </form>
      </Section>

      <Section title="What Nova remembers" count={view.memories.length}>
        {view.memories.length === 0 && (
          <p className="px-1 text-[12.500px] text-text-muted">
            Nothing copied yet. The copy is made whenever this phone reaches your PC.
          </p>
        )}
        {view.memories.map((memory, index) => (
          <Row
            key={memory.id > 0 ? memory.id : `new-${index}`}
            who="nova"
            label={memory.content}
            detail={memory.id > 0 ? undefined : "Saved on this phone"}
            action={memory.id > 0 ? "Forget" : undefined}
            onAction={memory.id > 0 ? () => void pocket.forget(memory.id) : undefined}
          />
        ))}
      </Section>
    </div>
  );
}
