from __future__ import annotations

import json
from pathlib import Path

import pytest

from nova.tools import file_ops
from nova.tools.file_ops import (
    CreateFolderArgs,
    DeleteArgs,
    ListArgs,
    MoveArgs,
    RenameArgs,
    SortArgs,
    create_folder,
    delete_paths,
    list_folder,
    move_paths,
    rename_path,
    sort_files,
)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A fake home folder, so no test touches real files or the real Recycle Bin."""
    root = tmp_path / "home"
    for name in ("Documents", "Downloads", "Desktop"):
        (root / name).mkdir(parents=True)
    for name in ("invoice-1.pdf", "invoice-2.pdf", "photo.jpg"):
        (root / "Downloads" / name).write_text("x")
    monkeypatch.setattr(Path, "home", lambda: root)
    return root


@pytest.fixture
def recycled(monkeypatch) -> list[list[Path]]:
    calls: list[list[Path]] = []
    monkeypatch.setattr(file_ops, "_recycle", calls.append)
    return calls


def body(result):
    return json.loads(result.content)


async def test_list_folder_by_name_and_extension(home):
    listing = body(await list_folder.handler(ListArgs(folder="downloads", extension="pdf")))
    assert [Path(e["path"]).name for e in listing["entries"]] == ["invoice-1.pdf", "invoice-2.pdf"]


async def test_create_folder_and_move_many_files_in_one_call(home):
    target = home / "Documents" / "Invoices"
    assert (await create_folder.handler(CreateFolderArgs(path=str(target)))).ok
    pdfs = [str(home / "Downloads" / n) for n in ("invoice-1.pdf", "invoice-2.pdf")]

    result = await move_paths.handler(MoveArgs(paths=pdfs, destination=str(target)))

    assert result.ok, result.content
    assert sorted(p.name for p in target.iterdir()) == ["invoice-1.pdf", "invoice-2.pdf"]
    assert move_paths.describe(MoveArgs(paths=pdfs, destination=str(target))).startswith(
        "Move invoice-1.pdf and invoice-2.pdf to"
    )


async def test_move_creates_its_destination(home):
    target = home / "Documents" / "Invoices"
    result = await move_paths.handler(MoveArgs(paths=[str(home / "Downloads" / "invoice-1.pdf")], destination=str(target)))
    assert result.ok and body(result)["created_folder"] is True
    assert (target / "invoice-1.pdf").exists()


def sort_args(home, *groups) -> SortArgs:
    return SortArgs(folder=str(home / "Downloads"), groups=[{"into": into, "files": files} for into, files in groups])


async def test_sort_files_in_one_call(home):
    args = sort_args(home, ("Documents", ["invoice-1.pdf", "invoice-2.pdf"]), ("Images", ["photo.jpg"]))
    assert sort_files.describe(args) == "Sort 3 files in Downloads into Documents (2), Images (1)"

    result = await sort_files.handler(args)

    assert result.ok, result.content
    assert body(result)["sorted"] == {"Documents": ["invoice-1.pdf", "invoice-2.pdf"], "Images": ["photo.jpg"]}
    downloads = home / "Downloads"
    assert sorted(p.name for p in downloads.iterdir()) == ["Documents", "Images"]
    assert (downloads / "Images" / "photo.jpg").exists()


@pytest.mark.parametrize(
    ("groups", "problem"),
    [
        ((("Images", ["photo.jpg", "missing.png"]),), "'missing.png' is not in"),
        ((("Images", ["photo.jpg"]), ("Pictures", ["photo.jpg"])), "listed twice"),
        ((("../Escape", ["photo.jpg"]),), "not a valid folder name"),
        ((("CON", ["photo.jpg"]),), "not a valid folder name"),
        ((("Images.", ["photo.jpg"]),), "not a valid folder name"),
    ],
)
async def test_a_bad_sort_plan_moves_nothing(home, groups, problem):
    result = await sort_files.handler(sort_args(home, *groups))
    assert not result.ok and problem in result.content
    assert sorted(p.name for p in (home / "Downloads").iterdir()) == ["invoice-1.pdf", "invoice-2.pdf", "photo.jpg"]


async def test_sort_never_overwrites(home):
    (home / "Downloads" / "Images").mkdir()
    (home / "Downloads" / "Images" / "photo.jpg").write_text("older")
    result = await sort_files.handler(sort_args(home, ("Images", ["photo.jpg"])))
    assert not result.ok and "already exists in Images" in result.content
    assert (home / "Downloads" / "Images" / "photo.jpg").read_text() == "older"


async def test_sort_refuses_folders_outside_home(home):
    result = await sort_files.handler(SortArgs(folder="C:/Windows", groups=[{"into": "x", "files": ["notepad.exe"]}]))
    assert not result.ok and "Refused" in result.content


async def test_move_never_overwrites(home):
    (home / "Documents" / "photo.jpg").write_text("older")
    result = await move_paths.handler(
        MoveArgs(paths=[str(home / "Downloads" / "photo.jpg")], destination=str(home / "Documents"))
    )
    assert not result.ok and "already exist" in result.content
    assert (home / "Documents" / "photo.jpg").read_text() == "older"
    assert (home / "Downloads" / "photo.jpg").exists()


@pytest.mark.parametrize(
    "path",
    [
        "{home}",  # the whole home folder
        "{home}/Documents",  # a standard folder
        "C:/Windows/System32/notepad.exe",  # outside home
        "{home}/AppData/Local/thing",  # app data
        "relative/path.txt",  # not a full path
    ],
)
async def test_dangerous_targets_are_refused(home, recycled, path):
    (home / "AppData" / "Local" / "thing").mkdir(parents=True)
    result = await delete_paths.handler(DeleteArgs(paths=[path.format(home=home)]))
    assert not result.ok and "Refused" in result.content
    assert recycled == []


async def test_delete_goes_to_the_recycle_bin(home, recycled):
    photo = home / "Downloads" / "photo.jpg"
    result = await delete_paths.handler(DeleteArgs(paths=[str(photo)]))
    assert result.ok
    assert recycled == [[photo.resolve()]]
    assert delete_paths.describe(DeleteArgs(paths=[str(photo)])) == "Send photo.jpg to the Recycle Bin"
    many = [f"{home}/Downloads/f{i}.txt" for i in range(12)]
    assert delete_paths.describe(DeleteArgs(paths=many)) == "Send 12 items (f0.txt, f1.txt, ...) to the Recycle Bin"


async def test_rename(home):
    photo = home / "Downloads" / "photo.jpg"
    assert not (await rename_path.handler(RenameArgs(path=str(photo), new_name="a/b.jpg"))).ok
    assert not (await rename_path.handler(RenameArgs(path=str(photo), new_name="invoice-1.pdf"))).ok
    assert (await rename_path.handler(RenameArgs(path=str(photo), new_name="beach.jpg"))).ok
    assert (home / "Downloads" / "beach.jpg").exists()


async def test_cannot_move_a_folder_into_itself(home):
    folder = home / "Documents" / "Projects"
    (folder / "inner").mkdir(parents=True)
    result = await move_paths.handler(MoveArgs(paths=[str(folder)], destination=str(folder / "inner")))
    assert not result.ok and "into itself" in result.content
