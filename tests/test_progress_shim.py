"""Pin the waves.progress shim to the rich bookkeeping semantics it replaced.

``waves.progress`` stands in for the slice of ``rich.progress`` the engine
used as a headless task table (rich itself rendered nothing here and is no
longer a dependency). The engine's loops gate on these exact behaviors, so
each one is pinned:

* task ids are sequential and equal to the task's index in ``tasks``, which
  is what lets ``progress.tasks[task_id]`` serve as an id lookup;
* ``percentage`` clamps to [0, 100] and is 0.0 for a missing or zero total;
* a ``total=None`` task never finishes (the single-URL track task stays
  unfinished until snapped complete), and ``Progress.finished`` needs every
  task done, which is the collection-loop termination condition;
* the task list survives concurrent add/advance from worker threads and
  ``tasks`` hands back a snapshot safe to iterate during mutation (the
  bridge's 500 ms track poll does exactly that).
"""

from __future__ import annotations

import threading

from waves.progress import Progress, TaskID


def test_ids_are_sequential_and_index_the_task_list():
    p = Progress()
    ids = [p.add_task(f"t{i}", total=10) for i in range(5)]
    assert ids == [0, 1, 2, 3, 4]
    for i in ids:
        assert p.tasks[i].id == i
    assert int(p.tasks[3].id) == 3  # the poller does int(t.id)
    assert TaskID(3) == 3


def test_percentage_clamps_and_survives_missing_total():
    p = Progress()
    t = p.add_task("clamp", total=10)
    p.update(t, completed=25)
    assert p.tasks[t].percentage == 100.0
    none_total = p.add_task("unknown", total=None)
    assert p.tasks[none_total].percentage == 0.0
    zero_total = p.add_task("empty", total=0)
    assert p.tasks[zero_total].percentage == 0.0
    half = p.add_task("half", total=200)
    p.update(half, completed=100)
    assert p.tasks[half].percentage == 50.0


def test_none_total_never_finishes():
    p = Progress()
    t = p.add_task("stream", total=None)
    for _ in range(1000):
        p.advance(t)
    assert not p.tasks[t].finished
    assert not p.finished


def test_finished_flips_at_total_and_progress_needs_every_task():
    p = Progress()
    a = p.add_task("a", total=3)
    b = p.add_task("b", total=2)
    p.advance(a)
    p.advance(a)
    assert not p.tasks[a].finished
    p.advance(a)
    assert p.tasks[a].finished
    assert not p.finished  # b still open
    p.update(b, completed=p.tasks[b].total)
    assert p.tasks[b].finished
    assert p.finished


def test_add_task_accepts_the_engine_call_shapes():
    p = Progress()
    # download.py: description + total= + visible=
    t = p.add_task("[blue]Item 'x'", total=None, visible=False)
    assert p.tasks[t].visible is False
    # test fixtures: completed= at creation
    done = p.add_task("failed track", total=100, completed=0)
    assert p.tasks[done].completed == 0
    # bare default matches rich's total=100.0
    d = p.add_task("default")
    assert p.tasks[d].total == 100.0


def test_snapshot_iteration_is_safe_under_concurrent_mutation():
    # Bounded churn: each worker adds a FIXED number of tasks (an open-ended
    # while loop here grows the table faster than the snapshot reader can
    # copy it and the test never ends).
    p = Progress()
    seed = [p.add_task(f"seed{i}", total=100) for i in range(8)]
    per_thread = 400
    errors: list[BaseException] = []

    def churn():
        try:
            for _ in range(per_thread):
                tid = p.add_task("worker", total=50)
                p.advance(tid, 10)
                p.update(seed[tid % len(seed)], completed=tid % 100)
        except BaseException as exc:  # - the test reports any failure
            errors.append(exc)

    threads = [threading.Thread(target=churn) for _ in range(4)]
    for th in threads:
        th.start()
    try:
        while any(th.is_alive() for th in threads):
            snapshot = p.tasks
            # The poller's exact read: build {id: percentage} from a snapshot.
            seen = {int(t.id): t.percentage for t in snapshot}
            assert all(0.0 <= v <= 100.0 for v in seen.values())
            assert list(seen) == sorted(seen)
    finally:
        for th in threads:
            th.join(timeout=10)
    assert not errors
    # After the dust settles, the id == index invariant still holds everywhere.
    final = p.tasks
    assert [t.id for t in final] == list(range(len(final)))
    assert len(final) == len(seed) + 4 * per_thread
