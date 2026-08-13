"""Glue tests for the bridge's local-library scan family (bridge_library.py).

Qt-free: the mixin's methods are bound onto a bare stub with an inline pool, so
the scan, the source resolution, the presence answers and the signal traffic are
tested without a QObject or an event loop. The watcher half (Qt-only) is covered
by test_library_watch_classify.py; the scanner itself by test_library_index.py.
"""

from __future__ import annotations

import contextlib
import os
import threading
from types import SimpleNamespace

from conftest import _InlinePool, _Signal

from tidaler.library_index import LibraryIndex, cache_file_for_root, root_comparison_key
from tidaler.matching import presence_key as _presence_key
from tidaler.waves_ui.backend import WavesBridge

_METHODS = (
    "_rebuild_library_index",
    "_invalidate_library_index",
    "libraryAlbumPresence",
    "libraryTrackPresence",
    "artistLibraryPresence",
    "libraryIndexReady",
    "_library_root",
    "_waves_pref_bool",
    "rescanLibrary",
    "setWavesPref",
    "librarySource",
    "libraryDownloadFolder",
    "_library_bulk_skip_on",
    "_library_claims_album",
    "_library_claims_track",
    # The MusicBrainz overlay rides inside the presence slot; the opt-in pref
    # defaults off in these stubs, so it answers pass-through (its own rules
    # are covered in test_mb_overlay.py).
    "_mb_arbitrated",
    "_mb_arbiter_on",
    # The scan sizes its pools from this classifier's verdict; the real one
    # runs here (tmp_path is a local disk, so these glue tests scan at full
    # speed). Its own rules are covered in test_library_watch_classify.py.
    "_library_root_is_local",
    "_library_root_locality",
    # The scan's TAIL: the watcher realignment and, behind it, the coalescing
    # read that dispatches a trailing rebuild. Missing here, every scan worker
    # in this file died on an AttributeError before reaching either (the Worker
    # wrapper swallows it, so the tests still passed) and the coalescing half
    # of test_two_threads_cannot_claim_the_same_scan was never exercised at all.
    "_resolve_watch_set",
    # The share self-heal offers: both getattr-guard the backend machinery
    # they forward to, so on these stubs they are no-ops unless a test plants
    # a recorder.
    "_library_share_remount",
    "_library_share_alive",
)


class _Stub:
    pass


for _m in _METHODS:
    setattr(_Stub, _m, getattr(WavesBridge, _m))


def _album(base, rel, files):
    d = os.path.join(base, *rel.split("/"))
    os.makedirs(d, exist_ok=True)
    for name in files:
        open(os.path.join(d, name), "w").close()
    return d


def _make(
    tmp_path, *, library_enabled=True, library_source="separate", library_folder="", download_base="", tagmap=None
):
    # library_enabled defaults True HERE (the app's factory default is False)
    # because these are glue tests of an activated scan; the master-switch
    # gate itself is covered by the dedicated tests below.
    tagmap = tagmap or {}
    s = _Stub()
    s.threadpool = _InlinePool()
    s._waves_prefs = {
        "library_enabled": library_enabled,
        "library_source": library_source,
        "library_folder": library_folder,
        "library_bulk_skip": True,
    }
    s.settings = SimpleNamespace(data=SimpleNamespace(download_base_path=download_base), save=lambda: None)

    # The bridge's real _open_library_index resolves one cache file per root
    # (cache_file_for_root) and is re-invoked on a root change; mirror that
    # here with the test's fake tag reader kept across reopens, so the glue
    # tests exercise the per-root file behaviour without mutagen.
    def _reopen():
        return LibraryIndex(
            cache_file_for_root(str(tmp_path), s._library_root()),
            read_tags=lambda p: tagmap.get(os.path.dirname(p)),
        )

    s._open_library_index = _reopen
    s._library = _reopen()
    s._library_index = None
    s._library_track_index = None
    s._library_artist_index = {}
    s._library_artist_index_src = None
    s._library_index_building = False
    s._library_index_pending = False
    s._library_force_full_pending = False
    s._library_index_lock = threading.Lock()
    s._library_scanning = None
    s._library_gen = 0
    s._library_scan_status = "unset"
    s._library_scan_progress = {}
    s._library_scan_read_t0 = 0.0
    s.libraryPresenceChanged = _Signal()
    s.libraryScanStatusChanged = _Signal()
    s.librarySourceChanged = _Signal()
    # The change-of-source path tears down the file watcher and persists prefs;
    # both are Qt/disk side effects out of scope for this glue test, so stub them
    # to no-ops. _logged_in is False so nothing re-initialises Download.
    s._teardown_library_watch = lambda: None
    s._library_poll_in_flight = False
    s._save_waves_prefs = lambda: None
    s._save_settings = lambda: None
    s._logged_in = False
    # The scan tail asks the GUI thread to realign the file watcher via this
    # signal; in production it is connected to _sync_library_watch, here it is a
    # no-op recorder (the watcher is Qt-only and out of scope for this Qt-free glue
    # test, which is covered instead by test_library_watch_classify.py).
    s._librarySyncWatch = _Signal()
    return s


def test_builds_index_from_library_folder_and_answers(tmp_path):
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "Lorna Shore/Pain Remains", ["1.flac", "2.flac", "3.flac"])
    s = _make(
        tmp_path, library_folder=lib, tagmap={d: {"album": "Pain Remains", "artist": "Lorna Shore", "date": "2022"}}
    )
    assert s._library_index is None
    s._rebuild_library_index()  # InlinePool runs the scan+build synchronously
    # Two publishes: the committed-batch progress publish, then the final one.
    assert s._library_index is not None and len(s.libraryPresenceChanged.emits) == 2
    r = s.libraryAlbumPresence("Lorna Shore", "Pain Remains", "2022", 3)
    assert r["present"] and not r["partial"]
    assert r["local_album_id"] == d  # the album folder, for reveal-in-file-manager
    assert s.libraryAlbumPresence("Boards of Canada", "Geogaddi", "2002", 10)["present"] is False


def test_partial_when_tidal_has_more_tracks(tmp_path):
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "A/Alb", ["1.flac"])  # only 1 of the album's tracks on disk
    s = _make(tmp_path, library_folder=lib, tagmap={d: {"album": "Alb", "artist": "A", "date": "2000"}})
    s._rebuild_library_index()
    r = s.libraryAlbumPresence("A", "Alb", "2000", 12)
    assert r["present"] and r["partial"] and r["local_tracks"] == 1


def test_hidden_when_index_not_built(tmp_path):
    s = _make(tmp_path, library_folder=str(tmp_path))
    assert s.libraryAlbumPresence("A", "Alb", "2000", 1) == {"present": False}


def test_download_source_scans_the_download_folder(tmp_path):
    # "download" source: the badge scan follows the download folder, only ever
    # by explicit opt-in.
    dl = str(tmp_path / "dl")
    os.makedirs(dl, exist_ok=True)
    d = _album(dl, "A/Alb", ["1.flac"])
    s = _make(
        tmp_path,
        library_source="download",
        download_base=dl,
        tagmap={d: {"album": "Alb", "artist": "A", "date": "2000"}},
    )
    assert s._library_root() == dl
    assert s.libraryDownloadFolder() == dl
    s._rebuild_library_index()
    assert s.libraryAlbumPresence("A", "Alb", "2000", 1)["present"]


