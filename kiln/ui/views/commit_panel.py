"""The Commit panel along the bottom of the Changes screen.

Commits are all-or-nothing on purpose: it commits everything in the list, not
whatever happens to be ticked. Picking a subset is a git idea, and an artist
who has changed four files has changed four files. The selection in the list
above is for looking at a file and for discarding, not for splitting a commit.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class CommitPanel(QGroupBox):
    commit_requested = Signal(str, bool)  # message, and_push

    def __init__(self, parent=None):
        super().__init__("Commit", parent)

        layout = QVBoxLayout(self)
        self.message = QPlainTextEdit()
        self.message.setPlaceholderText("What changed?")
        self.message.setMaximumHeight(70)
        layout.addWidget(self.message)

        buttons = QHBoxLayout()
        self.commit_button = QPushButton("Commit all changes")
        self.commit_button.clicked.connect(lambda: self._requested(and_push=False))
        self.commit_and_push_button = QPushButton("Commit and push")
        self.commit_and_push_button.clicked.connect(
            lambda: self._requested(and_push=True)
        )
        buttons.addWidget(self.commit_button)
        buttons.addWidget(self.commit_and_push_button)
        layout.addLayout(buttons)

    def _requested(self, and_push: bool) -> None:
        self.commit_requested.emit(self.message.toPlainText(), and_push)

    def clear(self) -> None:
        self.message.clear()

    def set_available(self, can_commit: bool, online: bool) -> None:
        self.commit_button.setEnabled(can_commit)
        self.commit_and_push_button.setEnabled(can_commit and online)
