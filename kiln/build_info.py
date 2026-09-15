"""Which build of Kiln is running, for logs and the update check.

scripts/build_release.py writes build_info.json beside this module just before
PyInstaller runs, and kiln.spec bundles it. Running from source there is no such
file, and Kiln reports itself as a development build — one an update check must
never offer to replace.

No Qt here, so the CLI and tests can read it too.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from kiln import __version__

BUILD_INFO_FILE = Path(__file__).resolve().parent / "build_info.json"


@dataclass(frozen=True)
class BuildInfo:
    version: str
    build: int = 0
    commit: str = ""
    channel: str = "dev"
    built_at: str = ""

    @property
    def is_ci_build(self) -> bool:
        """Only CI builds are numbered, so only they can be updated in place."""
        return self.build > 0

    def is_superseded_by(self, other: "BuildInfo") -> bool:
        """Is `other` a newer build on this build's channel?

        Builds are ordered by CI run number, which only increases, rather than
        by version string: "0.1.0-prototype" says nothing about which of two
        builds is newer. A pull request build is never offered the main
        channel's builds, nor the other way round.
        """
        return (
            self.is_ci_build
            and other.channel == self.channel
            and other.build > self.build
        )

    @property
    def label(self) -> str:
        """Short human form, e.g. "0.1.0 build 42 (main, 1a2b3c4)"."""
        if not self.is_ci_build:
            return f"{self.version} ({self.channel})"
        return f"{self.version} build {self.build} ({self.channel}, {self.commit[:7]})"


def from_dict(data: dict) -> BuildInfo:
    """Build a BuildInfo from a stamp or a release manifest, ignoring extras."""
    return BuildInfo(
        version=str(data.get("version") or __version__),
        build=int(data.get("build") or 0),
        commit=str(data.get("commit") or ""),
        channel=str(data.get("channel") or "dev"),
        built_at=str(data.get("built_at") or ""),
    )


def load(path: Path = BUILD_INFO_FILE) -> BuildInfo:
    """This installation's build. A missing or unreadable stamp means a dev build."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return from_dict(data)
    except (OSError, ValueError, TypeError, AttributeError):
        return BuildInfo(version=__version__)
