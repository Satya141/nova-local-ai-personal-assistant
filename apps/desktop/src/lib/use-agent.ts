"use client";

import { useCallback, useReducer, useRef } from "react";

import {
  type AgentEvent,
  type Memory,
  type Risk,
  type TurnOptions,
  answerConfirmation,
  chat,
  forgetMemory,
  speak,
} from "@/lib/backend";

export type ToolState = "running" | "awaiting" | "ok" | "failed" | "declined" | "blocked";

export type Item =
  | { kind: "user"; key: number; text: string; spoken?: boolean }
  | { kind: "assistant"; key: number; text: string }
  | {
      kind: "tool";
      key: number;
      callId: string;
      name: string;
      summary: string;
      state: ToolState;
      risk?: Risk;
      warning?: string;
    }
  | { kind: "error"; key: number; text: string }
  // NOVA noticed a lasting fact and saved it; the user can take it back.
  | { kind: "memory"; key: number; memoryId: number; content: string; forgotten: boolean };

/** What the agent is doing during a turn. "idle" means no turn is running. */
export type Phase = "idle" | "thinking" | "working" | "confirm" | "speaking";

export type AgentState = {
  items: Item[];
  phase: Phase;
  /** How the last finished turn ended. Null while a turn runs or before the first one. */
  outcome: "ok" | "error" | null;
};

type Action =
  | { type: "sent"; text: string; spoken: boolean }
  | { type: "event"; event: AgentEvent }
  | { type: "answered"; callId: string; approved: boolean }
  | { type: "finished" }
  | { type: "reset" }
  | { type: "noted"; memory: Memory }
  | { type: "forgot"; memoryId: number };

const INITIAL: AgentState = { items: [], phase: "idle", outcome: null };

function updateTool(items: Item[], callId: string, change: (item: Extract<Item, { kind: "tool" }>) => Partial<Item>): Item[] {
  return items.map((item) =>
    item.kind === "tool" && item.callId === callId ? ({ ...item, ...change(item) } as Item) : item,
  );
}

function reduce(state: AgentState, action: Action): AgentState {
  const key = state.items.length;
  switch (action.type) {
    case "sent":
      return {
        items: [...state.items, { kind: "user", key, text: action.text, spoken: action.spoken }],
        phase: "thinking",
        outcome: null,
      };

    case "answered":
      return {
        ...state,
        phase: "working",
        items: updateTool(state.items, action.callId, () => ({ state: action.approved ? "running" : "declined" })),
      };

    case "finished":
      return { ...state, phase: "idle", outcome: state.outcome ?? "ok" };

    case "reset":
      return INITIAL;

    case "noted":
      if (state.items.some((item) => item.kind === "memory" && item.memoryId === action.memory.id)) return state;
      return {
        ...state,
        items: [
          ...state.items,
          { kind: "memory", key, memoryId: action.memory.id, content: action.memory.content, forgotten: false },
        ],
      };

    case "forgot":
      return {
        ...state,
        items: state.items.map((item) =>
          item.kind === "memory" && item.memoryId === action.memoryId ? { ...item, forgotten: true } : item,
        ),
      };

    case "event": {
      const { event } = action;
      switch (event.type) {
        case "token": {
          const last = state.items.at(-1);
          const items =
            last?.kind === "assistant"
              ? [...state.items.slice(0, -1), { ...last, text: last.text + event.text }]
              : [...state.items, { kind: "assistant" as const, key, text: event.text }];
          return { ...state, items, phase: "speaking" };
        }
        case "tool_call":
          return {
            ...state,
            phase: "working",
            items: [
              ...state.items,
              { kind: "tool", key, callId: event.id, name: event.name, summary: event.summary, state: "running" },
            ],
          };
        case "confirm_request":
          return {
            ...state,
            phase: "confirm",
            items: updateTool(state.items, event.id, () => ({ state: "awaiting", risk: event.risk, warning: event.warning })),
          };
        case "tool_result":
          return {
            ...state,
            // The model reads the result next.
            phase: "thinking",
            items: updateTool(state.items, event.id, (item) =>
              // A declined call also comes back as not-ok; keep showing it as the user's choice.
              item.state === "declined" ? {} : { state: event.blocked ? "blocked" : event.ok ? "ok" : "failed" },
            ),
          };
        case "error":
          return {
            ...state,
            outcome: "error",
            items: [...state.items, { kind: "error", key, text: event.message }],
          };
        default:
          return state;
      }
    }
  }
}

export function useAgent() {
  const [state, dispatch] = useReducer(reduce, INITIAL);
  const conversation = useRef<string | null>(null);
  const running = useRef<AbortController | null>(null);

  /**
   * Run one turn. A spoken request (`voice`) gets a short reply that is also read
   * aloud: the final message, after any tool calls.
   */
  const send = useCallback(async (text: string, { voice = false, screen = false }: TurnOptions = {}) => {
    if (running.current) return false;
    const controller = new AbortController();
    running.current = controller;
    dispatch({ type: "sent", text, spoken: voice });
    let reply = "";
    let failed = false;
    try {
      for await (const event of chat(text, conversation.current, controller.signal, { voice, screen })) {
        if (event.type === "conversation") {
          conversation.current = event.id;
          continue;
        }
        if (event.type === "token") reply += event.text;
        else if (event.type === "tool_call") reply = "";
        else if (event.type === "error") failed = true;
        else if (event.type === "confirm_request" && voice) speak("I need your OK on screen first.");
        dispatch({ type: "event", event });
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        failed = true;
        const message = error instanceof Error ? error.message : String(error);
        dispatch({ type: "event", event: { type: "error", message: `Lost contact with NOVA's backend. ${message}` } });
      }
    } finally {
      if (voice && !controller.signal.aborted) {
        if (reply.trim()) speak(reply);
        else if (failed) speak("Sorry, that didn't work. The details are on screen.");
      }
      // After a reset the turn that is ending is no longer the current one.
      if (running.current === controller) {
        running.current = null;
        dispatch({ type: "finished" });
      }
    }
    return true;
  }, []);

  const answer = useCallback((callId: string, approved: boolean) => {
    dispatch({ type: "answered", callId, approved });
    answerConfirmation(callId, approved).catch(() => {});
  }, []);

  /** Start a new conversation, abandoning any turn in progress. */
  const reset = useCallback(() => {
    running.current?.abort();
    running.current = null;
    conversation.current = null;
    dispatch({ type: "reset" });
  }, []);

  /** Show a memory NOVA saved, if it came from the conversation on screen. */
  const noteMemory = useCallback((conversationId: string, memory: Memory) => {
    if (conversationId === conversation.current) dispatch({ type: "noted", memory });
  }, []);

  const forget = useCallback(async (memoryId: number) => {
    if (await forgetMemory(memoryId).catch(() => false)) dispatch({ type: "forgot", memoryId });
  }, []);

  return { ...state, busy: state.phase !== "idle", send, answer, reset, noteMemory, forget };
}
