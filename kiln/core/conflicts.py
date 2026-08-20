"""Whole-file conflict resolution (spec 9.7).

The rule that matters: **both sides are on disk as real files before either is
overwritten.** "Keep server version" destroys an artist's work otherwise, and
this screen gets used by someone who is already having a bad afternoon.

Getting both sides safely is done the blunt way — check out the server's copy,
copy it aside, then check out whichever side won. It costs one extra checkout
of a file under a gigabyte, and in exchange there is no path through this code
where a version exists only inside git's object store.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from kiln.core import trash
from kiln.core.pointers import is_pointer_file
from kiln.errors import KilnError
from kiln.git import merge, workdir
from kiln.git.runner import GitRunner

log = logging.getLogger(__name__)

SIDE_MINE = merge.SIDE_MINE
SIDE_SERVER = merge.SIDE_SERVER


@dataclass(frozen=True)
class ConflictedFile:
    path: str
    resolvable: bool  # False for text files: a whole-file pick would be wrong
    reason: str = ""  # why it is not resolvable, in words for an artist

    @property
    def name(self) -> str:
        return Path(self.path).name


@dataclass(frozen=True)
class Resolution:
    path: str
    side: str
    backup_mine: Path | None
    backup_server: Path | None


def list_conflicts(runner: GitRunner) -> list[ConflictedFile]:
    """Every conflicted file, flagged with whether Kiln will touch it."""
    conflicts: list[ConflictedFile] = []
    for path in merge.conflicted_paths(runner):
        if workdir.is_binary_asset(runner, path):
            conflicts.append(ConflictedFile(path=path, resolvable=True))
        else:
            conflicts.append(
                ConflictedFile(
                    path=path,
                    resolvable=False,
                    reason=(
                        "This is a text file. Picking one whole side would "
                        "silently drop someone's edits — resolve it in a text "
                        "editor or on the command line."
                    ),
                )
            )
    return conflicts


def resolve_conflict(
    runner: GitRunner, repo_root: Path, repo_relative_path: str, side: str
) -> Resolution:
    """Keep one whole side of a conflicted file, backing up both first."""
    if side not in (SIDE_MINE, SIDE_SERVER):
        raise ValueError(f"unknown side: {side!r}")

    # Both backups share one folder, so the two versions sit side by side.
    stamp = trash.new_stamp()

    # 1. My version is already in the working tree; save it before anything
    #    overwrites it.
    backup_mine = trash.backup_file(
        repo_root,
        repo_relative_path,
        category=trash.CONFLICTS_DIRECTORY,
        label="mine",
        stamp=stamp,
    )

    # 2. Bring the server's version into the working tree and save that too.
    #    Nothing is staged yet: staging here would collapse the conflict stages
    #    and make step 3 silently keep the wrong side.
    merge.checkout_side(runner, repo_relative_path, merge.SIDE_SERVER)
    backup_server = trash.backup_file(
        repo_root,
        repo_relative_path,
        category=trash.CONFLICTS_DIRECTORY,
        label="server",
        stamp=stamp,
    )

    # 3. Put the winning side in place. If the server won it is already there,
    #    so only the other case needs a second checkout.
    if side == SIDE_MINE:
        merge.checkout_side(runner, repo_relative_path, merge.SIDE_MINE)

    _verify_real_content(repo_root / repo_relative_path)

    # 4. Only now is it safe to tell git the file is dealt with.
    merge.mark_resolved(runner, repo_relative_path)

    log.info("resolved %s by keeping %s", repo_relative_path, side)
    return Resolution(
        path=repo_relative_path,
        side=side,
        backup_mine=backup_mine,
        backup_server=backup_server,
    )


def _verify_real_content(full_path: Path) -> None:
    """A resolved file must be real content, never a leftover LFS pointer."""
    if is_pointer_file(full_path):
        raise KilnError(
            f"{full_path.name} is still an LFS pointer after resolving. "
            "The file content could not be downloaded — check your connection "
            "and try again before committing this merge."
        )


def complete(runner: GitRunner, resolutions: dict[str, str], merged_ref: str) -> str:
    """Commit the finished merge, recording which side won for each file."""
    return merge.complete_merge(
        runner, merge.merge_message(resolutions, merged_ref)
    )


def abandon(runner: GitRunner) -> None:
    """Undo the whole merge. The artist's working tree returns to normal."""
    merge.abort_merge(runner)
