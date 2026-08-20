"""Talking to the server: fetch, pull, push, and reachability.

Pull is deliberately fast-forward only. When it fails because the branches have
diverged, the caller offers the conflict flow (spec 9.7) rather than silently
starting a merge.
"""

from __future__ import annotations

from dataclasses import dataclass

from kiln.errors import GitCommandError
from kiln.git.runner import GitRunner

DEFAULT_REMOTE = "origin"
REACHABILITY_TIMEOUT_SECONDS = 10.0
TRANSFER_TIMEOUT_SECONDS = 900.0  # large LFS objects over a slow link


@dataclass(frozen=True)
class PullResult:
    ok: bool
    diverged: bool
    message: str


def remote_reachable(runner: GitRunner, remote: str = DEFAULT_REMOTE) -> bool:
    """Cheap online check, used to drive the offline banner (spec 10.3)."""
    result = runner.run(
        "ls-remote",
        "--heads",
        remote,
        check=False,
        timeout=REACHABILITY_TIMEOUT_SECONDS,
    )
    return result.ok


def fetch(runner: GitRunner, remote: str = DEFAULT_REMOTE) -> None:
    runner.run("fetch", "--prune", remote, timeout=TRANSFER_TIMEOUT_SECONDS)


def pull_fast_forward(runner: GitRunner) -> PullResult:
    """Pull, but only if it is a clean fast-forward.

    Divergence is reported rather than raised, because it is an ordinary thing
    that happens to artists and has a defined next step.
    """
    result = runner.run(
        "pull", "--ff-only", check=False, timeout=TRANSFER_TIMEOUT_SECONDS
    )
    if result.ok:
        return PullResult(ok=True, diverged=False, message=result.stdout.strip())

    combined = f"{result.stdout}\n{result.stderr}".lower()
    diverged = "not possible to fast-forward" in combined or "diverged" in combined
    return PullResult(
        ok=False,
        diverged=diverged,
        message=(result.stderr or result.stdout).strip(),
    )


def push(runner: GitRunner, remote: str = DEFAULT_REMOTE) -> str:
    """Push the current branch. Never forces."""
    result = runner.run("push", remote, "HEAD", timeout=TRANSFER_TIMEOUT_SECONDS)
    return (result.stderr or result.stdout).strip()


def upstream_ref(runner: GitRunner) -> str:
    """The tracking branch, e.g. 'origin/main'. Empty if there is none."""
    result = runner.run(
        "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False
    )
    return result.stdout.strip() if result.ok else ""


def has_upstream(runner: GitRunner) -> bool:
    return bool(upstream_ref(runner))


def default_branch(runner: GitRunner) -> str:
    """Current branch name, or empty when HEAD is detached."""
    try:
        result = runner.run("symbolic-ref", "--short", "HEAD")
    except GitCommandError:
        return ""
    return result.stdout.strip()
