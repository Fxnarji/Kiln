"""Finding, downloading, and staging a newer build of Kiln.

GitHub is replaced by a dictionary of URLs: what is under test is how Kiln
reads what is published, not whether GitHub is up. The manifests are the ones
scripts/build_release.py really writes, so the two sides cannot drift apart.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import urllib.error
import zipfile
from pathlib import Path

import pytest

from kiln import installer, update
from kiln.build_info import BuildInfo
from scripts import build_release


class FakeGitHub:
    """An opener that serves fixed bytes by URL, and 404s everything else."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.requested: list[str] = []

    def publish(self, url: str, content: bytes) -> None:
        self.files[url] = content

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.requested.append(url)
        if url not in self.files:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return io.BytesIO(self.files[url])


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


def publish_build(
    github: FakeGitHub, tmp_path: Path, monkeypatch, build: int, channel: str = "main"
) -> dict[str, bytes]:
    """Publish a build the way CI does: build_release writes, CI uploads."""
    monkeypatch.setattr(build_release, "BUILD_INFO_FILE", tmp_path / "build_info.json")
    info = build_release.stamp_build_info("0.1.0", build, "c" * 40, channel)
    contents = {
        "zip": f"zip of build {build}".encode(),
        "portable": f"exe of build {build}".encode(),
    }
    artifacts = {}
    for kind, data in contents.items():
        suffix = "zip" if kind == "zip" else "exe"
        path = tmp_path / f"Kiln-0.1.0-b{build}-windows.{suffix}"
        path.write_bytes(data)
        artifacts[kind] = path
    manifest = build_release.write_manifest(tmp_path / "Kiln-windows.json", info, artifacts)

    base = update.download_base_url(channel)
    github.publish(base + manifest.name, manifest.read_bytes())
    for path in artifacts.values():
        github.publish(base + path.name, path.read_bytes())
    return contents


class TestChecking:
    def test_both_sides_agree_on_the_manifest_format(self):
        assert update.MANIFEST_SCHEMA == build_release.MANIFEST_SCHEMA

    def test_newer_build_on_the_channel_is_offered(self, github, tmp_path, monkeypatch):
        publish_build(github, tmp_path, monkeypatch, build=42)

        release = update.find_update(
            BuildInfo("0.1.0", build=41, channel="main"), "windows", opener=github
        )

        assert release is not None
        assert release.build.build == 42
        assert release.file("zip").name == "Kiln-0.1.0-b42-windows.zip"
        assert release.file("portable").url == (
            update.download_base_url("main") + "Kiln-0.1.0-b42-windows.exe"
        )
        assert github.requested == [update.manifest_url("main", "windows")]

    def test_same_build_is_up_to_date(self, github, tmp_path, monkeypatch):
        publish_build(github, tmp_path, monkeypatch, build=42)

        running = BuildInfo("0.1.0", build=42, channel="main")

        assert update.find_update(running, "windows", opener=github) is None

    def test_each_channel_reads_its_own_release(self, github, tmp_path, monkeypatch):
        publish_build(github, tmp_path, monkeypatch, build=50, channel="dev")

        running = BuildInfo("0.1.0", build=41, channel="dev")

        assert update.find_update(running, "windows", opener=github).build.channel == "dev"
        assert github.requested == [update.manifest_url("dev", "windows")]

    def test_development_build_never_asks(self, github):
        assert update.find_update(BuildInfo("0.1.0"), "windows", opener=github) is None
        assert github.requested == []

    def test_nothing_published_says_so(self, github):
        with pytest.raises(update.UpdateError, match="No build has been published"):
            update.fetch_release("main", "windows", opener=github)

    def test_unreachable_github_says_so(self):
        def offline(request, timeout=None):
            raise urllib.error.URLError("no route to host")

        with pytest.raises(update.UpdateError, match="Could not reach GitHub"):
            update.fetch_release("main", "windows", opener=offline)

    def test_newer_manifest_format_is_refused(self):
        with pytest.raises(update.UpdateError, match="does not understand"):
            update.parse_manifest({"schema": 99, "files": []}, "https://example.test/")

    def test_file_names_cannot_leave_the_release(self):
        manifest = {
            "schema": update.MANIFEST_SCHEMA,
            "build": 5,
            "files": [{"kind": "zip", "name": "../evil.zip", "size": 1, "sha256": "0"}],
        }

        with pytest.raises(update.UpdateError):
            update.parse_manifest(manifest, "https://example.test/")


