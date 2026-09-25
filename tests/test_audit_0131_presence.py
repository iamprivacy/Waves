"""Pins for the presence follow-ups of the v0.1.31 audit (the fixes to the
2026-09-24 final audit's C19/C36, C23, C32 and L04):

* the IN LIBRARY slots weigh the clean/explicit flag the bulk gates weigh,
  and memoize per flag (#18), and every asker passes it: the QML pills and
  buttons, the worker's dressing and the album page header;
* with both editions on disk, the album verdict picks the one on the
  release's own side (#19);
* a collaboration credit led by the album's artist does not unkey a
  single-artist album in the album-artist vote (#20);
* the reveal never stats on the GUI thread (#21);
* a cache marked by the older skip rule is swept once more, so recycled
  albums on an NTFS drive root stop reading as owned (#23).

Every test drives the real functions: the matcher on hand-built indexes, the
scanner on a temp tree with an injected tag reader, the bridge slots bound
onto the stub test_library_bridge builds.
"""

from __future__ import annotations

import os
import pathlib
import re
import sqlite3
import sys

import pytest

from waves import matching
from waves.library_index import LibraryIndex, _primary_credit
from waves.matching import decide_presence, presence_key

# ---- helpers ------------------------------------------------------------------


def _album_index(*entries):
    """A presence index from (title, artist, year, tracks, folder, extra)."""
    idx: dict = {}
    for title, artist, year, tracks, fp, extra in entries:
        idx.setdefault(presence_key(title, artist), []).append(
            {"title": title, "year": year, "tracks": tracks, "id": fp, **extra}
        )
    return idx


def _mk(base, rel, files):
    d = os.path.join(base, *rel.split("/"))
    os.makedirs(d, exist_ok=True)
    for name in files:
        open(os.path.join(d, name), "w").close()
    return d


def _by_name(filemap):
    def read(path):
        return filemap.get(os.path.basename(path))

    return read


def _tags(album="Album", artist="A", **extra):
    return {"album": album, "artist": artist, "date": "2020", "title": "Song", **extra}


# ---- #19: both editions on disk -------------------------------------------------


@pytest.mark.parametrize("clean_first", [True, False])
def test_the_edition_on_the_releases_side_is_chosen_whatever_the_row_order(clean_first):
    clean = ("Album", "A", "2020", 10, "/m/A/[2020] Album", {"explicit": 0})
    dirty = ("Album", "A", "2020", 10, "/m/A/[2020] Album (Explicit)", {"explicit": 1})
    idx = _album_index(*((clean, dirty) if clean_first else (dirty, clean)))
    r = decide_presence("Album", "A", "2020", 10, idx, explicit=True)
    assert r["sure"] is True and r["partial"] is False, "the user holds the explicit edition"
    assert r["local_album_id"] == "/m/A/[2020] Album (Explicit)", "and the reveal opens it"
    r = decide_presence("Album", "A", "2020", 10, idx, explicit=False)
    assert r["sure"] is True and r["local_album_id"] == "/m/A/[2020] Album"


def test_a_lone_other_edition_still_withholds_proof():
    idx = _album_index(("Album", "A", "2020", 10, "/m/A/Album", {"explicit": 0}))
    r = decide_presence("Album", "A", "2020", 10, idx, explicit=True)
    assert r["present"] is True and r["sure"] is False and r["local_explicit"] == 0


def test_the_edition_title_still_outranks_the_advisory_side():
    """Holding the standard clean and the deluxe explicit, the standard
    explicit release on screen is still matched against the standard copy
    (and so stays unproven), never against a different edition."""
    idx = _album_index(
        ("Album", "A", "2020", 10, "/m/A/Album", {"explicit": 0}),
        ("Album (Deluxe)", "A", "2020", 14, "/m/A/Album (Deluxe)", {"explicit": 1}),
    )
    r = decide_presence("Album", "A", "2020", 10, idx, explicit=True)
    assert r["local_album_id"] == "/m/A/Album" and r["sure"] is False


def test_an_unknown_flag_changes_nothing_about_the_choice():
    idx = _album_index(
        ("Album", "A", "2020", 10, "/m/A/one", {"explicit": 0}),
        ("Album", "A", "2020", 12, "/m/A/two", {"explicit": 1}),
    )
    assert decide_presence("Album", "A", "2020", 10, idx)["local_album_id"] == "/m/A/two", "the fuller copy, as before"


