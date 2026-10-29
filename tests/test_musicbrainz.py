"""Tests for waves/musicbrainz.py: the MusicBrainz side of the Releases
pane, driven through a fake transport (no network, no real sleep).

The promises: one request a second through a monotonic pacer; a 429 or
503 holds the pacer and goes again a few times, anything else is a
transient None;
only albums, EPs and singles with no secondary type survive the release
group read, which pages a catalog whole or returns nothing; an artist is
accepted only on a name match plus two shared titles (a namesake with
none is rejected), a thin follow only on a lone exact name; release groups
match TIDAL rows by title key and year within one.
"""

from __future__ import annotations

import json
import urllib.parse

from waves import musicbrainz as mb


def _clock():
    t = {"now": 1000.0, "slept": []}

    def monotonic():
        return t["now"]

    def sleep(s):
        t["slept"].append(round(s, 3))
        t["now"] += s

    return t, monotonic, sleep


class _Transport:
    """Canned answers, popped in order per endpoint path; the urls asked."""

    def __init__(self, **by_path):
        self.by_path = {k.replace("_", "-"): list(v) for k, v in by_path.items()}
        self.urls: list = []

    def __call__(self, url):
        self.urls.append(url)
        path = (
            urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
            or urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
        )
        answers = self.by_path.get(path) or self.by_path.get(path.rstrip("/"))
        if not answers:
            return 404, ""
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return status, body if isinstance(body, str) else json.dumps(body)


def _query(url) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def _client(transport):
    t, monotonic, sleep = _clock()
    pacer = mb.Pacer(sleep=sleep, monotonic=monotonic)
    client = mb.MusicBrainzClient(fetch=transport, pacer=pacer, user_agent="Waves/test (https://getwaves.dev)")
    return client, t


def _group(rgid, title, date="2020-01-01", primary="Album", secondary=()):
    g = {"id": rgid, "title": title, "first-release-date": date, "primary-type": primary}
    if secondary:
        g["secondary-types"] = list(secondary)
    return g


# --- pacing and backoff --------------------------------------------------------


def test_requests_are_paced_a_second_apart_on_the_monotonic_clock():
    tr = _Transport(artist=[(200, {"artists": []})] * 3)
    client, t = _client(tr)
    assert client.search_artist("One") == []
    assert client.search_artist("Two") == []
    # The first went at once; the second waited the full second.
    assert t["slept"] == [1.0]
    t["now"] += 5
    assert client.search_artist("Three") == []
    assert t["slept"] == [1.0]
    assert len(tr.urls) == 3


def test_the_pacer_is_shared_across_clients_by_default():
    assert mb.MusicBrainzClient(fetch=lambda u: (200, "{}"))._pacer is mb.shared_pacer()


def test_a_pause_asked_for_is_waited_out_then_the_request_goes_again():
    tr = _Transport(artist=[(503, ""), (429, ""), (200, {"artists": [{"id": "m1", "name": "One", "score": 100}]})])
    client, t = _client(tr)
    rows = client.search_artist("One")
    assert [r["mbid"] for r in rows] == ["m1"]
    # The backoff pauses, and no extra pacer wait: the pause already covered it.
    assert t["slept"] == [2.0, 5.0]
    assert len(tr.urls) == 3


def test_a_pause_that_never_lifts_gives_up_as_transient():
    tr = _Transport(artist=[(503, "")] * 6)
    client, t = _client(tr)
    assert client.search_artist("One") is None
    assert len(tr.urls) == 1 + len(mb.BACKOFF_SEC)
    assert t["slept"] == list(mb.BACKOFF_SEC)


def test_other_failures_are_transient_and_never_retried():
    for answer in ((500, ""), (404, ""), (200, "not json"), (200, "[]"), RuntimeError("offline")):
        tr = _Transport(artist=[answer])
        client, _t = _client(tr)
        assert client.search_artist("One") is None
        assert len(tr.urls) == 1