def test_separate_source_with_empty_folder_scans_nothing(tmp_path):
    # The opt-in default: a separate source with no folder set resolves to no root
    # and scans nothing, so the download folder is never indexed on its own even
    # when it is full of music.
    dl = str(tmp_path / "dl")
    os.makedirs(dl, exist_ok=True)
    _album(dl, "A/Alb", ["1.flac"])
    s = _make(tmp_path, library_source="separate", library_folder="", download_base=dl)
    assert s._library_root() == ""
    s._rebuild_library_index()
    assert s._library_index is None
    assert s._library_scan_status == "unset"
    assert s.libraryAlbumPresence("A", "Alb", "2000", 1)["present"] is False


def test_master_switch_off_scans_nothing_even_when_configured(tmp_path):
    # The library_enabled master switch (the app's factory default is OFF)
    # beats both sources: with it off a fully configured library resolves no
    # root, so every trigger that funnels through _rebuild_library_index scans
    # nothing and the download folder is never indexed.
    lib = str(tmp_path / "lib")
    dl = str(tmp_path / "dl")
    for base in (lib, dl):
        _album(base, "A/Alb", ["1.flac"])
    for source in ("separate", "download"):
        s = _make(tmp_path, library_enabled=False, library_source=source, library_folder=lib, download_base=dl)
        assert s._library_root() == ""
        s._rebuild_library_index()
        assert s._library_index is None
        assert s._library_scan_status == "unset"
        assert s.libraryAlbumPresence("A", "Alb", "2000", 1)["present"] is False


def test_turning_the_switch_off_drops_badges(tmp_path):
    # Committing library_enabled=False (setWavesPref, the applySettings path)
    # invalidates the index: badges drop at once, the generation bump discards
    # any in-flight scan, and no root resolves any more.
    lib = str(tmp_path / "lib")
    d = _album(lib, "A/Alb", ["1.flac"])
    s = _make(tmp_path, library_folder=lib, tagmap={d: {"album": "Alb", "artist": "A", "date": "2000"}})
    s._rebuild_library_index()
    assert s.libraryAlbumPresence("A", "Alb", "2000", 1)["present"]
    gen_before = s._library_gen
    s.setWavesPref("library_enabled", False)
    assert s._waves_prefs["library_enabled"] is False
    assert s._library_index is None
    assert s._library_gen > gen_before
    assert s._library_root() == ""
    assert s.libraryAlbumPresence("A", "Alb", "2000", 1)["present"] is False


def test_separate_source_uses_the_chosen_folder(tmp_path):
    lib = str(tmp_path / "lib")
    dl = str(tmp_path / "dl")
    s = _make(tmp_path, library_source="separate", library_folder=lib, download_base=dl)
    assert s._library_root() == lib  # the separate folder, not the download folder


def test_switching_source_drops_badges_and_waits_for_start_scan(tmp_path):
    # Committing a new source (setWavesPref, the applySettings path) persists it
    # and clears the previous source's badges, but does NOT auto-scan here: the
    # download folder is only indexed once applySettings starts the saved
    # configuration's scan.
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "A/Alb", ["1.flac"])
    s = _make(
        tmp_path,
        library_source="separate",
        library_folder=lib,
        tagmap={d: {"album": "Alb", "artist": "A", "date": "2000"}},
    )
    s._rebuild_library_index()
    assert s.libraryAlbumPresence("A", "Alb", "2000", 1)["present"]
    gen_before = s._library_gen
    s.setWavesPref("library_source", "download")
    assert s._waves_prefs["library_source"] == "download"
    assert s.librarySource() == "download"
    assert s._library_index is None  # badges cleared
    assert s._library_scan_status == "unset"
    assert s._library_gen > gen_before  # any in-flight scan of the old source discarded
    assert s.libraryAlbumPresence("A", "Alb", "2000", 1)["present"] is False


def test_changing_separate_folder_waits_for_start_scan(tmp_path):
    # Pointing the separate source at a different folder drops the old badges and
    # does not auto-scan HERE (applySettings starts the saved configuration's
    # scan itself); the next rebuild indexes the new folder and prunes the
    # previous folder's rows.
    a = str(tmp_path / "libA")
    b = str(tmp_path / "libB")
    da = _album(a, "A/One", ["1.flac"])
    db = _album(b, "B/Two", ["1.flac"])
    tagmap = {
        da: {"album": "One", "artist": "A", "date": "2000"},
        db: {"album": "Two", "artist": "B", "date": "2001"},
    }
    s = _make(tmp_path, library_source="separate", library_folder=a, tagmap=tagmap)
    s._rebuild_library_index()
    assert s.libraryAlbumPresence("A", "One", "2000", 1)["present"]
    s.setWavesPref("library_folder", b)
    assert s._library_index is None
    assert s.libraryAlbumPresence("A", "One", "2000", 1)["present"] is False
    s.rescanLibrary()
    assert s.libraryAlbumPresence("B", "Two", "2001", 1)["present"]
    # The old folder's albums never leak into the new root's badges (they now
    # live untouched in the old root's own cache file, see the test below).
    assert s.libraryAlbumPresence("A", "One", "2000", 1)["present"] is False


def test_switching_back_to_a_previous_folder_reopens_its_cache_warm(tmp_path):
    # Each root has its own cache file, so trying another folder and coming
    # back must NOT cost a rescan: the first folder's file is reopened as it
    # was, and the return scan is the cheap unchanged sweep, not a re-read.
    a = str(tmp_path / "libA")
    b = str(tmp_path / "libB")
    da = _album(a, "A/One", ["1.flac"])
    db = _album(b, "B/Two", ["1.flac"])
    tagmap = {
        da: {"album": "One", "artist": "A", "date": "2000"},
        db: {"album": "Two", "artist": "B", "date": "2001"},
    }
    s = _make(tmp_path, library_source="separate", library_folder=a, tagmap=tagmap)
    s._rebuild_library_index()
    assert s.libraryAlbumPresence("A", "One", "2000", 1)["present"]
    s.setWavesPref("library_folder", b)
    s.rescanLibrary()
    assert s.libraryAlbumPresence("B", "Two", "2001", 1)["present"]
    s.setWavesPref("library_folder", a)  # back to the first folder
    # Before any scan runs, the reopened cache already holds folder A's albums:
    # that is the warm start (matches_scan_root lets the badges seed from it).
    assert s._library.matches_scan_root(a)
    assert next(iter(s._library.iter_albums()))["title"] == "One"
    s.rescanLibrary()
    assert s.libraryAlbumPresence("A", "One", "2000", 1)["present"]
    assert s.libraryAlbumPresence("B", "Two", "2001", 1)["present"] is False


def test_download_folder_change_reindexes_when_library_follows_it(tmp_path):
    # In "download" source the download folder IS the library, so moving it drops
    # stale badges. (In the app, applySettings calls _invalidate_library_index
    # when download_base_path changed and the source is "download", then starts
    # the new folder's scan; the download folder is edited through the Save flow.)
    d1 = str(tmp_path / "dl1")
    d2 = str(tmp_path / "dl2")
    a1 = _album(d1, "A/One", ["1.flac"])
    a2 = _album(d2, "B/Two", ["1.flac"])
    tagmap = {
        a1: {"album": "One", "artist": "A", "date": "2000"},
        a2: {"album": "Two", "artist": "B", "date": "2001"},
    }
    s = _make(tmp_path, library_source="download", download_base=d1, tagmap=tagmap)
    s._rebuild_library_index()
    assert s.libraryAlbumPresence("A", "One", "2000", 1)["present"]
    s.settings.data.download_base_path = d2
    s._invalidate_library_index()  # what applySettings does on the change
    assert s.libraryDownloadFolder() == d2
    assert s._library_index is None
    assert s.libraryAlbumPresence("A", "One", "2000", 1)["present"] is False
    s.rescanLibrary()
    assert s.libraryAlbumPresence("B", "Two", "2001", 1)["present"]


