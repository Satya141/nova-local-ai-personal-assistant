// Reminders ringing on a locked phone. The PC sends each one through the browser's push
// service, sealed for this phone (backend/nova/phone/push.py); the service worker
// (public/phone-sw.js) shows it as a notification.

import { pushInfo, removePushSubscription, savePushSubscription } from "@/lib/backend";

export type PushState =
  | { kind: "unsupported"; reason: string }
  | { kind: "blocked" }
  | { kind: "off" }
  | { kind: "on" };

// Turned off here on purpose: don't sign up again by ourselves when the PC is next reached.
const OFF_KEY = "nova-push-off";

function unsupported(): string | null {
  if (!window.isSecureContext) return "Notifications need NOVA's secure address. Scan the QR code on your PC again.";
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    return "This browser can't show notifications from NOVA. Open it in Chrome.";
  }
  return null;
}

function fromB64url(text: string): Uint8Array<ArrayBuffer> {
  const base64 = text.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(text.length / 4) * 4, "=");
  return Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
}

function sameKey(a: ArrayBuffer | null, b: Uint8Array): boolean {
  if (!a || a.byteLength !== b.length) return false;
  const bytes = new Uint8Array(a);
  return bytes.every((value, index) => value === b[index]);
}

async function registration(): Promise<ServiceWorkerRegistration> {
  await navigator.serviceWorker.register("/phone-sw.js", { scope: "/" });
  const ready = navigator.serviceWorker.ready;
  const late = new Promise<never>((_, reject) =>
    setTimeout(() => reject(new Error("NOVA's background helper didn't start. Reload the page and try again.")), 10_000),
  );
  return Promise.race([ready, late]);
}

function remember(off: boolean): void {
  try {
    if (off) localStorage.setItem(OFF_KEY, "1");
    else localStorage.removeItem(OFF_KEY);
  } catch {
    // Storage blocked: the phone may sign up again by itself, which is harmless.
  }
}

function turnedOff(): boolean {
  try {
    return localStorage.getItem(OFF_KEY) === "1";
  } catch {
    return false;
  }
}

async function subscribe(): Promise<void> {
  const key = fromB64url((await pushInfo()).public_key);
  const worker = await registration();
  let subscription = await worker.pushManager.getSubscription();
  // Signed up for another PC's key (or an older one of this PC): start again with this one.
  if (subscription && !sameKey(subscription.options.applicationServerKey, key)) {
    await subscription.unsubscribe();
    subscription = null;
  }
  subscription ??= await worker.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  await savePushSubscription(subscription.toJSON());
}

export async function pushState(): Promise<PushState> {
  const reason = unsupported();
  if (reason) return { kind: "unsupported", reason };
  if (Notification.permission === "denied") return { kind: "blocked" };
  if (Notification.permission !== "granted" || turnedOff()) return { kind: "off" };
  const [info, subscription] = await Promise.all([
    pushInfo(),
    registration().then((worker) => worker.pushManager.getSubscription()),
  ]);
  return info.subscribed && subscription ? { kind: "on" } : { kind: "off" };
}

/** After a tap: the browser asks for permission only in answer to one. */
export async function turnOnPush(): Promise<void> {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("Notifications weren't allowed. NOVA can only ring this phone if you allow them.");
  }
  remember(false);
  await subscribe();
}

export async function turnOffPush(): Promise<void> {
  remember(true);
  const subscription = await registration().then((worker) => worker.pushManager.getSubscription());
  await subscription?.unsubscribe();
  await removePushSubscription();
}

/** Each time the phone reaches the PC: keep the PC's copy of where to ring this phone current. */
export async function refreshPush(): Promise<void> {
  if (unsupported() || Notification.permission !== "granted" || turnedOff()) return;
  await subscribe();
}
