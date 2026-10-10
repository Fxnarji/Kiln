"""What a refresh reads, and what it leaves alone.

A refresh runs after nearly every action, so the guarantees here are about
cost as much as correctness: local state never waits on the lock server, the
folder list never walks .git or .kiln, and history is read once per file per
commit however often the file is re-selected.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from kiln.core.models import FileEntry, Lock, LockReport, RepoState
from kiln.core.repository import Repository
from kiln.ui.history import HistoryLoader
from tests.conftest import git, write_binary


class TestLocalSnapshot:
    def test_never_asks_the_lock_server(self, repository: Repository, monkeypatch):
        def no_server():
            raise AssertionError("local_snapshot asked the lock server")

        monkeypatch.setattr(repository, "_load_locks", no_server)

        state = repository.local_snapshot()

        assert state.find("props/crate.blend") is not None
        assert state.locks_fetched_at is None

    def test_full_snapshot_still_includes_locks(self, repository: Repository, monkeypatch):
        lock = Lock("props/crate.blend", "Devin", None, is_mine=False)
        monkeypatch.setattr(
            repository, "_load_locks", lambda: ({"props/crate.blend": lock}, True)
        )

        state = repository.snapshot(include_locks=True)

        assert state.find("props/crate.blend").lock == lock
        assert state.locks_available and state.locks_fetched_at is not None

    def test_folders_skip_git_and_kiln_but_keep_empty_ones(
        self, repository: Repository, workspace: Path
    ):
        (workspace / "shots" / "empty").mkdir(parents=True)
        (workspace / ".kiln" / "trash" / "old").mkdir(parents=True)

        folders = repository.local_snapshot().folders

        assert "shots" in folders and "shots/empty" in folders
        assert "chars/hero/tex" in folders
        assert not any(f.split("/")[0] in {".git", ".kiln"} for f in folders)
        assert folders == repository.folders()

    def test_one_stat_answers_match_the_disk(self, repository: Repository, workspace: Path):
        pointer = workspace / "props" / "pointer.blend"
        pointer.write_text(
            "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 9\n"
        )
        git(workspace, "add", "props/pointer.blend")
        write_binary(workspace / "props" / "crate.blend", "edited crate")
        (workspace / "props" / "crate.blend").chmod(0o444)

        state = repository.local_snapshot()
        pointer_entry = state.find("props/pointer.blend")
        crate = state.find("props/crate.blend")

        assert not pointer_entry.downloaded and pointer_entry.size is None
        assert crate.downloaded and crate.size == 512
        assert crate.status == "modified"
        (workspace / "props" / "crate.blend").chmod(0o644)

    def test_locks_are_laid_over_a_snapshot_without_touching_it(self):
        state = RepoState(root="/p", files=[FileEntry("a.blend"), FileEntry("b.blend")])
        lock = Lock("b.blend", "Devin", None, is_mine=False)
        when = datetime.now(timezone.utc)

        with_locks = state.with_locks(LockReport({"b.blend": lock}, True, when))

        assert with_locks.find("b.blend").lock == lock
        assert with_locks.find("a.blend").lock is None
        assert with_locks.locks_fetched_at == when
        assert state.find("b.blend").lock is None

    def test_unreachable_server_reads_as_offline(self):
        state = RepoState(root="/p", files=[FileEntry("a.blend")])

        offline = state.with_locks(LockReport(available=False))

        assert not offline.online and not offline.locks_available
        assert offline.lock_freshness_description() == "lock state unavailable"


class FakeJobs:
    """Holds submitted jobs until told to run them, like the real queue."""

    def __init__(self):
        self.queue = []

    def submit(self, name, function, on_done=None, on_error=None):
        self.queue.append((function, on_done))

    def run_all(self):
        while self.queue:
            function, on_done = self.queue.pop(0)
            on_done(function())


class CountingRepository:
    def __init__(self):
        self.reads: list[str] = []

    def commits(self, limit, path):
        self.reads.append(path)
        return [f"commit of {path}"]


class TestHistoryLoader:
    def setup_method(self):
        self.jobs = FakeJobs()
        self.repository = CountingRepository()
        self.loader = HistoryLoader(self.jobs, self.repository)

    def test_reselecting_a_file_reads_nothing(self):
        shown = []
        for _ in range(3):
            self.loader.request("a.blend", "head1", lambda: True, shown.append)
            self.jobs.run_all()

        assert self.repository.reads == ["a.blend"]
        assert shown == [["commit of a.blend"]] * 3

    def test_two_panels_asking_at_once_share_one_read(self):
        shown = []
        self.loader.request("a.blend", "head1", lambda: True, shown.append)
        self.loader.request("a.blend", "head1", lambda: True, shown.append)
        self.jobs.run_all()

        assert self.repository.reads == ["a.blend"]
        assert len(shown) == 2

    def test_a_new_commit_reads_again(self):
        self.loader.request("a.blend", "head1", lambda: True, lambda c: None)
        self.jobs.run_all()
        self.loader.request("a.blend", "head2", lambda: True, lambda c: None)
        self.jobs.run_all()

        assert self.repository.reads == ["a.blend", "a.blend"]

    def test_files_skipped_past_are_never_read(self):
        selected = {"path": ""}
        shown = []
        for path in ("a.blend", "b.blend", "c.blend"):
            selected["path"] = path
            self.loader.request(
                path, "head1", lambda p=path: selected["path"] == p, shown.append
            )
        self.jobs.run_all()

        assert self.repository.reads == ["c.blend"]
        assert shown == [["commit of c.blend"]]
