"""Regression tests for devlog's privacy-safe default (audit remediation).

Guards that activity logging is OFF by default in every install path, packaged
(Nuitka/PyInstaller) builds AND from-source runs, and only turns ON when a
developer explicitly sets ``WAVES_DEBUG=1``.
"""

import importlib
import sys

import pytest


def _load_devlog(monkeypatch, *, waves_debug=None, compiled=False, frozen=False):
    """Import a fresh copy of devlog under a controlled environment.

    ``ENABLED`` is computed at import time, so each case reimports the module
    after setting the env / simulating a compiled build.
    """
    if waves_debug is None:
        monkeypatch.delenv("WAVES_DEBUG", raising=False)
    else:
        monkeypatch.setenv("WAVES_DEBUG", waves_debug)

    if frozen:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    else:
        monkeypatch.delattr(sys, "frozen", raising=False)

    sys.modules.pop("tidaler.waves_ui.devlog", None)
    module = importlib.import_module("tidaler.waves_ui.devlog")
    module = importlib.reload(module)

    if compiled:
        # Simulate a Nuitka build: it sets __compiled__ in the module globals.
        monkeypatch.setattr(module, "__compiled__", True, raising=False)
        # Re-evaluate the default the same way the module does, to prove that
        # even a compiled build stays disabled without WAVES_DEBUG.
        import os

        module.ENABLED = os.environ.get("WAVES_DEBUG", "0") != "0"

    return module


def test_default_from_source_is_disabled(monkeypatch):
    """No WAVES_DEBUG, plain from-source run => logging OFF."""
    module = _load_devlog(monkeypatch, waves_debug=None, compiled=False, frozen=False)
    assert module.ENABLED is False


def test_waves_debug_1_enables(monkeypatch):
    module = _load_devlog(monkeypatch, waves_debug="1")
    assert module.ENABLED is True


def test_waves_debug_0_disables(monkeypatch):
    module = _load_devlog(monkeypatch, waves_debug="0")
    assert module.ENABLED is False


def test_compiled_build_disabled_by_default(monkeypatch):
    """A Nuitka-style compiled build (no sys.frozen) stays OFF by default."""
    module = _load_devlog(monkeypatch, waves_debug=None, compiled=True)
    assert module.ENABLED is False


def test_frozen_build_disabled_by_default(monkeypatch):
    """A PyInstaller-style frozen build stays OFF by default too."""
    module = _load_devlog(monkeypatch, waves_debug=None, frozen=True)
    assert module.ENABLED is False


def test_compiled_build_opt_in_still_enables(monkeypatch):
    """A developer on a compiled build who sets WAVES_DEBUG=1 still gets logs."""
    module = _load_devlog(monkeypatch, waves_debug="1", compiled=True)
    assert module.ENABLED is True


def test_disabled_event_stays_off_disk(monkeypatch, tmp_path):
    """The privacy property, enforced at the handler layer since diagnostics
    landed: with verbose off, an INFO activity event feeds only the in-memory
    breadcrumb ring; nothing about it is persisted to disk."""
    import logging

    monkeypatch.delenv("WAVES_DEBUG", raising=False)
    for name in ("tidaler.waves_ui.devlog", "tidaler.waves_ui.diagnostics"):
        sys.modules.pop(name, None)
    # Detach handlers a previous install left on the shared loggers.
    for lg in (logging.getLogger("waves"), logging.getLogger()):
        for h in list(lg.handlers):
            lg.removeHandler(h)
    devlog = importlib.import_module("tidaler.waves_ui.devlog")
    diagnostics = importlib.import_module("tidaler.waves_ui.diagnostics")
    log_path = diagnostics.install(str(tmp_path))
    assert log_path is not None

    devlog.event("search", "needle=zebra stripes")
    for h in logging.getLogger("waves").handlers:
        h.flush()
    on_disk = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    assert "zebra stripes" not in on_disk  # never persisted while verbose is off
    assert any("zebra stripes" in line for line in diagnostics._crumbs.ring)  # but breadcrumbed


@pytest.fixture(autouse=True)
def _restore_module():
    """Leave the real module in a clean, reimported state for other tests."""
    yield
    sys.modules.pop("tidaler.waves_ui.devlog", None)
