"""The bridge's probe by name behind a badge miss on an untrusted listing
(bridge_library.py, the "Probe by name" section).

THE BUG THIS FENCES OFF
-----------------------
A share whose directory paging repeats lists the same first page of artist
folders over and over, so the scan never saw the artists past it and every
badge and download gate answered "not in library" for albums the user owned.
Duplicates were downloaded on that answer. The scan now flags such a listing
(test_library_listing_truncation.py), and here the bridge turns a MISS for an
artist the index has never seen into a direct lookup by folder name, then
republishes a NEW index object so every pill re-asks and every memo resets.

Pinned: a miss probes once per artist and republishes on a hit; the download
gates probe synchronously before deciding (one probe for a whole discography);
a miss is remembered for a while, then asked again; a cache held by a running
scan is not an answer and the ask re-arms; a healthy library never probes;
the Settings flag mirrors the scan and clears on invalidation; and a relaunch
seeded from the cache already knows to probe.
"""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace

from test_library_bridge import _album, _make, _Stub
from test_library_listing_truncation import _fake_listing

from waves.waves_ui import bridge_library
from waves.waves_ui.backend import WavesBridge

for _m in (
    "_library_probe_candidates",
    "_library_probe_wanted",
    "_library_probe_run",
    "_library_probe_enqueue",
    "_library_probe_drain",
    "_library_probe_rearm",
    "_library_probe_backfill",
    "_library_probe_async",
    "_library_probe_many",
    "_library_probe_page",
    "_library_probe_sync",
    "_library_probe_sync_many",
    "_library_probe_track_rows",
    "_library_gate_ready",
    "_library_gate_defer",
    "libraryScanPartial",
    "libraryListingShape",
    "libraryListingReconciled",
):
    setattr(_Stub, _m, getattr(WavesBridge, _m))


def _library(tmp_path, *, hide=("C",)):
    """A library of A (visible) and C (hidden by the root's listing, which
    repeats A twice and never names C), scanned once."""
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    a = _album(lib, "A/[2020] Alpha", ["1.flac", "2.flac"])
    c = _album(lib, "C/[2022] Gamma", ["1.flac", "2.flac", "3.flac"])
    tags = {
        a: {"album": "Alpha", "artist": "A", "date": "2020", "title": "Song"},
        c: {"album": "Gamma", "artist": "C", "date": "2022", "title": "Song"},
    }
    return lib, tags


def _bridge(tmp_path, monkeypatch, *, truncated=True):
    lib, tags = _library(tmp_path)
    if truncated:
        _fake_listing(monkeypatch, {lib: lambda e: [x for x in e if x.name != "C"] * 2})
    s = _make(tmp_path, library_folder=lib, tagmap=tags)
    s._library_scan_partial = False
    s._library_probe_memo = {}
    s._library_probe_inflight = set()
    s._library_probe_pending = {}
    s._library_probe_deferred = {}
    s._library_probe_draining = False
    s._library_probe_gate = threading.Lock()
    s._library_backfill_done = False
    s.emitted = []
    s._emit_from_worker = lambda name: s.emitted.append(name)
    s._rebuild_library_index()
    assert s._library_index is not None
    return s


def _count_probes(s):
    calls = []
    real = s._library.probe_folders

    def counting(*a, **k):
        calls.append(a[1])
        return real(*a, **k)

    s._library.probe_folders = counting
    return calls


def _gamma(s):
    return s.libraryAlbumPresence("C", "Gamma", "2022", 3)


def test_a_miss_on_an_untrusted_listing_probes_and_republishes(tmp_path, monkeypatch):
    s = _bridge(tmp_path, monkeypatch)
    assert s.libraryScanPartial() is True
    assert s.libraryAlbumPresence("A", "Alpha", "2020", 2)["present"]
    old = s._library_index
    s.emitted.clear()
    # The first ask answers from the index it has (a miss) and probes behind it.
    assert not _gamma(s)["present"]
    # The inline pool ran the probe: a NEW index object stands, and the signal
    # that makes every pill re-ask has fired.
    assert s._library_index is not old
    assert "libraryPresenceChanged" in s.emitted
    assert _gamma(s)["present"]
    assert s.artistLibraryPresence("C")["present"]


