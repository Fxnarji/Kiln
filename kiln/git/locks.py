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

# How `git lfs env` labels the resolved LFS API endpoint.
ENDPOINT_PREFIX = "Endpoint="

# The LFS locking API is served over HTTP and nothing else.
HTTP_SCHEMES = ("http://", "https://")


@dataclass(frozen=True)
class LockInfo:
    path: str
    owner: str
    locked_at: datetime | None
    is_mine: bool


def locking_endpoint(runner: GitRunner) -> str:
    """The LFS API endpoint git resolves for this remote.

    `git lfs env` reports it as `Endpoint=<url> (auth=...)`.
    """
    result = runner.run_lfs("env", check=False)
    for line in result.stdout.splitlines():
        if line.startswith(ENDPOINT_PREFIX):
            return line[len(ENDPOINT_PREFIX) :].split(" ", 1)[0].strip()
    return ""


def endpoint_supports_locking(endpoint: str) -> bool:
    """Could this endpoint serve the LFS locking API?

    The API is HTTP only, so anything else — a local path, a file:// URL, a
    bare ssh remote — can never report locks. Split out from locking_supported
    so the rule can be tested without a repository.
    """
    return endpoint.startswith(HTTP_SCHEMES)


def locking_supported(runner: GitRunner) -> bool:
    """Can this remote support file locking at all?

    This check exists because git-lfs does not fail when it cannot. Asked for
    locks against a file:// remote it prints a hint to stderr, **exits 0**, and
    returns an empty list. Trusting the exit code would therefore render
    "nobody holds this file" when the truth is "there is no way to know", which
    is the single most dangerous thing this application can display.
    """
    return endpoint_supports_locking(locking_endpoint(runner))


def list_locks(runner: GitRunner) -> list[LockInfo]:
    """Fetch all locks from the server.

    Callers must check locking_supported first; this reports what the server
    said, and cannot tell on its own whether a server was involved.

    Only the --verify form is used. It is what makes the server itself say
    which locks are ours, so ownership never has to be guessed by comparing a
    local git user.name against a server account name — two things that are
    routinely different, and getting it wrong would show another artist's lock
    as your own.
    """
    result = runner.run_lfs(
        "locks", "--verify", "--json", timeout=LOCK_TIMEOUT_SECONDS
    )
    return _parse_verified(result.stdout)


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


# -- parsing ---------------------------------------------------------------


def _parse_verified(raw: str) -> list[LockInfo]:
    """Parse the {"ours": [...], "theirs": [...]} shape of `locks --verify`."""
    payload = json.loads(raw or "{}")
    locks: list[LockInfo] = []
    for key, is_mine in (("ours", True), ("theirs", False)):
        for entry in payload.get(key) or []:
            locks.append(_lock_from_entry(entry, is_mine))
    return locks


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
