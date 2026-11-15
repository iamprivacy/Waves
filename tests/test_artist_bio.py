"""Tests for waves/artist_bio.py: the bio fallback for artists TIDAL has no
biography for, driven through canned answers (no network, no real sleep).

The promises: the artist is found by a proof on record, else MusicBrainz's
link from the TIDAL page, else the Releases check's title rule, and never
on a guess (one TIDAL page under two artists, or a namesake with no shared
titles, is no match); Wikipedia's intro wins, a disambiguation page is
skipped, Discogs fills in with its markup cleaned; Discogs is kept six
hours and never shown older; an expired bio is read again from its own
source in one request; a transient failure puts nothing on record; with
the setting off nothing is sent.
"""

from __future__ import annotations

import json
import urllib.parse

import pytest

from waves import artist_bio
from waves import musicbrainz as mb
from waves.mb_service import MBService

TID = "64518"
MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"


class _Router:
    """Canned answers by (host, last path part), popped in order; a key
    with nothing left answers 404. Every url asked is kept."""

    def __init__(self, answers: dict):
        self.answers = {k: list(v) for k, v in answers.items()}
        self.urls: list[str] = []

    def __call__(self, url):
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        key = (parts.netloc, parts.path.rstrip("/").rsplit("/", 1)[-1])
        left = self.answers.get(key) or []
        if not left:
            return 404, ""
        status, body = left.pop(0)
        return status, body if isinstance(body, str) else json.dumps(body)

    def asked(self, host: str) -> int:
        return sum(1 for u in self.urls if urllib.parse.urlsplit(u).netloc == host)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    clock = {"now": 0.0}

    def sleep(s):
        clock["now"] += s

    monkeypatch.setattr(artist_bio, "_DISCOGS_PACER", mb.Pacer(sleep=sleep, monotonic=lambda: clock["now"]))
    # Wikidata, Wikipedia and Discogs reads go to the test's router; with
    # none set up, a read fails the test instead of reaching the network.
    _ROUTE.clear()

    def fetch(url):
        assert _ROUTE, f"unrouted request {url}"
        return _ROUTE[0](url)

    monkeypatch.setattr(artist_bio, "_http_fetch", fetch)


_ROUTE: list = []


def _setup(tmp_path, answers, clock=None):
    clock = clock if clock is not None else {"now": 1_000_000.0}
    t = {"now": 0.0}

    def sleep(s):
        t["now"] += s

    router = _Router(answers)
    _ROUTE[:] = [router]
    pacer = mb.Pacer(sleep=sleep, monotonic=lambda: t["now"])
    svc = MBService(str(tmp_path / "musicbrainz.sqlite3"), fetch=router, pacer=pacer, now=lambda: clock["now"])
    client = mb.MusicBrainzClient(service=svc)
    return svc, client, router, clock


MB = "musicbrainz.org"
WD = "www.wikidata.org"
WP = "en.wikipedia.org"
DG = "api.discogs.com"


def _url_rel(*mbids):
    return {
        "resource": f"https://tidal.com/artist/{TID}",
        "relations": [{"type": "streaming", "target-type": "artist", "artist": {"id": m, "name": "X"}} for m in mbids],
    }


def _artist(wikidata="Q44190", discogs="3840"):
    rels = []
    if wikidata:
        rels.append({"type": "wikidata", "url": {"resource": f"https://www.wikidata.org/wiki/{wikidata}"}})
    if discogs:
        rels.append({"type": "discogs", "url": {"resource": f"https://www.discogs.com/artist/{discogs}"}})
    return {"id": MBID, "name": "Radiohead", "relations": rels}


def _wikidata(title="Radiohead", qid="Q44190"):
    links = {"enwiki": {"site": "enwiki", "title": title}} if title else {}
    return {"entities": {qid: {"id": qid, "sitelinks": links}}}


def _wiki_page(extract="Radiohead are an English rock band.\nThey formed in 1985.", disambiguation=False):
    page = {
        "title": "Radiohead",
        "extract": extract,
        "canonicalurl": "https://en.wikipedia.org/wiki/Radiohead",
    }
    if disambiguation:
        page["pageprops"] = {"disambiguation": ""}
    return {"query": {"pages": [page]}}