# --- the reads -----------------------------------------------------------------


def test_search_artist_asks_by_name_and_maps_the_candidates():
    tr = _Transport(
        artist=[
            (
                200,
                {
                    "artists": [
                        {"id": "m1", "name": "Nirvana", "disambiguation": "90s US grunge band", "score": 100},
                        {"id": "m2", "name": "Nirvana", "disambiguation": "60s band from the UK", "score": "87"},
                        {"name": "no id"},
                    ]
                },
            )
        ]
    )
    client, _t = _client(tr)
    rows = client.search_artist('Nir"vana')
    assert rows == [
        {"mbid": "m1", "name": "Nirvana", "disambiguation": "90s US grunge band", "score": 100},
        {"mbid": "m2", "name": "Nirvana", "disambiguation": "60s band from the UK", "score": 87},
    ]
    q = _query(tr.urls[0])
    assert tr.urls[0].startswith(mb.MB_BASE + "/artist/?")
    assert q == {"query": 'artist:"Nir vana"', "fmt": "json", "limit": str(mb.SEARCH_LIMIT)}
    # A blank name asks nothing.
    assert client.search_artist("  ") == [] and len(tr.urls) == 1


def test_release_groups_page_the_catalog_whole_and_keep_only_plain_albums_eps_and_singles():
    odd = [
        _group("g1", "Bleach", "1989-06-15"),
        _group("g2", "Sliver", "1990-09", "Single"),
        _group("g3", "Hormoaning", "1992-02-05", "EP"),
        _group("g4", "From the Muddy Banks", "1996-10-01", secondary=["Live"]),
        _group("g5", "Nirvana", "2002-10-29", secondary=["Compilation"]),
        _group("g6", "Some Broadcast", "2001", "Broadcast"),
        _group("g7", "Other", "2001", "Other"),
        {"id": "g8", "title": "No type"},
        {"title": "No id", "primary-type": "Album"},
    ]
    # A full first page (padded with plain albums) and a short second one.
    pad = [_group(f"p{i}", f"Pad {i}", "2000-01-01") for i in range(mb.PAGE - len(odd))]
    page1 = {"release-group-count": mb.PAGE + 1, "release-groups": [*odd, *pad]}
    page2 = {"release-group-count": mb.PAGE + 1, "release-groups": [_group("g9", "In Utero", "1993-09-13T00:00:00")]}
    tr = _Transport(release_group=[(200, page1), (200, page2)])
    client, _t = _client(tr)
    rows = client.release_groups("m1")
    assert [r for r in rows if not r["rgid"].startswith("p")] == [
        {"rgid": "g1", "title": "Bleach", "date": "1989-06-15", "rtype": "album"},
        {"rgid": "g2", "title": "Sliver", "date": "1990-09", "rtype": "single"},
        {"rgid": "g3", "title": "Hormoaning", "date": "1992-02-05", "rtype": "ep"},
        {"rgid": "g9", "title": "In Utero", "date": "1993-09-13", "rtype": "album"},
    ]
    assert len(rows) == 4 + len(pad)
    assert [(_query(u)["offset"], _query(u)["limit"]) for u in tr.urls] == [("0", "100"), ("100", "100")]
    assert all(_query(u)["artist"] == "m1" and _query(u)["type"] == "album|ep|single" for u in tr.urls)
    assert all(u.startswith(mb.MB_BASE + "/release-group?") for u in tr.urls)


def test_release_groups_stop_on_a_short_page_and_an_empty_catalog():
    tr = _Transport(release_group=[(200, {"release-group-count": 500, "release-groups": [_group("g1", "One")]})])
    client, _t = _client(tr)
    assert [r["rgid"] for r in client.release_groups("m1")] == ["g1"]
    assert len(tr.urls) == 1
    tr = _Transport(release_group=[(200, {"release-group-count": 0, "release-groups": []})])
    client, _t = _client(tr)
    assert client.release_groups("m1") == [] and len(tr.urls) == 1
    assert client.release_groups("") == [] and len(tr.urls) == 1


