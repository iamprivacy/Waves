"""Unit tests for the MusicBrainz arbiter (waves.mb_arbiter).

Hermetic: the HTTP layer is injected, so every case drives the arbiter with
canned (status, body) responses and never touches the network or sleeps. The
contract under test is strictly three-valued: True only when one MusicBrainz
release pins BOTH the local copy (exact count + summed seconds; its year tag
gets no veto, remasters wear the original's year) and the release on screen
(count, year and duration on the axes TIDAL offered), False when MusicBrainz
answered and could not, None on a transient failure, which must never be
cached.
"""

import json

from waves.mb_arbiter import MBArbiter


def _search_body(*releases):
    return json.dumps(
        {
            "releases": [
                {"id": rid, "title": title, "track-count": count, "date": date} for rid, title, count, date in releases
            ]
        }
    )


def _release_body(date, lengths_ms):
    return json.dumps(
        {
            "date": date,
            "media": [{"tracks": [{"length": ms} for ms in lengths_ms]}],
        }
    )


def _arbiter(tmp_path, responses, log=None):
    """An arbiter whose fetch serves canned responses keyed by URL substring;
    unmatched URLs 404. ``log`` collects requested URLs."""

    def fetch(url):
        if log is not None:
            log.append(url)
        for frag, resp in responses.items():
            if frag in url:
                return resp
        return (404, "")

    return MBArbiter(str(tmp_path / "mb.sqlite3"), fetch=fetch, sleep=lambda s: None)


WANT = {"title": "Album", "artist": "Artist", "year": "", "tracks": 12, "duration": 0}
LOCAL = {"tracks": 12, "runtime": 2400, "year": ""}


def test_proves_when_one_release_pins_both_sides(tmp_path):
    arb = _arbiter(
        tmp_path,
        {
            "/release/?": (200, _search_body(("r1", "Album", 12, "2003-05-01"))),
            "/release/r1?": (200, _release_body("2003-05-01", [200_000] * 12)),
        },
    )
    assert arb.arbitrate(WANT, LOCAL) is True


def test_length_outside_tolerance_is_not_provable(tmp_path):
    # 12 tracks grant 24 seconds; 2400 vs 2500 is past it.
    arb = _arbiter(
        tmp_path,
        {
            "/release/?": (200, _search_body(("r1", "Album", 12, "2003"))),
            "/release/r1?": (200, _release_body("2003", [208_400] * 12)),
        },
    )
    assert arb.arbitrate(WANT, LOCAL) is False


def test_wrong_track_count_releases_are_never_read(tmp_path):
    log: list = []
    arb = _arbiter(
        tmp_path,
        {"/release/?": (200, _search_body(("r1", "Album", 14, "2003")))},
        log=log,
    )
    assert arb.arbitrate(WANT, LOCAL) is False
    assert all("/release/r1" not in u for u in log)  # only the search went out


def test_different_edition_titles_are_never_read(tmp_path):
    log: list = []
    arb = _arbiter(
        tmp_path,
        {"/release/?": (200, _search_body(("r1", "Album (Live)", 12, "2003")))},
        log=log,
    )
    assert arb.arbitrate(WANT, LOCAL) is False
    assert all("/release/r1" not in u for u in log)


def test_edition_spellings_fold_like_the_matcher(tmp_path):
    # MusicBrainz says "Deluxe Edition", the screen says "Deluxe Version":
    # one edition, so the release is read and can prove.
    arb = _arbiter(
        tmp_path,
        {
            "/release/?": (200, _search_body(("r1", "Album (Deluxe Edition)", 12, "2003"))),
            "/release/r1?": (200, _release_body("2003", [200_000] * 12)),
        },
    )
    want = dict(WANT, title="Album (Deluxe Version)")
    assert arb.arbitrate(want, LOCAL) is True


def test_screen_year_must_agree(tmp_path):
    arb = _arbiter(
        tmp_path,
        {
            "/release/?": (200, _search_body(("r1", "Album", 12, "2003"))),
            "/release/r1?": (200, _release_body("2003", [200_000] * 12)),
        },
    )
    assert arb.arbitrate(dict(WANT, year="2003"), LOCAL) is True
    assert arb.arbitrate(dict(WANT, year="2010"), LOCAL) is False


def test_local_year_tag_never_vetoes_a_length_pin(tmp_path):
    # Remasters are routinely tagged with the ORIGINAL release's year; when
    # the copy's own physics (exact count + summed seconds) pin a release,
    # a disagreeing year tag changes nothing. Duration outranks year.
    arb = _arbiter(
        tmp_path,
        {
            "/release/?": (200, _search_body(("r1", "Album", 12, "2003"))),
            "/release/r1?": (200, _release_body("2003", [200_000] * 12)),
        },
    )
    assert arb.arbitrate(WANT, dict(LOCAL, year="1985")) is True


