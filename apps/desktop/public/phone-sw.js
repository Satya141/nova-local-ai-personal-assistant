// NOVA's phone app, offline: keeps the app itself on the phone so it opens while the PC is away.
// Live data (/api) and the pocket model's files (/pocket, cached by the model library) pass
// straight through. Only this origin's own files are kept.

const CACHE = "nova-phone-v1";
const SHELL = ["/phone", "/phone.webmanifest", "/phone-icon.png", "/phone-icon-256.png"];
// The PC may be on another network entirely: give up waiting and use the kept copy.
const PATIENCE_MS = 4000;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key.startsWith("nova-phone-") && key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/pocket/")) return;
  if (url.pathname.startsWith("/_next/static/")) {
    event.respondWith(cacheFirst(request)); // file names carry a hash: a kept copy never goes stale
  } else if (request.mode === "navigate" ? url.pathname === "/phone" : SHELL.includes(url.pathname)) {
    event.respondWith(networkFirst(request, url.pathname));
  }
});

async function fetchSoon(request) {
  const stop = new AbortController();
  const timer = setTimeout(() => stop.abort(), PATIENCE_MS);
  try {
    return await fetch(request, { signal: stop.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function networkFirst(request, key) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetchSoon(request);
    if (response.ok && !response.redirected) await cache.put(key, response.clone());
    return response;
  } catch {
    return (await cache.match(key)) || Response.error();
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const kept = await cache.match(request);
  if (kept) return kept;
  const response = await fetch(request);
  if (response.ok) await cache.put(request, response.clone());
  return response;
}

// Reminders on a locked phone: the PC sends each one through the browser's push service, sealed
// for this phone (backend/nova/phone/push.py). While NOVA is on screen it shows the reminder
// card itself, so only a test rings then.
self.addEventListener("push", (event) => {
  let message = {};
  try {
    message = event.data ? event.data.json() : {};
  } catch {
    // Not one of NOVA's messages: show nothing more than a plain notice below.
  }
  event.waitUntil(notify(message));
});

async function notify(message) {
  if (message.type === "reminder") {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    if (windows.some((client) => client.visibilityState === "visible" && client.focused)) return;
  }
  const isReminder = message.type === "reminder";
  const title = isReminder ? (message.result ? message.text : "Reminder") : "NOVA";
  const body = isReminder ? message.result || message.text : message.text || "NOVA has something for you.";
  await self.registration.showNotification(title, {
    body,
    tag: isReminder ? `nova-reminder-${message.id}` : "nova-test",
    renotify: true,
    requireInteraction: isReminder,
    silent: false, // the phone's notification sound (Chrome can't choose another)
    vibrate: [400, 150, 400, 150, 400],
    icon: "/phone-icon-256.png",
    badge: "/phone-icon.png",
    timestamp: Date.parse(message.fired_at || "") || Date.now(),
  });
}

// A tap opens NOVA, where the reminder card waits with Done and Snooze.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const open = windows.find((client) => new URL(client.url).pathname.startsWith("/phone"));
      return open ? open.focus() : self.clients.openWindow("/phone");
    }),
  );
});
