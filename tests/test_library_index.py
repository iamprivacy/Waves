"""Tests for the local music-library scanner (waves/library_index.py).

Hermetic: a temp tree of empty files stands in for a library, and the tag reader
is injected so no real audio is needed. The default extension predicate decides
what counts as a track.
"""

from __future__ import annotations

import itertools
import os
import time
from pathlib import Path

from waves.library_index import (
    _EMPTY_STRIKE_GAP_S,
    _NETWORK_WORKERS,
    POLL_GAUGE,
    READ_GAUGE,
    SCAN_MISSING,
    SCAN_OK,
    SCAN_UNREADABLE,
    SCAN_UNSET,
    WALK_GAUGE,
    LibraryIndex,
    _numbered,
    _read_album_tags,
    cache_file_for_root,
)


def _mk(base, rel, files):
    d = os.path.join(base, *rel.split("/"))
    os.makedirs(d, exist_ok=True)
    for name in files:
        open(os.path.join(d, name), "w").close()
    return d


def _reader(tagmap, counter=None):
    """A fake tag reader keyed by the file's parent folder."""

    def read(path):
        if counter is not None:
            counter.append(path)
        return tagmap.get(os.path.dirname(path))

    return read


def _index(tmp_path, tagmap, counter=None):
    return LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_reader(tagmap, counter))


def test_scans_album_folders_and_iterates(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d1 = _mk(tmp_path, "lib/Lorna Shore/[2022] Pain Remains", ["01.flac", "02.flac", "03.flac"])
    d2 = _mk(tmp_path, "lib/Boards of Canada/[2002] Geogaddi", ["a.mp3"])
    tags = {
        d1: {"album": "Pain Remains", "artist": "Lorna Shore", "date": "2022"},
        d2: {"album": "Geogaddi", "artist": "Boards of Canada", "date": "2002"},
    }
    idx = _index(tmp_path, tags)
    n = idx.refresh(lib)
    assert n == 2
    got = {a["title"]: a for a in idx.iter_albums()}
    assert got["Pain Remains"]["artist"] == "Lorna Shore"
    assert got["Pain Remains"]["tracks"] == 3
    assert got["Pain Remains"]["year"] == "2022"
    assert got["Pain Remains"]["id"] == d1  # folder path, for reveal
    assert got["Geogaddi"]["tracks"] == 1


def test_track_count_ignores_non_audio(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac", "2.flac", "cover.jpg", "notes.txt", "3.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "2000"}})
    idx.refresh(lib)
    assert next(idx.iter_albums())["tracks"] == 3


def test_folder_with_no_audio_is_not_an_album(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    # Artist folder holds only subfolders (no direct audio) -> not indexed.
    _mk(tmp_path, "lib/Artist", ["bio.txt"])
    d = _mk(tmp_path, "lib/Artist/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "Artist", "date": "1999"}})
    assert idx.refresh(lib) == 1
    assert next(idx.iter_albums())["id"] == d


def test_incremental_skips_unchanged_rereads_changed(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac", "2.flac"])
    calls: list[str] = []
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "2000"}}, counter=calls)
    idx.refresh(lib)
    # Counted as "did the folder get read at all", not as a file total: a folder
    # costs one read for its identity plus up to two more to cross-check that its
    # files really are one album (see _CROSS_CHECK_SAMPLES), so pinning an exact
    # file count here would pin the sampling constant instead of the skip.
    first_pass = len(calls)
    assert first_pass >= 1  # read once
    idx.refresh(lib)
    assert len(calls) == first_pass  # unchanged folder not re-read
    # Adding a track changes the audio count -> folder is re-read.
    open(os.path.join(d, "3.flac"), "w").close()
    idx.refresh(lib)
    assert len(calls) > first_pass
    assert next(idx.iter_albums())["tracks"] == 3


def test_prune_removes_deleted_folder(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d1 = _mk(tmp_path, "lib/A/One", ["1.flac"])
    d2 = _mk(tmp_path, "lib/A/Two", ["1.flac"])
    tags = {d1: {"album": "One", "artist": "A", "date": "1"}, d2: {"album": "Two", "artist": "A", "date": "2"}}
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 2
    # Remove one album's files, then its folder.
    os.remove(os.path.join(d2, "1.flac"))
    os.rmdir(d2)
    assert idx.refresh(lib) == 1
    assert [a["title"] for a in idx.iter_albums()] == ["One"]


def test_missing_root_leaves_cache_intact(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "2000"}})
    assert idx.refresh(lib) == 1
    # A vanished / offline root must NOT wipe the badge index.
    assert idx.refresh(str(tmp_path / "does-not-exist")) == 1
    assert len(list(idx.iter_albums())) == 1


def test_skips_playlists_videos_and_hidden(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    _mk(tmp_path, "lib/Playlists/My Mix", ["1.flac"])
    _mk(tmp_path, "lib/Videos/Clip", ["1.mp4"])
    _mk(tmp_path, "lib/.trash/Old", ["1.flac"])
    d = _mk(tmp_path, "lib/A/Real", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Real", "artist": "A", "date": "2000"}})
    assert idx.refresh(lib) == 1
    assert next(idx.iter_albums())["title"] == "Real"


def test_should_continue_false_aborts_without_wiping(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "2000"}})
    assert idx.refresh(lib) == 1
    # A superseded scan bails immediately; the prior cache survives intact.
    assert idx.refresh(lib, should_continue=lambda: False) == 1
    assert len(list(idx.iter_albums())) == 1


def test_switching_root_prunes_old_root(tmp_path):
    a = _mk(tmp_path, "libA", [])
    da = _mk(tmp_path, "libA/X/One", ["1.flac"])
    b = _mk(tmp_path, "libB", [])
    db = _mk(tmp_path, "libB/Y/Two", ["1.flac"])
    tags = {da: {"album": "One", "artist": "X", "date": "1"}, db: {"album": "Two", "artist": "Y", "date": "2"}}
    idx = _index(tmp_path, tags)
    assert idx.refresh(a) == 1
    assert idx.refresh(b) == 1  # switching root drops libA's rows
    assert [x["title"] for x in idx.iter_albums()] == ["Two"]


def test_empty_root_scans_nothing_never_walks_filesystem_root(tmp_path):
    # A blank library folder must index nothing, and must NEVER fall back to "/"
    # and walk the whole disk.
    idx = _index(tmp_path, {})
    assert idx.refresh("") == 0
    assert idx.refresh("   ") == 0
    assert list(idx.iter_albums()) == []


def test_on_progress_reports_walk_and_read_phases(tmp_path):
    # A cold scan streams live progress: the walk counts discoveries, the read
    # phase announces its known size up front (0 of total) then reports every
    # completed read with the artist/album under the needle, marking database
    # flushes with committed=True. interval 0 disables the rate limit so the
    # full stream is observable.
    lib = _mk(tmp_path, "lib", [])
    tags = {}
    for i in range(250):
        d = _mk(tmp_path, f"lib/A/Album{i:03d}", ["1.flac"])
        tags[d] = {"album": f"Album{i:03d}", "artist": "A", "date": "2000"}
    idx = _index(tmp_path, tags)
    events: list[dict] = []
    assert idx.refresh(lib, on_progress=events.append, progress_interval=0) == 250
    walk = [e for e in events if e["phase"] == "walk"]
    # One event per directory listed (lib + A + 250 album folders, + the forced
    # final), "checked" strictly rising so the counter moves even before the
    # breadth-first walk reaches album depth; "found" never decreases and lands
    # on every album.
    assert len(walk) == 253
    assert [e["checked"] for e in walk] == [*range(1, 253), 252]
    assert all(a["found"] <= b["found"] for a, b in itertools.pairwise(walk))
    assert walk[-1]["found"] == 250
    reads = [e for e in events if e["phase"] == "read"]
    assert (reads[0]["done"], reads[0]["total"]) == (0, 250)
    # One event per read, plus the final committed flush re-reporting done=250.
    assert [e["done"] for e in reads[1:]] == [*range(1, 251), 250]
    assert reads[-1]["done"] == reads[-1]["total"] == 250
    assert reads[1]["artist"] == "A" and reads[1]["album"].startswith("Album")
    # Flushes (every 200 and the final one) are flagged so the caller knows the
    # partial index is queryable; their indexed counts reflect the database.
    committed = [e for e in reads if e.get("committed")]
    assert [(e["done"], e["indexed"]) for e in committed] == [(200, 200), (250, 250)]
    # A warm rescan reads nothing, so no read-phase events (the walk still runs).
    events.clear()
    idx.refresh(lib, on_progress=events.append, progress_interval=0)
    assert [e for e in events if e["phase"] == "read"] == []


def test_on_progress_rate_limited_by_default(tmp_path):
    # With the default interval, a fast scan must not flood the callback: the
    # forced milestones (read announce + flushes) arrive, not hundreds of events.
    lib = _mk(tmp_path, "lib", [])
    tags = {}
    for i in range(250):
        d = _mk(tmp_path, f"lib/A/Album{i:03d}", ["1.flac"])
        tags[d] = {"album": f"Album{i:03d}", "artist": "A", "date": "2000"}
    idx = _index(tmp_path, tags)
    events: list[dict] = []
    assert idx.refresh(lib, on_progress=events.append) == 250
    assert len(events) < 20  # rate-limited: far fewer than one per folder/read
    committed = [e for e in events if e.get("committed")]
    assert [(e["done"], e["indexed"]) for e in committed] == [(200, 200), (250, 250)]


def test_tag_reads_run_concurrently(tmp_path):
    # A cold NAS scan is latency-bound, so tag reads must overlap. A reader that
    # sleeps 50ms per file over 16 albums takes 800ms serially; in parallel it
    # finishes in a couple of pool rounds. Generous bound to stay un-flaky.
    import time as _time

    lib = _mk(tmp_path, "lib", [])
    tags = {}
    for i in range(16):
        d = _mk(tmp_path, f"lib/A/Album{i:02d}", ["1.flac"])
        tags[d] = {"album": f"Album{i:02d}", "artist": "A", "date": "2000"}
    base_reader = _reader(tags)

    def slow_read(path):
        _time.sleep(0.05)
        return base_reader(path)

    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=slow_read)
    t0 = _time.monotonic()
    assert idx.refresh(lib) == 16
    assert _time.monotonic() - t0 < 0.5  # serial would be >= 0.8s


def test_walk_lists_directories_concurrently(tmp_path, monkeypatch):
    # Discovery is one listing per folder, a network round trip on a NAS, so the
    # walk must overlap listings like the tag reads overlap file opens. 20ms per
    # listing over ~49 folders is ~1s serially; concurrent finishes well under.
    import time as _time

    import waves.library_index as li

    lib = _mk(tmp_path, "lib", [])
    tags = {}
    for i in range(24):
        d = _mk(tmp_path, f"lib/Artist{i:02d}/Album", ["1.flac"])
        tags[d] = {"album": "Album", "artist": f"Artist{i:02d}", "date": "2000"}
    idx = _index(tmp_path, tags)

    real_scandir = os.scandir

    def slow_scandir(path=".", *args, **kwargs):
        _time.sleep(0.02)
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(li.os, "scandir", slow_scandir)
    t0 = _time.monotonic()
    assert idx.refresh(lib) == 24
    assert _time.monotonic() - t0 < 0.6  # serial would be >= 1s


