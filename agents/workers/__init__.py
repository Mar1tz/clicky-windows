"""Worker layer: one BaseWorker per kind of work, plus a registry.

The runtime looks a worker up by `task.worker_type` (the classifier sets it)
and calls `execute_step` for each step. `default_workers()` builds a registry
with all six concrete types registered; `get` falls back to the coding worker
for an unknown type, because CODING/GENERAL is the safe generalist.
"""
from __future__ import annotations

from typing import Optional

from agents.core import BaseWorker

from .base import Worker
from .browser import BrowserWorker
from .coding import CodingWorker
from .computer import ComputerWorker
from .file import FileWorker
from .research import ResearchWorker
from .terminal import TerminalWorker

__all__ = [
    "Worker", "CodingWorker", "FileWorker", "TerminalWorker", "ResearchWorker",
    "BrowserWorker", "ComputerWorker", "WorkerRegistry", "default_workers",
]

_DEFAULT_TYPE = "coding"


class WorkerRegistry:
    """Maps a worker_type string to a worker instance."""

    def __init__(self) -> None:
        self._workers: dict[str, BaseWorker] = {}

    def register(self, worker: BaseWorker) -> None:
        self._workers[worker.worker_type] = worker

    def get(self, worker_type: str) -> BaseWorker:
        """The worker for `worker_type`. Falls back to the coding worker (the
        generalist) for an unknown type rather than failing a task over a
        classifier quirk."""
        worker = self._workers.get(worker_type)
        if worker is not None:
            return worker
        if _DEFAULT_TYPE in self._workers:
            return self._workers[_DEFAULT_TYPE]
        raise KeyError(f"no worker registered for {worker_type!r} and no default")

    def types(self) -> list[str]:
        return list(self._workers)

    def __contains__(self, worker_type: str) -> bool:
        return worker_type in self._workers


def default_workers() -> WorkerRegistry:
    reg = WorkerRegistry()
    for cls in (CodingWorker, FileWorker, TerminalWorker, ResearchWorker,
                BrowserWorker, ComputerWorker):
        reg.register(cls())
    return reg
