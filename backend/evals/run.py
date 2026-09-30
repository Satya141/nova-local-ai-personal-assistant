"""Agent evaluations against the real local model.

Runs realistic requests through the real agent loop and the real model, with
the computer-facing tools swapped for sandboxed stand-ins: nothing opens,
closes or moves. Each scenario checks which tools were called, with what
arguments, and what NOVA said.

    cd backend
    .venv\\Scripts\\python -m evals.run                 # every scenario, twice
    .venv\\Scripts\\python -m evals.run --think         # with the model's reasoning on
    .venv\\Scripts\\python -m evals.run -k reminder     # only scenarios whose name matches

Needs Ollama running with the configured model. Takes a few minutes.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from nova.agent import Agent
from nova.config import Settings
from nova.database import Database
from nova.inference import OllamaProvider
from nova.memory import ConversationStore, MemoryStore
from nova.memory.embeddings import OllamaEmbedder
from nova.permissions import PermissionGate
from nova.scheduler import ReminderStore
from nova.tools import ToolResult, build_registry

HOME = r"C:\Users\satya"
FILES = {
    "resume": rf"{HOME}\Documents\Resume 2026.pdf",
    "budget": rf"{HOME}\Documents\Budget 2026.xlsx",
}
INSTALLED = {"visual studio code", "vs code", "vscode", "code", "calculator", "notepad", "paint", "google chrome", "chrome"}


@dataclass
class Run:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    reply: str = ""
    errors: list[str] = field(default_factory=list)
    reminders: ReminderStore | None = None
    memory: MemoryStore | None = None

    def called(self, name: str) -> list[dict[str, Any]]:
        return [args for tool, args in self.calls if tool == name]

    def order(self) -> list[str]:
        return [tool for tool, _ in self.calls]


@dataclass
class Scenario:
    name: str
    message: str
    check: Callable[[Run], str | None]  # None means pass; otherwise, why it failed
    memories: tuple[tuple[str, str], ...] = ()
    reminders: tuple[tuple[str, timedelta], ...] = ()


# --- sandboxed stand-ins for the tools that touch the computer ---------------------


def sandbox(registry, run: Run):
    def recorder(name: str, behaviour: Callable[[Any], ToolResult]):
        async def handler(args) -> ToolResult:
            run.calls.append((name, args.model_dump()))
            return behaviour(args)

        return handler

    def open_app(args) -> ToolResult:
        if args.name.lower().strip() in INSTALLED:
            return ToolResult(True, json.dumps({"opened": args.name.title()}))
        return ToolResult(False, f"'{args.name}' is not installed on this computer. Nothing was opened.")

    def search(args) -> ToolResult:
        hits = [path for key, path in FILES.items() if key in args.query.lower()]
        return ToolResult(True, json.dumps({"results": [{"path": p, "type": "file"} for p in hits]}))

    def open_path(args) -> ToolResult:
        known = set(FILES.values()) | {rf"{HOME}\Downloads", rf"{HOME}\Documents", rf"{HOME}\Desktop"}
        if args.path.rstrip("\\") in known:
            return ToolResult(True, json.dumps({"opened": args.path}))
        return ToolResult(False, f"'{args.path}' does not exist. Use search_files to find the right path.")

    stand_ins = {
        "open_application": open_app,
        "close_application": lambda args: ToolResult(True, json.dumps({"closed": [args.name]})),
        "search_files": search,
        "open_path": open_path,
    }
    for name in registry.names():
        tool = registry.get(name)
        if name in stand_ins:
            registry.replace(dataclasses.replace(tool, handler=recorder(name, stand_ins[name])))
        else:
            # The real memory and reminder tools run against the scenario's own in-memory database.
            registry.replace(dataclasses.replace(tool, handler=_recording(tool, run)))
    return registry


def _recording(tool, run: Run):
    async def handler(args) -> ToolResult:
        run.calls.append((tool.name, args.model_dump()))
        return await tool.handler(args)

    return handler


# --- checks ------------------------------------------------------------------------


def local_due(run: Run) -> datetime | None:
    upcoming = run.reminders.upcoming()
    return upcoming[0].due_at.astimezone() if upcoming else None


def expect_reminder(text_pattern: str, hour: int, minute: int = 0, day: str = "next", repeat: str = "none"):
    """day: 'next' (the next time that clock time comes round), 'tomorrow'."""

    def check(run: Run) -> str | None:
        if not run.called("create_reminder"):
            return f"no create_reminder call; calls={run.order()}"
        due = local_due(run)
        if due is None:
            return f"reminder not created; replies: {run.reply[:120]!r}"
        reminder = run.reminders.upcoming()[0]
        if not re.search(text_pattern, reminder.text, re.I):
            return f"text {reminder.text!r} does not match {text_pattern!r}"
        now = datetime.now().astimezone()
        wanted = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day == "tomorrow" or (day == "next" and wanted <= now):
            wanted += timedelta(days=1)
        if repeat == "weekdays":
            while wanted.weekday() >= 5:
                wanted += timedelta(days=1)
        if abs((due - wanted).total_seconds()) > 60:
            return f"due {due:%a %d %b %H:%M}, wanted {wanted:%a %d %b %H:%M}"
        if reminder.repeat != repeat:
            return f"repeat {reminder.repeat!r}, wanted {repeat!r}"
        return None

    return check


def check_relative_reminder(run: Run) -> str | None:
    due = local_due(run)
    if due is None:
        return f"no reminder; calls={run.order()}"
    delta = (due - datetime.now().astimezone()).total_seconds() / 60
    return None if 15 <= delta <= 21 else f"due in {delta:.1f} minutes, wanted 20"


def check_find_and_open(key: str):
    def check(run: Run) -> str | None:
        order = run.order()
        if "search_files" not in order or "open_path" not in order:
            return f"calls={order}"
        if order.index("search_files") > order.index("open_path"):
            return "opened before searching"
        opened = [args["path"] for args in run.called("open_path")]
        return None if FILES[key] in opened else f"opened {opened}"

    return check


def all_of(*checks: Callable[[Run], str | None]):
    def check(run: Run) -> str | None:
        for item in checks:
            problem = item(run)
            if problem:
                return problem
        return None

    return check


def opened_apps(*names: str):
    def check(run: Run) -> str | None:
        opened = {args["name"].lower() for args in run.called("open_application")}
        missing = [n for n in names if not any(n in o for o in opened)]
        return f"missing {missing}; opened {sorted(opened)}" if missing else None

    return check


def no_tools(run: Run) -> str | None:
    return f"unexpected calls {run.order()}" if run.calls else None


def reply_matches(pattern: str):
    def check(run: Run) -> str | None:
        return None if re.search(pattern, run.reply, re.I) else f"reply {run.reply[:160]!r} lacks /{pattern}/"

    return check


def reply_lacks(pattern: str):
    def check(run: Run) -> str | None:
        return f"reply claims /{pattern}/: {run.reply[:160]!r}" if re.search(pattern, run.reply, re.I) else None

    return check


def memory_contains(pattern: str):
    def check(run: Run) -> str | None:
        contents = [m.content for m in run.memory.all()]
        return None if any(re.search(pattern, c, re.I) for c in contents) else f"memories {contents}"

    return check


def memory_lacks(pattern: str):
    def check(run: Run) -> str | None:
        contents = [m.content for m in run.memory.all()]
        if any(re.search(pattern, c, re.I) for c in contents):
            return f"still remembered; calls={run.calls} reply={run.reply[:120]!r}"
        return None

    return check


def reminders_left(count: int):
    def check(run: Run) -> str | None:
        left = [r.text for r in run.reminders.upcoming()]
        return None if len(left) == count else f"reminders left {left}; calls={run.order()}"

    return check


SCENARIOS = [
    Scenario("open_one_app", "Open VS Code", opened_apps("code")),
    Scenario("open_two_apps", "Open Calculator and Notepad", opened_apps("calculator", "notepad")),
    Scenario(
        "close_then_open",
        "Close Notepad and open Paint",
        all_of(lambda r: None if r.called("close_application") else "no close", opened_apps("paint")),
    ),
    Scenario("find_and_open", "Find my resume and open it", check_find_and_open("resume")),
    Scenario(
        "open_downloads",
        "Open my Downloads folder",
        lambda r: None
        if any(a["path"].rstrip("\\").lower().endswith("downloads") for a in r.called("open_path"))
        else f"calls={r.calls}",
    ),
    Scenario("reminder_clock", "Remind me to call mom at 7 PM", expect_reminder(r"mom", 19)),
    Scenario("reminder_relative", "Remind me in 20 minutes to check the oven", check_relative_reminder),
    Scenario("reminder_tomorrow", "Set a reminder for 6 AM tomorrow to go running", expect_reminder(r"run", 6, day="tomorrow")),
    Scenario(
        "reminder_repeating",
        "Every weekday at 9 AM remind me to stand up and stretch",
        expect_reminder(r"stretch|stand", 9, repeat="weekdays"),
    ),
    Scenario(
        "reminder_weekday",
        "Remind me on Friday at 5 PM to submit the assignment",
        lambda r: (
            None
            if (due := local_due(r)) and due.strftime("%A") == "Friday" and due.hour == 17
            and timedelta(0) < due - datetime.now().astimezone() <= timedelta(days=7)
            else f"due {due:%A %d %b %H:%M}" if (due := local_due(r)) else f"no reminder; calls={r.calls}"
        ),
    ),
    Scenario(
        "three_step_chain",
        "Find the budget spreadsheet, open it, and remind me tomorrow at 10 AM to review it",
        all_of(check_find_and_open("budget"), expect_reminder(r"budget|review", 10, day="tomorrow")),
    ),
    Scenario("remember", "Remember that I prefer dark mode", memory_contains(r"dark mode")),
    Scenario(
        "recall_from_memory",
        "What am I working on these days?",
        reply_matches(r"NOVA"),
        memories=(("The user is building NOVA, a local-first AI assistant.", "project"),),
    ),
    Scenario(
        "forget",
        "Forget that I like jazz",
        memory_lacks(r"jazz"),
        memories=(("The user likes jazz.", "preference"), ("The user's sister is Priya.", "person")),
    ),
    Scenario(
        "cancel_reminder",
        "Cancel my gym reminder",
        reminders_left(1),
        reminders=(("Go to the gym", timedelta(hours=3)), ("Pay rent", timedelta(days=2))),
    ),
    Scenario("general_knowledge", "What is the capital of Australia?", all_of(no_tools, reply_matches(r"Canberra"))),
    Scenario(
        "unsupported_request",
        "Delete all the files in my Downloads folder",
        all_of(
            lambda r: None if not r.called("open_path") and not r.called("close_application") else f"calls={r.calls}",
            reply_lacks(r"\b(deleted|have been removed)\b"),
        ),
    ),
    Scenario(
        "missing_app",
        "Open Photoshop",
        all_of(
            lambda r: None if r.called("open_application") else "did not try",
            reply_lacks(r"photoshop (is|has been) (now )?open"),
        ),
    ),
]


# --- automatic memory ----------------------------------------------------------------

# (message, pattern the saved memory must match or None when nothing may be saved[, actions NOVA took])
EXTRACTION_CASES = [
    # Found in real use: the reminder half of these messages was saved as a "fact".
    (
        "I'm preparing for my Cognizant interview next Friday. Remind me in 1 minute to review my notes.",
        r"Cognizant",
        ["Remind you today at 11:20 PM: Review your Cognizant interview notes"],
    ),
    (
        "I'm preparing for my Cognizant interview next Friday. Remind me tomorrow at 9 AM to review my notes.",
        r"Cognizant",
        ["Remind you tomorrow at 9:00 AM: Review your Cognizant interview notes"],
    ),
    ("I'm working on a project called NOVA, it's a local-first AI assistant", r"NOVA"),
    ("My sister Priya's birthday is on 12 March", r"Priya"),
    ("I prefer short answers, please", r"short"),
    ("I usually go to the gym at 6 AM on weekdays", r"gym"),
    ("I live in Hyderabad", r"Hyderabad"),
    # Must be stored with a real date ("next Friday" is wrong a week later), and that date must be a Friday.
    ("I have my Cognizant interview next Friday, can you help me prepare?", r"(Cognizant|interview)(?!.*next).*Friday \d"),
    ("btw I'm a final-year computer science student", r"student|computer science"),
    ("Open VS Code, I'm going to work on my portfolio website", r"portfolio"),
    ("Open calculator", None),
    ("What is the capital of France?", None),
    ("Find my resume and open it", None),
    ("Remind me at 7 PM to call mom", None),
    ("Close Notepad", None),
    ("thanks!", None),
    ("my wifi password is hunter2", None),
    ("What time is it?", None),
    ("Search my downloads for the invoice pdf", None),
]


_DATE = re.compile(
    r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),? (\d{1,2}) "
    r"(January|February|March|April|May|June|July|August|September|October|November|December) (\d{4})"
)


def wrong_dates(text: str) -> list[str]:
    """Weekday-and-date phrases whose weekday does not match the date."""
    problems = []
    for weekday, day, month, year in _DATE.findall(text):
        actual = datetime.strptime(f"{day} {month} {year}", "%d %B %Y")
        if actual.strftime("%A") != weekday:
            problems.append(f"{weekday} {day} {month} {year} is a {actual:%A}")
    return problems


async def run_extraction(provider, embedder, repeat: int) -> tuple[int, int]:
    from nova.memory.extractor import MemoryExtractor

    passed = total = 0
    for message, pattern, *extra in EXTRACTION_CASES:
        handled = extra[0] if extra else None
        marks = []
        for _ in range(repeat):
            db = Database(":memory:")
            memory = MemoryStore(db, embedder)
            started = time.monotonic()
            extracted = await MemoryExtractor(provider, memory).extract(message, handled=handled)
            saved = [item.memory.content for item in extracted]
            elapsed = time.monotonic() - started
            db.close()
            if pattern is None:
                ok = not saved
            else:
                ok = bool(saved) and any(re.search(pattern, s, re.I) for s in saved)
            bad_dates = [problem for s in saved for problem in wrong_dates(s)]
            # Nothing short-lived may be kept, whatever else was found.
            bad_dates += [
                f"short-lived: {s}" for s in saved if re.search(r"minute|remind|from now|today|tomorrow", s, re.I)
            ]
            ok = ok and not bad_dates
            total += 1
            passed += ok
            marks.append(("ok" if ok else "FAIL") + f" ({elapsed:.1f}s) {saved}" + (f" {bad_dates}" if bad_dates else ""))
        print(f"{message[:44]:<46} " + " | ".join(marks))
        sys.stdout.flush()
    return passed, total


async def run_scenario(scenario: Scenario, settings: Settings, provider, embedder, db: Database) -> tuple[Run, float]:
    run = Run(reminders=ReminderStore(db), memory=MemoryStore(db, embedder))
    for content, category in scenario.memories:
        await run.memory.add(content, category, "explicit")
    for text, offset in scenario.reminders:
        run.reminders.create(text, datetime.now(UTC) + offset)

    registry = sandbox(build_registry(run.memory, run.reminders, lambda: None), run)
    gate = PermissionGate(confirm_timeout=5)
    store = ConversationStore(db)
    agent = Agent(provider, registry, gate, store, memory=run.memory, max_steps=settings.max_steps)

    started = time.monotonic()
    text: list[str] = []
    async for event in agent.run_turn(store.create_conversation(), scenario.message):
        if event["type"] == "token":
            text.append(event["text"])
        elif event["type"] == "confirm_request":
            gate.resolve(event["id"], True)  # The user says yes to everything in these runs.
        elif event["type"] == "error":
            run.errors.append(event["message"])
    run.reply = "".join(text).strip()
    return run, time.monotonic() - started


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--think", action="store_true", help="let the model reason before answering")
    parser.add_argument("-k", dest="filter", default="", help="only scenarios whose name contains this")
    parser.add_argument("--model", default=None)
    parser.add_argument("--memory", action="store_true", help="evaluate automatic memory extraction instead")
    options = parser.parse_args()

    settings = Settings.from_env()
    model = options.model or settings.model
    provider = OllamaProvider(settings.ollama_url, model, think=options.think, num_ctx=settings.num_ctx)
    embedder = OllamaEmbedder(settings.ollama_url, settings.embed_model) if settings.embed_model else None
    await provider.warm()

    if options.memory:
        print(f"model={model} extraction cases={len(EXTRACTION_CASES)} repeat={options.repeat}\n")
        passed, total = await run_extraction(provider, embedder, options.repeat)
        print(f"\npassed {passed}/{total} ({100 * passed / total:.0f}%)")
        await provider.aclose()
        return 0 if passed == total else 1

    chosen = [s for s in SCENARIOS if options.filter in s.name]
    print(f"model={model} think={options.think} scenarios={len(chosen)} repeat={options.repeat}\n")
    passed = total = 0
    timings = []
    for scenario in chosen:
        marks = []
        for _ in range(options.repeat):
            db = Database(":memory:")
            run, elapsed = await run_scenario(scenario, settings, provider, embedder, db)
            problem = (run.errors and f"error: {run.errors[0]}") or scenario.check(run)
            db.close()
            timings.append(elapsed)
            total += 1
            if problem:
                marks.append(f"FAIL ({elapsed:.1f}s) {problem}")
            else:
                passed += 1
                marks.append(f"ok ({elapsed:.1f}s)")
        print(f"{scenario.name:<22} " + " | ".join(marks))
        sys.stdout.flush()

    timings.sort()
    print(
        f"\npassed {passed}/{total} ({100 * passed / total:.0f}%)  "
        f"median {timings[len(timings) // 2]:.1f}s  slowest {timings[-1]:.1f}s"
    )
    await provider.aclose()
    if embedder:
        await embedder.aclose()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
