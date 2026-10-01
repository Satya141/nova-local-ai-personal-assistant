import { useCallback, useEffect, useState } from "react";

import { Character } from "@/components/character";
import { type PullProgress, type SetupModel, type SetupStatus, pullModel, setupStatus } from "@/lib/backend";

const BUTTON =
  "flex-none rounded-full bg-accent px-3 py-1 text-[12.500px] font-semibold text-surface disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent";

function gigabytes(bytes: number): string {
  return `${(bytes / 1e9).toFixed(bytes >= 1e10 ? 0 : 1)} GB`;
}

function ModelRow({ model, onDone }: { model: SetupModel; onDone: () => void }) {
  const [progress, setProgress] = useState<PullProgress | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const busy = progress !== null && !progress.done && !progress.error;
  const share = progress?.total ? (progress.completed ?? 0) / progress.total : 0;

  const download = () => {
    setProblem(null);
    setProgress({ status: "starting" });
    pullModel(model.role, (event) => {
      if (event.error) setProblem(event.error);
      setProgress(event);
    })
      .catch((error: Error) => setProblem(error.message))
      .finally(onDone);
  };

  return (
    <li className="flex flex-col gap-1 rounded-lg px-1 py-1.5">
      <div className="flex items-center gap-2.5">
        <span
          className={`grid size-[18px] flex-none place-items-center rounded-full text-[10px] font-bold ${model.present ? "bg-ok text-surface" : "border border-line"}`}
          aria-label={model.present ? "Installed" : "Not installed"}
        >
          {model.present ? "✓" : ""}
        </span>
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="text-[13px]">
            <span className="font-mono text-[12.500px]">{model.name}</span>
          </span>
          <span className="text-[11.500px] text-text-muted">{model.purpose}</span>
        </div>
        {!model.present && (
          <button type="button" className={BUTTON} disabled={busy || model.downloading} onClick={download}>
            {busy || model.downloading ? "Downloading…" : "Download"}
          </button>
        )}
      </div>
      {busy && (
        <div className="ml-7 flex items-center gap-2">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-line" aria-hidden="true">
            <div className="h-full bg-accent transition-[width] duration-500" style={{ width: `${Math.round(share * 100)}%` }} />
          </div>
          <span className="w-[120px] flex-none text-right text-[11px] tabular-nums text-text-muted">
            {progress?.total ? `${gigabytes(progress.completed ?? 0)} of ${gigabytes(progress.total)}` : progress?.status}
          </span>
        </div>
      )}
      {problem && <p className="ml-7 text-[12px] text-danger">{problem}</p>}
    </li>
  );
}

/**
 * The first run: NOVA's thinking happens in Ollama, with models of several gigabytes. This says
 * what is missing and fetches each model when its button is pressed; nothing downloads by itself.
 */
export function SetupCard({ onReady, preview }: { onReady: () => void; preview?: SetupStatus }) {
  const [status, setStatus] = useState<SetupStatus | null>(preview ?? null);

  const check = useCallback(() => {
    if (preview) return; // the design gallery: no backend to ask
    setupStatus()
      .then((fresh) => {
        setStatus(fresh);
        if (fresh.ready && fresh.models.every((m) => m.present)) onReady();
      })
      .catch(() => {});
  }, [onReady, preview]);
  useEffect(check, [check]);

  // While Ollama is missing, notice when the user has installed and started it.
  useEffect(() => {
    if (!status || status.ollama) return;
    const timer = setInterval(check, 5000);
    return () => clearInterval(timer);
  }, [status, check]);

  if (!status) return null;
  const missing = status.models.filter((m) => !m.present);

  return (
    <div className="rise flex flex-col gap-3 border-t border-line px-4 py-3">
      <div className="flex items-start gap-3">
        <Character kind="nova" state={status.ollama ? "idle" : "asleep"} size={36} />
        <div className="flex min-w-0 flex-1 flex-col">
          <h2 className="text-[14px] font-semibold">Set up NOVA&apos;s thinking</h2>
          <p className="text-[12.500px] leading-snug text-text-muted">
            NOVA thinks on this PC, with free AI models run by Ollama. Nothing you say leaves your computer.
          </p>
        </div>
      </div>

      <ol className="flex flex-col gap-1">
        <li className="flex items-center gap-2.5 rounded-lg px-1 py-1.5">
          <span
            className={`grid size-[18px] flex-none place-items-center rounded-full text-[10px] font-bold ${status.ollama ? "bg-ok text-surface" : "border border-line"}`}
          >
            {status.ollama ? "✓" : ""}
          </span>
          <div className="flex min-w-0 flex-1 flex-col">
            <span className="text-[13px]">Ollama</span>
            <span className="text-[11.500px] text-text-muted">
              {status.ollama ? (
                "Installed and running."
              ) : (
                <>
                  Install it from{" "}
                  <a href="https://ollama.com/download" className="font-medium text-accent underline-offset-2 hover:underline">
                    ollama.com/download
                  </a>
                  , then open it. NOVA notices by itself.
                </>
              )}
            </span>
          </div>
          {!status.ollama && (
            <button type="button" onClick={check} className="flex-none rounded px-1.5 py-0.5 text-[12px] text-text-muted hover:bg-surface-raised hover:text-text">
              Check again
            </button>
          )}
        </li>
        {status.ollama && status.models.map((model) => <ModelRow key={model.role} model={model} onDone={check} />)}
      </ol>

      {status.ollama && missing.length > 0 && (
        <p className="text-[11.500px] text-text-muted">
          The models download once and stay on this PC. The first one is the only one NOVA needs to start.
        </p>
      )}
      {status.ready && missing.length > 0 && (
        <button type="button" onClick={onReady} className="self-start rounded-full border border-line px-3 py-1 text-[12.500px] text-text-muted hover:text-text">
          Start using NOVA now
        </button>
      )}
    </div>
  );
}
