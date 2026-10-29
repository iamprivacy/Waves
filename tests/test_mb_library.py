"""The MusicBrainz library lookup (waves/mb_library.py) and how it and the
Releases check confirm each other.

Promises: the files' own MusicBrainz id settles an artist with no search;
otherwise the Releases check's title rule does; when that is not sure, a
followed TIDAL artist of the same name the Releases check proved settles
it (and the other direction: the library lookup's proof, or the files'
id, settles a follow the Releases check was unsure of), but a proof that
leaned on the asking side never comes back as its own; every settled
artist is read by id with its genres and its release groups paged; an
outcome is on record with when, so the next pass skips what is fresh and
looks again once it is old; a pass stops at the first artist MusicBrainz
could not be asked about. The library side: artist_tag_ids reads the
agreed id from the files' tags.
Hermetic: canned answers, fake clocks, no network.
"""

from __future__ import annotations

import json
import urllib.parse

from tests.test_library_file_tags import _flac
from waves import mb_library
from waves import musicbrainz as mb
from waves.library_index import LibraryIndex, _read_album_tags
from waves.mb_service import MBService, Pacer

A_ID = "11111111-1111-1111-1111-111111111111"
B_ID = "22222222-2222-2222-2222-222222222222"

GROUPS_A = {
    "release-group-count": 2,
    "release-groups": [
        {"id": "g1", "title": "Nevermind", "first-release-date": "1991-09-24", "primary-type": "Album"},
        {"id": "g2", "title": "Bleach", "first-release-date": "1989-06-15", "primary-type": "Album"},
    ],
}
GROUPS_B = {
    "release-group-count": 1,
    "release-groups": [{"id": "h1", "title": "Local Anaesthetic", "primary-type": "Album"}],
}


class _MB:
    """MusicBrainz by path and artist; logs every URL."""

    def __init__(self, artists=(), fail=False):
        self.artists = list(artists)
        self.fail = fail
        self.urls: list = []

    def __call__(self, url):
        self.urls.append(url)
        if self.fail:
            return 503, ""
        parts = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(parts.query)
        if parts.path.endswith("/artist/") or parts.path.endswith("/artist"):
            return 200, json.dumps({"artists": self.artists})
        if "/artist/" in parts.path:
            mbid = parts.path.rsplit("/", 1)[-1]
            return 200, json.dumps({"id": mbid, "name": "Nirvana", "genres": [{"name": "grunge", "count": 9}]})
        if parts.path.endswith("/release-group"):
            return 200, json.dumps(GROUPS_A if q["artist"][0] == A_ID else GROUPS_B)
        return 404, ""

    def asked(self, kind):
        return [u for u in self.urls if kind in urllib.parse.urlsplit(u).path]


def _client(tmp_path, fetch, clock):
    svc = MBService(
        str(tmp_path / "musicbrainz.sqlite3"),
        fetch=fetch,
        pacer=Pacer(sleep=lambda s: None, monotonic=lambda: clock["now"]),
        now=lambda: clock["now"],
    )
    return mb.MusicBrainzClient(service=svc), svc


ARTIST = {"key": "nirvana", "name": "Nirvana", "tracks": 30}
ALBUMS = [{"title": "Nevermind", "year": "1991"}, {"title": "Bleach", "year": "1989"}]


def test_the_files_own_id_settles_the_artist_without_a_search(tmp_path):
    clock = {"now": 1000.0}
    fetch = _MB()
    client, svc = _client(tmp_path, fetch, clock)
    out = mb_library.look_up(client, ARTIST, ALBUMS, tag_id=A_ID)
    assert out == {"mbid": A_ID, "how": "file_tag", "groups": 2}
    assert not [u for u in fetch.urls if "query=" in u], "no search was spent"
    # The artist was read by id with its genres, and its albums kept.
    raw = json.loads(svc._conn.execute("SELECT json FROM artists WHERE mbid = ?", (A_ID,)).fetchone()[0])
    assert raw["genres"][0]["name"] == "grunge"
    assert svc.counts()["release_groups"] == 2
    assert svc.linked("library", "nirvana")["how"] == "file_tag"


def test_the_title_rule_settles_an_artist_with_namesakes(tmp_path):
    clock = {"now": 1000.0}
    fetch = _MB([{"id": B_ID, "name": "Nirvana"}, {"id": A_ID, "name": "Nirvana"}])
    client, _svc = _client(tmp_path, fetch, clock)
    out = mb_library.look_up(client, ARTIST, ALBUMS)
    assert out["mbid"] == A_ID and out["how"] == "match"


