"""The one background thread (spec 5.1).

Every git call happens here, one at a time, so the window never freezes and two
git operations can never overlap. Jobs are delivered by a queued signal, which
means Qt's own event loop provides the queue — there is no locking to get wrong.

Usage from the UI thread:

    self.jobs.submit("refresh", repository.snapshot, on_done=self._show_state)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot

log = logging.getLogger(__name__)


@dataclass
class Job:
    name: str  # shown in the status bar while it runs
    function: Callable[[], Any]
    on_done: Callable[[Any], None] | None = None
    on_error: Callable[[Exception], None] | None = None


@dataclass
class JobResult:
    job: Job
    value: Any = None
    error: Exception | None = None
    extra: dict = field(default_factory=dict)


class _Worker(QObject):
    """Lives on the background thread and does the actual work."""

    completed = Signal(object)  # JobResult

    @Slot(object)
    def run(self, job: Job) -> None:
        try:
            value = job.function()
        except Exception as error:  # noqa: BLE001 - reported, never swallowed
            log.exception("job %r failed", job.name)
            self.completed.emit(JobResult(job=job, error=error))
        else:
            self.completed.emit(JobResult(job=job, value=value))


class JobRunner(QObject):
    """UI-thread handle on the background worker."""

    started = Signal(str)  # job name
    finished = Signal(str)  # job name
    idle = Signal()

    _requested = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._pending = 0

        self._thread = QThread()
        self._thread.setObjectName("kiln-git")
        self._worker = _Worker()
        self._worker.moveToThread(self._thread)

        # A queued connection is what serialises the jobs: they are delivered
        # one at a time to the worker's event loop, in the order submitted.
        self._requested.connect(self._worker.run)
        self._worker.completed.connect(self._on_completed)
        self._thread.start()

    def submit(
        self,
        name: str,
        function: Callable[[], Any],
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        job = Job(name=name, function=function, on_done=on_done, on_error=on_error)
        self._pending += 1
        self.started.emit(name)
        self._requested.emit(job)

    @property
    def busy(self) -> bool:
        return self._pending > 0

    @Slot(object)
    def _on_completed(self, result: JobResult) -> None:
        self._pending = max(0, self._pending - 1)
        try:
            if result.error is not None:
                if result.job.on_error is not None:
                    result.job.on_error(result.error)
            elif result.job.on_done is not None:
                result.job.on_done(result.value)
        finally:
            self.finished.emit(result.job.name)
            if self._pending == 0:
                self.idle.emit()

    def shutdown(self) -> None:
        """Stop the thread cleanly on application exit."""
        self._thread.quit()
        self._thread.wait(5000)