def test_scan_status_surfaced_and_signalled(tmp_path):
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "A/Alb", ["1.flac"])
    s = _make(tmp_path, library_folder=lib, tagmap={d: {"album": "Alb", "artist": "A", "date": "2000"}})
    s._rebuild_library_index()
    assert s._library_scan_status == "ok"
    assert s._library_scan_progress.get("indexed") == 1
    # Signalled at least at scan start ("scanning" appears) and end (it clears).
    assert len(s.libraryScanStatusChanged.emits) >= 2
    # An absent folder reports "missing" and re-signals.
    before = len(s.libraryScanStatusChanged.emits)
    s._waves_prefs["library_folder"] = str(tmp_path / "gone")
    s._library_gen += 1
    s._rebuild_library_index()
    assert s._library_scan_status == "missing"
    assert len(s.libraryScanStatusChanged.emits) > before


def test_presence_carries_local_quality_for_the_badge(tmp_path):
    # The badge names what you have: quality facts captured by the scan ride
    # through the index into the presence answer as a display label plus the
    # raw lossless/bits facts.
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d1 = _album(lib, "A/Lossy", ["1.mp3"])
    d2 = _album(lib, "B/Hires", ["1.flac"])
    s = _make(
        tmp_path,
        library_folder=lib,
        tagmap={
            d1: {"album": "Lossy", "artist": "A", "date": "2000", "codec": "mp3", "bitrate": 128, "bits": 0},
            d2: {"album": "Hires", "artist": "B", "date": "2001", "codec": "flac", "bitrate": 2900, "bits": 24},
        },
    )
    s._rebuild_library_index()
    r = s.libraryAlbumPresence("A", "Lossy", "2000", 1)
    assert r["local_quality"] == "MP3 128KBPS" and r["local_lossless"] is False
    r = s.libraryAlbumPresence("B", "Hires", "2001", 1)
    assert r["local_quality"] == "FLAC 24-BIT" and r["local_lossless"] is True and r["local_bits"] == 24
    # A copy indexed before quality capture (no codec) shows no label.
    assert s.libraryAlbumPresence("C", "None", "2002", 1)["local_quality"] == ""


def test_local_quality_label_includes_the_sample_rate():
    # The rate marks hi-res on its own: a 16-bit/96 kHz file must not be
    # undersold as plain lossless, and above-CD copies show both facts.
    from tidaler.matching import local_quality_label as _local_quality_label

    assert _local_quality_label("flac", 2900, 24, 96000) == "FLAC 24-BIT 96KHZ"
    assert _local_quality_label("flac", 1500, 16, 96000) == "FLAC 96KHZ"
    assert _local_quality_label("flac", 0, 24, 88200) == "FLAC 24-BIT 88.2KHZ"
    # CD grade (44.1/48 at 16-bit or unknown) stays a bare codec label.
    assert _local_quality_label("flac", 900, 16, 44100) == "FLAC"
    assert _local_quality_label("flac", 900, 0, 48000) == "FLAC"
    # Lossy still reads codec + bitrate; the rate adds nothing there.
    assert _local_quality_label("mp3", 320, 0, 44100) == "MP3 320KBPS"


def test_local_quality_label_covers_every_codec_family():
    # Every audio type a library can hold gets a proper label, not just the
    # popular ones: a user with WAV rips, Ogg Vorbis or a DSD collection must
    # see their format recognized, never a blank or a wrong badge.
    from tidaler.matching import local_quality_label as _local_quality_label

    # Uncompressed / lossless containers, with hi-res facts when above CD.
    assert _local_quality_label("wav", 0, 24, 96000) == "WAV 24-BIT 96KHZ"
    assert _local_quality_label("wav", 0, 16, 44100) == "WAV"
    assert _local_quality_label("aiff", 0, 24, 48000) == "AIFF 24-BIT"
    assert _local_quality_label("aif", 0, 16, 44100) == "AIFF"  # legacy alias rows
    assert _local_quality_label("aifc", 0, 16, 44100) == "AIFF"
    assert _local_quality_label("alac", 0, 24, 88200) == "ALAC 24-BIT 88.2KHZ"
    # Lossless compressors, popular and niche alike.
    assert _local_quality_label("ape", 0, 16, 44100) == "APE"
    assert _local_quality_label("wv", 0, 24, 96000) == "WAVPACK 24-BIT 96KHZ"
    assert _local_quality_label("tta", 0, 16, 44100) == "TTA"
    assert _local_quality_label("tak", 0, 16, 44100) == "TAK"
    assert _local_quality_label("ofr", 0, 16, 44100) == "OPTIMFROG"
    # DSD: 1-bit at MHz rates reads as the DSD64/128/256 family, never "1-BIT".
    assert _local_quality_label("dsf", 0, 1, 2822400) == "DSD64"
    assert _local_quality_label("dff", 0, 1, 5644800) == "DSD128"
    assert _local_quality_label("dsf", 0, 1, 11289600) == "DSD256"
    assert _local_quality_label("dsf", 0, 1, 0) == "DSD"  # rate not captured
    # Lossy families all read codec + bitrate.
    assert _local_quality_label("vorbis", 192, 0, 44100) == "VORBIS 192KBPS"
    assert _local_quality_label("opus", 160, 0, 48000) == "OPUS 160KBPS"
    assert _local_quality_label("opus", 0, 0, 48000) == "OPUS"  # opus hides bitrate
    assert _local_quality_label("speex", 24, 0, 32000) == "SPEEX 24KBPS"
    assert _local_quality_label("aac", 256, 0, 44100) == "AAC 256KBPS"
    assert _local_quality_label("wma", 128, 0, 44100) == "WMA 128KBPS"
    assert _local_quality_label("mpc", 175, 0, 44100) == "MUSEPACK 175KBPS"
    # Rows scanned before the Ogg container was resolved still read sanely.
    assert _local_quality_label("ogg", 192, 0, 44100) == "OGG 192KBPS"
    # A codec this map has never heard of still shows itself, uppercased.
    assert _local_quality_label("shn", 0, 16, 44100) == "SHN"


def test_local_quality_class_bands():
    # The coarse class drives the pill's at-a-glance color: gold hi-res, green
    # lossless, cyan healthy lossy, red small lossy, neutral when unknown.
    from tidaler.matching import local_quality_class as _local_quality_class

    assert _local_quality_class("flac", 0, 24, 96000) == "hires"
    assert _local_quality_class("flac", 0, 16, 96000) == "hires"  # rate alone is hi-res
    assert _local_quality_class("dsf", 0, 1, 2822400) == "hires"
    assert _local_quality_class("flac", 0, 16, 44100) == "lossless"
    assert _local_quality_class("wav", 0, 0, 0) == "lossless"
    assert _local_quality_class("mp3", 320, 0, 44100) == "high"
    assert _local_quality_class("aac", 0, 0, 0) == "high"  # unknown bitrate: benefit of the doubt
    assert _local_quality_class("mp3", 128, 0, 44100) == "low"
    assert _local_quality_class("", 0, 0, 0) == ""


