"""The right click menu on the Locks list.

This is the whole of the Locks screen's interface: the list says who is holding
what, and everything you can do about it is here. Releasing never forces, so
only your own locks can be released — a selection that includes somebody else's
file releases the part of it that is yours, and the entry says so rather than
quietly doing less than it offered.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMenu

from kiln.core.models import FileEntry, RepoState
from kiln.ui.views.menu import FileMenu


class LocksMenu(FileMenu):
    def fill(self, menu: QMenu, entries: list[FileEntry], state: RepoState) -> None:
        self.add_open_actions(menu, entries, state)

        mine = [entry.path for entry in entries if entry.locked_by_me]
        release = menu.addAction(
            self._label(mine, entries),
            lambda: self.window.release_locks(mine, self.view),
        )
        release.setEnabled(state.can_write and bool(mine))

        if not mine:
            release.setToolTip(
                "Only the artist holding a file can release it. Ask them to "
                "close it."
            )
        elif not state.can_write:
            release.setToolTip(state.blocked_reason)

    @staticmethod
    def _label(mine: list[str], entries: list[FileEntry]) -> str:
        if not mine:
            return "Release lock"
        if len(mine) == len(entries):
            return "Release lock" if len(mine) == 1 else f"Release {len(mine)} locks"
        noun = "lock" if len(mine) == 1 else "locks"
        return f"Release my {noun} ({len(mine)} of {len(entries)})"
