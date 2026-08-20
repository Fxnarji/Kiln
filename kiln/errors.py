"""Exception types for Kiln.

Every failure an artist can hit should surface as one of these, carrying enough
detail for the diagnostics panel to show what actually ran.
"""

from __future__ import annotations


class KilnError(Exception):
    """Base class for every error Kiln raises deliberately."""


class GitNotFoundError(KilnError):
    """The `git` or `git-lfs` binary is missing, or too old to rely on."""


class NotARepositoryError(KilnError):
    """The given path is not inside a git working tree."""


class GitCommandError(KilnError):
    """A git invocation exited non-zero.

    Carries the full invocation so the UI can offer "Copy diagnostics" without
    needing to reconstruct anything.
    """

    def __init__(self, argv: list[str], returncode: int, stdout: str, stderr: str):
        self.argv = argv
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        message = stderr.strip() or stdout.strip() or f"exit code {returncode}"
        super().__init__(f"{' '.join(argv)}\n{message}")

    def diagnostics(self) -> str:
        """A pasteable block for bug reports."""
        return (
            f"command : {' '.join(self.argv)}\n"
            f"exit    : {self.returncode}\n"
            f"stdout  : {self.stdout.strip()}\n"
            f"stderr  : {self.stderr.strip()}"
        )


class LockRefusedError(KilnError):
    """Someone else holds the lock on this file."""

    def __init__(self, path: str, holder: str | None = None):
        self.path = path
        self.holder = holder
        who = holder or "another user"
        super().__init__(f"{path} is locked by {who}")


class NotDownloadedError(KilnError):
    """The file on disk is still an LFS pointer, so there is nothing to open."""

    def __init__(self, path: str):
        self.path = path
        super().__init__(f"{path} has not been downloaded yet")


class RepositoryBusyError(KilnError):
    """The repository is in a state Kiln does not understand (see spec 10.5).

    Raised instead of attempting any write operation. `condition` is written for
    an artist to read, not a programmer.
    """

    def __init__(self, condition: str):
        self.condition = condition
        super().__init__(condition)
