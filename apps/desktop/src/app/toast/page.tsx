"use client";

import { invoke, isTauri } from "@tauri-apps/api/core";
import { useCallback, useEffect, useRef, useState } from "react";

import { ReminderCard, SNOOZE_MINUTES } from "@/components/reminder-card";
import { type Reminder, dismissReminder, listenForEvents, snoozeReminder } from "@/lib/backend";

/**
 * The reminder pop-up. It lives in its own always-on-top window that never
 * takes focus, so a reminder can appear while the user is typing elsewhere.
 */
export default function Toast() {
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const panel = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!isTauri()) return;
    return listenForEvents((event) => {
      if (event.type !== "reminder" || !event.reminder.pending_ack) return;
      // A reconnect replays unacknowledged reminders; keep one card per reminder.
      setReminders((shown) => [...shown.filter((r) => r.id !== event.reminder.id), event.reminder]);
    });
  }, []);

  // The window appears while there is something to show and is sized to the cards.
  const visible = reminders.length > 0;
  useEffect(() => {
    if (!isTauri()) return;
    const element = panel.current;
    if (!visible || !element) {
      invoke("hide_toast").catch(() => {});
      return;
    }
    const place = () => invoke("show_toast", { height: Math.ceil(element.getBoundingClientRect().height) });
    place();
    const observer = new ResizeObserver(place);
    observer.observe(element);
    return () => observer.disconnect();
  }, [visible]);

  const remove = useCallback((id: number) => setReminders((shown) => shown.filter((r) => r.id !== id)), []);
  const done = useCallback(
    (id: number) => {
      remove(id);
      dismissReminder(id).catch(() => {});
    },
    [remove],
  );
  const snooze = useCallback(
    (id: number) => {
      remove(id);
      snoozeReminder(id, SNOOZE_MINUTES).catch(() => {});
    },
    [remove],
  );

  return (
    <main ref={panel} className="flex max-h-[560px] flex-col divide-y divide-line overflow-y-auto">
      {reminders.map((reminder) => (
        <ReminderCard key={reminder.id} reminder={reminder} onDone={done} onSnooze={snooze} />
      ))}
    </main>
  );
}
