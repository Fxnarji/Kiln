"""Subprocess wrapper around the `git` and `git-lfs` binaries.

Everything in Kiln that talks to git goes through GitRunner. It is the only
place that calls subprocess, which makes it the only place that needs to know
about timeouts, encoding, or console windows.

Design rules:
  * argv lists only, never a shell string
  * an explicit cwd on every call
  * a timeout on every call, so a hung network op cannot wedge the app
  * every invocation recorded, for the diagnostics panel (spec 12)
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from kiln.errors import AuthenticationError, GitCommandError, GitNotFoundError

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120.0
RECENT_COMMAND_LIMIT = 20

# Bytes that are not valid UTF-8 are preserved rather than replaced, so a path
# survives a round trip even if it was created with an odd encoding.
DECODE_ERRORS = "surrogateescape"


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _creation_flags() -> int:
    """Suppress the console window that would otherwise flash on Windows.

    This is the only OS-specific branch in the git layer, and it exists purely
    for cosmetics in a packaged build. It has no behavioural effect.
    """
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


class GitRunner:
    """Runs git commands inside one repository."""

    def __init__(self, repo_path: Path, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.repo_path = Path(repo_path)
        self.timeout = timeout
        self._recent: deque[CommandResult] = deque(maxlen=RECENT_COMMAND_LIMIT)

    # -- invocation ---------------------------------------------------------

    def run(
        self,
        *args: str,
        stdin: str | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult:
        """Run `git <args>` in the repository."""
        return self._invoke(["git", *args], stdin=stdin, check=check, timeout=timeout)

    def run_lfs(
        self,
        *args: str,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult:
        """Run `git lfs <args>` in the repository."""
        return self._invoke(["git", "lfs", *args], check=check, timeout=timeout)

    def _invoke(
        self,
        argv: list[str],
        stdin: str | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=str(self.repo_path),
                input=stdin.encode("utf-8") if stdin is not None else None,
                capture_output=True,
                timeout=timeout or self.timeout,
                env=self._environment(),
                creationflags=_creation_flags(),
            )
        except FileNotFoundError as exc:
            raise GitNotFoundError(
                "git is not installed, or not on PATH. Install Git and Git LFS, "
                "then restart Kiln."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise GitCommandError(
                argv, -1, "", f"timed out after {exc.timeout:.0f}s"
            ) from exc

        result = CommandResult(
            argv=argv,
            returncode=completed.returncode,
            stdout=completed.stdout.decode("utf-8", DECODE_ERRORS),
            stderr=completed.stderr.decode("utf-8", DECODE_ERRORS),
            duration_seconds=time.monotonic() - started,
        )
        self._record(result)

        if check and not result.ok:
            raise _failure_for(result)
        return result

    @staticmethod
    def _environment() -> dict[str, str]:
        """Environment for every git call.

        GIT_TERMINAL_PROMPT=0 stops git asking for a username on a terminal
        that does not exist — behind a GUI that prompt has nowhere to appear
        and would simply hang.

        Git Credential Manager is deliberately *not* disabled. It shows its own
        window, which is a perfectly good way for a desktop application to
        authenticate, and turning it off leaves an artist with no way to log in
        at all: every remote call fails with "could not read Username" and
        there is nothing they can do about it from inside Kiln.
        """
        import os

        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["LC_ALL"] = "C"
        return env

    # -- diagnostics --------------------------------------------------------

    def _record(self, result: CommandResult) -> None:
        self._recent.append(result)
        log.debug(
            "%s -> %d in %.2fs",
            " ".join(result.argv),
            result.returncode,
            result.duration_seconds,
        )

    def recent_commands(self) -> list[CommandResult]:
        """The last commands run, newest last. Shown in the advanced panel."""
        return list(self._recent)


def _failure_for(result: CommandResult) -> GitCommandError:
    """Classify a failed command.

    Authentication is separated out because it is the one failure an artist can
    actually fix, and because raw git output for it is four lines of internals
    that explain nothing to someone who does not use git.
    """
    combined = f"{result.stderr}\n{result.stdout}"
    error_type = (
        AuthenticationError
        if AuthenticationError.looks_like_auth_failure(combined)
        else GitCommandError
    )
    return error_type(result.argv, result.returncode, result.stdout, result.stderr)


def find_repository_root(start: Path) -> Path | None:
    """Walk up from `start` looking for a git working tree.

    Returns None rather than raising, because "the user picked a folder that
    isn't a repo" is an expected outcome, not an error.
    """
    current = Path(start).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def tool_versions() -> dict[str, str]:
    """Report git and git-lfs versions, for the startup check and diagnostics."""
    versions: dict[str, str] = {}
    for name, argv in (("git", ["git", "--version"]), ("git-lfs", ["git", "lfs", "version"])):
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                timeout=15,
                creationflags=_creation_flags(),
            )
            versions[name] = completed.stdout.decode("utf-8", DECODE_ERRORS).strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            versions[name] = ""
    return versions