def test_one_probe_per_artist_however_many_pills_ask(tmp_path, monkeypatch):
    s = _bridge(tmp_path, monkeypatch)
    calls = _count_probes(s)
    for _ in range(3):
        s.libraryAlbumPresence("Nobody", "Nothing", "1999", 1)
        s.libraryTrackPresence("Nobody", "Silence", "Nothing", "1999")
        s.artistLibraryPresence("Nobody")
    assert calls == [["Nobody"]]


def test_a_miss_is_remembered_then_asked_again(tmp_path, monkeypatch):
    s = _bridge(tmp_path, monkeypatch)
    calls = _count_probes(s)
    s.artistLibraryPresence("Nobody")
    s.artistLibraryPresence("Nobody")
    assert len(calls) == 1
    # The rest window passes: the folder may have appeared, ask the disk again.
    key = next(iter(s._library_probe_memo))
    s._library_probe_memo[key] = 0.0
    s.artistLibraryPresence("Nobody")
    assert len(calls) == 2


def test_the_album_gate_probes_synchronously_and_once_for_a_discography(tmp_path, monkeypatch):
    s = _bridge(tmp_path, monkeypatch)
    calls = _count_probes(s)
    albums = [
        SimpleNamespace(name="Gamma", artist=SimpleNamespace(name="C"), year="2022", num_tracks=3, duration=0),
        SimpleNamespace(name="Delta", artist=SimpleNamespace(name="C"), year="2023", num_tracks=9, duration=0),
        SimpleNamespace(name="Epsilon", artist=SimpleNamespace(name="C"), year="2024", num_tracks=4, duration=0),
    ]
    verdicts = [s._library_claims_album(a) for a in albums]
    # The owned album is claimed AFTER the probe, in the same call; the two
    # the user does not have still download. One probe served all three.
    assert verdicts == [True, False, False]
    assert calls == [["C"]]


def test_the_track_gate_probes_too(tmp_path, monkeypatch):
    s = _bridge(tmp_path, monkeypatch)
    calls = _count_probes(s)
    assert s._library_index is not None
    before = s._library_index
    s._library_track_claim("C", "Song", "Gamma", "2022")
    assert calls == [["C"]]
    assert s._library_index is not before  # the hit republished


def test_a_scan_holding_the_cache_is_not_an_answer(tmp_path, monkeypatch):
    s = _bridge(tmp_path, monkeypatch)
    calls = _count_probes(s)
    old = s._library_index
    s._library._scan_busy.acquire()
    try:
        assert not _gamma(s)["present"]
    finally:
        s._library._scan_busy.release()
    assert calls == [["C"]]
    assert s._library_index is old  # nothing found, nothing published
    assert s._library_probe_memo == {}  # and nothing remembered: not a miss
    # The scan's own publish makes the pill re-ask; the probe now lands.
    assert not _gamma(s)["present"]
    assert _gamma(s)["present"]
    assert len(calls) == 2


def test_a_trusted_listing_never_probes(tmp_path, monkeypatch):
    s = _bridge(tmp_path, monkeypatch, truncated=False)
    assert s.libraryScanPartial() is False
    calls = _count_probes(s)
    s.libraryAlbumPresence("Nobody", "Nothing", "1999", 1)
    s.artistLibraryPresence("Nobody")
    assert (
        s._library_claims_album(
            SimpleNamespace(
                name="Nothing", artist=SimpleNamespace(name="Nobody"), year="1999", num_tracks=1, duration=0
            )
        )
        is False
    )
    assert calls == []


def test_various_artists_is_never_a_folder_to_probe(tmp_path, monkeypatch):
    s = _bridge(tmp_path, monkeypatch)
    calls = _count_probes(s)
    s.artistLibraryPresence("Various Artists")
    assert calls == []


def test_the_flag_clears_on_invalidation(tmp_path, monkeypatch):
    s = _bridge(tmp_path, monkeypatch)
    s._library_probe_memo["stale"] = 1.0
    s._invalidate_library_index()
    assert s.libraryScanPartial() is False
    assert s._library_probe_memo == {}


