"""Tests for waves/mb_service.py: the one door to MusicBrainz that the
library arbiter and the Releases check share.

The promises: both features together never send two requests inside a
second; a foreground request (a pill on screen) takes the next slot ahead
of a waiting background one; a pause MusicBrainz asks for holds every
caller; every answer is taken apart into artists, release groups and
releases, merged across answers; and what one feature learned saves the
other a request (a release length on record skips the arbiter's read, an
artist the arbiter met can spare the Releases check its search).
Hermetic: canned answers, fake clocks, no network.
"""

from __future__ import annotations

import itertools
import json
import sqlite3
import threading
import time
import urllib.parse

from waves import mb_service
from waves import musicbrainz as mb
from waves.mb_arbiter import MBArbiter
from waves.mb_service import MBService, Pacer

WANT = {"title": "Nevermind", "artist": "Nirvana", "year": "", "tracks": 3, "duration": 0}
LOCAL = {"tracks": 3, "runtime": 600}


def _clock():
    t = {"now": 1000.0, "slept": []}

    def monotonic():
        return t["now"]

    def sleep(s):
        t["slept"].append(round(s, 3))
        t["now"] += s

    return t, monotonic, sleep


def _credit(mbid, name):
    return [{"name": name, "artist": {"id": mbid, "name": name, "disambiguation": ""}}]


SEARCH = {
    "releases": [
        {
            "id": "r1",
            "title": "Nevermind",
            "track-count": 3,
            "date": "1991-09-24",
            "artist-credit": _credit("a", "Nirvana"),
            "release-group": {"id": "g1", "title": "Nevermind", "primary-type": "Album"},
        }
    ]
}
READ = {
    "id": "r1",
    "title": "Nevermind",
    "date": "1991-09-24",
    "media": [{"track-count": 3, "tracks": [{"length": 200_000}] * 3}],
}
GROUPS = {
    "release-group-count": 2,
    "release-groups": [
        {"id": "g1", "title": "Nevermind", "first-release-date": "1991-09-24", "primary-type": "Album"},
        {"id": "g2", "title": "Bleach", "first-release-date": "1989-06-15", "primary-type": "Album"},
    ],
}


class _Fetch:
    """Answers by path; logs (url, the clock at send)."""

    def __init__(self, clock=None, **answers):
        self.answers = answers
        self.clock = clock
        self.sent: list = []

    def __call__(self, url):
        self.sent.append((url, self.clock["now"] if self.clock else 0.0))
        path = urllib.parse.urlsplit(url).path
        for frag, answer in self.answers.items():
            if path.endswith(frag.replace("_", "-")):
                return answer if isinstance(answer, tuple) else (200, json.dumps(answer))
        return 404, ""


def _service(tmp_path, fetch, clock=None):
    if clock is None:
        clock, monotonic, sleep = _clock()
    else:
        monotonic, sleep = (lambda: clock["now"]), (lambda s: clock.__setitem__("now", clock["now"] + s))
    pacer = Pacer(sleep=sleep, monotonic=monotonic)
    return MBService(str(tmp_path / "musicbrainz.sqlite3"), fetch=fetch, pacer=pacer, now=lambda: clock["now"]), clock


# --- pacing ----------------------------------------------------------------------


def test_the_arbiter_and_the_releases_check_share_one_second_a_request(tmp_path):
    clock, _m, _s = _clock()
    answers = {"release/": SEARCH, "release/r1": READ, "release_group": GROUPS, "artist/": {"artists": []}}
    fetch = _Fetch(clock, **answers)
    svc, clock = _service(tmp_path, fetch, clock)
    arbiter = MBArbiter(service=svc)
    client = mb.MusicBrainzClient(service=svc)
    assert arbiter.arbitrate(WANT, LOCAL) is True
    assert client.release_groups("a") is not None
    assert client.search_artist("Nirvana") == []
    stamps = [at for _u, at in fetch.sent]
    assert len(stamps) == 4
    assert all(b - a >= 1.0 for a, b in itertools.pairwise(stamps))


