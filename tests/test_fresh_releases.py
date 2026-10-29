"""Tests for the fresh-releases hint (waves/fresh_releases.py) and its Qt
layer in bridge_releases: the list is read and matched by MBID or name key,
a hinted follow goes ahead of the round, the schedule shortens on release
night, a wall-clock jump reads as a wake, and a refusal keeps away.

Hermetic: canned answers through the injectable fetch, the real store on a
temporary file, the bridge methods bound onto the releases test stub.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import io
import json
import time
import urllib.error
from types import SimpleNamespace

import pytest
from conftest import _InlinePool, _Signal
from test_releases_backend import _bind, _Stub, _stub, _Timer

from waves import fresh_releases as fr
from waves.follows import FollowStore
from waves.tls import tls_context
from waves.waves_ui import bridge_releases as rel
from waves.waves_ui.backend import WavesBridge

for _m in (
    "_releases_fresh_on",
    "_releases_fresh_every",
    "_releases_fresh_tick",
    "_releases_fresh_read",
    "_releases_fresh_sources",
    "_releases_fresh_drop_held",
    "_releases_tidal_feed",
    "_on_rel_fresh_done",
    "_releases_fresh_switched",
):
    setattr(_Stub, _m, getattr(WavesBridge, _m))


def _body(*releases) -> bytes:
    return json.dumps({"payload": {"releases": list(releases), "total_count": len(releases)}}).encode()


def _rel(rgid, day, credit, mbids=()):
    return {
        "artist_credit_name": credit,
        "artist_mbids": list(mbids),
        "release_date": day,
        "release_group_mbid": rgid,
        "release_mbid": "r-" + rgid,
        "release_name": "T",
    }


# --- the pure module ----------------------------------------------------------


def test_read_fresh_asks_for_the_window_and_keeps_only_whole_rows():
    asked = []

    def fetch(url):
        asked.append(url)
        return _body(_rel("g1", "2026-10-16", "A"), {"release_date": "2026-10-16"}, "junk", _rel("g2", "bad", "B"))

    rows = fr.read_fresh(7, fetch)
    assert rows == [{"rgid": "g1", "day": "2026-10-16", "credit": "A", "mbids": []}]
    assert asked == [fr.LB_FRESH_URL + "?days=7&past=true&future=true"]
    # The window is clamped to what the service allows.
    fr.read_fresh(500, fetch)
    assert "days=90" in asked[-1]


@pytest.mark.parametrize("raw", [b"not json", b'{"payload": {}}', b'{"payload": {"releases": 3}}', b"[]"])
def test_read_fresh_refuses_an_answer_that_is_not_the_list(raw):
    with pytest.raises(fr.FetchError):
        fr.read_fresh(7, lambda url: raw)


def test_credit_keys_split_joined_artists_but_not_band_names():
    keys = fr.credit_keys("Alpha feat. Beta & Gamma")
    assert {fr.name_key("Alpha"), fr.name_key("Beta"), fr.name_key("Gamma")} <= keys
    assert fr.name_key("Alpha feat. Beta & Gamma") in keys
    assert fr.name_key("Florence") not in fr.credit_keys("Florence and the Machine")
    assert fr.name_key("Florence and the Machine") in fr.credit_keys("Florence and the Machine")


def _follow(aid, name, checked=0, mbid=""):
    return {"artist_id": aid, "name": name, "last_checked": checked, "mbid": mbid}


def test_hint_names_a_follow_not_checked_since_the_release_day():
    day_open = fr.day_start("2026-10-16")
    fresh = [
        {"rgid": "g1", "day": "2026-10-16", "credit": "Alpha", "mbids": []},
        {"rgid": "g2", "day": "2026-10-16", "credit": "Beta", "mbids": []},
        {"rgid": "g3", "day": "2026-10-20", "credit": "Gamma", "mbids": []},
    ]
    follows = [
        _follow("a", "Alpha", day_open - 60),  # checked before midnight: hinted
        _follow("b", "Beta", day_open + 60),  # checked since: not
        _follow("c", "Gamma", 0),  # dated ahead: not yet
    ]
    names, seen = fr.hinted_artists(fresh, follows, today="2026-10-16", known=None)
    assert names == ["a"]
    assert seen == {"g1", "g2", "g3"}


def test_hint_names_a_follow_for_a_release_new_to_the_list():
    day_open = fr.day_start("2026-10-12")
    fresh = [{"rgid": "late", "day": "2026-10-12", "credit": "Beta", "mbids": []}]
    follows = [_follow("b", "Beta", day_open + 86400)]
    # The first read has no memory: nothing is new.
    assert fr.hinted_artists(fresh, follows, today="2026-10-16", known=None)[0] == []
    # A later read that did not see it before: a release filed late.
    assert fr.hinted_artists(fresh, follows, today="2026-10-16", known={"other"})[0] == ["b"]
    assert fr.hinted_artists(fresh, follows, today="2026-10-16", known={"late"})[0] == []


def test_hint_prefers_the_mbid_over_a_namesake():
    fresh = [{"rgid": "g", "day": "2026-10-16", "credit": "Nirvana", "mbids": ["m-uk"]}]
    follows = [_follow("us", "Nirvana", mbid="m-us"), _follow("uk", "Nirvana (UK)", mbid="m-uk")]
    assert fr.hinted_artists(fresh, follows, today="2026-10-16", known=None)[0] == ["uk"]
    # A follow whose MBID did not match is someone else, whatever the name.
    follows = [_follow("us", "Nirvana", mbid="m-us")]
    assert fr.hinted_artists(fresh, follows, today="2026-10-16", known=None)[0] == []
    # A follow with no MBID on record: the name key decides.
    follows = [_follow("us", "Nirvana")]
    assert fr.hinted_artists(fresh, follows, today="2026-10-16", known=None)[0] == ["us"]


def test_a_release_group_whose_hint_fell_past_the_cap_stays_new():
    fresh = [
        {"rgid": "new", "day": "2026-10-16", "credit": "First", "mbids": []},
        {"rgid": "late", "day": "2026-10-10", "credit": "Second", "mbids": []},
        {"rgid": "shared", "day": "2026-10-09", "credit": "First & Second", "mbids": []},
    ]
    follows = [_follow("a", "First", 1), _follow("b", "Second", 1)]
    names, seen = fr.hinted_artists(fresh, follows, today="2026-10-16", known=set(), cap=1)
    assert names == ["a"]
    # "late" hinted only the follow cut: not remembered, so the next read
    # still sees it as new. "shared" also hinted a follow taken: remembered.
    assert seen == {"new", "shared"}


def test_hint_orders_the_newest_release_first():
    fresh = [
        {"rgid": "g1", "day": "2026-10-10", "credit": "Old", "mbids": []},
        {"rgid": "g2", "day": "2026-10-16", "credit": "New", "mbids": []},
    ]
    names, _ = fr.hinted_artists(fresh, [_follow("o", "Old"), _follow("n", "New")], today="2026-10-16", known=None)
    assert names == ["n", "o"]


def test_release_window_is_thursday_night_into_friday_morning():
    thu = _dt.datetime(2026, 10, 15)  # a Thursday
    assert thu.weekday() == 3
    assert not fr.in_release_window(thu.replace(hour=21, minute=59))
    assert fr.in_release_window(thu.replace(hour=22))
    assert fr.in_release_window((thu + _dt.timedelta(days=1)).replace(hour=5, minute=59))
    assert not fr.in_release_window((thu + _dt.timedelta(days=1)).replace(hour=6))
    assert not fr.in_release_window(_dt.datetime(2026, 10, 18, 23))


def test_day_start_is_local_midnight():
    assert fr.day_start("2026-10-16") == int(time.mktime((2026, 10, 16, 0, 0, 0, 0, 0, -1)))
    assert fr.day_start("nope") == 0


class _Resp(io.BytesIO):
    def __init__(self, data, headers):
        super().__init__(data)
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_http_fetch_unpacks_gzip_and_names_the_pause_on_a_refusal(monkeypatch):
    sent = []

    def ok(req, timeout, context=None):
        sent.append(req)
        assert context is tls_context()
        return _Resp(gzip.compress(b"hello"), {"Content-Encoding": "gzip"})

    monkeypatch.setattr(fr.urllib.request, "urlopen", ok)
    assert fr.http_fetch("https://api.listenbrainz.org/x", "Waves/test") == b"hello"
    assert sent[0].get_header("User-agent") == "Waves/test"
    assert sent[0].get_header("Accept-encoding") == "gzip"

    def refused(req, timeout, context=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"X-RateLimit-Reset-In": "7200"}, None)

    monkeypatch.setattr(fr.urllib.request, "urlopen", refused)
    with pytest.raises(fr.FetchError) as e:
        fr.http_fetch("https://api.listenbrainz.org/x", "Waves/test")
    assert e.value.retry_after == 7200

    def short(req, timeout, context=None):
        raise urllib.error.HTTPError(req.full_url, 503, "Busy", {"Retry-After": "5"}, None)

    monkeypatch.setattr(fr.urllib.request, "urlopen", short)
    with pytest.raises(fr.FetchError) as e:
        fr.http_fetch("https://api.listenbrainz.org/x", "Waves/test")
    # Never sooner than the floor, whatever the answer says.
    assert e.value.retry_after == fr.MIN_BACKOFF_SEC

    def down(req, timeout, context=None):
        raise OSError("unreachable")

    monkeypatch.setattr(fr.urllib.request, "urlopen", down)
    with pytest.raises(fr.FetchError) as e:
        fr.http_fetch("https://api.listenbrainz.org/x", "Waves/test")
    # Never reached (no network yet after a wake): it tries again soon.
    assert e.value.offline and e.value.retry_after == fr.OFFLINE_RETRY_SEC < fr.MIN_BACKOFF_SEC


# --- the store ------------------------------------------------------------------


def test_a_hinted_follow_goes_ahead_of_the_round_and_a_check_clears_it(tmp_path):
    st = FollowStore(str(tmp_path / "f.sqlite3"))
    for aid in ("a", "b", "c"):
        st.follow(aid, aid.upper(), "")
        st.record_check(aid, [{"id": f"r{aid}", "date": "2026-01-01"}])
    now = int(time.time())
    # Nothing is due inside the round.
    assert st.next_due(now - 3600) is None
    assert st.hint(["b", "gone"]) == 1
    assert st.hint(["b"]) == 0  # already waiting
    assert st.hinted_count() == 1
    assert st.next_due(now - 3600)["artist_id"] == "b"
    st.record_check("b", [{"id": "rb", "date": "2026-01-01"}])
    assert st.hinted_count() == 0 and st.next_due(now - 3600) is None
    st.hint(["c"])
    st.record_failure("c")
    assert st.hinted_count() == 0


def test_a_never_checked_follow_still_goes_before_a_hinted_one(tmp_path):
    st = FollowStore(str(tmp_path / "f.sqlite3"))
    st.follow("old", "Old", "")
    st.record_check("old", [])
    st.follow("new", "New", "")
    st.hint(["old"])
    assert st.next_due(int(time.time()) - 3600)["artist_id"] == "new"


def test_an_older_store_grows_the_hinted_column(tmp_path):
    path = str(tmp_path / "f.sqlite3")
    st = FollowStore(path)
    st.follow("a", "A", "")
    st._conn.execute("ALTER TABLE followed DROP COLUMN hinted")
    st._conn.commit()
    st.close()
    st = FollowStore(path)
    assert st.hint(["a"]) == 1


# --- the bridge -------------------------------------------------------------------


def _fresh_stub(tmp_path, **prefs):
    s = _stub(tmp_path, prefs={"releases_fresh_hints": "on", **prefs})
    s._rel_fresh_pool = _InlinePool()
    s._rel_fresh_running = False
    s._rel_fresh_beat_at = 0.0
    s._rel_fresh_backoff_until = 0.0
    s._rel_feed_backoff_until = 0.0
    s._rel_fresh_timer = _Timer()
    s._relFreshDone = _Signal()
    return s


def _checked(s, *names):
    for name in names:
        s._releases.follow(name.lower(), name, "")
        s._releases.record_check(name.lower(), [{"id": "r" + name, "date": "2026-01-01"}])


def test_tick_reads_when_due_and_not_again_until_the_interval(tmp_path, monkeypatch):
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha")
    reads = []
    s._releases_fresh_read = lambda gen, today, **kw: reads.append((gen, today))
    tick = _bind(s, "_releases_fresh_tick")
    tick()
    assert len(reads) == 1 and s._rel_fresh_running is True
    s._rel_fresh_running = False
    s._releases.meta_set("fresh_read_at", str(int(time.time())))
    tick()
    assert len(reads) == 1
    # A stamp ahead of the clock (set back) reads as due.
    s._releases.meta_set("fresh_read_at", str(int(time.time()) + 86400))
    tick()
    assert len(reads) == 2


def test_tick_holds_while_gated_off_seeding_or_backing_off(tmp_path):
    s = _fresh_stub(tmp_path)
    reads = []
    s._releases_fresh_read = lambda gen, today, **kw: reads.append(1)
    tick = _bind(s, "_releases_fresh_tick")
    tick()  # no follows
    s._releases.follow("a", "A", "")
    tick()  # the first round has not read it yet
    s._releases.record_check("a", [])
    s._waves_prefs["releases_fresh_hints"] = "off"
    tick()
    s._waves_prefs["releases_fresh_hints"] = "on"
    s._logged_in = False
    tick()
    s._logged_in = True
    s._rel_fresh_backoff_until = time.monotonic() + 600
    s._rel_feed_backoff_until = time.monotonic() + 600
    tick()
    assert reads == []
    s._rel_fresh_backoff_until = s._rel_feed_backoff_until = 0.0
    tick()
    assert reads == [1]


def test_a_wall_clock_jump_reads_at_once(tmp_path, monkeypatch, caplog):
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha")
    reads = []
    s._releases_fresh_read = lambda gen, today, **kw: reads.append(1)
    clock = [1_800_000_000.0]
    monkeypatch.setattr(rel.time, "time", lambda: clock[0])
    s._releases.meta_set("fresh_read_at", str(int(clock[0])))
    s._rel_fresh_beat_at = clock[0]
    tick = _bind(s, "_releases_fresh_tick")
    clock[0] += rel._REL_FRESH_TICK_MS / 1000
    tick()
    assert reads == []
    # Asleep for an hour: well past one beat.
    clock[0] += 3600
    with caplog.at_level("INFO", logger="waves.releases"):
        tick()
    assert reads == [1]
    assert "woke" in caplog.text
    s._rel_fresh_running = False
    s._releases.meta_set("fresh_read_at", str(int(clock[0])))
    # A wake while a pause holds is kept for the first beat that can read.
    clock[0] += 3600
    s._rel_fresh_backoff_until = s._rel_feed_backoff_until = time.monotonic() + 400
    tick()
    assert reads == [1]
    s._rel_fresh_backoff_until = s._rel_feed_backoff_until = 0.0
    s._releases.meta_set("fresh_read_at", str(int(clock[0])))
    clock[0] += rel._REL_FRESH_TICK_MS / 1000
    tick()
    assert reads == [1, 1]
    s._rel_fresh_running = False
    clock[0] += rel._REL_FRESH_TICK_MS / 1000
    tick()
    assert reads == [1, 1]


def test_release_night_reads_every_half_hour(tmp_path):
    s = _fresh_stub(tmp_path)
    every = _bind(s, "_releases_fresh_every")
    night = _dt.datetime(2026, 10, 15, 23).timestamp()
    day = _dt.datetime(2026, 10, 14, 12).timestamp()
    assert every(night) == rel._REL_FRESH_NIGHT_S
    assert every(day) == rel._REL_FRESH_EVERY_S


def test_read_hints_follows_remembers_the_list_and_posts_back(tmp_path):
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha", "Beta")
    # Checked a day before the release.
    s._releases._conn.execute("UPDATE followed SET last_checked = ?", (fr.day_start("2026-10-16") - 86400,))
    s._releases._conn.commit()
    body = _body(_rel("g1", "2026-10-16", "Alpha"), _rel("g2", "2026-10-16", "Someone Else"))
    _bind(s, "_releases_fresh_read")(0, "2026-10-16", lambda url: body, use_tidal=False)
    assert s._releases.hinted_count() == 1
    assert s._relFreshDone.emits == [{"gen": 0, "hinted": 1, "lb_count": 2}]
    known = json.loads(s._releases.meta_get("fresh_known"))
    assert known == {"g1": "2026-10-16", "g2": "2026-10-16"}
    assert int(s._releases.meta_get("fresh_read_at")) > 0


def test_read_forgets_release_groups_long_out_of_the_window(tmp_path):
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha")
    s._releases.meta_set("fresh_known", json.dumps({"old": "2026-09-01", "recent": "2026-10-12"}))
    _bind(s, "_releases_fresh_read")(0, "2026-10-16", lambda url: _body(_rel("g1", "2026-10-16", "X")), use_tidal=False)
    assert set(json.loads(s._releases.meta_get("fresh_known"))) == {"recent", "g1"}


def test_read_caps_how_many_follows_one_read_puts_ahead(tmp_path, monkeypatch):
    s = _fresh_stub(tmp_path)
    monkeypatch.setattr(rel, "_REL_FRESH_HINT_CAP", 2)
    names = [f"Artist {i}" for i in range(5)]
    _checked(s, *names)
    s._releases._conn.execute("UPDATE followed SET last_checked = 1")
    s._releases._conn.commit()
    body = _body(*[_rel(f"g{i}", "2026-10-16", n) for i, n in enumerate(names)])
    _bind(s, "_releases_fresh_read")(0, "2026-10-16", lambda url: body, use_tidal=False)
    assert s._releases.hinted_count() == 2


def test_read_failure_posts_the_pause_and_a_stale_generation_posts_nothing(tmp_path):
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha")

    def refused(url):
        raise fr.FetchError("HTTP 429", 7200)

    _bind(s, "_releases_fresh_read")(0, "2026-10-16", refused, use_tidal=False)
    assert s._relFreshDone.emits[-1]["lb_retry"] == 7200
    # Nothing was read: the schedule is not stamped, the next beat tries again.
    assert s._releases.meta_get("fresh_read_at", "") == ""
    s._rel_gen = 1
    _bind(s, "_releases_fresh_read")(0, "2026-10-16", lambda url: _body(), use_tidal=False)
    assert len(s._relFreshDone.emits) == 1


def test_done_backs_off_on_failure_and_brings_the_sweep_forward_on_a_hint(tmp_path, monkeypatch):
    s = _fresh_stub(tmp_path)
    monkeypatch.setattr(rel.time, "monotonic", lambda: 1000.0)
    s._rel_fresh_running = True
    done = _bind(s, "_on_rel_fresh_done")
    done({"gen": 0, "lb_retry": 1800.0, "tidal_count": 4})
    assert s._rel_fresh_backoff_until == 2800.0 and s._rel_fresh_running is False
    assert s._rel_feed_backoff_until == 0.0
    done({"gen": 0, "hinted": 0, "lb_count": 9, "tidal_retry": 600.0})
    assert s._rel_feed_backoff_until == 1600.0
    assert s._rel_fresh_backoff_until == 0.0 and s._rel_timer.starts == []
    s._rel_timer.remainingTime = lambda: 60_000
    s._rel_timer._active = True
    done({"gen": 0, "hinted": 3})
    assert s._rel_timer.starts == [rel._REL_MIN_STEP_MS]
    # Already due sooner: left alone.
    s._rel_timer.remainingTime = lambda: 100
    done({"gen": 0, "hinted": 3})
    assert s._rel_timer.starts == [rel._REL_MIN_STEP_MS]
    # A step in flight rearms the pacer itself when it lands.
    s._rel_step_running = True
    s._rel_timer.remainingTime = lambda: 60_000
    done({"gen": 0, "hinted": 3})
    assert len(s._rel_timer.starts) == 1
    # A late answer from a previous account is dropped.
    s._rel_fresh_running = True
    done({"gen": 5, "lb_retry": 1.0})
    assert s._rel_fresh_running is True


def test_hinted_follows_set_the_burst_pace(tmp_path):
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha", "Beta", "Gamma")
    assert _bind(s, "_releases_step_ms")() == rel._REL_MAX_STEP_MS
    s._releases.hint(["alpha"])
    assert _bind(s, "_releases_step_ms")() == rel._REL_BURST_MS


def test_the_sweep_checks_a_hinted_follow_inside_the_round(tmp_path):
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha", "Beta")
    s._releases.hint(["beta"])
    picked = []
    s._releases_check_one = lambda row, gen: picked.append(row["artist_id"])
    s._rel_step_at = 0.0
    _bind(s, "_releases_tick")()
    assert picked == ["beta"]


def test_switching_on_reads_on_the_next_beat(tmp_path):
    s = _fresh_stub(tmp_path)
    s._releases.meta_set("fresh_read_at", str(int(time.time())))
    _bind(s, "_releases_fresh_switched")(True)
    assert s._releases.meta_get("fresh_read_at") == "0"


def test_start_arms_the_beat_and_stop_halts_it(tmp_path):
    s = _fresh_stub(tmp_path)
    _bind(s, "_releases_maybe_start")()
    assert s._rel_fresh_timer.starts == [None]
    # A second start while a read runs leaves it marked running.
    s._rel_fresh_running = True
    _bind(s, "_releases_maybe_start")()
    assert s._rel_fresh_running is True
    _bind(s, "_releases_stop")()
    assert s._rel_fresh_timer.stops == 1 and s._rel_fresh_running is False


def test_two_reads_in_a_row_do_not_hint_a_follow_checked_between(tmp_path):
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha")
    s._releases._conn.execute("UPDATE followed SET last_checked = 1")
    s._releases._conn.commit()
    body = _body(_rel("g1", "2026-10-16", "Alpha"))
    read = _bind(s, "_releases_fresh_read")
    read(0, "2026-10-16", lambda url: body, use_tidal=False)
    assert s._releases.hinted_count() == 1
    s._releases.record_check("alpha", [{"id": "rAlpha", "date": "2026-01-01"}])
    read(0, "2026-10-16", lambda url: body, use_tidal=False)
    assert s._releases.hinted_count() == 0


def test_a_rate_limited_step_keeps_the_hint(tmp_path, monkeypatch):
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha")
    before = s._releases.follow_info("alpha")
    s._releases.hint(["alpha"])
    s._artist_recent_releases = lambda aid: ([], [], True)
    _bind(s, "_releases_check_one")({"artist_id": "alpha", "name": "Alpha"}, 0)
    assert s._relStepDone.emits[-1]["rate_limited"] is True
    assert s._releases.hinted_count() == 1
    assert s._releases.follow_info("alpha").get("last_checked") == before.get("last_checked")


def test_the_setting_is_on_by_default_and_a_two_way_choice_under_releases():
    from test_releases_settings_placement import _schema_stub

    stub = _schema_stub()
    assert stub._waves_prefs["releases_fresh_hints"] == "on"
    schema = WavesBridge.settingsSchema(stub)
    section = next(s for s in schema if s["id"] == "releases")
    row = next(f for f in section["fields"] if f["key"] == "releases_fresh_hints")
    assert row["type"] == "enum" and row["value"] == "on"
    assert [o["label"] for o in row["options"]] == ["On", "Off"]
    s = _Stub()
    s._waves_prefs = {"releases_fresh_hints": "off"}
    assert _bind(s, "_releases_fresh_on")() is False
    s._waves_prefs = {"releases_fresh_hints": "on"}
    assert _bind(s, "_releases_fresh_on")() is True


# --- TIDAL's feed -------------------------------------------------------------------


def _act(album_id, day, *artist_ids, kind="NEW_ALBUM_RELEASE"):
    return {
        "followableActivity": {
            "album": {
                "id": album_id,
                "releaseDate": day,
                "type": "SINGLE",
                "mainArtists": [{"id": a, "name": "N"} for a in artist_ids],
                "artists": [{"id": a, "name": "N"} for a in artist_ids],
            },
            "activityType": kind,
            "occurredAt": day + "T00:00:00.000+0000",
        },
        "seen": False,
    }


def test_parse_tidal_feed_keeps_only_new_releases_with_ids():
    body = {
        "activities": [
            _act(11, "2026-10-16", 5, 6),
            _act(12, "2026-10-16", 5, kind="NEW_HISTORY_MIX"),
            _act(None, "2026-10-16", 5),
            {"followableActivity": None},
            "junk",
        ],
        "stats": {"totalNotSeenActivities": 3},
    }
    assert fr.parse_tidal_feed(body) == [
        {"rgid": "t:11", "day": "2026-10-16", "credit": "", "mbids": [], "tidal_ids": ["5", "6"], "release_id": "11"}
    ]
    for bad in ({}, {"activities": 3}, None, []):
        with pytest.raises(fr.FetchError):
            fr.parse_tidal_feed(bad)


def test_a_tidal_release_matches_by_artist_id_never_by_name():
    fresh = [{"rgid": "t:1", "day": "2026-10-16", "credit": "", "mbids": [], "tidal_ids": ["5"], "release_id": "1"}]
    follows = [_follow("5", "Somebody", 1), _follow("6", "Somebody", 1)]
    assert fr.hinted_artists(fresh, follows, today="2026-10-16", known=None)[0] == ["5"]


class _TidalResp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _tidal(stub, resp, asked):
    def basic_request(method, path, params=None, base_url=None):
        asked.append((method, path, sorted(params), base_url))
        return resp

    request = SimpleNamespace(basic_request=basic_request)
    stub.tidal = SimpleNamespace(session=SimpleNamespace(request=request, user=SimpleNamespace(id=42)))


def test_the_tidal_feed_is_only_ever_read(tmp_path):
    s = _fresh_stub(tmp_path)
    asked = []
    _tidal(s, _TidalResp(200, {"activities": [_act(11, "2026-10-16", 5)]}), asked)
    rows = _bind(s, "_releases_tidal_feed")()
    assert rows[0]["release_id"] == "11"
    assert asked == [("GET", "feed/activities", ["userId"], "https://api.tidal.com/v2/")]


def test_the_tidal_feed_names_its_refusal(tmp_path):
    s = _fresh_stub(tmp_path)
    _tidal(s, _TidalResp(429, None, {"Retry-After": "3600"}), [])
    with pytest.raises(fr.FetchError) as e:
        _bind(s, "_releases_tidal_feed")()
    assert e.value.retry_after == 3600 and "429" in str(e.value)
    _tidal(s, _TidalResp(200, None), [])
    with pytest.raises(fr.FetchError):
        _bind(s, "_releases_tidal_feed")()


def test_read_hints_a_follow_from_the_tidal_feed_unless_its_catalog_holds_it(tmp_path):
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha", "Beta")
    s._releases._conn.execute("UPDATE followed SET last_checked = 1")
    s._releases._conn.commit()
    rows = [
        {"rgid": "t:new", "day": "2026-10-16", "credit": "", "mbids": [], "tidal_ids": ["alpha"], "release_id": "new"},
        # Beta's catalog already holds this one.
        {
            "rgid": "t:rBeta",
            "day": "2026-10-16",
            "credit": "",
            "mbids": [],
            "tidal_ids": ["beta"],
            "release_id": "rBeta",
        },
    ]
    _bind(s, "_releases_fresh_read")(0, "2026-10-16", None, lambda: [dict(r) for r in rows], use_lb=False)
    assert s._relFreshDone.emits == [{"gen": 0, "hinted": 1, "tidal_count": 2}]
    assert s._releases.next_due(int(time.time()) - 3600)["artist_id"] == "alpha"


def test_a_release_the_feed_keeps_showing_is_hinted_once(tmp_path):
    # TIDAL's feed shows a month; a release dated past the keep window must
    # stay remembered while it is listed, or it would read as new each read.
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha")
    rows = [
        {"rgid": "t:old", "day": "2026-09-20", "credit": "", "mbids": [], "tidal_ids": ["alpha"], "release_id": "old"}
    ]
    read = _bind(s, "_releases_fresh_read")
    read(0, "2026-10-16", None, lambda: [dict(r) for r in rows], use_lb=False)
    assert s._relFreshDone.emits[-1]["hinted"] == 0
    assert json.loads(s._releases.meta_get("fresh_known")) == {"t:old": "2026-10-16"}
    # A day later, still listed: known, so not new, and its day is long past.
    read(0, "2026-10-17", None, lambda: [dict(r) for r in rows], use_lb=False)
    assert s._relFreshDone.emits[-1]["hinted"] == 0
    # Once it drops off every list it is forgotten after the keep window.
    read(0, "2026-10-28", None, lambda: [], use_lb=False)
    assert json.loads(s._releases.meta_get("fresh_known")) == {}


def test_switching_off_drops_the_waiting_hints(tmp_path):
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha", "Beta")
    s._releases.hint(["alpha"])
    assert _bind(s, "_releases_step_ms")() == rel._REL_BURST_MS
    _bind(s, "_releases_fresh_switched")(False)
    assert s._releases.hinted_count() == 0
    assert _bind(s, "_releases_step_ms")() == rel._REL_MAX_STEP_MS


def test_a_feed_that_never_connected_tries_again_soon(tmp_path):
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha")

    def no_network():
        raise ConnectionError("network is unreachable")

    _bind(s, "_releases_fresh_read")(0, "2026-10-16", lambda url: _body(), no_network)
    assert s._relFreshDone.emits[-1]["tidal_retry"] == fr.OFFLINE_RETRY_SEC


def test_one_list_failing_never_costs_the_other(tmp_path):
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha")
    s._releases._conn.execute("UPDATE followed SET last_checked = 1")
    s._releases._conn.commit()

    def refused():
        raise fr.FetchError("HTTP 429", 900)

    _bind(s, "_releases_fresh_read")(0, "2026-10-16", lambda url: _body(_rel("g1", "2026-10-16", "Alpha")), refused)
    out = s._relFreshDone.emits[-1]
    assert out["hinted"] == 1 and out["lb_count"] == 1
    assert out["tidal_retry"] == fr.MIN_BACKOFF_SEC and out["tidal_limited"] is True


def test_a_tidal_rate_limit_pauses_the_sweep_too(tmp_path, monkeypatch):
    s = _fresh_stub(tmp_path)
    monkeypatch.setattr(rel.time, "monotonic", lambda: 50.0)
    s._rel_fresh_running = True
    _bind(s, "_on_rel_fresh_done")({"gen": 0, "hinted": 2, "tidal_retry": 1800.0, "tidal_limited": True})
    assert s._rel_backoff_until == 50.0 + rel._REL_BACKOFF_S[0]
    assert s._rel_timer.starts == [rel._REL_BACKOFF_S[0] * 1000]


def test_tick_skips_the_tidal_feed_while_the_sweep_is_paused(tmp_path, monkeypatch):
    s = _fresh_stub(tmp_path)
    _checked(s, "Alpha")
    asked = []
    s._releases_fresh_read = lambda gen, today, **kw: asked.append(kw)
    s._rel_backoff_until = rel.time.monotonic() + 600
    _bind(s, "_releases_fresh_tick")()
    assert asked == [{"use_lb": True, "use_tidal": False}]
    s._rel_fresh_running = False
    s._releases.meta_set("fresh_read_at", "0")
    s._rel_fresh_backoff_until = time.time() + 600
    _bind(s, "_releases_fresh_tick")()
    assert len(asked) == 1  # both lists paused: no read at all
