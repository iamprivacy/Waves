"""The Genre tag from MusicBrainz (waves/genres.py).

What is pinned: every genre MusicBrainz lists for the release reaches the
tag, best voted first, in the user's separator; the artist's genres stand in
when the release has none; a release is matched by barcode before its title;
a match goes on record under the TIDAL album id so the next job asks nothing;
a release MusicBrainz cannot match is remembered for a day; a service that
cannot be asked puts nothing on record; and the tag step never waits past
its budget. The engine side: the lookup leaves when a track passes its skip
check, the tag step joins what comes back, and nothing happens at all with
the setting off.
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import mutagen.flac
import mutagen.mp4

from waves import genres as genres_mod
from waves.download import Download
from waves.genres import SCOPE, GenreLookup, genre_label, join_genres
from waves.mb_service import NEGATIVE_TTL_SEC, MBService, Pacer
from waves.metadata import Metadata

RGID = "11111111-1111-1111-1111-111111111111"
ARTIST = "22222222-2222-2222-2222-222222222222"
UPC = "0602498123456"


# ---- labels and joining ----------------------------------------------------------
def test_genre_labels_read_like_tags():
    assert genre_label("hip hop") == "Hip Hop"
    assert genre_label("drum and bass") == "Drum and Bass"
    assert genre_label("uk garage") == "UK Garage"
    assert genre_label("r&b") == "R&B"
    assert genre_label("post-rock") == "Post-Rock"


def test_join_uses_the_separator_and_drops_duplicates():
    assert join_genres(["house", "deep house", "House"], "; ") == "House; Deep House"
    assert join_genres(["house", "techno"], " / ") == "House / Techno"
    assert join_genres(["house", "techno"], "") == "House; Techno", "an emptied separator falls back to the default"
    assert join_genres([], "; ") == ""


# ---- the lookup against a canned MusicBrainz ------------------------------------
class _MB:
    """A MusicBrainz that answers from a table keyed by a URL substring, and
    remembers every URL it was asked."""

    def __init__(self, answers: dict[str, dict], *, down: bool = False):
        self.answers = answers
        self.down = down
        self.urls: list[str] = []

    def __call__(self, url: str) -> tuple[int, str]:
        self.urls.append(url)
        if self.down:
            raise OSError("no network")
        for needle, body in self.answers.items():
            if needle in url:
                return 200, json.dumps(body)
        return 404, ""


def _release(rgid: str = RGID, *, barcode: str = UPC, title: str = "Album"):
    return {"id": "r-" + rgid, "title": title, "barcode": barcode, "release-group": {"id": rgid}}


def _group(genres, artist: str = ARTIST):
    return {"id": RGID, "genres": genres, "artist-credit": [{"artist": {"id": artist}}]}


_ANSWERS = {
    "query=barcode": {"releases": [_release()]},
    "query=release": {"releases": [_release(title="Album")]},
    f"release-group/{RGID}": _group([{"name": "techno", "count": 2}, {"name": "house", "count": 5}]),
    f"artist/{ARTIST}": {"id": ARTIST, "genres": [{"name": "electronic", "count": 9}]},
}


def _lookup(tmp_path, answers=_ANSWERS, *, now=None, down=False) -> tuple[GenreLookup, _MB]:
    fetch = _MB(answers, down=down)
    clock = now or (lambda: 1_000_000.0)
    svc = MBService(str(tmp_path / "mb.sqlite3"), fetch=fetch, pacer=Pacer(sleep=lambda s: None), now=clock)
    return GenreLookup(svc, now=clock), fetch


def test_every_voted_genre_comes_back_best_first(tmp_path):
    lookup, fetch = _lookup(tmp_path)
    assert lookup.genres_for("a1", upc=UPC, artist="Artist", title="Album") == ["house", "techno"]
    assert [u for u in fetch.urls if "query=barcode" in u], "the barcode is asked first"
    assert not [u for u in fetch.urls if "query=release" in u], "a barcode hit needs no title search"


def test_a_match_goes_on_record_so_the_next_job_asks_nothing(tmp_path):
    lookup, fetch = _lookup(tmp_path)
    lookup.genres_for("a1", upc=UPC, artist="Artist", title="Album")
    asked = len(fetch.urls)
    assert lookup._svc.linked(SCOPE, "a1")["mbid"] == RGID
    again, _ = _lookup(tmp_path, {})  # a fresh job over the same file, MusicBrainz now answering nothing
    assert again.genres_for("a1", upc=UPC, artist="Artist", title="Album") == ["house", "techno"]
    assert len(fetch.urls) == asked


def test_the_title_search_stands_in_for_a_missing_barcode(tmp_path):
    lookup, fetch = _lookup(tmp_path)
    assert lookup.genres_for("a2", upc="", artist="Artist", title="Album (Deluxe Edition)") == ["house", "techno"]
    assert [u for u in fetch.urls if "query=release" in u]
    assert lookup._svc.linked(SCOPE, "a2")["how"] == "search"


def test_the_artists_genres_stand_in_when_the_release_has_none(tmp_path):
    answers = {**_ANSWERS, f"release-group/{RGID}": _group([])}
    lookup, _ = _lookup(tmp_path, answers)
    assert lookup.genres_for("a1", upc=UPC, artist="Artist", title="Album") == ["electronic"]


def test_an_unmatched_release_is_remembered_for_a_day(tmp_path):
    clock = {"t": 1_000_000.0}
    lookup, fetch = _lookup(
        tmp_path, {"query=barcode": {"releases": []}, "query=release": {"releases": []}}, now=lambda: clock["t"]
    )
    assert lookup.genres_for("a3", upc=UPC, artist="Artist", title="Album") == []
    assert lookup._svc.linked(SCOPE, "a3") == {"mbid": "", "how": "none", "at": 1_000_000.0}
    asked = len(fetch.urls)
    assert lookup.genres_for("a3", upc=UPC, artist="Artist", title="Album") == []
    assert len(fetch.urls) == asked, "asked again within the day"
    clock["t"] += NEGATIVE_TTL_SEC + 1
    lookup.genres_for("a3", upc=UPC, artist="Artist", title="Album")
    assert len(fetch.urls) > asked, "a day later the release is asked about again"


def test_a_service_that_cannot_be_asked_records_nothing(tmp_path):
    lookup, _ = _lookup(tmp_path, down=True)
    assert lookup.genres_for("a1", upc=UPC, artist="Artist", title="Album") == []
    assert lookup._svc.linked(SCOPE, "a1") is None, "a blip must not stick as 'no match'"


def test_a_title_that_does_not_match_is_no_match(tmp_path):
    lookup, _ = _lookup(tmp_path, {"query=release": {"releases": [_release(title="Something Else")]}})
    assert lookup.genres_for("a4", upc="", artist="Artist", title="Album") == []
    assert lookup._svc.linked(SCOPE, "a4")["how"] == "none"


# ---- prefetch and the wait budget -------------------------------------------------
def test_prefetch_asks_once_per_release_and_the_tag_step_takes_the_answer(tmp_path):
    lookup, fetch = _lookup(tmp_path)
    for _ in range(3):
        lookup.prefetch("a1", upc=UPC, artist="Artist", title="Album")
    assert lookup.genres("a1", upc=UPC, artist="Artist", title="Album", wait=5) == ["house", "techno"]
    assert len(lookup._answers) == 1
    assert sum("query=barcode" in u for u in fetch.urls) == 1


def test_the_tag_step_never_waits_past_its_budget():
    release = threading.Event()

    def _slow(url):
        release.wait(5)
        return 404, ""

    lookup = GenreLookup(fetch=_slow, pacer=Pacer(sleep=lambda s: None))
    try:
        assert lookup.genres("a1", upc=UPC, artist="Artist", title="Album", wait=0.05) == []
    finally:
        release.set()


# ---- the tag writer ----------------------------------------------------------------
_UPC_TARGET = {"FLAC": "UPC", "MP4": "UPC", "MP3": "UPC"}


def _flac_stub():
    fake = mutagen.flac.FLAC.__new__(mutagen.flac.FLAC)
    fake.tags = None
    fake.metadata_blocks = []
    fake.save = lambda *a, **k: None
    return fake


def _mp4_stub():
    fake = mutagen.mp4.MP4.__new__(mutagen.mp4.MP4)
    fake.tags = None
    fake.save = lambda *a, **k: None
    return fake


def _write(stub, tmp_path, name, **kw):
    file = tmp_path / name
    file.write_bytes(b"x")
    with patch("waves.metadata.mutagen.File", return_value=stub):
        meta = Metadata(path_file=file, target_upc=_UPC_TARGET, title="Song", artists=[], albumartist=[], **kw)
        assert meta.save() is True
    return stub


def test_the_genre_reaches_flac_and_mp4(tmp_path):
    flac = _write(_flac_stub(), tmp_path, "a.flac", genre="House; Techno")
    assert flac.tags["GENRE"] == ["House; Techno"]
    mp4 = _write(_mp4_stub(), tmp_path, "a.m4a", genre="House; Techno")
    assert mp4.tags["\xa9gen"] in ("House; Techno", ["House; Techno"])


def test_no_genre_writes_no_genre_tag(tmp_path):
    flac = _write(_flac_stub(), tmp_path, "a.flac")
    assert "GENRE" not in flac.tags
    mp4 = _write(_mp4_stub(), tmp_path, "a.m4a")
    assert "\xa9gen" not in mp4.tags


# ---- the engine side -----------------------------------------------------------------
def _track(album_id="a1"):
    album = SimpleNamespace(id=album_id, name="Album", upc=UPC, artist=SimpleNamespace(name="Artist"), artists=[])
    return SimpleNamespace(id="t1", album=album, artists=[])


def _engine(lookup) -> Download:
    dl = Download.__new__(Download)
    dl.genre_lookup = lookup
    dl.settings = SimpleNamespace(data=SimpleNamespace(metadata_delimiter_genre=" / "))
    return dl


def test_the_engine_asks_with_the_releases_facts_and_joins_the_answer(monkeypatch):
    monkeypatch.setattr("waves.download.get_album_artists", lambda media: ["Artist"])
    lookup = MagicMock()
    lookup.genres.return_value = ["house", "techno"]
    dl = _engine(lookup)
    track = _track()
    with patch("waves.download.Track", SimpleNamespace):
        dl._genre_prefetch(track)
        assert dl._genre_text(track) == "House / Techno"
    lookup.prefetch.assert_called_once_with("a1", upc=UPC, artist="Artist", title="Album")
    lookup.genres.assert_called_once_with("a1", upc=UPC, artist="Artist", title="Album")


def test_the_engine_does_nothing_without_a_lookup_or_an_album():
    dl = _engine(None)
    with patch("waves.download.Track", SimpleNamespace):
        assert dl._genre_text(_track()) == ""
        dl.genre_lookup = MagicMock()
        assert dl._genre_text(SimpleNamespace(id="t1", album=None, artists=[])) == ""
        dl.genre_lookup.genres.assert_not_called()


def test_the_default_separator_is_a_semicolon():
    from waves.model.cfg import Settings

    assert Settings().metadata_delimiter_genre == "; "
    assert Settings().metadata_genre is False, "opt-in: it sends release facts to musicbrainz.org"
    assert genres_mod.DEFAULT_GENRE_DELIMITER == "; "


# ---- the artist fallback for a release MusicBrainz does not list yet ---------------
_UNLISTED = {
    "query=barcode": {"releases": []},
    "query=release": {"releases": []},
    "artist/?query": {"artists": [{"id": ARTIST, "name": "Artist", "score": 100}]},
    f"artist/{ARTIST}": {"id": ARTIST, "genres": [{"name": "electronic", "count": 9}]},
}


def test_an_unlisted_release_gets_the_artists_genres_with_the_fallback_on(tmp_path):
    lookup, fetch = _lookup(tmp_path, _UNLISTED)
    lookup._artist_fallback = True
    assert lookup.genres_for("a5", upc=UPC, artist="Artist", title="Fresh Single") == ["electronic"]
    assert lookup._svc.linked(SCOPE, "a5")["how"] == "none", "the release itself stays unmatched"
    asked = len(fetch.urls)
    assert lookup.genres_for("a5", upc=UPC, artist="Artist", title="Fresh Single") == ["electronic"]
    assert not [u for u in fetch.urls[asked:] if "query=barcode" in u or "query=release" in u]


def test_the_fallback_is_off_by_the_setting_and_live(tmp_path):
    switch = {"on": False}
    lookup, fetch = _lookup(tmp_path, _UNLISTED)
    lookup._artist_fallback = lambda: switch["on"]
    assert lookup.genres_for("a6", upc=UPC, artist="Artist", title="Fresh Single") == []
    assert not [u for u in fetch.urls if "artist/" in u], "off: the artist is never asked about"
    switch["on"] = True
    assert lookup.genres_for("a6", upc=UPC, artist="Artist", title="Fresh Single") == ["electronic"]


def test_namesakes_get_no_guess(tmp_path):
    two = {**_UNLISTED, "artist/?query": {"artists": [{"id": ARTIST, "name": "Artist"}, {"id": "x", "name": "Artist"}]}}
    lookup, _ = _lookup(tmp_path, two)
    lookup._artist_fallback = True
    assert lookup.genres_for("a7", upc=UPC, artist="Artist", title="Fresh Single") == []
    other = {**_UNLISTED, "artist/?query": {"artists": [{"id": "x", "name": "Artist Two", "score": 60}]}}
    lookup, _ = _lookup(tmp_path, other)
    lookup._artist_fallback = True
    assert lookup.genres_for("a8", upc=UPC, artist="Artist", title="Fresh Single") == [], "a near name is no match"


def _on_record(svc: MBService, mbid: str, name: str, genres: list[dict]) -> None:
    """What the library lookup leaves: the artist read by id, genres and all."""
    body = {"id": mbid, "name": name, "genres": genres}
    svc._remember(f"https://musicbrainz.org/ws/2/artist/{mbid}?inc=aliases", json.dumps(body), body, ttl=None)


def test_the_library_lookups_artist_answers_the_fallback_without_asking(tmp_path):
    # Two artists share the name on MusicBrainz, so the name search alone
    # would give up; the library lookup proved which one the library holds.
    two = {**_UNLISTED, "artist/?query": {"artists": [{"id": ARTIST, "name": "Artist"}, {"id": "x", "name": "Artist"}]}}
    lookup, fetch = _lookup(tmp_path, two)
    lookup._artist_fallback = True
    _on_record(
        lookup._svc, "lib-artist", "Artist", [{"name": "shoegaze", "count": 4}, {"name": "dream pop", "count": 2}]
    )
    lookup._svc.link("library", "artist", "Artist", "lib-artist", "match")

    assert lookup.genres_for("a9", upc=UPC, artist="Artist", title="Fresh Single") == ["shoegaze", "dream pop"]
    assert not [u for u in fetch.urls if "/artist" in u], "the record answers: no artist search, no artist read"


def test_the_releases_checks_proof_stands_in_too(tmp_path):
    lookup, fetch = _lookup(tmp_path, _UNLISTED)
    lookup._artist_fallback = True
    _on_record(lookup._svc, "followed", "Artist", [{"name": "ambient", "count": 1}])
    lookup._svc.link("tidal", "123", "Artist", "followed", "titles")

    assert lookup.genres_for("a10", upc=UPC, artist="Artist", title="Fresh Single") == ["ambient"]
    assert not [u for u in fetch.urls if "/artist" in u]


def test_a_proved_artist_without_genres_on_record_is_read_once(tmp_path):
    lookup, fetch = _lookup(tmp_path, _UNLISTED)
    lookup._artist_fallback = True
    lookup._svc.link("library", "artist", "Artist", ARTIST, "match")

    assert lookup.genres_for("a11", upc=UPC, artist="Artist", title="Fresh Single") == ["electronic"]
    assert not [u for u in fetch.urls if "artist/?query" in u], "proved: no name search"


def test_the_fallback_setting_defaults_on_and_rides_under_the_genre_switch():
    from waves.model.cfg import Settings

    assert Settings().metadata_genre_artist_fallback is True


def test_a_waiting_track_goes_first_and_a_given_up_lookup_is_dropped():
    # Three releases queued; the third one's track reaches the tag step
    # first, so it is asked first. The first one's track gave up before its
    # turn, so its lookup never spends a request.
    gate, busy = threading.Event(), threading.Event()
    asked: list[str] = []

    def _fetch(url):
        busy.set()
        gate.wait(5)
        asked.append(url)
        if "/release/?" in url:
            title = "Album 3" if "Album+3" in url else "Album 1" if "Album+1" in url else "Album 2"
            return 200, json.dumps({"releases": [_release(rgid=title, title=title)]})
        return 200, json.dumps(_group([{"name": "house", "count": 2}]))

    lookup = GenreLookup(fetch=_fetch, pacer=Pacer(sleep=lambda s: None))
    lookup.prefetch("a1", artist="Artist", title="Album 1")
    assert busy.wait(5)
    for n in (2, 3):
        lookup.prefetch(f"a{n}", artist="Artist", title=f"Album {n}")
    # Album 1 is taken at once and holds the worker on the gate; its track
    # gives up. Album 2's track gives up while still queued.
    assert lookup.genres("a2", artist="Artist", title="Album 2", wait=0.05) == []
    threading.Timer(0.2, gate.set).start()
    assert lookup.genres("a3", artist="Artist", title="Album 3", wait=5) == ["house"]
    assert not any("Album+2" in u for u in asked), "nobody waits on Album 2 any more"


def test_a_lone_release_keeps_its_full_wait():
    # An album job: one release in flight, so its first track waits for it.
    gate = threading.Event()

    def _fetch(url):
        gate.wait(5)
        if "/release/?" in url:
            return 200, json.dumps({"releases": [_release()]})
        return 200, json.dumps(_group([{"name": "house", "count": 2}]))

    lookup = GenreLookup(fetch=_fetch, pacer=Pacer(sleep=lambda s: None))
    threading.Timer(0.2, gate.set).start()
    assert lookup.genres("a1", upc=UPC, artist="Artist", title="Album", wait=5) == ["house"]
