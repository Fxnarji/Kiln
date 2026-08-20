"""Backups taken before Kiln destroys anything.

Two callers, both mandatory:
  * discarding changes to a file (spec 10.4)
  * resolving a conflict, where one whole side of the file loses (spec 9.7)

Backups live inside the working tree under .kiln/, which is gitignored. Nothing
is ever pruned automatically. At this repository size the disk cost does not
matter, and an artist finding their work still there a week later does.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

KILN_DIRECTORY = ".kiln"
TRASH_DIRECTORY = "trash"
CONFLICTS_DIRECTORY = "conflicts"


@dataclass(frozen=True)
class DeletedItem:
    repo_relative_path: str
    backup_path: Path
    was_tracked: bool


def kiln_directory(repo_root: Path) -> Path:
    return repo_root / KILN_DIRECTORY


def ensure_ignored(repo_root: Path) -> None:
    """Make sure .kiln/ never shows up as an untracked file.

    Written to .git/info/exclude rather than .gitignore, because that file is
    local to the clone and Kiln must not modify anything the team shares.
    """
    exclude_file = repo_root / ".git" / "info" / "exclude"
    if not exclude_file.parent.is_dir():
        return
    entry = f"/{KILN_DIRECTORY}/"
    existing = exclude_file.read_text(encoding="utf-8") if exclude_file.is_file() else ""
    if entry in existing:
        return
    separator = "" if existing.endswith("\n") or not existing else "\n"
    exclude_file.write_text(
        f"{existing}{separator}{entry}\n", encoding="utf-8"
    )


def new_stamp() -> str:
    """A folder name for one backup event.

    Pass the same stamp to several backup_file calls to group them — both sides
    of one conflict belong in the same folder, not two a second apart.
    """
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def backup_file(
    repo_root: Path,
    repo_relative_path: str,
    category: str = TRASH_DIRECTORY,
    label: str = "",
    stamp: str | None = None,
) -> Path | None:
    """Copy a file into .kiln/<category>/<timestamp>/ before it is destroyed.

    Returns the backup location, or None if there was nothing on disk to save.
    The copy preserves the original folder structure so a restore is an obvious
    drag-and-drop rather than a puzzle.
    """
    source = repo_root / repo_relative_path
    if not source.is_file():
        return None

    destination = kiln_directory(repo_root) / category / (stamp or new_stamp())
    if label:
        destination = destination / label
    destination = destination / repo_relative_path

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def backup_both_sides(repo_root: Path, repo_relative_path: str) -> Path | None:
    """Save the current working tree copy before a conflict is resolved.

    Only "mine" can be copied from disk — the server's version is not a file
    yet. It is recovered from git after the checkout, by resolve_conflict in
    core.conflicts, which calls back in here with label="theirs".
    """
    return backup_file(
        repo_root, repo_relative_path, category=CONFLICTS_DIRECTORY, label="mine"
    )
