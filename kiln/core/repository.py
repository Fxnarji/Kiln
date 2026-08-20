"""The single object the UI and the CLI both talk to.

Repository owns a GitRunner and turns the git layer's raw answers into a
RepoState snapshot. It is the only place that decides policy — what counts as a
state Kiln understands, when a write is allowed, what order things happen in.

It contains no Qt and no OS-specific calls, so everything below can be driven
from a terminal and tested headless.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from kiln.core import conflicts, editing, permissions, pins, trash
from kiln.core.models import FileEntry, Lock, Pin, RepoState
from kiln.core.pointers import is_downloaded
from kiln.errors import GitCommandError, NotARepositoryError, RepositoryBusyError
from kiln.git import history, locks, merge, remote, status, workdir
from kiln.git.runner import GitRunner, find_repository_root

log = logging.getLogger(__name__)


class Repository:
    """One open repository."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.runner = GitRunner(self.root)
        trash.ensure_ignored(self.root)

    @classmethod
    def open(cls, path: Path) -> "Repository":
        """Open the repository containing `path`."""
        root = find_repository_root(Path(path))
        if root is None:
            raise NotARepositoryError(f"{path} is not inside a git repository")
        return cls(root)

    # -- reading ------------------------------------------------------------

    def snapshot(self, include_locks: bool = True) -> RepoState:
        """Build a complete picture of the repository right now."""
        report = status.parse_status(self.runner.run(*status.status_argv()).stdout)

        lock_by_path: dict[str, Lock] = {}
        locks_available = True
        locks_fetched_at: datetime | None = None
        if include_locks:
            lock_by_path, locks_available = self._load_locks()
            if locks_available:
                locks_fetched_at = datetime.now(timezone.utc)

        status_by_path = {entry.path: entry.status for entry in report.entries}
        every_path = sorted(set(workdir.tracked_files(self.runner)) | set(status_by_path))

        files = [
            self._build_entry(path, status_by_path.get(path, "clean"), lock_by_path)
            for path in every_path
        ]

        return RepoState(
            root=str(self.root),
            branch=report.branch,
            head=report.head_oid,
            upstream=report.upstream,
            ahead=report.ahead,
            behind=report.behind,
            online=locks_available if include_locks else True,
            locks_available=locks_available,
            locks_fetched_at=locks_fetched_at,
            blocked_reason=self._blocked_reason(report),
            merge_in_progress=merge.merge_in_progress(self.runner),
            files=files,
        )

    def commits(self, limit: int = 50, path: str | None = None):
        return history.recent_commits(self.runner, limit=limit, path=path)

    def folders(self) -> list[str]:
        """Every visible folder in the working tree, including empty folders."""
        found: set[str] = set()
        for path in workdir.tracked_files(self.runner):
            parts = path.split("/")[:-1]
            for depth in range(1, len(parts) + 1):
                found.add("/".join(parts[:depth]))
        for directory in self.root.rglob("*"):
            if not directory.is_dir():
                continue
            relative = directory.relative_to(self.root)
            if any(part in {".git", ".kiln"} for part in relative.parts):
                continue
            found.add(relative.as_posix())
        return sorted(found)

    # -- pins ---------------------------------------------------------------

    def pins(self) -> list[Pin]:
        return pins.load_pins(self.root)

    def add_pin(self, pin: Pin) -> list[Pin]:
        return pins.add_pin(self.root, pin)

    def remove_pin(self, path: str) -> list[Pin]:
        return pins.remove_pin(self.root, path)

    # -- locking ------------------------------------------------------------

    def prepare_for_editing(self, repo_relative_path: str) -> editing.PreparedFile:
        self._require_writable_state()
        return editing.prepare_for_editing(self.runner, self.root, repo_relative_path)

    def prepare_read_only(self, repo_relative_path: str) -> Path:
        return editing.prepare_read_only(self.runner, self.root, repo_relative_path)

    def release_lock(self, repo_relative_path: str) -> None:
        self._require_writable_state()
        editing.release(self.runner, self.root, repo_relative_path)

    # -- creation ----------------------------------------------------------

    def templates(self) -> dict[str, list[Path]]:
        """Templates at the repository root, grouped by file extension."""
        directory = self.root / "_templates"
        found: dict[str, list[Path]] = {}
        if not directory.is_dir():
            return found
        for path in directory.iterdir():
            if path.is_file() and path.suffix:
                found.setdefault(path.suffix.lower(), []).append(path)
        for paths in found.values():
            paths.sort(key=lambda path: path.name.lower())
        return dict(sorted(found.items()))

    def add_folder(self, repo_relative_path: str) -> Path:
        """Create one folder in the working tree."""
        self._require_writable_state()
        path = self._safe_path(repo_relative_path)
        path.mkdir(parents=False, exist_ok=False)
        return path

    def add_from_template(
        self, template: Path, repo_relative_path: str
    ) -> Path:
        """Copy one root template into the tree and stage exactly that file."""
        self._require_writable_state()
        templates_root = (self.root / "_templates").resolve()
        source = template.resolve()
        if not source.is_file() or source.parent != templates_root:
            raise ValueError("template is outside the repository _templates folder")

        target = self._safe_path(repo_relative_path)
        if target.exists():
            raise FileExistsError(f"a file or folder already exists at {repo_relative_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        workdir.stage_file(self.runner, target.relative_to(self.root).as_posix())
        return target

    def delete_to_trash(self, repo_relative_path: str) -> trash.DeletedItem:
        """Move a file or folder to .kiln/trash and stage tracked deletion."""
        self._require_writable_state()
        source = self._safe_path(repo_relative_path)
        if not source.exists():
            raise FileNotFoundError(repo_relative_path)

        tracked_paths = workdir.tracked_files(self.runner)
        was_tracked = any(
            path == repo_relative_path or path.startswith(f"{repo_relative_path}/")
            for path in tracked_paths
        )
        destination = trash.kiln_directory(self.root) / trash.TRASH_DIRECTORY
        stamp = trash.new_stamp()
        backup = destination / stamp / repo_relative_path
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(backup))
        if was_tracked:
            workdir.stage_deleted_path(self.runner, repo_relative_path)
        return trash.DeletedItem(repo_relative_path, backup, was_tracked)

    def undo_delete(self, deleted: trash.DeletedItem) -> Path:
        """Restore one recent trash move and restage its original path."""
        self._require_writable_state()
        target = self._safe_path(deleted.repo_relative_path)
        if target.exists():
            raise FileExistsError(deleted.repo_relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(deleted.backup_path), str(target))
        if deleted.was_tracked:
            workdir.stage_file(self.runner, deleted.repo_relative_path)
        return target

    def _safe_path(self, repo_relative_path: str) -> Path:
        path = (self.root / repo_relative_path).resolve()
        if path == self.root or not path.is_relative_to(self.root):
            raise ValueError("path must stay inside the repository")
        return path

    # -- committing ---------------------------------------------------------

    def stage(self, repo_relative_paths: list[str]) -> None:
        self._require_writable_state()
        for path in repo_relative_paths:
            workdir.stage_file(self.runner, path)

    def commit(self, message: str, repo_relative_paths: list[str]) -> str:
        """Stage the given files and commit them."""
        self._require_writable_state()
        if not message.strip():
            raise ValueError("a commit needs a message")
        if not repo_relative_paths:
            raise ValueError("no files selected")
        self.stage(repo_relative_paths)
        return workdir.commit(self.runner, message)

    def discard(self, repo_relative_path: str) -> Path | None:
        """Throw away changes to one file, after copying it to .kiln/trash."""
        self._require_writable_state()
        backup = trash.backup_file(self.root, repo_relative_path)

        entry_status = {
            entry.path: entry.status for entry in self.snapshot(include_locks=False).files
        }
        if entry_status.get(repo_relative_path) == "new":
            workdir.remove_untracked_file(self.root / repo_relative_path)
        else:
            workdir.restore_file(self.runner, repo_relative_path)

        log.info("discarded %s (backup: %s)", repo_relative_path, backup)
        return backup

    # -- server -------------------------------------------------------------

    def is_online(self) -> bool:
        return remote.remote_reachable(self.runner)

    def fetch(self) -> None:
        remote.fetch(self.runner)

    def pull(self) -> remote.PullResult:
        self._require_writable_state()
        return remote.pull_fast_forward(self.runner)

    def push(self) -> str:
        self._require_writable_state()
        return remote.push(self.runner)

    # -- conflicts ----------------------------------------------------------

    def start_merge_with_server(self) -> bool:
        """Merge the upstream branch, expecting conflicts. True if it was clean."""
        upstream = remote.upstream_ref(self.runner)
        if not upstream:
            raise RepositoryBusyError(
                "This branch is not tracking anything on the server."
            )
        return merge.start_merge(self.runner, upstream)

    def list_conflicts(self) -> list[conflicts.ConflictedFile]:
        return conflicts.list_conflicts(self.runner)

    def resolve_conflict(self, repo_relative_path: str, side: str) -> conflicts.Resolution:
        return conflicts.resolve_conflict(self.runner, self.root, repo_relative_path, side)

    def complete_merge(self, resolutions: dict[str, str]) -> str:
        return conflicts.complete(
            self.runner, resolutions, remote.upstream_ref(self.runner) or "server"
        )

    def abandon_merge(self) -> None:
        conflicts.abandon(self.runner)

    # -- internals ----------------------------------------------------------

    def _load_locks(self) -> tuple[dict[str, Lock], bool]:
        """Fetch locks, reporting availability rather than raising.

        The server being unreachable is an ordinary condition here: Kiln shows
        the offline banner and disables the operations that need it.
        """
        try:
            found = locks.list_locks(self.runner)
        except GitCommandError as exc:
            log.info("lock listing unavailable: %s", exc.stderr.strip()[:200])
            return {}, False

        return {
            lock.path: Lock(
                path=lock.path,
                owner=lock.owner,
                locked_at=lock.locked_at,
                is_mine=lock.is_mine,
            )
            for lock in found
        }, True

    def _build_entry(
        self, path: str, file_status: str, lock_by_path: dict[str, Lock]
    ) -> FileEntry:
        full_path = self.root / path
        downloaded = is_downloaded(self.root, path)
        return FileEntry(
            path=path,
            status=file_status,
            downloaded=downloaded,
            writable=permissions.is_writable(full_path),
            size=workdir.file_size_on_disk(self.root, path) if downloaded else None,
            lock=lock_by_path.get(path),
        )

    def _blocked_reason(self, report: status.StatusReport) -> str:
        """States Kiln refuses to act in (spec 10.5).

        A merge with conflicts is deliberately *not* blocked: that is the one
        complicated state Kiln knows how to walk an artist out of.
        """
        if not report.branch:
            return (
                "This clone is not on a branch (detached HEAD). Kiln cannot make "
                "changes from here — open a terminal and ask for help."
            )

        git_directory = merge.git_dir(self.runner)
        if (git_directory / "rebase-merge").exists() or (
            git_directory / "rebase-apply"
        ).exists():
            return (
                "A rebase is in progress. Kiln does not handle rebases — finish "
                "or abort it on the command line."
            )
        if (git_directory / "CHERRY_PICK_HEAD").exists():
            return (
                "A cherry-pick is in progress. Finish or abort it on the command "
                "line."
            )
        return ""

    def _require_writable_state(self) -> None:
        report = status.parse_status(self.runner.run(*status.status_argv()).stdout)
        reason = self._blocked_reason(report)
        if reason:
            raise RepositoryBusyError(reason)