# ---- #18: the pills weigh the flag the gates weigh -------------------------------

bridge_library = pytest.importorskip("waves.waves_ui.bridge_library")
LibraryMixin = bridge_library.LibraryMixin


def _bridge(tmp_path, explicit):
    from test_library_bridge import _album, _make

    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "A/Album", ["1.flac", "2.flac", "3.flac"])
    tags = {"album": "Album", "artist": "A", "date": "2020", "title": "Song", "explicit": explicit}
    s = _make(tmp_path, library_folder=lib, tagmap={d: tags})
    s._rebuild_library_index()
    assert s._library_index is not None
    return s


def test_the_album_pill_does_not_vouch_for_the_other_edition(tmp_path):
    s = _bridge(tmp_path, explicit=0)
    assert s.libraryAlbumPresence("A", "Album", "2020", 3)["sure"] is True, "no flag: as before"
    clash = s.libraryAlbumPresence("A", "Album", "2020", 3, 0, 1)
    assert clash["present"] is True and clash["sure"] is False, "the explicit release is not the clean copy"
    assert s.libraryAlbumPresence("A", "Album", "2020", 3, 0, 0)["sure"] is True
    assert s.libraryAlbumPresence("A", "Album", "2020", 3, 0, -1)["sure"] is True, "-1 is unknown"


def test_the_album_pill_and_the_bulk_gate_agree(tmp_path):
    from types import SimpleNamespace

    s = _bridge(tmp_path, explicit=0)
    album = SimpleNamespace(name="Album", artist=SimpleNamespace(name="A"), year=2020, num_tracks=3, duration=0)
    for flag in (True, False):
        album.explicit = flag
        pill = s.libraryAlbumPresence("A", "Album", "2020", 3, 0, 1 if flag else 0)
        gate = s._library_claims_album(album)
        assert gate == (pill["present"] and not pill["partial"]), flag


def test_the_album_memo_is_keyed_by_the_flag(tmp_path, monkeypatch):
    s = _bridge(tmp_path, explicit=0)
    calls = []
    real = matching.decide_presence
    monkeypatch.setattr(matching, "decide_presence", lambda *a, **k: calls.append(a[-1]) or real(*a, **k))
    first = s.libraryAlbumPresence("A", "Album", "2020", 3, 0, 1)
    second = s.libraryAlbumPresence("A", "Album", "2020", 3, 0, 0)
    assert first["sure"] is False and second["sure"] is True, "one flag's verdict never answers for the other"
    s.libraryAlbumPresence("A", "Album", "2020", 3, 0, 1)
    assert calls == [True, False], "the same flag is answered from the memo"


def test_no_second_opinion_may_swear_the_other_edition_is_this_one(tmp_path):
    s = _bridge(tmp_path, explicit=0)
    asked = []
    s._mb_arbitrated = lambda verdict, *a: asked.append(1) or dict(verdict, sure=True)
    assert s.libraryAlbumPresence("A", "Album", "2020", 3, 0, 1)["sure"] is False
    assert asked == [], "a known clash never reaches the overlay"
    s.libraryAlbumPresence("A", "Other", "2020", 3, 0, 1)
    s.libraryAlbumPresence("A", "Album", "2019", 5, 0, -1)
    assert asked, "an unknown flag still reaches it"


def test_the_track_pill_does_not_vouch_for_the_other_cut(tmp_path):
    s = _bridge(tmp_path, explicit=0)
    assert s.libraryTrackPresence("A", "Song", "Album", "2020", 0)["sure"] is True, "no flag: as before"
    clash = s.libraryTrackPresence("A", "Song", "Album", "2020", 0, 1)
    assert clash["present"] is True and clash["sure"] is False
    assert s.libraryTrackPresence("A", "Song", "Album", "2020", 0, 0)["sure"] is True
    # The claim face and the claim gate reach the same verdict.
    assert s._library_track_claim("A", "Song", "Album", "2020", 0, True) is None
    assert s._library_track_claim("A", "Song", "Album", "2020", 0, False) is not None


def test_the_advisory_flag_reads_only_a_real_answer():
    f = LibraryMixin._advisory_flag
    assert f(1) is True and f(0) is False and f(True) is True and f(False) is False
    assert f(-1) is None and f(2) is None and f(None) is None and f("1") is None


