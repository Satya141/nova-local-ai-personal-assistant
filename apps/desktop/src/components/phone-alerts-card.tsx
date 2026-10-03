import { useCallback, useEffect, useState } from "react";

import { Character } from "@/components/character";
import { testPush } from "@/lib/backend";
import { type PushState, pushState, turnOffPush, turnOnPush } from "@/lib/push";

type State = PushState | { kind: "checking" } | { kind: "working" };

const BUTTON = "flex-none rounded-full bg-accent px-3 py-1.5 text-[12.500px] font-semibold text-surface disabled:opacity-50";
const QUIET = "flex-none rounded-full border border-line px-3 py-1.5 text-[12.500px] text-text-muted disabled:opacity-50";

/**
 * Letting the PC ring this phone's reminders while it is locked (src/lib/push.ts). With
 * `onlyWhenOff`, it shows only while there is something to turn on, as a nudge on the chat screen.
 */
export function PhoneAlertsCard({ onlyWhenOff = false }: { onlyWhenOff?: boolean }) {
  const [state, setState] = useState<State>({ kind: "checking" });
  const [problem, setProblem] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // Once used, the card stays (to say it worked and offer a test) even where it shows only when off.
  const [used, setUsed] = useState(false);

  const check = useCallback(() => {
    pushState()
      .then(setState)
      .catch((error: Error) => {
        setProblem(error.message);
        setState({ kind: "off" });
      });
  }, []);
  useEffect(check, [check]);

  const act = (action: () => Promise<void>, done?: string) => {
    setUsed(true);
    setProblem(null);
    setNote(null);
    setState({ kind: "working" });
    action()
      .then(() => done && setNote(done))
      .catch((error: Error) => setProblem(error.message))
      .finally(check);
  };

  if (state.kind === "checking") return null;
  if (onlyWhenOff && !used && state.kind !== "off") return null;

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-line bg-surface-raised px-3 py-3">
      <div className="flex items-start gap-3">
        <Character kind="chime" state={state.kind === "on" ? "done" : state.kind === "working" ? "working" : "idle"} size={30} />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="text-[13.500px] font-semibold">Reminders on a locked phone</span>
          <span className="text-[12.500px] leading-snug text-text-muted">
            {state.kind === "unsupported" && state.reason}
            {state.kind === "blocked" &&
              "Notifications are blocked for NOVA. In Chrome, tap the icon left of the address, then Permissions → Notifications → Allow, and reload."}
            {(state.kind === "off" || state.kind === "working") &&
              "Your PC rings this phone when a reminder is due, even with the screen off. It goes through Google's notification service, sealed so only this phone can read it, and needs the PC online."}
            {state.kind === "on" && "On. Reminders ring here even when this phone is locked, while your PC is on and online."}
          </span>
        </div>
      </div>
      {(state.kind === "off" || state.kind === "working") && (
        <button type="button" className={`${BUTTON} self-start`} disabled={state.kind === "working"} onClick={() => act(turnOnPush, "Done. Send a test to hear it.")}>
          Turn on
        </button>
      )}
      {state.kind === "on" && (
        <div className="flex flex-wrap gap-2">
          <button type="button" className={BUTTON} onClick={() => act(testPush, "Sent. It should ring in a moment.")}>
            Send a test
          </button>
          <button type="button" className={QUIET} onClick={() => act(turnOffPush)}>
            Turn off
          </button>
        </div>
      )}
      {note && <p className="text-[12px] text-text-muted">{note}</p>}
      {problem && <p className="text-[12px] text-danger">{problem}</p>}
    </div>
  );
}
