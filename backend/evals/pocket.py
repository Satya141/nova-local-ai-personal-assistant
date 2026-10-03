"""Pocket-mode evaluation: NOVA's small model in the phone app, with the PC switched off.

    cd backend; .venv\\Scripts\\python -m evals.pocket [--repeat 2] [--small]

--small evaluates the small model offered to phones without 16-bit maths on their graphics chip.

Needs the pocket model in backend/models/pocket (scripts/fetch_pocket_model.py), a built UI
(pnpm --dir apps/desktop build) and Microsoft Edge with WebGPU. It starts a scratch NOVA on
loopback ports, pairs a phone-sized headless Edge, copies the model to it, stops NOVA, chats
offline, starts NOVA again and checks what reached the PC. The service worker is blocked:
Edge's certificate-error flag does not reach it, so with one in control nothing loads here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import Page, async_playwright

from nova.config import Settings

BACKEND = Path(__file__).resolve().parent.parent
TOKEN = "pocket-eval"
API, APP = "http://127.0.0.1:8791", "https://127.0.0.1:8799"

# (message, check on the reply text and the action rows under it)
Check = Callable[[str, list[str]], str | None]


def reply_has(pattern: str) -> Check:
    return lambda reply, actions: None if re.search(pattern, reply, re.I) else f"reply lacks {pattern!r}: {reply!r}"


def no_actions(reply: str, actions: list[str]) -> str | None:
    return None if not actions else f"unexpected actions {actions}"


def remembered(pattern: str) -> Check:
    def check(reply: str, actions: list[str]) -> str | None:
        kept = [a for a in actions if a.startswith("Remembered:")]
        if len(kept) != 1 or not re.search(pattern, kept[0], re.I):
            return f"wanted one memory matching {pattern!r}, got {actions}"
        return None

    return check


def reminder(pattern: str, minutes: int | None = None, hour: int | None = None, day: str = "") -> Check:
    def check(reply: str, actions: list[str]) -> str | None:
        rows = [a for a in actions if a.startswith("Reminder")]
        if len(rows) != 1 or not re.search(pattern, rows[0], re.I) or any(a.startswith("Remembered") for a in actions):
            return f"wanted one reminder matching {pattern!r} and no memory, got {actions}"
        if day and day not in rows[0]:
            return f"wanted {day}: {rows[0]}"
        if hour is not None:
            clock = datetime(2000, 1, 1, hour).strftime("%-I:%M %p" if os.name != "nt" else "%#I:%M %p").lower()
            if clock not in rows[0].lower():
                return f"wanted {clock}: {rows[0]}"
        if minutes is not None:
            due = datetime.now() + timedelta(minutes=minutes)
            clock = due.strftime("%#I:%M" if os.name == "nt" else "%-I:%M")
            earlier = (due - timedelta(minutes=1)).strftime("%#I:%M" if os.name == "nt" else "%-I:%M")
            if clock not in rows[0] and earlier not in rows[0]:
                return f"wanted about {clock}: {rows[0]}"
        return None

    return check


def all_of(*checks: Check) -> Check:
    def check(reply: str, actions: list[str]) -> str | None:
        return next((problem for c in checks if (problem := c(reply, actions))), None)

    return check


CONVERSATION: list[tuple[str, Check]] = [
    ("Remember that my sister's name is Priya", remembered(r"Priya")),
    ("My exam is on 15 October", remembered(r"exam.*15 October|15 October.*exam")),
    ("Remind me in 10 minutes to drink water", reminder(r"water", minutes=10)),
    ("Remind me tomorrow at 7 AM to go running", reminder(r"^Reminder tomorrow at 7:00 am: Go running$", hour=7, day="tomorrow")),
    ("remind me in half an hour to check the oven", reminder(r": Check the oven$", minutes=30)),
    # "On Friday" is today when today is a Friday and 9 AM is still to come.
    ("Can you remind me about the electricity bill on Friday?", reminder(
        r"today at 9:00 am: The electricity bill$" if datetime.now().weekday() == 4 and datetime.now().hour < 9
        else r"Friday.*9:00 am: The electricity bill$")),
    ("What's my sister's name?", all_of(reply_has(r"Priya"), no_actions)),
    ("When is my exam?", all_of(reply_has(r"15 October|October 15"), no_actions)),
    ("Open VS Code", all_of(reply_has(r"needs your PC"), no_actions)),
    ("my bank password is hunter2", all_of(reply_has(r"don't keep|not saved|never keep"), no_actions)),
]


def call(path: str, body: dict | None = None, method: str | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        API + path, data=data, method=method or ("POST" if data else "GET"),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(request, timeout=15))


def start(env: dict) -> subprocess.Popen:
    process = subprocess.Popen(
        [sys.executable, "-m", "nova"], cwd=BACKEND, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for _ in range(80):
        try:
            call("/api/health")
            call("/api/phone", {"enabled": True})
            return process
        except Exception:
            time.sleep(0.5)
    raise SystemExit("NOVA did not start")


def stop(process: subprocess.Popen) -> None:
    """Stop NOVA for real. On Windows a venv's python.exe is a launcher for the real interpreter,
    and killing the launcher can leave the server running, so whatever listens on the port goes too."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
        ports = {API.rsplit(":", 1)[1], APP.rsplit(":", 1)[1]}
        listing = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True).stdout
        for line in listing.splitlines():
            parts = line.split()
            if len(parts) == 5 and parts[3] == "LISTENING" and parts[1].rsplit(":", 1)[1] in ports:
                subprocess.run(["taskkill", "/F", "/PID", parts[4]], capture_output=True)
    else:
        process.kill()
    process.wait()
    for _ in range(20):
        try:
            call("/api/health")
        except Exception:
            return
        time.sleep(0.5)
    raise SystemExit("NOVA did not stop")


