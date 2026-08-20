"""Commit history, for the home view and the per-file history table."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kiln.git.runner import GitRunner

# Unit separator between fields. %s (the subject) never contains a newline, so
# one commit per line stays unambiguous without needing -z.
FIELD_SEPARATOR = "\x1f"
LOG_FORMAT = FIELD_SEPARATOR.join(["%H", "%an", "%aI", "%s"])

DEFAULT_LIMIT = 50


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    when: datetime | None
    subject: str

    @property
    def short_sha(self) -> str:
        return self.sha[:7]


def recent_commits(
    runner: GitRunner, limit: int = DEFAULT_LIMIT, path: str | None = None
) -> list[Commit]:
    """Commits touching the whole repo, or one file if `path` is given."""
    argv = ["log", f"-n{limit}", f"--format={LOG_FORMAT}"]
    if path:
        argv += ["--", path]

    # An empty repository has no HEAD, which is not an error worth surfacing.
    result = runner.run(*argv, check=False)
    if not result.ok:
        return []
    return parse_log(result.stdout)


def parse_log(raw: str) -> list[Commit]:
    commits: list[Commit] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = line.split(FIELD_SEPARATOR)
        if len(fields) != 4:
            continue
        sha, author, iso_date, subject = fields
        commits.append(
            Commit(
                sha=sha,
                author=author,
                when=_parse_iso(iso_date),
                subject=subject,
            )
        )
    return commits


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
