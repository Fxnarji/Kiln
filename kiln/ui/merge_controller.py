"""Merging and conflict resolution, kept out of the main window.

Everything to do with the one genuinely complicated state Kiln understands —
a merge with conflicts — lives here: offering the merge, running the conflict
dialog, resolving files one at a time, and finishing or abandoning.

The controller borrows the window rather than abstracting it. It needs the
repository, the job runner, and a parent for its dialogs, and inventing an
interface for those three would be more to learn than it saves.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox

from kiln.ui import dialogs
from kiln.ui.conflict_dialog import ConflictDialog

MERGE_PROMPT = (
    "You and the server have both changed this project since you last synced."
    "\n\nKiln can combine them, and will ask you which version to keep for any "
    "file that was changed twice. Continue?"
)


class MergeController(QObject):
    """Owns the conflict dialog and every merge operation."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.dialog: ConflictDialog | None = None

    @property
    def dialog_open(self) -> bool:
        """Is the conflict dialog on screen?

        Deliberately not called merge_in_progress: RepoState already has a
        field by that name meaning "git is mid-merge", which is a different
        question with a different answer.
        """
        return self.dialog is not None

    # -- starting a merge ---------------------------------------------------

    def handle_pull_result(self, result) -> None:
        """Decide what a failed fast-forward pull means for the artist."""
        if result.ok:
            self.window.refresh(with_locks=True)
        elif result.diverged:
            self.offer_merge()
        else:
            QMessageBox.warning(self.window, "Pull failed", result.message)

    def offer_merge(self) -> None:
        if not dialogs.confirm(self.window, "Changes on both sides", MERGE_PROMPT):
            return

        self.window.run_job(
            "merging",
            self.window.repository.start_merge_with_server,
            on_done=self._merge_started,
            error_context="Merge failed",
        )

    def _merge_started(self, merged_cleanly: bool) -> None:
        if merged_cleanly:
            self.window.refresh(with_locks=True)
        else:
            self.open_conflicts()

    # -- the conflict dialog ------------------------------------------------

    def open_conflicts(self) -> None:
        if self.dialog is not None:
            self.dialog.raise_()
            return

        self.window.run_job(
            "reading conflicts",
            self.window.repository.list_conflicts,
            on_done=self._show_dialog,
            error_context="Could not read the conflict list",
        )

    def _show_dialog(self, conflicts) -> None:
        if not conflicts:
            return

        dialog = ConflictDialog(conflicts, self.window)
        dialog.resolve_requested.connect(self.resolve)
        dialog.complete_requested.connect(self.complete)
        dialog.abandon_requested.connect(self.abandon)
        dialog.finished.connect(self._dialog_closed)

        self.dialog = dialog
        dialog.show()

    def _dialog_closed(self, _result: int) -> None:
        self.dialog = None

    # -- resolving ----------------------------------------------------------

    def resolve(self, path: str, side: str) -> None:
        self.window.run_job(
            f"resolving {Path(path).name}",
            lambda: self.window.repository.resolve_conflict(path, side),
            on_done=self._resolved,
            error_context="Could not resolve this file",
        )

    def _resolved(self, resolution) -> None:
        if self.dialog is not None:
            self.dialog.mark_resolved(resolution.path, resolution.side)
        self.window.refresh()

    def complete(self) -> None:
        if self.dialog is None:
            return
        resolutions = self.dialog.resolutions

        self.window.run_job(
            "completing merge",
            lambda: self.window.repository.complete_merge(resolutions),
            on_done=lambda _: self._finish(accepted=True),
            error_context="Could not complete the merge",
        )

    def abandon(self) -> None:
        self.window.run_job(
            "abandoning merge",
            self.window.repository.abandon_merge,
            on_done=lambda _: self._finish(accepted=False),
            error_context="Could not abandon the merge",
        )

    def _finish(self, accepted: bool) -> None:
        if self.dialog is not None:
            self.dialog.accept() if accepted else self.dialog.reject()
        self.window.refresh(with_locks=True)
