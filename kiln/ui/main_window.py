"""The main window: layout, the refresh cycle, and the everyday file actions.

The pattern throughout is the same three steps, and it is worth knowing before
reading anything below:

  1. every git call goes through run_job, never runs inline
  2. when it comes back, the whole snapshot is rebuilt
  3. the widgets re-render from that snapshot

Nothing incrementally patches the display, so the window cannot drift out of
step with the disk.

Two larger concerns live in their own modules and are given the window to work
against: MergeController (merging and conflicts) and FileActionsController
(adding, deleting, and the undo bar).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from kiln.core.models import Pin, RepoState, describe_size
from kiln.core.repository import Repository
from kiln.core.settings import save_last_project
from kiln.errors import KilnError, LockRefusedError
from kiln.git.runner import find_repository_root
from kiln.ui import dialogs, sidebar as sidebar_module
from kiln.ui.detail_panel import DetailPanel
from kiln.ui.file_actions import FileActionsController
from kiln.ui.file_table import FileTable
from kiln.ui.merge_controller import MergeController
from kiln.ui.pin_dialog import PinDialog
from kiln.ui.settings_dialog import ProjectSettingsDialog
from kiln.ui.sidebar import Sidebar
from kiln.ui.thumbnails import ThumbnailCache
from kiln.ui.views import VIEW_WINDOWS, ViewWindow
from kiln.ui.worker import JobRunner

LOCK_REFRESH_INTERVAL_MS = 60_000

NOTHING_SELECTED = "No file selected"

DISCARD_WARNING = (
    "Throw away your changes to:\n\n{files}\n\n"
    "A copy is kept in .kiln/trash inside the project, so this can be undone "
    "by hand."
)

# Enough of a list to recognise what is about to go, without a dialog
# taller than the screen when somebody selects everything.
DISCARD_LIST_LIMIT = 12


class MainWindow(QMainWindow):
    #: Every open main window. A window opened from Project > Open project has
    #: no parent, so without a reference here Python would collect it the
    #: moment the method that made it returned.
    _windows: list["MainWindow"] = []

    def __init__(self, repository: Repository):
        super().__init__()
        self.repository = repository
        self.jobs = JobRunner(self)
        self.state: RepoState | None = None
        self._view = ("folder", "")
        self._folders: list[str] = []
        # Changes and Locks open beside this window rather than replacing the
        # grid, so browsing survives a look at either list.
        self._view_windows: dict[str, ViewWindow] = {}

        self.merge = MergeController(self)
        self.file_actions = FileActionsController(self)

        self.setWindowTitle(f"Kiln — {repository.root.name}")
        self.resize(1280, 800)
        MainWindow._windows.append(self)

        self._build_toolbar()
        self._build_layout()
        self._build_status_bar()

        self.jobs.started.connect(self.operation_label.setText)
        self.jobs.idle.connect(lambda: self.operation_label.setText(""))

        self._lock_timer = QTimer(self)
        self._lock_timer.timeout.connect(lambda: self.refresh(with_locks=True))
        self._lock_timer.start(LOCK_REFRESH_INTERVAL_MS)

        self.refresh(with_locks=True)

    # -- running work -------------------------------------------------------

    def run_job(
        self,
        label: str,
        work: Callable[[], Any],
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        error_context: str = "",
    ) -> None:
        """Run one git operation on the worker thread.

        This is the single entry point for everything that touches the
        repository, and it supplies the two behaviours nearly every caller
        wants: refresh the snapshot afterwards, and report failures in a dialog
        with a Copy diagnostics button.

        Pass on_done only when the result matters. Doing so **replaces** the
        automatic refresh rather than adding to it, so a callback that wants
        the display updated has to say `self.refresh(...)` itself — which is
        also how a callback chooses a cheaper refresh than the default.
        """

        def done(result: Any) -> None:
            if on_done is not None:
                on_done(result)
            else:
                self.refresh(with_locks=True)

        def failed(error: Exception) -> None:
            dialogs.show_error(self, error, error_context or label)

        self.jobs.submit(label, work, on_done=done, on_error=on_error or failed)

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
        open_folder_action = QAction("Open folder", self)
        open_folder_action.setToolTip("Open the working tree in the file browser")
        open_folder_action.triggered.connect(self._open_working_tree)
        toolbar.addAction(open_folder_action)

        diagnostics_action = QAction("Diagnostics", self)
        diagnostics_action.triggered.connect(self._show_diagnostics)
        toolbar.addAction(diagnostics_action)

        toolbar.addSeparator()
        toolbar.addWidget(self._build_project_button())

    def _build_project_button(self) -> QToolButton:
        """The Project drop-down, in the same row as Pull, Refresh, and the
        rest.

        Both entries under it concern the project as a whole rather than any
        one file, and neither is reached often enough to earn a button of its
        own beside the everyday actions — so they share one. The drop-down also
        keeps "Open project", which changes what Kiln is looking at, well away
        from "Open folder", which only shows the working tree in Explorer.
        """
        menu = QMenu(self)

        open_action = QAction("Open project...", self)
        open_action.setToolTip("Open a different project folder in Kiln")
        open_action.triggered.connect(self._open_project)
        menu.addAction(open_action)

        menu.addSeparator()

        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self._show_project_settings)
        menu.addAction(settings_action)

        button = QToolButton(self)
        button.setText("Project")
        button.setMenu(menu)
        button.setPopupMode(QToolButton.InstantPopup)
        button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        return button

    def _build_layout(self) -> None:
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setMargin(8)
        self.banner.hide()

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_centre_column())
        splitter.addWidget(self._build_detail_panel())
        splitter.setSizes([260, 660, 360])

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.banner)
        layout.addWidget(self.file_actions.undo_bar)
        layout.addWidget(splitter, stretch=1)
        self.setCentralWidget(container)

    def _build_sidebar(self) -> Sidebar:
        self.sidebar = Sidebar()
        self.sidebar.pin_opened.connect(self._open_pin)
        self.sidebar.folder_opened.connect(self._open_folder)
        self.sidebar.view_opened.connect(self._open_view)
        self.sidebar.context_menu_requested.connect(self.file_actions.show_menu)
        self.sidebar.pin_requested.connect(self.edit_pin)
        self.sidebar.unpin_requested.connect(self.remove_pin)
        return self.sidebar

    def _build_centre_column(self) -> QWidget:
        self.table = FileTable()
        self.table.thumbnail_cache = ThumbnailCache(self.repository.root)
        self.table.selection_changed.connect(self._on_selection_changed)
        self.table.file_activated.connect(self._on_file_activated)
        self.table.folder_activated.connect(self._open_folder)
        self.table.context_menu_requested.connect(self.file_actions.show_menu)

        header = QHBoxLayout()
        self.view_label = QLabel()
        header.addWidget(self.view_label)
        header.addStretch()
        self.up_button = QPushButton("Up")
        self.up_button.setToolTip("Open the parent folder")
        self.up_button.clicked.connect(self._open_parent_folder)
        header.addWidget(self.up_button)

        # Sits directly under the grid because it describes whatever is
        # selected there. It carries only what the grid cannot show by itself —
        # size and lock holder — rather than restating the name and state that
        # are already visible on the tile and in the detail panel.
        self.selection_label = QLabel(NOTHING_SELECTED)
        self.selection_label.setContentsMargins(4, 2, 4, 2)

        centre = QWidget()
        layout = QVBoxLayout(centre)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        layout.addWidget(self.table, stretch=1)
        layout.addWidget(self.selection_label)
        return centre

    def _build_detail_panel(self) -> DetailPanel:
        self.detail = DetailPanel()
        self.detail.open_requested.connect(self.open_for_editing)
        self.detail.open_read_only_requested.connect(self.open_read_only)
        self.detail.lock_requested.connect(self._lock_only)
        self.detail.release_requested.connect(lambda path: self.release_locks([path]))
        self.detail.discard_requested.connect(lambda path: self.discard([path]))
        return self.detail

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

        # Walking the tree for the folder list runs a git command and an rglob,
        # and this runs on the UI thread. Do it once per refresh and reuse the
        # result for both the sidebar and the folder view.
        self._folders = self.repository.folders()

        self.sidebar.refresh(state, self.repository.pins(), self._folders)
        self._update_banner(state)
        self._update_status_bar(state)
        self._render_view()
        for window in list(self._view_windows.values()):
            window.apply_state(state)

        self.pull_action.setEnabled(state.can_write and state.online)
        self.push_action.setEnabled(state.can_write and state.online)

        if state.merge_in_progress and not self.merge.dialog_open:
            self.merge.open_conflicts()

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
        self._open_folder(folder.rpartition("/")[0])

    def _open_view(self, name: str) -> None:
        """Changes, Locks, and conflicts are all windows of their own."""
        if name == sidebar_module.VIEW_CONFLICTS:
            self.merge.open_conflicts()
            return

        window = self._view_windows.get(name)
        if window is None:
            window = VIEW_WINDOWS[name](self)
            self._view_windows[name] = window
            if self.state is not None:
                window.apply_state(self.state)
            window.show()
        else:
            window.showNormal()
        window.raise_()
        window.activateWindow()

    def forget_view_window(self, name: str) -> None:
        """Called by a ViewWindow as it closes, so it stops being refreshed."""
        self._view_windows.pop(name, None)

    def _render_view(self) -> None:
        if self.state is None:
            return

        entries, folders, title = self._contents_of_current_view()
        kind, value = self._view

        self.view_label.setText(f"  {title}")
        self.up_button.setEnabled(kind == "folder" and bool(value))
        self.table.set_context_folder(value if kind == "folder" else "")
        self.table.show_files(entries, folders)

    def _contents_of_current_view(self) -> tuple[list, list[str], str]:
        """The files, subfolders, and heading for whatever is being shown."""
        kind, value = self._view
        files = self.state.files

        if kind == "pin":
            entries = [entry for entry in files if value.contains(entry.path)]
            return entries, [], f"{value.display_label()} — pinned, depth {value.depth}"

        entries = [entry for entry in files if entry.parent == value]
        folders = [
            folder for folder in self._folders if folder.rpartition("/")[0] == value
        ]
        name = value or self.repository.root.name
        return (
            entries,
            folders,
            f"{name} — {len(entries)} file(s), {len(folders)} folder(s)",
        )

    # -- file actions -------------------------------------------------------

    def _on_selection_changed(self, path: str) -> None:
        if self.state is None:
            return

        entry = self.state.find(path) if path else None
        self.detail.show_file(entry, self.state)
        self.selection_label.setText(self.describe_selection(entry))

        if entry is not None:
            self.jobs.submit(
                "reading history",
                lambda: self.repository.commits(limit=20, path=entry.path),
                on_done=lambda commits: self._show_history_for(entry.path, commits),
            )

    def _show_history_for(self, path: str, commits) -> None:
        """Display history only if that file is still the selected one.

        Arrow-keying down a folder queues one history read per file, and they
        come back in order but later than the selection moved. Without this
        check the panel would briefly show another file's history.
        """
        if self.table.selected_path() == path:
            self.detail.show_history(commits)

    @staticmethod
    def describe_selection(entry) -> str:
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

    def open_for_editing(self, path: str) -> None:
        if not path:
            return

        def launch(prepared) -> None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(prepared.path)))
            self.refresh(with_locks=True)

        self.run_job(
            f"opening {Path(path).name}",
            lambda: self.repository.prepare_for_editing(path),
            on_done=launch,
            on_error=lambda error: self._handle_open_failure(path, error),
        )

    def _handle_open_failure(self, path: str, error: Exception) -> None:
        """A refused lock is an ordinary outcome; anything else is an error."""
        if not isinstance(error, LockRefusedError):
            dialogs.show_error(self, error, f"Could not open {path}")
            return

        if dialogs.show_lock_refused(self, error):
            self.open_read_only(path)
        self.refresh(with_locks=True)

    def open_read_only(self, path: str) -> None:
        self.run_job(
            f"opening {Path(path).name} read-only",
            lambda: self.repository.prepare_read_only(path),
            on_done=lambda full: QDesktopServices.openUrl(QUrl.fromLocalFile(str(full))),
            error_context="Could not open this file",
        )

    def _lock_only(self, path: str) -> None:
        self.run_job(
            f"locking {Path(path).name}",
            lambda: self.repository.prepare_for_editing(path),
            on_error=lambda error: self._handle_open_failure(path, error),
        )

    def release_locks(
        self, paths: list[str], dialog_parent: QWidget | None = None
    ) -> None:
        """Give back our own locks. Never forces, so these can only be ours."""
        if not paths:
            return

        def work() -> None:
            for path in paths:
                self.repository.release_lock(path)

        self.run_job(
            _describe_job("releasing", paths, "locks"),
            work,
            on_error=lambda error: dialogs.show_error(
                dialog_parent or self, error, "Could not release the lock"
            ),
        )

    def discard(
        self, paths: list[str], dialog_parent: QWidget | None = None
    ) -> None:
        """Throw away the changes to these files, after one confirmation.

        One dialog for the whole selection, listing what it covers. Asking once
        per file would train an artist to click the question away.
        """
        if not paths or not dialogs.confirm(
            dialog_parent or self, "Discard changes", _discard_warning(paths)
        ):
            return

        def work() -> None:
            for path in paths:
                self.repository.discard(path)

        self.run_job(
            _describe_job("discarding", paths, "files"),
            work,
            on_error=lambda error: dialogs.show_error(
                dialog_parent or self, error, "Could not discard the changes"
            ),
        )

    # -- repository actions -------------------------------------------------

    def commit(
        self,
        message: str,
        and_push: bool,
        dialog_parent: QWidget | None = None,
        on_success: Callable[[], None] | None = None,
    ) -> None:
        """Commit everything that changed, on behalf of the Changes window."""
        if self.state is None:
            return

        message = message.strip()
        if not message:
            QMessageBox.information(
                dialog_parent or self,
                "Commit",
                "Describe what you changed before committing.",
            )
            return

        paths = [entry.path for entry in self.state.changed_files]

        def work() -> None:
            self.repository.commit(message, paths)
            if and_push:
                self.repository.push()

        def done(_result) -> None:
            if on_success is not None:
                on_success()
            self.refresh(with_locks=True)

        self.run_job(
            "committing and pushing" if and_push else "committing",
            work,
            on_done=done,
            error_context="Commit failed",
        )

    def _pull(self) -> None:
        self.run_job(
            "pulling",
            self.repository.pull,
            on_done=self.merge.handle_pull_result,
            error_context="Pull failed",
        )

    def _push(self) -> None:
        self.run_job("pushing", self.repository.push, error_context="Push failed")

    # -- pins ---------------------------------------------------------------

    def edit_pin(self, folder: str) -> None:
        existing = next(
            (pin for pin in self.repository.pins() if pin.path == folder), None
        )
        all_files = [entry.path for entry in (self.state.files if self.state else [])]

        dialog = PinDialog(folder, all_files, existing, self)
        if dialog.exec() == PinDialog.Accepted:
            self.repository.add_pin(dialog.pin())
            self.refresh()

    def remove_pin(self, folder: str) -> None:
        self.repository.remove_pin(folder)
        self.refresh()

    # -- the project --------------------------------------------------------

    def _open_project(self) -> None:
        """Swap this window for one on another project.

        A fresh window rather than a rebuilt one: everything here is built from
        the repository at construction, so there is no half-updated state to
        get wrong if the new project starts from scratch.
        """
        chosen = QFileDialog.getExistingDirectory(self, "Open a project folder")
        if not chosen:
            return

        folder = Path(chosen)
        if find_repository_root(folder) is None:
            QMessageBox.critical(
                self,
                "Not a project folder",
                f"{folder}\n\nis not inside a git repository.",
            )
            return

        try:
            repository = Repository.open(folder)
        except KilnError as error:
            dialogs.show_error(self, error, "Could not open this project")
            return

        if repository.root == self.repository.root:
            return

        save_last_project(repository.root)
        window = MainWindow(repository)
        window.show()
        self.close()

    def _show_project_settings(self) -> None:
        ProjectSettingsDialog(self.repository, self).exec()

    # -- misc ---------------------------------------------------------------

    def _open_working_tree(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.repository.root)))

    def _show_diagnostics(self) -> None:
        dialogs.DiagnosticsDialog(self.repository.runner, self).exec()

    def closeEvent(self, event) -> None:
        # Qt keeps the application alive while any window is open, so the
        # Changes and Locks windows have to go with this one.
        for window in list(self._view_windows.values()):
            window.close()
        self.jobs.shutdown()
        if self in MainWindow._windows:
            MainWindow._windows.remove(self)
        super().closeEvent(event)

def _discard_warning(paths: list[str]) -> str:
    shown = paths[:DISCARD_LIST_LIMIT]
    listing = "\n".join(shown)
    if len(paths) > len(shown):
        listing += f"\n... and {len(paths) - len(shown)} more"
    return DISCARD_WARNING.format(files=listing)


def _describe_job(verb: str, paths: list[str], plural: str) -> str:
    """What the status bar says while a selection is worked through."""
    if len(paths) == 1:
        return f"{verb} {Path(paths[0]).name}"
    return f"{verb} {len(paths)} {plural}"
