"""The "open a file for editing" sequence (spec 9.4).

This is the most important flow in Kiln, and its ordering is the safety
property: the lock is taken *before* the application launches, and if the lock
cannot be taken the application does not launch at all. An artist can therefore
never end up editing a file they do not hold.

Launching is not done here. This module gets the file ready and hands back the
path; the UI opens it with the desktop handler. That keeps every OS-specific
call out of core.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from kiln.core import permissions
from kiln.core.pointers import is_downloaded
from kiln.errors import LockRefusedError, NotDownloadedError
from kiln.git import locks, workdir
from kiln.git.runner import GitRunner

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedFile:
    """A file that is downloaded, locked by us, and writable."""

    path: Path
    was_downloaded: bool
    lock_taken: bool
    permission_fixed: bool


def prepare_for_editing(
    runner: GitRunner, repo_root: Path, repo_relative_path: str
) -> PreparedFile:
    """Get a file ready to hand to an application.

    Raises LockRefusedError if somebody else holds it, or NotDownloadedError if
    the content cannot be fetched. In both cases nothing has been launched and
    nothing has been changed.
    """
    full_path = repo_root / repo_relative_path

    # 1. Real content on disk, or there is nothing to open.
    was_downloaded = is_downloaded(repo_root, repo_relative_path)
    if not was_downloaded:
        log.info("materialising %s before opening", repo_relative_path)
        workdir.materialise(runner, repo_relative_path)
        if not is_downloaded(repo_root, repo_relative_path):
            raise NotDownloadedError(repo_relative_path)

    # 2 and 3. Ask the server who holds it right now. The cached lock list is a
    # hint for display; this is the answer we act on.
    holder = _current_holder(runner, repo_relative_path)
    if holder is not None:
        raise LockRefusedError(repo_relative_path, holder)

    # 4. Take the lock. If this fails we stop here, before anything launches.
    locks.lock_file(runner, repo_relative_path)

    # 5. `lockable` files are read-only until locked. git-lfs is expected to
    #    have cleared that already; verify, and fix it ourselves if not.
    permission_fixed = False
    if not permissions.is_writable(full_path):
        log.warning("%s still read-only after locking; fixing", repo_relative_path)
        permissions.make_writable(full_path)
        permission_fixed = True

    return PreparedFile(
        path=full_path,
        was_downloaded=was_downloaded,
        lock_taken=True,
        permission_fixed=permission_fixed,
    )


def prepare_read_only(
    runner: GitRunner, repo_root: Path, repo_relative_path: str
) -> Path:
    """Get a file ready to look at without claiming it.

    Downloads if necessary, takes no lock, changes no permissions.
    """
    if not is_downloaded(repo_root, repo_relative_path):
        workdir.materialise(runner, repo_relative_path)
        if not is_downloaded(repo_root, repo_relative_path):
            raise NotDownloadedError(repo_relative_path)
    return repo_root / repo_relative_path


def release(runner: GitRunner, repo_root: Path, repo_relative_path: str) -> None:
    """Give up our lock on a file and restore its read-only state."""
    locks.unlock_file(runner, repo_relative_path)

    full_path = repo_root / repo_relative_path
    if full_path.is_file():
        try:
            permissions.make_read_only(full_path)
        except OSError as exc:
            # Not fatal: the lock is released on the server, which is what
            # actually protects the file for everyone else.
            log.warning("could not restore read-only on %s: %s", full_path, exc)


def _current_holder(runner: GitRunner, repo_relative_path: str) -> str | None:
    """Who holds this file right now, according to the server. None if free."""
    for lock in locks.list_locks(runner):
        if lock.path == repo_relative_path and not lock.is_mine:
            return lock.owner
    return None
