"""A reissue shows the day it was really listed, on every surface.

TIDAL gives a reissue two dates that disagree: ``releaseDate`` is the
ORIGINAL album's (Sabaton's *The Last Stand (10th Anniversary Edition)* reads
2016-08-19) and ``streamStartDate`` is the day the listing went live
(2026-09-11). Neither can simply win: the first buries a new reissue among
decade-old albums, the second is a re-ingest event for the back catalogue (a
2012 album reads 2022, three unrelated albums share one 2019 day).

The rule is deterministic and reads only the row itself, so a search row and
an artist-page row agree and nothing decays with the calendar: a lag of more
than a year, an edition qualifier, and a ℗ year (when the copyright line has
one) that matches the stream-start year rather than the original release.

The corpus below is the live TIDAL data for Sabaton read on 2026-09-11.
"""

from __future__ import annotations

import datetime as dt
import json
from threading import Lock
from types import SimpleNamespace

import pytest

from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge

UTC = dt.UTC


def _album(name, released, listed, version="", notice="", ident="x"):
    """The two dates parse the way tidalapi parses them: releaseDate naive,
    streamStartDate tz-aware."""
    return SimpleNamespace(
        id=ident,
        name=name,
        version=version or None,
        copyright=notice,
        release_date=dt.datetime.fromisoformat(released) if released else None,
        tidal_release_date=(dt.datetime.fromisoformat(listed).replace(tzinfo=UTC) if listed else None),
    )


# (title, releaseDate, streamStartDate, version, copyright, expected listed)
SABATON = [
    (
        "The Symphony To End All Wars (Symphonic Version)",
        "2022-03-04",
        "2022-05-06",
        "Symphonic Version",
        "Nuclear Blast",
        "",
    ),
    ("The War To End All Wars (History Edition)", "2022-03-04", "2022-03-25", "History Edition", "Nuclear Blast", ""),
    ("The Great Show (Live In Prague, 2020)", "2021-11-19", "2021-12-01", "Live In Prague, 2020", "Nuclear Blast", ""),
    ("The Last Stand (Track Commentary Version)", "2016-08-19", "2016-08-18", "", "2016 Nuclear Blast", ""),
    ("The Last Stand", "2016-08-19", "2016-08-19", "", "2016 Nuclear Blast", ""),
    (
        "The Last Stand (10th Anniversary Edition)",
        "2016-08-19",
        "2026-09-11",
        "10th Anniversary Edition",
        "Nuclear Blast",
        "2026-09-11",
    ),
    ("Heroes (Deluxe Edition)", "2014-05-27", "2015-04-10", "", "2015 Nuclear Blast", ""),
    ("Swedish Empire (Live)", "2013-10-31", "2019-03-20", "Live", "2013 Nuclear Blast", ""),
    ("Carolus Rex (Swedish)", "2012-05-25", "2022-05-20", "", "Nuclear Blast", ""),
    ("Carolus Rex (English)", "2012-04-08", "2022-05-20", "", "Nuclear Blast", ""),
    ("World War Live - Battle of the Baltic Sea", "2011-08-19", "2019-03-20", "", "2011 Nuclear Blast", ""),
    ("Primo Victoria (Re-Armed)", "2011-04-19", "2010-09-24", "Re-Armed", "2010 Nuclear Blast", ""),
    ("Coat of Arms", "2010-01-01", "2019-03-20", "", "2010 Nuclear Blast", ""),
    ("The Art of War (Re-Armed)", "2008-06-02", "2010-09-24", "Re-Armed", "2010 Nuclear Blast", "2010-09-24"),
    ("Metalizer (Re-Armed)", "2007-03-19", "2010-09-24", "Re-Armed", "2010 Nuclear Blast", "2010-09-24"),
    ("Attero Dominatus (Re-Armed)", "2006-01-01", "2010-09-24", "Re-Armed", "2010 Nuclear Blast", "2010-09-24"),
]