class TestDownloading:
    def release_file(self, github: FakeGitHub, content: bytes, sha256: str | None = None):
        url = "https://example.test/Kiln.zip"
        github.publish(url, content)
        return update.ReleaseFile(
            kind="zip",
            name="Kiln.zip",
            size=len(content),
            sha256=sha256 or hashlib.sha256(content).hexdigest(),
            url=url,
        )

    def test_verified_download_lands_in_place(self, github, tmp_path):
        content = b"x" * (update.CHUNK_SIZE * 2 + 10)
        item = self.release_file(github, content)
        seen = []

        path = update.download(
            item, tmp_path / "Kiln.zip", progress=lambda d, t: seen.append(d), opener=github
        )

        assert path.read_bytes() == content
        assert seen[-1] == len(content)
        assert not (tmp_path / "Kiln.zip.part").exists()

    def test_tampered_download_is_thrown_away(self, github, tmp_path):
        item = self.release_file(github, b"contents", sha256="0" * 64)

        with pytest.raises(update.UpdateError, match="did not match"):
            update.download(item, tmp_path / "Kiln.zip", opener=github)

        assert list(tmp_path.iterdir()) == []

    def test_oversized_download_stops_early(self, github, tmp_path):
        item = self.release_file(github, b"contents")
        github.publish(item.url, b"contents and then some")

        with pytest.raises(update.UpdateError, match="larger"):
            update.download(item, tmp_path / "Kiln.zip", opener=github)

        assert list(tmp_path.iterdir()) == []

    def test_cancelled_download_leaves_nothing(self, github, tmp_path):
        item = self.release_file(github, b"contents")
        cancelled = threading.Event()
        cancelled.set()

        with pytest.raises(update.DownloadCancelled):
            update.download(item, tmp_path / "Kiln.zip", cancelled=cancelled, opener=github)

        assert list(tmp_path.iterdir()) == []


class TestInstallation:
    def test_one_folder_build_is_recognised(self, tmp_path):
        exe = tmp_path / "Kiln" / "Kiln.exe"

        installation = installer.detect(exe, tmp_path / "Kiln" / "_internal")

        assert installation.kind == installer.FOLDER
        assert installation.root == tmp_path / "Kiln"
        assert installation.artifact_kind == "zip"
        assert installation.staging_directory.parent == tmp_path

    def test_portable_build_is_recognised(self, tmp_path):
        exe = tmp_path / "Downloads" / "Kiln-0.1.0-b41-windows.exe"

        installation = installer.detect(exe, tmp_path / "Temp" / "_MEI12345")

        assert installation.kind == installer.PORTABLE
        assert installation.root == exe.resolve()
        assert installation.artifact_kind == "portable"

    def test_running_from_source_cannot_update(self):
        assert installer.current_installation() is None
        assert installer.unavailable_reason(None)

    def test_writable_installation_can_update(self, tmp_path):
        installation = installer.detect(tmp_path / "Kiln" / "Kiln.exe", tmp_path / "Kiln")

        assert installer.unavailable_reason(installation) == ""

    def test_published_zip_unpacks_to_a_runnable_folder(self, tmp_path, monkeypatch):
        build = tmp_path / "dist" / "Kiln"
        (build / "_internal").mkdir(parents=True)
        (build / "Kiln.exe").write_bytes(b"new launcher")
        (build / "_internal" / "python311.dll").write_bytes(b"dll")
        monkeypatch.setattr(build_release, "BUILD_OUTPUT", build)
        archive = build_release.make_zip(tmp_path / "Kiln-0.1.0-b42-windows.zip")
        installation = installer.detect(tmp_path / "Kiln" / "Kiln.exe", tmp_path / "Kiln")

        staged = installer.prepare(installation, archive)

        assert (staged / "Kiln.exe").read_bytes() == b"new launcher"
        assert (staged / "_internal" / "python311.dll").is_file()
        assert installation.staging_directory in staged.parents

        installer.clear_staging(installation)
        assert not installation.staging_directory.exists()

    def test_zip_that_escapes_its_folder_is_refused(self, tmp_path):
        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("Kiln/Kiln.exe", b"exe")
            handle.writestr("../outside.txt", b"gotcha")
        installation = installer.detect(tmp_path / "Kiln" / "Kiln.exe", tmp_path / "Kiln")

        with pytest.raises(installer.InstallError):
            installer.prepare(installation, archive)

        assert not (tmp_path / "outside.txt").exists()

    def test_helper_is_told_everything_it_uses(self, tmp_path):
        installation = installer.detect(tmp_path / "Kiln" / "Kiln.exe", tmp_path / "Kiln")

        environment = installer.helper_environment(
            installation, tmp_path / "staged", 1234, {"PATH": "x"}
        )
        used = set(re.findall(r"%(KILN_\w+)%", installer.HELPER_SCRIPT))

        assert used and used <= environment.keys()
        assert environment["KILN_LAUNCH"] == str(tmp_path / "Kiln" / "Kiln.exe")
        assert environment["KILN_WAIT_PID"] == "1234"
        installer.HELPER_SCRIPT.encode("ascii")

    def test_helper_does_not_inherit_the_old_bundle(self, tmp_path):
        installation = installer.detect(tmp_path / "Kiln.exe", tmp_path / "Temp" / "_MEI1")
        inherited = {"PATH": "x", "_PYI_ARCHIVE_FILE": "old", "_MEIPASS2": "old"}

        environment = installer.helper_environment(installation, tmp_path / "new", 1, inherited)

        assert "_PYI_ARCHIVE_FILE" not in environment
        assert "_MEIPASS2" not in environment
        assert environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
        assert environment["KILN_LAUNCH"] == str(installation.executable)
