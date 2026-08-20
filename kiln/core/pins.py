"""Pinned folder views, with a recursion depth.

A pin is a saved answer to "the part of the project I actually work in".
Depth controls how far down it looks: 1 is the folder itself, 5 is everything.

Pins are stored per clone, keyed by repository path, in the user config
directory. They are never written into the repository.
"""

from __future__ import annotations

from pathlib import Path

from kiln.core.models import DEPTH_UNLIMITED, FileEntry, Pin
from kiln.core.settings import config_directory, read_json, write_json

PINS_FILE_NAME = "pins.json"


def pins_file() -> Path:
    return config_directory() / PINS_FILE_NAME


def load_pins(repo_root: Path) -> list[Pin]:
    """Pins saved for this clone. Returns an empty list for an unknown repo."""
    stored = read_json(pins_file(), {})
    entries = stored.get(_repo_key(repo_root), []) if isinstance(stored, dict) else []
    return [
        Pin(
            path=entry.get("path", ""),
            depth=int(entry.get("depth", 2)),
            label=entry.get("label", ""),
            color=entry.get("color", ""),
        )
        for entry in entries
    ]


def save_pins(repo_root: Path, pins: list[Pin]) -> None:
    stored = read_json(pins_file(), {})
    if not isinstance(stored, dict):
        stored = {}
    stored[_repo_key(repo_root)] = [
        {
            "path": pin.path,
            "depth": pin.depth,
            "label": pin.label,
            "color": pin.color,
        }
        for pin in pins
    ]
    write_json(pins_file(), stored)


def add_pin(repo_root: Path, pin: Pin) -> list[Pin]:
    """Add a pin, replacing any existing pin on the same folder."""
    pins = [existing for existing in load_pins(repo_root) if existing.path != pin.path]
    pins.append(pin)
    pins.sort(key=lambda item: item.path)
    save_pins(repo_root, pins)
    return pins


def remove_pin(repo_root: Path, path: str) -> list[Pin]:
    pins = [existing for existing in load_pins(repo_root) if existing.path != path]
    save_pins(repo_root, pins)
    return pins


def files_in_pin(files: list[FileEntry], pin: Pin) -> list[FileEntry]:
    """The files a pin shows, honouring its depth limit."""
    return [entry for entry in files if pin.contains(entry.path)]


def describe_depth(depth: int) -> str:
    from kiln.core.models import DEPTH_DESCRIPTIONS

    return DEPTH_DESCRIPTIONS.get(depth, f"{depth} levels")


def depth_choices() -> list[int]:
    return list(range(1, DEPTH_UNLIMITED + 1))


def _repo_key(repo_root: Path) -> str:
    """Stable identifier for a clone, so two clones keep separate pins."""
    return str(Path(repo_root).resolve()).replace("\\", "/")
