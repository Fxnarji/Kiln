"""Reading preview images out of asset files.

Cheap extraction only: this reads a thumbnail the application already stored in
the file. Nothing here renders, converts, or launches anything, and any file
that does not hand over a preview easily gets a placeholder instead.

Currently supported: .blend

A .blend is a header followed by a chain of blocks. The preview, when there is
one, lives in a block coded `TEST`, as two 32-bit ints (width, height) followed
by width * height RGBA bytes. In practice it is the second block in the file,
right after `REND`, so almost nothing has to be read to find it.

There are two header layouts in the wild:

    legacy (Blender 2.x - 4.x), 12 bytes
        0..6   "BLENDER"
        7      pointer size: '_' = 4 bytes, '-' = 8 bytes
        8      endianness:   'v' = little,  'V' = big
        9..11  version, e.g. "403"

    current (Blender 4.5+), self-describing, e.g. "BLENDER17-01v0502"
        0..6   "BLENDER"
        7..8   header size in bytes, as ASCII digits ("17")
        9      pointer size: '_' = 4 bytes, '-' = 8 bytes
        10..11 header format version ("01")
        12..16 Blender version ("v0502")

    The current format carries no endianness byte — big-endian support was
    dropped, so these files are always little-endian.

Block headers differ to match:

    legacy   code (4) | length (4) | pointer (4 or 8) | SDNA (4) | count (4)
    current  code (4) | reserved (4) | pointer (8) | length (8) | SDNA (4) | count (4)

Blender only writes the preview when "Save Preview Images" is enabled in
Preferences, so a missing thumbnail is completely normal and never an error.
"""

from __future__ import annotations

import gzip
import logging
import struct
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

try:  # Modern .blend files are Zstandard compressed by default.
    import zstandard
except ImportError:  # pragma: no cover - depends on the install
    zstandard = None

BLENDER_MAGIC = b"BLENDER"
GZIP_MAGIC = b"\x1f\x8b"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

PREVIEW_BLOCK_CODE = b"TEST"
END_BLOCK_CODE = b"ENDB"

LEGACY_HEADER_SIZE = 12

# The preview is the second block in the file, and Blender caps previews at
# 128 pixels, so a megabyte is already far more than is ever needed. This is
# what stops us reading a 900 MB .blend to find out it has no thumbnail.
MAX_BYTES_TO_SCAN = 1024 * 1024

# Sanity limit, so a corrupt or misread header cannot make us allocate wildly.
MAX_PREVIEW_DIMENSION = 1024

_READ_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class Thumbnail:
    width: int
    height: int
    rgba: bytes  # width * height * 4 bytes, bottom row first (see ui.thumbnails)


@dataclass(frozen=True)
class _BlockLayout:
    """Where the interesting fields sit, for one of the two header formats."""

    first_block_offset: int
    header_size: int
    length_offset: int
    length_format: str

    def length_at(self, raw: bytes, block_offset: int) -> int:
        (length,) = struct.unpack_from(
            self.length_format, raw, block_offset + self.length_offset
        )
        return length


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
    raw = _read_head(path)
    if not raw.startswith(BLENDER_MAGIC):
        return None

    layout = _layout_for(raw)
    if layout is None:
        return None

    offset = layout.first_block_offset
    while offset + layout.header_size <= len(raw):
        code = raw[offset : offset + 4]
        if code == END_BLOCK_CODE:
            return None

        length = layout.length_at(raw, offset)
        if length < 0:
            return None

        if code == PREVIEW_BLOCK_CODE:
            return _decode_preview(raw, offset + layout.header_size, length)

        offset += layout.header_size + length

    return None


def _layout_for(raw: bytes) -> _BlockLayout | None:
    """Work out which header format this file uses.

    The current format announces its own header size in ASCII digits where the
    legacy format has a pointer-size character, which is what tells them apart.
    """
    if len(raw) < LEGACY_HEADER_SIZE:
        return None

    if raw[7:9].isdigit():
        header_size = int(raw[7:9])
        pointer_size = 8 if raw[9:10] == b"-" else 4
        return _BlockLayout(
            first_block_offset=header_size,
            header_size=4 + 4 + pointer_size + 8 + 4 + 4,
            length_offset=4 + 4 + pointer_size,
            length_format="<q",
        )

    pointer_size = 8 if raw[7:8] == b"-" else 4
    byte_order = "<" if raw[8:9] == b"v" else ">"
    return _BlockLayout(
        first_block_offset=LEGACY_HEADER_SIZE,
        header_size=4 + 4 + pointer_size + 4 + 4,
        length_offset=4,
        length_format=f"{byte_order}i",
    )


def _decode_preview(raw: bytes, data_start: int, length: int) -> Thumbnail | None:
    """Read the width, height, and pixels out of a TEST block's payload."""
    if length < 8 or data_start + 8 > len(raw):
        return None

    # The dimensions are little-endian in both formats: the legacy big-endian
    # variant only ever existed on hardware Blender no longer supports.
    width, height = struct.unpack_from("<ii", raw, data_start)
    if not (0 < width <= MAX_PREVIEW_DIMENSION and 0 < height <= MAX_PREVIEW_DIMENSION):
        return None

    pixel_bytes = width * height * 4
    pixel_start = data_start + 8
    if pixel_start + pixel_bytes > len(raw) or pixel_bytes + 8 > length:
        return None

    return Thumbnail(
        width=width, height=height, rgba=raw[pixel_start : pixel_start + pixel_bytes]
    )


def _read_head(path: Path) -> bytes:
    """Read the start of the file, decompressing it if necessary.

    Returns whatever could be read; callers treat "too short to parse" the same
    as "no thumbnail", so there is no need to distinguish here.
    """
    with path.open("rb") as handle:
        opening = handle.read(4)
        handle.seek(0)

        if opening.startswith(ZSTD_MAGIC):
            if zstandard is None:
                log.debug("%s is zstd compressed and zstandard is not installed", path.name)
                return b""
            reader = zstandard.ZstdDecompressor().stream_reader(handle)
            return _read_exactly(reader, MAX_BYTES_TO_SCAN)

        if opening.startswith(GZIP_MAGIC):
            with gzip.open(handle, "rb") as unzipped:
                return _read_exactly(unzipped, MAX_BYTES_TO_SCAN)

        return handle.read(MAX_BYTES_TO_SCAN)


def _read_exactly(stream, limit: int) -> bytes:
    """Read up to `limit` bytes from a decompressing stream.

    Decompressors return whatever a frame happens to yield, which is usually
    far less than asked for, so a single read() call would silently truncate
    the file just past the header.
    """
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = stream.read(min(_READ_CHUNK_SIZE, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
