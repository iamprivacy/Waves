"""A downloaded file lights its IN LIBRARY badge at once, not after a rescan
(LibraryIndex.index_folders and the bridge's landed-folder drain).

THE BUG THIS FENCES OFF
-----------------------
A finished download reached the presence index only through the debounced
full rebuild: a 15 s settle window that restarts on every track (with a 2 min
ceiling during a batch), and then a walk of the WHOLE library, which on a big
library or a network share takes minutes, and which walks the library folder
before a separate download folder. So the song you had just downloaded showed
no badge for a long while, on every surface (track pill, album pill, artist
badge).

Now the folder the file landed in is indexed on its own and the index is
republished within a couple of seconds. The full rebuild still runs behind it
as the reconciler.

Pinned: the landed folder alone is walked and read (siblings are not re-read);
the rows survive later scans with no duplicate; a cache that was never scanned
for the root, a folder outside the root, and the root itself are refused; a
busy cache answers None. On the bridge: the badge answers present and the
re-ask signal fires without the slow rebuild, for the download folder as the
library and for a download folder beside a separate library; a burst of tracks
is one publish; a busy cache keeps the folder for the next publish; a
sidecar (not audio) or a path outside both folders is ignored.
"""

from __future__ import annotations

import os

import pytest
from test_library_bridge import _album, _make, _Stub
from test_library_listing_truncation import _index, _mk

from waves.waves_ui.backend import WavesBridge
from waves.waves_ui.bridge_library import _rearm

# Every method the drain reaches. A missing one dies inside the drain's own
# catch-all, silently, so the list is checked whole.
for _m in (
    "_library_note_landed",
    "_library_landed_settled",
    "_library_index_landed",
    "_library_landed_rearm",
    "_library_index_landed_in",
    "_library_index_landed_one",
    "_library_worker_index",
):
    setattr(_Stub, _m, getattr(WavesBridge, _m))


# ---- the cache ------------------------------------------------------------------


