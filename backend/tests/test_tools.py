from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from nova.scheduler import ReminderStore
from nova.tools import build_registry, files
from nova.tools._windows import Window
from nova.tools.base import Risk
from nova.tools.files import OpenPathArgs, SearchFilesArgs, resolve_folder, search
from nova.tools.system import App, match_app, match_windows

APPS = [
    App("Visual Studio Code", "Microsoft.VisualStudioCode"),
    App("Visual Studio Code - Insiders", "Microsoft.VisualStudioCodeInsiders"),
    App("Visual Studio 2022", "VisualStudio.17"),
    App("Google Chrome", "Chrome"),
    App("Notepad", "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"),
    App("File Explorer", "Microsoft.Windows.Explorer"),
    App("Microsoft Edge", "MSEdge"),
]


@pytest.fixture
def full_registry(db, memory):
    return build_registry(memory, ReminderStore(db), lambda: None)


def test_registry_exposes_a_schema_for_every_tool(full_registry):
    assert full_registry.names() == [
        "cancel_reminder",
        "close_application",
        "create_reminder",
        "forget",
        "list_reminders",
        "open_application",
        "open_path",
        "recall",
        "remember",
        "search_files",
    ]
    for schema in full_registry.schemas():
        function = schema["function"]
        assert schema["type"] == "function"
        assert function["description"]
        assert function["parameters"]["type"] == "object"
        assert "title" not in function["parameters"]


def test_only_destructive_tools_need_confirmation(full_registry):
    confirming = [name for name in full_registry.names() if full_registry.get(name).requires_confirmation]
    assert confirming == ["close_application", "forget"]
    assert all(full_registry.get(name).risk is Risk.MEDIUM for name in confirming)


async def test_memory_tools(full_registry, memory):
    remember, recall, forget = (full_registry.get(n) for n in ("remember", "recall", "forget"))

    saved = await remember.handler(remember.args_model(content="The user supports Chennai Super Kings.", category="preference"))
    assert saved.ok
    memory_id = json.loads(saved.content)["id"]

    refused = await remember.handler(remember.args_model(content="My bank password is swordfish"))
    assert not refused.ok and "never stores" in refused.content

    found = json.loads((await recall.handler(recall.args_model(query="Chennai Super Kings"))).content)
    assert [m["id"] for m in found["memories"]] == [memory_id]
    everything = json.loads((await recall.handler(recall.args_model(query="everything you know about me"))).content)
    assert len(everything["memories"]) == 1

    args = forget.args_model(memory_id=memory_id)
    assert forget.describe(args) == "Forget: The user supports Chennai Super Kings."
    assert (await forget.handler(args)).ok
    assert memory.all() == []
    assert not (await forget.handler(args)).ok


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Notepad", "Notepad"),
        ("notepad", "Notepad"),
        ("VS Code", "Visual Studio Code"),
        ("vscode", "Visual Studio Code"),
        ("visual studio code", "Visual Studio Code"),
        ("chrome", "Google Chrome"),
        ("edge", "Microsoft Edge"),
        ("explorer", "File Explorer"),
        ("visual studio", "Visual Studio 2022"),
        ("notpad", "Notepad"),
    ],
)
def test_match_app_resolves_spoken_names(query, expected):
    app, _, _ = match_app(query, APPS)
    assert app is not None and app.name == expected


def test_match_app_flags_typo_matches_as_guesses():
    assert match_app("notpad", APPS)[1] is True
    assert match_app("notepad", APPS)[1] is False
    assert match_app("vs code", APPS)[1] is False


def test_match_app_never_substitutes_a_different_app():
    # Regression: "Photoshop" once opened "Photos" and was reported as a success.
    app, _, suggestions = match_app("Photoshop", [*APPS, App("Photos", "Microsoft.Windows.Photos")])
    assert app is None
    assert suggestions == ["Photos"]

    app, _, suggestions = match_app("google chrom browser", APPS)
    assert app is None
    assert "Google Chrome" in suggestions

    assert match_app("", APPS) == (None, False, [])