def test_scan_status_ok_after_successful_scan(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "2000"}})
    assert idx.refresh(lib) == 1
    assert idx.last_scan_status == SCAN_OK


def test_scan_status_unset_for_blank_root(tmp_path):
    idx = _index(tmp_path, {})
    assert idx.refresh("") == 0
    assert idx.last_scan_status == SCAN_UNSET


def test_scan_status_missing_for_absent_root(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "2000"}})
    assert idx.refresh(lib) == 1
    # An absent / offline root is reported as missing, and the cache survives.
    assert idx.refresh(str(tmp_path / "gone")) == 1
    assert idx.last_scan_status == SCAN_MISSING
    assert len(list(idx.iter_albums())) == 1


def test_scan_status_unreadable_when_root_denies_listing(tmp_path, monkeypatch):
    # A folder that exists (stat/isdir succeed) but whose listing is denied is
    # the TCC-gated network-volume case: os.walk would swallow the EPERM and
    # yield nothing, indistinguishable from empty. The probe must catch it, keep
    # the cache, and report SCAN_UNREADABLE so the UI can say "can't read".
    import waves.library_index as li

    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "2000"}})
    assert idx.refresh(lib) == 1
    assert idx.last_scan_status == SCAN_OK

    real_scandir = os.scandir

    def deny(path=".", *args, **kwargs):
        if os.path.abspath(path) == os.path.abspath(lib):
            raise PermissionError(1, "Operation not permitted")
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(li.os, "scandir", deny)
    assert idx.refresh(lib) == 1  # cache left intact, badges not blanked
    assert idx.last_scan_status == SCAN_UNREADABLE
    assert len(list(idx.iter_albums())) == 1


