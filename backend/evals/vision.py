"""Screen-understanding evaluation with the real vision model and synthetic screenshots.

Draws realistic screens (an editor with an error, a dialog, a chart, a form, a
traceback, an invoice), asks the question a user would, and checks the answer
contains the facts that are on screen. No real screen is captured.

    cd backend
    .venv\\Scripts\\python -m evals.vision               # the configured vision model
    .venv\\Scripts\\python -m evals.vision --model qwen3-vl:4b
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time

from PIL import Image, ImageDraw, ImageFont

from nova.config import Settings
from nova.inference import OllamaProvider
from nova.vision.capture import Capture
from nova.vision.reader import ScreenReader

FONTS = r"C:\Windows\Fonts"


def font(name: str, size: int):
    try:
        return ImageFont.truetype(f"{FONTS}\\{name}", size)
    except OSError:
        return ImageFont.load_default(size)


def editor() -> Image.Image:
    image = Image.new("RGB", (1600, 900), "#1e1e1e")
    draw = ImageDraw.Draw(image)
    mono, ui = font("consola.ttf", 20), font("segoeui.ttf", 18)
    draw.rectangle((0, 0, 1600, 36), fill="#323233")
    draw.text((16, 7), "App.tsx - shop-frontend - Visual Studio Code", font=ui, fill="#cccccc")
    code = [
        "38  export function ProductList({ items }: Props) {",
        "39    const [filter, setFilter] = useState('');",
        "40    const visible = filter ? items.filter(byName(filter)) : items;",
        "41    return (",
        "42      <ul>{visible.map((item) => <Row key={item.id} item={item} />)}</ul>",
        "43    );",
        "44  }",
    ]
    for line, text in enumerate(code):
        draw.text((24, 70 + 30 * line), text, font=mono, fill="#d4d4d4")
    draw.rectangle((0, 560, 1600, 900), fill="#181818")
    draw.text((16, 570), "PROBLEMS   OUTPUT   TERMINAL", font=ui, fill="#8f8f8f")
    for line, (text, colour) in enumerate(
        [
            ("> shop-frontend@1.0.0 dev", "#cccccc"),
            ("Uncaught TypeError: Cannot read properties of undefined (reading 'map')", "#f14c4c"),
            ("    at ProductList (src/App.tsx:42:21)", "#f14c4c"),
            ("    at renderWithHooks (react-dom.development.js:16305:18)", "#8f8f8f"),
        ]
    ):
        draw.text((16, 610 + 30 * line), text, font=mono, fill=colour)
    return image


def dialog() -> Image.Image:
    image = Image.new("RGB", (1280, 720), "#0f6cbd")
    draw = ImageDraw.Draw(image)
    ui, bold = font("segoeui.ttf", 20), font("segoeuib.ttf", 20)
    draw.rectangle((340, 220, 940, 470), fill="#ffffff", outline="#999999")
    draw.text((360, 234), "Excel", font=bold, fill="#000000")
    draw.text((360, 290), "We couldn't find 'C:\\Reports\\q3_final.xlsx'.", font=ui, fill="#000000")
    draw.text((360, 322), "Is it possible it was moved, renamed or deleted?", font=ui, fill="#000000")
    draw.rectangle((820, 410, 920, 450), fill="#0f6cbd")
    draw.text((853, 416), "OK", font=ui, fill="#ffffff")
    return image


def chart() -> Image.Image:
    image = Image.new("RGB", (1200, 800), "#ffffff")
    draw = ImageDraw.Draw(image)
    ui, bold = font("segoeui.ttf", 22), font("segoeuib.ttf", 28)
    draw.text((80, 30), "Sales by quarter, 2026 (lakh rupees)", font=bold, fill="#222222")
    draw.line((120, 700, 1120, 700), fill="#444444", width=2)
    for index, (label, value) in enumerate([("Q1", 12), ("Q2", 18), ("Q3", 9), ("Q4", 22)]):
        left = 180 + index * 230
        top = 700 - value * 25
        draw.rectangle((left, top, left + 140, 700), fill="#4c8dff")
        draw.text((left + 50, 710), label, font=ui, fill="#222222")
        draw.text((left + 55, top - 34), str(value), font=ui, fill="#222222")
    return image


def form() -> Image.Image:
    image = Image.new("RGB", (1200, 760), "#f5f6f8")
    draw = ImageDraw.Draw(image)
    ui, bold = font("segoeui.ttf", 22), font("segoeuib.ttf", 30)
    draw.text((360, 80), "Create your account", font=bold, fill="#111111")
    for top, label, value in ((170, "Email", "satya@example.com"), (270, "Password", "••••••••••")):
        draw.text((360, top), label, font=ui, fill="#333333")
        draw.rectangle((360, top + 36, 840, top + 80), fill="#ffffff", outline="#bbbbbb")
        draw.text((374, top + 44), value, font=ui, fill="#111111")
    draw.rectangle((360, 400, 386, 426), fill="#ffffff", outline="#888888")
    draw.text((400, 398), "I agree to the Terms of Service", font=ui, fill="#333333")
    draw.rectangle((360, 470, 840, 520), fill="#c9ccd1")
    draw.text((560, 480), "Sign up", font=ui, fill="#8a8d93")
    draw.text((360, 530), "Accept the Terms of Service to continue.", font=font("segoeui.ttf", 18), fill="#b3261e")
    return image


def traceback() -> Image.Image:
    image = Image.new("RGB", (1400, 600), "#0c0c0c")
    draw = ImageDraw.Draw(image)
    mono = font("consola.ttf", 20)
    lines = [
        "PS C:\\Users\\satya\\scraper> python main.py",
        "Traceback (most recent call last):",
        '  File "C:\\Users\\satya\\scraper\\main.py", line 3, in <module>',
        "    import requests",
        "ModuleNotFoundError: No module named 'requests'",
        "PS C:\\Users\\satya\\scraper> _",
    ]
    for index, text in enumerate(lines):
        draw.text((16, 20 + 32 * index), text, font=mono, fill="#cccccc")
    return image


def invoice() -> Image.Image:
    image = Image.new("RGB", (1000, 1300), "#ffffff")
    draw = ImageDraw.Draw(image)
    ui, bold = font("segoeui.ttf", 22), font("segoeuib.ttf", 34)
    draw.text((70, 60), "INVOICE #4471", font=bold, fill="#111111")
    for index, text in enumerate(
        [
            "Billed to: Satya J, Hyderabad",
            "Issued: 20 September 2026",
            "Web hosting (12 months) ........ Rs 12,000",
            "Domain renewal ...................... Rs 1,450",
            "Support plan ......................... Rs 5,000",
            "Total due: Rs 18,450",
            "Due date: 15 October 2026",
        ]
    ):
        draw.text((70, 160 + 48 * index), text, font=bold if text.startswith("Total") else ui, fill="#222222")
    return image


# (name, screen, question, patterns the answer must all contain)
CASES = [
    ("editor error", editor, "What's wrong with my code?", [r"undefined", r"map", r"42|items"]),
    ("missing file", dialog, "Why didn't my file open?", [r"q3_final", r"moved|renamed|deleted|find|found"]),
    ("chart", chart, "Which quarter had the highest sales, and how much?", [r"Q4", r"22"]),
    ("disabled button", form, "Why can't I click Sign up?", [r"terms"]),
    ("traceback", traceback, "How do I fix this error?", [r"pip install requests"]),
    ("invoice", invoice, "How much do I owe and by when?", [r"18,?450", r"15 October|October 15|15/10"]),
]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default=None)
    parser.add_argument("--repeat", type=int, default=2)
    options = parser.parse_args()
    settings = Settings.from_env()
    model = options.model or settings.vision_model
    vision = OllamaProvider(settings.ollama_url, model, keep_alive=settings.keep_alive, num_ctx=settings.vision_num_ctx)

    print(f"vision model={model} cases={len(CASES)} repeat={options.repeat}\n")
    passed = total = 0
    timings = []
    for name, draw, question, patterns in CASES:
        image = draw()
        reader = ScreenReader(vision, lambda image=image, name=name: Capture(image, name, "test", "window"))
        marks = []
        for _ in range(options.repeat):
            started = time.monotonic()
            result = await reader.look(question)
            elapsed = time.monotonic() - started
            timings.append(elapsed)
            answer = result.content
            missing = [p for p in patterns if not re.search(p, answer, re.I)]
            ok = result.ok and not missing
            passed += ok
            total += 1
            marks.append(f"ok ({elapsed:.1f}s)" if ok else f"FAIL ({elapsed:.1f}s) missing {missing}: {answer[:160]!r}")
        print(f"{name:<16} " + " | ".join(marks))
        sys.stdout.flush()

    timings.sort()
    print(f"\npassed {passed}/{total}  median {timings[len(timings) // 2]:.1f}s  slowest {timings[-1]:.1f}s")
    await vision.aclose()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
