"""Issue #67: a discography leaves out an EP or single a bigger release holds.

The pure rule (_drop_covered_releases) is tested on plain records; the sweep
(downloadArtist) is driven through the shared discography stub, so the switch,
its default and the STOP path are checked where they actually run.
"""

from threading import Lock
from types import SimpleNamespace

import pytest
from test_discography_video_source import _DiscoStub

from waves.waves_ui import backend
from waves.waves_ui.backend import (
    WavesBridge,
    _drop_covered_releases,
    _MergeRec,
    _ScanStopped,
)


def _rel(rid, kind, songs, quality=3, atmos=False, complete=True):
    """A release: ``songs`` are (title, isrc) or (title, isrc, explicit, seconds)."""
    recs = []
    for s in songs:
        title, isrc, explicit, dur = (*s, False, 200)[:4] if len(s) == 2 else s
        recs.append(_MergeRec(None, title, dur, isrc, explicit))
    return SimpleNamespace(
        id=rid,
        type=kind,
        quality=quality,
        recs=recs,
        complete=complete,
        audio_modes=["DOLBY_ATMOS"] if atmos else ["STEREO"],
    )


def _run(albums, extra=()):
    kept, dropped = _drop_covered_releases(
        albums, lambda a: a.recs, lambda a: a.complete, lambda a: a.quality, list(extra)
    )
    return [a.id for a in kept], [a.id for a in dropped]


ALBUM = [("intro", "A1"), ("greedy", "A2"), ("exes", "A3"), ("run for the hills", "A4")]


def test_a_pre_release_single_the_album_holds_is_left_out():
    album = _rel("lp", "ALBUM", ALBUM)
    single = _rel("s1", "SINGLE", [("greedy", "A2")])
    assert _run([album, single]) == (["lp"], ["s1"])


def test_a_standalone_single_still_downloads():
    album = _rel("lp", "ALBUM", ALBUM)
    single = _rel("s2", "SINGLE", [("just keep watching", "B9")])
    assert _run([album, single]) == (["lp", "s2"], [])


def test_a_single_with_its_b_side_missing_from_the_album_stays():
    album = _rel("lp", "ALBUM", ALBUM)
    single = _rel("s3", "SINGLE", [("greedy", "A2"), ("greedy (demo)", "D1")])
    assert _run([album, single]) == (["lp", "s3"], [])


def test_an_ep_split_across_two_albums_stays():
    one = _rel("lp1", "ALBUM", [("a", "X1"), ("b", "X2"), ("c", "X3")])
    two = _rel("lp2", "ALBUM", [("d", "X4"), ("e", "X5"), ("f", "X6")])
    ep = _rel("ep", "EP", [("a", "X1"), ("d", "X4")])
    assert _run([one, two, ep]) == (["lp1", "lp2", "ep"], [])


def test_a_remix_with_its_own_isrc_is_not_the_album_cut():
    album = _rel("lp", "ALBUM", ALBUM)
    remix = _rel("rx", "SINGLE", [("greedy", "R2")])
    assert _run([album, remix]) == (["lp", "rx"], [])


def test_a_clean_album_never_stands_in_for_an_explicit_single():
    album = _rel("lp", "ALBUM", [(t, None, False, 200) for t, _ in ALBUM])
    single = _rel("s1", "SINGLE", [("greedy", None, True, 200)])
    assert _run([album, single]) == (["lp", "s1"], [])


def test_without_isrcs_a_title_and_length_match_still_counts():
    album = _rel("lp", "ALBUM", [(t, None, False, 200) for t, _ in ALBUM])
    single = _rel("s1", "SINGLE", [("greedy", None, False, 201)])
    radio = _rel("s2", "SINGLE", [("greedy", None, False, 170)])
    assert _run([album, single, radio]) == (["lp", "s2"], ["s1"])


def test_a_single_better_than_its_album_stays():
    album = _rel("lp", "ALBUM", ALBUM, quality=2)
    single = _rel("s1", "SINGLE", [("greedy", "A2")], quality=4)
    assert _run([album, single]) == (["lp", "s1"], [])


def test_a_short_read_keeps_the_release():
    album = _rel("lp", "ALBUM", ALBUM)
    single = _rel("s1", "SINGLE", [("greedy", "A2")], complete=False)
    unread = _rel("s2", "SINGLE", [])
    assert _run([album, single, unread]) == (["lp", "s1", "s2"], [])


def test_an_atmos_album_does_not_hold_a_stereo_single():
    album = _rel("lp", "ALBUM", ALBUM, atmos=True)
    single = _rel("s1", "SINGLE", [("greedy", "A2")])
    assert _run([album, single]) == (["lp", "s1"], [])


def test_an_ep_holds_a_single_and_an_album_holds_the_ep():
    album = _rel("lp", "ALBUM", ALBUM)
    ep = _rel("ep", "EP", [("greedy", "A2"), ("exes", "A3")])
    single = _rel("s1", "SINGLE", [("exes", "A3")])
    assert _run([album, ep, single]) == (["lp"], ["ep", "s1"])


def test_a_single_goes_with_its_ep_when_the_album_holds_both():
    album = _rel("lp", "ALBUM", [("a", "X1"), ("b", "X2"), ("c", "X3")])
    ep = _rel("ep", "EP", [("a", "X1"), ("b", "X2")])
    single = _rel("s1", "SINGLE", [("a", "X1")])
    assert _run([album, ep, single]) == (["lp"], ["ep", "s1"])


