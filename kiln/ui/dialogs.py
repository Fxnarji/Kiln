"""Error reporting and the diagnostics panel (spec 12).

Every failure an artist sees comes with a Copy diagnostics button. That one
button is what turns "Kiln broke" into a pasteable transcript, which matters a
great deal when there is one maintainer.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from kiln.errors import (
    AuthenticationError,
    GitCommandError,
    LockRefusedError,
    RepositoryBusyError,
)
from kiln.git.runner import GitRunner


def show_error(parent, error: Exception, context: str = "") -> None:
    """Report a failure, with the underlying command available on request."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(_title_for(error))
    box.setText(context or _summary_for(error))
    box.setInformativeText(_detail_for(error))

    copy_button = box.addButton("Copy diagnostics", QMessageBox.ActionRole)
    box.addButton(QMessageBox.Ok)
    box.exec()

    if box.clickedButton() is copy_button:
        QApplication.clipboard().setText(_diagnostics_for(error, context))


def show_lock_refused(parent, error: LockRefusedError) -> bool:
    """The locked-by-someone-else modal (spec 9.6).

    Returns True if the artist chose to open the file read-only. There is no
    steal option: the way out of this is a conversation.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle("File is locked")
    box.setText(f"{error.path}\n\nis locked by {error.holder or 'another artist'}.")
    box.setInformativeText(
        "Ask them to release it when they are done. You can still open it to "
        "look at, but not to edit."
    )

    read_only_button = box.addButton("Open read-only", QMessageBox.AcceptRole)
    copy_button = box.addButton("Copy details", QMessageBox.ActionRole)
    box.addButton("Cancel", QMessageBox.RejectRole)
    box.exec()

    if box.clickedButton() is copy_button:
        QApplication.clipboard().setText(
            f"{error.path} is locked by {error.holder or 'another artist'}"
        )
        return False
    return box.clickedButton() is read_only_button


def confirm(parent, title: str, question: str) -> bool:
    answer = QMessageBox.question(
        parent, title, question, QMessageBox.Yes | QMessageBox.No, QMessageBox.No
    )
    return answer == QMessageBox.Yes


class DiagnosticsDialog(QDialog):
    """The last commands Kiln ran, verbatim."""

    def __init__(self, runner: GitRunner, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recent git commands")
        self.setMinimumSize(760, 460)

        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit(_format_history(runner))
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.text)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        copy_button = buttons.addButton("Copy all", QDialogButtonBox.ActionRole)
        copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.text.toPlainText())
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def _format_history(runner: GitRunner) -> str:
    blocks = []
    for result in reversed(runner.recent_commands()):
        block = [
            f"$ {' '.join(result.argv)}",
            f"  exit {result.returncode} in {result.duration_seconds:.2f}s",
        ]
        if result.stderr.strip():
            block.append(f"  stderr: {result.stderr.strip()[:2000]}")
        blocks.append("\n".join(block))
    return "\n\n".join(blocks) or "Nothing has run yet."


def _title_for(error: Exception) -> str:
    if isinstance(error, AuthenticationError):
        return "Cannot sign in to the server"
    if isinstance(error, RepositoryBusyError):
        return "Kiln cannot make changes right now"
    if isinstance(error, GitCommandError):
        return "A git command failed"
    return "Something went wrong"


def _summary_for(error: Exception) -> str:
    if isinstance(error, AuthenticationError):
        return error.advice()
    if isinstance(error, RepositoryBusyError):
        return error.condition
    return str(error).splitlines()[0] if str(error) else error.__class__.__name__


def _detail_for(error: Exception) -> str:
    if isinstance(error, AuthenticationError):
        return ""  # the advice above is the whole message; git's wording adds noise
    if isinstance(error, GitCommandError):
        return (error.stderr or error.stdout).strip()[:1000]
    return ""


def _diagnostics_for(error: Exception, context: str) -> str:
    parts = [f"context: {context}"] if context else []
    if isinstance(error, GitCommandError):
        parts.append(error.diagnostics())
    else:
        parts.append(f"{error.__class__.__name__}: {error}")
    return "\n".join(parts)
