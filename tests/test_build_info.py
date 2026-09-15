"""Build stamps and release manifests, the groundwork for updating Kiln."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from kiln import __version__, build_info
from scripts import build_release


def test_missing_stamp_is_a_dev_build(tmp_path: Path):
    info = build_info.load(tmp_path / "build_info.json")

    assert info.version == __version__
    assert not info.is_ci_build


def test_unreadable_stamp_is_a_dev_build(tmp_path: Path):
    stamp = tmp_path / "build_info.json"
    stamp.write_text("not json", encoding="utf-8")

    assert not build_info.load(stamp).is_ci_build


def test_newer_build_on_the_same_channel_supersedes():
    running = build_info.BuildInfo("0.1.0", build=41, channel="main")

    assert running.is_superseded_by(build_info.BuildInfo("0.1.0", build=42, channel="main"))
    assert not running.is_superseded_by(build_info.BuildInfo("0.1.0", build=41, channel="main"))
    assert not running.is_superseded_by(build_info.BuildInfo("0.1.0", build=90, channel="pr-7"))


def test_dev_build_is_never_superseded():
    running = build_info.BuildInfo("0.1.0")

    assert not running.is_superseded_by(build_info.BuildInfo("0.1.0", build=99, channel="dev"))


def test_manifest_describes_each_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(build_release, "BUILD_INFO_FILE", tmp_path / "build_info.json")
    info = build_release.stamp_build_info("0.1.0", 42, "a" * 40, "main")
    archive = tmp_path / "Kiln-0.1.0-b42-windows.zip"
    archive.write_bytes(b"zip contents")

    manifest_path = build_release.write_manifest(
        tmp_path / "Kiln-windows.json", info, {"zip": archive}
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema"] == build_release.MANIFEST_SCHEMA
    assert build_info.from_dict(manifest) == build_info.load(tmp_path / "build_info.json")
    assert manifest["files"] == [
        {
            "kind": "zip",
            "name": archive.name,
            "size": len(b"zip contents"),
            "sha256": hashlib.sha256(b"zip contents").hexdigest(),
        }
    ]