def _discogs(profile):
    return {"id": 3840, "profile": profile, "uri": "https://www.discogs.com/artist/3840-Radiohead"}


# ---- which artist -------------------------------------------------------------


def test_the_tidal_page_link_finds_the_artist_and_wikipedia_answers(tmp_path):
    svc, client, router, _ = _setup(
        tmp_path,
        {
            (MB, "url"): [(200, _url_rel(MBID))],
            (MB, MBID): [(200, _artist())],
            (WD, "api.php"): [(200, _wikidata())],
            (WP, "api.php"): [(200, _wiki_page())],
        },
    )
    got = artist_bio.lookup(client, svc, TID, "Radiohead", [])
    assert got == {
        "text": "Radiohead are an English rock band.\n\nThey formed in 1985.",
        "source": "wikipedia",
        "url": "https://en.wikipedia.org/wiki/Radiohead",
    }
    resource = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(router.urls[0]).query))["resource"]
    assert resource == f"https://tidal.com/artist/{TID}"
    # The proof goes on record for the other MusicBrainz features.
    assert svc.linked("tidal", TID)["mbid"] == MBID
    assert router.asked(DG) == 0


def test_a_proof_on_record_saves_the_url_lookup(tmp_path):
    svc, client, router, _ = _setup(
        tmp_path,
        {
            (MB, MBID): [(200, _artist())],
            (WD, "api.php"): [(200, _wikidata())],
            (WP, "api.php"): [(200, _wiki_page())],
        },
    )
    svc.link("tidal", TID, "Radiohead", MBID, "match")
    assert artist_bio.lookup(client, svc, TID, "Radiohead", [])["source"] == "wikipedia"
    assert not [u for u in router.urls if "/ws/2/url" in u]


def test_one_tidal_page_under_two_artists_is_not_sure(tmp_path):
    svc, client, router, _ = _setup(tmp_path, {(MB, "url"): [(200, _url_rel(MBID, "other-mbid"))]})
    assert artist_bio.lookup(client, svc, TID, "Radiohead", []) == {"text": "", "source": "", "url": ""}
    assert svc.linked("tidal", TID) is None
    assert router.asked(WP) == 0


def test_no_link_falls_back_on_the_title_rule(tmp_path):
    rows = [{"id": "1", "title": "OK Computer", "date": "1997-05-21"}, {"id": "2", "title": "Kid A", "date": "2000"}]
    groups = {
        "release-groups": [
            {"id": "g1", "title": "OK Computer", "first-release-date": "1997-05-21", "primary-type": "Album"},
            {"id": "g2", "title": "Kid A", "first-release-date": "2000-10-02", "primary-type": "Album"},
        ],
        "release-group-count": 2,
    }
    svc, client, _, _ = _setup(
        tmp_path,
        {
            (MB, "url"): [(404, "")],
            (MB, "artist"): [(200, {"artists": [{"id": MBID, "name": "Radiohead", "score": 100}]})],
            (MB, "release-group"): [(200, groups)],
            (MB, MBID): [(200, _artist())],
            (WD, "api.php"): [(200, _wikidata())],
            (WP, "api.php"): [(200, _wiki_page())],
        },
    )
    assert artist_bio.lookup(client, svc, TID, "Radiohead", rows)["source"] == "wikipedia"
    assert svc.linked("tidal", TID) == {"mbid": MBID, "how": "match", "at": pytest.approx(1_000_000.0)}


def test_a_namesake_with_no_shared_titles_gets_no_bio(tmp_path):
    rows = [{"id": "1", "title": "OK Computer", "date": "1997"}, {"id": "2", "title": "Kid A", "date": "2000"}]
    other = {
        "release-groups": [
            {"id": "g9", "title": "Something Else", "first-release-date": "2011", "primary-type": "Album"}
        ],
        "release-group-count": 1,
    }
    svc, client, router, _ = _setup(
        tmp_path,
        {
            (MB, "url"): [(404, "")],
            (MB, "artist"): [(200, {"artists": [{"id": "namesake", "name": "Radiohead", "score": 100}]})],
            (MB, "release-group"): [(200, other)],
        },
    )
    assert artist_bio.lookup(client, svc, TID, "Radiohead", rows)["source"] == ""
    assert router.asked(WP) == 0 and router.asked(DG) == 0
    # The "none" is kept a week, so the next view asks nothing.
    n = len(router.urls)
    assert artist_bio.lookup(client, svc, TID, "Radiohead", rows)["source"] == ""
    assert len(router.urls) == n


