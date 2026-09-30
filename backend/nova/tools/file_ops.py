"""File management: list, create folders, move, rename, and send to the Recycle Bin.

Everything is confined to the user's home folder, nothing is overwritten, and
nothing is ever deleted outright: "delete" means the Recycle Bin, so it can be
undone from there. Moving, renaming and deleting always ask first, once per
request, with the number of items spelled out.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from nova.tools.base import Risk, Tool, ToolResult
from nova.tools.files import normalize_extension, resolve_folder

_LIST_LIMIT = 200
# Folders the user sees as fixtures of their account; moving or binning them would be a disaster.
_FIXED = {"desktop", "documents", "downloads", "pictures", "music", "videos", "onedrive", "appdata"}


class Refused(ValueError):
    """The path is outside what NOVA may touch. The message is shown to the model."""


def home() -> Path:
    return Path.home().resolve()


def inside_home(raw: str, *, must_exist: bool = True) -> Path:
    """Resolve a path the model gave and make sure it is safely inside the home folder."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise Refused(f"'{raw}' is not a full path. Use the paths search_files or list_folder return.")
    path = path.resolve()
    root = home()
    if not path.is_relative_to(root):
        raise Refused(f"'{path}' is outside your home folder; NOVA only manages files in {root}.")
    if path.relative_to(root).parts[:1] and path.relative_to(root).parts[0].lower() == "appdata":
        raise Refused("App data is off limits: apps keep their settings there.")
    if must_exist and not path.exists():
        raise Refused(f"'{path}' does not exist.")
    return path


def movable(path: Path) -> None:
    """Refuse the home folder itself and its standard folders."""
    root = home()
    if path == root:
        raise Refused("That is your whole home folder.")
    if path.parent == root and path.name.lower() in _FIXED:
        raise Refused(f"'{path.name}' is one of your standard folders; NOVA will not move, rename or delete it.")


def _recycle(paths: list[Path]) -> None:
    """Send to the Recycle Bin with the shell's own undoable delete."""
    if sys.platform != "win32":
        raise Refused("Sending to the Recycle Bin is only implemented on Windows so far.")
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    fo_delete = 3
    flags = 0x0040 | 0x0010 | 0x0004 | 0x0400  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
    # A list of paths, each null-terminated, with an extra null at the end.
    sources = "\0".join(str(p) for p in paths) + "\0\0"
    operation = SHFILEOPSTRUCTW(None, fo_delete, sources, None, flags, False, None, None)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted:
        raise OSError(f"Windows could not move the items to the Recycle Bin (code {result}).")


def _describe_items(paths: list[str]) -> str:
    """'Resume.pdf', 'a.pdf, b.pdf and c.pdf', or '12 items (a.pdf, b.pdf, ...)' for the confirmation."""
    names = [Path(p).name or p for p in paths]
    if len(names) == 1:
        return names[0]
    if len(names) <= 3:
        return f"{', '.join(names[:-1])} and {names[-1]}"
    return f"{len(names)} items ({', '.join(names[:2])}, ...)"


# --- arguments -----------------------------------------------------------------


class ListArgs(BaseModel):
    folder: str = Field(description="Absolute folder path, or 'desktop', 'documents', 'downloads', 'pictures', 'music', 'videos'.")
    extension: str | None = Field(default=None, description="Only list files with this extension, e.g. 'pdf'.")


class CreateFolderArgs(BaseModel):
    path: str = Field(description="Absolute path of the folder to create, e.g. C:\\Users\\you\\Documents\\Invoices.")


class MoveArgs(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500, description="Absolute paths of the files or folders to move.")
    destination: str = Field(description="Absolute path of the folder to move them into. Created if it does not exist.")


class Group(BaseModel):
    into: str = Field(description="Name of the subfolder, e.g. 'Images'. Just a name, not a path.")
    files: list[str] = Field(min_length=1, description="Names of the files that go in it, exactly as list_folder shows them.")


class SortArgs(BaseModel):
    folder: str = Field(description="Absolute path of the folder whose files are being sorted. The subfolders are made inside it.")
    groups: list[Group] = Field(min_length=1, max_length=30, description="Every subfolder and the files that go in it.")


