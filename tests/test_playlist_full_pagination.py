"""Regression for issue #12: playlists were capped at 200 tracks.

The playlist browse path paged obj.items() with a hard-coded two-page loop
(offsets 0 and 100), so any playlist longer than 200 tracks was silently
truncated in the track list even though the header count was right. The
paging now lives in backend._all_playlist_items and loops until the endpoint
returns a short page.
"""

from __future__ import annotations

from tidalapi.media import Track

from tidaler.waves_ui.backend import _all_playlist_items


def _make_track(i: int) -> Track:
    t = Track.__new__(Track)
    t.id = i
    return t


class _FakePlaylist:
    """Serves n_tracks tracks in 100-item pages, like the TIDAL endpoint."""

    def __init__(self, n_tracks: int):
        self._tracks = [_make_track(i) for i in range(n_tracks)]
        self.calls: list[int] = []

    def items(self, limit: int = 100, offset: int = 0):
        self.calls.append(offset)
        return self._tracks[offset : offset + limit]


def test_long_playlist_returns_every_track():
    pl = _FakePlaylist(614)
    items, complete = _all_playlist_items(pl)
    assert complete
    assert len(items) == 614
    assert [t.id for t in items] == list(range(614))
    assert pl.calls == [0, 100, 200, 300, 400, 500, 600]


def test_exact_page_multiple_stops_after_empty_page():
    pl = _FakePlaylist(200)
    items, complete = _all_playlist_items(pl)
    assert complete
    assert len(items) == 200
    # One extra call to learn the list ended, then stop.
    assert pl.calls == [0, 100, 200]


def test_short_playlist_single_page():
    pl = _FakePlaylist(42)
    items, complete = _all_playlist_items(pl)
    assert complete
    assert len(items) == 42
    assert pl.calls == [0]


def test_non_media_entries_are_filtered_but_do_not_stop_paging():
    pl = _FakePlaylist(250)
    pl._tracks[5] = object()  # an entry tidalapi could not type
    items, complete = _all_playlist_items(pl)
    assert complete
    assert len(items) == 249


def test_ceiling_hit_reports_incomplete():
    # A "playlist" that always serves full pages: the loop must stop at the
    # ceiling AND say so, never report a truncated set as the whole thing.
    class _Endless(_FakePlaylist):
        def items(self, limit: int = 100, offset: int = 0):
            self.calls.append(offset)
            return [_make_track(offset + i) for i in range(limit)]

    pl = _Endless(0)
    items, complete = _all_playlist_items(pl)
    assert not complete
    assert len(items) == 10000