def _scanned(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    a = _mk(tmp_path, "lib/A/[2020] Alpha", ["01.flac", "02.flac"])
    tags = {a: {"album": "Alpha", "artist": "A", "date": "2020"}}
    reads: list[str] = []
    idx = _index(tmp_path, tags, reads)
    assert idx.refresh(lib) == 1
    reads.clear()
    return lib, a, tags, idx, reads


def test_index_folders_reads_only_the_landed_folder(tmp_path):
    lib, _a, tags, idx, reads = _scanned(tmp_path)
    b = _mk(tmp_path, "lib/B/[2021] Beta", ["01.flac"])
    tags[b] = {"album": "Beta", "artist": "B", "date": "2021"}
    assert idx.index_folders(lib, [b]) == 1
    assert sorted(x["title"] for x in idx.iter_albums()) == ["Alpha", "Beta"]
    assert all(os.path.dirname(p) == b for p in reads), "a sibling album was re-read"


def test_a_same_second_landing_is_still_read(tmp_path):
    """A share with whole-second stamps: the second track lands within the
    same second as the first one's listing, so the folder mtime is unchanged.
    The landed folder is listed and read regardless."""
    lib, a, _tags, idx, _reads = _scanned(tmp_path)
    stamp = os.stat(a).st_mtime
    open(os.path.join(a, "03.flac"), "w").close()
    os.utime(a, (stamp, stamp))
    assert idx.index_folders(lib, [a]) == 1
    assert [x["tracks"] for x in idx.iter_albums()] == [3]


def test_a_track_added_to_a_known_album_is_read_again(tmp_path):
    lib, a, tags, idx, _reads = _scanned(tmp_path)
    open(os.path.join(a, "03.flac"), "w").close()
    future = os.stat(a).st_mtime + 10
    os.utime(a, (future, future))
    assert idx.index_folders(lib, [a]) == 1
    assert [x["tracks"] for x in idx.iter_albums()] == [3]


def test_landed_rows_survive_later_scans_without_a_duplicate(tmp_path):
    lib, _a, tags, idx, _reads = _scanned(tmp_path)
    b = _mk(tmp_path, "lib/B/[2021] Beta", ["01.flac"])
    tags[b] = {"album": "Beta", "artist": "B", "date": "2021"}
    assert idx.index_folders(lib, [b]) == 1
    assert idx.refresh(lib) == 2
    assert idx.refresh(lib, force_full=True) == 2
    assert sorted(x["title"] for x in idx.iter_albums()) == ["Alpha", "Beta"]


def test_index_folders_refuses_what_it_must_not_write(tmp_path):
    lib, _a, tags, idx, _reads = _scanned(tmp_path)
    elsewhere = _mk(tmp_path, "other/C/Gamma", ["01.flac"])
    tags[elsewhere] = {"album": "Gamma", "artist": "C", "date": "2022"}
    assert idx.index_folders(lib, [elsewhere]) == 0  # not under the root
    assert idx.index_folders(lib, [lib]) == 0  # the root itself is the full scan's job
    assert idx.index_folders(str(tmp_path / "other"), [elsewhere]) == 0  # never scanned for that root
    assert sorted(x["title"] for x in idx.iter_albums()) == ["Alpha"]


def test_a_busy_cache_is_not_an_answer(tmp_path):
    lib, _a, tags, idx, _reads = _scanned(tmp_path)
    b = _mk(tmp_path, "lib/B/[2021] Beta", ["01.flac"])
    tags[b] = {"album": "Beta", "artist": "B", "date": "2021"}
    with idx._scan_busy:
        assert idx.index_folders(lib, [b], timeout=0.0) is None
    assert idx.index_folders(lib, [b]) == 1


# ---- the bridge -----------------------------------------------------------------


def _bridge(tmp_path, *, separate):
    dl = str(tmp_path / "dl")
    os.makedirs(dl, exist_ok=True)
    tags: dict = {}
    if separate:
        lib = str(tmp_path / "lib")
        os.makedirs(lib, exist_ok=True)
        a = _album(lib, "A/[2020] Alpha", ["1.flac"])
        tags[a] = {"album": "Alpha", "artist": "A", "date": "2020", "title": "Song"}
        s = _make(tmp_path, library_folder=lib, download_base=dl, tagmap=tags)
    else:
        a = _album(dl, "A/[2020] Alpha", ["1.flac"])
        tags[a] = {"album": "Alpha", "artist": "A", "date": "2020", "title": "Song"}
        s = _make(tmp_path, library_source="download", download_base=dl, tagmap=tags)
    s.emitted = []
    s._emit_from_worker = lambda name: s.emitted.append(name)
    s._rebuild_library_index()
    assert s.libraryTrackPresence("A", "Song", "Alpha", "2020")["present"]
    s.emitted.clear()
    # From here on, the slow path must not be what lights the badge.
    s._rebuild_library_index = lambda **kw: (_ for _ in ()).throw(AssertionError("full rebuild ran"))
    return s, dl, tags


def _land(dl, tags, rel="B/[2021] Beta", name="1.flac"):
    d = _album(dl, rel, [name])
    tags[d] = {"album": "Beta", "artist": "B", "date": "2021", "title": "Fresh"}
    return os.path.join(d, name)


def _beta(s):
    return s.libraryTrackPresence("B", "Fresh", "Beta", "2021")


def test_a_landed_track_lights_its_badge_without_the_rescan(tmp_path):
    s, dl, tags = _bridge(tmp_path, separate=False)
    assert not _beta(s)["present"]
    stamp = s.libraryStamp()
    s._library_note_landed(_land(dl, tags))
    s._library_index_landed()
    assert _beta(s)["present"]
    assert s.libraryAlbumPresence("B", "Beta", "2021", 1)["present"]
    assert s.artistLibraryPresence("B")["present"]
    assert "libraryPresenceChanged" in s.emitted
    assert s.libraryStamp() == stamp + 1


def test_the_settle_timer_dispatches_the_drain(tmp_path):
    """The GUI half: a recorded download starts the landed timer (not the
    slow rebuild), its timeout runs the drain on the pool, and the badge is
    on. A publish pushes the next timer out to the gap."""
    from waves.waves_ui import bridge_library

    s, dl, tags = _bridge(tmp_path, separate=False)

    class _Timer:
        def __init__(self):
            self.started: list[int] = []

        def isActive(self):
            return False

        def start(self, ms=None):
            self.started.append(ms)

        def stop(self):
            pass

    s._library_dl_debounce = _Timer()
    s._library_dl_burst_start = 0.0
    s._library_landed_timer = _Timer()
    s._on_download_recorded = WavesBridge._on_download_recorded.__get__(s)
    s._on_download_recorded()
    assert s._library_landed_timer.started == [], "nothing landed, nothing to index"
    s._library_note_landed(_land(dl, tags))
    s._on_download_recorded()
    assert s._library_landed_timer.started == [bridge_library._LIBRARY_LANDED_SETTLE_MS]
    s._library_landed_settled()  # the inline pool runs the drain
    assert _beta(s)["present"]
    assert "libraryPresenceChanged" in s.emitted
    s._library_note_landed(_land(dl, tags, name="2.flac"))
    s._on_download_recorded()
    gap = s._library_landed_timer.started[-1]
    assert gap > bridge_library._LIBRARY_LANDED_SETTLE_MS
    assert gap <= bridge_library._LIBRARY_LANDED_MIN_GAP_S * 1000


def test_the_recorder_queues_the_landed_file(tmp_path):
    """The ownership recorder hands the landed file's path to the library
    before it tells the GUI thread."""
    from test_issue55_upgrade_keeps_user_files import _recorder

    from waves.ownership import OwnershipStore

    song = tmp_path / "Artist" / "Album" / "01.flac"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"\0")
    stub = _recorder(OwnershipStore(str(tmp_path / "own.db")))
    order: list = []
    stub._library_note_landed = lambda p: order.append(("note", p))
    stub._downloadRecorded.emit = lambda *a: order.append(("emit",))
    stub._record_ownership({"id": "9", "path": str(song), "quality": {"tier": "LOSSLESS"}})
    assert order == [("note", os.path.abspath(str(song))), ("emit",)]


