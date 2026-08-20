"""Working tree operations: staging, committing, restoring, materialising LFS.

Nothing here decides policy. In particular `restore_file` really does throw the
artist's changes away, so callers must take a backup first (see core.trash).
"""

from __future__ import annotations

from pathlib import Path

from kiln.git.runner import GitRunner

LFS_CHECKOUT_TIMEOUT_SECONDS = 600.0


def stage_file(runner: GitRunner, repo_relative_path: str) -> None:
    """Stage exactly one path. Never `git add -A`, never `git add .`."""
    runner.run("add", "--", repo_relative_path)


def stage_deleted_path(runner: GitRunner, repo_relative_path: str) -> None:
    """Stage deletion or other tracked-index changes for one path."""
    runner.run("add", "-u", "--", repo_relative_path)


def unstage_file(runner: GitRunner, repo_relative_path: str) -> None:
    runner.run("restore", "--staged", "--", repo_relative_path)


def commit(runner: GitRunner, message: str) -> str:
    """Commit the staged index.

    The message goes in on stdin, which sidesteps every quoting and encoding
    difference between Windows and Linux shells.
    """
    result = runner.run("commit", "-F", "-", stdin=message)
    return result.stdout.strip()


def restore_file(runner: GitRunner, repo_relative_path: str) -> None:
    """Throw away working tree changes to one file. Destructive."""
    runner.run("restore", "--staged", "--worktree", "--", repo_relative_path)


def remove_untracked_file(path: Path) -> None:
    """Delete an untracked file. Destructive; back it up first."""
    path.unlink(missing_ok=True)


def materialise(runner: GitRunner, repo_relative_path: str) -> None:
    """Turn an LFS pointer on disk into the real file.

    Needed before handing anything to an application, and after a
    --ours/--theirs checkout, which can leave a pointer behind.
    """
    # Neither call is fatal on its own: a repository without LFS configured
    # refuses both, and a plain file needs no smudging. The caller verifies the
    # result is real content, which is the guarantee that actually matters.
    runner.run_lfs(
        "pull",
        f"--include={repo_relative_path}",
        check=False,
        timeout=LFS_CHECKOUT_TIMEOUT_SECONDS,
    )
    runner.run_lfs(
        "checkout",
        "--",
        repo_relative_path,
        check=False,
        timeout=LFS_CHECKOUT_TIMEOUT_SECONDS,
    )


def tracked_files(runner: GitRunner) -> list[str]:
    """Every file git knows about, as repo-relative POSIX paths."""
    result = runner.run("ls-files", "-z")
    return [path for path in result.stdout.split("\0") if path]


def is_binary_asset(runner: GitRunner, repo_relative_path: str) -> bool:
    """Is this a binary asset, where keeping one whole side is meaningful?

    Answered from .gitattributes: either the LFS filter applies, or the file is
    marked lockable. Both mean "an artist's binary", as opposed to something a
    person merges by hand. Text files are kept out of the conflict pick flow on
    purpose (spec 9.7).
    """
    result = runner.run(
        "check-attr", "filter", "lockable", "--", repo_relative_path, check=False
    )
    if not result.ok:
        return False

    attributes = parse_check_attr(result.stdout)
    return attributes.get("filter") == "lfs" or attributes.get("lockable") == "set"


def parse_check_attr(raw: str) -> dict[str, str]:
    """Parse `git check-attr` output: '<path>: <attribute>: <value>' per line."""
    attributes: dict[str, str] = {}
    for line in raw.splitlines():
        parts = line.rsplit(": ", 2)
        if len(parts) == 3:
            attributes[parts[1]] = parts[2]
    return attributes


def file_size_on_disk(repo_root: Path, repo_relative_path: str) -> int | None:
    full_path = repo_root / repo_relative_path
    try:
        return full_path.stat().st_size
    except OSError:
        return None
