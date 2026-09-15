"""Build Kiln and package it as a single zip to hand out.

    python scripts/build_release.py

The zip is the point. PyInstaller's one-folder output is a small launcher plus
a 110 MB `_internal` directory it cannot start without, and the launcher is the
only part that looks like the application — so sending "Kiln.exe" on its own is
the obvious mistake to make, and it fails with:

    Failed to load Python DLL '...\\_internal\\python311.dll'

There is no way to catch that from inside the application: it happens before
Python starts. The only fix is to never hand out a loose exe, so this script
produces one file whose name makes clear it must be extracted.

Every build is stamped (kiln/build_info.json, bundled by kiln.spec) and gets a
manifest beside it (dist/Kiln-<platform>.json) listing each artifact with its
size and SHA-256. The stamp tells a running Kiln what it is; the manifest tells
it what the newest build is and how to verify the download. Together they are
everything an update button needs. CI supplies the stamp through KILN_BUILD_NUMBER,
KILN_COMMIT and KILN_CHANNEL; a local build is build 0 on the "local" channel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = PROJECT_ROOT / "kiln.spec"
BUILD_INFO_FILE = PROJECT_ROOT / "kiln" / "build_info.json"
BUILD_OUTPUT = PROJECT_ROOT / "dist" / "Kiln"
PORTABLE_BUILD = PROJECT_ROOT / "dist" / "Kiln-portable.exe"

# Bump when the manifest layout changes incompatibly, so an older Kiln can tell
# it is looking at something it does not understand rather than misreading it.
MANIFEST_SCHEMA = 1

INSTRUCTIONS = """\
Kiln
====

1. Extract this ENTIRE folder somewhere sensible, for example
   C:\\Kiln  or  Documents\\Kiln

2. Run Kiln.exe from the extracted folder.

Do not copy Kiln.exe out on its own. It is only a launcher and needs the
_internal folder sitting next to it. On its own it fails with
"Failed to load Python DLL".

Kiln needs Git and Git LFS installed and on PATH. It will say so if they
are missing.
"""


def platform_name() -> str:
    return "windows" if sys.platform == "win32" else sys.platform


def read_version() -> str:
    """Read __version__ without importing kiln (which would need PySide6)."""
    text = (PROJECT_ROOT / "kiln" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "unknown"


def current_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(PROJECT_ROOT), capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def stamp_build_info(version: str, build: int, commit: str, channel: str) -> dict:
    """Write the stamp kiln.spec bundles, and return it for the manifest."""
    info = {
        "version": version,
        "build": build,
        "commit": commit,
        "channel": channel,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    BUILD_INFO_FILE.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


def run_pyinstaller() -> None:
    print("building...")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC_FILE)],
        cwd=str(PROJECT_ROOT),
        check=True,
    )


def make_zip(destination: Path) -> Path:
    """Zip the build folder, keeping `Kiln/` as the top level inside."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    files = sorted(path for path in BUILD_OUTPUT.rglob("*") if path.is_file())
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, Path("Kiln") / path.relative_to(BUILD_OUTPUT))
        archive.writestr("Kiln/HOW-TO-RUN.txt", INSTRUCTIONS)

    return destination


def collect_portable(destination: Path) -> Path | None:
    """Rename the one-file build to carry its version, ready to send."""
    if not PORTABLE_BUILD.is_file():
        return None
    if destination.exists():
        destination.unlink()
    PORTABLE_BUILD.replace(destination)
    return destination


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(destination: Path, info: dict, artifacts: dict[str, Path]) -> Path:
    """Describe this build and its downloads for an update check."""
    manifest = {
        "schema": MANIFEST_SCHEMA,
        **info,
        "platform": platform_name(),
        "files": [
            {
                "kind": kind,
                "name": path.name,
                "size": path.stat().st_size,
                "sha256": sha256_of(path),
            }
            for kind, path in artifacts.items()
        ],
    }
    destination.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="package the existing dist/Kiln instead of rebuilding",
    )
    parser.add_argument(
        "--build-number",
        type=int,
        default=int(os.environ.get("KILN_BUILD_NUMBER") or 0),
        help="increasing build number; CI passes its run number (default: 0)",
    )
    parser.add_argument(
        "--commit",
        default=os.environ.get("KILN_COMMIT") or current_commit(),
        help="commit the build was made from (default: HEAD)",
    )
    parser.add_argument(
        "--channel",
        default=os.environ.get("KILN_CHANNEL") or "local",
        help="which stream of builds this belongs to, e.g. main (default: local)",
    )
    arguments = parser.parse_args(argv)

    version = read_version()
    info = stamp_build_info(
        version, arguments.build_number, arguments.commit, arguments.channel
    )

    if not arguments.skip_build:
        run_pyinstaller()

    if not BUILD_OUTPUT.is_dir():
        print(f"nothing to package: {BUILD_OUTPUT} does not exist", file=sys.stderr)
        return 1

    build_suffix = f"-b{arguments.build_number}" if arguments.build_number else ""
    stem = f"Kiln-{version}{build_suffix}-{platform_name()}"

    artifacts = {"zip": make_zip(PROJECT_ROOT / "dist" / f"{stem}.zip")}
    portable = collect_portable(PROJECT_ROOT / "dist" / f"{stem}.exe")
    if portable is not None:
        artifacts["portable"] = portable
    manifest = write_manifest(
        PROJECT_ROOT / "dist" / f"Kiln-{platform_name()}.json", info, artifacts
    )

    print()
    print("Ready to hand out:")
    for path in artifacts.values():
        print(f"  {path.name:<40} {path.stat().st_size / 1_000_000:.0f} MB")
    print(f"  {manifest.name:<40} manifest for update checks")
    print()
    print("Either is safe to send. The .exe needs no extracting but takes about")
    print("a second longer to start; the .zip must be extracted whole.")
    print()
    print("Do NOT send dist/Kiln/Kiln.exe — that one is only a launcher and")
    print("cannot start without the _internal folder beside it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
