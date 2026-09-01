"""Regression tests for devlog's privacy-safe default (audit remediation).

Guards that activity logging is OFF by default in every install path, packaged
(Nuitka/PyInstaller) builds AND from-source runs, and only turns ON when a
developer explicitly sets ``WAVES_DEBUG=1``.
"""

import importlib
import os
import sys
from unittest import mock

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

    sys.modules.pop("waves.waves_ui.devlog", None)
    module = importlib.import_module("waves.waves_ui.devlog")
    module = importlib.reload(module)

    if compiled:
        # Nuitka sets __compiled__ in the module's own globals at build time,
        # which is BEFORE the module-level default is computed and therefore
        # before any test can reach it. This marks the module after the fact and
        # asserts nothing changed, which is all a test can honestly do here.
        # What it must NOT do is recompute ENABLED from the test's own copy of
        # the formula, as it used to: that asserted the test's arithmetic
        # against the fixture's environment and could not have failed whatever
        # devlog did, so a compiled-build term added to the default would have
        # kept every one of these green while shipping activity logging on by
        # default in every real build. test_the_default_stays_off_in_every_
        # packaged_build below is the guard that actually holds that line, by
        # running devlog's own source under the marker and asserting the VALUE.
        monkeypatch.setattr(module, "__compiled__", True, raising=False)

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


def _enabled_in(markers: dict, waves_debug=None):
    """What ENABLED comes out as in a build carrying ``markers``.

    Nuitka and PyInstaller set their markers BEFORE the module-level default is
    computed, which is before any test can reach the module. Executing the
    module's own source in a namespace that already carries them is therefore
    the faithful simulation, and the only form that sees the whole default:
    a guard reading the first column-0 ``ENABLED =`` line stays green on

        ENABLED = os.environ.get("WAVES_DEBUG", "0") != "0"
        if "__compiled__" in globals():
            ENABLED = True

    which is a packaged build shipping activity logging on, with CI green.
    """
    import inspect

    module = importlib.import_module("waves.waves_ui.devlog")
    source = inspect.getsource(module)
    env = dict(os.environ)
    env.pop("WAVES_DEBUG", None)
    if waves_debug is not None:
        env["WAVES_DEBUG"] = waves_debug
    namespace = {"__name__": "waves.waves_ui.devlog_probe", "__file__": module.__file__, **markers}
    with mock.patch.dict(os.environ, env, clear=True):
        exec(compile(source, module.__file__, "exec"), namespace)  # noqa: S102
    return namespace["ENABLED"]


def test_the_default_stays_off_in_every_packaged_build():
    """The VALUE, not the line.

    The default may read WAVES_DEBUG and nothing else, which is what keeps a
    packaged build from ever starting with activity logging on. Asserted by
    running the module's own source under each build marker instead of by
    matching the text of one line.
    """
    guidance = (
        "the verbose default now reads something other than WAVES_DEBUG. If that is "
        "deliberate, prove the packaged build still starts quiet before changing it."
    )
    assert _enabled_in({}) is False, f"a plain from-source run starts verbose: {guidance}"
    assert _enabled_in({"__compiled__": True}) is False, f"a Nuitka build starts verbose: {guidance}"
    assert _enabled_in({"__file__": "x", "frozen": True}) is False, f"a frozen build starts verbose: {guidance}"
    assert (
        _enabled_in({"__compiled__": True}, waves_debug="0") is False
    ), f"a compiled build ignores WAVES_DEBUG=0: {guidance}"


def test_a_developer_can_still_turn_it_on():
    """The guard above must not have made the switch inert."""
    assert _enabled_in({}, waves_debug="1") is True
    assert _enabled_in({"__compiled__": True}, waves_debug="1") is True


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
    for name in ("waves.waves_ui.devlog", "waves.waves_ui.diagnostics"):
        sys.modules.pop(name, None)
    # diagnostics.install() reconfigures the process-wide "waves" logger
    # (handlers, level, propagate=False). Snapshot both shared loggers and put
    # everything back afterwards: without the restore, every later test that
    # relies on caplog seeing "waves" records via root propagation captures
    # nothing (pytest < 9.1 caplog depends on propagation).
    shared = (logging.getLogger("waves"), logging.getLogger())
    saved = {lg: (list(lg.handlers), lg.propagate, lg.level) for lg in shared}
    # Detach handlers a previous install left on the shared loggers.
    for lg in shared:
        for h in list(lg.handlers):
            lg.removeHandler(h)
    try:
        devlog = importlib.import_module("waves.waves_ui.devlog")
        diagnostics = importlib.import_module("waves.waves_ui.diagnostics")
        log_path = diagnostics.install(str(tmp_path))
        assert log_path is not None

        devlog.event("search", "needle=zebra stripes")
        for h in logging.getLogger("waves").handlers:
            h.flush()
        on_disk = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        assert "zebra stripes" not in on_disk  # never persisted while verbose is off
        assert any("zebra stripes" in line for line in diagnostics._crumbs.ring)  # but breadcrumbed
    finally:
        for lg, (handlers, propagate, level) in saved.items():
            for h in list(lg.handlers):
                lg.removeHandler(h)
                h.close()
            for h in handlers:
                lg.addHandler(h)
            lg.propagate = propagate
            lg.setLevel(level)
        sys.modules.pop("waves.waves_ui.diagnostics", None)


@pytest.fixture(autouse=True)
def _restore_module():
    """Leave the real module in a clean, reimported state for other tests."""
    yield
    sys.modules.pop("waves.waves_ui.devlog", None)
