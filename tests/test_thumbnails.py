"""Extracting embedded previews from .blend files.

These caught a real failure: every .blend written by a current Blender is
Zstandard compressed and uses a header layout the first implementation did not
know about, so nothing was ever extracted in practice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kiln.core.thumbnails import read_thumbnail
from tests.blend_builder import (
    build_blend,
    compress_gzip,
    compress_zstd,
)


def write(tmp_path: Path, raw: bytes, name: str = "asset.blend") -> Path:
    path = tmp_path / name
    path.write_bytes(raw)
    return path


def test_reads_preview_from_current_format(tmp_path: Path):
    path = write(tmp_path, build_blend(width=8, height=4))

    thumbnail = read_thumbnail(path)

    assert thumbnail is not None
    assert (thumbnail.width, thumbnail.height) == (8, 4)
    assert len(thumbnail.rgba) == 8 * 4 * 4


def test_reads_preview_from_legacy_format(tmp_path: Path):
    path = write(tmp_path, build_blend(width=6, height=3, legacy=True))

    thumbnail = read_thumbnail(path)

    assert thumbnail is not None
    assert (thumbnail.width, thumbnail.height) == (6, 3)


def test_reads_preview_from_zstd_compressed_file(tmp_path: Path):
    """The case that matters: this is what Blender writes by default."""
    path = write(tmp_path, compress_zstd(build_blend(width=8, height=4)))

    thumbnail = read_thumbnail(path)

    assert thumbnail is not None
    assert (thumbnail.width, thumbnail.height) == (8, 4)


def test_reads_preview_from_gzip_compressed_file(tmp_path: Path):
    path = write(tmp_path, compress_gzip(build_blend(width=8, height=4)))

    thumbnail = read_thumbnail(path)

    assert thumbnail is not None
    assert (thumbnail.width, thumbnail.height) == (8, 4)


def test_decompression_reads_past_the_first_chunk(tmp_path: Path):
    """A single read() on a decompressing stream returns a short buffer.

    With a 128x128 preview the payload is 64 KB, which is exactly where a naive
    single read truncates — so this uses a full-size preview on purpose.
    """
    path = write(tmp_path, compress_zstd(build_blend(width=128, height=128)))

    thumbnail = read_thumbnail(path)

    assert thumbnail is not None
    assert (thumbnail.width, thumbnail.height) == (128, 128)
    assert len(thumbnail.rgba) == 128 * 128 * 4


def test_pixels_are_stored_bottom_row_first(tmp_path: Path):
    """Blender writes the preview the way OpenGL hands it over.

    The builder makes row 0 black and the last row red. Confirming that here is
    what justifies the vertical flip in kiln/ui/thumbnails.py — without it the
    grid would show every preview upside down.
    """
    path = write(tmp_path, build_blend(width=4, height=4))

    thumbnail = read_thumbnail(path)

    first_pixel = thumbnail.rgba[0:4]
    last_pixel = thumbnail.rgba[-4:]
    assert first_pixel == bytes([0, 0, 0, 255])
    assert last_pixel == bytes([255, 0, 0, 255])


def test_file_without_a_preview_returns_none(tmp_path: Path):
    """Normal: Blender only saves previews when the preference is enabled."""
    path = write(tmp_path, build_blend(with_preview=False))

    assert read_thumbnail(path) is None


def test_non_blend_file_is_ignored(tmp_path: Path):
    path = write(tmp_path, build_blend(), name="texture.png")

    assert read_thumbnail(path) is None


def test_truncated_file_returns_none_rather_than_raising(tmp_path: Path):
    path = write(tmp_path, build_blend()[:20])

    assert read_thumbnail(path) is None


def test_garbage_file_returns_none_rather_than_raising(tmp_path: Path):
    path = write(tmp_path, b"not a blend file at all" * 10)

    assert read_thumbnail(path) is None


def test_missing_file_returns_none(tmp_path: Path):
    assert read_thumbnail(tmp_path / "nothing.blend") is None


@pytest.mark.parametrize("dimensions", [(0, 4), (4, 0), (99999, 99999)])
def test_implausible_dimensions_are_rejected(tmp_path: Path, dimensions):
    """A misread header must not make us try to allocate gigabytes."""
    import struct

    from tests.blend_builder import CURRENT_HEADER, _current_block

    width, height = dimensions
    payload = struct.pack("<ii", width, height) + b"\0" * 16
    raw = (
        CURRENT_HEADER
        + _current_block(b"TEST", payload)
        + _current_block(b"ENDB", b"")
    )

    assert read_thumbnail(write(tmp_path, raw)) is None
