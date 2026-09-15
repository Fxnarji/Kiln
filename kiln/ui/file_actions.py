"""Creating and deleting files and folders, with an undo window.

Deleting moves the item to .kiln/trash rather than removing it, and puts an
Undo bar on screen for ten seconds. The bar belongs to this controller; the
window only decides where to put it in the layout.

Like MergeController, this borrows the window for the repository, the job
runner, and a dialog parent.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QWidget,
)

from kiln.ui import dialogs

UNDO_WINDOW_MS = 10_000

INVALID_NAME_MESSAGE = "Use a single file or folder name without path separators."


class UndoBar(QWidget):
    """A strip offering to undo the last delete.

    A self-contained widget like Sidebar and DetailPanel, so the window can
    place it without knowing anything about deletes, and the controller can
    drive it without owning raw layout code.
    """

    undo_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        self._message = QLabel()
        layout.addWidget(self._message)
        layout.addStretch()

        undo_button = QPushButton("Undo")
        undo_button.clicked.connect(self.undo_clicked)
        layout.addWidget(undo_button)

        self.hide()

    def show_message(self, text: str) -> None:
        self._message.setText(text)
        self.show()


class FileActionsController(QObject):
    """The add/delete context menu, and the undo bar that follows a delete."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._deleted_item = None

        self.undo_bar = UndoBar()
        self.undo_bar.undo_clicked.connect(self.undo_delete)

        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.timeout.connect(self._expire_undo)

    # -- context menu -------------------------------------------------------

    def show_menu(
        self,
        target: str,
        global_position,
        item_selected: bool = True,
        allow_delete: bool = True,
    ) -> None:
        """The right-click menu, shared by the sidebar and the file grid."""
        parent_folder = self._parent_folder(target)
        menu = QMenu(self.window)

        menu.addAction("Add folder", lambda: self.add_folder(parent_folder))
        self._add_template_menu(menu, parent_folder)

        menu.addSeparator()
        self._add_pin_actions(menu, parent_folder)

        if allow_delete and item_selected and self._exists(target):
            menu.addAction("Delete", lambda: self.delete(target))

        menu.exec(global_position)

    def _add_template_menu(self, menu: QMenu, parent_folder: str) -> None:
        templates = self.window.repository.templates()
        file_menu = menu.addMenu("Add file")

        if not templates:
            file_menu.setEnabled(False)
            file_menu.setTitle("Add file (no templates found)")
            return

        for suffix, paths in templates.items():
            type_menu = file_menu.addMenu(f"{suffix.lstrip('.').upper()} files")
            for template in paths:
                type_menu.addAction(
                    template.name,
                    lambda path=template: self.add_from_template(parent_folder, path),
                )

    def _add_pin_actions(self, menu: QMenu, folder: str) -> None:
        already_pinned = any(
            pin.path == folder for pin in self.window.repository.pins()
        )
        if already_pinned:
            menu.addAction("Edit pin...", lambda: self.window.edit_pin(folder))
            menu.addAction("Remove pin", lambda: self.window.remove_pin(folder))
        else:
            menu.addAction("Pin this folder...", lambda: self.window.edit_pin(folder))

    def _parent_folder(self, target: str) -> str:
        """A file's containing folder; a folder is its own parent for adding."""
        state = self.window.state
        entry = state.find(target) if state is not None else None
        return entry.parent if entry is not None else target

    def _exists(self, target: str) -> bool:
        return bool(target) and (self.window.repository.root / target).exists()

    # -- creating -----------------------------------------------------------

    def add_folder(self, parent_folder: str) -> None:
        name, accepted = QInputDialog.getText(self.window, "Add folder", "Folder name:")
        if not accepted:
            return

        path = self._child_path(parent_folder, name)
        if path is None:
            return

        self.window.run_job(
            f"creating {path}",
            lambda: self.window.repository.add_folder(path),
            error_context="Could not add the folder",
        )

    def add_from_template(self, parent_folder: str, template: Path) -> None:
        extension = template.suffix.lstrip(".").upper()
        name, accepted = QInputDialog.getText(
            self.window,
            "Add file",
            f"Name for the new {extension} file:",
            text=template.stem,
        )
        if not accepted:
            return

        name = name.strip()
        if name and not Path(name).suffix:
            name += template.suffix

        path = self._child_path(parent_folder, name)
        if path is None:
            return

        self.window.run_job(
            f"adding {path}",
            lambda: self.window.repository.add_from_template(template, path),
            error_context="Could not add the file",
        )

    def _child_path(self, parent_folder: str, name: str) -> str | None:
        """Validate a new name and turn it into a repo-relative path."""
        name = name.strip()
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            QMessageBox.warning(self.window, "Invalid name", INVALID_NAME_MESSAGE)
            return None
        return f"{parent_folder}/{name}" if parent_folder else name

    # -- deleting -----------------------------------------------------------

    def delete(self, path: str) -> None:
        kind = "folder" if (self.window.repository.root / path).is_dir() else "file"
        if not dialogs.confirm(
            self.window,
            f"Delete {kind}",
            f"Move {path} to Kiln trash?\n\n"
            "You will have ten seconds to undo this.",
        ):
            return

        self.window.run_job(
            f"deleting {path}",
            lambda: self.window.repository.delete_to_trash(path),
            on_done=self._deleted,
            error_context="Could not delete",
        )

    def _deleted(self, deleted) -> None:
        self._deleted_item = deleted
        self.undo_bar.show_message(
            f"Moved {deleted.repo_relative_path} to Kiln trash"
        )
        self._undo_timer.start(UNDO_WINDOW_MS)
        self.window.refresh(with_locks=True)

    def undo_delete(self) -> None:
        deleted = self._deleted_item
        if deleted is None:
            return

        self._expire_undo()
        self.window.run_job(
            f"restoring {deleted.repo_relative_path}",
            lambda: self.window.repository.undo_delete(deleted),
            error_context="Could not undo the delete",
        )

    def _expire_undo(self) -> None:
        """Hide the bar and forget the item, whether it was undone or not.

        The file stays in .kiln/trash regardless — the window closing only
        means the one-click undo is gone, not the copy.
        """
        self._undo_timer.stop()
        self._deleted_item = None
        self.undo_bar.hide()
