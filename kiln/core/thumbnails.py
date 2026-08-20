"""Reading preview images out of asset files.

Cheap extraction only: this reads a thumbnail that the application already
stored in the file. Nothing here renders, converts, or launches anything, and
any file that does not hand over a preview easily gets a placeholder instead.

Currently supported: .blend

A .blend is a header followed by a chain of blocks. The preview, if there is
one, lives in a block with the code `TEST`, as two 32-bit ints (width, height)
followed by width * height RGBA bytes.

    header (12 bytes)
        0..6   "BLENDER"
        7      pointer size: '_' = 4 bytes, '-' = 8 bytes
        8      endianness:   'v' = little,  'V' = big
        9..11  version, e.g. "403"

    block header
        code (4) | length (4) | old pointer (4 or 8) | SDNA index (4) | count (4)

Blender only writes the preview when "Save Preview Images" is enabled, so a
missing thumbnail is completely normal and never an error.
"""

from __future__ import annotations

import gzip
import logging
import struct
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

BLENDER_MAGIC = b"BLENDER"
GZIP_MAGIC = b"\x1f\x8b"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

PREVIEW_BLOCK_CODE = b"TEST"
END_BLOCK_CODE = b"ENDB"

# The preview sits near the start of the file, so there is no reason to read a
# 900 MB .blend to find out it has none.
MAX_BYTES_TO_SCAN = 2 * 1024 * 1024

# Sanity limits, so a corrupt or misread header cannot make us allocate wildly.
MAX_PREVIEW_DIMENSION = 1024


@dataclass(frozen=True)
class Thumbnail:
    width: int
    height: int
    rgba: bytes  # width * height * 4 bytes, top row first


def read_thumbnail(path: Path) -> Thumbnail | None:
    """Extract an embedded preview, or None if there isn't one we can read."""
    if path.suffix.lower() != ".blend":
        return None
    try:
        return _read_blend_thumbnail(path)
    except (OSError, struct.error, ValueError) as error:
        log.debug("no thumbnail from %s: %s", path.name, error)
        return None


def _read_blend_thumbnail(path: Path) -> Thumbnail | None:
    raw = _read_start_of_file(path)
    if raw is None or not raw.startswith(BLENDER_MAGIC):
        return None

    pointer_size = 8 if raw[7:8] == b"-" else 4
    byte_order = "<" if raw[8:9] == b"v" else ">"
    block_header_size = 4 + 4 + pointer_size + 4 + 4

    offset = 12  # past the file header
    while offset + block_header_size <= len(raw):
        code = raw[offset : offset + 4]
        (length,) = struct.unpack_from(f"{byte_order}i", raw, offset + 4)
        data_start = offset + block_header_size

        if code == END_BLOCK_CODE or length < 0:
            return None

        if code == PREVIEW_BLOCK_CODE:
            return _decode_preview(raw, data_start, length, byte_order)

        offset = data_start + length

    return None


def _decode_preview(
    raw: bytes, data_start: int, length: int, byte_order: str
) -> Thumbnail | None:
    if length < 8:
        return None

    width, height = struct.unpack_from(f"{byte_order}ii", raw, data_start)
    if not (0 < width <= MAX_PREVIEW_DIMENSION and 0 < height <= MAX_PREVIEW_DIMENSION):
        return None

    expected = width * height * 4
    pixel_start = data_start + 8
    if pixel_start + expected > len(raw) or expected + 8 > length:
        return None

    return Thumbnail(
        width=width, height=height, rgba=raw[pixel_start : pixel_start + expected]
    )


def _read_start_of_file(path: Path) -> bytes | None:
    """Read the first chunk, transparently handling a compressed .blend.

    Zstandard compression (Blender's default since 3.0) needs a third-party
    library, so those files simply get a placeholder. Adding support later
    means adding one branch here.
    """
    with path.open("rb") as handle:
        opening = handle.read(4)
        handle.seek(0)

        if opening.startswith(ZSTD_MAGIC):
            return None
        if opening.startswith(GZIP_MAGIC):
            with gzip.open(handle, "rb") as unzipped:
                return unzipped.read(MAX_BYTES_TO_SCAN)
        return handle.read(MAX_BYTES_TO_SCAN)