# ---- which text ---------------------------------------------------------------


def test_a_disambiguation_page_is_skipped_for_discogs(tmp_path):
    svc, client, _, _ = _setup(
        tmp_path,
        {
            (MB, "url"): [(200, _url_rel(MBID))],
            (MB, MBID): [(200, _artist())],
            (WD, "api.php"): [(200, _wikidata())],
            (WP, "api.php"): [(200, _wiki_page(disambiguation=True))],
            (DG, "3840"): [(200, _discogs("English band from Oxfordshire."))],
        },
    )
    got = artist_bio.lookup(client, svc, TID, "Radiohead", [])
    assert got == {
        "text": "English band from Oxfordshire.",
        "source": "discogs",
        "url": "https://www.discogs.com/artist/3840-Radiohead",
    }


def test_no_wikipedia_article_falls_to_discogs(tmp_path):
    svc, client, _, _ = _setup(
        tmp_path,
        {
            (MB, "url"): [(200, _url_rel(MBID))],
            (MB, MBID): [(200, _artist())],
            (WD, "api.php"): [(200, _wikidata(title=""))],
            (DG, "3840"): [(200, _discogs("A band."))],
        },
    )
    assert artist_bio.lookup(client, svc, TID, "Radiohead", [])["source"] == "discogs"


def test_an_artist_with_no_links_has_no_bio(tmp_path):
    svc, client, router, _ = _setup(
        tmp_path, {(MB, "url"): [(200, _url_rel(MBID))], (MB, MBID): [(200, _artist(wikidata="", discogs=""))]}
    )
    assert artist_bio.lookup(client, svc, TID, "Radiohead", [])["source"] == ""
    assert router.asked(WD) == 0 and router.asked(DG) == 0


def test_discogs_markup_is_cleaned_and_unnamed_references_dropped():
    raw = (
        "Band from [l=Oxford Records] country.\r\n\r\n"
        'The name comes from the [a=Talking Heads] song, from the "[url=http://x]True Stories[/url]" album. '
        'Their first single was "[r=767600]". [b]Loud[/b] and [i]clear[/i].\r\n\r\n'
        "Members: [a123], [a456]."
    )
    assert artist_bio.clean_discogs(raw) == (
        "Band from Oxford Records country.\n\n"
        'The name comes from the Talking Heads song, from the "True Stories" album. Loud and clear.'
    )


def test_sources_reads_wikidata_wikipedia_and_discogs_and_skips_ended_links():
    body = {
        "relations": [
            {"type": "wikidata", "url": {"resource": "https://www.wikidata.org/wiki/Q5"}},
            {"type": "wikipedia", "url": {"resource": "https://en.wikipedia.org/wiki/Sigur_R%C3%B3s"}},
            {"type": "discogs", "ended": True, "url": {"resource": "https://www.discogs.com/artist/1"}},
            {"type": "discogs", "url": {"resource": "https://www.discogs.com/artist/2-Name"}},
        ]
    }
    assert artist_bio.sources(body) == {"wikidata": "Q5", "wikipedia": "Sigur Rós", "discogs": "2"}


# ---- keeping it ---------------------------------------------------------------


def test_a_discogs_bio_is_kept_six_hours_then_read_again_by_its_own_id(tmp_path):
    svc, client, router, clock = _setup(
        tmp_path,
        {
            (MB, "url"): [(200, _url_rel(MBID))],
            (MB, MBID): [(200, _artist(wikidata=""))],
            (DG, "3840"): [(200, _discogs("First.")), (200, _discogs("Second."))],
        },
    )
    assert artist_bio.lookup(client, svc, TID, "Radiohead", [])["text"] == "First."
    clock["now"] += artist_bio.DISCOGS_TTL_SEC - 60
    assert artist_bio.lookup(client, svc, TID, "Radiohead", [])["text"] == "First."
    asked = len(router.urls)
    clock["now"] += 120
    assert artist_bio.lookup(client, svc, TID, "Radiohead", [])["text"] == "Second."
    # One request, to Discogs alone: no MusicBrainz chain again.
    assert len(router.urls) == asked + 1 and router.asked(DG) == 2