@pytest.mark.parametrize("title,released,listed,version,notice,expected", SABATON, ids=[r[0] for r in SABATON])
def test_the_measured_corpus_lifts_exactly_the_reissues(title, released, listed, version, notice, expected):
    assert backend._listed_date_str(_album(title, released, listed, version, notice)) == expected


def test_each_witness_is_necessary():
    # The anniversary edition, then one witness removed at a time.
    assert backend._listed_date_str(_album("X (Remaster)", "2016-08-19", "2026-09-11")) == "2026-09-11"
    # A year and a day, not a same-cycle listing lag.
    assert backend._listed_date_str(_album("X (Remaster)", "2016-08-19", "2017-08-19")) == ""
    assert backend._listed_date_str(_album("X (Remaster)", "2016-08-19", "2017-08-20")) == "2017-08-20"
    # No qualifier anywhere: a plain re-ingest of the original.
    assert backend._listed_date_str(_album("X", "2016-08-19", "2026-09-11")) == ""
    # TIDAL's version field alone qualifies, whatever the title says.
    assert backend._listed_date_str(_album("X", "2016-08-19", "2026-09-11", version="Deluxe")) == "2026-09-11"
    # A ℗ year that names the original release is the migration signature.
    assert backend._listed_date_str(_album("X (Remaster)", "2016-08-19", "2026-09-11", notice="℗ 2016 Label")) == ""
    assert (
        backend._listed_date_str(_album("X (Remaster)", "2016-08-19", "2026-09-11", notice="2026 Label"))
        == "2026-09-11"
    )


def test_missing_dates_never_lift_and_mixed_tz_never_raises():
    assert backend._listed_date(_album("X (Remaster)", "", "2026-09-11")) is None
    assert backend._listed_date(_album("X (Remaster)", "2016-08-19", "")) is None
    assert backend._listed_date(SimpleNamespace(name="X")) is None
    # A plain date object on either side is fine too.
    a = _album("X (Remaster)", "2016-08-19", "2026-09-11")
    a.release_date = dt.date(2016, 8, 19)
    assert backend._listed_date(a) == dt.date(2026, 9, 11)


def test_copyright_year_reads_the_first_year_or_none():
    assert backend._copyright_year(SimpleNamespace(copyright="2016 Nuclear Blast")) == 2016
    assert backend._copyright_year(SimpleNamespace(copyright="℗ 2010 Label, under exclusive license")) == 2010
    assert backend._copyright_year(SimpleNamespace(copyright="Nuclear Blast")) is None
    assert backend._copyright_year(SimpleNamespace(copyright=None)) is None
    assert backend._copyright_year(SimpleNamespace()) is None


def _shelf():
    rows = [_album(t, r, s, v, c, ident=str(i)) for i, (t, r, s, v, c, _) in enumerate(SABATON)]
    return rows


def test_a_lifted_row_moves_to_where_its_listed_date_belongs():
    rows = _shelf()
    placed = backend._place_by_listed(rows)
    names = [r.name for r in placed]
    # The 2026 reissue leads the shelf.
    assert names[0] == "The Last Stand (10th Anniversary Edition)"
    # The 2010 Re-Armed editions sit among the 2010 releases: after Primo
    # Victoria (Re-Armed) (2011) and before Coat of Arms (2010-01-01).
    i_primo = names.index("Primo Victoria (Re-Armed)")
    i_coat = names.index("Coat of Arms")
    for t in ("The Art of War (Re-Armed)", "Metalizer (Re-Armed)", "Attero Dominatus (Re-Armed)"):
        assert i_primo < names.index(t) < i_coat
    # Every row that was not lifted keeps TIDAL's relative order exactly.
    lifted = {r.name for r in rows if backend._listed_date(r) is not None}
    assert [n for n in names if n not in lifted] == [r.name for r in rows if r.name not in lifted]
    assert sorted(names) == sorted(r.name for r in rows)


