"""Per-file commit history, read once per commit and shared by every panel.

A file's history only changes when HEAD does, so each answer is kept until the
next commit, pull, or merge. That matters because a selection is re-made far
more often than a person picks a file: every refresh rebuilds each list and
selects the same file again, the main window's grid and table both report the
one click, and Changes and the main window can show the same file at once.
Without the cache each of those was another `git log` in the queue.

Two other kinds of wasted read are dropped:

- the same file asked for while its read is still queued joins that read
- a queued read whose file is no longer wanted by anyone (arrow-keying down a
  folder queues one per file) is skipped when its turn comes
"""

from __future__ import annotations

from typing import Callable

from kiln.core.repository import Repository
from kiln.ui.worker import JobRunner

HISTORY_LIMIT = 20

Wanted = Callable[[], bool]
Deliver = Callable[[list], None]


class HistoryLoader:
    def __init__(self, jobs: JobRunner, repository: Repository):
        self._jobs = jobs
        self._repository = repository
        self._head: str | None = None
        self._cache: dict[str, list] = {}
        # Path -> who asked, for reads still queued or running. Each asker
        # says whether it still wants the answer; `wanted` is read on the
        # worker thread, so it must only compare plain attributes.
        self._waiting: dict[str, list[tuple[Wanted, Deliver]]] = {}

    def request(self, path: str, head: str, wanted: Wanted, deliver: Deliver) -> None:
        """Hand `deliver` this file's history, at once if it is known."""
        if head != self._head:
            self._head = head
            self._cache.clear()

        cached = self._cache.get(path)
        if cached is not None:
            deliver(cached)
            return

        waiting = self._waiting.get(path)
        if waiting is not None:
            waiting.append((wanted, deliver))
            return
        self._waiting[path] = [(wanted, deliver)]

        def work():
            askers = list(self._waiting.get(path, ()))
            if not any(still_wanted() for still_wanted, _ in askers):
                return None
            return self._repository.commits(limit=HISTORY_LIMIT, path=path)

        self._jobs.submit(
            "reading history",
            work,
            on_done=lambda commits: self._arrived(path, head, commits),
            on_error=lambda _error: self._waiting.pop(path, None),
        )

    def _arrived(self, path: str, head: str, commits: list | None) -> None:
        askers = self._waiting.pop(path, [])
        if commits is None:
            # Skipped because nobody wanted it then. Anyone who has asked
            # again since gets a fresh read.
            for still_wanted, deliver in askers:
                if still_wanted():
                    self.request(path, head, still_wanted, deliver)
            return

        if head == self._head:
            self._cache[path] = commits
        for still_wanted, deliver in askers:
            if still_wanted():
                deliver(commits)
