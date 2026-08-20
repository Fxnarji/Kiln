"""The main window: layout, wiring, and the refresh cycle.

The pattern throughout is the same three steps, and it is worth knowing before
reading anything below:

  1. every git call goes to the JobRunner, never runs inline
  2. when it comes back, the whole snapshot is rebuilt
  3. the widgets re-render from that snapshot

Nothing incrementally patches the display, so the window cannot drift out of
step with the disk.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from kiln.core.models import Pin, RepoState, describe_size
from kiln.core.repository import Repository
from kiln.errors import LockRefusedError
from kiln.ui import dialogs, sidebar as sidebar_module
from kiln.ui.conflict_dialog import ConflictDialog
from kiln.ui.detail_panel import DetailPanel
from kiln.ui.file_table import FileTable
from kiln.ui.pin_dialog import PinDialog
from kiln.ui.sidebar import Sidebar
from kiln.ui.thumbnails import ThumbnailCache
from kiln.ui.worker import JobRunner

log = logging.getLogger(__name__)

LOCK_REFRESH_INTERVAL_MS = 60_000

NOTHING_SELECTED = "No file selected"


class MainWindow(QMainWindow):
    def __init__(self, repository: Repository):
        super().__init__()
        self.repository = repository
        self.jobs = JobRunner(self)
        self.state: RepoState | None = None
        self._view = ("folder", "")
        self._conflict_dialog: ConflictDialog | None = None
        self._undo_item = None

        self.setWindowTitle(f"Kiln — {repository.root.name}")
        self.resize(1280, 800)

        self._build_toolbar()
        self._build_layout()
        self._build_status_bar()

        self.jobs.started.connect(lambda name: self.operation_label.setText(name))
        self.jobs.idle.connect(lambda: self.operation_label.setText(""))

        self._lock_timer = QTimer(self)
        self._lock_timer.timeout.connect(lambda: self.refresh(with_locks=True))
        self._lock_timer.start(LOCK_REFRESH_INTERVAL_MS)

        self.refresh(with_locks=True)

    # -- construction -------------------------------------------------------

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)

        self.pull_action = QAction("Pull", self)
        self.pull_action.triggered.connect(self._pull)
        self.push_action = QAction("Push", self)
        self.push_action.triggered.connect(self._push)
        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(lambda: self.refresh(with_locks=True))

        for action in (self.pull_action, self.push_action, refresh_action):
            toolbar.addAction(action)

        toolbar.addSeparator()
        terminal_action = QAction("Open folder", self)
        terminal_action.setToolTip("Open the working tree in the file browser")
        terminal_action.triggered.connect(self._open_working_tree)
        toolbar.addAction(terminal_action)

        diagnostics_action = QAction("Diagnostics", self)
        diagnostics_action.triggered.connect(self._show_diagnostics)
        toolbar.addAction(diagnostics_action)

    def _build_layout(self) -> None:
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setMargin(8)
        self.banner.hide()

        self.sidebar = Sidebar()
        self.sidebar.pin_opened.connect(self._open_pin)
        self.sidebar.folder_opened.connect(self._open_folder)
        self.sidebar.view_opened.connect(self._open_view)
        self.sidebar.context_menu_requested.connect(self._show_add_menu)
        self.sidebar.pin_requested.connect(self._edit_pin)
        self.sidebar.unpin_requested.connect(self._remove_pin)

        self.table = FileTable()
        self.table.thumbnail_cache = ThumbnailCache(self.repository.root)
        self.table.selection_changed.connect(self._on_selection_changed)
        self.table.file_activated.connect(self._on_file_activated)
        self.table.folder_activated.connect(self._open_folder)
        self.table.context_menu_requested.connect(self._show_add_menu)

        self.detail = DetailPanel()
        self.detail.open_requested.connect(self._open_for_editing)
        self.detail.open_read_only_requested.connect(self._open_read_only)
        self.detail.lock_requested.connect(self._lock_only)
        self.detail.release_requested.connect(self._release_lock)
        self.detail.discard_requested.connect(self._discard)

        centre = QWidget()
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(0, 0, 0, 0)

        view_header = QHBoxLayout()
        self.view_label = QLabel()
        view_header.addWidget(self.view_label)
        view_header.addStretch()
        self.up_button = QPushButton("Up")
        self.up_button.setToolTip("Open the parent folder")
        self.up_button.clicked.connect(self._open_parent_folder)
        view_header.addWidget(self.up_button)
        centre_layout.addLayout(view_header)
        centre_layout.addWidget(self.table, stretch=1)

        # Sits directly under the grid because it describes whatever is
        # selected there. It carries only what the grid cannot show by itself —
        # size and lock holder — rather than restating the name and state that
        # are already visible on the tile and in the detail panel.
        self.selection_label = QLabel(NOTHING_SELECTED)
        self.selection_label.setContentsMargins(4, 2, 4, 2)
        centre_layout.addWidget(self.selection_label)

        centre_layout.addWidget(self._build_commit_box())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.sidebar)
        splitter.addWidget(centre)
        splitter.addWidget(self.detail)
        splitter.setSizes([260, 660, 360])

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.banner)
        self.undo_bar = QWidget()
        undo_layout = QHBoxLayout(self.undo_bar)
        undo_layout.setContentsMargins(8, 4, 8, 4)
        self.undo_label = QLabel()
        undo_layout.addWidget(self.undo_label)
        undo_layout.addStretch()
        self.undo_button = QPushButton("Undo")
        self.undo_button.clicked.connect(self._undo_delete)
        undo_layout.addWidget(self.undo_button)
        self.undo_bar.hide()
        layout.addWidget(self.undo_bar)
        layout.addWidget(splitter, stretch=1)
        self.setCentralWidget(container)

        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.timeout.connect(self._expire_undo)

    def _build_commit_box(self) -> QGroupBox:
        self.commit_box = QGroupBox("Commit")
        layout = QVBoxLayout(self.commit_box)

        self.commit_message = QPlainTextEdit()
        self.commit_message.setPlaceholderText("What changed?")
        self.commit_message.setMaximumHeight(70)
        layout.addWidget(self.commit_message)

        buttons = QHBoxLayout()
        self.commit_button = QPushButton("Commit all changes")
        self.commit_button.clicked.connect(lambda: self._commit(and_push=False))
        self.commit_and_push_button = QPushButton("Commit and push")
        self.commit_and_push_button.clicked.connect(lambda: self._commit(and_push=True))
        buttons.addWidget(self.commit_button)
        buttons.addWidget(self.commit_and_push_button)
        layout.addLayout(buttons)

        self.commit_box.hide()
        return self.commit_box

    def _build_status_bar(self) -> None:
        """Repository-wide state, all of it, in the one bar Qt provides.

        Everything here is true regardless of what is selected: which branch,
        how far in or out of step with the server, how fresh the lock
        information is, and what Kiln is currently doing. Per-file information
        belongs under the grid, not here.

        This method touches no widget built in _build_layout, so the order the
        two are called in does not matter.
        """
        self.branch_label = QLabel()
        self.sync_label = QLabel()
        self.locks_label = QLabel()
        self.operation_label = QLabel()

        bar = self.statusBar()
        for widget in (self.branch_label, self.sync_label, self.locks_label):
            bar.addWidget(widget)
        bar.addPermanentWidget(self.operation_label)

    # -- refresh cycle ------------------------------------------------------

    def refresh(self, with_locks: bool = False) -> None:
        self.jobs.submit(
            "refreshing",
            lambda: self.repository.snapshot(include_locks=with_locks),
            on_done=self._apply_state,
            on_error=lambda error: dialogs.show_error(self, error, "Refresh failed"),
        )

    def _apply_state(self, state: RepoState) -> None:
        self.state = state
        self.sidebar.refresh(state, self.repository.pins(), self.repository.folders())
        self._update_banner(state)
        self._update_status_bar(state)
        self._render_view()

        self.pull_action.setEnabled(state.can_write and state.online)
        self.push_action.setEnabled(state.can_write and state.online)

        if state.merge_in_progress and self._conflict_dialog is None:
            self._open_conflicts()

    def _update_banner(self, state: RepoState) -> None:
        if state.blocked_reason:
            self._show_banner(state.blocked_reason, "#4A2E2B")
        elif state.merge_in_progress:
            self._show_banner(
                f"A merge is in progress with {len(state.conflicted_files)} "
                "conflicted file(s). Resolve them to carry on.",
                "#56381F",
            )
        elif not state.locks_available:
            self._show_banner(
                "The server cannot be reached. You can look at files, but "
                "locking, pulling, and pushing are unavailable.",
                "#3A3A2B",
            )
        else:
            self.banner.hide()

    def _show_banner(self, text: str, colour: str) -> None:
        # The one place a stylesheet is used: a coloured strip has no palette
        # role, and the alternative is a custom painted widget.
        self.banner.setStyleSheet(f"background: {colour};")
        self.banner.setText(text)
        self.banner.show()

    def _update_status_bar(self, state: RepoState) -> None:
        self.branch_label.setText(f"  {state.branch or '(no branch)'}  ")
        self.sync_label.setText(f"  {state.behind} in · {state.ahead} out  ")
        freshness = state.lock_freshness_description()
        self.locks_label.setText(
            f"  {freshness}{'  (stale)' if state.locks_are_stale() else ''}  "
        )

    # -- views --------------------------------------------------------------

    def _open_pin(self, pin: Pin) -> None:
        self._view = ("pin", pin)
        self._render_view()

    def _open_folder(self, folder: str) -> None:
        self._view = ("folder", folder)
        self._render_view()

    def _open_parent_folder(self) -> None:
        kind, folder = self._view
        if kind != "folder" or not folder:
            return
        parent, _, _ = folder.rpartition("/")
        self._open_folder(parent)

    def _open_view(self, name: str) -> None:
        if name == sidebar_module.VIEW_CONFLICTS:
            self._open_conflicts()
            return
        self._view = ("view", name)
        self._render_view()

    def _render_view(self) -> None:
        if self.state is None:
            return

        kind, value = self._view
        folders = []
        if kind == "pin":
            entries = [entry for entry in self.state.files if value.contains(entry.path)]
            title = f"{value.display_label()} — pinned, depth {value.depth}"
        elif kind == "view" and value == sidebar_module.VIEW_CHANGES:
            entries = self.state.changed_files
            title = f"Changes — {len(entries)} file(s)"
        elif kind == "view" and value == sidebar_module.VIEW_MY_LOCKS:
            entries = [entry for entry in self.state.files if entry.lock is not None]
            title = f"Locks — {len(entries)} file(s)"
        else:
            entries = [entry for entry in self.state.files if entry.parent == value]
            folders = [
                folder
                for folder in self.repository.folders()
                if folder.rpartition("/")[0] == value
            ]
            title = (
                f"{value or self.repository.root.name} — "
                f"{len(entries)} file(s), {len(folders)} folder(s)"
            )

        self.view_label.setText(f"  {title}")
        self.up_button.setEnabled(kind == "folder" and bool(value))
        showing_table = kind == "view" and value in (
            sidebar_module.VIEW_CHANGES,
            sidebar_module.VIEW_MY_LOCKS,
        )
        self.table.set_context_folder(value if kind == "folder" else "")
        self.table.set_table_mode(showing_table)
        self.table.show_files(entries, folders)

        showing_changes = kind == "view" and value == sidebar_module.VIEW_CHANGES
        self.commit_box.setVisible(showing_changes)
        if showing_changes:
            self._update_commit_buttons()

    def _update_commit_buttons(self) -> None:
        if self.state is None:
            return
        can_commit = bool(self.state.changed_files) and self.state.can_write
        self.commit_button.setEnabled(can_commit)
        self.commit_and_push_button.setEnabled(can_commit and self.state.online)

    # -- creation ----------------------------------------------------------

    def _show_add_menu(
        self,
        target: str,
        global_position,
        item_selected: bool = True,
        allow_delete: bool = True,
    ) -> None:
        parent = self._context_parent(target)
        menu = QMenu(self)
        add_folder = menu.addAction("Add folder")
        add_folder.triggered.connect(lambda: self._add_folder(parent))

        file_menu = menu.addMenu("Add file")
        templates = self.repository.templates()
        if not templates:
            file_menu.setEnabled(False)
            file_menu.setTitle("Add file (no templates found)")
        else:
            for suffix, paths in templates.items():
                type_menu = file_menu.addMenu(f"{suffix.lstrip('.').upper()} files")
                for template in paths:
                    action = type_menu.addAction(template.name)
                    action.triggered.connect(
                        lambda _checked=False, path=template: self._add_file(
                            parent, path
                        )
                    )

        menu.addSeparator()
        existing_pin = next(
            (pin for pin in self.repository.pins() if pin.path == parent), None
        )
        if existing_pin is None:
            menu.addAction("Pin this folder...", lambda: self._edit_pin(parent))
        else:
            menu.addAction("Edit pin...", lambda: self._edit_pin(parent))
            menu.addAction("Remove pin", lambda: self._remove_pin(parent))
        if (
            allow_delete
            and item_selected
            and target
            and (self.repository.root / target).exists()
        ):
            menu.addAction("Delete", lambda: self._delete_item(target))
        menu.exec(global_position)

    def _context_parent(self, target: str) -> str:
        entry = self.state.find(target) if self.state is not None else None
        return entry.parent if entry is not None else target

    def _delete_item(self, path: str) -> None:
        full_path = self.repository.root / path
        kind = "folder" if full_path.is_dir() else "file"
        if not dialogs.confirm(
            self,
            f"Delete {kind}",
            f"Move {path} to Kiln trash?\n\nYou will have 10 seconds to undo this action.",
        ):
            return
        self.jobs.submit(
            f"deleting {path}",
            lambda: self.repository.delete_to_trash(path),
            on_done=self._deleted,
            on_error=lambda error: dialogs.show_error(self, error, "Could not delete"),
        )

    def _deleted(self, deleted) -> None:
        self._undo_item = deleted
        self.undo_label.setText(f"Moved {deleted.repo_relative_path} to Kiln trash")
        self.undo_bar.show()
        self._undo_timer.start(10_000)
        self.refresh(with_locks=True)

    def _undo_delete(self) -> None:
        deleted = self._undo_item
        if deleted is None:
            return
        self._expire_undo()
        self.jobs.submit(
            f"restoring {deleted.repo_relative_path}",
            lambda: self.repository.undo_delete(deleted),
            on_done=lambda _: self.refresh(with_locks=True),
            on_error=lambda error: dialogs.show_error(self, error, "Could not undo delete"),
        )

    def _expire_undo(self) -> None:
        self._undo_timer.stop()
        self._undo_item = None
        self.undo_bar.hide()

    def _add_folder(self, parent: str) -> None:
        name, accepted = QInputDialog.getText(self, "Add folder", "Folder name:")
        if not accepted:
            return
        path = self._child_path(parent, name)
        if path is None:
            return
        self.jobs.submit(
            f"creating {path}",
            lambda: self.repository.add_folder(path),
            on_done=lambda _: self.refresh(with_locks=True),
            on_error=lambda error: dialogs.show_error(self, error, "Could not add folder"),
        )

    def _add_file(self, parent: str, template: Path) -> None:
        name, accepted = QInputDialog.getText(
            self,
            "Add file",
            f"Name for the new {template.suffix.lstrip('.').upper()} file:",
            text=template.stem,
        )
        if not accepted:
            return
        name = name.strip()
        if not name:
            QMessageBox.information(self, "Add file", "Enter a file name.")
            return
        if not Path(name).suffix:
            name += template.suffix
        path = self._child_path(parent, name)
        if path is None:
            return
        self.jobs.submit(
            f"adding {path}",
            lambda: self.repository.add_from_template(template, path),
            on_done=lambda _: self.refresh(with_locks=True),
            on_error=lambda error: dialogs.show_error(self, error, "Could not add file"),
        )

    def _child_path(self, parent: str, name: str) -> str | None:
        name = name.strip()
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            QMessageBox.warning(
                self,
                "Invalid name",
                "Use a single file or folder name without path separators.",
            )
            return None
        return f"{parent}/{name}" if parent else name

    # -- file actions -------------------------------------------------------

    def _on_selection_changed(self, path: str) -> None:
        if self.state is None:
            return
        entry = self.state.find(path) if path else None
        self.detail.show_file(entry, self.state)
        self.selection_label.setText(self._describe_selection(entry))

        if entry is not None:
            self.jobs.submit(
                "reading history",
                lambda: self.repository.commits(limit=20, path=entry.path),
                on_done=self.detail.show_history,
            )

    @staticmethod
    def _describe_selection(entry) -> str:
        """One line about the selected file, for the strip under the grid.

        Deliberately not a full property list: the name is on the tile and the
        detail panel has everything. What the grid genuinely cannot tell you is
        how big the file is and who is holding it.
        """
        if entry is None:
            return NOTHING_SELECTED

        parts = [describe_size(entry.size) or "size unknown"]
        parts.append(
            f"locked by {entry.lock_description()}"
            if entry.lock is not None
            else "not locked"
        )
        if not entry.downloaded:
            parts.append("NOT DOWNLOADED")
        return "   ·   ".join(parts)

    def _on_file_activated(self, path: str) -> None:
        """Double-click selects and shows detail. It deliberately does not
        open or lock anything — opening claims the file, so it is a button."""
        self.table.select_path(path)

    def _open_for_editing(self, path: str) -> None:
        if not path:
            return

        def launch(prepared) -> None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(prepared.path)))
            self.refresh(with_locks=True)

        def handle(error: Exception) -> None:
            if isinstance(error, LockRefusedError):
                if dialogs.show_lock_refused(self, error):
                    self._open_read_only(path)
                self.refresh(with_locks=True)
            else:
                dialogs.show_error(self, error, f"Could not open {path}")

        self.jobs.submit(
            f"opening {Path(path).name}",
            lambda: self.repository.prepare_for_editing(path),
            on_done=launch,
            on_error=handle,
        )

    def _open_read_only(self, path: str) -> None:
        self.jobs.submit(
            f"opening {Path(path).name} read-only",
            lambda: self.repository.prepare_read_only(path),
            on_done=lambda full: QDesktopServices.openUrl(QUrl.fromLocalFile(str(full))),
            on_error=lambda error: dialogs.show_error(self, error, "Could not open file"),
        )

    def _lock_only(self, path: str) -> None:
        self.jobs.submit(
            f"locking {Path(path).name}",
            lambda: self.repository.prepare_for_editing(path),
            on_done=lambda _: self.refresh(with_locks=True),
            on_error=lambda error: (
                dialogs.show_lock_refused(self, error)
                if isinstance(error, LockRefusedError)
                else dialogs.show_error(self, error, "Could not lock file")
            ),
        )

    def _release_lock(self, path: str) -> None:
        self.jobs.submit(
            f"releasing {Path(path).name}",
            lambda: self.repository.release_lock(path),
            on_done=lambda _: self.refresh(with_locks=True),
            on_error=lambda error: dialogs.show_error(self, error, "Could not release"),
        )

    def _discard(self, path: str) -> None:
        if not dialogs.confirm(
            self,
            "Discard changes",
            f"Throw away your changes to:\n\n{path}\n\n"
            "A copy is kept in .kiln/trash inside the project, so this can be "
            "undone by hand.",
        ):
            return

        self.jobs.submit(
            f"discarding {Path(path).name}",
            lambda: self.repository.discard(path),
            on_done=lambda _: self.refresh(with_locks=True),
            on_error=lambda error: dialogs.show_error(self, error, "Could not discard"),
        )

    # -- repository actions -------------------------------------------------

    def _commit(self, and_push: bool) -> None:
        if self.state is None:
            return
        message = self.commit_message.toPlainText().strip()
        if not message:
            QMessageBox.information(
                self, "Commit", "Describe what you changed before committing."
            )
            return

        paths = [entry.path for entry in self.state.changed_files]

        def work():
            self.repository.commit(message, paths)
            if and_push:
                self.repository.push()

        def done(_result) -> None:
            self.commit_message.clear()
            self.refresh(with_locks=True)

        self.jobs.submit(
            "committing" + (" and pushing" if and_push else ""),
            work,
            on_done=done,
            on_error=lambda error: dialogs.show_error(self, error, "Commit failed"),
        )

    def _pull(self) -> None:
        def done(result) -> None:
            if result.ok:
                self.refresh(with_locks=True)
                return
            if result.diverged:
                self._offer_merge()
            else:
                QMessageBox.warning(self, "Pull failed", result.message)

        self.jobs.submit(
            "pulling",
            self.repository.pull,
            on_done=done,
            on_error=lambda error: dialogs.show_error(self, error, "Pull failed"),
        )

    def _push(self) -> None:
        self.jobs.submit(
            "pushing",
            self.repository.push,
            on_done=lambda _: self.refresh(with_locks=True),
            on_error=lambda error: dialogs.show_error(self, error, "Push failed"),
        )

    def _offer_merge(self) -> None:
        if not dialogs.confirm(
            self,
            "Changes on both sides",
            "You and the server have both changed this project since you last "
            "synced.\n\nKiln can combine them, and will ask you which version to "
            "keep for any file that was changed twice. Continue?",
        ):
            return

        self.jobs.submit(
            "merging",
            self.repository.start_merge_with_server,
            on_done=lambda clean: (
                self.refresh(with_locks=True) if clean else self._open_conflicts()
            ),
            on_error=lambda error: dialogs.show_error(self, error, "Merge failed"),
        )

    # -- conflicts ----------------------------------------------------------

    def _open_conflicts(self) -> None:
        if self._conflict_dialog is not None:
            self._conflict_dialog.raise_()
            return

        self.jobs.submit(
            "reading conflicts",
            self.repository.list_conflicts,
            on_done=self._show_conflict_dialog,
            on_error=lambda error: dialogs.show_error(self, error, "Conflict list failed"),
        )

    def _show_conflict_dialog(self, conflicts) -> None:
        if not conflicts:
            return

        dialog = ConflictDialog(conflicts, self)
        self._conflict_dialog = dialog
        dialog.resolve_requested.connect(self._resolve_conflict)
        dialog.complete_requested.connect(self._complete_merge)
        dialog.abandon_requested.connect(self._abandon_merge)
        dialog.finished.connect(lambda _: setattr(self, "_conflict_dialog", None))
        dialog.show()

    def _resolve_conflict(self, path: str, side: str) -> None:
        self.jobs.submit(
            f"resolving {Path(path).name}",
            lambda: self.repository.resolve_conflict(path, side),
            on_done=lambda resolution: self._conflict_resolved(resolution),
            on_error=lambda error: dialogs.show_error(self, error, "Could not resolve"),
        )

    def _conflict_resolved(self, resolution) -> None:
        if self._conflict_dialog is not None:
            self._conflict_dialog.mark_resolved(resolution.path, resolution.side)
        self.refresh()

    def _complete_merge(self) -> None:
        if self._conflict_dialog is None:
            return
        resolutions = self._conflict_dialog.resolutions

        def done(_result) -> None:
            if self._conflict_dialog is not None:
                self._conflict_dialog.accept()
            self.refresh(with_locks=True)

        self.jobs.submit(
            "completing merge",
            lambda: self.repository.complete_merge(resolutions),
            on_done=done,
            on_error=lambda error: dialogs.show_error(self, error, "Merge failed"),
        )

    def _abandon_merge(self) -> None:
        def done(_result) -> None:
            if self._conflict_dialog is not None:
                self._conflict_dialog.reject()
            self.refresh(with_locks=True)

        self.jobs.submit(
            "abandoning merge",
            self.repository.abandon_merge,
            on_done=done,
            on_error=lambda error: dialogs.show_error(self, error, "Could not abandon"),
        )

    # -- pins ---------------------------------------------------------------

    def _edit_pin(self, folder: str) -> None:
        existing = next(
            (pin for pin in self.repository.pins() if pin.path == folder), None
        )
        all_files = [entry.path for entry in (self.state.files if self.state else [])]

        dialog = PinDialog(folder, all_files, existing, self)
        if dialog.exec() == PinDialog.Accepted:
            self.repository.add_pin(dialog.pin())
            self.refresh()

    def _remove_pin(self, folder: str) -> None:
        self.repository.remove_pin(folder)
        self.refresh()

    # -- misc ---------------------------------------------------------------

    def _open_working_tree(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.repository.root)))

    def _show_diagnostics(self) -> None:
        dialogs.DiagnosticsDialog(self.repository.runner, self).exec()

    def closeEvent(self, event) -> None:
        self.jobs.shutdown()
        super().closeEvent(event)