def test_rescan_picks_up_music_added_after_the_first_scan(tmp_path):
    # Music added from outside Waves has no filesystem watcher over a network
    # mount; the Rescan button (and the hourly sweep, same code path) is how it
    # gains a badge without a relaunch.
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d1 = _album(lib, "A/One", ["1.flac"])
    tagmap = {d1: {"album": "One", "artist": "A", "date": "2000"}}
    s = _make(tmp_path, library_folder=lib, tagmap=tagmap)
    s._rebuild_library_index()
    assert s.libraryAlbumPresence("B", "Two", "2001", 1)["present"] is False
    # An album lands on the share while Waves runs...
    d2 = _album(lib, "B/Two", ["1.flac"])
    tagmap[d2] = {"album": "Two", "artist": "B", "date": "2001"}
    s.rescanLibrary()
    assert s.libraryAlbumPresence("B", "Two", "2001", 1)["present"] is True
    assert s.libraryAlbumPresence("A", "One", "2000", 1)["present"] is True  # old rows survive


def test_startup_seed_shows_badges_before_the_scan_finishes(tmp_path):
    # A relaunch must not be badge-less while it re-checks the library: the first
    # build seeds the in-memory index straight from the committed DB (badges up at
    # once), then the change-check runs underneath. Proven by a no-change relaunch,
    # where the seed is the ONLY reason a badge is present at the first publish.
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "A/Alb", ["1.flac"])
    tagmap = {d: {"album": "Alb", "artist": "A", "date": "2000"}}
    s = _make(tmp_path, library_folder=lib, tagmap=tagmap)
    s._rebuild_library_index()  # first run: populates the sqlite DB and the index
    assert s.libraryAlbumPresence("A", "Alb", "2000", 1)["present"]

    # Relaunch: the in-memory index is empty again, but the DB on disk is warm.
    # Record whether the album is already present at each publish.
    s._library_index = None
    present_at_publish: list = []

    class _Recorder:
        def emit(self, *a):
            present_at_publish.append(s.libraryAlbumPresence("A", "Alb", "2000", 1).get("present"))

    s.libraryPresenceChanged = _Recorder()
    s._rebuild_library_index()
    # The first publish is the seed and already shows the badge (nothing has been
    # re-read yet), and a final publish follows. Without the seed there would be a
    # single, post-scan publish only.
    assert len(present_at_publish) >= 2
    assert present_at_publish[0] is True


def test_the_seed_is_dispatched_ahead_of_the_scan(tmp_path):
    # The seed is its own pool job, queued FIRST, because the badge answer a
    # committed cache already holds must not wait behind a scan that shares a
    # pool with downloads and art fetches. Running only the first queued worker
    # has to be enough to light the badges.
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "A/Alb", ["1.flac"])
    tagmap = {d: {"album": "Alb", "artist": "A", "date": "2000"}}
    s = _make(tmp_path, library_folder=lib, tagmap=tagmap)
    s._rebuild_library_index()  # warms the on-disk cache

    s._library_index = None  # relaunch
    queued: list = []
    s.threadpool = SimpleNamespace(start=lambda w, priority=0: queued.append((w, priority)))
    s._rebuild_library_index()
    assert len(queued) == 2, "the seed and the scan are two separate jobs"
    assert queued[0][1] > queued[1][1], "the seed is dispatched at the higher priority"
    queued[0][0].run()  # the seed alone, with the scan still sitting in the queue
    assert s.libraryAlbumPresence("A", "Alb", "2000", 1)["present"] is True


def test_a_slow_seed_cannot_overwrite_a_finished_scan(tmp_path):
    # The seed now runs BESIDE the scan instead of before it, so a fast scan can
    # publish while the seed is still assembling an older picture of the same
    # folder. The last writer must not be the stale one.
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "A/Alb", ["1.flac"])
    tagmap = {d: {"album": "Alb", "artist": "A", "date": "2000"}}
    s = _make(tmp_path, library_folder=lib, tagmap=tagmap)
    s._rebuild_library_index()

    s._library_index = None
    queued: list = []
    s.threadpool = SimpleNamespace(start=lambda w, priority=0: queued.append(w))
    s._rebuild_library_index()
    seed, scan = queued

    # The scan publishes in the gap between the seed asking "has anyone
    # published?" and the seed publishing, which is the whole reason that pair
    # is one critical section. Firing off the lock's first acquisition puts the
    # scan exactly there: with the check and the set held together, the seed
    # finds the newer index and stands down; with a bare check the pair is not
    # under any lock at all, this hook never fires, and the assertion below
    # says so rather than passing on an ordering that never happened.
    published: list = []
    real_lock = s._library_index_lock

    class _PublishOnFirstEntry:
        def __init__(self):
            self.entries = 0

        def __enter__(self):
            self.entries += 1
            if self.entries == 1:
                # Before acquiring: the scan takes this same lock, and it is
                # not reentrant.
                scan.run()
                published.append(s._library_index)
            real_lock.acquire()
            return True

        def __exit__(self, *_exc):
            real_lock.release()
            return False

    s._library_index_lock = _PublishOnFirstEntry()
    seed.run()
    s._library_index_lock = real_lock
    assert published, "the seed's check and publish did not happen under the lock"
    assert s._library_index is published[0], "a late seed overwrote a newer index"


def test_index_ready_is_true_when_no_library_is_configured(tmp_path):
    # The factory default is off. A page that waits for the library to answer
    # must not wait forever for a scan that is never going to run.
    s = _make(tmp_path, library_enabled=False, library_folder=str(tmp_path / "lib"))
    assert s.libraryIndexReady() is True


def test_index_ready_is_false_until_the_first_publish(tmp_path):
    # This is the whole point of the slot: between launch and the first publish
    # every presence call answers "not present", so a page built in that window
    # renders with no badges and then lights all of them at once.
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "A/Alb", ["1.flac"])
    s = _make(tmp_path, library_folder=lib, tagmap={d: {"album": "Alb", "artist": "A", "date": "2000"}})
    assert s.libraryIndexReady() is False
    s._rebuild_library_index()
    assert s.libraryIndexReady() is True


def test_index_ready_stops_waiting_on_a_folder_that_cannot_be_read(tmp_path):
    # A root that is absent (an offline NAS, a wrong path) never publishes an
    # index at all: refresh returns before it stamps a scan root, so nothing
    # satisfies the publish gate, ever. Read as "not yet", that left every
    # search for the rest of the session waiting out the build veil's guard for
    # badges that were never coming. "Cannot be read" is an answer.
    missing = str(tmp_path / "gone")
    s = _make(tmp_path, library_folder=missing)
    assert s.libraryIndexReady() is False, "nothing has been asked yet, so nothing is known"
    s._rebuild_library_index()
    assert s._library_index is None, "an unreadable root must not publish an index"
    assert s.libraryIndexReady() is True


def test_generation_guard_drops_stale_build(tmp_path):
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "A/Alb", ["1.flac"])
    s = _make(tmp_path, library_folder=lib, tagmap={d: {"album": "Alb", "artist": "A", "date": "2000"}})
    orig = s._library.refresh

    def bump_then_refresh(root, **kw):
        s._library_gen += 1  # a library-folder change lands while the scan runs
        return orig(root, **kw)

    s._library.refresh = bump_then_refresh
    s._rebuild_library_index()
    assert s._library_index is None  # stale build discarded, never assigned
    assert s.libraryPresenceChanged.emits == []