def test_a_relaunch_seeded_from_the_cache_already_probes(tmp_path, monkeypatch):
    _bridge(tmp_path, monkeypatch)  # the first launch's scan leaves the cache flagged
    lib, tags = _library(tmp_path)
    # A second bridge over the same cache file, badges seeded WITHOUT a scan.
    s2 = _make(tmp_path, library_folder=lib, tagmap=tags)
    s2._library_scan_partial = False
    s2._library_probe_memo = {}
    s2._library_probe_inflight = set()
    s2._emit_from_worker = lambda name: None
    s2._seed_library_badges()
    assert s2._library_index is not None
    assert s2.libraryScanPartial() is True
    assert not s2.libraryAlbumPresence("C", "Gamma", "2022", 3)["present"]
    assert s2.libraryAlbumPresence("C", "Gamma", "2022", 3)["present"]


def test_candidates_come_from_the_naming_settings(tmp_path, monkeypatch):
    s = _bridge(tmp_path, monkeypatch)
    s.settings.data.filename_illegal_replacement = "_"
    s.settings.data.filename_illegal_map = {"/": "-"}
    assert s._library_probe_candidates("AC/DC") == ["AC-DC", "AC_DC", "ACDC"]
    monkeypatch.setattr(bridge_library, "folder_name_candidates", lambda *a: ["X"])
    assert s._library_probe_candidates("anything") == ["X"]


def _wide_library(tmp_path, monkeypatch, hidden=("C", "D", "E")):
    """A (visible) plus several artists the root's listing never names, which is
    the shape of a real search page: many misses arriving together."""
    lib = str(tmp_path / "wide")
    os.makedirs(lib, exist_ok=True)
    tags = {}
    for i, artist in (("2020", "A"),) and enumerate(("A", *hidden)):
        year = str(2020 + i)
        folder = _album(lib, f"{artist}/[{year}] Rec{artist}", ["1.flac", "2.flac"])
        tags[folder] = {"album": f"Rec{artist}", "artist": artist, "date": year, "title": "Song"}
    hide = set(hidden)
    _fake_listing(monkeypatch, {lib: lambda e: [x for x in e if x.name not in hide] * 2})
    s = _make(tmp_path, library_folder=lib, tagmap=tags)
    s.emitted = []
    s._emit_from_worker = lambda name: s.emitted.append(name)
    s._rebuild_library_index()
    assert s._library_index is not None
    assert s.libraryScanPartial() is True
    return s


def test_a_page_of_misses_is_one_probe_and_every_artist_resolves(tmp_path, monkeypatch):
    """THE SEARCH PAGE BUG. Every badge on a page misses at once. Asking one
    name per worker meant all but one bounced off the cache lock, answered
    nothing and were never retried, so a search showed no library marks while
    the artist page (one name, one uncontended probe) showed them all."""
    s = _wide_library(tmp_path, monkeypatch)
    calls = _count_probes(s)
    for artist in ("C", "D", "E"):
        assert not s.artistLibraryPresence(artist)["present"]
    # Every name reached the disk: none was dropped for losing a race.
    assert sorted(name for call in calls for name in call) == ["C", "D", "E"]
    # ...and every one of them now answers, which is what a search page shows.
    for artist in ("C", "D", "E"):
        assert s.artistLibraryPresence(artist)["present"], artist


def test_the_page_itself_asks_before_any_badge_misses(tmp_path, monkeypatch):
    """A page of results hands over every artist it names, so the marks are
    right on the first paint instead of after the user clicks into one."""
    s = _wide_library(tmp_path, monkeypatch)
    calls = _count_probes(s)
    s._library_probe_page(
        {
            "artists": [{"name": "C"}],
            "albums": [{"artist": "D", "title": "RecD"}],
            "tracks": [{"artist": "E", "title": "Song"}],
            "playlists": [{"name": "Not An Artist"}],
            "mixes": [{"name": "Also Not"}],
            "top": {"artist": "C", "name": "C"},
        }
    )
    assert len(calls) == 1
    # Playlists and mixes name neither an artist nor a folder, so they are not asked.
    assert sorted(calls[0]) == ["C", "D", "E"]
    for artist in ("C", "D", "E"):
        assert s.artistLibraryPresence(artist)["present"], artist