def test_a_foreground_request_goes_ahead_of_a_waiting_background_one():
    gate = threading.Event()
    order: list = []
    first = {"held": True}

    def sleep(_s):
        # The first waiter after the opening request blocks here, holding
        # the slot, until both others are queued.
        if first["held"]:
            first["held"] = False
            gate.wait(5)

    pacer = Pacer(min_interval=1.0, sleep=sleep, monotonic=time.monotonic)
    pacer.wait()  # the opening request: the next one has to wait

    def go(tag, fg):
        pacer.wait(foreground=fg)
        order.append(tag)

    holder = threading.Thread(target=go, args=("holder", False))
    holder.start()
    deadline = time.monotonic() + 5
    while first["held"] and time.monotonic() < deadline:
        time.sleep(0.005)
    bg = threading.Thread(target=go, args=("background", False))
    bg.start()
    while pacer.waiting() < 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    fg = threading.Thread(target=go, args=("foreground", True))
    fg.start()
    while pacer.waiting() < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    gate.set()
    for t in (holder, bg, fg):
        t.join(5)
    assert order == ["holder", "foreground", "background"]


def test_a_pause_asked_of_a_foreground_request_holds_everyone(tmp_path):
    fetch = _Fetch(**{"release/": (503, ""), "artist/": {"artists": []}})
    svc, clock = _service(tmp_path, fetch)
    # The page's request gives up at once (its page asks again next view)...
    assert MBArbiter(service=svc).arbitrate(WANT, LOCAL) is None
    assert len(fetch.sent) == 1
    # ...but the background read after it waits the pause out.
    before = clock["now"]
    assert mb.MusicBrainzClient(service=svc).search_artist("Nirvana") == []
    assert clock["now"] - before >= mb_service.BACKOFF_SEC[0]


# --- what an answer teaches --------------------------------------------------------


def test_every_answer_is_put_on_record_and_merged(tmp_path):
    fetch = _Fetch(**{"release/": SEARCH, "release/r1": READ, "release_group": GROUPS})
    svc, _ = _service(tmp_path, fetch)
    assert MBArbiter(service=svc).arbitrate(WANT, LOCAL) is True
    # The search named the artist, the group and the release; the read by id
    # added the length to the same release without losing the group.
    assert svc.release_facts("r1") == {"title": "Nevermind", "date": "1991-09-24", "track_count": 3, "length": 600}
    assert svc.artists_named("Nirvana") == ["a"]
    assert svc.artists_named("NIRVANA") == ["a"]
    mb.MusicBrainzClient(service=svc).release_groups("a")
    counts = svc.counts()
    assert counts["artists"] == 1 and counts["releases"] == 1 and counts["release_groups"] == 2
    conn = svc._conn
    rgid, raw = conn.execute("SELECT rgid, json FROM releases WHERE mbid = 'r1'").fetchone()
    assert rgid == "g1"
    raw = json.loads(raw)
    assert raw["media"][0]["tracks"] and raw["artist-credit"], "the raw JSON is the union of both answers"
    # The browse page filled the group's first-release date the search lacked,
    # and filed both groups under the browsed artist.
    rows = dict(conn.execute("SELECT rgid, first_date FROM release_groups WHERE artist_mbid = 'a'").fetchall())
    assert rows == {"g1": "1991-09-24", "g2": "1989-06-15"}


def test_a_release_length_on_record_saves_the_arbiter_its_read(tmp_path):
    fetch = _Fetch(**{"release/": SEARCH, "release/r1": READ})
    svc, _ = _service(tmp_path, fetch)
    # Some earlier answer (any feature, any URL) read r1 with every length.
    assert svc.get(mb_service.MB_BASE + "/release/r1?inc=recordings&fmt=json") is not None
    fetch.sent.clear()
    assert MBArbiter(service=svc).arbitrate(WANT, LOCAL) is True
    assert [u for u, _t in fetch.sent if "/release/r1" in u] == [], "the length on record answered"
    assert len(fetch.sent) == 1  # the search only


def test_an_artist_the_arbiter_met_spares_the_releases_check_its_search(tmp_path):
    fetch = _Fetch(**{"release/": SEARCH, "release/r1": READ, "release_group": GROUPS})
    svc, _ = _service(tmp_path, fetch)
    assert MBArbiter(service=svc).arbitrate(WANT, LOCAL) is True
    fetch.sent.clear()
    tidal = [
        {"id": "t1", "title": "Nevermind (Remastered)", "day": "1991-09-24"},
        {"id": "t2", "title": "Bleach", "day": "1989-06-15"},
    ]
    found = mb.resolve_artist(mb.MusicBrainzClient(service=svc), "Nirvana", tidal)
    assert found is not None and found[0] == "a"
    assert [u for u, _t in fetch.sent if "/artist/" in u] == [], "no artist search went out"


