"""The read-only bit that `lockable` in .gitattributes puts on files.

git-lfs is expected to make a file writable when you lock it and read-only when
you unlock it. Kiln verifies rather than trusting that, because the consequence
of being wrong is an artist losing a save at the end of a work session.

This is the one module where behaviour differs between Windows and Linux, so it
is deliberately tiny and tested on both.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

# The write bits for user, group, and other.
_WRITE_BITS = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH


def is_writable(path: Path) -> bool:
    """Can the current user write to this file?

    On Windows this reflects the read-only attribute; on Linux, the mode bits.
    os.access reports both correctly.
    """
    return path.is_file() and os.access(path, os.W_OK)


def make_writable(path: Path) -> None:
    """Clear the read-only state, preserving every other permission bit."""
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IWUSR)


def make_read_only(path: Path) -> None:
    """Restore the read-only state that `lockable` files carry when unlocked."""
    current_mode = path.stat().st_mode
    path.chmod(current_mode & ~_WRITE_BITS)