def test_quality_columns_migrate_and_backfill(tmp_path):
    import sqlite3

    # A cache from before quality capture opens cleanly (columns are added) and
    # its rows re-read ONCE to learn codec/bitrate/bits even though the folder
    # itself is unchanged; after the backfill the row is stable again.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    db = str(tmp_path / "library.sqlite3")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE albums (
        folder_path TEXT NOT NULL PRIMARY KEY, album TEXT, artist TEXT, year TEXT,
        track_count INTEGER NOT NULL DEFAULT 0, dir_mtime REAL NOT NULL DEFAULT 0,
        recorded_at INTEGER NOT NULL DEFAULT 0)""")
    conn.execute(
        "INSERT INTO albums VALUES (?, 'Album', 'A', '2000', 1, ?, 1)",
        (d, os.stat(d).st_mtime),
    )
    conn.commit()
    conn.close()

    calls: list[str] = []
    tags = {d: {"album": "Album", "artist": "A", "date": "2000", "codec": "flac", "bitrate": 900, "bits": 16}}
    idx = LibraryIndex(db, read_tags=_reader(tags, calls))
    assert idx.refresh(lib) == 1
    assert len(calls) == 1  # backfill read despite unchanged mtime/count
    a = next(idx.iter_albums())
    assert a["codec"] == "flac" and a["bitrate"] == 900 and a["bits"] == 16
    idx.refresh(lib)
    assert len(calls) == 1  # backfilled row is incremental again


def test_read_album_tags_none_for_unreadable(tmp_path):
    # A real (non-injected) read of an empty non-audio file: mutagen returns None.
    p = tmp_path / "not-audio.flac"
    p.write_bytes(b"")
    assert _read_album_tags(str(p)) is None


def _scandir_spy(monkeypatch):
    """Record the absolute path of every os.scandir the scanner performs, so a
    test can prove which folders were (and were not) listed."""
    import waves.library_index as li

    calls: list[str] = []
    real = os.scandir

    def spy(path=".", *args, **kwargs):
        calls.append(os.path.abspath(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(li.os, "scandir", spy)
    return calls


def test_skips_nas_metadata_dirs(tmp_path):
    # A NAS writes metadata/thumbnail/recycle folders (some holding media files)
    # under the library; they must never be walked or counted as albums, even
    # though they contain audio. This is the @eaDir explosion that inflated the
    # "checked" count far past the real folder count.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Real Album", ["1.flac", "2.flac"])
    _mk(tmp_path, "lib/@eaDir", ["ghost.flac"])  # Synology, at the root
    _mk(tmp_path, "lib/A/Real Album/@eaDir/SYNOPHOTO_THUMB", ["thumb.jpg"])  # nested per-file thumb dir
    _mk(tmp_path, "lib/#recycle/Deleted", ["old.flac"])  # Synology recycle bin
    _mk(tmp_path, "lib/@Recycle/Trash", ["x.flac"])  # QNAP recycle bin
    idx = _index(tmp_path, {d: {"album": "Real Album", "artist": "A", "date": "2000"}})
    assert idx.refresh(lib) == 1  # only the real album, none of the metadata folders
    got = list(idx.iter_albums())
    assert [a["id"] for a in got] == [d]
    assert got[0]["tracks"] == 2


def test_warm_relaunch_does_not_relist_unchanged_tree(tmp_path, monkeypatch):
    # The point of the persistent tree: a second scan with nothing changed lists
    # NO directories (it stats them and reuses the stored listing). Only the
    # readability probe on the root remains, so a relaunch is not "insane".
    lib = _mk(tmp_path, "lib", [])
    tags = {}
    for i in range(30):
        d = _mk(tmp_path, f"lib/Artist{i % 5}/Album{i:02d}", ["1.flac"])
        tags[d] = {"album": f"Album{i:02d}", "artist": f"Artist{i % 5}", "date": "2000"}
    idx = _index(tmp_path, tags)

    cold = _scandir_spy(monkeypatch)
    assert idx.refresh(lib) == 30
    assert len(cold) >= 30  # cold scan listed the whole tree

    warm = _scandir_spy(monkeypatch)
    assert idx.refresh(lib) == 30
    # Only the root readability probe; the walk itself listed nothing.
    assert warm == [os.path.abspath(lib)]


def test_incremental_finds_addition_without_relisting_siblings(tmp_path, monkeypatch):
    # Music added under one artist is picked up on the next scan, and the other
    # artists (whose mtime did not change) are not re-listed. This is what keeps
    # the hourly sweep / Rescan cheap while still catching new albums.
    lib = _mk(tmp_path, "lib", [])
    a1 = os.path.join(lib, "Artist1")
    a2 = os.path.join(lib, "Artist2")
    a3 = os.path.join(lib, "Artist3")
    tags = {}
    for art in ("Artist1", "Artist2", "Artist3"):
        for alb in ("One", "Two"):
            d = _mk(tmp_path, f"lib/{art}/{alb}", ["1.flac"])
            tags[d] = {"album": alb, "artist": art, "date": "2000"}
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 6

    # Add a new album under Artist1 and bump Artist1's mtime so the change is
    # unambiguous regardless of filesystem timestamp granularity.
    new = _mk(tmp_path, "lib/Artist1/Three", ["1.flac", "2.flac"])
    tags[new] = {"album": "Three", "artist": "Artist1", "date": "2001"}
    future = time.time() + 1000
    os.utime(a1, (future, future))

    spy = _scandir_spy(monkeypatch)
    assert idx.refresh(lib) == 7  # the new album was discovered
    got = {a["title"]: a for a in idx.iter_albums()}
    assert got["Three"]["tracks"] == 2 and got["Three"]["artist"] == "Artist1"
    # Artist1 (changed) and its new album were listed; the unchanged siblings were not.
    assert os.path.abspath(a1) in spy
    assert os.path.abspath(new) in spy
    assert os.path.abspath(a2) not in spy
    assert os.path.abspath(a3) not in spy


def test_interrupted_walk_resumes_and_completes(tmp_path):
    # A scan killed mid-walk (should_continue flips false after a few folders)
    # leaves a checkpoint: some folders fully listed, some a recorded frontier.
    # The next scan resumes from there and finishes with the whole library, not a
    # blank cache or a lost frontier.
    import sqlite3

    lib = _mk(tmp_path, "lib", [])
    tags = {}
    for art in range(4):
        for alb in range(5):
            d = _mk(tmp_path, f"lib/Artist{art}/Album{art}{alb}", ["1.flac"])
            tags[d] = {"album": f"Album{art}{alb}", "artist": f"Artist{art}", "date": "2000"}
    db = str(tmp_path / "library.sqlite3")
    idx = LibraryIndex(db, read_tags=_reader(tags))

    # Bail after the root and the first couple of artists are processed, so album
    # folders are known-but-unlisted (the resume frontier). The root is always
    # processed first, then artists, before any album is reached.
    calls = {"n": 0}

    def dying():
        calls["n"] += 1
        return calls["n"] <= 3  # allow 3 folders, then supersede

    assert idx.refresh(lib, should_continue=dying) == 0  # walk bailed before any read

    # The checkpoint is on disk: some folders listed, at least one frontier row.
    probe = sqlite3.connect(db)
    listed = probe.execute("SELECT COUNT(*) FROM dirs WHERE listed = 1").fetchone()[0]
    frontier = probe.execute("SELECT COUNT(*) FROM dirs WHERE listed = 0").fetchone()[0]
    probe.close()
    assert listed >= 1 and frontier >= 1

    # Resume: a normal scan finishes the whole library.
    assert idx.refresh(lib) == 20
    assert sorted(a["title"] for a in idx.iter_albums()) == sorted(tags[d]["album"] for d in tags)

    # The frontier is fully consumed; nothing is left half-listed.
    probe = sqlite3.connect(db)
    assert probe.execute("SELECT COUNT(*) FROM dirs WHERE listed = 0").fetchone()[0] == 0
    probe.close()


def test_transient_listing_failure_preserves_subtree(tmp_path, monkeypatch):
    # A one-time NAS/SMB listing hiccup on a folder that needed re-listing must
    # NOT be mistaken for an empty folder: doing so would orphan its albums and
    # the generation prune would delete them permanently. The subtree must survive
    # the failure and recover cleanly once the error clears.
    import waves.library_index as li

    lib = _mk(tmp_path, "lib", [])
    artist = os.path.join(lib, "Artist")
    d1 = _mk(tmp_path, "lib/Artist/Alb1", ["1.flac"])
    d2 = _mk(tmp_path, "lib/Artist/Alb2", ["1.flac"])
    tags = {
        d1: {"album": "Alb1", "artist": "Artist", "date": "1"},
        d2: {"album": "Alb2", "artist": "Artist", "date": "2"},
    }
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 2

    # Change Artist so it will be re-listed, then make that listing fail once.
    future = time.time() + 1000
    os.utime(artist, (future, future))
    real = os.scandir

    def flaky(path=".", *a, **k):
        if os.path.abspath(path) == os.path.abspath(artist):
            raise OSError("transient NAS timeout")
        return real(path, *a, **k)

    monkeypatch.setattr(li.os, "scandir", flaky)
    assert idx.refresh(lib) == 2  # albums preserved, NOT pruned by the failure
    assert sorted(a["title"] for a in idx.iter_albums()) == ["Alb1", "Alb2"]

    # Error clears: a healthy scan re-lists Artist and both albums remain.
    monkeypatch.setattr(li.os, "scandir", real)
    assert idx.refresh(lib) == 2
    assert sorted(a["title"] for a in idx.iter_albums()) == ["Alb1", "Alb2"]


def test_root_going_offline_midwalk_does_not_wipe_cache(tmp_path, monkeypatch):
    # If the root volume drops AFTER the readability probe passed but BEFORE the
    # walk stats it, the scan must leave the cache intact and report missing, not
    # let the generation prune wipe every badge (the offline-NAS invariant).
    import waves.library_index as li

    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "2000"}})
    assert idx.refresh(lib) == 1

    real_stat = os.stat
    real_scandir = os.scandir
    state = {"probed": False}

    def spy_scandir(path=".", *a, **k):
        if os.path.abspath(path) == os.path.abspath(lib):
            state["probed"] = True  # the readability probe has run
        return real_scandir(path, *a, **k)

    def flaky_stat(path, *a, **k):
        # Fail only the walk's stat of the root, after the probe already passed.
        if state["probed"] and os.path.abspath(path) == os.path.abspath(lib):
            raise OSError("mount dropped mid-walk")
        return real_stat(path, *a, **k)

    monkeypatch.setattr(li.os, "scandir", spy_scandir)
    monkeypatch.setattr(li.os, "stat", flaky_stat)
    assert idx.refresh(lib) == 1  # cache intact
    assert idx.last_scan_status == SCAN_MISSING
    assert len(list(idx.iter_albums())) == 1


def test_non_utf8_folder_name_is_skipped_not_crash(tmp_path, monkeypatch):
    # A folder whose name is not valid UTF-8 cannot be stored in sqlite; it must be
    # skipped at discovery, never crash (and re-crash) the whole scan. macOS will
    # not create such a name, so inject a fake directory entry into the listing.
    import waves.library_index as li

    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "2000"}})
    real = os.scandir
    a_dir = os.path.join(lib, "A")

    class _FakeEntry:
        def __init__(self, name, path):
            self.name = name
            self.path = path

        def is_dir(self, follow_symlinks=True):
            return True

    class _CM:
        def __init__(self, entries):
            self._entries = entries

        def __enter__(self):
            return iter(self._entries)

        def __exit__(self, *a):
            return False

    def inject(path=".", *a, **k):
        if os.path.abspath(path) == os.path.abspath(a_dir):
            with real(path, *a, **k) as it:
                entries = list(it)
            # A lone surrogate: str, but raises UnicodeEncodeError on utf-8 encode.
            entries.append(_FakeEntry("bad\udcff", os.path.join(path, "bad\udcff")))
            return _CM(entries)
        return real(path, *a, **k)

    monkeypatch.setattr(li.os, "scandir", inject)
    assert idx.refresh(lib) == 1  # the bad entry is skipped; the real album indexes
    assert next(idx.iter_albums())["title"] == "Album"


def test_force_full_relists_when_mtime_did_not_change(tmp_path):
    # The manual Rescan (force_full) re-lists every folder even when a parent's
    # mtime did not move, so a moved-in album is found on a network mount that
    # fails to bump the parent mtime, where the incremental sweep would miss it.
    lib = _mk(tmp_path, "lib", [])
    a = os.path.join(lib, "Artist")
    d1 = _mk(tmp_path, "lib/Artist/One", ["1.flac"])
    tags = {d1: {"album": "One", "artist": "Artist", "date": "1"}}
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 1
    stored = os.stat(a).st_mtime

    # Add an album but pin Artist's mtime back (simulating an unreliable mount).
    d2 = _mk(tmp_path, "lib/Artist/Two", ["1.flac", "2.flac"])
    tags[d2] = {"album": "Two", "artist": "Artist", "date": "2"}
    os.utime(a, (stored, stored))

    assert idx.refresh(lib) == 1  # incremental sweep cannot see the addition
    assert idx.refresh(lib, force_full=True) == 2  # a full Rescan finds it
    assert sorted(x["title"] for x in idx.iter_albums()) == ["One", "Two"]


def test_never_scanned_is_always_due_for_a_full_scan(tmp_path):
    # Before any scan completes there is no baseline, so the launch check must fall
    # back to a full re-list (which lists everything anyway on a cold library).
    idx = _index(tmp_path, {})
    assert idx.seconds_since_full_scan() is None
    assert idx.due_for_full_scan(60 * 60) is True


def test_first_scan_stamps_the_full_scan_baseline(tmp_path):
    # The first successful scan lists the whole tree, so it stamps the baseline:
    # a fresh install is not forced into a redundant full sweep on its next launch.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/One", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "One", "artist": "A", "date": "1"}})
    assert idx.refresh(lib) == 1  # a plain incremental call, force_full defaults False
    age = idx.seconds_since_full_scan()
    assert age is not None and age < 60
    assert idx.due_for_full_scan(60 * 60) is False  # fresh baseline, not yet due


def test_launch_falls_back_to_full_scan_once_the_baseline_ages(tmp_path):
    # A change an unreliable mount hides from mtimes is only caught by a full
    # re-list; ageing the baseline past the threshold is what makes the launch
    # check ask for one.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/One", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "One", "artist": "A", "date": "1"}})
    idx.refresh(lib)
    idx._meta_set("last_full_scan", str(time.time() - 10_000))  # pretend it is stale
    idx._conn.commit()
    assert idx.due_for_full_scan(3_600) is True
    assert idx.due_for_full_scan(20_000) is False  # not yet past a longer threshold


def test_force_full_refreshes_the_staleness_clock(tmp_path):
    # Running a full sweep (the deep sweep or the launch fallback) resets the clock
    # so the next fallback is another whole interval away, not immediate.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/One", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "One", "artist": "A", "date": "1"}})
    idx.refresh(lib)
    idx._meta_set("last_full_scan", str(time.time() - 10_000))
    idx._conn.commit()
    assert idx.due_for_full_scan(3_600) is True
    idx.refresh(lib, force_full=True)  # the fallback runs
    assert idx.due_for_full_scan(3_600) is False  # clock reset by the full sweep


def test_poll_containers_changed_detects_structural_changes(tmp_path):
    # The cheap background poll stats only container folders and reports whether a
    # rescan is warranted, so the watcher can skip the full walk when nothing
    # structural changed. This is the network-safe trigger (no fs events needed).
    lib = _mk(tmp_path, "lib", [])
    a1 = os.path.join(lib, "Artist1")
    d1 = _mk(tmp_path, "lib/Artist1/One", ["1.flac"])
    tags = {d1: {"album": "One", "artist": "Artist1", "date": "1"}}
    idx = _index(tmp_path, tags)

    # Nothing scanned yet -> None (a normal cold refresh will handle it).
    assert idx.poll_containers_changed(lib) is None
    assert idx.refresh(lib) == 1
    # Freshly scanned, nothing touched -> no rescan needed.
    assert idx.poll_containers_changed(lib) is False

    # An album moved in under an existing artist bumps that artist's mtime.
    d2 = _mk(tmp_path, "lib/Artist1/Two", ["1.flac"])
    tags[d2] = {"album": "Two", "artist": "Artist1", "date": "2"}
    future = time.time() + 1000
    os.utime(a1, (future, future))
    assert idx.poll_containers_changed(lib) is True

    # After the rescan indexes it, the poll is quiet again.
    assert idx.refresh(lib) == 2
    assert idx.poll_containers_changed(lib) is False

    # A brand-new artist at the root bumps the root's mtime.
    d3 = _mk(tmp_path, "lib/Artist2/Solo", ["1.flac"])
    tags[d3] = {"album": "Solo", "artist": "Artist2", "date": "3"}
    os.utime(lib, (future, future))
    assert idx.poll_containers_changed(lib) is True


def test_poll_containers_ignores_leaf_track_changes(tmp_path):
    # A track added INSIDE an existing album bumps only the album leaf's mtime, not
    # any container, so the cheap container poll deliberately does NOT fire on it
    # (that case is caught by the periodic full sweep). Documents the boundary.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/Artist/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "Artist", "date": "1"}})
    assert idx.refresh(lib) == 1
    assert idx.poll_containers_changed(lib) is False
    # Add a track to the existing album (bumps the album leaf mtime only).
    open(os.path.join(d, "2.flac"), "w").close()
    assert idx.poll_containers_changed(lib) is False  # container poll stays quiet


def test_poll_containers_returns_none_when_root_offline(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "1"}})
    assert idx.refresh(lib) == 1
    # An offline/missing root must not fan out stats; report None (unknown).
    assert idx.poll_containers_changed(str(tmp_path / "gone")) is None


# --- transient tag-read failures never poison an album (H3) ------------------


def test_transient_read_failure_retries_on_the_next_scan(tmp_path):
    # A momentary open/read hiccup (routine on a cold NAS under scan load) must
    # not persist an empty-identity row that no later scan re-reads; with no row
    # written, the very next scan tries the file again and indexes it.
    lib = _mk(tmp_path, "lib", [])
    _mk(tmp_path, "lib/A/Alb", ["1.flac"])
    calls = itertools.count()

    def flaky(path):
        if next(calls) == 0:
            raise OSError("NAS hiccup")
        return {"album": "Alb", "artist": "A", "date": "2000"}

    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=flaky)
    assert idx.refresh(lib) == 0  # failed read: not indexed, but not poisoned
    assert idx.refresh(lib) == 1  # retried and healed, no force_full needed
    got = list(idx.iter_albums())
    assert got[0]["title"] == "Alb" and got[0]["artist"] == "A"


def test_unparseable_file_keeps_retrying_without_a_row(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    _mk(tmp_path, "lib/A/Alb", ["1.flac"])
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=lambda p: None)
    assert idx.refresh(lib) == 0
    assert idx.refresh(lib) == 0
    assert list(idx.iter_albums()) == []


def test_tagless_but_readable_file_persists_and_stops_rereading(tmp_path):
    # A real file with no tags still reports its codec from the stream; that row
    # is honest (it will just never match a TIDAL album) and must NOT be re-read
    # on every incremental scan.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/Unknown/Untagged", ["1.flac"])
    reads = []
    idx = _index(tmp_path, {d: {"album": "", "artist": "", "date": "", "codec": "flac"}}, counter=reads)
    assert idx.refresh(lib) == 1
    idx.refresh(lib)  # unchanged folder: the incremental scan skips the re-read
    assert len(reads) == 1
    got = list(idx.iter_albums())
    assert got[0]["codec"] == "flac" and got[0]["title"] == ""


# --- force_full re-lists everything but still reads only changes -------------


def test_force_full_does_not_reread_an_unchanged_album(tmp_path):
    # Rescan re-LISTS every folder; it does not re-READ an album that compares
    # unchanged. Re-reading them cost a whole-library tag read (thousands of
    # files over a network mount) to find what the listing had already found.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Alb", ["1.flac"])
    tags = {d: {"album": "Alb", "artist": "A", "date": "2000"}}
    reads = []
    idx = _index(tmp_path, tags, counter=reads)
    idx.refresh(lib)
    reads.clear()
    idx.refresh(lib, force_full=True)
    assert reads == []  # re-listed, but nothing was worth re-reading


def test_force_full_still_reads_an_album_whose_track_count_changed(tmp_path):
    # The listing carries the file count, so a track added or removed in place
    # is caught by the cheap comparison, with no mtime involved: the folder's
    # mtime is pinned back to its indexed value after the file lands, exactly
    # the mount that never bumps mtimes force_full exists to cover. (An
    # earlier version of this test let _mk bump the real mtime, so it passed
    # through the mtime check and pinned nothing.)
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Alb", ["1.flac"])
    tags = {d: {"album": "Alb", "artist": "A", "date": "2000"}}
    idx = _index(tmp_path, tags)
    idx.refresh(lib)
    assert next(iter(idx.iter_albums()))["tracks"] == 1
    stat = os.stat(d)
    _mk(tmp_path, "lib/A/Alb", ["1.flac", "2.flac"])
    os.utime(d, (stat.st_atime, stat.st_mtime))
    idx.refresh(lib, force_full=True)
    assert next(iter(idx.iter_albums()))["tracks"] == 2


def test_force_full_still_retries_an_album_that_never_indexed(tmp_path):
    # A transient read failure leaves no row at all, and a row that is absent is
    # never "unchanged", so Rescan still picks it up without re-reading the rest.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Alb", ["1.flac"])
    tags = {}
    idx = _index(tmp_path, tags)
    idx.refresh(lib)
    assert list(idx.iter_albums()) == []
    tags[d] = {"album": "Alb", "artist": "A", "date": "2000"}
    idx.refresh(lib, force_full=True)
    assert next(iter(idx.iter_albums()))["title"] == "Alb"


# --- The scan must never claim more than it can prove ------------------------


def _per_file_reader(filemap):
    """A tag reader keyed by the file's own name, so one folder can hold files
    that disagree about which album they belong to."""

    def read(path):
        return filemap.get(os.path.basename(path))

    return read


def test_apple_double_sidecars_do_not_blind_the_scan(tmp_path):
    # macOS writes "._Track.flac" beside every file on filesystems without
    # native extended attributes (exFAT, most SMB shares). They carry an audio
    # extension and sort FIRST, so they were picked as the folder's
    # representative file; mutagen cannot parse one, so the whole library
    # indexed as zero albums and re-read every folder on every scan forever.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["01.flac", "._01.flac", "02.flac", "._02.flac"])
    calls: list[str] = []
    idx = LibraryIndex(
        str(tmp_path / "library.sqlite3"),
        read_tags=_reader({d: {"album": "Album", "artist": "A", "date": "2000"}}, calls),
    )
    assert idx.refresh(lib) == 1
    row = next(idx.iter_albums())
    assert row["title"] == "Album"
    assert row["tracks"] == 2  # the sidecars are not tracks
    assert not any(os.path.basename(p).startswith("._") for p in calls)  # never even opened
    # And the folder is now genuinely cached: a second scan re-reads nothing.
    before = len(calls)
    idx.refresh(lib)
    assert len(calls) == before


def test_folder_of_unrelated_tracks_cannot_claim_a_whole_album(tmp_path):
    # A "Singles"/"Inbox" dump takes its identity from its first file but used to
    # take its track count from the whole folder, so 30 unrelated songs whose
    # first file was tagged "Discovery" indexed as a 30-track Discovery and
    # satisfied every completeness test. A count of 0 is how the matcher is told
    # this folder cannot answer "do I have all of it?".
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 13)]
    _mk(tmp_path, "lib/Daft Punk/Singles", names)
    filemap = {n: {"album": f"Unrelated {n}", "artist": "Daft Punk", "date": "2001"} for n in names}
    filemap["01.flac"] = {"album": "Discovery", "artist": "Daft Punk", "date": "2001"}
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_per_file_reader(filemap))
    assert idx.refresh(lib) == 1
    row = next(idx.iter_albums())
    assert row["title"] == "Discovery"  # identity still comes from the first file
    assert row["tracks"] == 0  # but the count is refused


def test_one_album_survives_sloppy_tagging(tmp_path):
    # The cross-check must not split a real album: only the ALBUM tag is
    # compared, case- and whitespace-insensitively, so a featured-guest artist
    # credit or a stray capital does not cost the user their badge.
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 13)]
    _mk(tmp_path, "lib/A/Real Album", names)
    filemap = {n: {"album": "Real Album", "artist": "A", "date": "2020"} for n in names}
    filemap["06.flac"] = {"album": "Real Album", "artist": "A feat. Guest", "date": "2020"}
    filemap["12.flac"] = {"album": "  REAL ALBUM ", "artist": "A", "date": "2020"}
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_per_file_reader(filemap))
    idx.refresh(lib)
    assert next(idx.iter_albums())["tracks"] == 12


def test_unreadable_cross_check_file_never_costs_the_track_count(tmp_path):
    # A sample that will not open is not evidence of a second album: a transient
    # read failure must never silently zero a real album's count.
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 11)]
    _mk(tmp_path, "lib/A/Album", names)
    filemap = {n: {"album": "Album", "artist": "A", "date": "2020"} for n in names}
    filemap["05.flac"] = None  # unreadable
    filemap["10.flac"] = None
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_per_file_reader(filemap))
    idx.refresh(lib)
    assert next(idx.iter_albums())["tracks"] == 10


# --- The release's own declared shape ----------------------------------------
# Counting files says what the folder HOLDS. The tracktotal and discnumber on
# those files say what the release CONTAINS, which is a different number and the
# only one that can tell a complete copy of a smaller edition from a copy short
# a track. Believed only when the folder speaks with one voice.


def test_numbered_reads_both_tag_shapes():
    # FLAC keeps the halves apart (tracknumber + tracktotal), ID3 and MP4 pack
    # them into one field. A library holds both, and they must read the same.
    assert _numbered("3/12") == (3, 12)
    assert _numbered("12") == (12, 0)
    assert _numbered(" 3 / 12 ") == (3, 12)
    # Nothing here may raise on the junk a real library carries.
    assert _numbered("") == (0, 0)
    assert _numbered(None) == (0, 0)
    assert _numbered("A/B") == (0, 0)
    assert _numbered("-2/12") == (0, 12)


def _shape(album, **extra):
    return {"album": album, "artist": "A", "date": "2020", **extra}


def test_the_folder_records_the_shape_its_files_agree_on(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 10)]
    _mk(tmp_path, "lib/A/Album", names)
    filemap = {n: _shape("Album", track_total=12, disc_no=2, disc_total=3) for n in names}
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_per_file_reader(filemap))
    idx.refresh(lib)
    row = next(idx.iter_albums())
    assert row["tracks"] == 9  # what the folder holds
    assert row["declared"] == 12  # what the release says it has
    assert (row["disc_no"], row["disc_total"]) == (2, 3)


def test_a_silent_file_is_not_a_file_that_disagrees(tmp_path):
    # Only positive evidence rejects, the same rule the album cross-check runs
    # on. A ripper that tagged nine files and forgot the tenth must not cost the
    # folder a claim the nine of them make unanimously.
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 11)]
    _mk(tmp_path, "lib/A/Album", names)
    filemap = {n: _shape("Album", track_total=10) for n in names}
    filemap["07.flac"] = _shape("Album")  # no claim at all
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_per_file_reader(filemap))
    idx.refresh(lib)
    assert next(idx.iter_albums())["declared"] == 10


def test_files_that_disagree_leave_the_folder_with_no_claim(tmp_path):
    # Two different answers mean the folder cannot be asked, and a wrong
    # declared count is worse than none: it is the number a completeness claim
    # would be measured against.
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 11)]
    _mk(tmp_path, "lib/A/Album", names)
    filemap = {n: _shape("Album", track_total=10) for n in names}
    filemap["07.flac"] = _shape("Album", track_total=14)
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_per_file_reader(filemap))
    idx.refresh(lib)
    assert next(idx.iter_albums())["declared"] == 0


def test_a_whole_set_sitting_flat_is_not_one_disc_of_itself(tmp_path):
    # Files declaring disc 1 and disc 2 in ONE folder are not a folder in
    # conflict, they are a set nobody split up. Recording either number would
    # offer it to the disc join as a half, and its own file count already
    # covers the record.
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 19)]
    _mk(tmp_path, "lib/A/Album", names)
    filemap = {n: _shape("Album", disc_no=1 if i < 9 else 2, disc_total=2) for i, n in enumerate(names)}
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_per_file_reader(filemap))
    idx.refresh(lib)
    row = next(idx.iter_albums())
    assert row["tracks"] == 18 and row["disc_no"] == 0 and row["disc_total"] == 2


def test_a_cache_from_before_the_shape_was_read_backfills_itself(tmp_path):
    # The one-time migration: an existing cache has no declared column, and its
    # rows must re-read once rather than stay silent forever behind an unchanged
    # mtime.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["01.flac", "02.flac"])
    tags = {d: _shape("Album", track_total=2)}
    db = str(tmp_path / "library.sqlite3")
    reads: list = []
    idx = LibraryIndex(db, read_tags=_reader(tags, reads))
    idx.refresh(lib)
    assert next(idx.iter_albums())["declared"] == 2
    idx.close()

    import sqlite3

    conn = sqlite3.connect(db)  # rewind the row to its pre-migration state
    conn.execute("UPDATE albums SET declared = NULL, disc_no = NULL, disc_total = NULL")
    conn.commit()
    conn.close()

    reads.clear()
    aged = LibraryIndex(db, read_tags=_reader(tags, reads))
    aged.refresh(lib)
    assert reads, "an unread row must not stay unread behind an unchanged mtime"
    assert next(aged.iter_albums())["declared"] == 2


# --- An empty result is not proof of an empty library ------------------------


def test_root_that_walks_to_nothing_keeps_the_cache_the_first_time(tmp_path):
    # A share that unmounts often leaves its mountpoint behind as an EMPTY local
    # directory: it probes perfectly readable, walks to nothing, and the prune
    # used to take the whole index while reporting a successful scan.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "2000"}})
    assert idx.refresh(lib) == 1
    os.remove(os.path.join(d, "1.flac"))
    os.rmdir(d)
    os.rmdir(os.path.join(lib, "A"))
    assert idx.refresh(lib) == 1  # cache held
    assert idx.last_scan_status == SCAN_MISSING  # and reported honestly


def _age_empty_strike(idx):
    """Rewind the recorded empty-walk strike past the wall-clock gap, so the
    next empty scan counts as the SECOND strike instead of an uncounted echo
    of the first (see _EMPTY_STRIKE_GAP_S)."""
    with idx._lock:
        idx._meta_set("empty_walk_at", str(time.time() - _EMPTY_STRIKE_GAP_S - 1))
        idx._conn.commit()


def test_a_second_empty_scan_is_believed_and_prunes(tmp_path):
    # The guard above is a delay, not a refusal: a library the user really did
    # empty must still fall out, or the badges would be wrong forever.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "2000"}})
    idx.refresh(lib)
    os.remove(os.path.join(d, "1.flac"))
    os.rmdir(d)
    os.rmdir(os.path.join(lib, "A"))
    idx.refresh(lib)
    _age_empty_strike(idx)
    assert idx.refresh(lib) == 0
    assert idx.last_scan_status == SCAN_OK


def test_empty_scans_inside_the_strike_gap_never_prune(tmp_path):
    # The container poll manufactures a scan every five minutes, so counting
    # scans made "two consecutive empty scans" ten minutes of downtime. Any
    # number of empty walks inside the wall-clock gap stays one strike.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "2000"}})
    idx.refresh(lib)
    os.remove(os.path.join(d, "1.flac"))
    os.rmdir(d)
    os.rmdir(os.path.join(lib, "A"))
    for _ in range(4):
        assert idx.refresh(lib) == 1  # cache held every time
        assert idx.last_scan_status == SCAN_MISSING


def test_an_empty_walk_on_another_device_never_prunes(tmp_path):
    # A share that unmounts leaves its mountpoint behind as an empty local
    # directory ON THE PARENT VOLUME: readable, walkable, and holding nothing.
    # The albums were recorded with their filesystem's device id, so the
    # mismatch identifies the ghost however long the outage lasts and however
    # far apart the empty walks land.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "2000"}})
    assert idx.refresh(lib) == 1
    os.remove(os.path.join(d, "1.flac"))
    os.rmdir(d)
    os.rmdir(os.path.join(lib, "A"))
    real_dev = os.stat(lib).st_dev
    idx._root_dev = lambda root: real_dev + 1  # the ghost sits on another volume
    for _ in range(3):
        _age_empty_strike(idx)  # even aged strikes must not be believed
        assert idx.refresh(lib) == 1
        assert idx.last_scan_status == SCAN_MISSING
    # Back on the recorded device, an emptied-in-place library is still
    # believed once its strikes spread out: the guard is aimed, not absolute.
    idx._root_dev = lambda root: real_dev
    idx.refresh(lib)
    _age_empty_strike(idx)
    assert idx.refresh(lib) == 0


def test_a_recovered_mount_resets_the_empty_streak(tmp_path):
    # Blip, recovery, blip must be treated as two FIRST empties, not a second
    # one: otherwise one unlucky pair of blips wipes a healthy library.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    tags = {d: {"album": "Album", "artist": "A", "date": "2000"}}
    idx = _index(tmp_path, tags)
    idx.refresh(lib)
    away = str(tmp_path / "away")
    os.rename(os.path.join(lib, "A"), away)
    assert idx.refresh(lib) == 1  # blip 1: held
    os.rename(away, os.path.join(lib, "A"))
    assert idx.refresh(lib) == 1  # recovered
    assert idx.last_scan_status == SCAN_OK
    os.rename(os.path.join(lib, "A"), away)
    assert idx.refresh(lib) == 1  # blip 2 is a FIRST empty again: still held
    assert idx.last_scan_status == SCAN_MISSING


def test_deleting_one_album_still_prunes_that_album(tmp_path):
    # The empty guard must not stop ordinary pruning, including on a warm scan
    # where the surviving albums are unchanged and so are never re-listed.
    lib = _mk(tmp_path, "lib", [])
    d1 = _mk(tmp_path, "lib/A/One", ["1.flac"])
    d2 = _mk(tmp_path, "lib/A/Two", ["1.flac"])
    tags = {d1: {"album": "One", "artist": "A", "date": "1"}, d2: {"album": "Two", "artist": "A", "date": "2"}}
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 2
    os.remove(os.path.join(d2, "1.flac"))
    os.rmdir(d2)
    assert idx.refresh(lib) == 1
    assert idx.last_scan_status == SCAN_OK
    assert next(idx.iter_albums())["title"] == "One"


# --- The library is READ. Never written, never touched -----------------------


def _snapshot(root):
    """Every path under root with its size, mtime and content hash."""
    import hashlib

    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in list(dirnames) + list(filenames):
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            if os.path.isdir(p):
                out[rel] = ("dir", None, None)
                continue
            st = os.stat(p)
            with open(p, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            out[rel] = ("file", st.st_size, digest, st.st_mtime_ns)
    return out


def _library_tree(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d1 = _mk(tmp_path, "lib/A/One", ["01.flac", "02.flac", "03.flac", "cover.jpg", "album.nfo", "playlist.m3u"])
    d2 = _mk(tmp_path, "lib/B/Two", ["a.mp3", "b.mp3", "scan.log", "notes.txt"])
    _mk(tmp_path, "lib/B/Empty", [])
    for path, blob in (
        (os.path.join(d1, "cover.jpg"), b"\xff\xd8\xff\xe0 not really a jpeg"),
        (os.path.join(d1, "album.nfo"), b"release notes"),
        (os.path.join(d2, "scan.log"), b"rip log"),
    ):
        with open(path, "wb") as fh:
            fh.write(blob)
    return lib, d1, d2


def test_a_scan_never_writes_creates_or_deletes_anything_in_the_library(tmp_path):
    """THE data-safety invariant of the whole feature.

    Waves reads the user's music library and writes only its own sqlite cache.
    Nothing in a scan may create, modify, rename or delete a single byte under
    the library root, including the non-audio files (cover art, rip logs, .nfo,
    playlists) that a cleanup-minded scanner might think it owns. User libraries
    are irreplaceable, so this is pinned rather than trusted.

    Run twice on purpose: once through the REAL tag reader, so mutagen's own
    file handling is exercised (it must never open for writing and never save),
    and once through an injected reader that succeeds, so the scan machinery is
    exercised on the path where rows are actually produced.
    """
    lib, _d1, _d2 = _library_tree(tmp_path)
    before = _snapshot(lib)
    assert before, "the fixture tree is empty, the assertion below would be vacuous"

    # 1. The real reader: mutagen genuinely opens each representative file.
    real = LibraryIndex(str(tmp_path / "real.sqlite3"), read_tags=_read_album_tags)
    real.refresh(lib)
    real.refresh(lib, force_full=True)
    real.close()
    assert _snapshot(lib) == before

    # 2. A reader that succeeds, so every album produces a row and the full
    #    upsert/prune path runs against a library that is present the whole time.
    tags = {
        os.path.join(lib, "A", "One"): {"album": "One", "artist": "A", "date": "2001"},
        os.path.join(lib, "B", "Two"): {"album": "Two", "artist": "B", "date": "2002"},
    }
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 2
    idx.refresh(lib, force_full=True)
    idx.close()
    assert _snapshot(lib) == before


def test_the_scan_pools_report_their_saturation(tmp_path):
    """Each of the scanner's three executors is visible to the perf sampler.

    The project's diagnostics convention: every new thread pool registers with
    diagnostics so a verbose report shows its saturation. The scanner's pools
    cannot be handed over directly (ThreadPoolExecutor has no
    activeThreadCount, and they are built and torn down per scan), so what is
    registered is a stable in-flight gauge per pool. A gauge that never moved
    would satisfy the letter of the rule and report nothing, so this runs a real
    scan and requires each of the three to have counted concurrent work and to
    have come back to zero.

    Read from each gauge's own high-water mark rather than sampled from a
    watcher thread. The first version of this test polled activeThreadCount
    every 0.5ms and passed on its own but failed under full-suite load: the
    scanner's pools drain fast enough that a sampler competing with the rest of
    the suite for a core can miss every concurrent moment. A high-water mark
    updated inside the counter cannot be sampled past.
    """
    lib = str(tmp_path / "lib")
    tags = {}
    for i in range(60):
        d = _mk(lib, f"Artist {i}/Album {i}", ["1.flac", "2.flac", "3.flac", "4.flac"])
        tags[d] = {"album": f"Album {i}", "artist": f"Artist {i}", "date": "2020"}

    def slow_tags(path):
        time.sleep(0.002)  # stand in for a real tag read's IO latency
        return tags.get(os.path.dirname(path))

    before = {name: g.peak for name, g in (("walk", WALK_GAUGE), ("read", READ_GAUGE), ("poll", POLL_GAUGE))}
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=slow_tags)
    assert idx.refresh(lib) == 60
    idx.poll_containers_changed(lib)
    idx.close()

    for name, gauge in (("walk", WALK_GAUGE), ("read", READ_GAUGE), ("poll", POLL_GAUGE)):
        assert gauge.peak > 1, f"the {name} pool never counted concurrent work (peak {gauge.peak})"
        assert gauge.peak >= before[name]  # a high-water mark only ever rises
        assert gauge.peak <= gauge.maxThreadCount(), f"the {name} pool ran wider than it is allowed to"
        assert gauge.activeThreadCount() == 0, f"the {name} gauge leaked a count after the scan"
        assert gauge.maxThreadCount() > 0


def test_the_scan_pools_are_actually_registered():
    """The gauges exist and move (above), and the bridge hands all three to
    diagnostics at startup. Without this the rule is only half kept: a gauge
    nobody registered reports to nobody."""
    backend = (Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "backend.py").read_text(encoding="utf-8")
    for name in ("libwalk", "libread", "libpoll"):
        assert f'diagnostics.register_pool("{name}"' in backend, f"the {name} pool is not registered"


# --- One cache file per root (a root change never destroys a scan) ------------


def test_each_root_gets_its_own_cache_file(tmp_path):
    cfg = str(tmp_path / "cfg")
    os.makedirs(cfg)
    a = cache_file_for_root(cfg, str(tmp_path / "libA"))
    b = cache_file_for_root(cfg, str(tmp_path / "libB"))
    assert a != b
    assert os.path.dirname(a) == cfg and os.path.dirname(b) == cfg
    # The user's folder path must not be readable from the filename.
    assert "libA" not in os.path.basename(a)


def test_respellings_of_one_root_share_one_cache_file(tmp_path):
    # APFS and NTFS compare case-insensitively, so two case spellings of one
    # folder are one folder and must map to one cache; a trailing separator is
    # likewise spelling, not identity. Splitting them is a silent cold rescan.
    cfg = str(tmp_path / "cfg")
    os.makedirs(cfg)
    root = str(tmp_path / "Music")
    assert cache_file_for_root(cfg, root) == cache_file_for_root(cfg, root + os.sep)
    assert cache_file_for_root(cfg, root) == cache_file_for_root(cfg, root.lower())


def test_a_respelled_root_still_adopts_the_legacy_cache(tmp_path):
    # The filename hashes the folded key, but adoption compared raw spellings:
    # opening the same folder under a new spelling (a casing change, a file
    # dialog handing back NFD) created an EMPTY per-root file beside the full
    # legacy cache, and because adoption only fires while the per-root file
    # does not exist, even the original spelling could never adopt afterwards.
    # The whole scan was stranded, permanently.
    cfg = str(tmp_path / "cfg")
    os.makedirs(cfg)
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Alb", ["1.flac"])
    legacy = LibraryIndex(
        os.path.join(cfg, "library.sqlite3"),
        read_tags=_reader({d: {"album": "Alb", "artist": "A", "date": "2000"}}),
    )
    assert legacy.refresh(lib) == 1
    legacy.close()
    respelled = lib.upper() if lib != lib.upper() else lib.lower()
    adopted = LibraryIndex(cache_file_for_root(cfg, respelled), read_tags=lambda p: None)
    assert not os.path.exists(os.path.join(cfg, "library.sqlite3")), "adoption renames, never copies"
    assert adopted.matches_scan_root(respelled), "the adopted cache must seed badges for the respelled root"
    assert adopted.matches_scan_root(lib)
    assert len(list(adopted.iter_albums())) == 1, "the legacy scan must arrive intact"


def test_a_respelled_root_keeps_its_walk_warm_and_never_doubles(tmp_path):
    # Keeping the cache FILE was only half the promise: the stored rows still
    # carried the old spelling as their path prefix, so the respelled walk
    # missed every known row (a full cold re-read) and, until the prune caught
    # up, iter_albums served BOTH spellings of every album, doubling the
    # artist rollup for up to an hour. _begin_scan now rewrites the prefix.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Alb", ["1.flac"])
    tags = {d: {"album": "Alb", "artist": "A", "date": "2000"}}
    reads: list = []
    idx = _index(tmp_path, tags, counter=reads)
    assert idx.refresh(lib) == 1
    reads.clear()

    respelled = lib.upper() if lib != lib.upper() else lib.lower()
    # The fake reader is keyed by the folder's stored spelling; answer the
    # respelled path too, so a wrongly-cold re-read is visible, not an error.
    tags[d.replace(lib, respelled, 1)] = tags[d]
    assert idx.refresh(respelled) == 1, "one album, one spelling: never both"
    assert reads == [], "a respelling must not cost the warm walk a re-read"
    paths = [a["id"] for a in idx.iter_albums()]
    assert len(paths) == 1 and paths[0].startswith(respelled), "rows must carry the new spelling"


def test_adoption_strands_no_sidecars_and_loses_no_commits(tmp_path):
    # A -wal/-shm pair left under the legacy name would silently drop any
    # commits still living in it. Adoption's probe connection checkpoints the
    # wal into the database (sqlite folds and removes an outstanding wal when
    # the last connection closes), and whatever survives the probe moves with
    # the rename, so nothing may remain under the legacy name and every
    # committed album must arrive in the adopted file.
    cfg = str(tmp_path / "cfg")
    os.makedirs(cfg)
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Alb", ["1.flac"])
    legacy_path = os.path.join(cfg, "library.sqlite3")
    legacy = LibraryIndex(legacy_path, read_tags=_reader({d: {"album": "Alb", "artist": "A", "date": "2000"}}))
    legacy.refresh(lib)
    legacy.close()
    open(legacy_path + "-wal", "wb").close()  # an empty wal is valid; sqlite consumes it
    open(legacy_path + "-shm", "wb").close()
    per_root = cache_file_for_root(cfg, lib)
    for stranded in (legacy_path, legacy_path + "-wal", legacy_path + "-shm"):
        assert not os.path.exists(stranded), f"{os.path.basename(stranded)} left under the legacy name"
    adopted = LibraryIndex(per_root, read_tags=lambda p: None)
    assert len(list(adopted.iter_albums())) == 1, "a commit was lost in adoption"


def test_a_garbage_legacy_cache_never_blocks_the_per_root_file(tmp_path):
    # A corrupt legacy file is left alone and the per-root file starts fresh;
    # the probe must never raise past cache_file_for_root (it runs at startup).
    cfg = str(tmp_path / "cfg")
    os.makedirs(cfg)
    with open(os.path.join(cfg, "library.sqlite3"), "wb") as fh:
        fh.write(b"this was never a database")
    root = str(tmp_path / "Music")
    path = cache_file_for_root(cfg, root)
    assert os.path.basename(path).startswith("library-")
    assert os.path.exists(os.path.join(cfg, "library.sqlite3")), "a foreign file is left alone"


def test_no_root_answers_the_legacy_name(tmp_path):
    # Nothing is scanned while no library is configured; the constructor still
    # needs a file it can open.
    cfg = str(tmp_path / "cfg")
    os.makedirs(cfg)
    assert cache_file_for_root(cfg, "") == os.path.join(cfg, "library.sqlite3")


def test_legacy_cache_is_adopted_for_its_own_root(tmp_path):
    # An existing user's single-file cache holds one root's scan; the first
    # per-root open for that very root renames it in rather than rescanning.
    cfg = str(tmp_path / "cfg")
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Alb", ["1.flac"])
    legacy = LibraryIndex(
        os.path.join(cfg, "library.sqlite3"), read_tags=_reader({d: {"album": "Alb", "artist": "A", "date": "2000"}})
    )
    legacy.refresh(lib)
    legacy.close()
    path = cache_file_for_root(cfg, lib)
    assert not os.path.exists(os.path.join(cfg, "library.sqlite3"))  # renamed, not copied
    idx = LibraryIndex(path, read_tags=_reader({}))
    assert next(iter(idx.iter_albums()))["title"] == "Alb"  # the scan survived
    assert idx.matches_scan_root(lib)


def test_legacy_cache_for_another_root_is_left_alone(tmp_path):
    # A legacy file scanned against a DIFFERENT folder is not this root's data:
    # it stays put (its own root may claim it later) and this root starts fresh.
    cfg = str(tmp_path / "cfg")
    old = _mk(tmp_path, "old", [])
    d = _mk(tmp_path, "old/A/Alb", ["1.flac"])
    legacy = LibraryIndex(
        os.path.join(cfg, "library.sqlite3"), read_tags=_reader({d: {"album": "Alb", "artist": "A", "date": "2000"}})
    )
    legacy.refresh(old)
    legacy.close()
    new = _mk(tmp_path, "new", [])
    path = cache_file_for_root(cfg, new)
    assert os.path.exists(os.path.join(cfg, "library.sqlite3"))  # untouched
    idx = LibraryIndex(path, read_tags=_reader({}))
    assert list(idx.iter_albums()) == []  # fresh, no other root's albums
    # And the legacy file's own root still adopts it afterwards.
    assert cache_file_for_root(cfg, old) != path
    assert not os.path.exists(os.path.join(cfg, "library.sqlite3"))


# --- Per-track rows (the track-level presence pill's data) ---------------------


def _path_reader(pathmap, counter=None):
    """A fake tag reader keyed by the file's FULL path, for per-file titles."""

    def read(path):
        if counter is not None:
            counter.append(path)
        return pathmap.get(path)

    return read


