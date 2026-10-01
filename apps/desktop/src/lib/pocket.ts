/**
 * NOVA in your pocket: what the phone keeps for when the PC is out of reach.
 *
 * The PC holds the one memory. The phone keeps a copy of it (and of the reminders) to read,
 * and an outbox of the changes made on the phone meanwhile. When the PC is reachable again the
 * outbox goes to /api/sync, which applies each change once, with the PC's own safety checks,
 * and sends back a fresh copy. Kept in IndexedDB for this app's origin only; wiped when the PC
 * removes this phone.
 */

import { type Memory, type Reminder, type Snapshot, type SyncOp, syncPhone } from "@/lib/backend";

const DB = "nova-pocket";
const STORE = "state";

type State = { snapshot: Snapshot | null; outbox: SyncOp[]; lastSync: string | null };
const EMPTY: State = { snapshot: null, outbox: [], lastSync: null };

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function read(): Promise<State> {
  try {
    const db = await open();
    return await new Promise((resolve) => {
      const get = db.transaction(STORE).objectStore(STORE).get("state");
      get.onsuccess = () => resolve({ ...EMPTY, ...(get.result as State | undefined) });
      get.onerror = () => resolve(EMPTY);
    });
  } catch {
    return EMPTY; // storage blocked (a private tab): the phone simply has no copy
  }
}

async function write(state: State): Promise<void> {
  try {
    const db = await open();
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(STORE, "readwrite");
      transaction.objectStore(STORE).put(state, "state");
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
    });
  } catch {
    // Not stored: the change is still applied to what is on screen.
  }
}

// Changes are read-modify-write; one at a time, so two quick taps cannot lose one.
let queue: Promise<unknown> = Promise.resolve();
function update<T>(change: (state: State) => [State, T]): Promise<T> {
  const next = queue.then(async () => {
    const [state, result] = change(await read());
    await write(state);
    notify(state);
    return result;
  });
  queue = next.catch(() => {});
  return next;
}

type Listener = (view: PocketView) => void;
const listeners = new Set<Listener>();
function notify(state: State): void {
  const view = viewOf(state);
  listeners.forEach((listener) => listener(view));
}

/** Reminders the phone made offline carry this until the PC gives them a number. */
export type PocketReminder = Reminder & { madeBy?: string };

/** What the phone shows while the PC is away: its copy, with the outbox applied on top. */
export type PocketView = {
  memories: Memory[];
  reminders: PocketReminder[];
  waiting: number;
  lastSync: string | null;
};

function viewOf(state: State): PocketView {
  let memories = [...(state.snapshot?.memories ?? [])];
  let reminders: PocketReminder[] = [...(state.snapshot?.reminders ?? [])];
  const pending = state.snapshot?.pending ?? [];
  for (const reminder of pending) if (!reminders.some((r) => r.id === reminder.id)) reminders.push(reminder);
  for (const op of state.outbox) {
    if (op.kind === "remember") {
      memories = [
        { id: -1, category: "fact", content: op.text, source: "explicit", created_at: op.said_on, updated_at: op.said_on },
        ...memories,
      ];
    } else if (op.kind === "forget") {
      memories = memories.filter((m) => m.id !== op.memory_id);
    } else if (op.kind === "remind") {
      reminders.push({
        id: -1,
        madeBy: op.id,
        text: op.text,
        due_at: op.due_at,
        repeat: op.repeat ?? "none",
        status: op.shown ? "done" : "active",
        pending_ack: false,
        fired_at: null,
        task: null,
        result: null,
      });
    } else {
      const same = (r: PocketReminder) =>
        op.reminder_id !== undefined ? r.id === op.reminder_id : r.madeBy !== undefined && r.madeBy === op.made_by;
      if (op.kind === "cancel") {
        reminders = reminders.map((r) => (same(r) ? { ...r, pending_ack: false, status: "cancelled" } : r));
      } else if (op.kind === "dismiss") {
        // Rung on the phone and dismissed: a one-off is done (the PC counts the ring when it syncs).
        reminders = reminders.map((r) =>
          same(r) ? { ...r, pending_ack: false, status: r.repeat === "none" ? "done" : r.status } : r,
        );
      } else if (op.kind === "snooze") {
        const later = new Date(new Date(op.at).getTime() + op.minutes * 60_000).toISOString();
        reminders = reminders.map((r) => (same(r) ? { ...r, due_at: later, status: "active", pending_ack: false } : r));
      }
    }
  }
  reminders = reminders.filter((r) => r.status === "active" || r.pending_ack);
  reminders.sort((a, b) => a.due_at.localeCompare(b.due_at));
  return { memories, reminders, waiting: state.outbox.length, lastSync: state.lastSync };
}

export function watchPocket(listener: Listener): () => void {
  listeners.add(listener);
  void read().then((state) => listener(viewOf(state)));
  return () => listeners.delete(listener);
}

function newId(): string {
  return crypto.randomUUID();
}

function localDate(when = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}`;
}

/** Queue a change made on the phone. Returns its id. */
function queueOp(op: SyncOp): Promise<string> {
  return update((state) => [{ ...state, outbox: [...state.outbox, op] }, op.id]);
}

export const pocket = {
  remember: (text: string) => queueOp({ id: newId(), kind: "remember", text, said_on: localDate() }),
  forget: (memoryId: number) => queueOp({ id: newId(), kind: "forget", memory_id: memoryId }),
  remind: (text: string, due: Date, repeat: Reminder["repeat"] = "none") =>
    queueOp({ id: newId(), kind: "remind", text, due_at: due.toISOString(), repeat }),
  cancel: (reminder: PocketReminder) => queueOp({ id: newId(), kind: "cancel", ...target(reminder) }),
  dismiss: (reminder: PocketReminder) =>
    // A reminder made offline and not yet sent: just mark it seen, so the PC does not ring it again.
    reminder.madeBy
      ? update((state) => [
          { ...state, outbox: state.outbox.map((op) => (op.id === reminder.madeBy ? { ...op, shown: true } : op)) },
          reminder.madeBy as string,
        ])
      : queueOp({ id: newId(), kind: "dismiss", ...target(reminder) }),
  snooze: (reminder: PocketReminder, minutes: number) =>
    queueOp({ id: newId(), kind: "snooze", ...target(reminder), minutes, at: new Date().toISOString() }),
};

function target(reminder: PocketReminder): { reminder_id?: number; made_by?: string } {
  return reminder.madeBy ? { made_by: reminder.madeBy } : { reminder_id: reminder.id };
}

let syncing: Promise<boolean> | null = null;

/** Send the outbox and take a fresh copy. False if the PC could not be reached. */
export function syncNow(): Promise<boolean> {
  syncing ??= (async () => {
    try {
      const { outbox } = await read();
      const { snapshot } = await syncPhone(outbox);
      // Changes queued while this request was out stay queued for the next one.
      const sent = new Set(outbox.map((op) => op.id));
      await update((state) => [
        { snapshot, outbox: state.outbox.filter((op) => !sent.has(op.id)), lastSync: new Date().toISOString() },
        null,
      ]);
      return true;
    } catch {
      return false;
    } finally {
      syncing = null;
    }
  })();
  return syncing;
}

/** Forget everything the phone keeps: the PC removed this phone. */
export async function wipePocket(): Promise<void> {
  await update(() => [EMPTY, null]);
}