def test_a_releases_proof_confirms_what_the_library_rule_could_not(tmp_path):
    clock = {"now": 1000.0}
    # Two namesakes, and the library holds nothing they share two titles with.
    fetch = _MB([{"id": B_ID, "name": "Nirvana"}, {"id": A_ID, "name": "Nirvana"}])
    client, svc = _client(tmp_path, fetch, clock)
    albums = [{"title": "Unreleased Demos", "year": "1988"}, {"title": "Live Somewhere", "year": "1992"}]
    assert mb_library.look_up(client, ARTIST, albums)["mbid"] == ""
    assert svc.linked("library", "nirvana")["how"] == "none"
    # The Releases check proved the followed TIDAL artist of that name.
    svc.link("tidal", "tidal-1", "Nirvana", A_ID, "match")
    out = mb_library.look_up(client, ARTIST, albums)
    assert out == {"mbid": A_ID, "how": "follow", "groups": 2}


def test_a_proof_that_leaned_on_the_asker_is_not_its_own_confirmation(tmp_path):
    clock = {"now": 1000.0}
    fetch = _MB([{"id": B_ID, "name": "Nirvana"}, {"id": A_ID, "name": "Nirvana"}])
    client, svc = _client(tmp_path, fetch, clock)
    # The follow was only settled because the library said so: no loop.
    svc.link("tidal", "tidal-1", "Nirvana", A_ID, "library")
    albums = [{"title": "Unreleased Demos", "year": "1988"}, {"title": "Live Somewhere", "year": "1992"}]
    assert mb_library.look_up(client, ARTIST, albums)["mbid"] == ""
    # Two follows of the name proved to different artists: no way to tell.
    svc.link("tidal", "tidal-2", "Nirvana", A_ID, "match")
    svc.link("tidal", "tidal-3", "Nirvana", B_ID, "match")
    assert mb_library.look_up(client, ARTIST, albums)["mbid"] == ""


def test_the_library_proof_confirms_a_follow_the_releases_check_was_unsure_of(tmp_path):
    clock = {"now": 1000.0}
    fetch = _MB([{"id": B_ID, "name": "Nirvana"}, {"id": A_ID, "name": "Nirvana"}])
    client, svc = _client(tmp_path, fetch, clock)
    tidal = [{"id": "t1", "title": "Some Single"}, {"id": "t2", "title": "Another Single"}]
    assert mb.resolve_artist(client, "Nirvana", tidal) == ("", [])
    svc.link("library", "nirvana", "Nirvana", A_ID, "file_tag")
    found = mb.resolve_artist(
        client, "Nirvana", tidal, confirm=lambda: client.confirmed("library", "Nirvana", refuse=frozenset({"follow"}))
    )
    assert found[0] == A_ID and len(found[1]) == 2


def test_a_pass_skips_what_is_fresh_and_looks_again_once_it_is_old(tmp_path):
    clock = {"now": 1000.0}
    fetch = _MB([{"id": A_ID, "name": "Nirvana"}])
    client, _svc = _client(tmp_path, fetch, clock)
    run = lambda: mb_library.run_pass(
        client, [ARTIST], lambda k: ALBUMS, {}, alive=lambda: True, now=lambda: clock["now"]
    )
    assert run()["looked"] == 1
    assert run()["looked"] == 0
    clock["now"] += mb_library.LOOKUP_AGAIN_S + 1
    assert run()["looked"] == 1


def test_a_pass_stops_when_musicbrainz_cannot_be_asked_and_retries_sooner(tmp_path):
    clock = {"now": 1000.0}
    fetch = _MB([{"id": A_ID, "name": "Nirvana"}], fail=True)
    client, svc = _client(tmp_path, fetch, clock)
    other = {"key": "other", "name": "Other", "tracks": 1}
    out = mb_library.run_pass(
        client, [ARTIST, other], lambda k: ALBUMS, {}, alive=lambda: True, now=lambda: clock["now"]
    )
    assert out["stopped"] and out["looked"] == 0
    assert svc.linked("library", "nirvana")["how"] == "retry" and svc.linked("library", "other") is None
    fetch.fail = False
    clock["now"] += mb_library.LOOKUP_RETRY_S + 1
    out = mb_library.run_pass(
        client, [ARTIST, other], lambda k: ALBUMS, {}, alive=lambda: True, now=lambda: clock["now"]
    )
    assert out["looked"] == 2


def test_a_pass_ends_when_it_is_told_to(tmp_path):
    clock = {"now": 1000.0}
    fetch = _MB([{"id": A_ID, "name": "Nirvana"}])
    client, _svc = _client(tmp_path, fetch, clock)
    out = mb_library.run_pass(client, [ARTIST], lambda k: ALBUMS, {}, alive=lambda: False)
    assert out["stopped"] and fetch.urls == []


def test_a_lookup_switched_off_midway_leaves_no_retry_on_record(tmp_path):
    # The setting goes off while an artist is being asked about: the call
    # refused for that is no failure, and the artist stays never looked at.
    clock = {"now": 1000.0}
    fetch = _MB([{"id": A_ID, "name": "Nirvana"}])
    _client_on, svc = _client(tmp_path, fetch, clock)
    calls = []

    def alive():
        calls.append(1)
        return len(calls) == 1

    client = mb.MusicBrainzClient(service=svc, alive=alive)
    out = mb_library.run_pass(client, [ARTIST], lambda k: ALBUMS, {}, alive=alive, now=lambda: clock["now"])
    assert out["stopped"] and fetch.urls == []
    assert svc.linked("library", "nirvana") is None