def test_artist_rollup_counts_albums_and_tracks(tmp_path):
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d1 = _album(lib, "Lorna Shore/Pain Remains", ["1.flac", "2.flac", "3.flac"])
    d2 = _album(lib, "Lorna Shore/And I Return", ["1.flac", "2.flac"])
    s = _make(
        tmp_path,
        library_folder=lib,
        tagmap={
            d1: {"album": "Pain Remains", "artist": "Lorna Shore", "date": "2022"},
            d2: {"album": "And I Return To Nothingness", "artist": "Lorna Shore", "date": "2021"},
        },
    )
    s._rebuild_library_index()
    r = s.artistLibraryPresence("Lorna Shore")
    assert r["present"] and r["albums"] == 2 and r["tracks"] == 5
    # An artist with nothing on disk stays hidden.
    assert s.artistLibraryPresence("Boards of Canada")["present"] is False


def test_artist_rollup_dedups_editions(tmp_path):
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    # Two folders that normalise to the same album key (a deluxe reissue) count
    # once, at the best (max) track count, never as two separate albums.
    d1 = _album(lib, "A/Alb", ["1.flac", "2.flac"])
    d2 = _album(lib, "A/Alb Deluxe", ["1.flac", "2.flac", "3.flac"])
    s = _make(
        tmp_path,
        library_folder=lib,
        tagmap={
            d1: {"album": "Alb", "artist": "A", "date": "2000"},
            d2: {"album": "Alb (Deluxe Edition)", "artist": "A", "date": "2000"},
        },
    )
    s._rebuild_library_index()
    r = s.artistLibraryPresence("A")
    assert r["present"] and r["albums"] == 1 and r["tracks"] == 3


def test_artist_rollup_excludes_various_artists(tmp_path):
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "VA/Comp", ["1.flac", "2.flac"])
    s = _make(tmp_path, library_folder=lib, tagmap={d: {"album": "Comp", "artist": "Various Artists", "date": "2010"}})
    s._rebuild_library_index()
    assert s.artistLibraryPresence("Various Artists")["present"] is False


def test_artist_rollup_hidden_when_index_not_built(tmp_path):
    s = _make(tmp_path, library_folder=str(tmp_path))
    assert s.artistLibraryPresence("A") == {"present": False, "albums": 0, "tracks": 0}


def test_artist_rollup_cache_rebuilds_when_index_swaps(tmp_path):
    # The rollup is cached against the album-index object; a fresh scan swaps in a
    # NEW dict, so the next query must recompute rather than serve a stale count.
    s = _make(tmp_path, library_folder=str(tmp_path))
    s._library_index = {_presence_key("One", "A"): [{"year": "2000", "tracks": 1, "id": "/x", "codec": "flac"}]}
    assert s.artistLibraryPresence("A")["albums"] == 1
    s._library_index = {
        _presence_key("One", "A"): [{"year": "2000", "tracks": 1, "id": "/x", "codec": "flac"}],
        _presence_key("Two", "A"): [{"year": "2001", "tracks": 2, "id": "/y", "codec": "flac"}],
    }
    assert s.artistLibraryPresence("A")["albums"] == 2


def test_seed_crash_cannot_wedge_the_building_flag(tmp_path):
    # The startup seed reads the committed DB before the try that guards the
    # scan; if that read raises (disk I/O error, a close race), the Worker
    # swallows it. The building flag must still clear, or every later rebuild
    # (timers, Rescan, ownershipChanged) no-ops for the whole session.
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "A/Alb", ["1.flac"])
    s = _make(tmp_path, library_folder=lib, tagmap={d: {"album": "Alb", "artist": "A", "date": "2000"}})
    s._rebuild_library_index()  # a first scan commits rows for the seed to read
    s._library_index = None  # simulate the next launch (nothing published yet)

    def boom():
        raise RuntimeError("sqlite disk I/O error")

    s._library.iter_albums = boom
    # In production the Worker wrapper swallows the raise; suppress mirrors that
    # in case the inline pool lets it propagate.
    with contextlib.suppress(RuntimeError):
        s._rebuild_library_index()
    assert s._library_index_building is False  # not wedged: the next trigger scans
    del s._library.iter_albums
    s._rebuild_library_index()
    assert s._library_index is not None  # and it actually recovers


def test_a_long_download_batch_still_gets_its_badges(tmp_path, monkeypatch):
    """The per-track rebuild debounce has a ceiling.

    THE BUG: every landed track restarted a 15s settle window, and any sustained
    download lands tracks faster than that, so the deadline kept moving and the
    rebuild never ran. Badges froze for the whole batch (on a discography, hours)
    and the moment they matter most is exactly when they stopped.

    Driven on a fake clock: tracks land every second forever, and the rebuild
    must still happen, roughly on the ceiling's cadence.
    """
    from tidaler.waves_ui import bridge_library

    s = _make(tmp_path, library_source="download", download_base=str(tmp_path / "dl"))
    s._on_download_recorded = WavesBridge._on_download_recorded.__get__(s)

    rebuilds: list[int] = []
    s._rebuild_library_index = lambda **kw: rebuilds.append(1)

    now = {"t": 1000.0}
    monkeypatch.setattr(bridge_library.time, "monotonic", lambda: now["t"])

    # A single-shot QTimer stand-in: active until it is stopped, never fires on
    # its own, which is exactly the situation the bug lived in.
    class _Timer:
        def __init__(self):
            self.active = False

        def isActive(self):
            return self.active

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

    s._library_dl_debounce = _Timer()
    s._library_dl_burst_start = 0.0

    ceiling = bridge_library._LIBRARY_DL_MAX_DEBOUNCE_S
    for _ in range(int(ceiling * 5)):  # five ceilings' worth of steady downloading
        s._on_download_recorded()
        now["t"] += 1.0

    assert rebuilds, "badges never refreshed during a long download batch"
    assert 3 <= len(rebuilds) <= 6, f"expected a rebuild about every {ceiling}s, got {len(rebuilds)}"


def test_a_short_download_batch_still_coalesces(tmp_path, monkeypatch):
    """And the ceiling does not break the debounce it guards: a burst shorter
    than the ceiling still collapses to nothing forced, leaving the ordinary
    settle timer to run one rebuild after the last track."""
    from tidaler.waves_ui import bridge_library

    s = _make(tmp_path, library_source="download", download_base=str(tmp_path / "dl"))
    s._on_download_recorded = WavesBridge._on_download_recorded.__get__(s)
    rebuilds: list[int] = []
    s._rebuild_library_index = lambda **kw: rebuilds.append(1)
    now = {"t": 500.0}
    monkeypatch.setattr(bridge_library.time, "monotonic", lambda: now["t"])

    class _Timer:
        def __init__(self):
            self.active = False
            self.starts = 0

        def isActive(self):
            return self.active

        def start(self):
            self.active = True
            self.starts += 1

        def stop(self):
            self.active = False

    s._library_dl_debounce = _Timer()
    s._library_dl_burst_start = 0.0

    for _ in range(20):  # 20 tracks over 20 seconds, well inside the ceiling
        s._on_download_recorded()
        now["t"] += 1.0

    assert rebuilds == [], "a short batch must not force a rebuild mid-flight"
    assert s._library_dl_debounce.isActive()  # the settle timer will do it


