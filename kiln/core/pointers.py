"""Detecting Git LFS pointer files.

A pointer is a tiny text stub that stands in for a file whose real content has
not been downloaded. Handing one to Blender produces a baffling error, so Kiln
checks before opening anything and shows the state in every file list.
"""

from __future__ import annotations

from pathlib import Path

POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"

# Real pointer files are a few hundred bytes at most. The generous ceiling here
# keeps us from reading the first kilobyte of a 900 MB .blend on every refresh.
MAX_POINTER_SIZE_BYTES = 1024


def is_pointer_file(path: Path) -> bool:
    """True if this file is an undownloaded LFS pointer."""
    try:
        if path.stat().st_size > MAX_POINTER_SIZE_BYTES:
            return False
        with path.open("rb") as handle:
            return handle.read(len(POINTER_PREFIX)) == POINTER_PREFIX
    except OSError:
        return False


def is_downloaded(repo_root: Path, repo_relative_path: str) -> bool:
    """True if the real content is on disk.

    A file that does not exist at all counts as not downloaded, which is the
    right answer for the UI: either way there is nothing to open.
    """
    full_path = repo_root / repo_relative_path
    if not full_path.is_file():
        return False
    return not is_pointer_file(full_path)
