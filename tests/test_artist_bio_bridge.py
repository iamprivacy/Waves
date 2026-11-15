"""The artist bio fallback's bridge side (Settings > Appearance, "Fill
missing artist bios").

The switch ships off (it sends artist ids and names to MusicBrainz,
Wikipedia and Discogs) and is a row in the Appearance section. A page
build fills an empty TIDAL bio from the record (no request), so a
revalidate never reads as changed only because TIDAL still has none, and a
Discogs bio past its six hours is never shown. wantArtistBio looks one up
on its own pool, lands it on artistBioLanded and into the cached page; a
logout mid-lookup lands nothing, and with the switch off nothing starts.
"""

from __future__ import annotations

import time
from threading import Lock

import pytest

from tests.test_artist_card_summary import _Pool, _Sig, _Stub
from tests.test_video_hover_peek_pref import _bind, _prefs_stub, _schema_stub
from waves import artist_bio
from waves.mb_service import MBService
from waves.waves_ui.backend import WavesBridge

# ---- the switch ---------------------------------------------------------------


def test_the_switch_ships_off():
    assert _prefs_stub()._waves_prefs["artist_bio_fallback"] is False


def test_the_switch_is_a_row_in_the_appearance_section():
    schema = WavesBridge.settingsSchema(_schema_stub())
    appearance = next(s for s in schema if s["id"] == "appearance")
    field = next((f for f in appearance["fields"] if f["key"] == "artist_bio_fallback"), None)
    assert field is not None
    assert field["type"] == "bool" and field["value"] is False
    assert field.get("rowToggle") is True
    for host in ("musicbrainz.org", "wikipedia.org", "discogs.com"):
        assert host in field["help"], "the help says where the artist's id or name goes"


def test_flipping_the_switch_notifies_the_ui():
    stub = _prefs_stub()
    stub._save_waves_prefs = lambda: None
    stub._factory_reset = False
    fired = []

    class _Fire:
        def emit(self):
            fired.append(True)

    stub.artistBioFallbackChanged = _Fire()
    _bind(stub, "setWavesPref")("artist_bio_fallback", True)
    assert stub._waves_prefs["artist_bio_fallback"] is True and fired


# ---- the bridge ---------------------------------------------------------------


class _BioStub(_Stub):
    _bio_fill = WavesBridge._bio_fill
    _bio_alive = WavesBridge._bio_alive
    wantArtistBio = WavesBridge.wantArtistBio
    _waves_pref_bool = WavesBridge._waves_pref_bool

    def __init__(self, svc, *, on=True):
        super().__init__()
        self._svc = svc
        self._waves_prefs = {"artist_bio_fallback": on}
        self._bio_pool = _Pool()
        self._bio_lock = Lock()
        self._bio_wanted = set()
        self._bio_client = None
        self._bio_stopped = False
        self.artistBioLanded = _Sig()

    def _mb_service_instance(self):
        return self._svc


@pytest.fixture
def svc(tmp_path):
    clock = {"now": 1_000_000.0}
    s = MBService(
        str(tmp_path / "mb.sqlite3"),
        fetch=lambda url: pytest.fail(f"no request expected: {url}"),
        now=lambda: clock["now"],
    )
    s.clock = clock
    return s


def _put(svc, source="wikipedia", text="From the record.", ttl=3600, tid="9"):
    svc.bio_put(tid, mbid="m", source=source, ref="r", text=text, url=f"https://{source}.test/r", ttl=ttl)


def test_a_build_fills_an_empty_tidal_bio_from_the_record(svc):
    _put(svc)
    b = _BioStub(svc)
    b.loadArtist("9")
    b.threadpool.run_all()
    page = b.artistLoaded.emits[-1][0]
    assert (page["bio"], page["bio_source"], page["bio_url"]) == (
        "From the record.",
        "wikipedia",
        "https://wikipedia.test/r",
    )
    summary = b.artistSummary("9")
    assert summary["bio"] == "From the record." and summary["bio_source"] == "wikipedia"


