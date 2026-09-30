"""File tools: find files by name and open them with their default app."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from nova.tools.base import Tool, ToolResult

_SEARCH_SECONDS = 5.0
_MAX_CANDIDATES = 500

# Directories that are huge and never hold what the user means by "my files".
_SKIP_DIRS = {
    "appdata", "node_modules", "__pycache__", "venv", "site-packages", "target",
    "$recycle.bin", "system volume information", "windows", "program files",
    "program files (x86)", "programdata",
}

_KNOWN_FOLDERS = ("desktop", "documents", "downloads", "pictures", "music", "videos")

# Opening these would run code. Phase 1 has no tool that is allowed to do that.
_EXECUTABLE_SUFFIXES = {
    ".exe", ".com", ".scr", ".pif", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".hta", ".msi", ".msp", ".reg", ".cpl", ".jar",
    ".py", ".pyw", ".lnk", ".url", ".appref-ms", ".application", ".gadget", ".inf",
}


def resolve_folder(folder: str | None, home: Path) -> Path | None:
    """Turn the model's `folder` argument into a directory, or None if it is not one."""
    if not folder or not folder.strip():
        return home
    key = folder.strip().lower()
    if key in ("home", "~"):
        return home
    if key in _KNOWN_FOLDERS:
        # OneDrive folder backup moves Desktop/Documents/Pictures under OneDrive.
        for base in (home, home / "OneDrive"):
            candidate = base / key.capitalize()
            if candidate.is_dir():
                return candidate
        return None
    path = Path(folder).expanduser()
    return path if path.is_absolute() and path.is_dir() else None


# Words people use for a kind of file rather than its name: "the budget spreadsheet" is
# Budget 2026.xlsx. They filter by type instead of having to appear in the name.
_KINDS = {
    "spreadsheet": {".xlsx", ".xls", ".xlsm", ".csv", ".ods"},
    "excel": {".xlsx", ".xls", ".xlsm"},
    "pdf": {".pdf"},
    "document": {".docx", ".doc", ".pdf", ".txt", ".odt", ".rtf", ".md"},
    "doc": {".docx", ".doc"},
    "word": {".docx", ".doc"},
    "presentation": {".pptx", ".ppt", ".odp", ".key"},
    "slides": {".pptx", ".ppt", ".odp", ".key"},
    "deck": {".pptx", ".ppt", ".odp", ".key"},
    "powerpoint": {".pptx", ".ppt"},
    "photo": {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".bmp"},
    "picture": {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".bmp"},
    "image": {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".bmp", ".svg"},
    "screenshot": {".png", ".jpg", ".jpeg"},
    "video": {".mp4", ".mov", ".mkv", ".avi", ".webm"},
    "song": {".mp3", ".m4a", ".wav", ".flac", ".ogg"},
    "audio": {".mp3", ".m4a", ".wav", ".flac", ".ogg"},
    "zip": {".zip", ".7z", ".rar"},
    "archive": {".zip", ".7z", ".rar", ".tar", ".gz"},
}
_FILLER = {"my", "the", "a", "an", "file", "files", "of", "for", "latest", "newest", "recent"}


def normalize_extension(extension: str | None) -> str | None:
    """'.PDF' -> 'pdf'. Words the model uses for "no filter" ('all', '*', 'any') mean None:
    once it passed extension='all' and reported a full folder as empty."""
    if not extension:
        return None
    cleaned = extension.strip().lower().lstrip("*").lstrip(".")
    return None if cleaned in ("", "*", "all", "any", "none", "every", "everything") else cleaned


def parse_query(query: str, extension: str | None) -> tuple[list[str], set[str] | None]:
    """Name words to match, and the file types allowed (None: any)."""
    tokens: list[str] = []
    kinds: set[str] = set()
    for word in query.lower().replace(",", " ").split():
        singular = word[:-1] if word.endswith("s") and word[:-1] in _KINDS else word
        if singular in _KINDS:
            kinds |= _KINDS[singular]
        elif word not in _FILLER:
            tokens.append(word)
    extension = normalize_extension(extension)
    if extension:
        return tokens, {"." + extension}
    return tokens, kinds or None


def name_matches(name: str, is_dir: bool, tokens: list[str], suffixes: set[str] | None) -> bool:
    name = name.lower()
    if suffixes is not None and (is_dir or not name.endswith(tuple(suffixes))):
        return False
    return all(token in name for token in tokens)


