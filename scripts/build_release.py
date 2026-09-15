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
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = PROJECT_ROOT / "kiln.spec"
BUILD_OUTPUT = PROJECT_ROOT / "dist" / "Kiln"
PORTABLE_BUILD = PROJECT_ROOT / "dist" / "Kiln-portable.exe"

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="package the existing dist/Kiln instead of rebuilding",
    )
    arguments = parser.parse_args(argv)

    if not arguments.skip_build:
        run_pyinstaller()

    if not BUILD_OUTPUT.is_dir():
        print(f"nothing to package: {BUILD_OUTPUT} does not exist", file=sys.stderr)
        return 1

    version = read_version()
    stem = f"Kiln-{version}-{platform_name()}"

    artifacts = [make_zip(PROJECT_ROOT / "dist" / f"{stem}.zip")]
    portable = collect_portable(PROJECT_ROOT / "dist" / f"{stem}.exe")
    if portable is not None:
        artifacts.append(portable)

    print()
    print("Ready to hand out:")
    for path in artifacts:
        print(f"  {path.name:<40} {path.stat().st_size / 1_000_000:.0f} MB")
    print()
    print("Either is safe to send. The .exe needs no extracting but takes about")
    print("a second longer to start; the .zip must be extracted whole.")
    print()
    print("Do NOT send dist/Kiln/Kiln.exe — that one is only a launcher and")
    print("cannot start without the _internal folder beside it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