def test_a_revalidate_with_the_bio_on_record_is_not_a_change(svc):
    _put(svc)
    b = _BioStub(svc)
    b.loadArtist("9")
    b.threadpool.run_all()
    emitted = len(b.artistLoaded.emits)
    b._start_artist_build("9", b._artist_cache["9"], False, silent=False)
    b.threadpool.run_all()
    assert len(b.artistLoaded.emits) == emitted, "nothing changed, so no in-place update"


def test_with_the_switch_off_the_record_is_not_shown(svc):
    _put(svc)
    b = _BioStub(svc, on=False)
    b.loadArtist("9")
    b.threadpool.run_all()
    assert b.artistLoaded.emits[-1][0]["bio"] == ""


def test_an_expired_discogs_bio_is_never_shown_but_an_expired_wikipedia_one_is(svc):
    b = _BioStub(svc)
    _put(svc, source="discogs", ttl=60, tid="1")
    _put(svc, source="wikipedia", ttl=60, tid="2")
    svc.clock["now"] += 120
    discogs, wiki = {"id": "1", "bio": ""}, {"id": "2", "bio": ""}
    b._bio_fill(discogs)
    b._bio_fill(wiki)
    assert discogs["bio"] == "" and wiki["bio"] == "From the record."


def test_a_tidal_bio_is_never_replaced(svc):
    _put(svc)
    payload = {"id": "9", "bio": "TIDAL's own."}
    _BioStub(svc)._bio_fill(payload)
    assert payload["bio"] == "TIDAL's own."


def test_a_lookup_lands_and_the_cached_page_takes_it(svc, monkeypatch):
    asked = []

    def lookup(client, s, tid, name, rows, alive=None):
        asked.append((tid, name, [r["id"] for r in rows]))
        return {"text": "Found.", "source": "discogs", "url": "https://www.discogs.com/artist/1"}

    monkeypatch.setattr(artist_bio, "lookup", lookup)
    b = _BioStub(svc)
    b._artist_cache["9"] = {"id": "9", "name": "Artist 9", "bio": "", "albums": [{"id": "al1"}], "eps": []}
    b.wantArtistBio("9")
    b.wantArtistBio("9")  # already asked: one lookup
    b._bio_pool.run_all()
    assert asked == [("9", "Artist 9", ["al1"])]
    assert b.artistBioLanded.emits == [
        (
            {
                "id": "9",
                "bio": "Found.",
                "bio_source": "discogs",
                "bio_url": "https://www.discogs.com/artist/1",
                "bio_until": 0.0,
                "failed": False,
            },
        )
    ]
    assert b._artist_cache["9"]["bio"] == "Found." and b._artist_cache["9"]["bio_source"] == "discogs"


def test_a_lookup_that_could_not_ask_lands_as_failed_and_leaves_the_page_alone(svc, monkeypatch):
    monkeypatch.setattr(artist_bio, "lookup", lambda *a, **k: None)
    b = _BioStub(svc)
    b._artist_cache["9"] = {"id": "9", "name": "A", "bio": ""}
    b.wantArtistBio("9")
    b._bio_pool.run_all()
    land = {"id": "9", "bio": "", "bio_source": "", "bio_url": "", "bio_until": 0.0, "failed": True}
    assert b.artistBioLanded.emits == [(land,)], "failed: the next view asks again"
    assert b._artist_cache["9"]["bio"] == ""


def test_a_none_on_record_lands_with_its_expiry(svc, monkeypatch):
    def lookup(client, s, tid, name, rows, alive=None):
        s.bio_put(tid, mbid="", source="", ref="", text="", url="", ttl=artist_bio.NONE_TTL_SEC)
        return {"text": "", "source": "", "url": ""}

    monkeypatch.setattr(artist_bio, "lookup", lookup)
    b = _BioStub(svc)
    b._artist_cache["9"] = {"id": "9", "name": "A", "bio": ""}
    b.wantArtistBio("9")
    b._bio_pool.run_all()
    land = b.artistBioLanded.emits[-1][0]
    assert land["failed"] is False and land["bio"] == ""
    assert land["bio_until"] == pytest.approx(svc.clock["now"] + artist_bio.NONE_TTL_SEC)