def test_match_windows_uses_process_name_and_title():
    windows = [
        Window(1, "notes.txt - Notepad", "Notepad"),
        Window(2, "chrome-tips.md - NOVA - Visual Studio Code", "Code"),
        Window(3, "Inbox - Google Chrome", "chrome"),
        # Store apps: every window belongs to the same host process.
        Window(4, "Calculator", "ApplicationFrameHost"),
        Window(5, "Settings", "ApplicationFrameHost"),
    ]

    def matched(query: str) -> list[int]:
        return [window.hwnd for window in match_windows(query, windows)]

    assert matched("Notepad") == [1]
    assert matched("VS Code") == [2]
    assert matched("chrome") == [3], "a document that mentions chrome must not match"
    assert matched("calculator") == [4], "closing one Store app must not touch the others"
    assert matched("excel") == []
    assert matched("") == []


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "Documents" / "work").mkdir(parents=True)
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    old = tmp_path / "Documents" / "Resume 2024.pdf"
    new = tmp_path / "Documents" / "work" / "resume-final.docx"
    for path in (
        old,
        new,
        tmp_path / "Desktop" / "budget 2026.xlsx",
        tmp_path / "node_modules" / "pkg" / "resume.js",
        tmp_path / ".git" / "resume",
    ):
        path.write_text("x")
    os.utime(old, (time.time() - 86400, time.time() - 86400))
    return tmp_path


def names(outcome: dict) -> list[str]:
    return [Path(item["path"]).name for item in outcome["results"]]


def test_search_matches_case_insensitively_newest_first(tree):
    outcome = search(tree, "resume", None, 10)
    assert names(outcome) == ["resume-final.docx", "Resume 2024.pdf"]
    assert outcome["complete"] is True


def test_search_skips_dependency_and_hidden_folders(tree):
    paths = [item["path"] for item in search(tree, "resume", None, 10)["results"]]
    assert not any("node_modules" in p or ".git" in p for p in paths)


def test_search_requires_every_word_and_honours_extension(tree):
    assert names(search(tree, "2026 budget", None, 10)) == ["budget 2026.xlsx"]
    assert names(search(tree, "resume", "pdf", 10)) == ["Resume 2024.pdf"]
    assert names(search(tree, "resume", ".PDF", 10)) == ["Resume 2024.pdf"]
    assert names(search(tree, "", "xlsx", 10)) == ["budget 2026.xlsx"]


def test_search_finds_folders_and_respects_limit(tree):
    assert names(search(tree, "work", None, 10)) == ["work"]
    outcome = search(tree, "resume", None, 1)
    assert len(outcome["results"]) == 1 and outcome["total_matches"] == 2


def test_search_reports_when_it_ran_out_of_time(tree):
    assert search(tree, "resume", None, 10, budget=-1)["complete"] is False


def test_resolve_folder(tree):
    assert resolve_folder(None, tree) == tree
    assert resolve_folder("Documents", tree) == tree / "Documents"
    assert resolve_folder(str(tree / "Desktop"), tree) == tree / "Desktop"
    assert resolve_folder("pictures", tree) is None
    assert resolve_folder("relative/path", tree) is None


async def test_search_files_tool_rejects_unknown_folder(tree, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tree)
    result = await files.search_files.handler(SearchFilesArgs(query="resume", folder="Z:/nope"))
    assert result.ok is False

    result = await files.search_files.handler(SearchFilesArgs(query="resume", folder="documents"))
    assert result.ok is True
    assert len(json.loads(result.content)["results"]) == 2


@pytest.fixture
def opened(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(os, "startfile", calls.append, raising=False)
    return calls


async def test_open_path_opens_documents_and_folders(tree, opened):
    document = tree / "Documents" / "Resume 2024.pdf"
    assert (await files.open_path.handler(OpenPathArgs(path=str(document)))).ok
    assert (await files.open_path.handler(OpenPathArgs(path=str(tree / "Desktop")))).ok
    assert opened == [str(document), str(tree / "Desktop")]


@pytest.mark.parametrize("name", ["setup.exe", "run.bat", "script.ps1", "Tool.LNK", "macro.vbs", "app.py"])
async def test_open_path_refuses_to_run_programs(tmp_path, opened, name):
    target = tmp_path / name
    target.write_text("x")
    result = await files.open_path.handler(OpenPathArgs(path=str(target)))
    assert result.ok is False
    assert opened == []


async def test_open_path_rejects_missing_and_relative_paths(tmp_path, opened):
    assert (await files.open_path.handler(OpenPathArgs(path=str(tmp_path / "ghost.txt")))).ok is False
    assert (await files.open_path.handler(OpenPathArgs(path="notes.txt"))).ok is False
    assert opened == []
