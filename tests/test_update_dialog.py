"""The update dialog, driven headless from click to restart.

GitHub and the restart itself are replaced; everything between them (the
background threads, cancelling, unpacking, closing windows) is real.
"""

from __future__ import annotations

import io
import os
import threading
import time
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from kiln import installer, update  # noqa: E402
from kiln.build_info import BuildInfo  # noqa: E402
from kiln.ui import update_dialog  # noqa: E402

RUNNING = BuildInfo("0.1.0", build=41, commit="a" * 40, channel="main")


def release(build: int) -> update.Release:
    item = update.ReleaseFile(
        kind="zip",
        name=f"Kiln-0.1.0-b{build}-windows.zip",
        size=1,
        sha256="0",
        url=f"https://example.test/b{build}.zip",
    )
    return update.Release(BuildInfo("0.1.0", build=build, channel="main"), (item,))


def zipped_build() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Kiln/Kiln.exe", b"new launcher")
    return buffer.getvalue()


@pytest.fixture(scope="module")
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def config_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture
def world(tmp_path, monkeypatch, application):
    """GitHub serving `published`, and a record of what was restarted into."""

    class World:
        published: update.Release | None = release(43)
        downloaded: list[str] = []
        launched: list[Path] = []
        quit_calls = 0

    def find_update(current):
        return World.published

    def download(item, destination, progress=None, cancelled=None):
        World.downloaded.append(item.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(zipped_build())
        return destination

    def quit_application():
        World.quit_calls += 1

    monkeypatch.setattr(update, "find_update", find_update)
    monkeypatch.setattr(update, "download", download)
    monkeypatch.setattr(
        installer, "launch_replacement", lambda inst, staged: World.launched.append(staged)
    )
    monkeypatch.setattr(update_dialog.QApplication, "quit", staticmethod(quit_application))
    World.installation = installer.detect(tmp_path / "Kiln" / "Kiln.exe", tmp_path / "Kiln")
    World.downloaded, World.launched = [], []
    return World


def settle(application, condition, seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)
    application.processEvents()


def dialog_for(world, offered: update.Release | None = None) -> update_dialog.UpdateDialog:
    return update_dialog.UpdateDialog(
        release=offered, current=RUNNING, installation=world.installation
    )


def test_download_rereads_the_release_the_startup_check_saw(world, application):
    """Every push to main deletes the files an earlier check found."""
    dialog = dialog_for(world, offered=release(42))
    settle(application, lambda: not dialog.install_button.isHidden())
    world.published = release(43)

    dialog.install_button.click()
    settle(application, lambda: world.launched)

    assert world.downloaded == ["Kiln-0.1.0-b43-windows.zip"]
    assert (world.launched[0] / "Kiln.exe").read_bytes() == b"new launcher"
    assert dialog.release.build.build == 43


def test_offered_build_gone_without_a_newer_one(world, application):
    dialog = dialog_for(world, offered=release(42))
    settle(application, lambda: not dialog.install_button.isHidden())
    world.published = None

    dialog.install_button.click()
    settle(application, lambda: "gone" in dialog.message.text())

    assert "gone" in dialog.message.text()
    assert world.launched == []


def test_cancel_while_unpacking_still_cancels(world, application, monkeypatch):
    unpacking, finish = threading.Event(), threading.Event()
    real_prepare = installer.prepare

    def slow_prepare(installation, downloaded):
        unpacking.set()
        finish.wait(5)
        return real_prepare(installation, downloaded)

    monkeypatch.setattr(installer, "prepare", slow_prepare)
    dialog = dialog_for(world)
    settle(application, lambda: not dialog.install_button.isHidden())

    dialog.install_button.click()
    settle(application, unpacking.is_set)
    dialog.reject()  # Cancel, Esc and the close box all land here
    finish.set()
    settle(application, lambda: not dialog.install_button.isHidden())

    assert world.launched == []
    assert world.quit_calls == 0
    assert not world.installation.staging_directory.exists()
    assert "newer Kiln is available" in dialog.message.text()


def test_window_that_will_not_close_blocks_the_restart(world, application):
    class Stubborn(QtWidgets.QWidget):
        def closeEvent(self, event):
            event.ignore()

    stubborn = Stubborn()
    stubborn.show()
    try:
        dialog = dialog_for(world)
        settle(application, lambda: not dialog.install_button.isHidden())

        dialog.install_button.click()
        settle(application, lambda: dialog.install_button.text() == "Restart now")

        assert world.launched == []
        assert "would not close" in dialog.message.text()
    finally:
        stubborn.hide()
        stubborn.deleteLater()


def test_restart_waits_for_running_git_work(world, application):
    busy = ["A git operation is still running."]
    dialog = update_dialog.UpdateDialog(
        release=release(43),
        busy_reason=lambda: busy[0],
        current=RUNNING,
        installation=world.installation,
    )
    settle(application, lambda: not dialog.install_button.isHidden())

    dialog.install_button.click()
    assert world.downloaded == []

    busy[0] = ""
    dialog.install_button.click()
    settle(application, lambda: world.launched)

    assert len(world.launched) == 1
    assert world.quit_calls == 1
