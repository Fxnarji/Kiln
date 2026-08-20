"""Whole-file conflict resolution (spec 9.7).

Two buttons per file: keep mine, keep theirs. No three-way view, no content
merging, no cleverness — for a .blend there is nothing sensible between the two
versions anyway.

The dialog states plainly that both versions are saved before anything is
overwritten, because the artist using this screen is about to throw away either
their own afternoon or someone else's, and needs to know it is recoverable.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kiln.core.conflicts import SIDE_MINE, SIDE_SERVER, ConflictedFile
from kiln.ui import theme

COLUMNS = ["File", "Folder", "Resolution", ""]

EXPLANATION = (
    "These files were changed both by you and on the server. Pick which whole "
    "version to keep for each one.\n\n"
    "Both versions are copied into .kiln/conflicts before anything is "
    "overwritten, so a wrong choice can be undone."
)


class ConflictDialog(QDialog):
    """Resolve conflicts one file at a time, then commit the merge."""

    resolve_requested = Signal(str, str)  # path, side
    complete_requested = Signal()
    abandon_requested = Signal()

    def __init__(self, conflicts: list[ConflictedFile], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Resolve conflicts")
        self.setMinimumSize(720, 420)

        self._conflicts = conflicts
        self._resolved: dict[str, str] = {}

        layout = QVBoxLayout(self)

        explanation = QLabel(EXPLANATION)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(2, 140)
        self.table.setColumnWidth(3, 260)
        layout.addWidget(self.table, stretch=1)

        self.buttons = QDialogButtonBox()
        self.complete_button = self.buttons.addButton(
            "Complete merge", QDialogButtonBox.AcceptRole
        )
        self.abandon_button = self.buttons.addButton(
            "Abandon merge", QDialogButtonBox.DestructiveRole
        )
        self.complete_button.clicked.connect(self._on_complete)
        self.abandon_button.clicked.connect(self._on_abandon)
        layout.addWidget(self.buttons)

        self._populate()

    # -- table --------------------------------------------------------------

    def _populate(self) -> None:
        self.table.setRowCount(len(self._conflicts))
        for row, conflict in enumerate(self._conflicts):
            self._fill_row(row, conflict)
        self._update_complete_button()

    def _fill_row(self, row: int, conflict: ConflictedFile) -> None:
        from pathlib import PurePosixPath

        name_item = QTableWidgetItem(conflict.name)
        folder_item = QTableWidgetItem(str(PurePosixPath(conflict.path).parent))
        self.table.setItem(row, 0, name_item)
        self.table.setItem(row, 1, folder_item)

        if conflict.resolvable:
            self.table.setItem(row, 2, QTableWidgetItem("not resolved"))
            self.table.setCellWidget(row, 3, self._build_choice_buttons(conflict.path))
        else:
            blocked = QTableWidgetItem("cannot resolve here")
            blocked.setForeground(theme.COLOUR_CONFLICTED)
            blocked.setToolTip(conflict.reason)
            self.table.setItem(row, 2, blocked)

            reason = QLabel(conflict.reason)
            reason.setWordWrap(True)
            reason.setToolTip(conflict.reason)
            self.table.setCellWidget(row, 3, reason)
            self.table.setRowHeight(row, 60)

    def _build_choice_buttons(self, path: str) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        mine = QPushButton("Keep my version")
        server = QPushButton("Keep server version")
        mine.clicked.connect(lambda: self.resolve_requested.emit(path, SIDE_MINE))
        server.clicked.connect(lambda: self.resolve_requested.emit(path, SIDE_SERVER))

        layout.addWidget(mine)
        layout.addWidget(server)
        return container

    # -- state --------------------------------------------------------------

    def mark_resolved(self, path: str, side: str) -> None:
        """Called back by the window once the resolution actually succeeded."""
        self._resolved[path] = side
        label = "kept my version" if side == SIDE_MINE else "kept server version"

        for row, conflict in enumerate(self._conflicts):
            if conflict.path != path:
                continue
            item = QTableWidgetItem(label)
            item.setForeground(theme.COLOUR_NEW)
            self.table.setItem(row, 2, item)
            self.table.removeCellWidget(row, 3)
            self.table.setItem(row, 3, QTableWidgetItem(""))

        self._update_complete_button()

    @property
    def resolutions(self) -> dict[str, str]:
        return dict(self._resolved)

    def _update_complete_button(self) -> None:
        resolvable = [c.path for c in self._conflicts if c.resolvable]
        blocked = [c for c in self._conflicts if not c.resolvable]
        everything_done = all(path in self._resolved for path in resolvable)

        self.complete_button.setEnabled(everything_done and not blocked)
        if blocked:
            self.complete_button.setToolTip(
                "Some conflicts have to be resolved outside Kiln first."
            )
        elif not everything_done:
            self.complete_button.setToolTip("Choose a version for every file first.")
        else:
            self.complete_button.setToolTip("")

    # -- buttons ------------------------------------------------------------

    def _on_complete(self) -> None:
        self.complete_requested.emit()

    def _on_abandon(self) -> None:
        confirmed = QMessageBox.question(
            self,
            "Abandon merge",
            "Undo the whole merge and put your files back the way they were?\n\n"
            "Any conflicts you already resolved will be undone. Nothing you had "
            "committed is lost.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmed == QMessageBox.Yes:
            self.abandon_requested.emit()
