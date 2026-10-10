"""The main window shows local changes without waiting for the lock server."""

from __future__ import annotations

import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from kiln.core.models import Lock  # noqa: E402
from kiln.ui import main_window  # noqa: E402


@pytest.fixture(scope="module")
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def config_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


def settle(application, condition, seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)
    application.processEvents()
    return condition()


def test_files_show_while_the_lock_server_is_still_answering(
    application, repository, monkeypatch
):
    server_answers = threading.Event()
    lock = Lock("props/crate.blend", "Devin", None, is_mine=False)

    def slow_server():
        server_answers.wait(10)
        return {"props/crate.blend": lock}, True

    monkeypatch.setattr(repository, "_supports_locking", None, raising=False)
    monkeypatch.setattr(repository, "supports_locking", lambda: True)
    monkeypatch.setattr(repository, "_load_locks", slow_server)
    monkeypatch.setattr(main_window.MainWindow, "_check_for_update_quietly", lambda self: None)

    window = main_window.MainWindow(repository)
    try:
        assert settle(application, lambda: window.state is not None)
        assert window.state.find("props/crate.blend").lock is None
        assert "checking locks" in window.locks_label.text()

        server_answers.set()
        assert settle(application, lambda: window.state.find("props/crate.blend").lock)
        assert window.state.find("props/crate.blend").lock.owner == "Devin"
    finally:
        server_answers.set()
        window.close()


def test_refreshes_asked_for_while_one_is_queued_are_merged(
    application, repository, monkeypatch
):
    reads = []
    real = repository.local_snapshot
    monkeypatch.setattr(repository, "_load_locks", lambda: ({}, False))
    monkeypatch.setattr(main_window.MainWindow, "_check_for_update_quietly", lambda self: None)

    window = main_window.MainWindow(repository)
    try:
        assert settle(application, lambda: window.state is not None and not window.jobs.busy)
        monkeypatch.setattr(repository, "local_snapshot", lambda: reads.append(1) or real())
        # Hold the queue so the refreshes pile up behind something.
        gate = threading.Event()
        window.jobs.submit("busy", lambda: gate.wait(10))
        for _ in range(5):
            window.refresh()
        gate.set()
        assert settle(application, lambda: not window.jobs.busy)

        assert reads == [1]
    finally:
        window.close()