def test_a_browse_page_asks_for_the_artists_on_its_shelves(tmp_path, monkeypatch):
    """A browse page keeps its rows in shelves rather than per-kind lists, and
    a shelf mixes kinds, so the artist sits under a different field per tile."""
    s = _wide_library(tmp_path, monkeypatch)
    calls = _count_probes(s)
    s._library_probe_page(
        {
            "sections": [
                {"rowKind": "cards", "items": [{"kind": "album", "artist": "C"}, {"kind": "artist", "name": "D"}]},
                {
                    "rowKind": "cards",
                    "items": [{"kind": "playlist", "name": "Not An Artist"}, {"kind": "track", "artist": "E"}],
                },
            ]
        }
    )
    assert len(calls) == 1
    assert sorted(calls[0]) == ["C", "D", "E"]
    for artist in ("C", "D", "E"):
        assert s.artistLibraryPresence(artist)["present"], artist


def test_an_artist_pages_eps_are_asked_for_too(tmp_path, monkeypatch):
    s = _wide_library(tmp_path, monkeypatch)
    calls = _count_probes(s)
    s._library_probe_page({"eps": [{"artist": "D", "title": "RecD"}]})
    assert calls == [["D"]]


def test_a_page_of_only_playlists_asks_nothing(tmp_path, monkeypatch):
    s = _wide_library(tmp_path, monkeypatch)
    calls = _count_probes(s)
    s._library_probe_page({"playlists": [{"name": "Chill"}], "mixes": [{"name": "Daily"}]})
    assert calls == []


def test_a_scan_holding_the_cache_defers_instead_of_forgetting(tmp_path, monkeypatch):
    """A probe that never happened must not be remembered as a miss, and must
    not need a second badge to ask again: the next publish re-asks it."""
    s = _wide_library(tmp_path, monkeypatch)
    calls = _count_probes(s)
    with s._library._scan_busy:
        assert not s.artistLibraryPresence("C")["present"]
        assert calls == [["C"]]  # asked, and told the cache was busy
    assert s._library_probe_memo == {}  # nothing was learned, so nothing rests
    assert s._library_probe_deferred  # ...it is held for the next publish
    s._library_probe_rearm()
    assert s.artistLibraryPresence("C")["present"]


def test_the_download_record_seeds_the_queue_once(tmp_path, monkeypatch):
    """The exact folder names Waves itself wrote are the best seed there is:
    they are real directory names, not guesses."""
    s = _wide_library(tmp_path, monkeypatch)
    root = s._library_root()
    s._ownership = SimpleNamespace(folder_names_under=lambda base, limit=5000: ["D"] if base == root else [])
    calls = _count_probes(s)
    s._library_probe_backfill()
    assert calls == [["D"]]
    assert s.artistLibraryPresence("D")["present"]
    # Once per session only: a publish must not re-seed it every time.
    s._library_probe_backfill()
    assert len(calls) == 1


def test_the_backfill_also_uses_the_download_folder_when_it_is_elsewhere(tmp_path, monkeypatch):
    """An artist folder is named the same wherever Waves writes it, so names it
    used while downloading are worth looking for inside a separate library."""
    s = _wide_library(tmp_path, monkeypatch)
    root = s._library_root()
    s.settings.data.download_base_path = str(tmp_path / "staging")
    asked = []

    def names(base, limit=5000):
        asked.append(base)
        return [] if base == root else ["E"]

    s._ownership = SimpleNamespace(folder_names_under=names)
    calls = _count_probes(s)
    s._library_probe_backfill()
    assert asked == [root, str(tmp_path / "staging")]
    assert calls == [["E"]]
    assert s.artistLibraryPresence("E")["present"]


def test_a_healthy_library_never_seeds_the_backfill(tmp_path, monkeypatch):
    s = _bridge(tmp_path, monkeypatch, truncated=False)
    asked = []
    s._ownership = SimpleNamespace(folder_names_under=lambda base, limit=5000: asked.append(base) or ["D"])
    s._library_probe_backfill()
    assert asked == []


