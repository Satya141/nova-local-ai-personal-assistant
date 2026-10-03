import { type ReactNode, useCallback, useEffect, useState } from "react";

import { Character, type CharacterKind } from "@/components/character";
import { ConnectionsSection } from "@/components/connections";
import { PhoneAccessSection } from "@/components/phone-access";
import { PhoneAlertsCard } from "@/components/phone-alerts-card";
import { PocketModelCard } from "@/components/pocket-model-card";
import { toolOwner } from "@/components/transcript";
import {
  type Action,
  type Memory,
  type Reminder,
  cancelReminder,
  forgetMemory,
  listActions,
  listMemories,
  listReminders,
} from "@/lib/backend";

/** How each attempt ended, in the user's words. A failed run says so rather than "Done". */
function outcomeLabel(action: Action): string {
  switch (action.outcome) {
    case "approved":
      return action.ok ? "You confirmed" : "You confirmed · it failed";
    case "declined":
      return "You cancelled";
    case "blocked":
      return "Blocked for safety";
    case "rejected":
      return "Not possible";
    default:
      return action.ok ? "Done" : "Failed";
  }
}

function ago(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const moment = new Date(iso);
  const clock = moment.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return moment.toDateString() === new Date().toDateString()
    ? clock
    : `${moment.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}, ${clock}`;
}

const REPEAT_LABEL: Record<Reminder["repeat"], string> = {
  none: "",
  daily: "every day",
  weekdays: "every weekday",
  weekly: "every week",
};

export function when(reminder: Reminder): string {
  const due = new Date(reminder.due_at);
  const now = new Date();
  const clock = due.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  const day =
    due.toDateString() === now.toDateString() ? "Today"
    : due.toDateString() === tomorrow.toDateString() ? "Tomorrow"
    : due.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  const repeat = REPEAT_LABEL[reminder.repeat];
  return `${day}, ${clock}${repeat ? ` · ${repeat}` : ""}`;
}

export function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1">
      <h2 className="px-1 text-[11px] font-medium uppercase tracking-wide text-text-muted">
        {title} {count !== undefined && <span className="tabular-nums">· {count}</span>}
      </h2>
      {children}
    </section>
  );
}

export function Row({
  who,
  label,
  detail,
  action,
  onAction,
}: {
  who: CharacterKind;
  label: string;
  detail?: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className="rise flex items-center gap-2.5 rounded-lg px-1 py-1">
      <Character kind={who} state="idle" size={22} />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="selectable truncate text-[13px]">{label}</span>
        {detail && <span className="truncate text-[11.500px] text-text-muted">{detail}</span>}
      </div>
      {action && onAction && (
        <button
          type="button"
          onClick={onAction}
          className="flex-none rounded px-1.5 py-0.5 text-[12px] text-text-muted hover:bg-surface-raised hover:text-text focus-visible:outline-2 focus-visible:outline-accent"
        >
          {action}
        </button>
      )}
    </div>
  );
}

/**
 * What NOVA is holding for the user: upcoming reminders and scheduled tasks, and every memory.
 * Cancel and Forget act at once; the click is the user's own confirmation, as with Forget in a chat.
 */
export function KnownPanel({ phone = false }: { phone?: boolean }) {
  const [reminders, setReminders] = useState<Reminder[] | null>(null);
  const [memories, setMemories] = useState<Memory[] | null>(null);
  const [actions, setActions] = useState<Action[]>([]);
  const [problem, setProblem] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([listReminders(), listMemories(), listActions()])
      .then(([r, m, a]) => {
        setReminders(r.upcoming);
        setActions(a);
        // Newest first: what NOVA learned most recently is what the user is most likely checking.
        setMemories([...m].sort((a, b) => b.updated_at.localeCompare(a.updated_at)));
        setProblem(null);
      })
      .catch((error: Error) => setProblem(`Could not load: ${error.message}`));
  }, []);
  useEffect(load, [load]);

  const cancel = (id: number) => {
    setReminders((list) => list?.filter((r) => r.id !== id) ?? null);
    cancelReminder(id).then((ok) => ok || load()).catch(load);
  };
  const forget = (id: number) => {
    setMemories((list) => list?.filter((m) => m.id !== id) ?? null);
    forgetMemory(id).then((ok) => ok || load()).catch(load);
  };

  if (problem) return <p className="px-1 text-[13px] text-danger">{problem}</p>;
  if (reminders === null || memories === null) return <p className="px-1 text-[13px] text-text-muted">Loading…</p>;

  return (
    <div className="flex flex-col gap-3">
      {phone && <PhoneAlertsCard />}
      {phone && <PocketModelCard online />}
      <Section title="Reminders and tasks" count={reminders.length}>
        {reminders.length === 0 ? (
          <p className="px-1 text-[12.500px] text-text-muted">Nothing scheduled. Try “Every morning at 8, summarise the tech news”.</p>
        ) : (
          reminders.map((r) => (
            <Row
              key={r.id}
              who="chime"
              label={r.task ? `${r.text}: ${r.task}` : r.text}
              detail={`${r.task ? "Task" : "Reminder"} · ${when(r)}`}
              action="Cancel"
              onAction={() => cancel(r.id)}
            />
          ))
        )}
      </Section>
      <Section title="What Nova remembers" count={memories.length}>
        {memories.length === 0 ? (
          <p className="px-1 text-[12.500px] text-text-muted">Nothing yet. Nova notes lasting facts you mention, like your projects and preferences.</p>
        ) : (
          memories.map((m) => (
            <Row
              key={m.id}
              who="nova"
              label={m.content}
              detail={`${m.category} · ${m.source === "explicit" ? "you asked" : "noticed"}`}
              action="Forget"
              onAction={() => forget(m.id)}
            />
          ))
        )}
      </Section>
      {/* Accounts and phone access are managed on the PC only. */}
      {!phone && (
        <>
          <Section title="Phone">
            <PhoneAccessSection />
          </Section>
          <Section title="Connections">
            <ConnectionsSection />
          </Section>
        </>
      )}
      {actions.length > 0 && (
        <Section title="Recent actions" count={actions.length}>
          {actions.map((a) => (
            <Row key={a.id} who={toolOwner(a.tool)} label={a.summary} detail={`${outcomeLabel(a)} · ${ago(a.created_at)}`} />
          ))}
        </Section>
      )}
    </div>
  );
}