async def ask(page: Page, text: str) -> tuple[str, list[str], float]:
    before = await page.locator("[data-pocket='nova']").count()
    await page.fill("input[aria-label='Message Nova']", text)
    await page.click("button[aria-label='Send']")
    started = time.monotonic()
    await page.wait_for_function(
        "() => !document.querySelector('header').innerText.match(/Thinking|Waking/)", timeout=300_000
    )
    elapsed = time.monotonic() - started
    answers = page.locator("[data-pocket='nova']")
    if await answers.count() <= before:
        return "", [], elapsed
    answer = answers.nth(before)
    reply = (await answer.locator("[data-pocket='reply']").inner_text()).strip()
    actions = [a.strip() for a in await answer.locator("[data-pocket='action']").all_inner_texts()]
    return reply, actions, elapsed


async def run_once(env: dict) -> tuple[int, int, list[str]]:
    process = start(env)
    code = call("/api/phone/code", method="POST")["code"]
    problems: list[str] = []
    passed = 0
    async with async_playwright() as p:
        # A real profile, as on a phone: a private ("incognito") one gets a few hundred MB of
        # storage, and the model then fails with "Quota exceeded".
        profile = Path(tempfile.mkdtemp(prefix="nova-pocket-profile-"))
        context = await p.chromium.launch_persistent_context(
            profile, channel="msedge", headless=True, args=["--ignore-certificate-errors", "--enable-unsafe-webgpu"],
            viewport={"width": 390, "height": 860}, service_workers="block",
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(f"{APP}/phone#code={code}")
        await page.wait_for_timeout(4000)
        await page.get_by_role("button", name="Memory").click()
        await page.get_by_role("button", name="Put Nova on this phone").click()
        started = time.monotonic()
        while "Remove from this phone" not in (card := await page.inner_text("main")):
            if time.monotonic() - started > 300:
                problem = await page.locator(".text-danger").all_inner_texts()
                raise SystemExit(f"the model was not copied to the phone: {problem or card[card.find('Nova on this phone'):][:300]!r}")
            await page.wait_for_timeout(2000)
        print(f"  copied the pocket model to the phone in {time.monotonic() - started:.0f}s")
        await page.get_by_role("button", name="Chat").click()

        stop(process)  # the PC goes away
        await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        for _ in range(30):
            header = await page.inner_text("header")
            if "Nova on this phone" in header:
                break
            await page.wait_for_timeout(2000)
        else:
            diagnosis = await page.evaluate(
                """async () => {
                    const info = JSON.parse(localStorage.getItem('nova-pocket-model') || 'null');
                    const adapter = navigator.gpu ? await navigator.gpu.requestAdapter() : null;
                    const keys = (await caches.keys()).join(',');
                    return {info, adapter: !!adapter, f16: adapter ? adapter.features.has('shader-f16') : null, caches: keys};
                }"""
            )
            raise SystemExit(f"pocket mode did not start; header {header!r}; {diagnosis}")
        for message, check in CONVERSATION:
            reply, actions, elapsed = await ask(page, message)
            problem = check(reply, actions)
            passed += problem is None
            print(f"  {'ok  ' if problem is None else 'FAIL'} ({elapsed:4.1f}s) {message!r} -> {reply[:90]!r} {actions}")
            if problem:
                problems.append(f"{message!r}: {problem}")

        process = start(env)  # and comes back: the changes go across
        await page.wait_for_function(
            "() => document.querySelector('header').innerText.includes('On your PC')", timeout=60_000
        )
        await page.wait_for_timeout(4000)
        memories = [m["content"] for m in call("/api/memories")["memories"]]
        reminders = [r["text"] for r in call("/api/reminders")["upcoming"]]
        synced_ok = (
            len(memories) == 2 and any("Priya" in m for m in memories) and any("exam" in m.lower() for m in memories)
            and len(reminders) == 4 and not any("hunter2" in m for m in memories)
        )
        passed += synced_ok
        print(f"  {'ok  ' if synced_ok else 'FAIL'} synced to the PC: memories={memories} reminders={reminders}")
        if not synced_ok:
            problems.append(f"sync: memories={memories} reminders={reminders}")
        await context.close()
        shutil.rmtree(profile, ignore_errors=True)
    stop(process)
    return passed, len(CONVERSATION) + 1, problems


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--small", action="store_true", help="the small model, for phones without shader-f16")
    args = parser.parse_args()
    passed = total = 0
    for run in range(args.repeat):
        data = Path(tempfile.mkdtemp(prefix="nova-pocket-eval-"))
        env = {
            **os.environ, "NOVA_DATA_DIR": str(data), "NOVA_PORT": "8791", "NOVA_API_TOKEN": TOKEN,
            "NOVA_VOICE": "false", "NOVA_BROWSER": "false", "NOVA_EMBED_MODEL": "", "NOVA_PHONE_HOST": "127.0.0.1",
            "NOVA_PHONE_PORT": "8798", "NOVA_PHONE_SECURE_PORT": "8799",
        }
        if args.small:  # offered as the main model, so the eval's Edge (which has 16-bit maths) gets it
            env["NOVA_POCKET_MODEL"], env["NOVA_POCKET_MODEL_LIB"] = Settings.pocket_model_small, Settings.pocket_model_small_lib
        print(f"run {run + 1}")
        got, of, _ = await run_once(env)
        passed, total = passed + got, total + of
        shutil.rmtree(data, ignore_errors=True)
    print(f"\npassed {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