def test_a_failed_page_drops_the_whole_read():
    tr = _Transport(
        release_group=[
            (200, {"release-group-count": 200, "release-groups": [_group(f"g{i}", f"T{i}") for i in range(100)]}),
            (500, ""),
        ]
    )
    client, _t = _client(tr)
    assert client.release_groups("m1") is None


def test_the_page_cap_bounds_a_bottomless_catalog():
    page = {"release-group-count": 10**6, "release-groups": [_group(f"g{i}", f"T{i}") for i in range(100)]}
    tr = _Transport(release_group=[(200, page)] * (mb.MAX_PAGES + 5))
    client, _t = _client(tr)
    assert len(client.release_groups("m1")) == 100 * mb.MAX_PAGES
    assert len(tr.urls) == mb.MAX_PAGES


# --- matching ------------------------------------------------------------------


def test_title_key_strips_editions_features_and_punctuation():
    assert mb.title_key("Hollow (Deluxe Edition)") == "hollow"
    assert mb.title_key("Hollow [Remastered 2011] (feat. Someone)") == "hollow"
    assert mb.title_key("Guns N' Roses & Friends") == "guns n roses and friends"
    assert mb.title_key("Café: Live!  ") == "cafe live"
    assert mb.title_key("(What's the Story) Morning Glory?") == "morning glory"
    assert mb.title_key("Round (Deluxe (Extra))") == "round"
    assert mb.title_key("( )") == "" and mb.title_key("") == "" and mb.title_key(None) == ""


def test_match_groups_pairs_by_title_key_and_year_within_one():
    tidal = [
        {"id": "t1", "title": "Hollow (Deluxe)", "day": "2020-03-03", "rtype": "album"},
        {"id": "t2", "title": "Hollow", "day": "2020-03-03", "rtype": "single"},
        {"id": "t3", "title": "Night", "day": "2015-01-01", "rtype": "album"},
        {"id": "t4", "title": "", "day": "2015-01-01", "rtype": "album"},
    ]
    groups = [
        {"rgid": "g1", "title": "Hollow", "date": "2019-11-20", "rtype": "album"},
        {"rgid": "g2", "title": "Hollow", "date": "2020", "rtype": "single"},
        {"rgid": "g3", "title": "Night", "date": "2018-01-01", "rtype": "album"},
        {"rgid": "g4", "title": "Night", "date": "", "rtype": "ep"},
        {"rgid": "g5", "title": "Gone", "date": "2021-05-05", "rtype": "album"},
        {"rgid": "g6", "title": "Hollow", "date": "2021-01-01", "rtype": "ep"},
    ]
    out = {g["rgid"]: g["release_id"] for g in mb.match_groups(groups, tidal)}
    # The same kind wins among same-day rows; a group with no date matches
    # on the title alone; three years off is another record; a row already
    # taken serves again only when nothing free fits.
    assert out == {"g1": "t1", "g2": "t2", "g3": "", "g4": "t3", "g5": "", "g6": "t1"}
    assert mb.match_groups([], tidal) == [] and mb.match_groups(groups[:1], []) == [{**groups[0], "release_id": ""}]


class _Fake:
    def __init__(self, artists, groups, *, fail=()):
        self.artists = artists
        self.groups = groups
        self.fail = set(fail)
        self.calls: list = []

    def search_artist(self, name, limit=10):
        self.calls.append(("search", name))
        return None if "search" in self.fail else list(self.artists)

    def release_groups(self, mbid):
        self.calls.append(("groups", mbid))
        return None if mbid in self.fail else [dict(g) for g in self.groups.get(mbid, [])]


def _cand(mbid, name):
    return {"mbid": mbid, "name": name, "disambiguation": "", "score": 100}


