"""Git LFS file locking.

Locking is the whole point of Kiln, and it is the one thing here that always
needs the server. Every function in this module can fail because the network is
down, and callers are expected to handle that rather than assume success.

The `--verify` form is preferred because the server itself tells us which locks
are ours, so we never have to guess by comparing user names.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from kiln.errors import GitCommandError, LockRefusedError
from kiln.git.runner import GitRunner

log = logging.getLogger(__name__)

# Locking always talks to the server, so it gets a shorter leash than the
# default: an artist waiting to open a file should be told quickly.
LOCK_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class LockInfo:
    path: str
    owner: str
    locked_at: datetime | None
    is_mine: bool


def list_locks(runner: GitRunner) -> list[LockInfo]:
    """Fetch all locks from the server.

    Raises GitCommandError if the server cannot be reached; the caller decides
    whether that means "offline" or "broken".
    """
    try:
        result = runner.run_lfs(
            "locks", "--verify", "--json", timeout=LOCK_TIMEOUT_SECONDS
        )
        return _parse_verified(result.stdout)
    except GitCommandError:
        # --verify is not supported everywhere. Fall back to the plain listing
        # and work out ownership from the local user name.
        log.debug("locks --verify failed, falling back to plain listing")
        result = runner.run_lfs("locks", "--json", timeout=LOCK_TIMEOUT_SECONDS)
        return _parse_plain(result.stdout, local_user_name(runner))


def lock_file(runner: GitRunner, repo_relative_path: str) -> None:
    """Take the lock on one file.

    Raises LockRefusedError if somebody else holds it.
    """
    try:
        runner.run_lfs("lock", "--", repo_relative_path, timeout=LOCK_TIMEOUT_SECONDS)
    except GitCommandError as exc:
        holder = _holder_from_error(exc.stderr)
        if holder or "already locked" in exc.stderr.lower():
            raise LockRefusedError(repo_relative_path, holder) from exc
        raise


def unlock_file(runner: GitRunner, repo_relative_path: str) -> None:
    """Release our own lock on one file. Never forces."""
    runner.run_lfs("unlock", "--", repo_relative_path, timeout=LOCK_TIMEOUT_SECONDS)


def local_user_name(runner: GitRunner) -> str:
    """The configured git user name, used only by the fallback path above."""
    result = runner.run("config", "user.name", check=False)
    return result.stdout.strip()


# -- parsing ---------------------------------------------------------------


def _parse_verified(raw: str) -> list[LockInfo]:
    """Parse the {"ours": [...], "theirs": [...]} shape of `locks --verify`."""
    payload = json.loads(raw or "{}")
    locks: list[LockInfo] = []
    for key, is_mine in (("ours", True), ("theirs", False)):
        for entry in payload.get(key) or []:
            locks.append(_lock_from_entry(entry, is_mine))
    return locks


def _parse_plain(raw: str, local_user: str) -> list[LockInfo]:
    """Parse the flat list shape of `locks --json`."""
    payload = json.loads(raw or "[]")
    entries = payload if isinstance(payload, list) else payload.get("locks") or []
    return [
        _lock_from_entry(entry, is_mine=_owner_name(entry) == local_user)
        for entry in entries
    ]


def _lock_from_entry(entry: dict, is_mine: bool) -> LockInfo:
    return LockInfo(
        path=entry.get("path", ""),
        owner=_owner_name(entry),
        locked_at=_parse_timestamp(entry.get("locked_at")),
        is_mine=is_mine,
    )


def _owner_name(entry: dict) -> str:
    owner = entry.get("owner")
    if isinstance(owner, dict):
        return owner.get("name", "")
    return str(owner or "")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # git-lfs emits RFC 3339; Python 3.11 handles the trailing Z.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _holder_from_error(stderr: str) -> str | None:
    """Pull the lock holder out of git-lfs's refusal message, if it is there.

    Best effort only: the message wording is not a stable interface, so a miss
    just means the dialog says "another user" instead of a name.
    """
    marker = "locked by "
    lowered = stderr.lower()
    if marker not in lowered:
        return None
    start = lowered.index(marker) + len(marker)
    holder = stderr[start:].strip().splitlines()[0].strip()
    return holder.rstrip(".") or None
