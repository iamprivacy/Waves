"""Headless progress bookkeeping, drop-in for the slice of ``rich.progress``
the engine used.

Nothing in Waves ever rendered a progress bar to a terminal: download.py and
the bridge used ``rich.progress.Progress`` purely as a thread-safe task table
(ids, totals, completed counts, percentages) and the QML UI gets numbers via
signals. Compiling rich plus its pygments dependency (150+ modules) into the
binary bought nothing, so this module keeps the exact bookkeeping surface and
the dependency is gone.

Semantics mirror rich for the surface in use:

* task ids are sequential ints equal to the task's index in ``tasks`` (tasks
  are never removed), so ``progress.tasks[task_id]`` works as both a list
  index and an id lookup;
* ``percentage`` is 0.0 with no total (or a zero total) and is clamped to
  [0, 100];
* a task with ``total=None`` never finishes;
* ``Progress.finished`` is true only when every task is finished;
* the task list is guarded by an internal lock and ``tasks`` returns a
  snapshot copy, safe to iterate while worker threads add or update tasks.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

# rich typed task ids as ``NewType("TaskID", int)``; call sites do
# ``TaskID(n)`` and ``int(task.id)``, both of which a plain alias serves.
TaskID = int


@dataclass
class Task:
    """One row of bookkeeping: how far a single item has progressed."""

    id: TaskID
    description: str
    total: float | None = 100.0
    completed: float = 0
    visible: bool = True

    @property
    def finished(self) -> bool:
        return self.total is not None and self.completed >= self.total

    @property
    def percentage(self) -> float:
        if not self.total:
            return 0.0
        return min(100.0, max(0.0, (self.completed / self.total) * 100.0))


class Progress:
    """Thread-safe task table with rich's ``Progress`` bookkeeping API."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: list[Task] = []

    @property
    def tasks(self) -> list[Task]:
        with self._lock:
            return list(self._tasks)

    @property
    def finished(self) -> bool:
        with self._lock:
            return all(task.finished for task in self._tasks)

    def add_task(
        self,
        description: str,
        start: bool = True,
        total: float | None = 100.0,
        completed: float = 0,
        visible: bool = True,
    ) -> TaskID:
        with self._lock:
            task_id = TaskID(len(self._tasks))
            self._tasks.append(
                Task(id=task_id, description=description, total=total, completed=completed, visible=visible)
            )
            return task_id

    def update(
        self,
        task_id: TaskID,
        *,
        total: float | None = None,
        completed: float | None = None,
        advance: float | None = None,
        description: str | None = None,
        visible: bool | None = None,
    ) -> None:
        with self._lock:
            task = self._tasks[task_id]
            if total is not None:
                task.total = total
            if completed is not None:
                task.completed = completed
            if advance is not None:
                task.completed += advance
            if description is not None:
                task.description = description
            if visible is not None:
                task.visible = visible

    def advance(self, task_id: TaskID, advance: float = 1) -> None:
        with self._lock:
            self._tasks[task_id].completed += advance