class RenameArgs(BaseModel):
    path: str = Field(description="Absolute path of the file or folder to rename.")
    new_name: str = Field(description="The new name only, not a path, e.g. 'Resume 2026.pdf'.")


class DeleteArgs(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500, description="Absolute paths to send to the Recycle Bin.")


# --- handlers ---------------------------------------------------------------------


def _list(args: ListArgs) -> ToolResult:
    folder = resolve_folder(args.folder, home())
    if folder is None:
        return ToolResult(False, f"'{args.folder}' is not a folder on this computer.")
    folder = inside_home(str(folder))
    extension = normalize_extension(args.extension)
    suffix = "." + extension if extension else None
    entries = []
    for entry in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if entry.name.startswith(".") or (suffix and (entry.is_dir() or entry.suffix.lower() != suffix)):
            continue
        stat = entry.stat()
        entries.append(
            {
                "path": str(entry),
                "type": "folder" if entry.is_dir() else "file",
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
        )
    return ToolResult(
        True,
        json.dumps({"folder": str(folder), "count": len(entries), "entries": entries[:_LIST_LIMIT], "complete": len(entries) <= _LIST_LIMIT}),
    )


def _create_folder(args: CreateFolderArgs) -> ToolResult:
    path = inside_home(args.path, must_exist=False)
    if path.exists():
        return ToolResult(True, json.dumps({"exists": str(path)}))
    path.mkdir(parents=True)
    return ToolResult(True, json.dumps({"created": str(path)}))


def _move(args: MoveArgs) -> ToolResult:
    destination = inside_home(args.destination, must_exist=False)
    if destination.exists() and not destination.is_dir():
        return ToolResult(False, f"'{destination}' is a file, not a folder.")
    sources = [inside_home(p) for p in args.paths]
    for source in sources:
        movable(source)
        if destination == source or destination.is_relative_to(source):
            raise Refused(f"Cannot move '{source.name}' into itself.")
    clashes = [s.name for s in sources if (destination / s.name).exists()]
    if clashes:
        return ToolResult(False, f"Not moved: {', '.join(clashes[:5])} already exist in {destination}. Nothing was overwritten.")
    created = not destination.exists()
    destination.mkdir(parents=True, exist_ok=True)
    moved = []
    for source in sources:
        shutil.move(str(source), str(destination / source.name))
        moved.append(source.name)
    return ToolResult(True, json.dumps({"moved": moved, "to": str(destination), "created_folder": created}))


# Names Windows reserves for devices; creating a folder called "CON" fails.
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _valid_name(name: str) -> bool:
    return (
        bool(name)
        and name not in {".", ".."}
        and not any(c in name for c in '\\/:*?"<>|')
        and name.split(".")[0].strip().lower() not in _RESERVED
        # Windows drops trailing dots and spaces, so "Images." would silently become "Images".
        and not name.endswith((".", " "))
    )


def _sort(args: SortArgs) -> ToolResult:
    """Check the whole plan first, so a mistake anywhere leaves the folder untouched."""
    folder = inside_home(args.folder)
    if not folder.is_dir():
        return ToolResult(False, f"'{folder}' is not a folder.")
    problems: list[str] = []
    seen: set[str] = set()
    plan: list[tuple[Path, Path]] = []
    for group in args.groups:
        name = group.into.strip()
        if not _valid_name(name):
            problems.append(f"'{group.into}' is not a valid folder name (just a name, no slashes)")
            continue
        target = folder / name
        if target.exists() and not target.is_dir():
            problems.append(f"'{name}' already exists as a file")
            continue
        for file_name in group.files:
            file_name = Path(file_name).name  # tolerate full paths to files in this folder
            source = folder / file_name
            if file_name.lower() in seen:
                problems.append(f"'{file_name}' is listed twice")
            elif not source.exists():
                problems.append(f"'{file_name}' is not in {folder}")
            elif source == target:
                problems.append(f"'{file_name}' cannot go into itself")
            elif (target / file_name).exists():
                problems.append(f"'{file_name}' already exists in {name}")
            else:
                movable(source)
                plan.append((source, target))
            seen.add(file_name.lower())
    if problems:
        return ToolResult(False, "Nothing was moved: " + "; ".join(problems[:6]) + ". Check with list_folder and try again.")
    summary: dict[str, list[str]] = {}
    for source, target in plan:
        target.mkdir(exist_ok=True)
        shutil.move(str(source), str(target / source.name))
        summary.setdefault(target.name, []).append(source.name)
    return ToolResult(True, json.dumps({"folder": str(folder), "sorted": summary}))


def _describe_sort(args: SortArgs) -> str:
    count = sum(len(g.files) for g in args.groups)
    groups = ", ".join(f"{g.into} ({len(g.files)})" for g in args.groups)
    return f"Sort {count} {'file' if count == 1 else 'files'} in {Path(args.folder).name or args.folder} into {groups}"


def _rename(args: RenameArgs) -> ToolResult:
    path = inside_home(args.path)
    movable(path)
    name = args.new_name.strip()
    if not _valid_name(name):
        return ToolResult(False, f"'{args.new_name}' is not a valid name. Give just the new name, without folders.")
    target = path.with_name(name)
    if target.exists():
        return ToolResult(False, f"'{name}' already exists in {path.parent}. Nothing was changed.")
    path.rename(target)
    return ToolResult(True, json.dumps({"renamed": str(path), "to": str(target)}))


def _delete(args: DeleteArgs) -> ToolResult:
    paths = [inside_home(p) for p in args.paths]
    for path in paths:
        movable(path)
    _recycle(paths)
    return ToolResult(True, json.dumps({"recycled": [p.name for p in paths], "note": "They can be restored from the Recycle Bin."}))


def _guarded(function):
    """Run a file operation off the event loop, turning refusals into honest results."""

    async def handler(args) -> ToolResult:
        try:
            return await asyncio.to_thread(function, args)
        except Refused as exc:
            return ToolResult(False, f"Refused: {exc}")
        except OSError as exc:
            return ToolResult(False, f"Windows reported an error: {exc}")

    return handler


list_folder = Tool(
    name="list_folder",
    description="List the files and folders inside a folder, newest details included. Use to see what is there before moving or deleting.",
    args_model=ListArgs,
    handler=_guarded(_list),
    describe=lambda args: f"Look in {args.folder}",
    read_only=True,
)

create_folder = Tool(
    name="create_folder",
    description="Create a new folder inside the user's home folder.",
    args_model=CreateFolderArgs,
    handler=_guarded(_create_folder),
    describe=lambda args: f"Create folder {args.path}",
)

sort_files = Tool(
    name="sort_files",
    description=(
        "Sort the files of one folder into subfolders inside it (made as needed), all in one go, "
        "e.g. by type: Images, Documents, Archives. Never overwrites. The user confirms first."
    ),
    args_model=SortArgs,
    handler=_guarded(_sort),
    describe=_describe_sort,
    risk=Risk.MEDIUM,
    requires_confirmation=True,
)

move_paths = Tool(
    name="move_paths",
    description="Move files or folders into another folder, which is created if needed. Never overwrites. The user confirms first.",
    args_model=MoveArgs,
    handler=_guarded(_move),
    describe=lambda args: f"Move {_describe_items(args.paths)} to {args.destination}",
    risk=Risk.MEDIUM,
    requires_confirmation=True,
)

rename_path = Tool(
    name="rename_path",
    description="Rename a file or folder. The user confirms first.",
    args_model=RenameArgs,
    handler=_guarded(_rename),
    describe=lambda args: f"Rename {Path(args.path).name} to {args.new_name}",
    risk=Risk.MEDIUM,
    requires_confirmation=True,
)

delete_paths = Tool(
    name="delete_paths",
    description="Send files or folders to the Recycle Bin (they can be restored). The user confirms first.",
    args_model=DeleteArgs,
    handler=_guarded(_delete),
    describe=lambda args: f"Send {_describe_items(args.paths)} to the Recycle Bin",
    risk=Risk.HIGH,
    requires_confirmation=True,
)

FILE_TOOLS = (list_folder, create_folder, sort_files, move_paths, rename_path, delete_paths)