def test_a_discogs_bio_lands_with_its_records_expiry(svc, monkeypatch):
    def lookup(client, s, tid, name, rows, alive=None):
        _put(s, source="discogs", ttl=artist_bio.DISCOGS_TTL_SEC, tid=tid)
        return {"text": "From the record.", "source": "discogs", "url": "https://discogs.test/r"}

    monkeypatch.setattr(artist_bio, "lookup", lookup)
    b = _BioStub(svc)
    b._artist_cache["9"] = {"id": "9", "name": "A", "bio": ""}
    b.wantArtistBio("9")
    b._bio_pool.run_all()
    land = b.artistBioLanded.emits[-1][0]
    assert land["bio_until"] == pytest.approx(svc.clock["now"] + artist_bio.DISCOGS_TTL_SEC)
    assert b._artist_cache["9"]["bio_until"] == land["bio_until"]


def test_a_card_held_before_the_cache_asks_by_its_name(svc, monkeypatch):
    asked = []

    def lookup(client, s, tid, name, rows, alive=None):
        asked.append((tid, name, [r["id"] for r in rows]))
        return {"text": "", "source": "", "url": ""}

    monkeypatch.setattr(artist_bio, "lookup", lookup)
    b = _BioStub(svc)
    b._artist_card_early["9"] = {"id": "9", "name": "Artist 9", "bio": "", "albums": [{"id": "al1"}], "eps": []}
    b.wantArtistBio("9")
    b._bio_pool.run_all()
    assert asked == [("9", "Artist 9", ["al1"])]


def test_a_bio_carries_how_long_it_may_be_shown(svc):
    svc.clock["now"] = time.time()
    _put(svc, source="discogs", ttl=3600, tid="1")
    _put(svc, source="wikipedia", ttl=3600, tid="2")
    discogs, wiki = {"id": "1", "bio": ""}, {"id": "2", "bio": ""}
    b = _BioStub(svc)
    b._bio_fill(discogs)
    b._bio_fill(wiki)
    assert discogs["bio_until"] == pytest.approx(svc.clock["now"] + 3600)
    assert wiki["bio_until"] == 0, "a Wikipedia intro has no showing limit"


def test_a_cached_discogs_bio_past_its_six_hours_is_never_served(svc):
    svc.clock["now"] = time.time()
    _put(svc, source="discogs", ttl=artist_bio.DISCOGS_TTL_SEC)
    b = _BioStub(svc)
    b.loadArtist("9")
    b.threadpool.run_all()
    assert b._artist_cache["9"]["bio"] == "From the record."
    assert b.artistSummary("9")["bio"] == "From the record."
    # Six hours on (a page restored from disk, or a long session).
    b._artist_cache["9"]["bio_until"] = time.time() - 1
    summary = b.artistSummary("9")
    assert (summary["bio"], summary["bio_source"], summary["bio_url"]) == ("", "", "")
    n = len(b.artistLoaded.emits)
    b.loadArtist("9")
    painted = b.artistLoaded.emits[n][0]
    assert (painted["bio"], painted["bio_source"]) == ("", ""), "the cached paint showed an expired Discogs bio"
    assert b._artist_cache["9"]["bio"] == "From the record.", "the cache itself is left for the revalidate"


def test_a_logout_mid_lookup_lands_nothing(svc, monkeypatch):
    b = _BioStub(svc)

    def lookup(*a, **k):
        b._browse_gen += 1
        return {"text": "Late.", "source": "wikipedia", "url": "u"}

    monkeypatch.setattr(artist_bio, "lookup", lookup)
    b._artist_cache["9"] = {"id": "9", "name": "A", "bio": ""}
    b.wantArtistBio("9")
    b._bio_pool.run_all()
    assert b.artistBioLanded.emits == [] and b._artist_cache["9"]["bio"] == ""


def test_with_the_switch_off_no_lookup_starts(svc):
    b = _BioStub(svc, on=False)
    b.wantArtistBio("9")
    assert b._bio_pool.workers == []