def search(root: Path, query: str, extension: str | None, limit: int, budget: float = _SEARCH_SECONDS) -> dict:
    tokens, suffixes = parse_query(query, extension)
    deadline = time.monotonic() + budget
    found: list[tuple[float, dict]] = []
    timed_out = False

    # Breadth-first, so files near the top of the tree are found before the budget runs out.
    queue: deque[Path] = deque([root])
    while queue and len(found) < _MAX_CANDIDATES:
        if time.monotonic() > deadline:
            timed_out = True
            break
        try:
            entries = list(os.scandir(queue.popleft()))
        except OSError:
            continue
        for entry in entries:
            name = entry.name.lower()
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if name in _SKIP_DIRS or name.startswith("."):
                    continue
                queue.append(Path(entry.path))
            if name_matches(name, is_dir, tokens, suffixes):
                try:
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                found.append(
                    (
                        stat.st_mtime,
                        {
                            "path": entry.path,
                            "type": "folder" if is_dir else "file",
                            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                        },
                    )
                )

    found.sort(key=lambda item: item[0], reverse=True)
    return {
        "searched": str(root),
        "total_matches": len(found),
        "results": [item for _, item in found[:limit]],
        "complete": not timed_out and len(found) < _MAX_CANDIDATES,
    }


class SearchFilesArgs(BaseModel):
    query: str = Field(
        description="Words from the file or folder name, e.g. 'resume' or 'budget 2026'. Kind words such as "
        "'spreadsheet', 'pdf' or 'photo' filter by file type."
    )
    folder: str | None = Field(
        default=None,
        description="Where to search: 'desktop', 'documents', 'downloads', 'pictures', 'music', 'videos', "
        "or an absolute folder path. Omit to search the whole home folder.",
    )
    extension: str | None = Field(default=None, description="Only match this file extension, e.g. 'pdf'.")
    max_results: int = Field(default=10, ge=1, le=50)


async def _search_files(args: SearchFilesArgs) -> ToolResult:
    if not args.query.strip() and not normalize_extension(args.extension):
        return ToolResult(False, "Provide a query or an extension to search for.")
    root = resolve_folder(args.folder, Path.home())
    if root is None:
        return ToolResult(False, f"'{args.folder}' is not a folder on this computer.")
    outcome = await asyncio.to_thread(search, root, args.query, args.extension, args.max_results)
    return ToolResult(True, json.dumps(outcome))


class OpenPathArgs(BaseModel):
    path: str = Field(description="Absolute path of the file or folder to open, exactly as returned by search_files.")


def near_misses(path: Path, limit: int = 5) -> list[str]:
    """Real files in the same folder sharing a word with a path that does not exist.

    The model sometimes opens a name it made up from the user's words ("budget spreadsheet.xlsx")
    instead of the one search_files returned.
    """
    words = {w for w in path.stem.lower().replace("-", " ").replace("_", " ").split() if len(w) > 2}
    try:
        entries = list(os.scandir(path.parent)) if words and path.parent.is_dir() else []
    except OSError:
        return []
    return [e.path for e in entries if any(w in e.name.lower() for w in words)][:limit]


def missing_path(raw: str, guesses: list[str]) -> str:
    # Once, told only "does not exist", the model still said the file "has been opened".
    hint = (
        f" Files that do exist there: {'; '.join(guesses)}. Call open_path again with one of these exact paths."
        if guesses
        else " Use search_files to find the right path."
    )
    return f"Not opened: '{raw}' does not exist.{hint} Do not tell the user it was opened unless a call succeeds."


async def _open_path(args: OpenPathArgs) -> ToolResult:
    path = Path(args.path)
    if not path.is_absolute() or not path.exists():
        return ToolResult(False, missing_path(args.path, near_misses(path) if path.is_absolute() else []))
    if path.is_file() and path.suffix.lower() in _EXECUTABLE_SUFFIXES:
        return ToolResult(
            False,
            f"Refused: '{path.name}' is a program or script, and opening it would run it. "
            "Tell the user NOVA does not run programs from file paths; open_application launches installed apps.",
        )
    if not hasattr(os, "startfile"):
        return ToolResult(False, "Opening files is only implemented on Windows so far.")
    await asyncio.to_thread(os.startfile, str(path))
    return ToolResult(True, json.dumps({"opened": str(path)}))


search_files = Tool(
    name="search_files",
    description="Search the user's files and folders by name. Returns matching paths, newest first.",
    args_model=SearchFilesArgs,
    handler=_search_files,
    describe=lambda args: f"Search files for '{args.query}'" + (f" in {args.folder}" if args.folder else ""),
    read_only=True,
)

open_path = Tool(
    name="open_path",
    description="Open a document or folder in its default application. Cannot run programs or scripts.",
    args_model=OpenPathArgs,
    handler=_open_path,
    describe=lambda args: f"Open {args.path}",
)
