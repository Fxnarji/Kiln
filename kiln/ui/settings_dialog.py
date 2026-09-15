"""Project settings.

Everything Kiln can be configured to do belongs here, so there is one place to
look rather than a preference hidden behind each screen. At the moment nothing
is configurable, so the dialog says so plainly instead of pretending to offer
choices.

Adding a setting means one call to `add_setting` in `_build_settings`; the
empty-state notice takes itself away as soon as there is a first row.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from kiln.core.repository import Repository

NOTHING_TO_CONFIGURE = (
    "Kiln has nothing to configure yet.\n\n"
    "Settings will appear here as they are added."
)


class ProjectSettingsDialog(QDialog):
    """The one window for per-project preferences."""

    def __init__(self, repository: Repository, parent=None):
        super().__init__(parent)
        self.repository = repository

        self.setWindowTitle("Project settings")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Project"))
        project = QLabel(str(repository.root))
        project.setTextInteractionFlags(Qt.TextSelectableByMouse)
        project.setWordWrap(True)
        layout.addWidget(project)

        self.settings_form = QFormLayout()
        layout.addLayout(self.settings_form)

        self.empty_notice = QLabel(NOTHING_TO_CONFIGURE)
        self.empty_notice.setWordWrap(True)
        layout.addWidget(self.empty_notice)

        self._build_settings()
        self.empty_notice.setVisible(self.settings_form.rowCount() == 0)

        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_settings(self) -> None:
        """Put the configurable options on the form.

        Deliberately empty: there are none yet. Each future setting is one
        `self.add_setting(label, widget)` here.
        """

    def add_setting(self, label: str, widget: QWidget) -> None:
        self.settings_form.addRow(label, widget)