def test_the_slots_keep_every_older_overload():
    import inspect

    album = inspect.signature(LibraryMixin.libraryAlbumPresence).parameters
    track = inspect.signature(LibraryMixin.libraryTrackPresence).parameters
    assert album["explicit"].default == -1 and track["explicit"].default == -1


# ---- #18 follow-up: every asker passes the flag -----------------------------------


def _dressing(s):
    """The worker-side dressing bound onto the scan stub, the way the real
    bridge runs it: the live slots it calls are the stub's own."""
    from waves.waves_ui.backend import WavesBridge

    s._CARD_DRESS_KINDS = WavesBridge._CARD_DRESS_KINDS
    s.collectionOwnership = lambda _id: False
    return lambda card: WavesBridge._dress_card(s, card), lambda cat, row: WavesBridge._dress_library_row(s, cat, row)


def _album_card(explicit):
    # The shape _album_dict emits: its explicit is bool(Album.explicit), so a
    # flag TIDAL never sent arrives here as False.
    return {
        "kind": "album",
        "id": "al-1",
        "title": "Album",
        "artist": "A",
        "year": "2020",
        "tracks": 3,
        "audio_tracks": 3,
        "duration_sec": 0,
        "explicit": explicit,
    }


def test_a_dressed_card_and_the_live_ask_agree_on_the_other_edition(tmp_path):
    """Only the clean copy on disk, the explicit release on screen: the
    verdict baked for first render, the one a publish asks for live (the QML
    passes 1 for an explicit card) and the bulk gate all refuse to vouch."""
    from types import SimpleNamespace

    s = _bridge(tmp_path, explicit=0)
    dress_card, dress_row = _dressing(s)
    baked = dress_card(_album_card(True))["lib"]
    live = s.libraryAlbumPresence("A", "Album", "2020", 3, 0, 1)
    assert baked == live, "the baked verdict and the live ask disagree"
    assert baked["present"] is True and baked["sure"] is False, "the pill vouched for the clean copy"
    assert dress_row("albums", _album_card(True))["lib"] == live, "a My Tidal row bakes the same verdict"
    album = SimpleNamespace(
        name="Album", artist=SimpleNamespace(name="A"), year=2020, num_tracks=3, duration=0, explicit=True
    )
    assert s._library_claims_album(album) == (baked["present"] and not baked["partial"])


def test_a_dressed_card_with_an_unknown_flag_answers_as_before(tmp_path):
    """An album dict's False may be TIDAL saying nothing, so it is never read
    as clean: the card answers exactly as the flagless ask did."""
    s = _bridge(tmp_path, explicit=1)
    dress_card, dress_row = _dressing(s)
    before = s.libraryAlbumPresence("A", "Album", "2020", 3, 0)
    assert before["sure"] is True
    assert dress_card(_album_card(False))["lib"] == before, "a missing flag was read as clean"
    assert dress_row("albums", _album_card(False))["lib"] == before
    card = _album_card(False)
    del card["explicit"]
    assert dress_card(card)["lib"] == before, "a payload cached before the flag existed"


def test_a_dressed_track_row_weighs_the_tracks_own_flag(tmp_path):
    """A track's own flag is always parsed, so here False IS clean: the row
    bakes what the claim gate and the live ask (1, 0 or -1) decide."""
    s = _bridge(tmp_path, explicit=0)
    _dress_card, dress_row = _dressing(s)
    row = {"id": "t1", "title": "Song", "artist": "A", "album": "Album", "year": "2020", "duration_sec": 0}
    dirty = dress_row("tracks", dict(row, explicit=True))["lib"]
    assert dirty == s.libraryTrackPresence("A", "Song", "Album", "2020", 0, 1)
    assert dirty["present"] is True and dirty["sure"] is False
    assert s._library_track_claim("A", "Song", "Album", "2020", 0, True) is None
    clean = dress_row("tracks", dict(row, explicit=False))["lib"]
    assert clean == s.libraryTrackPresence("A", "Song", "Album", "2020", 0, 0) and clean["sure"] is True
    assert dress_row("tracks", row)["lib"] == s.libraryTrackPresence("A", "Song", "Album", "2020", 0), "no flag"


def _call_args(src, name):
    """The top-level argument lists of every ``waves.<name>(...)`` call."""
    out = []
    for m in re.finditer(r"waves\." + name + r"\(", src):
        depth, i, args, cur = 1, m.end(), [], ""
        while depth:
            ch = src[i]
            depth += ch in "([{"
            depth -= ch in ")]}"
            if ch == "," and depth == 1:
                args.append(cur.strip())
                cur = ""
            elif depth:
                cur += ch
            i += 1
        out.append([*args, cur.strip()])
    return out


