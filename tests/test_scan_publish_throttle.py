"""Mid-scan badge publishes are throttled; the rollup is derived off-GUI.

THE COSTS THESE FENCE OFF
-------------------------
R7: every committed flush of a running scan rebuilt BOTH presence indexes
from a full table read (a cold scan of a big library commits every 200
albums, so ~90 rebuilds whose own cost grows as the table fills). Mid-scan
partial publishes are now rate-limited to one per _SCAN_PUBLISH_MIN_S; the
FIRST commit still publishes immediately (badges light up as soon as
anything is committed) and the scan's final publish is unconditional, so
nothing committed is ever left unpublished.

R9: the artist rollup (a full pass over the album index) was derived lazily
inside the synchronous artistLibraryPresence slot, on the GUI thread, on the
first ask after every republish. Every publish now precomputes it on the
worker (_publish_artist_rollup) before libraryPresenceChanged fires; the
slot keeps the lazy derive only as a race fallback.

The scan is driven through a fake lib whose refresh fires a burst of
committed events, over a REAL LibraryIndex's rows (the fake delegates
everything else), so build_index sees genuine data.
"""

from __future__ import annotations

import os

from test_library_bridge import _album, _make

from waves.waves_ui import bridge_library


class _BurstLib:
    """Delegates to a real, already-scanned LibraryIndex; refresh only fires
    committed progress events in a tight burst (all inside the throttle
    window, since nothing sleeps between them)."""

    def __init__(self, inner, commits: int):
        self._inner = inner
        self._commits = commits

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def refresh(self, root, should_continue=None, on_progress=None, force_full=False, root_is_local=None):
        n = len(list(self._inner.iter_albums()))
        for i in range(self._commits):
            on_progress({"phase": "read", "done": i + 1, "total": self._commits, "indexed": n, "committed": True})
        return n


def _scanned_bridge(tmp_path, commits: int):
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    tagmap = {}
    for i, (artist, album) in enumerate([("Lorna Shore", "Pain Remains"), ("Boards of Canada", "Geogaddi")]):
        d = _album(lib, f"{artist}/{album}", ["1.flac", "2.flac"])
        tagmap[d] = {"album": album, "artist": artist, "date": f"200{i}"}
    s = _make(tmp_path, library_folder=lib, tagmap=tagmap)
    s._rebuild_library_index()  # populate the real cache once
    assert s._library_index is not None
    s._library = _BurstLib(s._library, commits)
    s.libraryPresenceChanged.emits.clear()
    return s


def test_a_commit_burst_publishes_once_plus_the_final(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_library, "_SCAN_PUBLISH_MIN_S", 10_000.0)
    s = _scanned_bridge(tmp_path, commits=5)
    s._rebuild_library_index()
    # First commit of the burst, then the unconditional final publish.
    assert len(s.libraryPresenceChanged.emits) == 2
    # The trailing publish left the complete index standing.
    assert s.libraryAlbumPresence("Lorna Shore", "Pain Remains", "2000", 2)["present"]


def test_an_open_window_lets_every_commit_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_library, "_SCAN_PUBLISH_MIN_S", 0.0)
    s = _scanned_bridge(tmp_path, commits=5)
    s._rebuild_library_index()
    assert len(s.libraryPresenceChanged.emits) == 5 + 1


def test_every_publish_precomputes_the_artist_rollup(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_library, "_SCAN_PUBLISH_MIN_S", 10_000.0)
    s = _scanned_bridge(tmp_path, commits=3)
    s._rebuild_library_index()
    # Precomputed for the published index BEFORE anyone asked the slot.
    assert s._library_artist_index_src is s._library_index
    assert s._library_artist_index, "rollup published empty"
    # And it answers: the slot's fast path is a dict.get, no derive needed.
    r = s.artistLibraryPresence("Lorna Shore")
    assert r["present"] and r["albums"] == 1
