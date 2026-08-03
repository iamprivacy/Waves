"""The hover peek's stream picker favours instant start.

_pick_peek_stream always chooses the smallest variant (the card is
thumbnail scale, so resolution is invisible and start-up speed is
everything), never consults the persisted quality setting or the
bandwidth probe, and falls back to the master URL untouched on anything
unexpected. These tests drive it with stubbed playlists, no network.
"""

from types import SimpleNamespace

from tidaler.waves_ui.backend import WavesBridge


def _master(heights):
    """A fake m3u8 master playlist with one variant per height."""
    playlists = [
        SimpleNamespace(
            stream_info=SimpleNamespace(resolution=(h * 16 // 9, h)),
            absolute_uri=f"https://cdn.example/{h}.m3u8",
        )
        for h in heights
    ]
    return SimpleNamespace(is_variant=True, playlists=playlists)


class _Stub:
    """Just enough bridge for the picker: a canned _load_playlist."""

    def __init__(self, master):
        self._load_playlist = lambda url: master


def _pick(master):
    return WavesBridge._pick_peek_stream(_Stub(master), "https://cdn.example/master.m3u8")


def test_picks_smallest_variant():
    assert _pick(_master([1080, 720, 480, 360])) == "https://cdn.example/360.m3u8"


def test_picks_smallest_regardless_of_order():
    assert _pick(_master([360, 1080, 240, 720])) == "https://cdn.example/240.m3u8"


def test_single_variant_is_taken():
    assert _pick(_master([1080])) == "https://cdn.example/1080.m3u8"


def test_non_variant_playlist_falls_back_to_master():
    master = SimpleNamespace(is_variant=False, playlists=[])
    assert _pick(master) == "https://cdn.example/master.m3u8"


def test_empty_candidates_fall_back_to_master():
    master = SimpleNamespace(is_variant=True, playlists=[])
    assert _pick(master) == "https://cdn.example/master.m3u8"


def test_load_failure_falls_back_to_master():
    stub = _Stub(None)
    stub._load_playlist = lambda url: (_ for _ in ()).throw(RuntimeError("net down"))
    url = "https://cdn.example/master.m3u8"
    assert WavesBridge._pick_peek_stream(stub, url) == url