def test_every_file_yields_a_track_row(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Alb", ["1.flac", "2.flac"])
    pathmap = {
        os.path.join(d, "1.flac"): {"album": "Alb", "artist": "A", "date": "2000", "title": "One", "track_artist": "A"},
        os.path.join(d, "2.flac"): {
            "album": "Alb",
            "artist": "A",
            "date": "2000",
            "title": "Two",
            "track_artist": "A feat. B",
        },
    }
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_path_reader(pathmap))
    idx.refresh(lib)
    got = sorted((t["title"], t["artist"], t["id"]) for t in idx.iter_tracks())
    assert got == [("One", "A", d), ("Two", "A feat. B", d)]


def test_track_rows_carry_their_own_quality(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Alb", ["1.flac", "2.mp3"])
    pathmap = {
        os.path.join(d, "1.flac"): {
            "album": "Alb",
            "artist": "A",
            "date": "2000",
            "title": "One",
            "codec": "flac",
            "bits": 24,
            "rate": 96000,
        },
        os.path.join(d, "2.mp3"): {
            "album": "Alb",
            "artist": "A",
            "date": "2000",
            "title": "Two",
            "codec": "mp3",
            "bitrate": 128,
        },
    }
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_path_reader(pathmap))
    idx.refresh(lib)
    got = {t["title"]: t for t in idx.iter_tracks()}
    assert got["One"]["codec"] == "flac" and got["One"]["bits"] == 24 and got["One"]["rate"] == 96000
    assert got["Two"]["codec"] == "mp3" and got["Two"]["bitrate"] == 128


