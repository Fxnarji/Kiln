"""The Changes screen: everything modified since the last commit.

Four pieces, each from its own module — the list, the menu that acts on it, the
details down the right, and the commit box along the bottom. This file is only
the choice of which four, and the columns the list is made of.
"""

from __future__ import annotations

from kiln.core.models import FileEntry, RepoState, describe_size
from kiln.ui import sidebar as sidebar_module, theme
from kiln.ui.views.base import ViewWindow
from kiln.ui.views.changes_details import ChangesDetails
from kiln.ui.views.changes_menu import ChangesMenu
from kiln.ui.views.commit_panel import CommitPanel
from kiln.ui.views.file_list import Column, FileList

COLUMNS = (
    Column("Name", lambda entry: entry.name, width=240),
    Column("Folder", lambda entry: entry.parent, width=280),
    Column("Size", lambda entry: describe_size(entry.size), width=90),
    Column(
        "State",
        lambda entry: entry.status,
        width=110,
        colour=lambda entry: theme.status_colour(entry.status),
    ),
)


class ChangesWindow(ViewWindow):
    view_name = sidebar_module.VIEW_CHANGES
    title = "My changes"

    def build_list(self) -> FileList:
        return FileList(COLUMNS)

    def build_menu(self) -> ChangesMenu:
        return ChangesMenu(self.window, self)

    def build_side_panel(self) -> ChangesDetails:
        self.details = ChangesDetails()
        return self.details

    def build_bottom_panel(self) -> CommitPanel:
        self.commit_panel = CommitPanel()
        self.commit_panel.commit_requested.connect(self._commit)
        return self.commit_panel

    # -- contents -----------------------------------------------------------

    def entries_for(self, state: RepoState) -> list[FileEntry]:
        return state.changed_files

    def heading_for(self, entries: list[FileEntry]) -> str:
        return f"Changes — {len(entries)} file(s)"

    def selection_shown(self, entries: list[FileEntry], state: RepoState) -> None:
        self.show_history_in(self.details, self.details.show_selection(entries, state))

    def state_applied(self, state: RepoState) -> None:
        self.commit_panel.set_available(
            bool(state.changed_files) and state.can_write, state.online
        )

    # -- committing ---------------------------------------------------------

    def _commit(self, message: str, and_push: bool) -> None:
        self.window.commit(
            message,
            and_push=and_push,
            dialog_parent=self,
            on_success=self.commit_panel.clear,
        )
