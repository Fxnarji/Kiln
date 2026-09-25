"""Check for updates, and the dialog that downloads one and restarts Kiln.

The network never touches the git worker: a slow or unreachable GitHub must not
hold up a pull. Each check or download runs on its own short-lived thread and
reports back through a Qt signal, which Qt delivers on the UI thread.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from kiln import build_info, installer, update
from kiln.build_info import BuildInfo

log = logging.getLogger(__name__)


class Background(QObject):
    """Run one function off the UI thread and hand its outcome back to it."""

    succeeded = Signal(object)
    failed = Signal(object)  # Exception
    progressed = Signal(int, int)

    def run(self, function: Callable[[], Any]) -> None:
        def target() -> None:
            try:
                value = function()
            except Exception as error:  # noqa: BLE001 - reported, never swallowed
                self._emit(self.failed, error)
            else:
                self._emit(self.succeeded, value)

        threading.Thread(target=target, name="kiln-update", daemon=True).start()

    def report_progress(self, done: int, total: int) -> None:
        self._emit(self.progressed, done, total)

    @staticmethod
    def _emit(signal, *values) -> None:
        # The dialog may have been closed and deleted while the thread ran, and
        # there is nobody left to tell.
        try:
            signal.emit(*values)
        except RuntimeError:
            pass


def describe(build: BuildInfo) -> str:
    """ "build 42 of main, from 1a2b3c4 (2026-09-20)" """
    text = f"build {build.build} of {build.channel}"
    if build.commit:
        text += f", from {build.commit[:7]}"
    if build.built_at:
        text += f" ({build.built_at[:10]})"
    return text


class UpdateDialog(QDialog):
    """One window from "is there anything newer?" to "restarting now".

    `busy_reason` is asked just before restarting; it returns why Kiln cannot
    quit right now (a push still running, say), or "" if it can.
    """

    def __init__(
        self,
        parent=None,
        release: update.Release | None = None,
        busy_reason: Callable[[], str] = lambda: "",
        current: BuildInfo | None = None,
        installation: installer.Installation | None = None,
    ):
        super().__init__(parent)
        self.current = current or build_info.load()
        self.installation = (
            installation if installation is not None else installer.current_installation()
        )
        self.release: update.Release | None = None
        self.busy_reason = busy_reason
        self._cancel = threading.Event()
        self._staged = None  # the downloaded build, once it is ready
        self._task: Background | None = None

        self.setWindowTitle("Update Kiln")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.message)

        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        layout.addStretch(1)

        self.buttons = QDialogButtonBox()
        self.install_button = QPushButton("Download and restart")
        self.install_button.clicked.connect(self._install)
        self.retry_button = QPushButton("Check again")
        self.retry_button.clicked.connect(self.check)
        self.page_button = QPushButton("Open download page")
        self.page_button.clicked.connect(self._open_page)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        for button in (self.install_button, self.retry_button, self.page_button):
            self.buttons.addButton(button, QDialogButtonBox.ActionRole)
        self.buttons.addButton(self.close_button, QDialogButtonBox.RejectRole)
        layout.addWidget(self.buttons)

        if not self.current.is_ci_build:
            self._show(
                f"This is a development build of Kiln ({self.current.label}).\n\n"
                "Only builds made by GitHub Actions can update themselves. Pull "
                "and run from source as usual, or download a build from GitHub."
            )
        elif release is not None:
            self._offer(release)
        else:
            self.check()

    # -- checking -----------------------------------------------------------

    def check(self) -> None:
        self._show("Checking for updates...", busy=True)
        current = self.current
        self._start(lambda: update.find_update(current), self._offer, self._check_failed)

    def _offer(self, release: update.Release | None) -> None:
        if release is None:
            self._show(f"Kiln is up to date.\n\nYou have {describe(self.current)}.")
            return

        self.release = release
        text = (
            f"A newer Kiln is available: {describe(release.build)}.\n"
            f"You have {describe(self.current)}."
        )
        reason = installer.unavailable_reason(self.installation)
        if not reason and release.file(self.installation.artifact_kind) is None:
            reason = "This build was published without the file this copy of Kiln updates from."
        if reason:
            self._show(f"{text}\n\n{reason}", page=True)
        else:
            self._show(
                f"{text}\n\nKiln will download it, close, and start again. "
                "Your project is not touched.",
                install=True,
            )

    def _check_failed(self, error: Exception) -> None:
        log.warning("update check failed: %s", error)
        self._show(f"Could not check for updates.\n\n{error}", retry=True, page=True)

    # -- downloading and restarting -----------------------------------------

    def _install(self) -> None:
        if self._staged is not None:
            self._restart(self._staged)
            return
        reason = self.busy_reason()
        if reason:
            self._show(f"{reason}\n\nTry again once it has finished.", install=True)
            return
        self._download()

    def _download(self) -> None:
        release, installation = self.release, self.installation
        item = release.file(installation.artifact_kind)
        self._cancel.clear()
        self._show(f"Downloading {item.name}...", busy=True, cancel=True)
        self.progress.setRange(0, max(item.size, 1))

        task = Background(self)
        task.progressed.connect(lambda done, _total: self.progress.setValue(done))
        cancelled = self._cancel

        def work():
            installer.clear_staging(installation)
            downloaded = update.download(
                item,
                installation.download_path(item.name),
                progress=task.report_progress,
                cancelled=cancelled,
            )
            return installer.prepare(installation, downloaded)

        self._start(work, self._restart, self._download_failed, task)

    def _download_failed(self, error: Exception) -> None:
        installer.clear_staging(self.installation)
        if isinstance(error, update.DownloadCancelled):
            self._offer(self.release)
            return
        log.warning("update download failed: %s", error)
        self._show(f"The update could not be downloaded.\n\n{error}", install=True, page=True)
        self.install_button.setText("Try again")

    def _restart(self, staged) -> None:
        self._staged = staged
        reason = self.busy_reason()
        if reason:
            self._show(
                f"The update is downloaded, but {reason[0].lower()}{reason[1:]}\n\n"
                "Restart once it has finished.",
                install=True,
            )
            self.install_button.setText("Restart now")
            return
        try:
            installer.launch_replacement(self.installation, staged)
        except installer.InstallError as error:
            self._staged = None
            installer.clear_staging(self.installation)
            self._show(str(error), page=True)
            return

        log.info("restarting into %s", describe(self.release.build))
        self._show("Restarting Kiln...")
        QApplication.closeAllWindows()
        QApplication.quit()

    # -- plumbing -----------------------------------------------------------

    def _start(self, work, on_success, on_failure, task: Background | None = None) -> None:
        task = task or Background(self)
        task.succeeded.connect(on_success)
        task.failed.connect(on_failure)
        self._task = task
        task.run(work)

    def _show(
        self,
        text: str,
        busy: bool = False,
        install: bool = False,
        retry: bool = False,
        page: bool = False,
        cancel: bool = False,
    ) -> None:
        self.message.setText(text)
        self.progress.setVisible(busy)
        self.progress.setRange(0, 0)  # an indeterminate bar until told the size
        self.install_button.setText("Download and restart")
        self.install_button.setVisible(install)
        self.retry_button.setVisible(retry)
        self.page_button.setVisible(page or not self.current.is_ci_build)
        self.close_button.setText("Cancel" if cancel else "Close")

    def _open_page(self) -> None:
        channel = self.current.channel if self.current.is_ci_build else "main"
        QDesktopServices.openUrl(QUrl(update.release_url(channel)))

    def reject(self) -> None:
        """Close, or during a download, cancel it and go back to the offer."""
        if self.close_button.text() == "Cancel":
            self._cancel.set()
            return
        super().reject()
