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

# Which rows and buttons a panel offers. Named rather than passed as widgets,
# so a window can ask for the ones its screen is about without reaching into
# the panel afterwards. The Locks screen has no use for a Discard button, and
# the Changes screen none for a lock holder.
FIELD_NAME = "name"
FIELD_FOLDER = "folder"
FIELD_SIZE = "size"
FIELD_STATE = "state"
FIELD_LOCK = "lock"
FIELD_DISK = "disk"

ALL_FIELDS = (
    FIELD_NAME,
    FIELD_FOLDER,
    FIELD_SIZE,
    FIELD_STATE,
    FIELD_LOCK,
    FIELD_DISK,
)

FIELD_LABELS = {
    FIELD_NAME: "Name",
    FIELD_FOLDER: "Folder",
    FIELD_SIZE: "Size",
    FIELD_STATE: "State",
    FIELD_LOCK: "Lock",
    FIELD_DISK: "On disk",
}

ACTION_OPEN = "open"
ACTION_READ_ONLY = "read_only"
ACTION_LOCK = "lock"
ACTION_RELEASE = "release"
ACTION_DISCARD = "discard"

ALL_ACTIONS = (
    ACTION_OPEN,
    ACTION_READ_ONLY,
    ACTION_LOCK,
    ACTION_RELEASE,
    ACTION_DISCARD,
)


class DetailPanel(QWidget):
    open_requested = Signal(str)
    open_read_only_requested = Signal(str)
    lock_requested = Signal(str)
    release_requested = Signal(str)
    discard_requested = Signal(str)

    def __init__(
        self,
        parent=None,
        fields: tuple[str, ...] = ALL_FIELDS,
        actions: tuple[str, ...] = ALL_ACTIONS,
    ):
        super().__init__(parent)
        self._path = ""
        self._fields = tuple(fields)
        self._actions = tuple(actions)

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_properties())
        # A panel can ask for no actions at all. The box is still built, so the
        # rest of this class never has to ask whether the buttons exist, but it
        # is left out of the layout rather than shown empty.
        self._actions_box = self._build_actions()
        if self._actions:
            layout.addWidget(self._actions_box)
        layout.addWidget(self._build_history(), stretch=1)

        self.show_file(None, None)

    # -- construction -------------------------------------------------------

    def _build_properties(self) -> QGroupBox:
        """Every row is built, only the requested ones are shown.

        Keeping the unshown labels alive costs nothing and means show_file does
        not have to ask which rows this particular panel happens to have.
        """
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

        rows = {
            FIELD_NAME: self.name_label,
            FIELD_FOLDER: self.folder_label,
            FIELD_SIZE: self.size_label,
            FIELD_STATE: self.state_label,
            FIELD_LOCK: self.lock_label,
            FIELD_DISK: self.disk_label,
        }
        for field in self._fields:
            form.addRow(FIELD_LABELS[field], rows[field])
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

        buttons = {
            ACTION_OPEN: self.open_button,
            ACTION_READ_ONLY: self.read_only_button,
            ACTION_LOCK: self.lock_button,
            ACTION_RELEASE: self.release_button,
            ACTION_DISCARD: self.discard_button,
        }
        for action in self._actions:
            layout.addWidget(buttons[action])

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

        # Unknown lock state disables claiming just as firmly as somebody
        # else holding the file: "we cannot tell" must not read as "it's free".
        self.open_button.setEnabled(can_write and locks_known and not held_by_other)
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
        elif not locks_known:
            self.open_button.setToolTip(
                "Kiln cannot reach the lock server, so it cannot tell whether "
                "anyone else is working on this file. Open read-only instead."
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
