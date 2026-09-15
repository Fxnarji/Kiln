"""The read-only bit that `lockable` in .gitattributes puts on files.

Spec 7.1 calls this the one place where behaviour genuinely differs between
Windows and Linux, so it gets tested on whichever platform the suite runs on
and must pass on both.

What is being protected: git-lfs is *expected* to make a lockable file writable
when you lock it and read-only when you unlock it, but Kiln verifies rather
than trusting that. If is_writable returns the wrong answer, an artist either
gets a file they cannot save at the end of a session, or one they can edit
without holding the lock.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from kiln.core.permissions import is_writable, make_read_only, make_writable


@pytest.fixture
def asset(tmp_path: Path) -> Path:
    path = tmp_path / "hero.blend"
    path.write_bytes(b"content")
    return path


def restore_write_access(path: Path) -> None:
    """So tmp_path cleanup can delete a file left read-only on Windows."""
    if path.exists():
        path.chmod(path.stat().st_mode | stat.S_IWUSR)


def test_a_new_file_is_writable(asset: Path):
    assert is_writable(asset)


def test_make_read_only_takes_write_access_away(asset: Path):
    make_read_only(asset)
    try:
        assert not is_writable(asset)
    finally:
        restore_write_access(asset)


def test_make_writable_gives_it_back(asset: Path):
    make_read_only(asset)
    make_writable(asset)

    assert is_writable(asset)


def test_round_trip_is_stable(asset: Path):
    """Lock, unlock, lock again — the sequence an artist actually produces."""
    for _ in range(3):
        make_read_only(asset)
        assert not is_writable(asset)
        make_writable(asset)
        assert is_writable(asset)


def test_read_only_file_really_cannot_be_written(asset: Path):
    """os.access is a claim; this checks the filesystem agrees with it."""
    make_read_only(asset)
    try:
        with pytest.raises(OSError):
            with asset.open("wb") as handle:
                handle.write(b"should not get here")
    finally:
        restore_write_access(asset)


def test_writable_file_really_can_be_written(asset: Path):
    make_read_only(asset)
    make_writable(asset)

    with asset.open("wb") as handle:
        handle.write(b"new content")

    assert asset.read_bytes() == b"new content"


def test_missing_file_is_not_writable(tmp_path: Path):
    assert not is_writable(tmp_path / "nothing.blend")


def test_a_directory_is_not_reported_as_a_writable_file(tmp_path: Path):
    """is_writable answers about files; a folder must not pass as one."""
    assert not is_writable(tmp_path)


def test_content_survives_permission_changes(asset: Path):
    make_read_only(asset)
    make_writable(asset)

    assert asset.read_bytes() == b"content"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits only")
def test_make_writable_preserves_other_mode_bits():
    """Only the write bits move; execute and read stay as they were."""
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "tool.sh"
        path.write_bytes(b"#!/bin/sh\n")
        path.chmod(0o755)

        make_read_only(path)
        make_writable(path)

        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, "execute bit was lost"
        assert mode & stat.S_IRUSR, "read bit was lost"
        assert mode & stat.S_IWUSR


@pytest.mark.skipif(sys.platform != "win32", reason="Windows read-only attribute")
def test_read_only_attribute_is_what_windows_reports(asset: Path):
    """On Windows this is the FILE_ATTRIBUTE_READONLY flag, not mode bits."""
    make_read_only(asset)
    try:
        assert not os.access(asset, os.W_OK)
        assert asset.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    finally:
        restore_write_access(asset)
