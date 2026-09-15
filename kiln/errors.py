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


class AuthenticationError(GitCommandError):
    """The server refused us, or no credentials were available for it.

    Subclasses GitCommandError on purpose: everything that already treats a
    failed git command as "this did not work" keeps behaving correctly, while
    the UI can recognise this case and say something an artist can act on.

    Kiln never handles passwords itself (spec 7.4). The fix is always to
    authenticate once outside Kiln, after which the system credential helper
    remembers it.
    """

    # Wording varies between git, git-lfs, the credential helper, and ssh, so
    # match on several. Getting this wrong only costs a less helpful message.
    HTTP_SIGNATURES = (
        "could not read username",
        "could not read password",
        "authentication failed",
        "terminal prompts disabled",
        "user interactivity has been disabled",
        "credentials for",
        "invalid username or password",
        "http 401",
        "403 forbidden",
    )

    SSH_SIGNATURES = (
        "permission denied (publickey",
        "host key verification failed",
        "no supported authentication methods",
        "could not open a connection to your authentication agent",
    )

    SIGNATURES = HTTP_SIGNATURES + SSH_SIGNATURES

    @classmethod
    def looks_like_auth_failure(cls, output: str) -> bool:
        lowered = output.lower()
        return any(signature in lowered for signature in cls.SIGNATURES)

    def __init__(self, argv: list[str], returncode: int, stdout: str, stderr: str):
        super().__init__(argv, returncode, stdout, stderr)
        output = f"{stderr}\n{stdout}"
        self.host = _host_from(output)
        self.over_ssh = any(
            signature in output.lower() for signature in self.SSH_SIGNATURES
        )

    def advice(self) -> str:
        """What to tell the artist. Written for someone who does not know git."""
        where = f" to {self.host}" if self.host else ""
        if self.over_ssh:
            return (
                f"Kiln could not connect{where} with your SSH key.\n\n"
                "Kiln never handles keys itself. Fix it once outside Kiln:\n\n"
                "  1. Open the project folder in a terminal\n"
                "  2. Run:  git fetch\n"
                "  3. Accept the host fingerprint if asked, and make sure your\n"
                "     key is loaded (ssh-add -l should list it)\n\n"
                "Then try again in Kiln."
            )
        return (
            f"Kiln could not sign in{where}.\n\n"
            "Kiln never handles your password itself. Sign in once outside "
            "Kiln and Windows will remember it:\n\n"
            "  1. Open the project folder in a terminal\n"
            "  2. Run:  git fetch\n"
            "  3. Complete the login when prompted\n\n"
            "Then try again in Kiln."
        )


def _host_from(output: str) -> str:
    """Pull a hostname out of git's message, for a more specific error.

    Handles both an https URL and ssh's `user@host:` prefix.
    """
    import re

    url_match = re.search(r"https?://([^/\s'\"]+)", output)
    if url_match:
        return url_match.group(1)

    ssh_match = re.search(r"(?:^|\s)[\w.-]+@([\w.-]+?)(?::|\s|$)", output)
    return ssh_match.group(1) if ssh_match else ""


class LockRefusedError(KilnError):
    """Someone else holds the lock on this file."""

    def __init__(self, path: str, holder: str | None = None):
        self.path = path
        self.holder = holder
        who = holder or "another user"
        super().__init__(f"{path} is locked by {who}")


class LocksUnavailableError(KilnError):
    """Lock state cannot be determined, so claiming a file is not safe.

    Raised instead of letting an artist edit a file when Kiln cannot tell
    whether somebody else already holds it (spec 10.2). Refusing is the safe
    answer: the cost is waiting, the cost of guessing is two people editing the
    same .blend.
    """

    def __init__(self, path: str = ""):
        self.path = path
        super().__init__(
            "Kiln cannot reach the lock server, so it cannot tell whether "
            "anyone else is working on this file. Opening it for editing is "
            "disabled until the connection is back. You can still open it "
            "read-only."
        )


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
