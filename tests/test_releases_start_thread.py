"""A sign-in finishes on a pool worker, and ``_set_logged_in(True)`` starts the
release sweep from there. A QTimer started from a thread other than its own
never fires (Qt only prints a warning), so the start must be posted back to
the GUI thread: these tests run the real mixin methods on a real QObject and
flip the flag from a plain Python thread.
"""

from __future__ import annotations

import threading

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")

from waves.waves_ui.bridge_releases import ReleasesMixin  # noqa: E402


class _Host(QtCore.QObject):
    _relStartAsked = QtCore.Signal()
    _releases_maybe_start = ReleasesMixin._releases_maybe_start
    _releases_on_gui_thread = ReleasesMixin._releases_on_gui_thread
    _releases_rearm = ReleasesMixin._releases_rearm

    def __init__(self) -> None:
        super().__init__()
        self._logged_in = True
        self._rel_revealed = True
        self._rel_step_running = False
        self._rel_mirror_due = False
        self._rel_timer = QtCore.QTimer(self)
        self._rel_timer.setSingleShot(True)
        self.moved_on: list = []
        self.ticks: list = []
        self._rel_timer.timeout.connect(lambda: self.ticks.append(1))
        self._relStartAsked.connect(self._releases_maybe_start, QtCore.Qt.ConnectionType.QueuedConnection)

    def _releases_library_moved(self) -> None:
        self.moved_on.append(QtCore.QThread.currentThread() is self.thread())


@pytest.fixture
def app():
    return QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])


def test_a_start_from_a_worker_arms_the_timer_on_the_gui_thread(app):
    host = _Host()
    t = threading.Thread(target=host._releases_maybe_start)
    t.start()
    t.join()
    # Nothing ran on the worker: the start waits for the GUI thread.
    assert not host._rel_timer.isActive() and host.moved_on == []
    for _ in range(5):
        app.processEvents()
    # The timer really fired (one started off-thread never does).
    assert host.ticks == [1] and host._rel_mirror_due is True
    assert host.moved_on == [True]


def test_a_start_on_the_gui_thread_runs_at_once(app):
    host = _Host()
    host._releases_maybe_start()
    assert host._rel_timer.isActive() and host.moved_on == [True]
