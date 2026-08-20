"""Parser for `git status --porcelain=v2 -z --branch`.

Porcelain v2 is a documented, stable, machine-readable format. Nothing here
parses human-facing git output.

Record layout (fields are space separated, records NUL separated):

    # branch.oid <sha>              header
    # branch.head <name>            header
    # branch.upstream <name>        header, absent if no upstream
    # branch.ab +<ahead> -<behind>  header, absent if no upstream
    1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
    2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <Xscore> <path>  followed by <origPath>
    u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
    ? <path>
    ! <path>

XY is a two character code: X is the staged state, Y the working tree state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Value git reports for branch.head when HEAD is not on a branch.
DETACHED_HEAD = "(detached)"

# Number of space-separated fields that precede the path, per record type.
_FIELDS_BEFORE_PATH = {"1": 8, "2": 9, "u": 10}


@dataclass(frozen=True)
class StatusEntry:
    path: str
    xy: str
    unmerged: bool = False
    untracked: bool = False

    @property
    def status(self) -> str:
        """Collapse git's status codes into the states Kiln shows an artist."""
        if self.unmerged:
            return "conflicted"
        if self.untracked:
            return "new"
        if "A" in self.xy:
            return "new"
        if "D" in self.xy:
            return "deleted"
        return "modified"


@dataclass(frozen=True)
class StatusReport:
    branch: str = ""
    head_oid: str = ""
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    entries: list[StatusEntry] = field(default_factory=list)

    @property
    def has_upstream(self) -> bool:
        return bool(self.upstream)

    @property
    def conflicted_paths(self) -> list[str]:
        return [entry.path for entry in self.entries if entry.unmerged]


def status_argv(untracked: str = "all") -> list[str]:
    """The exact arguments Kiln uses to ask for status.

    --no-optional-locks keeps status from taking the index lock, so refreshing
    while an application holds files open is harmless.
    """
    return [
        "--no-optional-locks",
        "status",
        "--porcelain=v2",
        "--branch",
        "-z",
        f"--untracked-files={untracked}",
    ]


def parse_status(raw: str) -> StatusReport:
    """Parse porcelain v2 output into a StatusReport."""
    records = [record for record in raw.split("\0") if record]

    branch = head_oid = upstream = ""
    ahead = behind = 0
    entries: list[StatusEntry] = []

    index = 0
    while index < len(records):
        record = records[index]
        index += 1

        if record.startswith("# "):
            key, _, value = record[2:].partition(" ")
            if key == "branch.head":
                # A detached HEAD is reported as the literal "(detached)"
                # rather than an empty value. Normalising it here means every
                # caller can simply test whether there is a branch.
                branch = "" if value == DETACHED_HEAD else value
            elif key == "branch.oid":
                head_oid = value
            elif key == "branch.upstream":
                upstream = value
            elif key == "branch.ab":
                ahead, behind = _parse_ahead_behind(value)
            continue

        kind = record[0]

        if kind == "?":
            entries.append(StatusEntry(path=record[2:], xy="??", untracked=True))
        elif kind == "!":
            continue  # ignored files are not shown
        elif kind in _FIELDS_BEFORE_PATH:
            parts = record.split(" ", _FIELDS_BEFORE_PATH[kind])
            if len(parts) <= _FIELDS_BEFORE_PATH[kind]:
                continue  # malformed record; skip rather than crash the refresh
            entries.append(
                StatusEntry(path=parts[-1], xy=parts[1], unmerged=(kind == "u"))
            )
            if kind == "2":
                index += 1  # a rename record is followed by its original path

    return StatusReport(
        branch=branch,
        head_oid=head_oid,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        entries=entries,
    )


def _parse_ahead_behind(value: str) -> tuple[int, int]:
    """Parse the '+3 -1' form of the branch.ab header."""
    ahead = behind = 0
    for token in value.split():
        try:
            if token.startswith("+"):
                ahead = int(token[1:])
            elif token.startswith("-"):
                behind = int(token[1:])
        except ValueError:
            continue
    return ahead, behind