def test_symlink_mode_queues_the_album_folder_not_the_track_folder(tmp_path):
    """Symlink-to-track mode records the real bytes (the track folder), but
    the folder the library walks is the album folder holding the link."""
    from test_issue55_upgrade_keeps_user_files import _recorder

    from waves.ownership import OwnershipStore

    real = tmp_path / "Tracks" / "Artist - 01.flac"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"\0")
    link = tmp_path / "Artist" / "Album" / "01.flac"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("this system cannot make symlinks")
    stub = _recorder(OwnershipStore(str(tmp_path / "own.db")))
    stub.settings.data.symlink_to_track = True
    noted: list = []
    stub._library_note_landed = noted.append
    stub._downloadRecorded.emit = lambda *a: None
    stub._record_ownership({"id": "9", "path": str(link), "quality": {"tier": "LOSSLESS"}})
    assert noted == [os.path.abspath(str(link))]


def test_a_separate_download_folder_lights_too(tmp_path):
    s, dl, tags = _bridge(tmp_path, separate=True)
    assert s._library_extra is not None
    s._library_note_landed(_land(dl, tags))
    s._library_index_landed()
    assert _beta(s)["present"]
    assert "libraryPresenceChanged" in s.emitted


def test_a_burst_of_tracks_is_one_publish(tmp_path):
    s, dl, tags = _bridge(tmp_path, separate=False)
    stamp = s.libraryStamp()
    for n in range(1, 6):
        s._library_note_landed(_land(dl, tags, name=f"{n}.flac"))
    s._library_index_landed()
    assert s.libraryStamp() == stamp + 1
    assert s.emitted.count("libraryPresenceChanged") == 1
    # Nothing new landed: the next drain writes nothing and publishes nothing.
    s._library_index_landed()
    assert s.libraryStamp() == stamp + 1


def test_a_busy_cache_keeps_the_folder_for_the_next_publish(tmp_path):
    s, dl, tags = _bridge(tmp_path, separate=False)
    s._library_note_landed(_land(dl, tags))
    with s._library._scan_busy:
        s._library_index_landed()
    assert not _beta(s)["present"]
    assert "libraryPresenceChanged" not in s.emitted
    # The scan that held the cache publishes; the re-arm asks again.
    _rearm(s)
    assert _beta(s)["present"]


def test_sidecars_and_strangers_are_ignored(tmp_path):
    s, dl, _tags = _bridge(tmp_path, separate=False)
    lyrics = os.path.join(_album(dl, "B/[2021] Beta", ["1.lrc"]), "1.lrc")
    s._library_note_landed(lyrics)
    s._library_note_landed(str(tmp_path / "elsewhere" / "x.flac"))
    s._library_note_landed("")
    assert not getattr(s, "_library_landed", None)
    s._library_index_landed()
    assert "libraryPresenceChanged" not in s.emitted


def test_no_library_no_work(tmp_path):
    s, dl, tags = _bridge(tmp_path, separate=False)
    s._waves_prefs["library_enabled"] = False
    s._library_note_landed(_land(dl, tags))
    assert not getattr(s, "_library_landed", None)
