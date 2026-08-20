"""The left hand navigation tree and repository shortcuts."""

from __future__ import annotations

from zlib import crc32

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kiln.core.models import Pin, RepoState
from kiln.ui import theme

KIND_ROLE = Qt.UserRole
VALUE_ROLE = Qt.UserRole + 1

KIND_PIN = "pin"
KIND_FOLDER = "folder"
KIND_VIEW = "view"

VIEW_CHANGES = "changes"
VIEW_MY_LOCKS = "locks"
VIEW_CONFLICTS = "conflicts"


def _pin_icon(path: str, color: str = "") -> QIcon:
    """Create a small stable color marker for one pinned folder."""
    marker_color = QColor(color) if color else QColor.fromHsv(
        crc32(path.encode("utf-8")) % 360, 190, 225
    )
    pixmap = QPixmap(12, 12)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(marker_color)
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(1, 1, 10, 10)
    painter.end()
    return QIcon(pixmap)


class Sidebar(QWidget):
    """A roomy folder tree with repository actions anchored at the bottom."""

    pin_opened = Signal(object)
    folder_opened = Signal(str)
    view_opened = Signal(str)
    context_menu_requested = Signal(str, object, bool, bool)
    pin_requested = Signal(str)
    unpin_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        self.tree.setColumnWidth(1, 52)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemClicked.connect(self._on_clicked)
        self.tree.setStyleSheet(
            "QTreeWidget::item { min-height: 30px; padding: 4px; }"
        )
        self.tree.setIconSize(QPixmap(12, 12).size())
        layout.addWidget(self.tree, stretch=1)

        buttons = QVBoxLayout()
        buttons.setContentsMargins(8, 6, 8, 6)
        buttons.setSpacing(6)
        changes_button = QPushButton("My changes")
        locks_button = QPushButton("Locks")
        for button in (changes_button, locks_button):
            button.setMinimumHeight(44)
            button.setStyleSheet("font-weight: 600;")
        changes_button.clicked.connect(lambda: self.view_opened.emit(VIEW_CHANGES))
        locks_button.clicked.connect(lambda: self.view_opened.emit(VIEW_MY_LOCKS))
        buttons.addWidget(changes_button)
        buttons.addWidget(locks_button)
        layout.addLayout(buttons)
        self._pins: list[Pin] = []

    def refresh(self, state: RepoState, pins: list[Pin], folders: list[str]) -> None:
        """Rebuild the tree, remembering which folders were expanded."""
        expanded = self._expanded_folders()
        self._pins = pins
        self.tree.clear()
        self._build_pins(pins)
        self._build_folders(folders, expanded)

    def _build_pins(self, pins: list[Pin]) -> None:
        root = self._section("PINNED FOLDERS")
        if not pins:
            hint = QTreeWidgetItem(root, ["right-click a folder below to pin it"])
            hint.setForeground(0, theme.COLOUR_MUTED)
            hint.setFlags(Qt.ItemIsEnabled)
        for pin in pins:
            depth = "ALL" if pin.depth >= 5 else f"D{pin.depth}"
            item = QTreeWidgetItem(root, [pin.display_label(), f"[{depth}]"])
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            item.setIcon(0, _pin_icon(pin.path, pin.color))
            item.setData(0, KIND_ROLE, KIND_PIN)
            item.setData(0, VALUE_ROLE, pin.path)
        root.setExpanded(True)

    def _build_folders(self, folders: list[str], expanded: set[str]) -> None:
        root = self._section("WORKING TREE")
        pinned_paths = {pin.path for pin in self._pins}
        nodes: dict[str, QTreeWidgetItem] = {"": root}

        for folder in folders:
            parent_path, _, name = folder.rpartition("/")
            parent = nodes.get(parent_path, root)
            item = QTreeWidgetItem(parent, [name])
            item.setData(0, KIND_ROLE, KIND_FOLDER)
            item.setData(0, VALUE_ROLE, folder)
            if folder in pinned_paths:
                item.setForeground(0, theme.COLOUR_LOCKED_BY_ME)
            item.setExpanded(folder in expanded)
            nodes[folder] = item

        root.setExpanded(True)

    def _section(self, title: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self.tree, [title])
        item.setFirstColumnSpanned(True)
        item.setForeground(0, theme.COLOUR_MUTED)
        item.setFlags(Qt.ItemIsEnabled)
        return item

    def _on_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        kind = item.data(0, KIND_ROLE)
        value = item.data(0, VALUE_ROLE)
        if kind == KIND_PIN:
            for pin in self._pins:
                if pin.path == value:
                    self.pin_opened.emit(pin)
                    return
        elif kind == KIND_FOLDER:
            self.folder_opened.emit(value)
        elif kind == KIND_VIEW:
            self.view_opened.emit(value)

    def _show_context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            target = ""
            allow_delete = False
        elif item.data(0, KIND_ROLE) in (KIND_FOLDER, KIND_PIN):
            target = item.data(0, VALUE_ROLE)
            allow_delete = item.data(0, KIND_ROLE) == KIND_FOLDER
        elif item.text(0) == "WORKING TREE":
            target = ""
            allow_delete = False
        else:
            return
        self.context_menu_requested.emit(
            target,
            self.tree.viewport().mapToGlobal(position),
            item is not None,
            allow_delete,
        )

    def _expanded_folders(self) -> set[str]:
        expanded: set[str] = set()

        def walk(item: QTreeWidgetItem) -> None:
            for index in range(item.childCount()):
                child = item.child(index)
                if child.isExpanded() and child.data(0, KIND_ROLE) == KIND_FOLDER:
                    expanded.add(child.data(0, VALUE_ROLE))
                walk(child)

        walk(self.tree.invisibleRootItem())
        return expanded
