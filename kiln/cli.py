"""Command line entry point.

This exists so that integrations — the Blender addon first — can lock and query
files without importing Qt or reimplementing any of the locking rules. It is
the same code path the GUI uses, so the two cannot drift apart.

    kiln status [--json]
    kiln locks
    kiln lock <path>
    kiln unlock <path>
    kiln file <path>

Exit codes: 0 success, 1 refused or failed, 2 bad usage.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from kiln.core.models import describe_size
from kiln.core.repository import Repository
from kiln.errors import KilnError

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kiln", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="path inside the repository (default: current directory)",
    )
    parser.add_argument("--verbose", action="store_true", help="log every git command")

    subcommands = parser.add_subparsers(dest="command", required=True)

    status_command = subcommands.add_parser("status", help="summarise the repository")
    status_command.add_argument("--json", action="store_true", help="machine readable")

    subcommands.add_parser("locks", help="list locks held on the server")

    for name, help_text in (
        ("lock", "take the lock on a file"),
        ("unlock", "release your lock on a file"),
        ("file", "report one file's state"),
    ):
        command = subcommands.add_parser(name, help=help_text)
        command.add_argument("path", help="path to the file")

    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if arguments.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        repository = Repository.open(arguments.repo)
        handler = {
            "status": _status,
            "locks": _locks,
            "lock": _lock,
            "unlock": _unlock,
            "file": _file,
        }[arguments.command]
        return handler(repository, arguments)
    except KilnError as error:
        print(f"kiln: {error}", file=sys.stderr)
        return EXIT_FAILED


# -- commands ---------------------------------------------------------------


def _status(repository: Repository, arguments: argparse.Namespace) -> int:
    state = repository.snapshot()
    if arguments.json:
        print(
            json.dumps(
                {
                    "root": state.root,
                    "branch": state.branch,
                    "ahead": state.ahead,
                    "behind": state.behind,
                    "online": state.online,
                    "blocked": state.blocked_reason,
                    "merge_in_progress": state.merge_in_progress,
                    "changed": [entry.path for entry in state.changed_files],
                    "conflicted": [entry.path for entry in state.conflicted_files],
                    "my_locks": [entry.path for entry in state.my_locks],
                },
                indent=2,
            )
        )
        return EXIT_OK

    print(f"repository : {state.root}")
    print(f"branch     : {state.branch or '(detached)'}")
    print(f"ahead      : {state.ahead}    behind: {state.behind}")
    print(f"locks      : {state.lock_freshness_description()}")
    if state.blocked_reason:
        print(f"BLOCKED    : {state.blocked_reason}")
    if state.merge_in_progress:
        print(f"merge      : in progress, {len(state.conflicted_files)} conflicted")
    print(f"changed    : {len(state.changed_files)} file(s)")
    for entry in state.changed_files:
        print(f"  {entry.status:<10} {entry.path}")
    return EXIT_OK


def _locks(repository: Repository, _arguments: argparse.Namespace) -> int:
    state = repository.snapshot()
    if not state.locks_available:
        print("kiln: lock server unreachable", file=sys.stderr)
        return EXIT_FAILED

    held = [entry for entry in state.files if entry.lock is not None]
    if not held:
        print("no locks held")
        return EXIT_OK
    for entry in held:
        owner = "you" if entry.locked_by_me else entry.lock.owner
        print(f"{owner:<24} {entry.path}")
    return EXIT_OK


def _lock(repository: Repository, arguments: argparse.Namespace) -> int:
    relative = _relative_path(repository, arguments.path)
    prepared = repository.prepare_for_editing(relative)
    print(f"locked {relative}")
    if prepared.permission_fixed:
        print("note: cleared the read-only flag manually")
    return EXIT_OK


def _unlock(repository: Repository, arguments: argparse.Namespace) -> int:
    relative = _relative_path(repository, arguments.path)
    repository.release_lock(relative)
    print(f"released {relative}")
    return EXIT_OK


def _file(repository: Repository, arguments: argparse.Namespace) -> int:
    relative = _relative_path(repository, arguments.path)
    entry = repository.snapshot().find(relative)
    if entry is None:
        print(f"kiln: {relative} is not tracked", file=sys.stderr)
        return EXIT_FAILED

    print(
        json.dumps(
            {
                "path": entry.path,
                "status": entry.status,
                "downloaded": entry.downloaded,
                "writable": entry.writable,
                "size": describe_size(entry.size),
                "lock": entry.lock_description(),
                "locked_by_me": entry.locked_by_me,
            },
            indent=2,
        )
    )
    return EXIT_OK


def _relative_path(repository: Repository, given: str) -> str:
    """Turn whatever the caller passed into a repo-relative POSIX path.

    Accepts an absolute path, a path relative to the working directory, or a
    path already relative to the repository root — the last being what a script
    or the Blender addon will naturally have.
    """
    candidate = Path(given)
    attempts = [candidate] if candidate.is_absolute() else [
        Path.cwd() / candidate,
        repository.root / candidate,
    ]

    for attempt in attempts:
        try:
            return attempt.resolve().relative_to(repository.root).as_posix()
        except ValueError:
            continue

    raise KilnError(f"{given} is not inside {repository.root}")


if __name__ == "__main__":
    sys.exit(main())