def test_every_qml_presence_ask_passes_the_flag():
    """A QML asker left on the five-argument overload asks as if the flag
    were unknown and so vouches for the other edition the gate refuses."""
    qml = pathlib.Path(__file__).resolve().parents[1] / "waves" / "waves_ui" / "qml" / "Main.qml"
    src = qml.read_text(encoding="utf-8")
    for name in ("libraryAlbumPresence", "libraryTrackPresence"):
        calls = _call_args(src, name)
        assert calls, name
        for args in calls:
            assert len(args) == 6 and "explicit" in args[5], (name, args)


def test_the_album_page_header_carries_the_gates_flag():
    from test_browse_item_prefetch import _bridge as _page_bridge
    from test_browse_item_prefetch import _Cover, _page

    for flag in (True, False, None):
        album = _Cover("album", 5, [])
        album.explicit = flag
        b = _page_bridge(album, "album")
        b.openBrowseItem("album", "5")
        assert _page(b)["header"]["explicit"] is LibraryMixin._release_explicit(album) is flag
    playlist = _Cover("playlist", "p", [])
    b = _page_bridge(playlist, "playlist")
    b.openBrowseItem("playlist", "p")
    assert _page(b)["header"]["explicit"] is None, "only an album page names a release"


# ---- #20: collaboration credits in the album-artist vote -------------------------


def _scan(tmp_path, filemap):
    lib = _mk(tmp_path, "lib", [])
    _mk(tmp_path, "lib/X/Album", sorted(filemap))
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_by_name(filemap))
    idx.refresh(lib)
    return idx


@pytest.mark.parametrize("collab", ["Jay-Z & Kanye West", "Jay-Z, Beyonce", "Jay-Z x Future", "Jay-Z with Rihanna"])
def test_collaborations_led_by_the_artist_keep_the_album_keyed(tmp_path, collab):
    named = ["Jay-Z"] * 9 + [collab] * 3
    filemap = {f"{i:02d}.flac": _tags("Album", artist=a, albumartist="", track_artist=a) for i, a in enumerate(named)}
    idx = _scan(tmp_path, filemap)
    row = next(idx.iter_albums())
    assert not matching.is_various_artists(row["artist"]), collab
    assert idx.presence_facts(presence_key("Album", "Jay-Z")), "the album still answers for its artist"


def test_a_collaboration_sorting_first_still_agrees_with_the_rest(tmp_path):
    named = ["Jay-Z & Kanye West"] * 3 + ["Jay-Z"] * 9
    filemap = {f"{i:02d}.flac": _tags("Album", artist=a, albumartist="", track_artist=a) for i, a in enumerate(named)}
    assert not matching.is_various_artists(next(_scan(tmp_path, filemap).iter_albums())["artist"])


def test_a_compilation_of_collaborations_still_goes_keyless(tmp_path):
    named = ["Queen & David Bowie", "ABBA", "Blondie x Cher", "Devo, Eagles", "Genesis with Heart", "INXS"] * 2
    filemap = {
        f"{i:02d}.flac": _tags("Greatest Hits", artist=a, albumartist="", track_artist=a) for i, a in enumerate(named)
    }
    idx = _scan(tmp_path, filemap)
    assert matching.is_various_artists(next(idx.iter_albums())["artist"])
    assert idx.presence_facts(presence_key("Greatest Hits", "Queen & David Bowie")) == []


def test_the_primary_credit_is_the_first_name():
    assert _primary_credit("jay-z and kanye west") == "jay-z"
    assert _primary_credit("drake x future") == "drake"
    assert _primary_credit("malcolm x") == "malcolm x", "a trailing x names no second artist"
    assert _primary_credit("queen") == "queen"


# ---- #21: the reveal never stats on the GUI thread -------------------------------


