/**
 * NOVA in pocket mode: one turn with the phone's small model while the PC is away.
 *
 * Deliberately narrow, and decided in code where a 1.7B model is unreliable. What a message asks
 * for is classified here: things only the PC can do get an honest answer without the model;
 * reminders are read from the user's words here (time and text), so the model never touches
 * dates (AGENTS.md rule 5); something to remember is kept in the user's own words (the model
 * reworded "my sister's name is Priya" into "My user's sister's name is Priya" and saved it
 * twice). Only questions go to the model, which answers from the phone's copy of the memory in
 * a fixed JSON shape (web-llm constrains the output to the schema). Changes go through the phone's outbox
 * (src/lib/pocket.ts), which the PC applies with its own checks when it is back, and secrets are
 * refused here as well (rule 6).
 */

import { loadPocketModel, type PocketModelInfo } from "@/lib/pocket-model";
import { type PocketView, pocket } from "@/lib/pocket";

const WEEKDAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];

/**
 * What a message asks for, decided here rather than by the model: a 1.7B model, free to choose,
 * saved "Remind me in 10 minutes…" as a memory and claimed to have opened VS Code.
 *   "pc"       something only the PC can do: answered here, honestly, without the model
 *   "remind"   a reminder: time and text read here, without the model
 *   "remember" something to keep, in the user's own words, without the model
 *   "chat"     the model only answers
 */
export type Intent = "pc" | "remind" | "remember" | "chat";

const PC_ONLY =
  /^(please\s+|can you\s+|could you\s+)?(open|close|launch|start|run|quit|search|google|look up|browse|email|mail|send|reply|find|delete|move|rename|sort|play|show me|check my (email|mail|calendar|github))\b/i;

