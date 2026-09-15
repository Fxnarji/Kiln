"""What a right click menu on a list of files has in common.

Each screen writes its own menu, because what you can do with a file depends on
why it is in front of you. The two things that do not depend on that are here:
opening a file, and the rule that a menu never offers an action against a
selection it would not really apply to.

Opening is offered for one file at a time on purpose. Opening claims the lock,
and claiming eleven files because eleven rows happened to be dragged over is
not something an artist can undo in one gesture.
"""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMenu

from kiln.core.models import FileEntry, RepoState


class FileMenu(QObject):
    """Builds and shows the right click menu for the chosen files."""

    def __init__(self, window, view):
        super().__init__(view)
        #: The main window, which owns every action against the repository.
        self.window = window
        #: The screen the menu belongs to, and the parent for its dialogs.
        self.view = view

    def popup(
        self, position, entries: list[FileEntry], state: RepoState
    ) -> None:
        if not entries:
            return

        menu = QMenu(self.view)
        self.fill(menu, entries, state)
        if not menu.isEmpty():
            menu.exec(position)

    def fill(self, menu: QMenu, entries: list[FileEntry], state: RepoState) -> None:
        """Put this screen's actions on the menu."""
        raise NotImplementedError

    # -- shared entries -----------------------------------------------------

    def add_open_actions(
        self, menu: QMenu, entries: list[FileEntry], state: RepoState
    ) -> None:
        """Open, for a single file. Nothing at all for a wider selection."""
        if len(entries) != 1:
            return

        entry = entries[0]
        held_by_other = entry.locked_by_someone_else

        # Unknown lock state disables claiming just as firmly as somebody else
        # holding the file: "we cannot tell" must not read as "it is free".
        opening = menu.addAction(
            "Open for editing",
            lambda: self.window.open_for_editing(entry.path),
        )
        opening.setEnabled(
            state.can_write and state.locks_available and not held_by_other
        )
        if held_by_other:
            opening.setToolTip(f"{entry.lock.owner} holds this file.")

        menu.addAction(
            "Open read-only",
            lambda: self.window.open_read_only(entry.path),
        )
        menu.addSeparator()
