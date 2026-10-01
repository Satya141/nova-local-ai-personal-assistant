import { type ChangeEvent, type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { Character, type CharacterKind } from "@/components/character";
import {
  type Connections,
  connectGitHub,
  connectGoogle,
  connectionStatus,
  disconnectService,
  setGoogleClient,
} from "@/lib/backend";

const POLL_MS = 2000;

const BUTTON =
  "flex-none rounded px-1.5 py-0.5 text-[12px] text-text-muted hover:bg-surface-raised hover:text-text focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50";

function Account({ who, name, detail, children }: { who: CharacterKind; name: string; detail: string; children: ReactNode }) {
  return (
    <div className="rise flex flex-col gap-1.5 rounded-lg px-1 py-1">
      <div className="flex items-center gap-2.5">
        <Character kind={who} state="idle" size={22} />
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="text-[13px]">{name}</span>
          <span className="selectable truncate text-[11.500px] text-text-muted">{detail}</span>
        </div>
        <div className="flex flex-none items-center gap-1">{children}</div>
      </div>
    </div>
  );
}

/**
 * Gmail, Google Calendar and GitHub. Signing in happens on Google's or GitHub's own pages, or with
 * a token the user pastes here; credentials go straight to the backend's encrypted store and never
 * come back to this page.
 */
export function ConnectionsSection() {
  const [status, setStatus] = useState<Connections | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pasting, setPasting] = useState(false);
  const [token, setToken] = useState("");
  const file = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => {
    connectionStatus()
      .then(setStatus)
      .catch((error: Error) => setProblem(error.message));
  }, []);
  useEffect(refresh, [refresh]);

  // While the user finishes signing in in their browser, keep checking.
  const signingIn = status?.google.signing_in ?? false;
  useEffect(() => {
    if (!signingIn) return;
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [signingIn, refresh]);

  const run = async (action: () => Promise<Connections | void>) => {
    setBusy(true);
    setProblem(null);
    try {
      const next = await action();
      if (next) setStatus(next);
      else refresh();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const onClientFile = (event: ChangeEvent<HTMLInputElement>) => {
    const chosen = event.target.files?.[0];
    event.target.value = "";
    if (chosen) run(async () => setGoogleClient(await chosen.text()));
  };

  if (!status) return problem ? <p className="px-1 text-[12.500px] text-danger">{problem}</p> : null;
  const { google, github } = status;

  return (
    <div className="flex flex-col gap-1">
      <Account
        who="nova"
        name="Gmail and Google Calendar"
        detail={
          google.connected ? `Connected as ${google.account ?? "your Google account"}`
          : signingIn ? "Finish signing in in your browser…"
          : google.client ? "Not connected"
          : "Needs your Google OAuth client file first (see the README)"
        }
      >
        {google.connected ? (
          <button type="button" className={BUTTON} disabled={busy} onClick={() => run(() => disconnectService("google"))}>
            Disconnect
          </button>
        ) : (
          <>
            <input ref={file} type="file" accept=".json,application/json" className="hidden" onChange={onClientFile} />
            <button type="button" className={BUTTON} disabled={busy} onClick={() => file.current?.click()}>
              {google.client ? "Replace client file" : "Add client file"}
            </button>
            {google.client && (
              <button type="button" className={BUTTON} disabled={busy || signingIn} onClick={() => run(connectGoogle)}>
                Connect
              </button>
            )}
          </>
        )}
      </Account>
      {google.error && !google.connected && <p className="px-1 text-[12px] text-danger">{google.error}</p>}

      <Account
        who="ember"
        name="GitHub"
        detail={github.connected ? `Connected as ${github.account}` : "Not connected"}
      >
        {github.connected ? (
          <button type="button" className={BUTTON} disabled={busy} onClick={() => run(() => disconnectService("github"))}>
            Disconnect
          </button>
        ) : (
          <>
            {github.cli && (
              <button type="button" className={BUTTON} disabled={busy} onClick={() => run(() => connectGitHub({ fromCli: true }))}>
                Use my GitHub CLI login
              </button>
            )}
            <button type="button" className={BUTTON} disabled={busy} onClick={() => setPasting(!pasting)}>
              Paste a token
            </button>
          </>
        )}
      </Account>
      {pasting && !github.connected && (
        <form
          className="flex items-center gap-2 px-1"
          onSubmit={(event) => {
            event.preventDefault();
            run(async () => {
              const next = await connectGitHub({ token });
              setToken("");
              setPasting(false);
              return next;
            });
          }}
        >
          <input
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="GitHub token (github.com → Settings → Developer settings)"
            aria-label="GitHub token"
            autoComplete="off"
            spellCheck={false}
            className="selectable min-w-0 flex-1 rounded border border-line bg-surface px-2 py-1 text-[12.500px] outline-none focus:border-accent"
          />
          <button type="submit" className={BUTTON} disabled={busy || !token.trim()}>
            Connect
          </button>
        </form>
      )}
      {problem && <p className="px-1 text-[12px] text-danger">{problem}</p>}
    </div>
  );
}
