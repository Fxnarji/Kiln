"""The Locks screen: every file somebody is holding.

A list and a right click menu, and nothing else. The question this screen
answers is "who has got what, and can I have it back", which the columns
already answer for every row at once — a panel describing one file at a time
would say less than the list does.
"""

from __future__ import annotations

from kiln.core.models import FileEntry, RepoState
from kiln.ui import sidebar as sidebar_module, theme
from kiln.ui.views.base import ViewWindow
from kiln.ui.views.file_list import Column, FileList
from kiln.ui.views.locks_menu import LocksMenu


def _held_since(entry: FileEntry) -> str:
    return entry.lock.age_description() if entry.lock else ""


def _holder_colour(entry: FileEntry):
    return None if entry.lock is None else theme.lock_colour(entry.lock.is_mine)


COLUMNS = (
    Column("Name", lambda entry: entry.name, width=240),
    Column("Folder", lambda entry: entry.parent, width=280),
    Column(
        "Held by",
        lambda entry: entry.lock_description(),
        width=150,
        colour=_holder_colour,
    ),
    Column("Held for", _held_since, width=120),
)


class LocksWindow(ViewWindow):
    view_name = sidebar_module.VIEW_MY_LOCKS
    title = "Locks"
    window_size = (900, 640)

    def build_list(self) -> FileList:
        return FileList(COLUMNS)

    def build_menu(self) -> LocksMenu:
        return LocksMenu(self.window, self)

    # -- contents -----------------------------------------------------------

    def entries_for(self, state: RepoState) -> list[FileEntry]:
        return [entry for entry in state.files if entry.lock is not None]

    def heading_for(self, entries: list[FileEntry]) -> str:
        mine = sum(1 for entry in entries if entry.locked_by_me)
        return f"Locks — {len(entries)} file(s), {mine} yours"
