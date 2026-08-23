"""Signing out ends the downloads first, on the session that started them.

Every job is handed to the download pool at the moment it is queued, and the
signed-in check sits at that moment, never inside the running job. So signing
out used to leave the whole backlog running against the account being signed
out of: it failed one item at a time, for as long as the queue was, which is
exactly what someone switching to a second account is trying to escape
(issue #30).

The stop is the STOP button's own, so nothing is lost: the rows stay, marked
stopped, and RETRY ALL picks them up on whichever account signs in next (the
Stopped section, issue #27). What this pins is the order. The session object is
destroyed a few lines later, so the aborts have to be set before it goes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from tidaler.waves_ui.backend import WavesBridge


def _bridge(tmp_path) -> MagicMock:
    """A stand-in holding only what ``logout`` reaches for.

    MagicMock answers the rest, so the test says nothing about the cache
    clearing around it and keeps working when that list changes.
    """
    bridge = MagicMock()
    bridge._objs = {"album": {}, "track": {}}
    bridge._page_cache_path = str(tmp_path / "pages.json")
    return bridge


def _order(bridge: MagicMock) -> list[str]:
    return [name for name, _args, _kwargs in bridge.mock_calls]


def test_signing_out_stops_the_downloads(tmp_path):
    bridge = _bridge(tmp_path)

    WavesBridge.logout(bridge)

    assert bridge.stopAll.called, "the queue was left running on the account being signed out of"


def test_the_stop_comes_before_the_session_is_torn_down(tmp_path):
    bridge = _bridge(tmp_path)

    WavesBridge.logout(bridge)
    order = _order(bridge)

    assert order.index("stopAll") < order.index("tidal.logout"), "the aborts must be set while the session lives"
    assert order.index("stopAll") < order.index("_set_logged_in")


def test_signing_out_still_says_so(tmp_path):
    # The stop sets its own status; the sign-out's is the one that must stand.
    bridge = _bridge(tmp_path)

    WavesBridge.logout(bridge)

    assert bridge._set_status.call_args.args == ("Signed out",)