def test_the_watcher_sync_slot_never_touches_the_disk(tmp_path):
    """The GUI-thread half of the watcher realignment cannot block the window.

    THE BUG: _sync_library_watch runs on the GUI thread and used to answer two
    questions there: is this root on a local disk (QStorageInfo, which STATS THE
    VOLUME) and which container folders exist (a sqlite read). On a dead network
    mount that stat can hang for many seconds, and the window is frozen for all
    of them. Both answers are now resolved on the pool by the scan that emits
    _librarySyncWatch, and arrive as arguments.

    Pinned by booby-trapping both: a classifier and a database that raise if the
    slot so much as asks. It must still do its job from the arguments alone.
    """

    def _exploded(*_a, **_k):
        raise AssertionError("the GUI-thread watcher sync touched the disk")

    s = _make(tmp_path, library_folder=str(tmp_path / "lib"))
    s._library_root_is_local = _exploded
    s._library.container_paths = _exploded
    s._sync_library_watch = WavesBridge._sync_library_watch.__get__(s)
    s._add_watch_chunk = WavesBridge._add_watch_chunk.__get__(s)
    s._teardown_library_watch = WavesBridge._teardown_library_watch.__get__(s)  # _make no-ops it
    s._watched_paths = {"/lib/gone"}
    s._library_watch_pending_add = []
    added: list[list[str]] = []
    removed: list[list[str]] = []
    s._library_watcher = SimpleNamespace(
        addPaths=lambda ps: (added.append(list(ps)), [])[1],
        removePaths=lambda ps: removed.append(list(ps)),
    )

    s._sync_library_watch(True, ["/lib/a", "/lib/b"])

    assert removed == [["/lib/gone"]], "a vanished container must be unwatched"
    assert sorted(added[0]) == ["/lib/a", "/lib/b"]
    assert s._watched_paths == {"/lib/a", "/lib/b"}

    # And a non-local root drops every watch without asking the disk either.
    s._sync_library_watch(False, [])
    assert s._watched_paths == set()


def test_two_threads_cannot_claim_the_same_scan(tmp_path):
    """Claiming the scan is mutually exclusive, so two scans never walk the same
    sqlite cache at once.

    THE BUG: ``_rebuild_library_index`` is entered from the GUI thread (poll and
    sweep timers, the watcher, Rescan) and from a pool thread (a finishing scan
    dispatching its own trailing rebuild). "if not building: building = True" is
    two bytecodes with a thread switch available in between, so both callers
    could pass the check, and two concurrent scans over one cache were measured
    publishing a 60-album index as 0 albums.

    Driven deterministically: the first caller is held INSIDE the critical
    section while a second caller runs at it. The second must block, then find
    the scan already claimed and record itself as pending instead. (Take the
    lock away and the wrapper below is never entered, ``inside`` never fires,
    and this fails on the wait.)
    """
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "A/One", ["1.flac"])
    s = _make(tmp_path, library_folder=lib, tagmap={d: {"album": "One", "artist": "A", "date": "2000"}})

    real = s._library_index_lock
    inside = threading.Event()
    release = threading.Event()
    stalled_once = []

    class _StallingLock:
        """Holds the FIRST acquirer inside the critical section until released,
        so the window the bug lived in can be aimed at on purpose."""

        def __enter__(self):
            real.acquire()
            if not stalled_once:
                stalled_once.append(True)
                inside.set()
                release.wait(5)
            return self

        def __exit__(self, *exc):
            real.release()

    s._library_index_lock = _StallingLock()

    first_done = threading.Event()
    second_done = threading.Event()

    def first():
        s._rebuild_library_index()
        first_done.set()

    def second():
        s._rebuild_library_index()
        second_done.set()

    t1 = threading.Thread(target=first)
    t1.start()
    assert inside.wait(5), "the claim is not taken under the lock any more"

    t2 = threading.Thread(target=second)
    t2.start()
    # The second caller must be BLOCKED, not racing the first through the claim.
    assert not second_done.wait(0.3), "a second caller claimed the scan while the first was claiming it"
    assert s._library_index_pending is False  # it has not got that far yet

    release.set()
    t1.join(10)
    t2.join(10)
    assert first_done.is_set() and second_done.is_set()
    # The second caller found the scan claimed and coalesced into it, which is
    # the whole point of the flag: one scan, one trailing rebuild.
    assert s._library_index is not None


def test_offline_new_folder_does_not_resurrect_old_badges(tmp_path):
    # Switching the library to a folder that is currently unreadable (an
    # unplugged drive, a down NAS) must not republish the previous folder's
    # albums as the current index: the probe fails before the root switch is
    # recorded, so the database still holds the OLD library's rows.
    a = str(tmp_path / "libA")
    da = _album(a, "A/One", ["1.flac"])
    s = _make(
        tmp_path,
        library_source="separate",
        library_folder=a,
        tagmap={da: {"album": "One", "artist": "A", "date": "2000"}},
    )
    s._rebuild_library_index()
    assert s.libraryAlbumPresence("A", "One", "2000", 1)["present"]
    s.setWavesPref("library_folder", str(tmp_path / "not-plugged-in"))
    assert s._library_index is None  # cleared by the switch
    s.rescanLibrary()  # probe fails: nothing to publish
    assert s._library_index is None, "the old library's badges must not come back"
    assert s.libraryAlbumPresence("A", "One", "2000", 1)["present"] is False
    assert s._library_scan_status == "missing"


def test_presence_never_reaches_the_download_engine():
    """THE safety property of this whole feature: the tag-matched presence index
    can DECLINE a fetch, and it can do NOTHING else.

    The engine's own skip decisions (ownership store, skip_existing, the ISRC
    scan) run on exact identifiers Waves recorded itself. Presence reaches the
    download path in exactly one shape: the bulk claim gate
    (library_bulk_skip), which answers "don't fetch this" through callables the
    bridge injects at enqueue time. A claim can therefore only ever cost a
    re-click, never a file: the engine module itself still never pulls in the
    matcher, so no code that writes, moves or overwrites can consult a tag
    guess, and _claim_verdict (tested in test_library_claim_gate.py) only maps
    a claim to "skip", never to the overwriting "force". The QML side keeps its
    click-through (gold MAYBE IN LIBRARY, DOWNLOAD ANYWAY), which registers an
    override so a claim is never the end of the conversation.

    Pinned by IMPORT CLOSURE, not by grepping for a substring. The substring
    version read download.py for the literal "tidaler.matching", which survives
    `from tidaler import matching`, `import tidaler.matching as m`, an
    `importlib.import_module` call, and reaching the matcher through any third
    module. Importing the engine in a clean interpreter and looking at what
    landed in sys.modules survives all of those, because it asks what the code
    actually pulls in rather than how it spells it.
    """
    import ast
    import pathlib
    import subprocess
    import sys

    root = pathlib.Path(__file__).resolve().parent.parent

    # 1. The whole transitive closure of the engine, however it is spelled.
    probe = "import sys, tidaler.download; print('tidaler.matching' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, f"could not import the download engine:\n{out.stderr}"
    assert out.stdout.strip() == "False", (
        "tidaler.matching is now in the download engine's import closure. The engine "
        "must never consult the presence matcher, however indirectly."
    )

    # 2. A lazy import inside a function body never reaches sys.modules until it
    #    runs, so the closure check alone would miss it. Walk the AST at every
    #    depth rather than only the module's top level.
    engine_tree = ast.parse((root / "tidaler" / "download.py").read_text(encoding="utf-8"))
    for node in ast.walk(engine_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0:2] != [
                    "tidaler",
                    "matching",
                ], f"download.py imports the presence matcher at line {node.lineno}"
        elif isinstance(node, ast.ImportFrom):
            imported = f"{node.module or ''}.{ '.'.join(a.name for a in node.names) }"
            assert "matching" not in imported, f"download.py imports the presence matcher at line {node.lineno}"

    # 3. And the decision it all turns on has exactly two callers, both in
    #    bridge_library: the badge slot and the bulk claim helper. Not the
    #    engine, and not the rest of the bridge: backend reaches presence only
    #    through the bridge's claim helpers, whose answers are skip-or-nothing.
    backend = (root / "tidaler" / "waves_ui" / "backend.py").read_text(encoding="utf-8")
    assert "decide_presence" not in backend, "presence answers stay in bridge_library"
    bridge = (root / "tidaler" / "waves_ui" / "bridge_library.py").read_text(encoding="utf-8")
    assert bridge.count("decide_presence") == 2  # the badge slot + _library_claims_album
    assert bridge.count("decide_track_presence") == 2  # the pill slot + _library_claims_track