def test_a_known_artist_that_does_not_prove_itself_still_leaves_the_search_its_budget(tmp_path):
    other = {"release-group-count": 1, "release-groups": [GROUPS["release-groups"][1]]}
    fetch = _Fetch(**{"release/": SEARCH, "release/r1": READ, "release_group": other})
    svc, _ = _service(tmp_path, fetch)
    assert MBArbiter(service=svc).arbitrate(WANT, LOCAL) is True
    tidal = [{"id": "t1", "title": "Nevermind", "day": "1991"}, {"id": "t2", "title": "Bleach", "day": "1989"}]
    fetch.answers["artist/"] = {"artists": [{"id": "a", "name": "Nirvana"}, {"id": "b", "name": "Nirvana"}]}
    # "a" shares one title only: rejected; the search's other namesake is
    # still weighed, "a" not twice.
    assert mb.resolve_artist(mb.MusicBrainzClient(service=svc), "Nirvana", tidal) == ("", [])
    browsed = [
        urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["artist"][0]
        for u, _t in fetch.sent
        if urllib.parse.urlsplit(u).path.endswith("/release-group")
    ]
    assert browsed == ["a", "b"]


# --- caching -------------------------------------------------------------------------


def test_a_page_of_release_groups_is_read_again_after_its_ttl(tmp_path):
    fetch = _Fetch(**{"release_group": GROUPS})
    svc, clock = _service(tmp_path, fetch)
    client = mb.MusicBrainzClient(service=svc)
    client.release_groups("a")
    client.release_groups("a")
    assert len(fetch.sent) == 1
    clock["now"] += mb.PAGE_TTL_SEC + 1
    client.release_groups("a")
    assert len(fetch.sent) == 2


def test_a_not_found_is_an_answer_only_when_the_caller_says_so(tmp_path):
    fetch = _Fetch()
    svc, _ = _service(tmp_path, fetch)
    url = mb_service.MB_BASE + "/release/zz?fmt=json"
    assert svc.get(url) is None
    assert svc.get(url, not_found_answers=True) == {}
    assert svc.get(url, not_found_answers=True) == {} and len(fetch.sent) == 2


def test_a_service_that_keeps_nothing_still_asks(tmp_path):
    fetch = _Fetch(**{"artist/": {"artists": [{"id": "a", "name": "Nirvana"}]}})
    svc = MBService(None, fetch=fetch, pacer=Pacer(sleep=lambda s: None))
    client = mb.MusicBrainzClient(service=svc)
    assert client.search_artist("Nirvana")[0]["mbid"] == "a"
    assert client.search_artist("Nirvana")[0]["mbid"] == "a"
    assert len(fetch.sent) == 2 and svc.artists_named("Nirvana") == [] and not list(tmp_path.iterdir())


def test_a_closed_service_sends_nothing_and_never_reopens_its_file(tmp_path):
    fetch = _Fetch(**{"artist/": {"artists": []}})
    svc, _ = _service(tmp_path, fetch)
    client = mb.MusicBrainzClient(service=svc)
    assert client.search_artist("One") == []
    svc.close()
    for name in list(tmp_path.iterdir()):
        name.unlink()
    assert client.search_artist("Two") is None
    assert svc.release_facts("r1") is None and svc.artists_named("One") == []
    assert len(fetch.sent) == 1 and not list(tmp_path.iterdir())


def test_an_arbiter_handed_the_service_leaves_closing_it_to_its_owner():
    svc = MBService(None, fetch=_Fetch(), pacer=Pacer(sleep=lambda s: None))
    MBArbiter(service=svc).close()
    assert not svc.closed


# --- richer answers, refused extras, and change over time ---------------------------


def test_reads_ask_for_the_extras_and_fall_back_once_they_are_refused(tmp_path):
    seen: list = []

    def fetch(url):
        seen.append(url)
        inc = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("inc", [""])[0]
        return (400, "") if "genres" in inc else (200, json.dumps(GROUPS))

    svc, _ = _service(tmp_path, fetch)
    client = mb.MusicBrainzClient(service=svc)
    assert len(client.release_groups("a")) == 2
    # The page asked for genres and tags, was refused, and went plain.
    assert "genres" in seen[0] and "inc" not in seen[1]
    assert svc.extras("release-group") == "" and svc.extras("release")
    seen.clear()
    client.release_groups("b")
    assert len(seen) == 1 and "inc" not in seen[0], "a refused kind asks plain from then on"