def test_an_expired_discogs_bio_is_never_shown_when_its_refresh_fails(tmp_path):
    svc, client, _, clock = _setup(
        tmp_path,
        {
            (MB, "url"): [(200, _url_rel(MBID))],
            (MB, MBID): [(200, _artist(wikidata=""))],
            (DG, "3840"): [(200, _discogs("First.")), (503, "")],
        },
    )
    assert artist_bio.lookup(client, svc, TID, "Radiohead", [])["text"] == "First."
    clock["now"] += artist_bio.DISCOGS_TTL_SEC + 1
    assert artist_bio.lookup(client, svc, TID, "Radiohead", []) is None


def test_an_expired_wikipedia_bio_still_shows_when_its_refresh_fails(tmp_path):
    svc, client, _, clock = _setup(
        tmp_path,
        {
            (MB, "url"): [(200, _url_rel(MBID))],
            (MB, MBID): [(200, _artist())],
            (WD, "api.php"): [(200, _wikidata())],
            (WP, "api.php"): [(200, _wiki_page()), (503, "")],
        },
    )
    first = artist_bio.lookup(client, svc, TID, "Radiohead", [])
    clock["now"] += artist_bio.WIKIPEDIA_TTL_SEC + 1
    assert artist_bio.lookup(client, svc, TID, "Radiohead", []) == first


def test_a_transient_failure_puts_nothing_on_record(tmp_path):
    svc, client, _, _ = _setup(
        tmp_path,
        {
            (MB, "url"): [(200, _url_rel(MBID))],
            (MB, MBID): [(200, _artist())],
            (WD, "api.php"): [(500, "")],
        },
    )
    assert artist_bio.lookup(client, svc, TID, "Radiohead", []) is None
    assert svc.bio_get(TID) is None


def test_no_name_and_no_link_from_the_page_puts_nothing_on_record(tmp_path):
    # A card can ask before its page has a name: with no link from the
    # TIDAL page to weigh, that is "could not ask", never a week of none.
    svc, client, router, _ = _setup(tmp_path, {(MB, "url"): [(404, "")]})
    assert artist_bio.lookup(client, svc, TID, "", []) is None
    assert svc.bio_get(TID) is None
    assert router.asked(MB) == 1, "only the link from the TIDAL page was asked"


def test_a_record_carries_its_expiry(tmp_path):
    svc, _, _, clock = _setup(tmp_path, {})
    svc.bio_put(TID, mbid=MBID, source="discogs", ref="3840", text="T.", url="u", ttl=artist_bio.DISCOGS_TTL_SEC)
    assert svc.bio_get(TID)["expires"] == pytest.approx(clock["now"] + artist_bio.DISCOGS_TTL_SEC)


def test_with_the_setting_off_nothing_is_sent(tmp_path):
    svc, client, router, _ = _setup(tmp_path, {(MB, "url"): [(200, _url_rel(MBID))]})
    client = mb.MusicBrainzClient(service=svc, alive=lambda: False)
    assert artist_bio.lookup(client, svc, TID, "Radiohead", [], alive=lambda: False) is None
    assert router.urls == []


def test_a_bio_on_record_answers_with_the_setting_off(tmp_path):
    svc, client, router, _ = _setup(tmp_path, {})
    svc.bio_put(
        TID, mbid=MBID, source="wikipedia", ref="Radiohead", text="T.", url="https://en.wikipedia.org/wiki/R", ttl=60
    )
    got = artist_bio.lookup(client, svc, TID, "Radiohead", [], alive=lambda: False)
    assert got == {"text": "T.", "source": "wikipedia", "url": "https://en.wikipedia.org/wiki/R"}
    assert router.urls == []
