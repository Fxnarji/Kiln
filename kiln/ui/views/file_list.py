"""The multi-select file list the Changes and Locks screens are built on.

One table, driven by whatever columns the screen asks for, with two ways to
pick more than one file:

  * drag, shift-click, or ctrl-click the rows, as in any file browser
  * tick the boxes in the first column

They are not two separate ideas. The boxes and the highlight are kept in step
with each other, so there is only ever one answer to "which files is this about
to act on", however the artist arrived at it. Anything else means a menu that
acts on files the artist cannot see it is acting on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from kiln.core.models import FileEntry

PATH_ROLE = Qt.UserRole


@dataclass(frozen=True)
class Column:
    """One column of the list: a heading, and how to fill it from a file."""

    title: str
    value: Callable[[FileEntry], str]
    width: int = 140
    colour: Callable[[FileEntry], QColor | None] | None = None


class FileList(QTableWidget):
    """A sortable, multi-selectable table of files."""

    selection_changed = Signal(list)  # list[str] of repo-relative paths
    file_activated = Signal(str)
    menu_requested = Signal(object)  # global position; ask for the selection

    def __init__(self, columns: tuple[Column, ...], parent=None):
        super().__init__(0, len(columns), parent)
        self._columns = columns
        self._entries: list[FileEntry] = []
        # Guards the check-box/selection sync against re-entering itself, and
        # keeps both quiet while the table is being refilled.
        self._syncing = False

        self.setHorizontalHeaderLabels([column.title for column in columns])
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)
        self.verticalHeader().setVisible(False)
        self.setContextMenuPolicy(Qt.CustomContextMenu)

        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        for index, column in enumerate(columns):
            header.setSectionResizeMode(index, QHeaderView.Interactive)
            self.setColumnWidth(index, column.width)

        self.customContextMenuRequested.connect(self._request_menu)
        self.itemDoubleClicked.connect(self._activate)
        self.itemChanged.connect(self._check_box_toggled)
        self.itemSelectionChanged.connect(self._selection_moved)

    # -- filling ------------------------------------------------------------

    def show_files(self, entries: list[FileEntry]) -> None:
        """Replace the contents, keeping whatever is still there selected."""
        chosen = set(self.selected_paths())
        self._entries = list(entries)

        self._syncing = True
        self.setSortingEnabled(False)
        self.clearSelection()
        self.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            for index, column in enumerate(self._columns):
                self.setItem(row, index, self._cell(entry, column, index))
        self.setSortingEnabled(True)
        self._syncing = False

        self._select_paths(chosen & {entry.path for entry in entries})

    def _cell(self, entry: FileEntry, column: Column, index: int) -> QTableWidgetItem:
        item = QTableWidgetItem(column.value(entry))
        item.setData(PATH_ROLE, entry.path)
        if column.colour is not None:
            colour = column.colour(entry)
            if colour is not None:
                item.setForeground(colour)
        if index == 0:
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
        return item

    # -- the selection ------------------------------------------------------

    def selected_paths(self) -> list[str]:
        """Every chosen file, in the order the rows are currently shown."""
        return [
            self.item(row, 0).data(PATH_ROLE)
            for row in range(self.rowCount())
            if self.selectionModel().isRowSelected(row)
        ]

    def selected_entries(self) -> list[FileEntry]:
        chosen = set(self.selected_paths())
        return [entry for entry in self._entries if entry.path in chosen]

    def _select_paths(self, paths: set[str]) -> None:
        if not paths:
            self._selection_moved()
            return
        for row in range(self.rowCount()):
            if self.item(row, 0).data(PATH_ROLE) in paths:
                self._set_row_selected(row, True)

    def _set_row_selected(self, row: int, selected: bool) -> None:
        flag = (
            QItemSelectionModel.Select if selected else QItemSelectionModel.Deselect
        )
        span = QItemSelection(
            self.model().index(row, 0),
            self.model().index(row, self.columnCount() - 1),
        )
        self.selectionModel().select(span, flag | QItemSelectionModel.Rows)

    # -- keeping the boxes and the highlight in step ------------------------

    def _check_box_toggled(self, item: QTableWidgetItem) -> None:
        if self._syncing or item.column() != 0:
            return
        self._syncing = True
        self._set_row_selected(item.row(), item.checkState() == Qt.Checked)
        self._syncing = False
        self._selection_moved()

    def _selection_moved(self) -> None:
        """Redraw the boxes from the selection, then say what is chosen."""
        if self._syncing:
            return
        self._syncing = True
        for row in range(self.rowCount()):
            box = self.item(row, 0)
            if box is None:
                continue
            selected = self.selectionModel().isRowSelected(row)
            box.setCheckState(Qt.Checked if selected else Qt.Unchecked)
        self._syncing = False
        self.selection_changed.emit(self.selected_paths())

    # -- events -------------------------------------------------------------

    def _activate(self, item: QTableWidgetItem) -> None:
        self.file_activated.emit(item.data(PATH_ROLE))

    def _request_menu(self, position) -> None:
        item = self.itemAt(position)
        # Right-clicking outside the current selection moves to that row first,
        # which is what every file browser does and what stops a menu acting on
        # files somewhere else in the list.
        if item is not None and not self.selectionModel().isRowSelected(item.row()):
            self.clearSelection()
            self._set_row_selected(item.row(), True)
            self._selection_moved()
        self.menu_requested.emit(self.viewport().mapToGlobal(position))
