import { useCallback, useEffect, useState } from "react";

import { Character } from "@/components/character";
import { pocketModelInfo } from "@/lib/backend";
import {
  type PocketModelInfo,
  deletePocketModel,
  loadPocketModel,
  pocketModelReady,
  pocketSupport,
  rememberInfo,
  rememberedInfo,
  unloadPocketModel,
} from "@/lib/pocket-model";

type State =
  | { kind: "checking" }
  | { kind: "unsupported"; reason: string }
  | { kind: "pc-has-none" }
  | { kind: "absent"; info: PocketModelInfo }
  | { kind: "downloading"; info: PocketModelInfo; share: number }
  | { kind: "ready"; info: PocketModelInfo }
  | { kind: "needs-pc" };

const megabytes = (bytes: number) => `${Math.round(bytes / 1e6).toLocaleString()} MB`;

const BUTTON = "flex-none rounded-full bg-accent px-3 py-1.5 text-[12.500px] font-semibold text-surface disabled:opacity-50";

/**
 * Putting NOVA's pocket model on this phone, so it can chat while the PC is away. The download
 * comes from the PC (so it needs the PC, once), after the user presses the button.
 */
export function PocketModelCard({ online, onReady }: { online: boolean; onReady?: (ready: boolean) => void }) {
  const [state, setState] = useState<State>({ kind: "checking" });
  const [problem, setProblem] = useState<string | null>(null);

  const check = useCallback(async () => {
    const support = await pocketSupport();
    if (!support.ok) return setState({ kind: "unsupported", reason: support.reason });
    let info = rememberedInfo();
    if (online) {
      info = await pocketModelInfo(support.f16).catch(() => info);
      if (info) rememberInfo(info);
    }
    if (info && (await pocketModelReady(info))) return setState({ kind: "ready", info });
    if (!online) return setState({ kind: "needs-pc" });
    setState(info?.available ? { kind: "absent", info } : { kind: "pc-has-none" });
  }, [online]);
  useEffect(() => {
    void check();
  }, [check]);
  useEffect(() => onReady?.(state.kind === "ready"), [state.kind, onReady]);

  const download = async (info: PocketModelInfo) => {
    setProblem(null);
    setState({ kind: "downloading", info, share: 0 });
    // Ask the browser to keep it: otherwise it may clear a gigabyte of "site data" to save space.
    await navigator.storage?.persist?.().catch(() => false);
    loadPocketModel(info, (share) => setState({ kind: "downloading", info, share }))
      .then(() => unloadPocketModel()) // downloaded and checked; free the graphics memory until needed
      .then(() => setState({ kind: "ready", info }))
      .catch((error: Error) => {
        setProblem(
          /quota/i.test(error.message)
            ? `Not enough free space on this phone: Nova needs about ${megabytes(info.size)}. Free some space and try again.`
            : error.message,
        );
        setState({ kind: "absent", info });
      });
  };

  if (state.kind === "checking") return null;

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-line bg-surface-raised px-3 py-3">
      <div className="flex items-start gap-3">
        <Character kind="nova" state={state.kind === "ready" ? "done" : state.kind === "downloading" ? "working" : "idle"} size={30} />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="text-[13.500px] font-semibold">Nova on this phone</span>
          <span className="text-[12.500px] leading-snug text-text-muted">
            {state.kind === "unsupported" && state.reason}
            {state.kind === "pc-has-none" && "Your PC has no pocket model to share yet."}
            {state.kind === "needs-pc" && "Connect to your PC once to put Nova on this phone, so it can chat while the PC is away."}
            {state.kind === "absent" &&
              `Chat with Nova even when your PC is off: a small AI model on this phone (${megabytes(state.info.size)}, copied from your PC, not the internet). Smaller than the PC's, and it can only remember things and set reminders.`}
            {state.kind === "downloading" && `Copying from your PC… ${Math.round(state.share * 100)}%`}
            {state.kind === "ready" && "Ready. When your PC is away, Nova answers here and sends what you add to the PC later."}
          </span>
        </div>
      </div>
      {state.kind === "downloading" && (
        <div className="h-1.5 overflow-hidden rounded-full bg-line" aria-hidden="true">
          <div className="h-full bg-accent transition-[width] duration-500" style={{ width: `${Math.round(state.share * 100)}%` }} />
        </div>
      )}
      {state.kind === "absent" && (
        <button type="button" className={`${BUTTON} self-start`} onClick={() => void download(state.info)}>
          Put Nova on this phone
        </button>
      )}
      {state.kind === "ready" && online && (
        <button
          type="button"
          className="self-start rounded-full border border-line px-3 py-1.5 text-[12.500px] text-text-muted"
          onClick={() =>
            void deletePocketModel(state.info)
              .then(check)
              .catch((error: Error) => setProblem(error.message))
          }
        >
          Remove from this phone
        </button>
      )}
      {problem && <p className="text-[12px] text-danger">{problem}</p>}
    </div>
  );
}
