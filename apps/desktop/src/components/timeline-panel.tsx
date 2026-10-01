import { useCallback, useEffect, useState } from "react";

import { Character, type CharacterKind } from "@/components/character";
import { toolOwner } from "@/components/transcript";
import { type TimelineDay, type TimelineEntry, summariseDay, timelineDays } from "@/lib/backend";

const PAGE = 7;

function dayLabel(iso: string): string {
  const day = new Date(`${iso}T12:00:00`);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (day.toDateString() === today.toDateString()) return "Today";
  if (day.toDateString() === yesterday.toDateString()) return "Yesterday";
  return day.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
}

function before(iso: string): string {
  const day = new Date(`${iso}T12:00:00`);
  day.setDate(day.getDate() - 1);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
}

function who(entry: TimelineEntry): CharacterKind {
  if (entry.kind === "did" && entry.tool) return toolOwner(entry.tool);
  return entry.kind === "reminded" ? "chime" : "nova";
}

const VERB: Record<TimelineEntry["kind"], string> = {
  asked: "You asked",
  did: "Nova did",
  remembered: "Remembered",
  reminded: "Reminder",
};

function ending(entry: TimelineEntry): string | null {
  if (entry.kind !== "did") return null;
  if (entry.outcome === "declined") return "You cancelled";
  if (entry.outcome === "blocked") return "Blocked for safety";
  if (entry.outcome === "rejected") return "Not possible";
  return entry.ok === false ? "Did not work" : null;
}

function Entry({ entry }: { entry: TimelineEntry }) {
  const time = new Date(entry.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const note = ending(entry);
  return (
    <li className="flex items-start gap-2.5 py-1 pl-1">
      <span className="w-[54px] flex-none pt-0.5 text-right text-[11px] tabular-nums text-text-muted">{time}</span>
      {entry.kind === "asked" ? (
        <span className="mt-0.5 grid size-[22px] flex-none place-items-center rounded-full bg-accent-soft text-[10px] font-semibold text-accent">
          You
        </span>
      ) : (
        <Character kind={who(entry)} state="idle" size={22} />
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="text-[10.500px] font-medium uppercase tracking-wide text-text-muted">{VERB[entry.kind]}</span>
        <span className={`selectable text-[13px] leading-snug ${entry.kind === "asked" ? "italic" : ""}`}>
          {entry.kind === "asked" ? `“${entry.text}”` : entry.text}
        </span>
        {note && <span className="text-[11.500px] text-danger">{note}</span>}
      </div>
    </li>
  );
}

function Day({ day }: { day: TimelineDay }) {
  const [summary, setSummary] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const asked = day.entries.filter((e) => e.kind === "asked").length;
  const done = day.entries.filter((e) => e.kind === "did").length;

  return (
    <section className="flex flex-col gap-1">
      <div className="flex items-baseline gap-2 px-1">
        <h2 className="text-[13px] font-semibold">{dayLabel(day.day)}</h2>
        <span className="text-[11.500px] text-text-muted">
          {day.entries.length === 0
            ? "nothing yet"
            : `${asked} ${asked === 1 ? "request" : "requests"} · ${done} ${done === 1 ? "action" : "actions"}`}
        </span>
        {day.entries.length > 0 && !summary && (
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              summariseDay(day.day)
                .then(setSummary)
                .catch((error: Error) => setSummary(`Could not sum up this day: ${error.message}`))
                .finally(() => setBusy(false));
            }}
            className="ml-auto rounded px-1.5 py-0.5 text-[12px] text-text-muted hover:bg-surface-raised hover:text-text disabled:opacity-60"
          >
            {busy ? "Summing up…" : "Sum up the day"}
          </button>
        )}
      </div>
      {summary && (
        <p className="rise mx-1 rounded-xl bg-accent-soft px-3 py-2 text-[13px] leading-snug">{summary}</p>
      )}
      {day.entries.length > 0 && (
        <ol className="flex flex-col">
          {day.entries.map((entry, index) => (
            <Entry key={`${entry.at}-${index}`} entry={entry} />
          ))}
        </ol>
      )}
    </section>
  );
}

/**
 * The life timeline: day by day, what the user asked, what NOVA did, what it remembered and
 * which reminders rang. Built from what NOVA already keeps; the model only writes a day's
 * summary when asked.
 */
export function TimelinePanel() {
  const [days, setDays] = useState<TimelineDay[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback((last?: string) => {
    setLoading(true);
    timelineDays(PAGE, last)
      .then((more) => setDays((shown) => [...(last ? (shown ?? []) : []), ...more]))
      .catch((error: Error) => setProblem(error.message))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => load(), [load]);

  if (problem) return <p className="px-1 text-[13px] text-danger">{problem}</p>;
  if (!days) return <p className="px-1 text-[13px] text-text-muted">Loading…</p>;

  // Today always shows; other days only when something happened.
  const shown = days.filter((day, index) => index === 0 || day.entries.length > 0);
  const oldest = days[days.length - 1]?.day;

  return (
    <div className="flex flex-col gap-4">
      {shown.map((day) => (
        <Day key={day.day} day={day} />
      ))}
      {shown.length === 1 && days[0].entries.length === 0 && (
        <p className="px-1 text-[12.500px] text-text-muted">
          Your days with NOVA appear here: what you asked, what it did, what it remembered.
        </p>
      )}
      {oldest && (
        <button
          type="button"
          disabled={loading}
          onClick={() => load(before(oldest))}
          className="self-center rounded-full border border-line px-3 py-1.5 text-[12.500px] text-text-muted hover:text-text disabled:opacity-60"
        >
          {loading ? "Loading…" : "Earlier days"}
        </button>
      )}
    </div>
  );
}
