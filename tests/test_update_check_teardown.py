"""The update-check worker must survive outliving the bridge.

shutdown() drains the pools with bounded waits, so a check parked in a
network read can return after the bridge's C++ object is destroyed; its
plain ``appUpdateChecked.emit`` then raised RuntimeError ("Signal source
has been deleted") and was logged as a background-worker crash (three
occurrences in the 2026-07-13 debug export, and reproducible by quitting
a headless harness during the startup check). The fix routes worker-side
emits through ``_emit_from_worker``, which resolves the signal by name and
swallows only that teardown RuntimeError.

Qt-free, per the test_audit_backend.py pattern: the real bridge methods are
bound onto a bare stub, the pool runs inline, and signal stand-ins either
record emits or raise like a deleted QObject would.
"""

from __future__ import annotations

import pytest

from tidaler.waves_ui.backend import WavesBridge


class _RecordingSignal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args)


class _DeletedSignal:
    """Emit raises exactly like a signal whose QObject was destroyed."""

    def __init__(self):
        self.attempts = 0

    def emit(self, *args):
        self.attempts += 1
        raise RuntimeError("Signal source has been deleted")


class _InlinePool:
    """Runs the submitted Worker immediately, on this thread."""

    def start(self, worker):
        worker.run()


class _Stub:
    """Bare bridge stand-in: real checkAppUpdate/_emit_from_worker bound on."""

    def __init__(self, updater, signal):
        self._updater = updater
        self.appUpdateChecked = signal
        self.threadpool = _InlinePool()
        for name in ("checkAppUpdate", "_emit_from_worker"):
            setattr(self, name, getattr(WavesBridge, name).__get__(self, _Stub))


class _Updater:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def update_available(self):
        if self._error is not None:
            raise self._error
        return self._result


def test_check_emits_result_while_alive():
    sig = _RecordingSignal()
    stub = _Stub(_Updater(result=(True, "0.1.0", "0.2.0")), sig)
    stub.checkAppUpdate(manual=True)
    assert sig.emits == [(True, "0.1.0", "0.2.0", True)]


def test_check_failure_still_reports_no_update():
    sig = _RecordingSignal()
    stub = _Stub(_Updater(error=OSError("offline")), sig)
    stub.checkAppUpdate()
    assert sig.emits == [(False, "", "", False)]


def test_late_result_after_teardown_is_dropped_quietly():
    # The regression: the worker returns after the bridge is gone. The emit
    # attempt must be swallowed, not escape as a worker crash.
    sig = _DeletedSignal()
    stub = _Stub(_Updater(result=(False, "0.1.0", "0.1.0")), sig)
    stub.checkAppUpdate()  # must not raise
    assert sig.attempts == 1


def test_late_failure_report_after_teardown_is_dropped_quietly():
    sig = _DeletedSignal()
    stub = _Stub(_Updater(error=OSError("offline")), sig)
    stub.checkAppUpdate()  # must not raise
    assert sig.attempts == 1


def test_guard_covers_the_signal_attribute_access_too():
    # On a deleted QObject even reading the signal attribute raises, which is
    # why _emit_from_worker resolves the name inside its guard.
    class _DeletedBridge:
        _emit_from_worker = WavesBridge._emit_from_worker

        @property
        def appUpdateChecked(self):
            raise RuntimeError("Internal C++ object (WavesBridge) already deleted.")

    _DeletedBridge()._emit_from_worker("appUpdateChecked", False, "", "", False)  # must not raise


def test_guard_does_not_hide_programming_errors():
    # Only the teardown RuntimeError is expected traffic; a wrong-arity call
    # (TypeError) must still surface so bugs cannot pass silently.
    class _Arity:
        def emit(self, *args):
            raise TypeError("wrong argument count")

    class _Bridge:
        _emit_from_worker = WavesBridge._emit_from_worker
        appUpdateChecked = _Arity()

    with pytest.raises(TypeError):
        _Bridge()._emit_from_worker("appUpdateChecked", "too", "many")