def test_track_presence_answers_from_the_same_scan(tmp_path):
    # End to end: the scan reads per-file titles, the rebuild publishes the
    # track index beside the album one, and the slot answers exact tracks,
    # including a track from an INCOMPLETE album copy (the pill's whole point).
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "A/Alb", ["1.flac"])  # one track of a 12-track album
    s = _make(
        tmp_path,
        library_folder=lib,
        tagmap={d: {"album": "Alb", "artist": "A", "date": "2000", "title": "The Single", "codec": "flac"}},
    )
    # Index not built yet: absent, and unproven with it (the pill's "?" is the
    # safe reading of "no answer", so `sure` is never missing from a verdict).
    assert s.libraryTrackPresence("A", "The Single") == {"present": False, "sure": False}
    s._rebuild_library_index()
    r = s.libraryTrackPresence("A", "The Single")
    assert r["present"] is True
    assert r["local_album_id"] == d
    assert r["local_class"] == "lossless"
    assert s.libraryTrackPresence("A", "Some Other Song")["present"] is False
    assert s.libraryTrackPresence("Somebody Else", "The Single")["present"] is False
    # The identity axis rides on the album the caller names. Unnamed (the
    # two-argument overload) the match stays unproven; named and agreeing, the
    # track inherits its folder's proof and the badge drops the "?".
    assert r["sure"] is False
    assert s.libraryTrackPresence("A", "The Single", "Alb", "2000")["sure"] is True
    assert s.libraryTrackPresence("A", "The Single", "Alb", "1975")["sure"] is False
    assert s.libraryTrackPresence("A", "The Single", "Other Album", "2000")["sure"] is False


def _tidal_album(name, artist, year, num_tracks):
    return SimpleNamespace(name=name, artist=SimpleNamespace(name=artist), year=year, num_tracks=num_tracks)


def test_bulk_claim_answers_full_albums_and_exact_tracks_only(tmp_path):
    # The bulk gate's two questions, answered from the same scan the badges
    # read: an album is only claimed when FULLY matched (the gold-button bar),
    # a partial copy is not claimed at album grain, and a track claim is the
    # pill's exact match. No index means no claims of either kind.
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    full = _album(lib, "A/Whole", ["1.flac", "2.flac"])
    part = _album(lib, "A/Part", ["1.flac"])
    s = _make(
        tmp_path,
        library_folder=lib,
        tagmap={
            full: {"album": "Whole", "artist": "A", "date": "2000", "title": "Song W", "codec": "flac"},
            part: {"album": "Part", "artist": "A", "date": "2001", "title": "Song P", "codec": "flac"},
        },
    )
    # Before any scan: an unbuilt index claims nothing, whatever the prefs say.
    assert s._library_bulk_skip_on() is True
    assert s._library_claims_album(_tidal_album("Whole", "A", 2000, 2)) is False
    assert s._library_claims_track("A", "Song W") is False
    s._rebuild_library_index()
    assert s._library_claims_album(_tidal_album("Whole", "A", 2000, 2)) is True
    # The partial copy (1 of 12 tracks) is not claimed at album grain...
    assert s._library_claims_album(_tidal_album("Part", "A", 2001, 12)) is False
    # ...but its one track is, exactly, and nothing else.
    assert s._library_claims_track("A", "Song P") is True
    assert s._library_claims_track("A", "Some Other Song") is False
    assert s._library_claims_album(_tidal_album("", "A", 2000, 2)) is False
    assert s._library_claims_album(None) is False


def test_bulk_claim_gate_obeys_both_switches(tmp_path):
    # library_bulk_skip off, or the master switch off, and bulk downloads stop
    # consulting the scan entirely.
    s = _make(tmp_path, library_folder=str(tmp_path))
    s._waves_prefs["library_bulk_skip"] = False
    assert s._library_bulk_skip_on() is False
    s._waves_prefs["library_bulk_skip"] = True
    s._waves_prefs["library_enabled"] = False
    assert s._library_bulk_skip_on() is False


def test_a_stale_queued_scan_cannot_wipe_the_new_roots_cache(tmp_path):
    # The user switches library folders while a rebuild sits queued on the
    # pool. The stale worker must never open a scan of the OLD folder against
    # the NEW root's per-root cache file: doing so deleted its dirs tree and
    # stamped the old folder into its scan_root, so the warm cache the
    # per-root design exists to keep was lost and the launch seed refused the
    # file as belonging to a different root.
    lib_a = str(tmp_path / "libA")
    lib_b = str(tmp_path / "libB")
    da = _album(lib_a, "ArtistA/AlbumA", ["1.flac"])
    db = _album(lib_b, "ArtistB/AlbumB", ["1.flac", "2.flac"])
    tagmap = {
        da: {"album": "AlbumA", "artist": "ArtistA", "date": "2001"},
        db: {"album": "AlbumB", "artist": "ArtistB", "date": "2002"},
    }
    s = _make(tmp_path, library_folder=lib_b, tagmap=tagmap)
    s._rebuild_library_index()  # B scanned: its per-root cache is warm
    s._waves_prefs["library_folder"] = lib_a
    s._invalidate_library_index()
    s._rebuild_library_index()  # A scanned: A is now the current root
    # A rebuild of A claims the scan but its worker sits queued on the pool...
    queued = []
    s.threadpool = SimpleNamespace(start=lambda w, priority=0: queued.append(w))
    s._rebuild_library_index()
    # ...while the user switches back to B. No scan follows the switch here
    # (invalidation never dispatches one), so the stale worker runs last.
    s._waves_prefs["library_folder"] = lib_b
    s._invalidate_library_index()
    for w in queued:
        w.run()
    fresh = LibraryIndex(
        cache_file_for_root(str(tmp_path), lib_b),
        read_tags=lambda p: tagmap.get(os.path.dirname(p)),
    )
    assert fresh.matches_scan_root(lib_b), "the stale scan restamped B's cache"
    assert len(list(fresh.iter_albums())) == 1, "B's warm cache was wiped"


