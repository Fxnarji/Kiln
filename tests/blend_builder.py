"""Build synthetic .blend files for the thumbnail tests.

Writing real Blender files by hand is the only way to test the extractor
without shipping binary fixtures or depending on Blender being installed. The
byte layout here was derived from real files and is documented in
kiln/core/thumbnails.py.

The pixel payload is a deliberately asymmetric gradient, so a test can tell a
correctly oriented image from a vertically flipped one.
"""

from __future__ import annotations

import gzip
import struct

CURRENT_HEADER = b"BLENDER17-01v0502"  # 17 bytes, self-describing
LEGACY_HEADER = b"BLENDER-v403"  # 12 bytes, 8-byte pointers, little-endian


def _current_block(code: bytes, payload: bytes) -> bytes:
    """code (4) | reserved (4) | pointer (8) | length (8) | SDNA (4) | count (4)"""
    return (
        code
        + b"\0" * 4
        + struct.pack("<Q", 0xDEADBEEF)
        + struct.pack("<q", len(payload))
        + struct.pack("<i", 0)
        + struct.pack("<i", 1)
        + payload
    )


def _legacy_block(code: bytes, payload: bytes) -> bytes:
    """code (4) | length (4) | pointer (8) | SDNA (4) | count (4)"""
    return (
        code
        + struct.pack("<i", len(payload))
        + struct.pack("<Q", 0xDEADBEEF)
        + struct.pack("<i", 0)
        + struct.pack("<i", 1)
        + payload
    )


def preview_payload(width: int, height: int) -> bytes:
    """A TEST block payload: width, height, then RGBA rows.

    Row 0 is black and the last row is bright red, so a vertical flip is
    obvious from the first pixel alone.
    """
    pixels = bytearray()
    for row in range(height):
        intensity = int(255 * row / max(1, height - 1))
        pixels += bytes([intensity, 0, 0, 255]) * width
    return struct.pack("<ii", width, height) + bytes(pixels)


def build_blend(
    width: int = 8,
    height: int = 4,
    legacy: bool = False,
    with_preview: bool = True,
) -> bytes:
    """A minimal .blend: REND, optionally TEST, then ENDB."""
    header = LEGACY_HEADER if legacy else CURRENT_HEADER
    block = _legacy_block if legacy else _current_block

    body = block(b"REND", b"\0" * 264)
    if with_preview:
        body += block(b"TEST", preview_payload(width, height))
    body += block(b"GLOB", b"\0" * 16)
    body += block(b"ENDB", b"")
    return header + body


def compress_zstd(raw: bytes) -> bytes:
    import zstandard

    return zstandard.ZstdCompressor().compress(raw)


def compress_gzip(raw: bytes) -> bytes:
    return gzip.compress(raw)
