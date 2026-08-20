"""Details and actions for the selected file.

A panel beside the file table rather than a separate screen: at this repository
size an artist is nearly always picking a file and then immediately acting on
it, and a panel keeps both in view.

The button states here are the visible half of the safety rules — Open is
disabled when somebody else holds the file, and everything is disabled when the
repository is in a state Kiln does not understand.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kiln.core.models import FileEntry, RepoState, describe_size
from kiln.ui import theme

HISTORY_COLUMNS = ["When", "Who", "Change"]


class DetailPanel(QWidget):
    open_requested = Signal(str)
    open_read_only_requested = Signal(str)
    lock_requested = Signal(str)
    release_requested = Signal(str)
    discard_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = ""

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_properties())
        layout.addWidget(self._build_actions())
        layout.addWidget(self._build_history(), stretch=1)

        self.show_file(None, None)

    # -- construction -------------------------------------------------------

    def _build_properties(self) -> QGroupBox:
        box = QGroupBox("File")
        form = QFormLayout(box)

        self.name_label = QLabel()
        self.name_label.setWordWrap(True)
        self.folder_label = QLabel()
        self.folder_label.setWordWrap(True)
        self.size_label = QLabel()
        self.state_label = QLabel()
        self.lock_label = QLabel()
        self.disk_label = QLabel()

        form.addRow("Name", self.name_label)
        form.addRow("Folder", self.folder_label)
        form.addRow("Size", self.size_label)
        form.addRow("State", self.state_label)
        form.addRow("Lock", self.lock_label)
        form.addRow("On disk", self.disk_label)
        return box

    def _build_actions(self) -> QGroupBox:
        box = QGroupBox("Actions")
        layout = QVBoxLayout(box)

        self.open_button = QPushButton("Open for editing")
        self.open_button.setToolTip(
            "Downloads if needed, claims the lock, then opens the file.\n"
            "If someone else holds it, nothing is opened."
        )
        self.read_only_button = QPushButton("Open read-only")
        self.read_only_button.setToolTip("Opens without claiming the file.")
        self.lock_button = QPushButton("Lock without opening")
        self.release_button = QPushButton("Release lock")
        self.discard_button = QPushButton("Discard changes")
        self.discard_button.setToolTip(
            "Throws away your changes to this file.\n"
            "A copy is kept in .kiln/trash first."
        )

        for button in (
            self.open_button,
            self.read_only_button,
            self.lock_button,
            self.release_button,
            self.discard_button,
        ):
            layout.addWidget(button)

        self.open_button.clicked.connect(lambda: self.open_requested.emit(self._path))
        self.read_only_button.clicked.connect(
            lambda: self.open_read_only_requested.emit(self._path)
        )
        self.lock_button.clicked.connect(lambda: self.lock_requested.emit(self._path))
        self.release_button.clicked.connect(
            lambda: self.release_requested.emit(self._path)
        )
        self.discard_button.clicked.connect(
            lambda: self.discard_requested.emit(self._path)
        )
        return box

    def _build_history(self) -> QGroupBox:
        box = QGroupBox("History")
        layout = QVBoxLayout(box)

        self.history_table = QTableWidget(0, len(HISTORY_COLUMNS))
        self.history_table.setHorizontalHeaderLabels(HISTORY_COLUMNS)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch
        )
        layout.addWidget(self.history_table)
        return box

    # -- populating ---------------------------------------------------------

    def show_file(self, entry: FileEntry | None, state: RepoState | None) -> None:
        self._path = entry.path if entry else ""

        if entry is None:
            self.name_label.setText("no file selected")
            for label in (
                self.folder_label,
                self.size_label,
                self.state_label,
                self.lock_label,
                self.disk_label,
            ):
                label.setText("")
            self._set_buttons_enabled(False)
            self.history_table.setRowCount(0)
            return

        self.name_label.setText(entry.name)
        self.folder_label.setText(entry.parent or "(repository root)")
        self.size_label.setText(describe_size(entry.size) or "—")
        self.state_label.setText(entry.status)
        self.disk_label.setText(
            "downloaded" if entry.downloaded else "NOT DOWNLOADED (LFS pointer)"
        )
        self._show_lock(entry, state)
        self._update_buttons(entry, state)

    def _show_lock(self, entry: FileEntry, state: RepoState | None) -> None:
        if state is not None and not state.locks_available:
            self.lock_label.setText("unknown — server unreachable")
            self.lock_label.setStyleSheet("")
            return

        if entry.lock is None:
            self.lock_label.setText("not locked")
            return

        who = "you" if entry.lock.is_mine else entry.lock.owner
        self.lock_label.setText(f"{who}, {entry.lock.age_description()}")

    def _update_buttons(self, entry: FileEntry, state: RepoState | None) -> None:
        can_write = state.can_write if state else True
        locks_known = state.locks_available if state else True
        held_by_other = entry.locked_by_someone_else

        self.open_button.setEnabled(can_write and not held_by_other)
        self.read_only_button.setEnabled(True)
        self.lock_button.setEnabled(
            can_write and locks_known and not held_by_other and not entry.locked_by_me
        )
        self.release_button.setEnabled(can_write and entry.locked_by_me)
        self.discard_button.setEnabled(can_write and entry.is_changed)

        if held_by_other:
            self.open_button.setToolTip(
                f"{entry.lock.owner} holds this file. Ask them to release it."
            )
        elif not can_write and state is not None:
            self.open_button.setToolTip(state.blocked_reason)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for button in (
            self.open_button,
            self.read_only_button,
            self.lock_button,
            self.release_button,
            self.discard_button,
        ):
            button.setEnabled(enabled)

    def show_history(self, commits) -> None:
        self.history_table.setRowCount(len(commits))
        for row, commit in enumerate(commits):
            when = commit.when.strftime("%Y-%m-%d") if commit.when else ""
            for column, text in enumerate([when, commit.author, commit.subject]):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setForeground(theme.COLOUR_MUTED)
                self.history_table.setItem(row, column, item)