def _reveal(monkeypatch, platform, target):
    opened: list = []
    stats: list = []
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(bridge_library.QtGui.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    real_is_dir, real_exists = pathlib.Path.is_dir, pathlib.Path.exists
    monkeypatch.setattr(pathlib.Path, "is_dir", lambda self, *a, **k: stats.append(str(self)) or real_is_dir(self))
    monkeypatch.setattr(pathlib.Path, "exists", lambda self, *a, **k: stats.append(str(self)) or real_exists(self))
    LibraryMixin._reveal_in_file_manager(str(target))
    return opened, stats


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_a_plain_folder_opens_without_a_stat(tmp_path, monkeypatch, platform):
    """A share that stopped answering hangs any stat for its timeout; the
    ancestor walk runs on a worker for exactly that reason, so the GUI-thread
    half must not stat again."""
    target = tmp_path / "gone" / "[2020] Album"  # need not exist: nothing may look
    opened, stats = _reveal(monkeypatch, platform, target)
    assert opened == [str(target)] and stats == []


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_a_package_suffixed_target_shows_its_parent_without_a_stat(tmp_path, monkeypatch, platform):
    target = tmp_path / "Artist" / "Album.app"
    opened, stats = _reveal(monkeypatch, platform, target)
    assert opened == [str(target.parent)] and stats == []


# ---- #23: an old prune marker sweeps once more -----------------------------------


def _recycled_cache(tmp_path, marker):
    lib = _mk(tmp_path, "lib", [])
    real = _mk(tmp_path, "lib/A/[2020] Alpha", ["01.flac"])
    tags = {real: {"album": "Alpha", "artist": "A", "date": "2020"}}
    db = str(tmp_path / "library.sqlite3")

    def read(p):
        return tags.get(os.path.dirname(p))

    idx = LibraryIndex(db, read_tags=read)
    assert idx.refresh(lib) == 1
    idx.close()
    # What an earlier build left behind on a Windows drive root: an album
    # deleted in Explorer, indexed inside the NTFS bin before the rule folded,
    # in a cache already marked swept by that earlier rule.
    binned = os.path.join(lib, "$Recycle.Bin", "S-1-5-21-1", "$R1ABC")
    conn = sqlite3.connect(db)
    (gen,) = conn.execute("SELECT MAX(seen_gen) FROM dirs").fetchone()
    conn.execute(
        "INSERT INTO dirs (path, parent, mtime, listed, is_album, seen_gen) VALUES (?, ?, 0, 1, 1, ?)",
        (binned, os.path.dirname(binned), gen),
    )
    conn.execute("INSERT INTO albums (folder_path, album, artist, year) VALUES (?, 'Gone', 'A', '2019')", (binned,))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('skipped_dirs_pruned', ?)", (marker,))
    conn.commit()
    conn.close()
    return db, read, binned


def test_a_cache_marked_by_the_older_rule_drops_its_recycled_albums(tmp_path):
    db, read, binned = _recycled_cache(tmp_path, "1")
    reopened = LibraryIndex(db, read_tags=read)
    assert [x["title"] for x in reopened.iter_albums()] == ["Alpha"], "the real album stays, the recycled one goes"
    with reopened._lock:
        assert reopened._conn.execute("SELECT COUNT(*) FROM dirs WHERE path = ?", (binned,)).fetchone() == (0,)
        (marker,) = reopened._conn.execute("SELECT value FROM meta WHERE key = 'skipped_dirs_pruned'").fetchone()
    assert marker != "1", "restamped, so the sweep runs once"
    reopened.close()


def test_a_cache_marked_by_the_current_rule_is_not_swept_again(tmp_path):
    db, read, _binned = _recycled_cache(tmp_path, "1")
    LibraryIndex(db, read_tags=read).close()
    conn = sqlite3.connect(db)
    (marker,) = conn.execute("SELECT value FROM meta WHERE key = 'skipped_dirs_pruned'").fetchone()
    # A row planted AFTER the current rule's sweep stays until a scan says
    # otherwise: the open-time sweep is once per rule, not every launch.
    late = os.path.join(str(tmp_path / "lib"), "$Recycle.Bin", "S-1-5-21-1", "$R2DEF")
    (gen,) = conn.execute("SELECT MAX(seen_gen) FROM dirs").fetchone()
    conn.execute(
        "INSERT INTO dirs (path, parent, mtime, listed, is_album, seen_gen) VALUES (?, ?, 0, 1, 1, ?)",
        (late, os.path.dirname(late), gen),
    )
    conn.commit()
    conn.close()
    again = LibraryIndex(db, read_tags=read)
    with again._lock:
        assert again._conn.execute("SELECT COUNT(*) FROM dirs WHERE path = ?", (late,)).fetchone() == (1,)
        assert again._conn.execute("SELECT value FROM meta WHERE key = 'skipped_dirs_pruned'").fetchone() == (marker,)
    again.close()