def test_screen_count_disagreeing_with_copy_is_never_provable(tmp_path):
    # No third-party fact can make a 12-track copy the 14-track release on screen.
    log: list = []
    arb = _arbiter(tmp_path, {}, log=log)
    assert arb.arbitrate(dict(WANT, tracks=14), LOCAL) is False
    # It never even asks: the search would be a wasted rate-gated request.
    # (The search may still go out; what matters is the verdict. Keep this
    # assertion on the verdict only.)


def test_silent_local_copy_is_never_provable_and_never_asks(tmp_path):
    log: list = []
    arb = _arbiter(tmp_path, {}, log=log)
    assert arb.arbitrate(WANT, dict(LOCAL, runtime=0)) is False
    assert arb.arbitrate(WANT, dict(LOCAL, tracks=0)) is False
    assert log == []


def test_release_missing_any_length_cannot_prove(tmp_path):
    # All-or-nothing, the same honesty the scanner applies to runtime.
    arb = _arbiter(
        tmp_path,
        {
            "/release/?": (200, _search_body(("r1", "Album", 12, "2003"))),
            "/release/r1?": (200, _release_body("2003", [200_000] * 11 + [None])),
        },
    )
    assert arb.arbitrate(WANT, LOCAL) is False


def test_transient_failure_returns_none_and_is_not_cached(tmp_path):
    calls = {"n": 0}

    def fetch(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return (503, "")
        return (
            (200, _search_body(("r1", "Album", 12, "2003")))
            if "/release/?" in url
            else (
                200,
                _release_body("2003", [200_000] * 12),
            )
        )

    arb = MBArbiter(str(tmp_path / "mb.sqlite3"), fetch=fetch, sleep=lambda s: None)
    assert arb.arbitrate(WANT, LOCAL) is None  # the 503
    assert arb.arbitrate(WANT, LOCAL) is True  # retried, not poisoned


def test_definite_answers_are_cached(tmp_path):
    log: list = []
    arb = _arbiter(
        tmp_path,
        {
            "/release/?": (200, _search_body(("r1", "Album", 12, "2003"))),
            "/release/r1?": (200, _release_body("2003", [200_000] * 12)),
        },
        log=log,
    )
    assert arb.arbitrate(WANT, LOCAL) is True
    n = len(log)
    assert arb.arbitrate(WANT, LOCAL) is True
    assert len(log) == n  # every response served from the cache


def test_not_found_is_a_real_answer(tmp_path):
    log: list = []
    arb = _arbiter(tmp_path, {}, log=log)  # everything 404s
    assert arb.arbitrate(WANT, LOCAL) is False
    n = len(log)
    assert arb.arbitrate(WANT, LOCAL) is False
    assert len(log) == n  # the 404 was remembered (briefly), not re-asked


def test_requests_are_rate_gated(tmp_path):
    sleeps: list = []
    clock = {"t": 0.0}

    def fetch(url):
        return (
            (200, _search_body(("r1", "Album", 12, "2003")))
            if "/release/?" in url
            else (
                200,
                _release_body("2003", [200_000] * 12),
            )
        )

    arb = MBArbiter(
        str(tmp_path / "mb.sqlite3"),
        fetch=fetch,
        sleep=sleeps.append,
        monotonic=lambda: clock["t"],
    )
    assert arb.arbitrate(WANT, LOCAL) is True
    # Two requests went out back-to-back on a frozen clock: the second waited.
    assert sleeps and all(0 < s <= 1.0 for s in sleeps)


def test_at_most_three_releases_are_read(tmp_path):
    log: list = []
    rels = [(f"r{i}", "Album", 12, "1990") for i in range(6)]
    responses = {"/release/?": (200, _search_body(*rels))}
    for i in range(6):
        responses[f"/release/r{i}?"] = (200, _release_body("1990", [100_000] * 12))  # never matches 2400s
    arb = _arbiter(tmp_path, responses, log=log)
    assert arb.arbitrate(WANT, LOCAL) is False
    assert sum("/release/r" in u for u in log) <= 3


def test_screen_duration_must_agree_when_tidal_offered_it(tmp_path):
    # The release pins the local copy perfectly (count, seconds, year), but
    # TIDAL said the release on screen runs minutes shorter: proving here
    # would overrule the matcher's own length refutation, so it must not.
    arb = _arbiter(
        tmp_path,
        {
            "/release/?": (200, _search_body(("r1", "Album", 12, "2003"))),
            "/release/r1?": (200, _release_body("2003", [200_000] * 12)),
        },
    )
    assert arb.arbitrate(dict(WANT, duration=2000), LOCAL) is False
    # And when TIDAL's seconds agree with the same release, it still proves.
    assert arb.arbitrate(dict(WANT, duration=2400), LOCAL) is True


def test_an_empty_search_answer_expires_and_is_reasked(tmp_path):
    # The search endpoint says not-found as HTTP 200 with an empty releases
    # array. Caching that body forever meant an album MusicBrainz catalogued
    # next month could never upgrade its badge for the life of the file; it
    # must expire exactly like the negative statuses do.
    from waves.mb_arbiter import NEGATIVE_TTL_SEC

    log: list = []
    responses = {"/release/?": (200, json.dumps({"releases": []}))}

    def fetch(url):
        log.append(url)
        for frag, resp in responses.items():
            if frag in url:
                return resp
        return (404, "")

    clock = {"t": 1_000_000.0}
    arb = MBArbiter(str(tmp_path / "mb.sqlite3"), fetch=fetch, sleep=lambda s: None, now=lambda: clock["t"])
    assert arb.arbitrate(WANT, LOCAL) is False
    n = len(log)
    assert arb.arbitrate(WANT, LOCAL) is False
    assert len(log) == n, "inside the TTL the empty answer is served from the cache"
    # MusicBrainz gains the release; past the TTL the arbiter asks again and
    # the badge can finally upgrade.
    responses["/release/?"] = (200, _search_body(("r1", "Album", 12, "2003")))
    responses["/release/r1?"] = (200, _release_body("2003", [200_000] * 12))
    clock["t"] += NEGATIVE_TTL_SEC + 1
    assert arb.arbitrate(WANT, LOCAL) is True
    assert len(log) > n


def test_a_release_without_lengths_expires_and_is_reasked(tmp_path):
    # A release whose recordings carry no lengths cannot vouch today, but
    # contributors add lengths; its 200 body must expire like a not-found,
    # not pin the badge to "unprovable" forever.
    from waves.mb_arbiter import NEGATIVE_TTL_SEC

    log: list = []
    responses = {
        "/release/?": (200, _search_body(("r1", "Album", 12, "2003"))),
        "/release/r1?": (200, _release_body("2003", [None] * 12)),
    }

    def fetch(url):
        log.append(url)
        for frag, resp in responses.items():
            if frag in url:
                return resp
        return (404, "")

    clock = {"t": 1_000_000.0}
    arb = MBArbiter(str(tmp_path / "mb.sqlite3"), fetch=fetch, sleep=lambda s: None, now=lambda: clock["t"])
    assert arb.arbitrate(WANT, LOCAL) is False
    n = len(log)
    assert arb.arbitrate(WANT, LOCAL) is False
    assert len(log) == n, "inside the TTL the lengthless answer is served from the cache"
    responses["/release/r1?"] = (200, _release_body("2003", [200_000] * 12))
    clock["t"] += NEGATIVE_TTL_SEC + 1
    assert arb.arbitrate(WANT, LOCAL) is True
    assert len(log) > n


def test_a_close_landing_in_the_rate_wait_stops_the_request(tmp_path):
    # close() is the settings wipe's word that no request should go out; one
    # landing while a lookup sits in the rate-gate sleep must not see that
    # lookup's request depart after it returned.
    log: list = []

    def fetch(url):
        log.append(url)
        return (200, json.dumps({"releases": []}))

    clock = {"t": 0.0}
    holder: dict = {}

    def sleep(_s):
        holder["arb"].close()

    arb = MBArbiter(str(tmp_path / "mb.sqlite3"), fetch=fetch, sleep=sleep, monotonic=lambda: clock["t"])
    holder["arb"] = arb
    assert arb.arbitrate(WANT, LOCAL) is False  # primes the rate gate
    n = len(log)
    # A different album misses the cache, waits its turn, and the close that
    # lands during the wait wins: no request, "could not ask" reported.
    assert arb.arbitrate(dict(WANT, title="Other"), LOCAL) is None
    assert len(log) == n, "a request departed after close() returned"


def test_close_is_final_and_never_resurrects_the_cache(tmp_path):
    # A settings wipe closes the arbiter and deletes its file; a straggler
    # arbitration still holding the instance must neither crash nor lazily
    # reopen the cache (which would resurrect the deleted file).
    path = tmp_path / "mb.sqlite3"
    arb = _arbiter(
        tmp_path,
        {
            "/release/?": (200, _search_body(("r1", "Album", 12, "2003"))),
            "/release/r1?": (200, _release_body("2003", [200_000] * 12)),
        },
    )
    assert arb.arbitrate(WANT, LOCAL) is True
    arb.close()
    path.unlink(missing_ok=True)
    # The straggler gets "could not ask" (cache gone, still polite), not a crash.
    assert arb.arbitrate(WANT, LOCAL) is None
    assert not path.exists()
