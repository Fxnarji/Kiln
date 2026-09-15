"""The grid and table presentations used by the Browse and Changes screens."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kiln.core.models import FileEntry, describe_size
from kiln.ui import theme
from kiln.ui.thumbnails import ICON_SIZE, ThumbnailCache

PATH_ROLE = Qt.UserRole
ITEM_KIND_ROLE = Qt.UserRole + 1
COLUMNS = ["Name", "Folder", "Size", "State", "Author"]


class FileTable(QWidget):
    """A file browser with a visual grid and a detailed table presentation."""

    file_activated = Signal(str)
    folder_activated = Signal(str)
    context_menu_requested = Signal(str, object, bool, bool)
    selection_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thumbnail_cache: ThumbnailCache | None = None
        self._entries: list[FileEntry] = []
        self._folders: list[str] = []
        self._context_folder = ""

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setFlow(QListWidget.LeftToRight)
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.grid.setGridSize(QSize(170, 172))
        self.grid.setSpacing(8)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.grid.setWordWrap(True)
        self.grid.setUniformItemSizes(True)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._grid_context_menu)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        for column in range(len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 260)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 150)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_context_menu)

        self.presentations = QStackedWidget()
        self.presentations.addWidget(self.grid)
        self.presentations.addWidget(self.table)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.presentations)

        self.grid.itemDoubleClicked.connect(self._emit_grid_activated)
        self.grid.itemSelectionChanged.connect(self._emit_selection)
        self.table.itemDoubleClicked.connect(self._emit_table_activated)
        self.table.itemSelectionChanged.connect(self._emit_selection)

    def set_table_mode(self, enabled: bool) -> None:
        self.presentations.setCurrentWidget(self.table if enabled else self.grid)

    def set_context_folder(self, folder: str) -> None:
        self._context_folder = folder

    def show_files(
        self, entries: list[FileEntry], folders: list[str] | None = None
    ) -> None:
        """Replace both presentations while retaining the selected path."""
        previously_selected = self.selected_path()
        self._entries = list(entries)
        self._folders = list(folders or [])
        self._fill_grid()
        self._fill_table()
        if previously_selected:
            self.select_path(previously_selected)

    def _fill_grid(self) -> None:
        self.grid.clear()
        if self.thumbnail_cache is None:
            return
        for folder in self._folders:
            item = QListWidgetItem(self.thumbnail_cache.icon_for_folder(), folder.rsplit("/", 1)[-1])
            item.setData(PATH_ROLE, folder)
            item.setData(ITEM_KIND_ROLE, "folder")
            item.setToolTip(folder)
            self.grid.addItem(item)
        for entry in self._entries:
            item = QListWidgetItem(
                self.thumbnail_cache.icon_for(entry.path, entry.downloaded), entry.name
            )
            item.setData(PATH_ROLE, entry.path)
            item.setData(ITEM_KIND_ROLE, "file")
            item.setToolTip(self._tooltip(entry))
            if entry.lock is not None:
                item.setForeground(theme.lock_colour(entry.lock.is_mine))
            self.grid.addItem(item)

    def _fill_table(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            values = [
                entry.name,
                entry.parent,
                describe_size(entry.size),
                "" if entry.status == "clean" else entry.status,
                entry.lock_description(),
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(PATH_ROLE, entry.path)
                self._apply_table_colour(item, entry, column)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)

    @staticmethod
    def _tooltip(entry: FileEntry) -> str:
        state = entry.status if entry.status != "clean" else "clean"
        lock = entry.lock_description() or "not locked"
        download = "downloaded" if entry.downloaded else "not downloaded"
        return f"{entry.path}\n{state} | {lock} | {download}"

    @staticmethod
    def _apply_table_colour(
        item: QTableWidgetItem, entry: FileEntry, column: int
    ) -> None:
        if column == 3:
            colour = theme.status_colour(entry.status)
            if colour is not None:
                item.setForeground(colour)
        elif column == 4 and entry.lock is not None:
            item.setForeground(theme.lock_colour(entry.lock.is_mine))

    def selected_path(self) -> str:
        if self.presentations.currentWidget() is self.table:
            items = self.table.selectedItems()
        else:
            items = self.grid.selectedItems()
        return items[0].data(PATH_ROLE) if items else ""

    def select_path(self, path: str) -> None:
        for index in range(self.grid.count()):
            item = self.grid.item(index)
            if item.data(PATH_ROLE) == path:
                self.grid.setCurrentItem(item)
                break
        for index, entry in enumerate(self._entries):
            if entry.path == path:
                self.table.selectRow(index)
                break

    def _emit_grid_activated(self, item: QListWidgetItem) -> None:
        path = item.data(PATH_ROLE)
        if item.data(ITEM_KIND_ROLE) == "folder":
            self.folder_activated.emit(path)
        else:
            self.file_activated.emit(path)

    def _emit_table_activated(self, item: QTableWidgetItem) -> None:
        self.file_activated.emit(item.data(PATH_ROLE))

    def _emit_selection(self) -> None:
        self.selection_changed.emit(self.selected_path())

    def _grid_context_menu(self, position) -> None:
        item = self.grid.itemAt(position)
        target = self._context_folder
        if item is not None:
            path = item.data(PATH_ROLE)
            if item.data(ITEM_KIND_ROLE) == "folder":
                target = path
            else:
                target = path
        self.context_menu_requested.emit(
            target,
            self.grid.viewport().mapToGlobal(position),
            item is not None,
            item is not None,
        )

    def _table_context_menu(self, position) -> None:
        item = self.table.itemAt(position)
        target = self._context_folder
        if item is not None:
            target = item.data(PATH_ROLE)
        self.context_menu_requested.emit(
            target,
            self.table.viewport().mapToGlobal(position),
            item is not None,
            item is not None,
        )
