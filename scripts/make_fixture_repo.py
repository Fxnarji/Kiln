"""Build a throwaway repository that looks like a small art project.

Run this to get something to point Kiln at without needing a Gitea server:

    python scripts/make_fixture_repo.py
    python run_kiln.py .fixtures/project/workspace

It creates a bare "server" repo, a working clone, and a second clone standing in
for another artist. Pass --conflict to leave the working clone mid-merge with a
conflicted .blend, which is the state the conflict screen exists for.

Fixtures are written inside the project, under .fixtures/, which is gitignored.
Keeping them in the tree means throwaway repositories never end up scattered
around the parent directory. --into can still point anywhere if you want it to.

Locking is not simulated: that needs a real LFS server. Kiln will report locks
as unavailable, which is itself worth seeing — it is what artists get when the
server is down.
"""

from __future__ import annotations

import argparse
import shutil
import stat
import subprocess
import sys
from pathlib import Path

GITATTRIBUTES = """\
# Binary assets: locked rather than merged.
*.blend  lockable -text
*.spp    lockable -text
*.psd    lockable -text
"""

# Folder -> files, roughly mirroring the layout in the mockup.
PROJECT_FILES = {
    "chars/Character01/src": ["char.blend", "char_face.blend"],
    "chars/Character01/tex": ["char_basecolor.spp"],
    "chars/Character02/src": ["crowd_variants.blend"],
    "env/kits/ruins/src": ["stone_arch_A.blend"],
    "pre/concept/kael": ["silhouettes_v4.psd"],
    "docs": ["pipeline.md"],
}

CONFLICT_FILE = "chars/Character01/src/char.blend"

# Fixtures live inside the project so throwaway repositories never end up
# scattered around the parent directory. Gitignored.
FIXTURE_DIRECTORY = ".fixtures"


def run(argv: list[str], cwd: Path) -> None:
    subprocess.run(argv, cwd=str(cwd), check=True, capture_output=True)


def remove_tree(path: Path) -> None:
    """Delete a directory tree containing a git repository.

    Git marks objects in .git/objects read-only, which stops a plain rmtree on
    Windows. Clearing the flag and retrying is the standard fix.
    """

    def clear_read_only(func, failed_path, _exc_info):
        Path(failed_path).chmod(stat.S_IWRITE)
        func(failed_path)

    shutil.rmtree(path, onerror=clear_read_only)


def configure_identity(repo: Path, name: str, email: str) -> None:
    run(["git", "config", "user.name", name], repo)
    run(["git", "config", "user.email", email], repo)


def write_fake_asset(path: Path, marker: str, kilobytes: int = 16) -> None:
    """Write a file that is plausibly binary, so nothing tries to merge it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".md":
        path.write_text(f"# {path.stem}\n\n{marker}\n", encoding="utf-8")
        return
    payload = marker.encode("utf-8").ljust(64, b"\0")
    path.write_bytes(payload + bytes(kilobytes * 1024))


def build_server(root: Path) -> Path:
    server = root / "server.git"
    server.mkdir(parents=True)
    run(["git", "init", "--bare", "--initial-branch=main"], server)
    return server


def build_workspace(root: Path, server: Path) -> Path:
    workspace = root / "workspace"
    run(["git", "clone", str(server), str(workspace)], root)
    configure_identity(workspace, "Mira", "mira@emberfall.dev")

    (workspace / ".gitattributes").write_text(GITATTRIBUTES, encoding="utf-8")
    for folder, names in PROJECT_FILES.items():
        for name in names:
            write_fake_asset(workspace / folder / name, f"base version of {name}")

    run(["git", "add", "-A"], workspace)
    run(["git", "commit", "-m", "Initial project layout"], workspace)
    run(["git", "push", "-u", "origin", "main"], workspace)
    return workspace


def build_other_artist(root: Path, server: Path) -> Path:
    other = root / "other-artist"
    run(["git", "clone", str(server), str(other)], root)
    configure_identity(other, "Devin", "devin@emberfall.dev")
    return other


def push_server_side_change(other: Path) -> None:
    """Another artist edits and pushes the same file we are about to edit."""
    write_fake_asset(other / CONFLICT_FILE, "DEVIN's version, pushed to the server")
    run(["git", "add", "--", CONFLICT_FILE], other)
    run(["git", "commit", "-m", "Kael shoulder deform fixes"], other)
    run(["git", "push"], other)


def create_local_change(workspace: Path) -> None:
    write_fake_asset(workspace / CONFLICT_FILE, "MY version, not pushed yet")
    run(["git", "add", "--", CONFLICT_FILE], workspace)
    run(["git", "commit", "-m", "Kael torso retopo pass"], workspace)


def leave_mid_merge(workspace: Path) -> None:
    """Fetch and merge so the clone is sitting on a real conflict."""
    run(["git", "fetch", "origin"], workspace)
    subprocess.run(
        ["git", "merge", "--no-edit", "origin/main"],
        cwd=str(workspace),
        capture_output=True,
    )  # expected to fail with a conflict


def default_location(conflict: bool) -> Path:
    """Where a fixture goes unless --into says otherwise.

    Anchored to the project root rather than the working directory, so the
    fixture lands in the same gitignored place however the script is invoked.
    The two variants get separate folders so building one does not quietly
    destroy the other.
    """
    project_root = Path(__file__).resolve().parent.parent
    return project_root / FIXTURE_DIRECTORY / ("conflict" if conflict else "project")


def _display_path(path: Path) -> str:
    """Show a project-relative path when the fixture is inside the project."""
    project_root = Path(__file__).resolve().parent.parent
    try:
        return path.resolve().relative_to(project_root).as_posix()
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--into",
        type=Path,
        default=None,
        help=f"directory to create the fixture in (default: {FIXTURE_DIRECTORY}/...)",
    )
    parser.add_argument(
        "--conflict",
        action="store_true",
        help="leave the workspace mid-merge with a conflicted .blend",
    )
    parser.add_argument(
        "--force", action="store_true", help="delete the target directory first"
    )
    arguments = parser.parse_args(argv)

    chosen = arguments.into or default_location(arguments.conflict)
    root = chosen.resolve()
    if root.exists():
        if not arguments.force:
            print(f"{root} already exists (use --force to replace it)", file=sys.stderr)
            return 1
        remove_tree(root)
    root.mkdir(parents=True)

    server = build_server(root)
    workspace = build_workspace(root, server)
    other = build_other_artist(root, server)

    if arguments.conflict:
        push_server_side_change(other)
        create_local_change(workspace)
        leave_mid_merge(workspace)
    else:
        # A couple of uncommitted edits, so the Changes screen has something.
        write_fake_asset(
            workspace / "chars/Character01/tex/char_basecolor.spp", "work in progress"
        )
        write_fake_asset(
            workspace / "chars/Character02/src/char_blendshapes.blend", "brand new file"
        )

    print(f"fixture ready: {root}")
    print(f"  server     : {server}")
    print(f"  workspace  : {workspace}")
    print(f"  other clone: {other}")
    print()
    print("Open it with:")
    print(f"  python run_kiln.py {_display_path(workspace)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