def test_the_release_read_carries_the_extras(tmp_path):
    fetch = _Fetch(**{"release/": SEARCH, "release/r1": READ})
    svc, _ = _service(tmp_path, fetch)
    assert MBArbiter(service=svc).arbitrate(WANT, LOCAL) is True
    read = next(u for u, _t in fetch.sent if "/release/r1" in u)
    inc = urllib.parse.parse_qs(urllib.parse.urlsplit(read).query)["inc"][0]
    assert {"recordings", "genres", "tags", "labels", "url-rels"} <= set(inc.split("+"))


def test_an_edit_on_musicbrainz_is_noticed_and_dated(tmp_path):
    clock = {"now": 1000.0}
    body = {"artists": [{"id": "a", "name": "Nirvana", "score": 100, "genres": [{"name": "grunge"}]}]}
    fetch = _Fetch(clock, **{"artist/": body})
    svc, clock = _service(tmp_path, fetch, clock)
    url = mb_service.MB_BASE + "/artist/?query=x&fmt=json"

    def changed():
        return svc._conn.execute("SELECT changed FROM artists WHERE mbid = 'a'").fetchone()[0]

    svc.get(url, ttl=10)
    first = changed()
    # Asked again with the same facts (only the search score moved): no change.
    clock["now"] += 100
    body["artists"][0]["score"] = 90
    svc.get(url, ttl=10)
    assert changed() == first
    # MusicBrainz gained a genre: the record changes and says when.
    clock["now"] += 100
    body["artists"][0]["genres"] = [{"name": "grunge"}, {"name": "alternative rock"}]
    svc.get(url, ttl=10)
    assert changed() == clock["now"] > first
    raw = json.loads(svc._conn.execute("SELECT json FROM artists WHERE mbid = 'a'").fetchone()[0])
    assert [g["name"] for g in raw["genres"]] == ["grunge", "alternative rock"]


def test_a_definite_arbiter_answer_is_asked_again_after_its_ttl(tmp_path):
    fetch = _Fetch(**{"release/": SEARCH, "release/r1": READ})
    svc, clock = _service(tmp_path, fetch)
    arbiter = MBArbiter(service=svc)
    assert arbiter.arbitrate(WANT, LOCAL) is True
    n = len(fetch.sent)
    assert arbiter.arbitrate(WANT, LOCAL) is True and len(fetch.sent) == n
    clock["now"] += mb_service.DEFINITE_TTL_SEC + 1
    assert arbiter.arbitrate(WANT, LOCAL) is True
    assert len(fetch.sent) > n, "the search is asked again once its answer is old"


# --- audit 3 ------------------------------------------------------------------


def test_a_pause_asked_for_while_a_caller_sleeps_pushes_it_back():
    # Another caller's 429 lands while this one sleeps out its second: the
    # hold must move this caller's slot, not be overwritten by it.
    clock, monotonic, _sleep = _clock()
    pacer = Pacer(monotonic=monotonic)

    def sleep(s):
        if not clock["slept"]:
            pacer.hold(5.0)  # the other caller's pause, mid-sleep
        clock["slept"].append(round(s, 3))
        clock["now"] += s

    pacer._sleep = sleep
    pacer.wait()
    start = clock["now"]
    pacer.wait()
    assert clock["now"] >= start + 5.0


def test_namesakes_met_only_in_searches_name_no_one(tmp_path):
    body = {
        "artists": [
            {"id": "z", "name": "Nirvana", "disambiguation": "60s UK band"},
            {"id": "b", "name": "Nirvana", "disambiguation": "US grunge"},
        ]
    }
    fetch = _Fetch(**{"artist/": body, "release/": SEARCH})
    svc, _ = _service(tmp_path, fetch)
    mb.MusicBrainzClient(service=svc).search_artist("Nirvana")
    assert svc.artists_named("Nirvana") == []
    # A release on record credits one of them: that one leads.
    MBArbiter(service=svc).arbitrate(WANT, LOCAL)
    assert svc.artists_named("Nirvana")[:1] == ["a"]
    # The one artist of a name on record is still named.
    mb.MusicBrainzClient(service=svc).search_artist("Bleach Band")
    lone = {"artists": [{"id": "x", "name": "Lonely", "disambiguation": ""}]}
    svc._remember(mb_service.MB_BASE + "/artist/?q=lonely", json.dumps(lone), lone, ttl=10)
    assert svc.artists_named("Lonely") == ["x"]


