import { useCallback, useEffect, useState } from "react";

import { Character } from "@/components/character";
import { LockIcon } from "@/components/icons";
import {
  type PhoneAccessStatus,
  listenForEvents,
  newPairingCode,
  phoneStatus,
  removePhone,
  setPhoneAccess,
} from "@/lib/backend";

const CODE_SECONDS = 300;

const STEPS = [
  "Point your phone's camera at the code and open the link.",
  "New phone? It shows how to install NOVA's certificate. Once only.",
  "It pairs by itself and opens NOVA.",
];

/** The pairing code on the PC: a QR code to scan, the same code to type, and how long it lasts. */
export function PairingCard({
  code,
  url,
  qr,
  secondsLeft,
  certificate,
  onCancel,
}: {
  code: string;
  url: string;
  qr: string | null;
  secondsLeft: number;
  certificate: PhoneAccessStatus["certificate"];
  onCancel: () => void;
}) {
  const clock = `${Math.floor(secondsLeft / 60)}:${String(secondsLeft % 60).padStart(2, "0")}`;
  return (
    <div className="rise mx-1 overflow-hidden rounded-2xl border border-line bg-surface-raised">
      <div className="flex gap-5 p-4">
        {qr && (
          <div className="flex-none self-start rounded-xl bg-white p-2 shadow-[0_1px_3px_rgb(0_0_0/0.12)]">
            {/* eslint-disable-next-line @next/next/no-img-element -- a data address drawn by NOVA, not a remote image */}
            <img src={qr} alt="QR code for pairing a phone" className="block size-[148px] [image-rendering:pixelated]" />
          </div>
        )}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center gap-2">
            <Character kind="nova" state="listening" size={22} />
            <span className="text-[14px] font-semibold">Pair a phone</span>
            <span
              className="ml-auto rounded-full bg-accent-soft px-2 py-0.5 text-[11.500px] font-medium tabular-nums text-accent"
              aria-label={`Code expires in ${clock}`}
            >
              {clock}
            </span>
            <button type="button" className={BUTTON} onClick={onCancel}>
              Cancel
            </button>
          </div>
          <ol className="mt-3 flex flex-col gap-2">
            {STEPS.map((step, index) => (
              <li key={step} className="flex items-start gap-2.5 text-[12.500px] leading-snug">
                <span className="mt-px grid size-[18px] flex-none place-items-center rounded-full bg-accent text-[10.500px] font-semibold text-surface">
                  {index + 1}
                </span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
          <div className="mt-auto flex flex-wrap items-end gap-x-3 gap-y-1 pt-3">
            <div className="flex gap-1" aria-label={`Pairing code ${code}`}>
              {code.split("").map((digit, index) => (
                <span
                  key={index}
                  className={`selectable grid h-8 w-6 place-items-center rounded-md border border-line bg-surface font-mono text-[17px] font-semibold ${index === 2 ? "mr-1.5" : ""}`}
                >
                  {digit}
                </span>
              ))}
            </div>
            <span className="pb-1 text-[11.500px] leading-snug text-text-muted">
              No camera? Open <span className="selectable font-medium text-text">{url}</span> and type the code.
            </span>
          </div>
        </div>
      </div>
      {certificate && (
        <div className="flex items-center gap-1.5 border-t border-line px-4 py-2 text-[11.500px] text-text-muted">
          <LockIcon size={12} />
          <span className="truncate">
            Encrypted with <span className="font-medium text-text">{certificate.name}</span> · fingerprint starts{" "}
            <span className="selectable font-mono">{certificate.fingerprint}</span>
          </span>
        </div>
      )}
      <div className="h-[3px] bg-line" aria-hidden="true">
        <div
          className="h-full bg-accent transition-[width] duration-1000 ease-linear"
          style={{ width: `${(secondsLeft / CODE_SECONDS) * 100}%` }}
        />
      </div>
    </div>
  );
}

const BUTTON =
  "flex-none rounded px-1.5 py-0.5 text-[12px] text-text-muted hover:bg-surface-raised hover:text-text focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50";

/**
 * Phone access, on the PC: switch it on, pair a phone with a short-lived code, remove phones.
 * Off by default; while on, NOVA listens on the home network for paired phones only.
 */
export function PhoneAccessSection() {
  const [status, setStatus] = useState<PhoneAccessStatus | null>(null);
  const [pairing, setPairing] = useState<{ code: string; url: string; qr: string | null; until: number } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    phoneStatus()
      .then(setStatus)
      .catch((error: Error) => setProblem(error.message));
  }, []);
  useEffect(refresh, [refresh]);

  // The PC moving to another network changes the addresses, and makes any code on show stale.
  useEffect(
    () =>
      listenForEvents((event) => {
        if (event.type !== "phones") return;
        setStatus(event.status);
        setPairing((shown) => (shown && shown.url !== event.status.url ? null : shown));
      }),
    [],
  );

  // While a code is showing, count it down, and notice the phone that uses it.
  useEffect(() => {
    if (!pairing) return;
    const timer = setInterval(() => {
      setNow(Date.now());
      phoneStatus().then(setStatus).catch(() => {});
    }, 2000);
    return () => clearInterval(timer);
  }, [pairing]);
  const deviceCount = status?.devices.length ?? 0;
  const [pairedBefore, setPairedBefore] = useState(deviceCount);
  useEffect(() => {
    if (pairing && deviceCount > pairedBefore) setPairing(null);
    setPairedBefore(deviceCount);
  }, [deviceCount, pairing, pairedBefore]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setProblem(null);
    try {
      await action();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  if (!status) return problem ? <p className="px-1 text-[12px] text-danger">{problem}</p> : null;
  const secondsLeft = pairing ? Math.max(0, Math.round((pairing.until - now) / 1000)) : 0;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2.5 rounded-lg px-1 py-1">
        <Character kind="nova" state={status.running ? "done" : "asleep"} size={22} />
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="text-[13px]">Use NOVA from my phone</span>
          <span className="selectable truncate text-[11.500px] text-text-muted">
            {status.running && status.name_url
              ? `Paired phones find NOVA on any Wi-Fi you share, at ${status.name_url.replace("https://", "")}`
              : status.running && status.url
              ? `On your phone, open ${status.url} (same Wi-Fi). Encrypted with NOVA's certificate.`
              : status.enabled
              ? "On, waiting for a network."
              : "Off. While on, paired phones on the same Wi-Fi can use NOVA."}
          </span>
        </div>
        {status.running && (
          <button
            type="button"
            className={BUTTON}
            disabled={busy}
            onClick={() =>
              run(async () => {
                const fresh = await newPairingCode();
                setNow(Date.now());
                setPairing({ ...fresh, until: Date.now() + fresh.expires_in * 1000 });
              })
            }
          >
            Pair a phone
          </button>
        )}
        <button
          type="button"
          role="switch"
          aria-checked={status.enabled}
          className={BUTTON}
          disabled={busy}
          onClick={() =>
            run(async () => {
              setPairing(null);
              setStatus(await setPhoneAccess(!status.enabled));
            })
          }
        >
          {status.enabled ? "Turn off" : "Turn on"}
        </button>
      </div>

      {pairing && secondsLeft > 0 && (
        <PairingCard
          code={pairing.code}
          url={pairing.url}
          qr={pairing.qr}
          secondsLeft={secondsLeft}
          certificate={status.certificate}
          onCancel={() => setPairing(null)}
        />
      )}

      {status.devices.map((device) => (
        <div key={device.id} className="flex items-center gap-2.5 rounded-lg px-1 py-1 pl-9">
          <div className="flex min-w-0 flex-1 flex-col">
            <span className="truncate text-[13px]">{device.name}</span>
            <span className="text-[11.500px] text-text-muted">
              {device.last_seen ? `Last used ${new Date(device.last_seen).toLocaleString()}` : "Paired, not used yet"}
            </span>
          </div>
          <button
            type="button"
            className={BUTTON}
            disabled={busy}
            onClick={() => run(async () => setStatus(await removePhone(device.id)))}
          >
            Remove
          </button>
        </div>
      ))}
      {status.running && status.network?.category === "Public" && (
        <p className="mx-1 rounded-lg bg-accent-soft px-3 py-2 text-[12px] leading-snug">
          Windows treats <b>{status.network.name || "this Wi-Fi"}</b> as a public network, so its firewall turns your
          phone away. If you trust this network, make it private: Settings → Network &amp; internet → Wi-Fi →{" "}
          {status.network.name || "this network"} → <b>Private network</b>.
        </p>
      )}
      {(problem || status.error) && <p className="px-1 text-[12px] text-danger">{problem ?? status.error}</p>}
    </div>
  );
}