def test_pre_tracks_cache_backfills_once_then_rests(tmp_path):
    # An existing cache from before per-track capture has album rows but no
    # track rows: the next scan re-reads every album ONCE to backfill, and the
    # scan after that is quiet again.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Alb", ["1.flac", "2.flac"])
    tags = {d: {"album": "Alb", "artist": "A", "date": "2000", "title": "Song"}}
    idx = _index(tmp_path, tags)
    idx.refresh(lib)
    import sqlite3 as _sq

    conn = _sq.connect(str(tmp_path / "library.sqlite3"))
    conn.execute("DELETE FROM tracks")  # simulate the pre-tracks cache
    conn.commit()
    conn.close()
    calls: list[str] = []
    idx2 = _index(tmp_path, tags, counter=calls)
    idx2.refresh(lib)
    assert calls != []  # backfill re-read happened
    assert len(list(idx2.iter_tracks())) == 2
    calls.clear()
    idx2.refresh(lib)
    assert calls == []  # and only once


def test_deleted_album_prunes_its_track_rows(tmp_path):
    # Two albums so the walk still finds one: a fully empty walk is (rightly)
    # held back by the empty-library guard and prunes nothing yet.
    lib = _mk(tmp_path, "lib", [])
    d1 = _mk(tmp_path, "lib/A/One", ["1.flac"])
    d2 = _mk(tmp_path, "lib/A/Two", ["1.flac"])
    tags = {
        d1: {"album": "One", "artist": "A", "date": "1", "title": "Keep"},
        d2: {"album": "Two", "artist": "A", "date": "2", "title": "Drop"},
    }
    idx = _index(tmp_path, tags)
    idx.refresh(lib)
    assert sorted(t["title"] for t in idx.iter_tracks()) == ["Drop", "Keep"]
    os.remove(os.path.join(d2, "1.flac"))
    os.rmdir(d2)
    idx.refresh(lib)
    assert [t["title"] for t in idx.iter_tracks()] == ["Keep"]


