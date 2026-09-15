"""The panel down the right of the Changes screen.

Properties and history, and no buttons. Everything you can do to a changed file
is on the right click menu, where it applies to the whole selection — a button
here could only ever mean the one file, which is a different promise from the
one the list is making once several rows are ticked.

What it shows is what the screen is about: the state of a file, not who is
holding it. Nothing is in this list because of a lock.
"""

from __future__ import annotations

from kiln.core.models import FileEntry, RepoState
from kiln.ui import detail_panel as panel
from kiln.ui.detail_panel import DetailPanel

FIELDS = (
    panel.FIELD_NAME,
    panel.FIELD_FOLDER,
    panel.FIELD_SIZE,
    panel.FIELD_STATE,
    panel.FIELD_DISK,
)


class ChangesDetails(DetailPanel):
    def __init__(self, parent=None):
        super().__init__(parent, fields=FIELDS, actions=())

    def show_selection(
        self, entries: list[FileEntry], state: RepoState
    ) -> FileEntry | None:
        """Describe the one chosen file, or say how many there are.

        Returns the file worth reading history for, which is only ever the one
        that is on its own.
        """
        if len(entries) == 1:
            self.show_file(entries[0], state)
            return entries[0]

        self.show_file(None, state)
        if entries:
            self.name_label.setText(f"{len(entries)} files selected")
        return None
