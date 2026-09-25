"""Application entry point.

    python -m kiln.ui.app [path-inside-a-repository]

With no path, Kiln asks for a folder.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from kiln import build_info, installer
from kiln.core.repository import Repository
from kiln.core.settings import load_last_project, log_file_path, save_last_project
from kiln.errors import KilnError
from kiln.git.runner import find_repository_root, tool_versions
from kiln.ui.main_window import MainWindow
from kiln.ui.theme import apply_dark_theme

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOG_MAX_BYTES = 2_000_000


def configure_logging() -> None:
    """Set up file logging, without ever preventing the application starting.

    A missing APPDATA, a read-only config directory, or a log file held open by
    something else must not stop an artist opening their project. Logging is a
    convenience for the maintainer; the application is the point.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    try:
        handler = RotatingFileHandler(
            log_file_path(), maxBytes=LOG_MAX_BYTES, backupCount=3, encoding="utf-8"
        )
    except OSError as error:
        fallback = logging.StreamHandler()
        fallback.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(fallback)
        root.warning("could not open the log file (%s); logging to stderr", error)
        return

    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)


def check_tools() -> str:
    """Refuse to start without the binaries Kiln is a front end for."""
    versions = tool_versions()
    missing = [name for name, value in versions.items() if not value]
    if missing:
        return (
            f"Kiln needs {' and '.join(missing)} on your PATH.\n\n"
            "Install Git and Git LFS, then start Kiln again."
        )
    logging.info("tools: %s", versions)
    return ""


def choose_repository(argv: list[str]) -> Path | None:
    if len(argv) > 1:
        return Path(argv[1])

    last_project = load_last_project()
    if last_project is not None:
        return last_project

    chosen = QFileDialog.getExistingDirectory(None, "Open a project folder")
    return Path(chosen) if chosen else None


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    configure_logging()
    logging.info("Kiln %s starting", build_info.load().label)

    application = QApplication(argv)
    application.setApplicationName("Kiln")
    apply_dark_theme(application)

    problem = check_tools()
    if problem:
        QMessageBox.critical(None, "Kiln cannot start", problem)
        return 1

    chosen = choose_repository(argv)
    if chosen is None:
        return 0
    # Resolved before leaving the working directory it may be relative to.
    chosen = chosen.resolve()
    installer.leave_installation_folder()

    if find_repository_root(chosen) is None:
        QMessageBox.critical(
            None,
            "Not a project folder",
            f"{chosen}\n\nis not inside a git repository.",
        )
        return 1

    try:
        repository = Repository.open(chosen)
    except KilnError as error:
        QMessageBox.critical(None, "Kiln cannot start", str(error))
        return 1

    save_last_project(repository.root)

    window = MainWindow(repository)
    window.show()
    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