def test_retagged_file_replaces_its_track_row(tmp_path):
    # A re-tagged (or renamed) file must not leave its old title lingering
    # beside the new one: the folder's rows are replaced wholesale on re-read.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Alb", ["1.flac"])
    tags = {d: {"album": "Alb", "artist": "A", "date": "2000", "title": "Old"}}
    idx = _index(tmp_path, tags)
    idx.refresh(lib)
    tags[d] = {"album": "Alb", "artist": "A", "date": "2000", "title": "New"}
    # Touch the folder so the incremental sweep re-reads it.
    open(os.path.join(d, "2.flac"), "w").close()
    idx.refresh(lib)
    titles = sorted(t["title"] for t in idx.iter_tracks())
    assert titles == ["New", "New"]


def test_mixed_folder_zeroes_count_but_keeps_every_title(tmp_path):
    # A dump folder (two different album tags) must not claim an album's track
    # count, but each file's own title is still real and still indexed.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/Inbox", ["a.flac", "b.flac"])
    pathmap = {
        os.path.join(d, "a.flac"): {
            "album": "Discovery",
            "artist": "Daft Punk",
            "date": "2001",
            "title": "One More Time",
        },
        os.path.join(d, "b.flac"): {
            "album": "Homework",
            "artist": "Daft Punk",
            "date": "1997",
            "title": "Around the World",
        },
    }
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_path_reader(pathmap))
    idx.refresh(lib)
    assert next(iter(idx.iter_albums()))["tracks"] == 0
    assert sorted(t["title"] for t in idx.iter_tracks()) == ["Around the World", "One More Time"]