def test_asks_arriving_while_a_drain_runs_coalesce_into_one_question(tmp_path, monkeypatch):
    """The queue is what makes a page one question: whatever lands while the
    drain worker is busy is carried by the same worker's next batch, instead of
    starting a worker each that would only fight over the cache lock."""
    s = _wide_library(tmp_path, monkeypatch)
    calls = _count_probes(s)
    held = []
    s.threadpool = SimpleNamespace(start=lambda worker, *a: held.append(worker))
    for artist in ("C", "D", "E"):
        s.artistLibraryPresence(artist)
    assert len(held) == 1  # one drain worker, not one per badge
    assert calls == []  # nothing asked until it runs
    held[0].run() if hasattr(held[0], "run") else held[0]()
    assert len(calls) == 1
    assert sorted(calls[0]) == ["C", "D", "E"]


def test_the_listing_shape_reaches_the_settings_note(tmp_path, monkeypatch):
    """Settings prints these two numbers, so the slot has to carry them off the
    cache and clear when the folder is left behind."""
    s = _bridge(tmp_path, monkeypatch)
    assert s.libraryScanPartial() is True
    shape = s.libraryListingShape()
    # The fake root lists its one visible artist twice: 2 entries, 1 distinct.
    assert shape == {"entries": 2, "distinct": 1}
    s._invalidate_library_index()
    assert s.libraryListingShape() == {"entries": 0, "distinct": 0}


def test_the_recovery_verdict_reaches_the_settings_note(tmp_path, monkeypatch):
    """Settings picks its words off this slot: a truncated listing whose gaps
    are all filled must not read as a warning about missing badges."""
    s = _bridge(tmp_path, monkeypatch)
    assert s.libraryScanPartial() is True
    assert s.libraryListingReconciled() is False  # nothing has recovered it
    s._library.note_listing_reconciled(True)
    s._rebuild_library_index()
    assert s.libraryListingReconciled() is True
    s._invalidate_library_index()
    assert s.libraryListingReconciled() is False


def test_a_healthy_library_reports_no_shape(tmp_path, monkeypatch):
    s = _bridge(tmp_path, monkeypatch, truncated=False)
    assert s.libraryScanPartial() is False
    assert s.libraryListingShape() == {"entries": 0, "distinct": 0}


# ---- a bulk gate asks once for the whole set ----


def test_the_bulk_gate_asks_about_every_artist_in_one_probe(tmp_path, monkeypatch):
    """A discography is one artist, so the per-album probe already pays once
    for the whole run. A playlist is not: sixty albums by sixty artists meant
    sixty probes, each taking the cache lock on its own and each waiting out a
    running scan on its own. Asked together they cost one lock and one wait."""
    s = _wide_library(tmp_path, monkeypatch)
    calls = _count_probes(s)
    s._library_probe_sync_many(["C", "D", "E"])
    assert calls == [["C", "D", "E"]]
    for artist in ("C", "D", "E"):
        assert s.artistLibraryPresence(artist)["present"], artist


def test_a_busy_cache_stops_the_gate_asking_again_but_not_a_badge(tmp_path, monkeypatch):
    """A deferral says the cache was busy, not that the artist is absent.

    Those two callers want opposite things from that. The bulk gate is about to
    ask about fifty more albums and must not spend the gate wait on each of
    them for the same non-answer, so it stands down. A badge may always ask
    again the moment it wants to, which is why a deferral deliberately leaves
    the memo alone; the cooldown lives beside the memo, not in it.
    """
    s = _wide_library(tmp_path, monkeypatch)
    calls = _count_probes(s)
    # The real gate wait is 15s of standing still, which is the cost this
    # cooldown exists to stop paying. The test only needs ONE deferral to
    # happen, so it buys that at a hundredth of the price.
    monkeypatch.setattr(bridge_library, "_LIBRARY_PROBE_GATE_WAIT_S", 0.05)
    with s._library._scan_busy:
        s._library_probe_sync_many(["C", "D"])
        assert len(calls) == 1  # asked once, told the cache is busy
        s._library_probe_sync_many(["C", "D"])
        assert len(calls) == 1, "the gate paid the wait a second time"
        # The memo is untouched, so the badge path is not blocked at all.
        assert s._library_probe_memo == {}
        assert not s.artistLibraryPresence("C")["present"]
        assert len(calls) == 2, "a badge was silenced by the gate's cooldown"
    # A publish means the cache is free again, so the gate is willing at once
    # rather than serving out the rest of the cooldown.
    s._library_probe_rearm()
    assert s.artistLibraryPresence("C")["present"]
    assert s._library_gate_cooldown == {}
