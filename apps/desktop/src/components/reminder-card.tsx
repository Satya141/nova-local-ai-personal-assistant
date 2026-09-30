import { useEffect, useState } from "react";

import { Character } from "@/components/character";
import type { Reminder } from "@/lib/backend";

export const SNOOZE_MINUTES = 10;
/** The bell rings this long, then settles, so it does not nag for ever. */
const RING_FOR_MS = 5000;
/** A reminder that fires this much after it was due is shown as missed. */
const MISSED_AFTER_MS = 2 * 60 * 1000;

function clock(moment: Date): string {
  return moment.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function dueLabel(reminder: Reminder, now: Date): string {
  const due = new Date(reminder.fired_at ?? reminder.due_at);
  const sameDay = due.toDateString() === now.toDateString();
  const when = sameDay
    ? clock(due)
    : `${due.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}, ${clock(due)}`;
  if (now.getTime() - due.getTime() > MISSED_AFTER_MS) return `Missed · was due ${when}`;
  return reminder.repeat === "none" ? `Reminder · ${when}` : `Reminder · ${when} · repeats ${reminder.repeat}`;
}

export function ReminderCard({
  reminder,
  onDone,
  onSnooze,
}: {
  reminder: Reminder;
  onDone: (id: number) => void;
  onSnooze: (id: number) => void;
}) {
  const [ringing, setRinging] = useState(true);
  useEffect(() => {
    const timer = setTimeout(() => setRinging(false), RING_FOR_MS);
    return () => clearTimeout(timer);
  }, []);

  return (
    <article className="toast-in flex gap-3 px-4 py-3.5" aria-live="assertive">
      <Character kind="chime" state={ringing ? "ringing" : "idle"} size={46} />
      <div className="flex min-w-0 flex-1 flex-col">
        <p className="text-[11px] font-medium uppercase tracking-wide text-text-muted">
          {dueLabel(reminder, new Date())}
        </p>
        <p className="selectable mt-0.5 text-[15px] font-medium leading-snug">{reminder.text}</p>
        <div className="mt-2.5 flex gap-2">
          <button
            type="button"
            onClick={() => onSnooze(reminder.id)}
            className="rounded-lg border border-line bg-surface-raised px-3 py-1 text-[12px] font-medium hover:border-accent focus-visible:outline-2 focus-visible:outline-accent"
          >
            Snooze {SNOOZE_MINUTES} min
          </button>
          <button
            type="button"
            onClick={() => onDone(reminder.id)}
            className="rounded-lg bg-accent px-3 py-1 text-[12px] font-semibold text-surface hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Done
          </button>
        </div>
      </div>
    </article>
  );
}
