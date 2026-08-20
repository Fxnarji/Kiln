"""Merge state and whole-file conflict resolution (spec 9.7).

Kiln does not merge content. For a conflicted binary it takes one side of the
file wholesale, which is the only resolution that means anything for a .blend.

The safety rule lives in core.conflicts, not here: both sides are backed up
before either is overwritten. This module is the mechanism only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kiln.errors import GitCommandError
from kiln.git.runner import GitRunner

MERGE_TIMEOUT_SECONDS = 600.0

# Which side of the conflict to keep.
SIDE_MINE = "ours"
SIDE_SERVER = "theirs"


@dataclass(frozen=True)
class MergeState:
    in_progress: bool
    conflicted_paths: list[str]


def git_dir(runner: GitRunner) -> Path:
    """Absolute path of the .git directory for this working tree."""
    result = runner.run("rev-parse", "--absolute-git-dir")
    return Path(result.stdout.strip())


def merge_in_progress(runner: GitRunner) -> bool:
    """True between `git merge` failing and the merge being resolved."""
    try:
        return (git_dir(runner) / "MERGE_HEAD").exists()
    except GitCommandError:
        return False


def start_merge(runner: GitRunner, ref: str) -> bool:
    """Begin a merge with the given ref. Returns True if it completed cleanly.

    False means there are conflicts to resolve, which is the expected path into
    the conflict screen — not an error.
    """
    result = runner.run("merge", "--no-edit", ref, check=False, timeout=MERGE_TIMEOUT_SECONDS)
    return result.ok


def abort_merge(runner: GitRunner) -> None:
    """Put everything back the way it was. Always available to the artist."""
    runner.run("merge", "--abort")


def checkout_side(runner: GitRunner, repo_relative_path: str, side: str) -> None:
    """Put one side of a conflicted file into the working tree.

    This deliberately does **not** stage the result. Staging collapses the
    conflict stages in the index, after which `--ours` and `--theirs` stop
    meaning anything and silently check out whatever is already staged. Callers
    that need to look at both sides must therefore stage only at the very end,
    via mark_resolved.

    `git checkout --ours/--theirs` on an LFS file can leave a pointer behind, so
    the content is materialised straight afterwards. A repository without LFS
    configured refuses that, which is harmless — the caller verifies the result
    is real content either way.
    """
    if side not in (SIDE_MINE, SIDE_SERVER):
        raise ValueError(f"unknown side: {side!r}")

    runner.run("checkout", f"--{side}", "--", repo_relative_path)
    runner.run_lfs(
        "checkout", "--", repo_relative_path, check=False, timeout=MERGE_TIMEOUT_SECONDS
    )


def mark_resolved(runner: GitRunner, repo_relative_path: str) -> None:
    """Stage a conflicted file, telling git it has been dealt with.

    Irreversible in the sense that matters here: the other side is no longer
    reachable with --ours/--theirs afterwards.
    """
    runner.run("add", "--", repo_relative_path)


def conflicted_paths(runner: GitRunner) -> list[str]:
    """Files currently in a conflicted state, as repo-relative paths."""
    result = runner.run("diff", "--name-only", "--diff-filter=U", "-z")
    return [path for path in result.stdout.split("\0") if path]


def complete_merge(runner: GitRunner, message: str) -> str:
    """Commit the resolved merge."""
    result = runner.run("commit", "-F", "-", stdin=message)
    return result.stdout.strip()


def merge_message(resolutions: dict[str, str], merged_ref: str) -> str:
    """Generate a commit message recording which side won for each file."""
    lines = [f"Merge {merged_ref} (resolved in Kiln)", ""]
    for path in sorted(resolutions):
        side = "my version" if resolutions[path] == SIDE_MINE else "server version"
        lines.append(f"  {path}: kept {side}")
    return "\n".join(lines) + "\n"
