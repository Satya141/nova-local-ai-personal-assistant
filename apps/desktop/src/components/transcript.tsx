import Markdown from "react-markdown";

import { Character, type CharacterKind, type CharacterState } from "@/components/character";
import { AlertIcon, CheckIcon, CrossIcon, MicIcon, ShieldIcon } from "@/components/icons";
import type { Item, ToolState } from "@/lib/use-agent";

/** Which character carries out each tool. Unknown tools, and memory, are Nova's own. */
const TOOL_OWNERS: Record<string, CharacterKind> = {
  open_application: "ember",
  search_files: "fern",
  open_path: "fern",
  close_application: "plum",
  create_reminder: "chime",
  list_reminders: "chime",
  cancel_reminder: "chime",
  look_at_screen: "iris",
};

export function toolOwner(name: string): CharacterKind {
  return TOOL_OWNERS[name] ?? "nova";
}

const TOOL_FACES: Record<ToolState, CharacterState> = {
  running: "working",
  awaiting: "confirm",
  ok: "done",
  failed: "error",
  declined: "asleep",
};

const TOOL_STATUS: Record<ToolState, string> = {
  running: "Working",
  awaiting: "Needs your OK",
  ok: "Done",
  failed: "Did not work",
  declined: "Cancelled",
};

function ToolStatus({ state }: { state: ToolState }) {
  const tone = {
    running: "text-text-muted",
    awaiting: "text-warn",
    ok: "text-ok",
    failed: "text-danger",
    declined: "text-text-muted",
  }[state];
  return (
    <span className={`ml-auto flex flex-none items-center gap-1 pl-3 text-[11px] font-medium ${tone}`}>
      {state === "running" && <span className="spinner" aria-hidden="true" />}
      {state === "awaiting" && <ShieldIcon size={12} />}
      {state === "ok" && <CheckIcon size={12} />}
      {(state === "failed" || state === "declined") && <CrossIcon size={12} />}
      {TOOL_STATUS[state]}
    </span>
  );
}

function ToolRow({
  item,
  onAnswer,
}: {
  item: Extract<Item, { kind: "tool" }>;
  onAnswer: (callId: string, approved: boolean) => void;
}) {
  const awaiting = item.state === "awaiting";
  return (
    <div
      className={`rise rounded-xl border px-2.5 py-1.5 ${
        awaiting ? "border-warn/50 bg-warn/8" : "border-line bg-surface-raised"
      }`}
    >
      <div className="flex items-center gap-2 text-[13px]">
        <Character kind={toolOwner(item.name)} state={TOOL_FACES[item.state]} size={22} />
        <span className={`truncate ${item.state === "declined" ? "text-text-muted line-through" : ""}`}>
          {item.summary}
        </span>
        <ToolStatus state={item.state} />
      </div>
      {awaiting && (
        <div className="mt-2 flex items-center justify-end gap-2 pb-0.5">
          <button
            type="button"
            // The safe choice holds focus, so a stray Enter never approves an action.
            autoFocus
            onClick={() => onAnswer(item.callId, false)}
            className="rounded-lg border border-line bg-surface px-3 py-1 text-[12px] font-medium hover:bg-surface-raised focus-visible:outline-2 focus-visible:outline-accent"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onAnswer(item.callId, true)}
            className="rounded-lg bg-warn px-3 py-1 text-[12px] font-semibold text-surface hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-warn"
          >
            Confirm
          </button>
        </div>
      )}
    </div>
  );
}

function MemoryRow({
  item,
  onForget,
}: {
  item: Extract<Item, { kind: "memory" }>;
  onForget: (memoryId: number) => void;
}) {
  return (
    <div className="rise flex items-center gap-2 px-1 text-[12px] text-text-muted">
      <Character kind="nova" state={item.forgotten ? "asleep" : "done"} size={18} />
      <span className="flex-none font-medium">{item.forgotten ? "Forgotten" : "Noted"}</span>
      <span className={`selectable truncate ${item.forgotten ? "line-through" : ""}`}>{item.content}</span>
      {!item.forgotten && (
        <button
          type="button"
          onClick={() => onForget(item.memoryId)}
          className="ml-auto flex-none rounded px-1.5 py-0.5 hover:bg-surface-raised hover:text-text focus-visible:outline-2 focus-visible:outline-accent"
        >
          Forget
        </button>
      )}
    </div>
  );
}

export function Transcript({
  items,
  onAnswer,
  onForget = () => {},
}: {
  items: Item[];
  onAnswer: (callId: string, approved: boolean) => void;
  onForget?: (memoryId: number) => void;
}) {
  return (
    <div className="flex flex-col gap-2.5">
      {items.map((item) => {
        switch (item.kind) {
          case "user":
            return (
              <p
                key={item.key}
                className="rise selectable flex max-w-[85%] items-center gap-1.5 self-end rounded-2xl rounded-br-md bg-accent-soft px-3 py-1.5 text-[13.500px] leading-snug"
              >
                {item.spoken && (
                  <span className="text-accent" title="Said out loud">
                    <MicIcon size={12} />
                  </span>
                )}
                {item.text}
              </p>
            );
          case "assistant":
            return (
              <div key={item.key} className="markdown selectable rise text-[14px]">
                <Markdown>{item.text}</Markdown>
              </div>
            );
          case "tool":
            return <ToolRow key={item.key} item={item} onAnswer={onAnswer} />;
          case "memory":
            return <MemoryRow key={item.key} item={item} onForget={onForget} />;
          case "error":
            return (
              <p
                key={item.key}
                role="alert"
                className="rise selectable flex items-start gap-2 rounded-xl border border-danger/40 bg-danger/8 px-2.5 py-2 text-[13px] text-danger"
              >
                <span className="mt-0.5">
                  <AlertIcon />
                </span>
                {item.text}
              </p>
            );
        }
      })}
    </div>
  );
}
