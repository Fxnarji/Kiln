"""Where Kiln keeps local preferences.

Pins are preference, not project data, so they live outside the repository and
are never committed. The location is derived from environment variables rather
than any OS API, which keeps this module portable.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

APPLICATION_NAME = "Kiln"
LAST_PROJECT_FILE_NAME = "last-project.json"


def config_directory() -> Path:
    """Per-user configuration directory, created on demand.

    Windows: %APPDATA%\\Kiln
    Linux:   $XDG_CONFIG_HOME/kiln, or ~/.config/kiln
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        directory = Path(appdata) / APPLICATION_NAME
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
        directory = Path(base) / APPLICATION_NAME.lower()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def log_file_path() -> Path:
    return config_directory() / "kiln.log"


def load_last_project() -> Path | None:
    stored = read_json(config_directory() / LAST_PROJECT_FILE_NAME, {})
    value = stored.get("path") if isinstance(stored, dict) else None
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return path if path.is_dir() else None


def save_last_project(path: Path) -> None:
    write_json(
        config_directory() / LAST_PROJECT_FILE_NAME,
        {"path": str(path.resolve())},
    )


def read_json(path: Path, default: dict | list) -> dict | list:
    """Read a JSON file, falling back to `default` on anything unexpected.

    A corrupted preferences file must never stop the application starting.
    """
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("could not read %s (%s); using defaults", path, exc)
        return default


def write_json(path: Path, payload: dict | list) -> None:
    """Write JSON atomically, so an interrupted write cannot corrupt the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(path)