def test_same_size_releases_never_drop_each_other():
    one = _rel("s1", "SINGLE", [("greedy", "A2")])
    two = _rel("s2", "SINGLE", [("greedy", "A2")])
    ep_a = _rel("e1", "EP", [("a", "X1"), ("b", "X2")])
    ep_b = _rel("e2", "EP", [("a", "X1"), ("b", "X2"), ("c", "X3")])
    assert _run([one, two, ep_a, ep_b]) == (["s1", "s2", "e1", "e2"], [])


def test_an_album_typed_release_is_never_a_candidate():
    big = _rel("lp", "ALBUM", ALBUM)
    small = _rel("mini", "ALBUM", [("greedy", "A2")])
    assert _run([big, small]) == (["lp", "mini"], [])


def test_a_merge_identity_holds_a_single_but_is_never_dropped():
    identity = _rel("dlx", "ALBUM", ALBUM)
    single = _rel("s1", "SINGLE", [("greedy", "A2")])
    assert _run([single], extra=[identity]) == ([], ["s1"])
    ep_identity = _rel("epx", "EP", [("greedy", "A2"), ("exes", "A3")])
    assert _run([], extra=[ep_identity, identity]) == ([], [])


# ---- the sweep ---------------------------------------------------------------


class _Track:
    def __init__(self, tid, name, isrc, dur=200):
        self.id, self.name, self.isrc, self.duration, self.explicit = tid, name, isrc, dur, False


class _Release:
    def __init__(self, rid, kind, songs, store):
        self.id, self.type, self.name = rid, kind, rid
        self._tracks = [_Track(f"{rid}-{i}", t, isrc) for i, (t, isrc) in enumerate(songs)]
        self.num_tracks = len(self._tracks)
        self.audio_modes = ["STEREO"]
        self._store = store

    def tracks(self):
        self._store.reads.append(self.id)
        if self._store.on_read:
            self._store.on_read()
        return list(self._tracks)


class _SweepStub(_DiscoStub):
    _skip_covered_releases = WavesBridge._skip_covered_releases
    _remember_capped = WavesBridge._remember_capped
    _EDITION_TRACKS_CACHE_MAX = 2000

    def __init__(self, skip=True, eps=True):
        super().__init__(SimpleNamespace(id="ar"), video_download=False)
        self.prefs = {"disco_skip_covered": skip, "disco_eps": eps}
        self.reads: list = []
        self.on_read = None
        self._edition_tracks_cache: dict = {}
        self._evict_lock = Lock()
        self.releases = [
            _Release("lp", "ALBUM", ALBUM, self),
            _Release("s1", "SINGLE", [("greedy", "A2")], self),
            _Release("s2", "SINGLE", [("just keep watching", "B9")], self),
        ]

    def _artist_releases(self, artist):
        return list(self.releases), [], True

    def _waves_pref_bool(self, key):
        return bool(self.prefs.get(key, False))


def _queued(stub):
    return stub._albumsQueued.emits[-1][1] if stub._albumsQueued.emits else []


def test_the_switch_is_off_by_default():
    assert WavesBridge._default_waves_prefs(None)["disco_skip_covered"] is False


def test_off_queues_everything_and_reads_no_track_list():
    stub = _SweepStub(skip=False)
    stub.downloadArtist("ar")
    assert _queued(stub) == ["lp", "s1", "s2"]
    assert stub.reads == []


def test_on_leaves_the_covered_single_out():
    stub = _SweepStub()
    stub.downloadArtist("ar")
    assert _queued(stub) == ["lp", "s2"]
    assert sorted(stub.reads) == ["lp", "s1", "s2"]
    assert "Checking singles against albums…" in stub.statuses


def test_with_eps_and_singles_off_nothing_is_checked():
    stub = _SweepStub(eps=False)
    stub.releases = stub.releases[:1]
    stub.downloadArtist("ar")
    assert stub.reads == []


def test_a_second_sweep_reads_nothing_again():
    stub = _SweepStub()
    stub.downloadArtist("ar")
    stub.reads.clear()
    stub.downloadArtist("ar")
    assert stub.reads == []
    assert _queued(stub) == ["lp", "s2"]


def test_a_failed_read_keeps_the_release_and_is_not_cached():
    stub = _SweepStub()

    def boom():
        if stub.reads[-1] == "s1":
            raise RuntimeError("network")

    stub.on_read = boom
    stub.downloadArtist("ar")
    assert _queued(stub) == ["lp", "s1", "s2"]
    assert "s1" not in stub._edition_tracks_cache


def test_stop_during_the_reads_queues_nothing():
    stub = _SweepStub()

    def press_stop():
        stub._scan_gen += 1

    stub.on_read = press_stop
    stub.downloadArtist("ar")
    assert stub._albumsQueued.emits == []


def test_the_stop_check_is_never_called_from_a_worker(monkeypatch):
    stub = _SweepStub()
    import threading

    main = threading.current_thread()
    seen: list = []

    def check():
        seen.append(threading.current_thread() is main)

    albums = list(stub.releases)
    stub._skip_covered_releases(albums, [], stop_check=check)
    assert seen and all(seen)


def test_the_merge_path_passes_its_identities_as_holders():
    stub = _SweepStub()
    identity = stub.releases[0]
    stub.prefs["collapse_editions"] = True
    stub._merge_pref_on = lambda: True
    stub._merge_editions = lambda albums, stop_check=None: (
        [a for a in albums if a is not identity],
        [(identity, ["plan"])],
    )
    stub.downloadArtist("ar")
    assert _queued(stub) == ["s2", "lp"]


def test_scan_stopped_is_what_stop_raises():
    # The sweep's own stop check is the one the reads honor.
    stub = _SweepStub()
    check = backend._stop_check_for(stub)
    stub._scan_gen += 1
    with pytest.raises(_ScanStopped):
        check()
