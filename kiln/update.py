"""Finding and downloading a newer build of Kiln.

CI publishes each branch's latest build to a rolling GitHub pre-release tagged
latest-<channel> (see .github/workflows/build.yml), next to a manifest that
lists every file with its size and SHA-256. Checking for an update is fetching
that manifest and asking kiln.build_info whether it describes a newer build on
this build's channel; downloading one is fetching a file it lists and refusing
it unless the size and hash match.

Replacing the running installation is kiln.installer's job. No Qt here: the
dialog runs these functions on a background thread.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kiln import __version__, build_info
from kiln.build_info import BuildInfo

UPDATE_REPOSITORY = "Fxnarji/Kiln"

# The manifest layout this Kiln understands; scripts/build_release.py writes it.
MANIFEST_SCHEMA = 1

CHECK_TIMEOUT_SECONDS = 15
DOWNLOAD_TIMEOUT_SECONDS = 60
CHUNK_SIZE = 256 * 1024

Opener = Callable[..., object]


class UpdateError(Exception):
    """Checking or downloading failed. The message is fit to show an artist."""


class DownloadCancelled(UpdateError):
    pass


@dataclass(frozen=True)
class ReleaseFile:
    kind: str  # "zip" or "portable"
    name: str
    size: int
    sha256: str
    url: str


@dataclass(frozen=True)
class Release:
    """One published build, as its manifest describes it."""

    build: BuildInfo
    files: tuple[ReleaseFile, ...]

    def file(self, kind: str) -> ReleaseFile | None:
        return next((item for item in self.files if item.kind == kind), None)


def platform_name() -> str:
    """Matches scripts/build_release.py, which names the manifest after it."""
    return "windows" if sys.platform == "win32" else sys.platform


def release_url(channel: str, repository: str = UPDATE_REPOSITORY) -> str:
    """Where a person can see the channel's latest build in a browser."""
    return f"https://github.com/{repository}/releases/tag/latest-{channel}"


def download_base_url(channel: str, repository: str = UPDATE_REPOSITORY) -> str:
    return f"https://github.com/{repository}/releases/download/latest-{channel}/"


def manifest_url(
    channel: str, platform: str | None = None, repository: str = UPDATE_REPOSITORY
) -> str:
    platform = platform or platform_name()
    return download_base_url(channel, repository) + f"Kiln-{platform}.json"


def parse_manifest(data: object, base_url: str) -> Release:
    """Turn a manifest into a Release, refusing anything this Kiln can't read."""
    if not isinstance(data, dict):
        raise UpdateError("The update information is not in a format Kiln understands.")
    schema = data.get("schema")
    if schema != MANIFEST_SCHEMA:
        raise UpdateError(
            f"The update information uses format {schema!r}, which this version of "
            "Kiln does not understand. Download the new version by hand."
        )

    files = []
    for entry in data.get("files") or []:
        try:
            name = str(entry["name"])
            files.append(
                ReleaseFile(
                    kind=str(entry["kind"]),
                    name=name,
                    size=int(entry["size"]),
                    sha256=str(entry["sha256"]).lower(),
                    url=base_url + urllib.parse.quote(name),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise UpdateError("The update information is incomplete.") from error
        if "/" in name or "\\" in name:
            raise UpdateError("The update information names a file Kiln will not download.")

    return Release(build=build_info.from_dict(data), files=tuple(files))


def fetch_release(
    channel: str,
    platform: str | None = None,
    repository: str = UPDATE_REPOSITORY,
    opener: Opener = urllib.request.urlopen,
) -> Release:
    """Read the latest published build on `channel`."""
    url = manifest_url(channel, platform, repository)
    try:
        with opener(_request(url), timeout=CHECK_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise UpdateError(
                f"No build has been published for the {channel!r} channel yet."
            ) from error
        raise UpdateError(f"GitHub answered with an error ({error.code}).") from error
    except (urllib.error.URLError, OSError) as error:
        raise UpdateError(f"Could not reach GitHub ({_reason(error)}).") from error
    except ValueError as error:
        raise UpdateError("The update information could not be read.") from error
    return parse_manifest(data, download_base_url(channel, repository))


def find_update(
    current: BuildInfo,
    platform: str | None = None,
    repository: str = UPDATE_REPOSITORY,
    opener: Opener = urllib.request.urlopen,
) -> Release | None:
    """The newer build on this build's channel, or None if this one is newest.

    A development build asks nobody: it has no channel to follow and nothing
    it could safely be replaced with.
    """
    if not current.is_ci_build:
        return None
    release = fetch_release(current.channel, platform, repository, opener)
    return release if current.is_superseded_by(release.build) else None


def download(
    item: ReleaseFile,
    destination: Path,
    progress: Callable[[int, int], None] | None = None,
    cancelled: threading.Event | None = None,
    opener: Opener = urllib.request.urlopen,
) -> Path:
    """Download `item` to `destination`, verified against the manifest.

    The data goes to a .part file first and is only renamed into place once
    its size and SHA-256 match, so `destination` never holds a partial or
    tampered download. `progress` is called with (bytes so far, total).
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    digest = hashlib.sha256()
    received = 0

    try:
        with opener(_request(item.url), timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            with partial.open("wb") as handle:
                while True:
                    if cancelled is not None and cancelled.is_set():
                        raise DownloadCancelled("The download was cancelled.")
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > item.size:
                        raise UpdateError(
                            f"{item.name} is larger than the update information says."
                        )
                    digest.update(chunk)
                    handle.write(chunk)
                    if progress is not None:
                        progress(received, item.size)
    except UpdateError:
        _remove(partial)
        raise
    except (urllib.error.URLError, OSError) as error:
        _remove(partial)
        raise UpdateError(f"Downloading {item.name} failed ({_reason(error)}).") from error

    if received != item.size or digest.hexdigest() != item.sha256:
        _remove(partial)
        raise UpdateError(
            f"{item.name} did not match the update information, so it was thrown "
            "away. Try again; if it keeps happening, download it by hand."
        )

    partial.replace(destination)
    return destination


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": f"Kiln/{__version__}"})


def _reason(error: Exception) -> str:
    return str(getattr(error, "reason", None) or error)


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