def test_a_search_view_and_a_read_by_id_are_not_an_edit(tmp_path):
    clock = {"now": 1000.0}
    svc, clock = _service(tmp_path, _Fetch(clock), clock)

    def changed():
        return svc._conn.execute("SELECT changed FROM releases WHERE mbid = 'r1'").fetchone()[0]

    search = {"releases": [{"id": "r1", "title": "Nevermind", "media": [{"format": "CD", "track-count": 3}]}]}
    svc._remember(mb_service.MB_BASE + "/release/?q=1", json.dumps(search), search, ttl=10)
    first = changed()
    clock["now"] += 100
    read = {"id": "r1", "title": "Nevermind", "media": [{"format": "CD", "track-count": 3, "tracks": [{"id": "t1"}]}]}
    svc._remember(mb_service.MB_BASE + "/release/r1?inc=x", json.dumps(read), read, ttl=10)
    assert changed() == first
    # A vote count moving on a genre is no edit either; a new genre is.
    clock["now"] += 100
    with_genres = {**read, "genres": [{"name": "grunge", "count": 3}]}
    svc._remember(mb_service.MB_BASE + "/release/r1?inc=y", json.dumps(with_genres), with_genres, ttl=10)
    clock["now"] += 100
    recount = {**read, "genres": [{"name": "grunge", "count": 9}]}
    svc._remember(mb_service.MB_BASE + "/release/r1?inc=z", json.dumps(recount), recount, ttl=10)
    assert changed() == first
    clock["now"] += 100
    more = {**read, "genres": [{"name": "grunge", "count": 9}, {"name": "rock", "count": 1}]}
    svc._remember(mb_service.MB_BASE + "/release/r1?inc=w", json.dumps(more), more, ttl=10)
    assert changed() == clock["now"]


def test_a_release_length_on_record_is_read_again_once_it_is_old(tmp_path):
    fetch = _Fetch(**{"release/": SEARCH, "release/r1": READ})
    svc, clock = _service(tmp_path, fetch)
    arbiter = MBArbiter(service=svc)
    assert arbiter.arbitrate(WANT, LOCAL) is True
    assert svc.release_facts("r1", max_age=mb_service.DEFINITE_TTL_SEC)["length"] == 600
    clock["now"] += mb_service.DEFINITE_TTL_SEC + 1
    assert svc.release_facts("r1", max_age=mb_service.DEFINITE_TTL_SEC) is None
    reads = len([u for u, _t in fetch.sent if "/release/r1" in u])
    assert arbiter.arbitrate(WANT, LOCAL) is True
    assert len([u for u, _t in fetch.sent if "/release/r1" in u]) == reads + 1


def test_a_client_switched_off_sends_nothing_more(tmp_path):
    fetch = _Fetch(**{"artist/": {"artists": []}})
    svc, _ = _service(tmp_path, fetch)
    on = {"v": True}
    client = mb.MusicBrainzClient(service=svc, alive=lambda: on["v"])
    assert client.search_artist("Nirvana") == []
    on["v"] = False
    assert client.search_artist("Bleach") is None and client.release_groups("a") is None
    assert len(fetch.sent) == 1


def test_the_old_arbiter_cache_is_removed(tmp_path):
    for suffix in ("", "-wal", "-shm"):
        (tmp_path / ("mbarbiter.sqlite3" + suffix)).write_bytes(b"x")
    (tmp_path / "musicbrainz.sqlite3").write_bytes(b"")
    mb_service.drop_legacy_cache(str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["musicbrainz.sqlite3"]


def test_a_length_on_record_before_lengths_were_dated_counts_from_its_last_read(tmp_path):
    # A file from before the length_at column: its lengths are not all due
    # at once on the first open after the update.
    path = str(tmp_path / "musicbrainz.sqlite3")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE releases (mbid TEXT PRIMARY KEY, rgid TEXT, title TEXT, date TEXT,"
        " track_count INTEGER, length INTEGER, json TEXT, seen REAL, changed REAL)"
    )
    read_at = 1_760_000_000.0
    conn.execute(
        "INSERT INTO releases VALUES ('r1', 'g1', 'Nevermind', '1991-09-24', 3, 600, '{}', ?, ?)", (read_at, read_at)
    )
    conn.commit()
    conn.close()
    svc, clock = _service(tmp_path, _Fetch(), {"now": read_at + 60})
    assert svc.release_facts("r1", max_age=mb_service.DEFINITE_TTL_SEC)["length"] == 600
    clock["now"] = read_at + mb_service.DEFINITE_TTL_SEC + 1
    assert svc.release_facts("r1", max_age=mb_service.DEFINITE_TTL_SEC) is None
