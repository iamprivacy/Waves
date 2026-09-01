"""The launch library sweep waits for the boot reveal.

THE STUTTER THIS FENCES OFF
---------------------------
The first library scan used to start inside the bridge constructor, so its
directory walk ran on pool threads through the whole launch sequence. Python
threads hold the interpreter between syscalls, the GUI thread must run for
every frame the boot water presents, and the probe showed exactly that
arithmetic on screen: 59-73 ms GUI stalls with the walk busy, the water
visibly stuttering under the wordmark (livetest report, 2026-09-01).

Construction now dispatches only the cheap badge seed (a sqlite read) and
parks the sweep behind a pending flag; the QML reveal (bootRevealed) or a
failsafe timer releases it, whichever comes first, and the release is
one-shot so the loser is a no-op.

Runs the REAL bridge constructor in a SUBPROCESS, like the QML scenarios and
for the same reason: building the bridge installs process-global handlers
that must not leak into the suite. No QML is loaded; bootRevealed is called
directly, which is exactly what the overlay's zoom tail does.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_EXIT_OK = 0
_EXIT_SCANNED_AT_BOOT = 1  # the constructor claimed the sweep itself
_EXIT_NOT_ARMED = 2  # nothing pending and no failsafe: the sweep is simply lost
_EXIT_NOT_RELEASED = 3  # bootRevealed left the sweep parked
_EXIT_RERELEASED = 4  # a second release dispatched a second sweep
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78


def test_the_launch_sweep_waits_for_the_reveal():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-boot-scan-defer-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-10:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    if proc.returncode == _EXIT_SCANNED_AT_BOOT:
        pytest.fail(f"the constructor started the sweep the boot water pays for:\n{tail}")
    if proc.returncode == _EXIT_NOT_ARMED:
        pytest.fail(f"the deferred sweep has no pending flag and no failsafe; it would never run:\n{tail}")
    if proc.returncode == _EXIT_NOT_RELEASED:
        pytest.fail(f"bootRevealed did not release the parked sweep:\n{tail}")
    if proc.returncode == _EXIT_RERELEASED:
        pytest.fail(f"the release is not one-shot; a second reveal dispatched a second sweep:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the boot sweep deferral regressed:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from PySide6.QtGui import QGuiApplication
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import patch_offline

    patch_offline()
    QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    bridge = WavesBridge(tidal=None)

    # The constructor's contract: the sweep is parked, not started. The
    # sandbox has no library folder configured, but that must not matter:
    # the deferral is decided before the root is even read, or a configured
    # machine would be back to scanning under the boot water.
    if bridge._library_index_building:
        print("a scan was already claimed during construction", file=sys.stderr)
        return _EXIT_SCANNED_AT_BOOT
    if not getattr(bridge, "_boot_library_scan_pending", False) or not bridge._boot_library_scan_timer.isActive():
        print("no pending flag or no running failsafe timer after construction", file=sys.stderr)
        return _EXIT_NOT_ARMED

    # The reveal releases it: the pending flag drops and the rebuild is
    # dispatched. Counted through _rebuild_library_index so the assert holds
    # whether or not a root is configured.
    releases: list = []
    orig = bridge._rebuild_library_index
    bridge._rebuild_library_index = lambda **kw: (releases.append(kw), orig(**kw))[1]
    bridge.bootRevealed()
    if len(releases) != 1 or getattr(bridge, "_boot_library_scan_pending", True):
        print(f"after bootRevealed: releases={len(releases)}, pending flag not cleared", file=sys.stderr)
        return _EXIT_NOT_RELEASED
    if bridge._boot_library_scan_timer.isActive():
        print("the failsafe timer is still armed after the release", file=sys.stderr)
        return _EXIT_NOT_RELEASED

    # One-shot: a stray second reveal (or the failsafe losing the race) must
    # not dispatch a second sweep.
    bridge.bootRevealed()
    bridge._start_boot_library_scan()
    if len(releases) != 1:
        print(f"a second release dispatched again: releases={len(releases)}", file=sys.stderr)
        return _EXIT_RERELEASED
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
