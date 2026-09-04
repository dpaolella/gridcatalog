"""Job queue protocol (ADR-0002).

Harvest, enrichment, projection, link-health probing and recompute batches are
queued work. In production that is Celery on Redis; in development and test it
is an eager in-process queue so a whole pipeline can be exercised in one test.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from datahub.config import QueueBackend as QueueKind
from datahub.config import Settings, get_settings

log = logging.getLogger(__name__)

TaskFn = Callable[..., Any]


@dataclass(slots=True)
class JobResult:
    job_id: str
    task: str
    state: str  # queued | running | success | failure
    result: Any = None
    error: str | None = None
    queued_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


class JobQueue(ABC):
    """Registry plus dispatch. Tasks are named so they can be enqueued by name."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskFn] = {}

    def task(self, name: str) -> Callable[[TaskFn], TaskFn]:
        def decorate(fn: TaskFn) -> TaskFn:
            self.register(name, fn)
            return fn

        return decorate

    def register(self, name: str, fn: TaskFn) -> None:
        if name in self._tasks and self._tasks[name] is not fn:
            raise ValueError(f"task already registered under a different function: {name}")
        self._tasks[name] = fn

    def resolve(self, name: str) -> TaskFn:
        if name not in self._tasks:
            raise KeyError(f"unknown task: {name!r}; registered: {sorted(self._tasks)}")
        return self._tasks[name]

    @property
    def task_names(self) -> list[str]:
        return sorted(self._tasks)

    @abstractmethod
    def enqueue(self, name: str, *args: Any, **kwargs: Any) -> JobResult: ...


class EagerQueue(JobQueue):
    """Runs the task immediately, in-process. Failures propagate as results."""

    def __init__(self) -> None:
        super().__init__()
        self.history: list[JobResult] = []

    def enqueue(self, name: str, *args: Any, **kwargs: Any) -> JobResult:
        fn = self.resolve(name)
        job = JobResult(job_id=uuid.uuid4().hex, task=name, state="running")
        try:
            job.result = fn(*args, **kwargs)
            job.state = "success"
        except Exception as exc:
            job.state = "failure"
            job.error = f"{type(exc).__name__}: {exc}"
            log.exception("task %s failed", name)
        job.finished_at = datetime.now(UTC)
        self.history.append(job)
        return job


class CeleryQueue(JobQueue):
    """Celery on Redis. Registration is deferred to the Celery app."""

    def __init__(self, broker_url: str, app: Any | None = None) -> None:
        super().__init__()
        if app is None:
            from celery import Celery  # imported lazily: optional extra

            app = Celery("datahub", broker=broker_url, backend=broker_url)
        self.app = app

    def register(self, name: str, fn: TaskFn) -> None:
        super().register(name, fn)
        self.app.task(name=name)(fn)

    def enqueue(self, name: str, *args: Any, **kwargs: Any) -> JobResult:
        self.resolve(name)
        async_result = self.app.send_task(name, args=args, kwargs=kwargs)
        return JobResult(job_id=async_result.id, task=name, state="queued")


_QUEUE: JobQueue | None = None


def get_queue(settings: Settings | None = None) -> JobQueue:
    """Process-wide queue. Tasks register against it at import time."""
    global _QUEUE
    if _QUEUE is None:
        settings = settings or get_settings()
        _QUEUE = (
            CeleryQueue(settings.redis_url)
            if settings.queue_backend is QueueKind.CELERY
            else EagerQueue()
        )
    return _QUEUE


def reset_queue() -> None:
    global _QUEUE
    _QUEUE = None
