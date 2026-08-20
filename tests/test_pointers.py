"""LFS pointer detection.

Handing an application a pointer file instead of a 300 MB .blend produces a
baffling error, so this check guards every open.
"""

from __future__ import annotations

from pathlib import Path

from kiln.core.pointers import is_downloaded, is_pointer_file

POINTER_TEXT = (
    "version https://git-lfs.github.com/spec/v1\n"
    "oid sha256:4d7a214614ab2935c943f9e0ff69d22eadbb8f32b1258daaa5e2ca24d17e2393\n"
    "size 12345\n"
)


def test_pointer_file_is_recognised(tmp_path: Path):
    pointer = tmp_path / "hero.blend"
    pointer.write_text(POINTER_TEXT, encoding="utf-8")

    assert is_pointer_file(pointer)


def test_real_binary_is_not_a_pointer(tmp_path: Path):
    real = tmp_path / "hero.blend"
    real.write_bytes(b"BLENDER-v300" + bytes(4096))

    assert not is_pointer_file(real)


def test_large_file_is_never_read(tmp_path: Path):
    """The size check short-circuits before opening anything big."""
    big = tmp_path / "hero.blend"
    big.write_bytes(POINTER_TEXT.encode("utf-8") + bytes(4096))

    assert not is_pointer_file(big)


def test_missing_file_counts_as_not_downloaded(tmp_path: Path):
    assert not is_downloaded(tmp_path, "nothing/here.blend")


def test_text_file_that_merely_mentions_lfs_is_not_a_pointer(tmp_path: Path):
    notes = tmp_path / "notes.txt"
    notes.write_text("we use version https://git-lfs.github.com/spec/v1 here\n")

    assert not is_pointer_file(notes)