def test_a_rebuild_straddling_a_folder_change_is_refused(tmp_path):
    # The other half of the stale-scan family: setWavesPref stores and saves
    # the new folder BEFORE _invalidate_library_index bumps the generation, so
    # a trailing rebuild dispatched from the pool in that gap captures the OLD
    # generation and index while resolving the NEW root. Scanning that pair
    # wiped the old file's dirs tree and stamped the new root into it. The
    # index remembers which root it was opened for, and the mismatch refuses
    # the scan outright (the invalidation that follows re-triggers matched).
    old_lib = str(tmp_path / "old")
    new_lib = str(tmp_path / "new")
    d = _album(old_lib, "A/Alb", ["1.flac"])
    os.makedirs(new_lib, exist_ok=True)
    tagmap = {d: {"album": "Alb", "artist": "A", "date": "2000"}}
    s = _make(tmp_path, library_folder=old_lib, tagmap=tagmap)
    s._library.opened_for_key = root_comparison_key(old_lib)  # as the real open stamps it
    s._rebuild_library_index()  # the old folder's cache is warm
    s._waves_prefs["library_folder"] = new_lib  # the pref lands first...
    s._rebuild_library_index()  # ...and the trailing rebuild fires in the gap
    assert s._library.matches_scan_root(old_lib), "the straddling rebuild restamped the old cache"
    assert len(list(s._library.iter_albums())) == 1, "the old folder's warm cache was wiped"
    assert not s._library_index_building, "a refused rebuild must release its claim"
    # The invalidation that follows the pref write re-triggers with a matched
    # pair, and the new folder scans normally.
    s._invalidate_library_index()
    s._rebuild_library_index()
    assert s._library.matches_scan_root(new_lib)


def _closed(idx) -> bool:
    """True when the index's sqlite connection has been close()d."""
    import sqlite3

    try:
        idx._conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def test_a_folder_change_closes_the_retired_index(tmp_path):
    # Every library-folder change swaps in the new root's cache file; the old
    # object's sqlite connection must close with the swap or each change leaked
    # one for the session (plus its -wal file handle on the old cache).
    lib_a = str(tmp_path / "libA")
    da = _album(lib_a, "ArtistA/AlbumA", ["1.flac"])
    s = _make(tmp_path, library_folder=lib_a, tagmap={da: {"album": "AlbumA", "artist": "ArtistA", "date": "2001"}})
    s._rebuild_library_index()
    retired = s._library
    s._waves_prefs["library_folder"] = str(tmp_path / "libB")
    s._invalidate_library_index()
    assert retired is not s._library
    assert _closed(retired), "the retired index kept its sqlite connection open"
    assert not _closed(s._library)


def test_a_scan_held_index_closes_when_its_scan_ends_not_before(tmp_path):
    # If the swap happens while a scan still holds the old object, closing it
    # under the scan would break its writes mid-flight (its work into the old
    # file is deliberately preserved for a switch back). The scan-held object
    # closes when its superseded worker finishes instead.
    lib_a = str(tmp_path / "libA")
    da = _album(lib_a, "ArtistA/AlbumA", ["1.flac"])
    s = _make(tmp_path, library_folder=lib_a, tagmap={da: {"album": "AlbumA", "artist": "ArtistA", "date": "2001"}})
    s._rebuild_library_index()
    held = s._library
    queued = []
    s.threadpool = SimpleNamespace(start=lambda w, priority=0: queued.append(w))
    s._rebuild_library_index()  # claims the scan; its worker sits queued holding ``held``
    s._waves_prefs["library_folder"] = str(tmp_path / "libB")
    s._invalidate_library_index()
    assert not _closed(held), "closed under a scan that still holds it"
    for w in queued:
        w.run()
    assert _closed(held), "the superseded scan finished without closing its index"


def test_claim_gate_receives_the_duration_witness(tmp_path):
    # A remaster wearing the original's year: the pill proves it through the
    # length witness, and the bulk gate must reach the same verdict, or the
    # queue re-downloads an album whose button already reads IN LIBRARY.
    s = _make(tmp_path, library_folder=str(tmp_path))
    s._library_index = {
        _presence_key("Album", "A"): [{"title": "Album", "year": "1985", "tracks": 12, "id": "fp", "runtime": 2400}]
    }
    album = SimpleNamespace(name="Album", artist=SimpleNamespace(name="A"), year=2011, num_tracks=12, duration=2400)
    assert s._library_claims_album(album) is True


def test_claim_gate_length_refutation_blocks_the_skip(tmp_path):
    # Years agree but the seconds are minutes apart over the same count: a
    # different recording wearing the same name, so the gate must not skip it.
    s = _make(tmp_path, library_folder=str(tmp_path))
    s._library_index = {
        _presence_key("Album", "A"): [{"title": "Album", "year": "2020", "tracks": 12, "id": "fp", "runtime": 1710}]
    }
    album = SimpleNamespace(name="Album", artist=SimpleNamespace(name="A"), year=2020, num_tracks=12, duration=2495)
    assert s._library_claims_album(album) is False


def test_feat_credit_travels_through_the_scan(tmp_path):
    # A tagger's "Song (feat. G)" answers TIDAL's plain "Song" (and vice
    # versa), while a DIFFERENT guest never claims it: the index rows carry
    # the parsed guest list the key deliberately strips.
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "A/Alb", ["1.flac"])
    s = _make(
        tmp_path,
        library_folder=lib,
        tagmap={d: {"album": "Alb", "artist": "A", "date": "2000", "title": "Song (feat. G)", "codec": "flac"}},
    )
    s._rebuild_library_index()
    assert s.libraryTrackPresence("A", "Song")["present"] is True
    assert s.libraryTrackPresence("A", "Song (feat. G)")["present"] is True
    assert s.libraryTrackPresence("A", "Song (feat. Other)")["present"] is False


def test_va_credited_folders_never_enter_the_indexes(tmp_path):
    # "V / A" folds to an artist key of "v" (the spaced-slash split), past
    # both VA detectors, so it rolled up as a real artist "v" and could
    # answer presence for one. The build refuses VA rows on the RAW tag,
    # before any fold: no album row, no track row, no rollup entry.
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "Comps/Summer Hits", ["1.flac"])
    s = _make(
        tmp_path,
        library_folder=lib,
        tagmap={d: {"album": "Summer Hits", "artist": "V / A", "date": "2000", "title": "Song", "codec": "flac"}},
    )
    s._rebuild_library_index()
    assert s._library_index == {}
    assert s._library_track_index == {}
    assert s.artistLibraryPresence("V")["present"] is False


def test_every_scan_offers_the_share_remount_first(tmp_path):
    # The library can live on a share macOS quietly ejects, and no probe or
    # rescan can help until something MOUNTS (the download folder's old
    # disease). Each scan must therefore hand its root to the remount
    # machinery before probing; presence and platform no-op INSIDE it, so
    # the offer itself is unconditional.
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    s = _make(tmp_path, library_folder=lib)
    offered = []
    s._remount_download_share = lambda path: (offered.append(path), False)[1]
    s._rebuild_library_index()
    assert offered == [lib]


def test_a_healthy_scan_records_the_library_shares_origin(tmp_path):
    # Proof of life is when the origin URL is captured (a statfs of a dead
    # mount can hang); downloads earn theirs on a landed file, the library
    # on a scan that read its root. A failed probe must record nothing.
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "A/One", ["1.flac"])
    s = _make(tmp_path, library_folder=lib, tagmap={d: {"album": "One", "artist": "A", "date": "2000"}})
    remembered = []
    s._remember_share_origin = remembered.append
    s._rebuild_library_index()
    assert remembered == [lib]

    gone = _make(tmp_path, library_folder=str(tmp_path / "vanished"))
    gone._remember_share_origin = remembered.append
    gone._rebuild_library_index()
    assert remembered == [lib], "a missing root must not be treated as proof of life"
