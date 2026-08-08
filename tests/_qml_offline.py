"""Keep offscreen Main.qml scenarios off the live TIDAL API.

Every scenario that builds ``WavesBridge(tidal=None)`` gets a REAL
cached-token login fired from the bridge's ``__init__``: a network round
trip whose completion flips ``sessionResolved`` and shows ``loginPanel``,
a full-window scrim with a fill MouseArea. When that lands mid-scenario
it silently swallows every later synthetic click or hover, so whether a
test passed depended on live TIDAL latency (the source of a family of
full-suite-only failures). A unit test must not talk to the live API at
all, so:

- call :func:`patch_offline` BEFORE constructing the bridge (the login
  fires from ``__init__``; a later patch loses the race), and
- run ``PARK_LOGIN_QML`` through the scenario's ``q()`` helper right
  after the boot handover, unless the scenario is actually about login.
"""

from __future__ import annotations


def patch_offline() -> None:
    """Make the bridge's cached-token login resolve instantly, offline."""
    from tidaler.waves_ui.session import WavesTidal

    WavesTidal.login_token = lambda self: False  # type: ignore[method-assign]


# The session resolves logged-out (instantly, via patch_offline), so the
# login overlay is up and would swallow every synthetic click and hover.
# Scenarios that test other surfaces park it.
PARK_LOGIN_QML = "loginPanel.visible = false"