export function intentOf(message: string): Intent {
  const text = message.trim();
  if (/\bremind(er)?\b|\bwake me\b|\balarm\b/i.test(text)) return "remind";
  if (PC_ONLY.test(text)) return "pc";
  if (/\b(remember|note that|make a note|keep in mind|don'?t forget)\b/i.test(text)) return "remember";
  // A plain statement about themselves ("my sister's name is Priya", "I'm a student"), not a question.
  if (!/\?\s*$/.test(text) && /^(my|i am|i'm|im|i have|i live|i work|i study|i like|i love|i prefer|i usually)\b/i.test(text)) {
    return "remember";
  }
  return "chat";
}

const SCHEMA = { type: "object", properties: { reply: { type: "string" } }, required: ["reply"] };

/** What to keep, in the user's words: "Remember that my sister's name is Priya" → "My sister's name is Priya." */
export function factFrom(message: string): string {
  const text = message
    .trim()
    .replace(/^(please\s+|can you\s+|could you\s+|hey nova,?\s+)*/i, "")
    .replace(/^(remember|note|make a note|keep in mind|don'?t forget)\s*(that|this|:)?\s*/i, "")
    .replace(/\s+/g, " ")
    .replace(/[.!\s]+$/, "");
  return text ? `${text[0].toUpperCase()}${text.slice(1)}.` : "";
}

type Wanted = { reply: string };

export type PocketMessage = { role: "user" | "assistant"; text: string; actions?: string[] };

// Mirrors looks_sensitive in backend/nova/memory/long_term.py; the PC checks again when it syncs.
const SENSITIVE = [
  /\b(password|passcode|passwd|pin code|pin is|cvv|otp|one[- ]time code|secret key|aadhaar|aadhar|pan number|passport number|social security|bank account|ifsc)\b/i,
  /\b(sk|pk|rk)-[A-Za-z0-9_-]{16,}/,
  /\bgh[pousr]_[A-Za-z0-9]{30,}/,
  /\bAKIA[0-9A-Z]{16}\b/,
  /\b\d{3}-\d{2}-\d{4}\b/,
  /\b\d{4} \d{4} \d{4}\b/,
];

function luhn(digits: string): boolean {
  let total = 0;
  [...digits].reverse().forEach((char, index) => {
    let value = Number(char);
    if (index % 2) {
      value *= 2;
      if (value > 9) value -= 9;
    }
    total += value;
  });
  return total % 10 === 0;
}

export function looksSensitive(text: string): boolean {
  if (SENSITIVE.some((pattern) => pattern.test(text))) return true;
  for (const match of text.matchAll(/\b(?:\d[ -]?){13,19}\b/g)) {
    const digits = match[0].replace(/\D/g, "");
    if (digits.length >= 13 && digits.length <= 19 && luhn(digits)) return true;
  }
  return false;
}

const NUMBER_WORDS: Record<string, number> = {
  a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  fifteen: 15, twenty: 20, thirty: 30, forty: 40, "forty-five": 45, fifty: 50,
};
const AFTER = /\bin\s+(?:(half an hour)|(\d+|[a-z-]+)\s*(minutes?|mins?|hours?|hrs?))\b/i;
const CLOCK = /\b(?:at\s+)?(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])|\bat\s+(\d{1,2})(?:[:.](\d{2}))?\b|\b(noon|midday|midnight)\b/i;
const DAY = new RegExp(`\\b(day after tomorrow|tomorrow|today|tonight|this evening|this morning|(?:on\\s+|next\\s+|this\\s+)?(${WEEKDAYS.join("|")}))\\b`, "i");

/**
 * When the user wants a reminder, read from their own words: "in 10 minutes", "tomorrow at 7 AM",
 * "on Friday at 6 pm", "tonight". Done here, not by the model: given the choice, the 1.7B model
 * left the time out or made one up. Null when the message names no time.
 */
export function parseWhen(message: string, now = new Date()): Date | null {
  const after = AFTER.exec(message);
  if (after) {
    const amount = after[1] ? 30 : /^\d+$/.test(after[2]) ? Number(after[2]) : NUMBER_WORDS[after[2].toLowerCase()];
    if (amount) {
      const hours = !after[1] && after[3].toLowerCase().startsWith("h");
      return new Date(now.getTime() + amount * (hours ? 3_600_000 : 60_000));
    }
  }
  const day = DAY.exec(message)?.[1]?.toLowerCase().replace(/^(on|next|this)\s+(?=[a-z]+day$)/, "") ?? "";
  const clock = CLOCK.exec(message);
  let hour: number | null = null;
  let minute = 0;
  let meridiem: string | null = null;
  if (clock?.[6]) {
    hour = clock[6].toLowerCase() === "midnight" ? 0 : 12;
  } else if (clock) {
    hour = Number(clock[1] ?? clock[4]);
    minute = Number(clock[2] ?? clock[5] ?? 0);
    meridiem = clock[3]?.toLowerCase().replace(/\./g, "") ?? null;
    if (hour > 23 || minute > 59) return null;
    if (meridiem === "pm" && hour < 12) hour += 12;
    if (meridiem === "am" && hour === 12) hour = 0;
  }
  if (hour === null) {
    if (day === "tonight" || day === "this evening") hour = day === "tonight" ? 20 : 18;
    else if (day === "this morning") hour = 9;
    else if (day) hour = 9; // "tomorrow", "on Friday": the morning
    else return null;
  }
  const due = new Date(now);
  due.setSeconds(0, 0);
  due.setHours(hour, minute);
  if (day === "tomorrow") {
    due.setDate(due.getDate() + 1);
  } else if (day === "day after tomorrow") {
    due.setDate(due.getDate() + 2);
  } else if (WEEKDAYS.includes(day)) {
    let ahead = (WEEKDAYS.indexOf(day) - now.getDay() + 7) % 7;
    if (ahead === 0 && due <= now) ahead = 7;
    due.setDate(due.getDate() + ahead);
  } else if (due <= now) {
    // "at 7" with no am/pm said at 9 AM means 7 PM; otherwise the same time tomorrow.
    if (meridiem === null && hour < 12 && due.getTime() + 12 * 3_600_000 > now.getTime()) due.setHours(hour + 12);
    else due.setDate(due.getDate() + 1);
  }
  return due;
}

/** What to be reminded of, from the user's words: "Remind me tomorrow at 7 AM to go running" → "Go running". */
export function reminderText(message: string): string {
  let text = message
    .replace(/^\s*(please\s+|can you\s+|could you\s+|hey nova,?\s+)*/i, "")
    .replace(/\b(set (a|an)\s+)?(reminder|remind me|alarm|wake me( up)?)\b/gi, " ")
    .replace(AFTER, " ")
    .replace(new RegExp(CLOCK.source, "gi"), " ")
    .replace(new RegExp(DAY.source, "gi"), " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^(to|about|that|for|of)\s+/i, "")
    .replace(/[.!?,\s]+$/, "");
  return text ? text[0].toUpperCase() + text.slice(1) : "";
}

function describeDue(due: Date): string {
  const today = new Date();
  const tomorrow = new Date();
  tomorrow.setDate(today.getDate() + 1);
  const clock = due.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (due.toDateString() === today.toDateString()) return `today at ${clock}`;
  if (due.toDateString() === tomorrow.toDateString()) return `tomorrow at ${clock}`;
  return `${due.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })} at ${clock}`;
}

function instructions(view: PocketView, now: Date): string {
  const memories = view.memories.slice(0, 40).map((m) => `- ${m.content}`).join("\n") || "- (nothing yet)";
  const reminders =
    view.reminders.slice(0, 15).map((r) => `- ${r.text}: ${describeDue(new Date(r.due_at))}`).join("\n") || "- (none)";
  const clock = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return [
    "You are Nova, the user's assistant, running on their phone while their PC is away.",
    `It is ${now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}, ${clock}.`,
    "Answer the user's message from what you know about them, in one or two short, friendly sentences.",
    "If you do not know, say so. Do not make things up.",
    "You cannot open apps, files, websites, email or the calendar, and you cannot change anything: say that needs their PC, which will be back later.",
    "Reply in the JSON shape you are given.",
    "",
    "What you know about the user:",
    memories,
    "",
    "Their upcoming reminders:",
    reminders,
  ].join("\n");
}

/**
 * The model's JSON answer. With thinking switched off, Qwen3's reply still starts with an empty
 * "<think></think>" block, so the object is taken from the first "{" to the last "}".
 */
export function parseReply(content: string): Wanted | null {
  const start = content.indexOf("{");
  const end = content.lastIndexOf("}");
  if (start < 0 || end <= start) return null;
  try {
    const parsed = JSON.parse(content.slice(start, end + 1)) as Partial<Wanted>;
    if (typeof parsed.reply !== "string") return null;
    return { reply: parsed.reply };
  } catch {
    console.warn("pocket model reply was not JSON:", content.slice(0, 300));
    return null;
  }
}

/** One turn in pocket mode: the reply, and any memory or reminder queued for the PC. */
export async function pocketTurn(
  info: PocketModelInfo,
  view: PocketView,
  history: PocketMessage[],
  message: string,
  onLoading?: (share: number, text: string) => void,
): Promise<PocketMessage> {
  const intent = intentOf(message);
  if (intent === "pc") {
    return {
      role: "assistant",
      text: "That needs your PC, which is away right now. Ask me again when it is back. Meanwhile I can remember things and set reminders.",
    };
  }
  if (intent === "remember") {
    const fact = factFrom(message);
    if (looksSensitive(message)) {
      return { role: "assistant", text: "I don't keep passwords, codes or ID numbers, so I haven't saved that." };
    }
    const plain = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    if (view.memories.some((m) => plain(m.content) === plain(fact))) {
      return { role: "assistant", text: "I already know that." };
    }
    await pocket.remember(fact);
    return { role: "assistant", text: "Got it, I'll remember that.", actions: [`Remembered: ${fact}`] };
  }
  if (intent === "remind") {
    // Time and wording come from the user's own words, read in code; no model needed.
    const due = parseWhen(message);
    const what = reminderText(message);
    if (!due || !what) {
      return {
        role: "assistant",
        text: !due
          ? "When should it ring? Try “remind me at 6 PM to call home” or “in 20 minutes”."
          : "What should I remind you about? Try “remind me at 6 PM to call home”.",
      };
    }
    await pocket.remind(what, due);
    return {
      role: "assistant",
      text: `Okay, I'll remind you ${describeDue(due)}.`,
      actions: [`Reminder ${describeDue(due)}: ${what}`],
    };
  }

  const engine = await loadPocketModel(info, onLoading);
  const now = new Date();
  const response = await engine.chat.completions.create({
    messages: [
      { role: "system", content: instructions(view, now) },
      ...history.slice(-4).map((m) => ({ role: m.role, content: m.text })),
      { role: "user", content: message },
    ],
    response_format: { type: "json_object", schema: JSON.stringify(SCHEMA) },
    temperature: 0.3,
    max_tokens: 400,
    extra_body: { enable_thinking: false },
  });
  const content = response.choices[0]?.message?.content ?? "";
  const wanted = parseReply(content);
  if (!wanted) return { role: "assistant", text: "Sorry, I got muddled. Could you say that again?" };

  return { role: "assistant", text: wanted.reply.trim() || "I'm not sure." };
}