def test_unreadable_file_costs_its_row_only_then_rests_then_heals(tmp_path):
    # One file failing to read loses only its own title; the album row stands.
    # The folder is then short of one row per file, and that deficit must NOT
    # mean a full re-read on every scan for as long as the file stays broken:
    # a permanently corrupt file (0-byte rip, broken symlink) can never close
    # it, and the unbounded retry re-read its whole folder's tags per scan,
    # forever, over the network. The retry rests for _UNREADABLE_RETRY_S and
    # then runs, so a transient NAS hiccup still heals on its own and a
    # corrupt file costs one bounded retry per window.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Alb", ["1.flac", "2.flac"])
    good = {
        os.path.join(d, "1.flac"): {"album": "Alb", "artist": "A", "date": "2000", "title": "One"},
        os.path.join(d, "2.flac"): {"album": "Alb", "artist": "A", "date": "2000", "title": "Two"},
    }
    flaky = dict(good)
    del flaky[os.path.join(d, "2.flac")]  # unreadable this pass
    holder = {"map": flaky}
    reads: list[str] = []
    idx = LibraryIndex(
        str(tmp_path / "library.sqlite3"),
        read_tags=lambda p: (reads.append(p), holder["map"].get(p))[1],
    )
    idx.refresh(lib)
    assert next(iter(idx.iter_albums()))["tracks"] == 2  # count from the listing, not the reads
    assert sorted(t["title"] for t in idx.iter_tracks()) == ["One"]
    holder["map"] = good  # the hiccup clears...
    reads.clear()
    idx.refresh(lib)  # ...but inside the rest window the folder is left alone
    assert reads == [], "a short folder must rest between retries, not re-read every scan"
    assert sorted(t["title"] for t in idx.iter_tracks()) == ["One"]
    with idx._lock:  # the window lapses (backdate the read, the test's fake clock)
        idx._conn.execute("UPDATE albums SET recorded_at = recorded_at - 999999")
        idx._conn.commit()
    idx.refresh(lib)
    assert sorted(t["title"] for t in idx.iter_tracks()) == ["One", "Two"]


def test_a_midwalk_vanish_gets_one_scan_of_grace_before_pruning(tmp_path):
    # A share unmounting mid-walk answers ENOENT for every folder the walk has
    # not reached, and can leave its mountpoint behind as a readable empty
    # directory, so the root re-probe passes and part of the walk is already
    # stamped. In the moment that is indistinguishable from a deletion a NAS
    # hid from the parent's mtime, so a vanished row earns ONE generation of
    # grace: the badges survive the drop, and a persistent absence is still
    # believed (and pruned) on the following scan. A deletion whose parent
    # re-lists prunes immediately via the condemned path, covered elsewhere.
    lib = _mk(tmp_path, "lib", [])
    d1 = _mk(tmp_path, "lib/A/One", ["1.flac"])
    d2 = _mk(tmp_path, "lib/B/Two", ["1.flac"])
    tags = {d1: {"album": "One", "artist": "A", "date": "1"}, d2: {"album": "Two", "artist": "B", "date": "2"}}
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 2
    keep = os.stat(lib).st_mtime
    os.remove(os.path.join(d2, "1.flac"))
    os.rmdir(d2)
    os.rmdir(os.path.dirname(d2))
    os.utime(lib, (keep, keep))  # the parent's mtime hides the loss, as a mid-walk drop would
    assert idx.refresh(lib) == 2, "one vanished walk must not prune: a mount drop looks exactly like this"
    os.utime(lib, (keep, keep))
    assert idx.refresh(lib) == 1, "a second consecutive absence is a real deletion and prunes"
    assert [a["title"] for a in idx.iter_albums()] == ["One"]


def test_a_folder_replaced_by_a_same_named_file_is_pruned_not_retried(tmp_path):
    # An album folder replaced by a same-named FILE stats fine but cannot be
    # listed (NotADirectoryError), which the transient-error branch read as a
    # hiccup: the ghost row was re-stamped alive on every scan forever, its
    # badge with it. A non-directory is positive evidence, so it takes the same
    # road as ENOENT: one generation of grace, then pruned. The parent's mtime
    # is pinned so the condemned path (a fresh listing missing the child)
    # cannot answer instead of the check under test.
    lib = _mk(tmp_path, "lib", [])
    d1 = _mk(tmp_path, "lib/A/One", ["1.flac"])
    d2 = _mk(tmp_path, "lib/B/Two", ["1.flac"])
    tags = {d1: {"album": "One", "artist": "A", "date": "1"}, d2: {"album": "Two", "artist": "B", "date": "2"}}
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 2
    parent = os.path.dirname(d2)
    keep = os.stat(parent).st_mtime
    os.remove(os.path.join(d2, "1.flac"))
    os.rmdir(d2)
    open(d2, "w").close()  # a file now sits where the album folder was
    os.utime(parent, (keep, keep))
    idx.refresh(lib)
    os.utime(parent, (keep, keep))
    assert idx.refresh(lib) == 1, "a path that is no longer a directory kept its album row alive"
    assert [a["title"] for a in idx.iter_albums()] == ["One"]


def test_a_mixed_folder_is_not_reread_on_every_rescan(tmp_path):
    # A dump folder's recorded verdict is track_count 0 (files disagreeing on
    # their album tag), which the walk's raw file count can never equal, so the
    # unchanged-check failed and the manual Rescan re-read every file of the
    # most expensive folders there are, every time. An mtime match plus the
    # recorded verdict is settled: no re-read until the folder actually moves.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/Inbox", ["a.flac", "b.flac"])
    pathmap = {
        os.path.join(d, "a.flac"): {
            "album": "Discovery",
            "artist": "Daft Punk",
            "date": "2001",
            "title": "One More Time",
        },
        os.path.join(d, "b.flac"): {
            "album": "Homework",
            "artist": "Daft Punk",
            "date": "1997",
            "title": "Around the World",
        },
    }
    counter: list = []
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_path_reader(pathmap, counter))
    idx.refresh(lib)
    assert next(iter(idx.iter_albums()))["tracks"] == 0
    before = len(counter)
    assert before > 0
    idx.refresh(lib, force_full=True)
    assert len(counter) == before, "the mixed folder's tags were re-read despite nothing changing"
    idx.refresh(lib)
    assert len(counter) == before


def test_a_superseded_scan_dies_before_touching_the_cache(tmp_path):
    # A refresh whose should_continue is already False (a worker that sat
    # queued across a library-folder switch) must bail BEFORE _begin_scan:
    # entering it against a root this index no longer serves would wipe the
    # dirs tree and restamp scan_root, costing the per-root cache its warmth
    # and the launch seed its badges.
    lib = _mk(tmp_path, "lib", [])
    other = _mk(tmp_path, "other", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    idx = _index(tmp_path, {d: {"album": "Album", "artist": "A", "date": "2000"}})
    assert idx.refresh(lib) == 1
    idx.refresh(other, should_continue=lambda: False)
    assert idx.matches_scan_root(lib), "a superseded scan restamped scan_root"
    assert len(list(idx.iter_albums())) == 1


def test_runtime_and_track_lengths_recorded(tmp_path):
    # Every file reports a length: the album row carries the folder's sum and
    # each track row its own seconds, the matcher's duration witness.
    lib = _mk(tmp_path, "lib", [])
    _mk(tmp_path, "lib/A/Album", ["1.flac", "2.flac"])
    base = {"album": "Album", "artist": "A", "date": "2020", "codec": "flac"}

    def read(path):
        return {**base, "title": os.path.basename(path), "length": 100 if path.endswith("1.flac") else 150}

    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=read)
    idx.refresh(lib)
    a = next(idx.iter_albums())
    assert a["runtime"] == 250
    assert sorted(t["length"] for t in idx.iter_tracks()) == [100, 150]


def test_runtime_zero_unless_every_file_spoke(tmp_path):
    # One silent file would make the sum a lie that refutes true matches, so
    # the folder honestly reports "never said".
    lib = _mk(tmp_path, "lib", [])
    _mk(tmp_path, "lib/A/Album", ["1.flac", "2.flac"])
    base = {"album": "Album", "artist": "A", "date": "2020", "codec": "flac"}

    def read(path):
        return {**base, "length": 100 if path.endswith("1.flac") else 0}

    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=read)
    idx.refresh(lib)
    assert next(idx.iter_albums())["runtime"] == 0