def _g(rgid, title, date="2000-01-01", rtype="album"):
    return {"rgid": rgid, "title": title, "date": date, "rtype": rtype}


_TIDAL = [
    {"id": "t1", "title": "Nevermind (Remastered)", "day": "1991-09-24", "rtype": "album"},
    {"id": "t2", "title": "Bleach", "day": "1989-06-15", "rtype": "album"},
    {"id": "t3", "title": "In Utero", "day": "1993-09-13", "rtype": "album"},
]


def test_an_artist_needs_a_name_match_and_two_shared_titles():
    us = [_g("a1", "Bleach", "1989-06-15"), _g("a2", "Nevermind", "1991-09-24")]
    uk = [_g("b1", "Local Anaesthetic", "1971-01-01"), _g("b2", "Nevermind", "1990-01-01")]
    # The namesake ranks first and shares one title: rejected, the band
    # that shares two is taken, in the search's order.
    fake = _Fake([_cand("b", "Nirvana"), _cand("a", "Nirvana"), _cand("c", "Nirvana Sitar")], {"a": us, "b": uk})
    assert mb.resolve_artist(fake, "Nirvana", _TIDAL) == ("a", us)
    assert fake.calls == [("search", "Nirvana"), ("groups", "b"), ("groups", "a")]
    # No candidate of that name: nothing is paged.
    fake = _Fake([_cand("c", "Nirvana Sitar")], {})
    assert mb.resolve_artist(fake, "Nirvana", _TIDAL) == ("", [])
    assert fake.calls == [("search", "Nirvana")]
    # A name match with only one shared title is not sure either.
    fake = _Fake([_cand("b", "Nirvana")], {"b": uk})
    assert mb.resolve_artist(fake, "Nirvana", _TIDAL) == ("", [])
    # The name key folds accents and apostrophes the way the import does.
    fake = _Fake([_cand("a", "Björk")], {"a": [_g("x", "Debut"), _g("y", "Post")]})
    assert (
        mb.resolve_artist(
            fake,
            "Bjork",
            [{"id": "1", "title": "Debut", "day": "1993-07-05"}, {"id": "2", "title": "Post", "day": "1995-06-13"}],
        )[0]
        == "a"
    )


def test_a_thin_follow_needs_a_lone_exact_name():
    thin = _TIDAL[:1]
    fake = _Fake([_cand("a", "Nirvana")], {"a": [_g("a1", "Bleach")]})
    assert mb.resolve_artist(fake, "Nirvana", thin) == ("a", [_g("a1", "Bleach")])
    # Two of the name: no way to tell, so neither.
    fake = _Fake([_cand("a", "Nirvana"), _cand("b", "Nirvana")], {"a": [_g("a1", "Bleach")]})
    assert mb.resolve_artist(fake, "Nirvana", thin) == ("", [])
    assert fake.calls == [("search", "Nirvana")]
    # A blank name is never asked.
    assert mb.resolve_artist(fake, "   ", thin) == ("", []) and len(fake.calls) == 1


def test_only_a_few_namesakes_are_weighed():
    fake = _Fake([_cand(m, "Nirvana") for m in "abcde"], {})
    assert mb.resolve_artist(fake, "Nirvana", _TIDAL) == ("", [])
    assert [c for c in fake.calls if c[0] == "groups"] == [("groups", "a"), ("groups", "b"), ("groups", "c")]


def test_a_read_that_could_not_be_made_leaves_the_question_open():
    fake = _Fake([_cand("a", "Nirvana")], {}, fail=("search",))
    assert mb.resolve_artist(fake, "Nirvana", _TIDAL) is None
    fake = _Fake([_cand("a", "Nirvana")], {}, fail=("a",))
    assert mb.resolve_artist(fake, "Nirvana", _TIDAL) is None
    assert mb.resolve_artist(fake, "Nirvana", _TIDAL[:1]) is None
