"""The frame every list screen is hung on.

A screen is four separable pieces, and this class owns none of them: the list
itself, the panel down the right, the panel along the bottom, and the right
click menu. Each is built by a method a subclass overrides, and each lives in
its own module, so a screen can have a menu and nothing else (Locks) or a
panel, a menu, and a commit box (Changes) without either one carrying code it
does not use.

What the base does own is the wiring: pushing each new snapshot into the list,
turning a selection into "these entries", and closing tidily.

The windows do not refresh themselves. MainWindow._apply_state pushes the new
snapshot into every open one, which keeps them in step with the grid without a
second refresh cycle.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from kiln.core.models import FileEntry, RepoState, describe_size
from kiln.ui.views.file_list import FileList

NOTHING_SELECTED = "No file selected"


class ViewWindow(QMainWindow):
    """A list of files in a window of its own, beside the main window."""

    #: Identifies this screen to the sidebar, and to the open windows the main
    #: window keeps.
    view_name = ""
    #: Window title, before the repository name is appended.
    title = ""
    #: Width of the window, and of its side panel when it has one.
    window_size = (1040, 640)
    side_panel_width = 360

    def __init__(self, window):
        super().__init__(window, Qt.Window)
        self.window = window
        self.menu = self.build_menu()

        self.setWindowTitle(f"{self.title} — {window.repository.root.name}")
        self.resize(*self.window_size)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCentralWidget(self._build_body())

    # -- what a subclass decides --------------------------------------------

    def build_list(self) -> FileList:
        """The list, with the columns this screen is about."""
        raise NotImplementedError

    def build_menu(self):
        """The right click menu. Needs a popup(position, entries, state)."""
        raise NotImplementedError

    def build_side_panel(self) -> QWidget | None:
        """The panel down the right. None for a screen that wants none."""
        return None

    def build_bottom_panel(self) -> QWidget | None:
        """The panel along the bottom. None for a screen that wants none."""
        return None

    def entries_for(self, state: RepoState) -> list[FileEntry]:
        """The files this screen lists, taken from the current snapshot."""
        raise NotImplementedError

    def heading_for(self, entries: list[FileEntry]) -> str:
        return f"{self.title} — {len(entries)} file(s)"

    def selection_shown(self, entries: list[FileEntry], state: RepoState) -> None:
        """Called when the chosen files change, for a screen with a panel."""

    def state_applied(self, state: RepoState) -> None:
        """Called after each snapshot, for a screen with a bottom panel."""

    # -- construction -------------------------------------------------------

    def _build_body(self) -> QWidget:
        centre = self._build_centre_column()
        side = self.build_side_panel()
        if side is None:
            return centre

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(centre)
        splitter.addWidget(side)
        splitter.setSizes(
            [self.window_size[0] - self.side_panel_width, self.side_panel_width]
        )
        return splitter

    def _build_centre_column(self) -> QWidget:
        self.list = self.build_list()
        self.list.selection_changed.connect(self._selection_changed)
        self.list.file_activated.connect(self._activated)
        self.list.menu_requested.connect(self._show_menu)

        self.heading_label = QLabel()
        self.summary_label = QLabel(NOTHING_SELECTED)
        self.summary_label.setContentsMargins(4, 2, 4, 2)

        centre = QWidget()
        layout = QVBoxLayout(centre)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.heading_label)
        layout.addWidget(self.list, stretch=1)
        layout.addWidget(self.summary_label)

        bottom = self.build_bottom_panel()
        if bottom is not None:
            layout.addWidget(bottom)
        return centre

    # -- refresh ------------------------------------------------------------

    def apply_state(self, state: RepoState) -> None:
        """Re-render from the snapshot the main window just fetched."""
        entries = self.entries_for(state)
        self.heading_label.setText(f"  {self.heading_for(entries)}")
        # Rebuilding drops any chosen file that has left the list, and re-emits
        # the selection, so nothing downstream can go on describing it.
        self.list.show_files(entries)
        self.state_applied(state)

    # -- selection ----------------------------------------------------------

    def _selection_changed(self, _paths: list[str]) -> None:
        state = self.window.state
        if state is None:
            return

        entries = self.list.selected_entries()
        self.summary_label.setText(self.describe(entries))
        self.selection_shown(entries, state)

    def describe(self, entries: list[FileEntry]) -> str:
        """The line under the list. One file reads like the main window."""
        if not entries:
            return NOTHING_SELECTED
        if len(entries) == 1:
            return self.window.describe_selection(entries[0])

        total = sum(entry.size or 0 for entry in entries)
        return f"{len(entries)} files selected   ·   {describe_size(total)}"

    def _activated(self, path: str) -> None:
        """Double-click picks a file. It deliberately opens nothing: opening
        claims the file, so it stays behind a named menu entry."""

    def _show_menu(self, position) -> None:
        state = self.window.state
        if state is not None:
            self.menu.popup(position, self.list.selected_entries(), state)

    # -- history ------------------------------------------------------------

    def show_history_in(self, panel, entry: FileEntry | None) -> None:
        """Fill a panel's history, for the screens that have one.

        Arrow-keying down the list queues one read per file, and they come back
        in order but later than the selection moved, so the answer is dropped
        unless it is still the file being looked at.
        """
        if entry is None:
            return

        def arrived(commits) -> None:
            if self.list.selected_paths() == [entry.path]:
                panel.show_history(commits)

        self.window.jobs.submit(
            "reading history",
            lambda: self.window.repository.commits(limit=20, path=entry.path),
            on_done=arrived,
        )

    # -- lifetime -----------------------------------------------------------

    def closeEvent(self, event) -> None:
        self.window.forget_view_window(self.view_name)
        super().closeEvent(event)