def _tagged(folder, ids):
    folder.mkdir(parents=True)
    for n, mbid in enumerate(ids):
        path = folder / f"0{n}.flac"
        tags = {"title": f"T{n}", "album": folder.name, "albumartist": "Nirvana", "artist": "Nirvana"}
        if mbid is not None:
            tags["musicbrainz_albumartistid"] = mbid
        _flac(str(path), **tags)


def test_artist_tag_ids_reads_the_id_the_files_agree_on(tmp_path):
    lib = tmp_path / "lib"
    _tagged(lib / "Nirvana" / "Nevermind", [A_ID, A_ID, None])
    _tagged(lib / "Nirvana" / "Bleach", [A_ID])
    _tagged(lib / "Split" / "Mixed", [A_ID, B_ID])
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_read_album_tags)
    idx.refresh(str(lib))
    ids = idx.artist_tag_ids()
    # The "Split" folder is filed under Nirvana too and its files name two
    # artists: Nirvana's files no longer agree, so nothing is claimed.
    assert ids == {}
    # Without it they agree (a file without the tag says nothing).
    with idx._lock:
        idx._conn.execute("DELETE FROM file_tags WHERE folder_path LIKE ?", ("%Mixed%",))
        idx._conn.commit()
    assert idx.artist_tag_ids() == {"nirvana": A_ID}
    idx.close()


def test_a_collaboration_id_says_nothing(tmp_path):
    lib = tmp_path / "lib"
    _tagged(lib / "Nirvana" / "Duets", [f"{A_ID}; {B_ID}"])
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_read_album_tags)
    idx.refresh(str(lib))
    assert idx.artist_tag_ids() == {}
    idx.close()


class _Bridge:
    """The bridge's lookup methods over a stub: prefs, a published cache."""

    from waves.waves_ui.bridge_library import LibraryMixin as _L

    _mb_lookup_on = _L._mb_lookup_on
    _mb_lookup_kick = _L._mb_lookup_kick
    _mb_lookup_stop = _L._mb_lookup_stop
    _mb_lookup_run = _L._mb_lookup_run

    def __init__(self, prefs, fetch):
        import types

        self.prefs = prefs
        self.fetch = fetch
        self.built = 0
        cache = types.SimpleNamespace(
            distinct_artists=lambda: [ARTIST],
            artist_albums=lambda keys: {"nirvana": ALBUMS},
            artist_tag_ids=lambda: {},
        )
        self._library_index = types.SimpleNamespace(cache=cache)

    def _waves_pref_bool(self, key):
        return bool(self.prefs.get(key))

    def _mb_service_instance(self):
        self.built += 1
        return MBService(None, fetch=self.fetch, pacer=Pacer(sleep=lambda s: None))


def test_the_lookup_sends_nothing_unless_both_switches_are_on():
    for prefs in ({}, {"library_enabled": True}, {"library_mb_lookup": True}):
        fetch = _MB([{"id": A_ID, "name": "Nirvana"}])
        bridge = _Bridge(prefs, fetch)
        bridge._mb_lookup_kick()
        assert bridge.built == 0 and getattr(bridge, "_mb_lookup_thread", None) is None and fetch.urls == []


def test_the_lookup_runs_one_pass_on_its_own_thread_and_stops_when_told():
    fetch = _MB([{"id": A_ID, "name": "Nirvana"}])
    bridge = _Bridge({"library_enabled": True, "library_mb_lookup": True}, fetch)
    bridge._mb_lookup_kick()
    bridge._mb_lookup_thread.join(5)
    assert bridge.built == 1 and any("release-group" in u for u in fetch.urls)
    # Stopped before it starts: the generation moved, nothing more is asked.
    fetch.urls.clear()
    bridge._mb_lookup_stop()
    bridge._mb_lookup_run(mb.MusicBrainzClient(service=bridge._mb_service_instance()), bridge._library_index.cache, 0)
    assert fetch.urls == []


def test_turning_the_lookup_off_and_on_again_starts_a_new_pass():
    import threading

    release = threading.Event()
    fetch = _MB([{"id": A_ID, "name": "Nirvana"}])
    bridge = _Bridge({"library_enabled": True, "library_mb_lookup": True}, fetch)
    real_run = bridge._mb_lookup_run

    def slow_run(client, cache, gen):
        release.wait(5)
        real_run(client, cache, gen)

    bridge._mb_lookup_run = slow_run
    bridge._mb_lookup_kick()
    first = bridge._mb_lookup_thread
    bridge._mb_lookup_stop()
    bridge._mb_lookup_kick()
    assert bridge._mb_lookup_thread is not first
    release.set()
    first.join(5)
    bridge._mb_lookup_thread.join(5)
