"""The data the rest of Kiln reasons about.

Everything is frozen. A refresh builds a whole new RepoState rather than
mutating the old one, so the UI can never drift out of step with the disk.
At this repository size a full rebuild costs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

# How old lock information may get before the UI calls it stale (spec 10.2).
LOCK_STALE_AFTER = timedelta(minutes=5)

FileStatus = str  # one of: clean, modified, new, deleted, conflicted


@dataclass(frozen=True)
class Lock:
    path: str
    owner: str
    locked_at: datetime | None
    is_mine: bool

    def age_description(self, now: datetime | None = None) -> str:
        if self.locked_at is None:
            return "unknown"
        reference = now or datetime.now(timezone.utc)
        return describe_age(reference - self.locked_at)


@dataclass(frozen=True)
class FileEntry:
    path: str  # repo-relative, POSIX separators
    status: FileStatus = "clean"
    downloaded: bool = True
    writable: bool = True
    size: int | None = None
    lock: Lock | None = None

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def parent(self) -> str:
        parent = str(PurePosixPath(self.path).parent)
        return "" if parent == "." else parent

    @property
    def is_changed(self) -> bool:
        return self.status != "clean"

    @property
    def is_conflicted(self) -> bool:
        return self.status == "conflicted"

    @property
    def locked_by_someone_else(self) -> bool:
        return self.lock is not None and not self.lock.is_mine

    @property
    def locked_by_me(self) -> bool:
        return self.lock is not None and self.lock.is_mine

    def lock_description(self) -> str:
        if self.lock is None:
            return ""
        return "You" if self.lock.is_mine else self.lock.owner


@dataclass(frozen=True)
class RepoState:
    """An immutable snapshot of everything the UI displays."""

    root: str
    branch: str = ""
    head: str = ""
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    online: bool = True
    locks_available: bool = True
    locks_fetched_at: datetime | None = None
    blocked_reason: str = ""  # non-empty means writes are disabled (spec 10.5)
    merge_in_progress: bool = False
    files: list[FileEntry] = field(default_factory=list)

    # -- derived views ------------------------------------------------------

    @property
    def can_write(self) -> bool:
        return not self.blocked_reason

    @property
    def changed_files(self) -> list[FileEntry]:
        return [entry for entry in self.files if entry.is_changed]

    @property
    def conflicted_files(self) -> list[FileEntry]:
        return [entry for entry in self.files if entry.is_conflicted]

    @property
    def my_locks(self) -> list[FileEntry]:
        return [entry for entry in self.files if entry.locked_by_me]

    @property
    def missing_files(self) -> list[FileEntry]:
        """Files that are still LFS pointers, i.e. never finished downloading."""
        return [entry for entry in self.files if not entry.downloaded]

    def find(self, path: str) -> FileEntry | None:
        for entry in self.files:
            if entry.path == path:
                return entry
        return None

    def locks_are_stale(self, now: datetime | None = None) -> bool:
        if not self.locks_available or self.locks_fetched_at is None:
            return True
        reference = now or datetime.now(timezone.utc)
        return reference - self.locks_fetched_at > LOCK_STALE_AFTER

    def lock_freshness_description(self, now: datetime | None = None) -> str:
        if not self.locks_available:
            return "lock state unavailable"
        if self.locks_fetched_at is None:
            return "locks not loaded"
        reference = now or datetime.now(timezone.utc)
        return f"locks as of {describe_age(reference - self.locks_fetched_at)}"


@dataclass(frozen=True)
class Pin:
    """A saved folder view. Local preference, never stored in the repo."""

    path: str  # repo-relative, "" means the repository root
    depth: int = 2  # 1 = this folder only; DEPTH_UNLIMITED = no limit
    label: str = ""
    color: str = ""

    def display_label(self) -> str:
        return self.label or (self.path or "Repository root")

    def contains(self, file_path: str) -> bool:
        """Is this file inside the pin, within its depth limit?"""
        if self.path and not file_path.startswith(f"{self.path}/"):
            return False
        relative = file_path[len(self.path) + 1 :] if self.path else file_path
        if self.depth >= DEPTH_UNLIMITED:
            return True
        return len(PurePosixPath(relative).parts) <= self.depth


DEPTH_UNLIMITED = 5

DEPTH_DESCRIPTIONS = {
    1: "this folder only",
    2: "one level of subfolders",
    3: "two levels of subfolders",
    4: "three levels of subfolders",
    DEPTH_UNLIMITED: "everything below",
}


def describe_age(delta: timedelta) -> str:
    """Human wording for how long ago something happened."""
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def describe_size(size: int | None) -> str:
    if size is None:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
