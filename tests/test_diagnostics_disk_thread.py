"""The on-disk log is written by its own thread, never by the caller's.

WHAT THIS FENCES OFF
--------------------
The RotatingFileHandler used to sit directly on the loggers, so any WARNING
logged from the GUI thread wrote and flushed to disk inline. With the disk
busy at launch (the library scan, the ownership stats) that write blocked the
event loop 50-130ms (sampled live) and the launch animation stalled on it.
Now the loggers hold a QueueHandler; a writer thread owns the file handler.
Redaction still happens before anything is queued, and an export can wait
(bounded) for the queue to land.
"""

from __future__ import annotations

import importlib
import logging
import sys
import time
from logging.handlers import QueueHandler, RotatingFileHandler


def _fresh(monkeypatch, tmp_path):
    monkeypatch.delenv("WAVES_DEBUG", raising=False)
    for name in ("waves.waves_ui.devlog", "waves.waves_ui.diagnostics"):
        sys.modules.pop(name, None)
    shared = (logging.getLogger("waves"), logging.getLogger())
    saved = {lg: (list(lg.handlers), lg.propagate, lg.level) for lg in shared}
    for lg in shared:
        for h in list(lg.handlers):
            lg.removeHandler(h)
    diagnostics = importlib.import_module("waves.waves_ui.diagnostics")
    log_path = diagnostics.install(str(tmp_path))
    assert log_path is not None
    return diagnostics, log_path, shared, saved


def _restore(diagnostics, shared, saved):
    diagnostics._stop_disk_listener()
    for lg, (handlers, propagate, level) in saved.items():
        for h in list(lg.handlers):
            lg.removeHandler(h)
            h.close()
        for h in handlers:
            lg.addHandler(h)
        lg.propagate = propagate
        lg.setLevel(level)
    sys.modules.pop("waves.waves_ui.diagnostics", None)


def test_loggers_hold_a_queue_not_the_file_and_the_line_still_lands_scrubbed(monkeypatch, tmp_path):
    diagnostics, log_path, shared, saved = _fresh(monkeypatch, tmp_path)
    try:
        for lg in shared:
            kinds = [type(h) for h in lg.handlers]
            assert RotatingFileHandler not in kinds, "the disk writer must not sit on the caller's thread"
            assert QueueHandler in kinds
        logging.getLogger("waves.probe").warning("copy failed for /Users/somebody/Music/x.flac (marker-A)")
        diagnostics.flush_disk_log()
        deadline = time.monotonic() + 3.0
        text = ""
        while time.monotonic() < deadline:
            text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            if "marker-A" in text:
                break
            time.sleep(0.02)
        assert "marker-A" in text
        assert "/Users/somebody" not in text, "redaction runs before the record is queued"
    finally:
        _restore(diagnostics, shared, saved)


def test_an_error_still_carries_its_breadcrumb_trail_to_disk(monkeypatch, tmp_path):
    diagnostics, log_path, shared, saved = _fresh(monkeypatch, tmp_path)
    try:
        logging.getLogger("waves.probe").info("crumb-B happened first")
        logging.getLogger("waves.probe").error("then this broke (marker-C)")
        diagnostics.flush_disk_log()
        deadline = time.monotonic() + 3.0
        text = ""
        while time.monotonic() < deadline:
            text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            if "end trail" in text:
                break
            time.sleep(0.02)
        assert "marker-C" in text
        assert "crumb-B" in text, "the crumb dump rides the same queue as the error"
    finally:
        _restore(diagnostics, shared, saved)


def test_stopping_the_writer_twice_is_harmless(monkeypatch, tmp_path):
    diagnostics, _log_path, shared, saved = _fresh(monkeypatch, tmp_path)
    try:
        diagnostics._stop_disk_listener()
        diagnostics._stop_disk_listener()
        assert diagnostics._disk_listener is None
    finally:
        _restore(diagnostics, shared, saved)