def test_runtime_column_migrates_and_backfills(tmp_path):
    import sqlite3

    # A cache from before duration capture opens cleanly and its rows re-read
    # ONCE to learn their runtime even though the folder itself is unchanged.
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["1.flac"])
    db = str(tmp_path / "library.sqlite3")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE albums (
        folder_path TEXT NOT NULL PRIMARY KEY, album TEXT, artist TEXT, year TEXT,
        track_count INTEGER NOT NULL DEFAULT 0, dir_mtime REAL NOT NULL DEFAULT 0,
        recorded_at INTEGER NOT NULL DEFAULT 0, codec TEXT,
        bitrate INTEGER NOT NULL DEFAULT 0, bits INTEGER NOT NULL DEFAULT 0,
        rate INTEGER NOT NULL DEFAULT 0, declared INTEGER, disc_no INTEGER, disc_total INTEGER)""")
    conn.execute("""CREATE TABLE tracks (
        folder_path TEXT NOT NULL, title TEXT, artist TEXT, codec TEXT,
        bitrate INTEGER NOT NULL DEFAULT 0, bits INTEGER NOT NULL DEFAULT 0,
        rate INTEGER NOT NULL DEFAULT 0)""")
    conn.execute(
        "INSERT INTO albums VALUES (?, 'Album', 'A', '2000', 1, ?, ?, 'flac', 900, 16, 44100, 1, 0, 0)",
        (d, os.stat(d).st_mtime, int(__import__("time").time())),
    )
    conn.execute("INSERT INTO tracks VALUES (?, 'T', 'A', 'flac', 900, 16, 44100)", (d,))
    conn.commit()
    conn.close()

    calls: list[str] = []
    tags = {d: {"album": "Album", "artist": "A", "date": "2000", "codec": "flac", "length": 123, "title": "T"}}
    idx = LibraryIndex(db, read_tags=_reader(tags, calls))
    assert idx.refresh(lib) == 1
    assert len(calls) == 1  # backfill read despite unchanged mtime/count
    a = next(idx.iter_albums())
    assert a["runtime"] == 123
    assert next(idx.iter_tracks())["length"] == 123
    idx.refresh(lib)
    assert len(calls) == 1  # backfilled row is incremental again


def test_single_stray_tag_no_longer_hides_the_album(tmp_path):
    # One leftover single in an album folder: the album keeps its identity and
    # counts only the agreeing files, so coverage stays honest and the badge
    # stays lit. The stray file is simply not counted.
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 14)]
    _mk(tmp_path, "lib/A/Album", names)
    filemap = {n: {"album": "Album", "artist": "A", "date": "2020"} for n in names}
    filemap["13.flac"] = {"album": "Some Single", "artist": "A", "date": "2019"}
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_per_file_reader(filemap))
    idx.refresh(lib)
    row = next(idx.iter_albums())
    assert row["title"] == "Album"
    assert row["tracks"] == 12


def test_a_split_folder_still_yields_no_claim(tmp_path):
    # Half one album, half another: no overwhelming majority, no count. The
    # dump-folder refusal is unchanged below the threshold.
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 11)]
    _mk(tmp_path, "lib/A/Mixed", names)
    filemap = {n: {"album": "One", "artist": "A", "date": "2020"} for n in names[:6]}
    filemap.update({n: {"album": "Two", "artist": "A", "date": "2020"} for n in names[6:]})
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_per_file_reader(filemap))
    idx.refresh(lib)
    assert next(idx.iter_albums())["tracks"] == 0


def test_a_stray_on_the_representative_seat_still_zeroes(tmp_path):
    # The first file IS the stray: the row's identity would be the stray's
    # album wearing the majority's count, so the count is refused instead.
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 11)]
    _mk(tmp_path, "lib/A/Album", names)
    filemap = {n: {"album": "Album", "artist": "A", "date": "2020"} for n in names}
    filemap["01.flac"] = {"album": "Some Single", "artist": "A", "date": "2019"}
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_per_file_reader(filemap))
    idx.refresh(lib)
    row = next(idx.iter_albums())
    assert row["title"] == "Some Single"  # identity still comes from the first file
    assert row["tracks"] == 0


def test_a_disagreeing_folder_never_declares_shape_or_runtime(tmp_path):
    # With any dissent at all, the declared tracktotal, the disc numbers and
    # the summed runtime are silenced: they may belong to the strays, and a
    # wrong witness is worse than none.
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 14)]
    _mk(tmp_path, "lib/A/Album", names)
    filemap = {n: {"album": "Album", "artist": "A", "date": "2020", "track_total": 12, "length": 200} for n in names}
    filemap["13.flac"] = {"album": "Some Single", "artist": "A", "date": "2019", "track_total": 1, "length": 100}
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_per_file_reader(filemap))
    idx.refresh(lib)
    row = next(idx.iter_albums())
    assert row["tracks"] == 12
    assert row["declared"] == 0
    assert row["runtime"] == 0


def test_majority_counts_only_the_files_that_voted(tmp_path):
    # 10 agreeing + 1 stray + 1 unreadable: the count is 10, the votes, never
    # 11 (raw files minus the stray), which landed exactly on an 11-track
    # release and let the bulk gate skip an album the user holds 10 of. A
    # folder proven mixed forfeits the unreadable file's benefit of the doubt.
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 13)]
    _mk(tmp_path, "lib/A/Album", names)
    filemap = {n: {"album": "Album", "artist": "A", "date": "2020"} for n in names}
    filemap["11.flac"] = {"album": "Stray", "artist": "A", "date": "2019"}
    filemap["12.flac"] = None  # unreadable
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_per_file_reader(filemap))
    idx.refresh(lib)
    assert next(idx.iter_albums())["tracks"] == 10


def test_majority_boundary_is_one_in_ten(tmp_path):
    # The documented bar, pinned exactly: 2 dissenters among 20 tagged files
    # (10%, inclusive) still rule; 2 among 19 (10.5%) refuse.
    for total, expected in ((20, 18), (19, 0)):
        names = [f"{i:02d}.flac" for i in range(1, total + 1)]
        _mk(tmp_path, f"lib{total}/A/Album", names)
        filemap = {n: {"album": "Album", "artist": "A", "date": "2020"} for n in names}
        filemap["01.flac"] = {"album": "Stray One", "artist": "A", "date": "2019"}
        filemap["02.flac"] = {"album": "Stray Two", "artist": "A", "date": "2019"}
        # the representative must sit on the majority side, so strays are 3rd+
        filemap["01.flac"], filemap["03.flac"] = filemap["03.flac"], filemap["01.flac"]
        filemap["02.flac"], filemap["04.flac"] = filemap["04.flac"], filemap["02.flac"]
        idx = LibraryIndex(str(tmp_path / f"library{total}.sqlite3"), read_tags=_per_file_reader(filemap))
        idx.refresh(_mk(tmp_path, f"lib{total}", []))
        assert next(idx.iter_albums())["tracks"] == expected, total


def test_majority_verdict_rows_are_not_rereread_forever(tmp_path):
    # A majority-ruled row stores a count that is neither the raw file count
    # nor 0; the unchanged-guard must treat it as a finished verdict, or every
    # stray-carrying folder re-reads all its tags on every deep Rescan.
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 13)]
    _mk(tmp_path, "lib/A/Album", names)
    filemap = {n: {"album": "Album", "artist": "A", "date": "2020"} for n in names}
    filemap["12.flac"] = {"album": "Stray", "artist": "A", "date": "2019"}
    calls: list[str] = []

    def read(path):
        calls.append(path)
        return filemap.get(os.path.basename(path))

    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=read)
    idx.refresh(lib)
    before = len(calls)
    idx.refresh(lib, force_full=True)
    assert len(calls) == before  # the verdict stands, nothing is re-read


# --- A network root throttles the scan pools (the SMB thundering herd) --------


class _PoolSpy:
    """Records every max_workers the scanner asks for."""

    def __init__(self) -> None:
        self.sizes: list[int] = []

    def install(self, monkeypatch):
        import waves.library_index as mod

        real = mod.ThreadPoolExecutor
        sizes = self.sizes

        class Spy(real):
            def __init__(self, max_workers=None, **kwargs):
                sizes.append(max_workers)
                super().__init__(max_workers=max_workers, **kwargs)

        monkeypatch.setattr(mod, "ThreadPoolExecutor", Spy)


def test_a_network_root_throttles_the_scan_pools(tmp_path, monkeypatch):
    """A root classified as a network mount runs BOTH scan phases and the
    container poll on small pools. Sixteen concurrent round trips against a
    cold SMB share is the herd that can wedge the mount for the whole desktop
    (macOS funnels every process touching a wedged mount into uninterruptible
    kernel I/O); a share is latency-bound anyway, so the small pools keep the
    concurrency win without the cliff."""
    lib = str(tmp_path / "lib")
    d = _mk(lib, "Artist/Album", ["1.flac", "2.flac"])
    tags = {d: {"album": "Album", "artist": "Artist", "date": "2020"}}

    spy = _PoolSpy()
    spy.install(monkeypatch)
    idx = _index(tmp_path, tags)
    idx.refresh(lib, root_is_local=False)
    idx.poll_containers_changed(lib)

    assert spy.sizes, "the scan never built a pool"
    assert set(spy.sizes) == {2}, f"a network root must throttle every pool (saw {spy.sizes})"
    assert WALK_GAUGE.maxThreadCount() == 2, "the perf gauge must report the cap the scan ran under"
    idx.close()


def test_a_local_or_unclassified_root_keeps_the_full_pools(tmp_path, monkeypatch):
    """True keeps full speed, and so does an ABSENT verdict: only positive
    knowledge of a network mount changes behavior, mirroring the classifier's
    own confidently-local framing."""
    lib = str(tmp_path / "lib")
    d = _mk(lib, "Artist/Album", ["1.flac"])
    tags = {d: {"album": "Album", "artist": "Artist", "date": "2020"}}

    spy = _PoolSpy()
    spy.install(monkeypatch)
    idx = _index(tmp_path, tags)
    idx.refresh(lib, root_is_local=True)
    idx.refresh(lib, force_full=True)  # no verdict given: stays full-size

    assert spy.sizes and set(spy.sizes) == {8}, f"full-size pools expected (saw {spy.sizes})"
    assert WALK_GAUGE.maxThreadCount() == 8
    idx.close()


def test_a_probe_failed_scan_still_sizes_the_poll_for_a_network_root(tmp_path, monkeypatch):
    """The container poll borrows the last refresh's pool size, and a launch
    against an OFFLINE NAS returns at the probe: sizing after the probe left
    the poll to greet the returning share with the constructor's full-size
    herd, exactly the restraint the throttle exists for."""
    lib = str(tmp_path / "lib")
    d = _mk(lib, "Artist/Album", ["1.flac"])
    tags = {d: {"album": "Album", "artist": "Artist", "date": "2020"}}
    idx = _index(tmp_path, tags)
    idx.refresh(lib, root_is_local=False)  # a past session's scan, so the poll has rows
    idx.close()

    # A fresh launch: the constructor's default size, then the first scan dies
    # at the probe (the share is offline). The sizing must already have landed.
    idx = _index(tmp_path, tags)
    idx.refresh(str(tmp_path / "gone"), root_is_local=False)
    assert idx.last_scan_status == SCAN_MISSING

    spy = _PoolSpy()
    spy.install(monkeypatch)
    assert idx.poll_containers_changed(lib) is not None
    assert spy.sizes and set(spy.sizes) == {
        _NETWORK_WORKERS
    }, f"the poll after a probe-failed scan must stay throttled (saw {spy.sizes})"
    idx.close()


def test_a_scan_quit_partway_through_the_read_is_finished_by_the_next_plain_scan(tmp_path):
    """Quitting Waves mid-scan must not cost the library its unread albums.

    A scan reaches every album folder in the walk, stamps each one into the
    dirs tree with the mtime it was listed at, and only then reads their tags.
    Quitting during that read phase leaves folders that are listed, stamped
    and current, but have no album row yet. The next launch runs an ORDINARY
    incremental scan, and if that scan trusted the dirs mtime it would skip
    exactly those folders as "unchanged" and the albums would stay missing
    until a forced full sweep happened to run (livetest question: a library
    that read 9600 albums, then 11177 on the next full sweep).

    The read state is authoritative in the albums table, not the dirs tree
    (see _walk_album_dirs.expected_mtime), so a folder owed a read is re-listed
    however fresh its mtime looks. Pinned here end to end: interrupt, resume,
    nothing lost, and no forced sweep needed.
    """
    lib = _mk(tmp_path, "lib", [])
    dirs = [_mk(tmp_path, f"lib/Artist{i}/Album{i}", ["01.flac"]) for i in range(6)]
    tags = {d: {"album": f"Album{i}", "artist": f"Artist{i}", "date": "2020"} for i, d in enumerate(dirs)}

    # The quit: the reader survives two albums, then every later call sees a
    # dead should_continue, exactly as a closing app does mid-read.
    reads: list[str] = []
    idx = _index(tmp_path, tags, counter=reads)
    n_first = idx.refresh(lib, should_continue=lambda: len(reads) < 2)
    idx.close()
    assert n_first < 6, f"the interrupted scan was supposed to stop short, indexed {n_first}"

    # The relaunch: a plain incremental scan, NOT force_full.
    idx = _index(tmp_path, tags)
    n_second = idx.refresh(lib)
    assert n_second == 6, (
        "an interrupted scan left albums listed-but-unread, and the next plain scan "
        f"skipped them as unchanged: {n_second} of 6 indexed. Only a forced sweep would "
        "have healed the library."
    )
    # And it is genuinely settled: a third scan finds nothing left to do.
    assert idx.refresh(lib) == 6
    idx.close()


def test_a_scan_quit_during_the_WALK_is_finished_by_the_next_plain_scan(tmp_path):
    """The other half of the same invariant: a quit during the LISTING.

    A walk records each newly discovered subfolder as an unlisted frontier row
    before descending into it, and checkpoints those rows when it bails, so the
    next scan knows the folder exists and still owes a listing. A quit here is
    the common one on a big network library, since the walk is the long phase.
    """
    lib = _mk(tmp_path, "lib", [])
    dirs = [_mk(tmp_path, f"lib/Artist{i}/Album{i}", ["01.flac"]) for i in range(8)]
    tags = {d: {"album": f"Album{i}", "artist": f"Artist{i}", "date": "2020"} for i, d in enumerate(dirs)}

    walk_events: list[dict] = []
    idx = _index(tmp_path, tags)
    n_first = idx.refresh(
        lib,
        on_progress=walk_events.append,
        progress_interval=0.0,  # every event, so the cut lands mid-walk
        should_continue=lambda: len([e for e in walk_events if e.get("phase") == "walk"]) < 3,
    )
    idx.close()
    assert n_first < 8, f"the interrupted walk was supposed to stop short, indexed {n_first}"

    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 8, "a walk cut short left folders the next plain scan never listed"
    idx.close()
