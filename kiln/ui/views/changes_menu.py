"""The right click menu on the Changes list.

Discarding lives here rather than on a button, because it is the one action on
this screen that cannot be undone from inside Kiln, and a menu entry has to be
gone looking for. The dialog behind it still says what will happen and where
the copy goes.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMenu

from kiln.core.models import FileEntry, RepoState
from kiln.ui.views.menu import FileMenu


class ChangesMenu(FileMenu):
    def fill(self, menu: QMenu, entries: list[FileEntry], state: RepoState) -> None:
        self.add_open_actions(menu, entries, state)

        paths = [entry.path for entry in entries]
        label = (
            "Discard changes"
            if len(paths) == 1
            else f"Discard changes in {len(paths)} files"
        )
        discard = menu.addAction(
            label, lambda: self.window.discard(paths, self.view)
        )
        discard.setEnabled(state.can_write)
        if not state.can_write:
            discard.setToolTip(state.blocked_reason)
