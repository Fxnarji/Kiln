"""Push, pull, and lock availability against a real remote.

The remote here is a bare repository on disk. That is enough to exercise every
git operation, and it is also what makes the lock-availability tests possible:
a file:// remote genuinely cannot serve the LFS locking API, which is the exact
condition Kiln has to report honestly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kiln.core.repository import Repository
from kiln.errors import (
    AuthenticationError,
    GitCommandError,
    LocksUnavailableError,
)
from kiln.git import locks
from tests.conftest import git, write_binary


class TestPush:
    def test_push_sends_a_commit_to_the_server(
        self, repository: Repository, second_clone: Path
    ):
        write_binary(repository.root / "chars/hero/hero.blend", "my new work")
        repository.commit("Hero pass", ["chars/hero/hero.blend"])

        repository.push()

        git(second_clone, "pull")
        assert b"my new work" in (second_clone / "chars/hero/hero.blend").read_bytes()

    def test_push_clears_the_outgoing_count(self, repository: Repository):
        write_binary(repository.root / "props/crate.blend", "edited")
        repository.commit("Crate pass", ["props/crate.blend"])
        assert repository.snapshot(include_locks=False).ahead == 1

        repository.push()

        assert repository.snapshot(include_locks=False).ahead == 0

    def test_push_is_refused_when_the_server_has_moved_on(
        self, repository: Repository, second_clone: Path
    ):
        """Never force. A rejected push has to surface, not be worked around."""
        write_binary(second_clone / "props/crate.blend", "their work")
        git(second_clone, "add", "--", "props/crate.blend")
        git(second_clone, "commit", "-m", "Their crate")
        git(second_clone, "push")

        write_binary(repository.root / "props/crate.blend", "my work")
        repository.commit("My crate", ["props/crate.blend"])

        with pytest.raises(GitCommandError):
            repository.push()

        # and the other artist's work is still what the server holds
        git(second_clone, "fetch")
        assert b"their work" in (second_clone / "props/crate.blend").read_bytes()

    def test_nothing_to_push_is_not_an_error(self, repository: Repository):
        repository.push()

        assert repository.snapshot(include_locks=False).ahead == 0


class TestPull:
    def test_pull_brings_down_another_artists_commit(
        self, repository: Repository, second_clone: Path
    ):
        write_binary(second_clone / "props/crate.blend", "their work")
        git(second_clone, "add", "--", "props/crate.blend")
        git(second_clone, "commit", "-m", "Their crate")
        git(second_clone, "push")

        repository.fetch()
        assert repository.snapshot(include_locks=False).behind == 1

        result = repository.pull()

        assert result.ok
        assert b"their work" in (repository.root / "props/crate.blend").read_bytes()
        assert repository.snapshot(include_locks=False).behind == 0


class TestLockAvailability:
    """A remote that cannot serve locks must say so, not say "no locks".

    git-lfs does not help here: asked for locks against a file:// remote it
    prints a hint, exits 0, and returns an empty list. Reporting that as "no
    locks held" would tell an artist a file is free when the truth is that
    nothing is known — the worst thing this application can get wrong.
    """

    def test_file_remote_is_not_a_locking_endpoint(self, repository: Repository):
        assert not locks.locking_supported(repository.runner)

    def test_endpoint_is_reported_for_diagnostics(self, repository: Repository):
        endpoint = locks.locking_endpoint(repository.runner)

        assert endpoint, "an endpoint should always be resolvable"
        assert not endpoint.startswith("http")

    def test_snapshot_reports_locks_unavailable_rather_than_empty(
        self, repository: Repository
    ):
        state = repository.snapshot(include_locks=True)

        assert not state.locks_available
        assert state.locks_fetched_at is None

    def test_unavailable_locks_read_as_stale(self, repository: Repository):
        """So the UI degrades the lock column instead of showing it as fact."""
        state = repository.snapshot(include_locks=True)

        assert state.locks_are_stale()
        assert "unavailable" in state.lock_freshness_description()

    def test_no_file_claims_to_be_unlocked_when_locks_are_unknown(
        self, repository: Repository
    ):
        state = repository.snapshot(include_locks=True)

        assert state.files, "the fixture should have files"
        assert all(entry.lock is None for entry in state.files)
        assert not state.locks_available, (
            "lock is None everywhere, so locks_available is the only thing "
            "distinguishing 'free' from 'unknown'"
        )

    def test_support_check_is_cached(self, repository: Repository):
        """It runs on every refresh, so it must not shell out every time."""

        def env_calls() -> int:
            return sum(
                1
                for result in repository.runner.recent_commands()
                if result.argv[-1] == "env"
            )

        repository.supports_locking()
        repository.supports_locking()
        repository.supports_locking()

        assert env_calls() == 1


@pytest.mark.parametrize(
    "endpoint,supported",
    [
        ("https://git.example.com/team/art.git/info/lfs", True),
        ("http://gitea.local:3000/team/art.git/info/lfs", True),
        ("file:///C:/repos/server.git", False),
        ("/srv/git/server.git", False),
        ("ssh://git@example.com/team/art.git", False),
        ("", False),
    ],
)
def test_endpoint_scheme_decides_locking_support(endpoint: str, supported: bool):
    """The LFS locking API is HTTP only; everything else cannot serve it."""
    assert locks.endpoint_supports_locking(endpoint) is supported


class TestLockOperationsRefuseWhenStateIsUnknown:
    """Spec 10.2 in its strictest form: do not claim what cannot be verified.

    Without these guards the claim is attempted anyway and fails inside
    git-lfs, after the artist has already been shown the file as free.
    """

    def test_opening_for_editing_is_refused(self, repository: Repository):
        with pytest.raises(LocksUnavailableError):
            repository.prepare_for_editing("chars/hero/hero.blend")

    def test_releasing_a_lock_is_refused(self, repository: Repository):
        with pytest.raises(LocksUnavailableError):
            repository.release_lock("chars/hero/hero.blend")

    def test_read_only_opening_is_still_allowed(self, repository: Repository):
        """Looking at a file never needs the lock server."""
        path = repository.prepare_read_only("chars/hero/hero.blend")

        assert path.is_file()

    def test_the_refusal_explains_itself_in_plain_language(
        self, repository: Repository
    ):
        with pytest.raises(LocksUnavailableError) as raised:
            repository.prepare_for_editing("chars/hero/hero.blend")

        message = str(raised.value).lower()
        assert "lock server" in message
        assert "read-only" in message


class TestAuthenticationFailures:
    """Credentials are the first thing that goes wrong on a real server.

    Kiln never handles passwords (spec 7.4), so all it can do is recognise the
    failure and say what to do about it. Raw git output for this case is four
    lines of internals that mean nothing to an artist.
    """

    REAL_FAILURE = (
        "fatal: Cannot prompt because user interactivity has been disabled.\n"
        "fatal: could not read Username for 'https://git.example.com': "
        "terminal prompts disabled\n"
        "unable to get lock ID: Git credentials for "
        "https://git.example.com/TEAM/project.git not found."
    )

    def test_a_real_credential_failure_is_recognised(self):
        assert AuthenticationError.looks_like_auth_failure(self.REAL_FAILURE)

    @pytest.mark.parametrize(
        "output",
        [
            "fatal: Authentication failed for 'https://git.example.com/x.git/'",
            "remote: HTTP 401 Unauthorized",
            "fatal: could not read Password for 'https://git.example.com'",
        ],
    )
    def test_other_credential_wordings_are_recognised(self, output: str):
        assert AuthenticationError.looks_like_auth_failure(output)

    @pytest.mark.parametrize(
        "output",
        [
            "fatal: not a git repository",
            "error: failed to push some refs to 'origin'",
            "hint: Updates were rejected because the tip of your branch is behind",
            "",
        ],
    )
    def test_ordinary_failures_are_not_mistaken_for_auth(self, output: str):
        assert not AuthenticationError.looks_like_auth_failure(output)

    def test_the_host_is_extracted_for_the_message(self):
        error = AuthenticationError(["git", "fetch"], 128, "", self.REAL_FAILURE)

        assert error.host == "git.example.com"
        assert "git.example.com" in error.advice()

    def test_advice_survives_an_unparseable_host(self):
        error = AuthenticationError(["git", "fetch"], 128, "", "Authentication failed")

        assert error.host == ""
        assert "could not sign in" in error.advice().lower()

    def test_it_is_still_a_git_command_error(self):
        """So every existing handler keeps working unchanged."""
        error = AuthenticationError(["git", "fetch"], 128, "", self.REAL_FAILURE)

        assert isinstance(error, GitCommandError)
        assert error.diagnostics()

    def test_credential_manager_is_not_disabled(self):
        """Blocking GCM leaves an artist with no way to authenticate at all.

        GIT_TERMINAL_PROMPT stays off — there is no terminal to prompt into —
        but the credential helper's own window is how a desktop application is
        meant to ask.
        """
        from kiln.git.runner import GitRunner

        environment = GitRunner._environment()

        assert environment["GIT_TERMINAL_PROMPT"] == "0"
        assert "GCM_INTERACTIVE" not in environment


class TestSshRemotes:
    """Locking has to keep working once HTTP auth is turned off.

    Git itself will run over SSH with keys, but LFS object transfer and the
    locking API are always HTTP: for an SSH remote git-lfs runs
    `git-lfs-authenticate` over the SSH connection to obtain an HTTPS endpoint
    and a short-lived token.

    That is why locking_supported still passes for SSH clones, and this pins
    it — if a future git-lfs stopped resolving SSH remotes to an HTTPS
    endpoint, Kiln would report every file's lock state as unknown and these
    tests would say so instead of an artist discovering it.
    """

    SSH_REMOTES = [
        "git@git.example.com:TEAM/project.git",
        "ssh://git@git.example.com/TEAM/project.git",
        "ssh://git@git.example.com:2222/TEAM/project.git",
    ]

    @pytest.mark.parametrize("url", SSH_REMOTES)
    def test_ssh_remotes_resolve_to_an_http_lfs_endpoint(
        self, repository: Repository, url: str
    ):
        result = repository.runner.run(
            "-c", f"remote.probe.url={url}", "lfs", "env", check=False
        )

        endpoint = ""
        for line in result.stdout.splitlines():
            if line.startswith("Endpoint (probe)="):
                endpoint = line.split("=", 1)[1].split(" ", 1)[0]

        assert endpoint.startswith("https://"), f"{url} resolved to {endpoint!r}"
        assert locks.endpoint_supports_locking(endpoint)

    @pytest.mark.parametrize("url", SSH_REMOTES)
    def test_locking_is_considered_supported_for_ssh_remotes(
        self, repository: Repository, url: str
    ):
        """The check Kiln actually runs, not just the endpoint string."""
        result = repository.runner.run(
            "-c", f"remote.probe.url={url}", "lfs", "env", check=False
        )

        assert "Endpoint (probe)=https://" in result.stdout


class TestSshAuthenticationFailures:
    """SSH failures read nothing like HTTP ones, so they need their own match."""

    @pytest.mark.parametrize(
        "output",
        [
            "git@git.example.com: Permission denied (publickey).",
            "Host key verification failed.",
            "no supported authentication methods available",
        ],
    )
    def test_ssh_failures_are_recognised(self, output: str):
        assert AuthenticationError.looks_like_auth_failure(output)

    def test_ssh_advice_mentions_keys_not_passwords(self):
        error = AuthenticationError(
            ["git", "fetch"],
            128,
            "",
            "git@git.example.com: Permission denied (publickey).",
        )

        advice = error.advice().lower()
        assert "key" in advice
        assert "password" not in advice.split("never handles")[0]

    def test_host_is_extracted_from_an_ssh_failure(self):
        error = AuthenticationError(
            ["git", "fetch"],
            128,
            "",
            "git@git.example.com: Permission denied (publickey).",
        )

        assert error.host == "git.example.com"
