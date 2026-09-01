"""Regression guard: crash.log must be scrubbed like every other log sink.

THE BUG
-------
``_install_crash_diagnostics._record`` logged the exception once through the
logger (where ``_RedactingFilter`` scrubs it) and then wrote **the same
traceback a second time** into a bare ``open()`` handle that has no filter,
formatter or scrubber attached. The frame paths and, decisively, the
exception's own message reached ``crash.log`` verbatim.

That is the file ``.github/ISSUE_TEMPLATE/bug.yml`` tells users to paste into a
public issue, while promising it contains "no personal data or account
details". ``export_bundle`` does re-scrub crash.log, so the export was
protected and the file itself was not, which is the file the template asks for.

THE FIX formats the traceback, runs it through ``diagnostics.scrub`` (the same
scrubber the logger's filter uses), and writes the scrubbed text.
"""

from __future__ import annotations

import faulthandler
import sys
import threading

import pytest

from waves.waves_ui import app as waves_app


@pytest.fixture
def crash_log(tmp_path, monkeypatch):
    """Install the real crash diagnostics against a temp crash.log, and put
    every global it touches back afterwards."""
    path = tmp_path / "crash.log"
    monkeypatch.setattr(waves_app, "_crash_log_path", lambda: path)

    prev_excepthook = sys.excepthook
    prev_thread_hook = threading.excepthook
    prev_file = waves_app._crash_log_file
    was_enabled = faulthandler.is_enabled()

    waves_app._install_crash_diagnostics()
    try:
        yield path
    finally:
        # faulthandler must stop pointing at the handle we are about to close.
        faulthandler.disable()
        handle = waves_app._crash_log_file
        if handle is not None:
            handle.close()
        waves_app._crash_log_file = prev_file
        sys.excepthook = prev_excepthook
        threading.excepthook = prev_thread_hook
        if was_enabled:
            faulthandler.enable()


def _raise_with(message: str):
    """Produce a real exc_info whose message carries the given text."""
    try:
        raise RuntimeError(message)  # noqa: TRY301
    except RuntimeError:
        return sys.exc_info()


def test_crash_log_write_is_scrubbed_like_the_logger(crash_log):
    """A home path in the exception message must not reach crash.log."""
    home = "/Users/testuser/Music/Waves/Aphex Twin/Selected Ambient Works/04 Heliosphan.flac"
    sys.excepthook(*_raise_with(f"could not write {home}"))

    written = crash_log.read_text(encoding="utf-8")

    assert "Uncaught exception" in written  # the record did land
    assert "RuntimeError" in written  # ...with its traceback
    assert "testuser" not in written, "the username reached crash.log unscrubbed"
    assert home not in written


def test_a_registered_secret_never_reaches_crash_log(crash_log):
    """Runtime secrets registered with the redactor are scrubbed here too."""
    # Register through the very module object app.py holds: another test file
    # re-imports the diagnostics module under a fresh name, which would
    # otherwise give us a different _redactor than the one _record scrubs with.
    diagnostics = waves_app.diagnostics

    secret = "sk-live-9f3ac21be77d4410"  # noqa: S105
    diagnostics.register_secret(secret, "‹secret›")

    sys.excepthook(*_raise_with(f"auth rejected value={secret}"))

    written = crash_log.read_text(encoding="utf-8")
    assert secret not in written
    assert "‹secret›" in written


def test_worker_thread_exceptions_are_scrubbed_too(crash_log):
    """The threading.excepthook path shares _record, so it shares the fix."""
    home = "/Users/testuser/Downloads/some album/01 track.flac"

    exc_type, exc, tb = _raise_with(f"failed on {home}")
    threading.excepthook(threading.ExceptHookArgs([exc_type, exc, tb, threading.current_thread()]))

    written = crash_log.read_text(encoding="utf-8")
    assert "testuser" not in written
