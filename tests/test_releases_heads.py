"""Tests for the heads read: every follow's newest release ids, 20 follows a
request, and the follows whose head shows something new checked first.

Covers the pure parsing in waves/fresh_releases.py, the store's remembered
head ids in waves/follows.py, and the pass in bridge_releases (bound onto the
releases test stub, the pool inline, TIDAL a canned answer).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
from conftest import _InlinePool
from test_releases_backend import _bind, _stub

from waves import fresh_releases as fr
from waves.follows import FollowStore
from waves.waves_ui import bridge_releases as rel

V2 = "https://openapi.example/v2/"


# --- the pure module ----------------------------------------------------------


def _answer(heads: dict) -> dict:
    """A v2 artists answer: artist id to [(release id, day), ...]."""
    data, included = [], []
    for aid, head in heads.items():
        rel_data = [{"id": rid, "type": "albums"} for rid, _day in head]
        data.append({"id": aid, "type": "artists", "relationships": {"albums": {"data": rel_data}}})
        included += [{"id": rid, "type": "albums", "attributes": {"releaseDate": day}} for rid, day in head]
    return {"data": data, "included": included}


def test_parse_keeps_the_answer_order_and_joins_the_days():
    body = _answer({"1": [("b", "2026-11-13"), ("a", "2026-01-02")], "2": []})
    body["data"].append({"id": "3", "type": "artists"})
    body["included"].append({"id": "x", "type": "artists", "attributes": {"releaseDate": "2026-11-14"}})
    heads = fr.parse_artist_heads(body)
    assert heads == {"1": [("b", "2026-11-13"), ("a", "2026-01-02")], "2": [], "3": []}


def test_parse_refuses_what_is_not_the_list():
    with pytest.raises(fr.FetchError):
        fr.parse_artist_heads({"errors": []})
    with pytest.raises(fr.FetchError):
        fr.parse_artist_heads({"data": {}})


def test_heads_params_name_the_ids_and_the_albums():
    assert fr.heads_params(["1", "2"]) == {"filter[id]": "1,2", "include": "albums"}


def test_first_read_counts_only_recent_unseen_ids():
    head = [("new", "2026-11-13"), ("guest", "2025-03-01"), ("held", "2026-11-13"), ("undated", "")]
    assert fr.head_news(head, set(), {"held"}, "2026-11-02") == ["new", "undated"]


def test_known_heads_count_only_additions():
    head = [("new", "2025-01-01"), ("a", "2024-01-01")]
    assert fr.head_news(head, {"a", "gone"}, set(), "2026-11-02") == ["new"]
    assert fr.head_news([("a", "2024-01-01")], {"a", "gone"}, set(), "") == []


def test_day_before():
    ts = time.mktime((2026, 11, 14, 12, 0, 0, 0, 0, -1))
    assert fr.day_before(ts) == "2026-11-13"
    assert fr.day_before(0) == ""


# --- the store ----------------------------------------------------------------


def test_note_heads_unions_newest_first_and_caps(tmp_path):
    st = FollowStore(str(tmp_path / "r.sqlite3"))
    st.follow("1", "One", "")
    assert st.heads_known(["1", "nobody"]) == {"1": set()}
    assert st.note_heads({"1": ["b", "a"], "nobody": ["z"]}) == 1
    assert st.note_heads({"1": ["a"]}) == 0
    assert st.note_heads({"1": ["c"]}) == 1
    assert st.heads_known(["1"]) == {"1": {"a", "b", "c"}}
    st.note_heads({"1": [f"n{i}" for i in range(70)]})
    known = st.heads_known(["1"])["1"]
    assert len(known) == fr.HEADS_KEEP and "n0" in known and "a" not in known


def test_an_old_store_grows_the_column(tmp_path):
    path = str(tmp_path / "r.sqlite3")
    st = FollowStore(path)
    st.follow("1", "One", "")
    st.close()
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE followed DROP COLUMN head_ids")
    con.commit()
    con.close()
    st = FollowStore(path)
    assert st.note_heads({"1": ["a"]}) == 1
    assert st.heads_known(["1"]) == {"1": {"a"}}


# --- the pass -----------------------------------------------------------------


class _Relay:
    def __init__(self, fn):
        self.fn = fn

    def emit(self, result):
        self.fn(result)


class _Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        return self._body


def _heads_stub(tmp_path):
    s = _stub(tmp_path)
    s._rel_heads_pool = _InlinePool()
    s._rel_heads_left = 0
    s._rel_heads_tally = {}
    s._rel_heads_stop = threading.Event()
    s._rel_heads_backoff_until = 0.0
    s._rel_heads_woke = False
    s._rel_heads_lock = threading.Lock()
    s._rel_heads_pending = {}
    s._relHeadsDone = _Relay(lambda r: _bind(s, "_on_rel_heads_done")(r))
    s.catalog = {}
    s.asked = []
    s.answers = []

    def basic_request(method, path, params=None, base_url=None):
        ids = params["filter[id]"].split(",")
        s.asked.append((method, path, ids, base_url))
        if s.answers:
            return s.answers.pop(0)
        return _Resp(200, _answer({a: s.catalog.get(a, []) for a in ids}))

    s.tidal = SimpleNamespace(
        session=SimpleNamespace(
            request=SimpleNamespace(basic_request=basic_request), config=SimpleNamespace(openapi_v2_location=V2)
        )
    )
    return s


def _follow(s, aid, held=(), checked=True):
    s._releases.follow(aid, "Artist " + aid, "")
    if checked:
        s._releases.record_check(aid, [{"id": r, "date": "2025-01-01"} for r in held])


def _is_hinted(s, aid) -> bool:
    with s._releases._lock:
        row = s._releases._conn.execute("SELECT hinted FROM followed WHERE artist_id = ?", (aid,)).fetchone()
    return bool(row and row[0])


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def test_a_pass_asks_20_follows_a_request_and_leaves_out_the_never_checked(tmp_path):
    s = _heads_stub(tmp_path)
    for i in range(45):
        _follow(s, f"a{i}")
    _follow(s, "fresh", checked=False)
    assert _bind(s, "_releases_heads_maybe")(0) is True
    assert [len(ids) for _m, _p, ids, _b in s.asked] == [20, 20, 5]
    assert all(p == fr.TIDAL_HEADS_PATH and b == V2 for _m, p, _ids, b in s.asked)
    assert "fresh" not in {i for _m, _p, ids, _b in s.asked for i in ids}
    assert s._rel_heads_left == 0
    assert int(s._releases.meta_get("heads_read_at", "0")) > 0


def test_news_is_hinted_and_held_until_a_complete_check(tmp_path):
    s = _heads_stub(tmp_path)
    _follow(s, "ren", held=["old"])
    _follow(s, "guest", held=["mine"])
    s.catalog = {
        "ren": [("new", _today()), ("old", "2025-01-01")],
        "guest": [("feat", "2024-05-05"), ("mine", "2025-01-01")],
    }
    _bind(s, "_releases_heads_maybe")(0)
    assert _is_hinted(s, "ren") and not _is_hinted(s, "guest")
    # A quiet head is remembered at once, one with news waits for its check.
    assert s._releases.heads_known(["ren", "guest"]) == {"ren": set(), "guest": {"feat", "mine"}}
    assert "ren" in s._rel_heads_pending
    assert s._rel_timer.starts[-1] == rel._REL_MIN_STEP_MS
    assert _bind(s, "_releases_step_ms")() == rel._REL_MIN_STEP_MS

    s._artist_recent_releases = lambda aid: (
        [{"id": "new", "date": _today()}, {"id": "old", "date": "2025-01-01"}],
        [],
        False,
    )
    _bind(s, "_releases_check_one")(s._releases.next_due(0), s._rel_gen)
    assert not _is_hinted(s, "ren")
    assert s._releases.heads_known(["ren"])["ren"] == {"new", "old"}
    assert s._rel_heads_pending == {}

    # The next pass finds nothing new.
    _bind(s, "_releases_heads_maybe")(0)
    assert not _is_hinted(s, "ren") and not _is_hinted(s, "guest")


def test_a_failed_check_keeps_the_head_new_for_the_next_pass(tmp_path):
    s = _heads_stub(tmp_path)
    _follow(s, "ren", held=["old"])
    s.catalog = {"ren": [("new", _today()), ("old", "2025-01-01")]}
    _bind(s, "_releases_heads_maybe")(0)
    s._artist_recent_releases = lambda aid: ([], ["get_albums", "get_ep_singles"], False)
    _bind(s, "_releases_check_one")(s._releases.next_due(0), s._rel_gen)
    assert not _is_hinted(s, "ren")
    assert s._releases.heads_known(["ren"])["ren"] == set()
    _bind(s, "_releases_heads_maybe")(0)
    assert _is_hinted(s, "ren")


def test_a_head_read_after_the_check_started_is_not_settled_by_it(tmp_path):
    s = _heads_stub(tmp_path)
    _follow(s, "ren")
    s._rel_heads_pending["ren"] = (time.time() + 60, ["new"])
    _bind(s, "_releases_heads_settle")("ren", time.time())
    assert "ren" in s._rel_heads_pending
    assert s._releases.heads_known(["ren"])["ren"] == set()


def test_a_head_read_during_a_check_is_hinted_again_after_it(tmp_path):
    s = _heads_stub(tmp_path)
    _follow(s, "ren", held=["old"])
    s.catalog = {"ren": [("new", _today()), ("old", "2025-01-01")]}

    def releases(aid):
        # The heads pass answers while this check is under way, after the
        # check read TIDAL's list without the new release.
        _bind(s, "_releases_heads_maybe")(0)
        return [{"id": "old", "date": "2025-01-01"}], [], False

    s._artist_recent_releases = releases
    _bind(s, "_releases_check_one")(s._releases.next_due(time.time() + 60), s._rel_gen)
    assert "ren" in s._rel_heads_pending
    assert _is_hinted(s, "ren"), "the check cleared a hint it could not have answered"


def test_a_wake_pass_that_found_the_network_down_is_owed_again(tmp_path):
    s = _heads_stub(tmp_path)
    _follow(s, "a")
    s._releases.meta_set("heads_read_at", str(int(time.time()) - 1800))

    def down(*a, **kw):
        raise ConnectionError("no route")

    s.tidal.session.request.basic_request = down
    s._rel_heads_woke = True
    assert _bind(s, "_releases_heads_maybe")(rel._REL_HEADS_STALE_S) is True
    assert s._rel_heads_woke is True, "the read on wake was dropped with the network still down"
    assert s._releases.meta_get("heads_read_at", "0") != "0"
    # An ordinary pass that fails offline owes nothing.
    s._rel_heads_woke = False
    s._rel_heads_backoff_until = 0.0
    assert _bind(s, "_releases_heads_maybe")(0) is True
    assert s._rel_heads_woke is False


def test_a_rate_limit_stops_the_pass_and_pauses(tmp_path):
    s = _heads_stub(tmp_path)
    for i in range(50):
        _follow(s, f"a{i}")
    s.answers = [_Resp(429, headers={"Retry-After": "60"})]
    _bind(s, "_releases_heads_maybe")(0)
    assert len(s.asked) == 1
    assert s._rel_heads_left == 0
    assert s._rel_backoff_until > time.monotonic()
    assert s._rel_heads_backoff_until >= time.monotonic() + fr.MIN_BACKOFF_SEC - 5
    assert s._releases.meta_get("heads_read_at", "0") == "0"
    # Paused: nothing starts again until the pause ends.
    assert _bind(s, "_releases_heads_maybe")(0) is False


def test_a_recent_pass_is_not_repeated_but_check_now_reads_at_once(tmp_path):
    s = _heads_stub(tmp_path)
    _follow(s, "a")
    s._releases.meta_set("heads_read_at", str(int(time.time()) - 60))
    assert _bind(s, "_releases_heads_maybe")(rel._REL_HEADS_STALE_S) is False
    assert s.asked == []
    s._releases.meta_set("rescan_at", "0")
    assert _bind(s, "releasesCheckNow")() is True
    assert len(s.asked) == 1


def test_a_sign_out_mid_pass_writes_nothing(tmp_path):
    s = _heads_stub(tmp_path)
    _follow(s, "ren", held=["old"])
    _follow(s, "quiet", held=["q"])
    s.catalog = {"ren": [("new", _today())], "quiet": [("q", "2025-01-01")]}
    real = s.tidal.session.request.basic_request

    def signed_out(*a, **kw):
        resp = real(*a, **kw)
        _bind(s, "_releases_stop")()
        return resp

    s.tidal.session.request.basic_request = signed_out
    _bind(s, "_releases_heads_maybe")(0)
    assert not _is_hinted(s, "ren")
    assert s._releases.heads_known(["ren", "quiet"]) == {"ren": set(), "quiet": set()}
    assert s._rel_heads_pending == {}
    assert s._releases.meta_get("heads_read_at", "0") == "0"


def test_signed_out_or_hidden_never_reads(tmp_path):
    s = _heads_stub(tmp_path)
    _follow(s, "a")
    s._logged_in = False
    assert _bind(s, "_releases_heads_maybe")(0) is False
    s._logged_in, s._rel_revealed = True, False
    assert _bind(s, "_releases_heads_maybe")(0) is False
    assert s.asked == []