def test_a_shelf_with_nothing_lifted_is_returned_verbatim():
    rows = [r for r in _shelf() if backend._listed_date(r) is None]
    assert backend._place_by_listed(rows) == rows


class _Pool:
    def start(self, worker, priority=0):
        worker.fn()


class _Sig:
    def __init__(self):
        self.emits = []

    def emit(self, *a):
        self.emits.append(a)


class _PageStub:
    loadArtist = WavesBridge.loadArtist
    _start_artist_build = WavesBridge._start_artist_build

    def __init__(self, artist):
        self.threadpool = _Pool()
        self._artist = artist
        self._artist_cache = {}
        self._artist_loading = set()
        self._artist_prefetch = None
        self._artist_prefetch_claimed = False
        self._prefetch_lock = Lock()
        self._browse_gen = 0
        self.artistLoaded = _Sig()
        self.artistLoadFailed = _Sig()

    def _artist_page_collapses_editions(self):
        return False

    def _set_status(self, s):
        pass

    def _set_busy(self, b):
        pass

    def _get_artist(self, artist_id):
        return self._artist

    def _dedup_albums(self, a):
        return a

    def _dedup_tracks(self, t):
        return t

    def _dedup_videos(self, v):
        return v

    def _album_dict(self, a):
        return {"id": a.id, "listed": backend._listed_date_str(a)}

    def _track_dict(self, t):
        return {"id": t}

    def _video_dict(self, v):
        return {"id": v}

    def _remember_artist_page(self, artist_id, payload):
        self._artist_cache[artist_id] = payload

    def _save_page_cache(self):
        pass


def test_the_artist_page_leads_with_the_reissue_and_revalidates_quietly():
    rows = _shelf()
    artist = SimpleNamespace(
        name="Sabaton",
        get_bio=lambda: "",
        get_albums=lambda: list(rows),
        get_ep_singles=lambda: [],
        get_top_tracks=lambda limit=10: [],
        get_videos=lambda limit=None: [],
    )
    stub = _PageStub(artist)
    stub.loadArtist("3616281")
    page = stub.artistLoaded.emits[-1][0]
    ids = [a["id"] for a in page["albums"]]
    anniversary = next(str(i) for i, r in enumerate(SABATON) if "Anniversary" in r[0])
    assert ids[0] == anniversary
    assert page["albums"][0]["listed"] == "2026-09-11"
    # Every row carries the key, lifted or not (ListModel reads roles from row 0).
    assert all("listed" in a for a in page["albums"])
    # The placement is a pure function of TIDAL's data: the cached page and
    # the rebuilt one are equal, so the revalidate does not repaint.
    stub.artistLoaded.emits.clear()
    stub.loadArtist("3616281")
    assert len(stub.artistLoaded.emits) == 1
    assert "refresh" not in stub.artistLoaded.emits[0][0]


class _CacheStub:
    _load_page_cache = WavesBridge._load_page_cache

    def __init__(self, path):
        self._page_cache_lock = Lock()
        self._page_cache_path = str(path)
        self._browse_root_cache = None
        self._browse_pages = {}
        self._artist_cache = {}
        self._lib_cache = {}
        self._home_cache = None
        self._search_cache = {}

    def _cache_user_id(self):
        return "u1"


def test_a_snapshot_from_before_listed_dates_is_discarded(tmp_path):
    path = tmp_path / "page_cache.json"
    page = {"id": "3616281", "albums": [{"id": "1"}]}
    path.write_text(json.dumps({"version": 3, "user": "u1", "artists": {"3616281": page}}))
    stub = _CacheStub(path)
    stub._load_page_cache()
    assert stub._artist_cache == {}

    path.write_text(json.dumps({"version": backend._PAGE_CACHE_VERSION, "user": "u1", "artists": {"3616281": page}}))
    stub = _CacheStub(path)
    stub._load_page_cache()
    assert stub._artist_cache == {"3616281": page}
