"""The Releases library import (bridge_releases, the import section).

The resolver itself is covered by test_artist_import.py; here the Qt layer
around it: the guards, the tag pass that needs no request, the paced name
search, the tray, the confirm (which never writes TIDAL favorites), and the
cancel, sign-out and rate-limit paths. The real mixin methods are bound onto
the stub test_releases_backend builds.
"""

from __future__ import annotations

from types import SimpleNamespace

from test_releases_backend import _bind, _http_429, _stub

from waves.waves_ui import bridge_releases as rel


class _Cache:
    """The scan cache: artists by name, each folder holding one 'file';
    ``tracks`` is how many files each has (ten unless said), ``titles`` what
    it holds under each artist key ({key: {"albums": [...], "tracks": [...]}})."""

    def __init__(self, artists, *, fail=False, tracks=None, titles=None):
        self._artists = artists
        self._fail = fail
        self._tracks = tracks or {}
        self._titles = titles or {}

    def distinct_artists(self):
        if self._fail:
            raise RuntimeError("no index")
        return [
            {
                "key": name.lower(),
                "name": name,
                "albums": 1,
                "tracks": self._tracks.get(name, 10),
                "folders": [f"/lib/{name}/{i}" for i in range(len(tags))] or [f"/lib/{name}"],
            }
            for name, tags in self._artists
        ]

    def artist_titles(self, key):
        return self._titles.get(key, {"albums": [], "tracks": []})

    @staticmethod
    def first_audio_file(folder):
        return folder + "/01.flac"


def _artist_class(s, releases):
    """The id-only TIDAL artist handle the catalog reads go through: its
    album (and EP and single) titles come from ``releases``, each read
    recorded in ``s.read``."""

    class _Artist:
        def __init__(self, session, raw=None):
            self.id = None

        def _titles(self, singles):
            s.read.append((str(self.id), singles))
            hit = (releases or {}).get((str(self.id), singles))
            if isinstance(hit, Exception):
                raise hit
            return [SimpleNamespace(name=t) for t in hit or []]

        def get_albums(self, limit=None):
            return self._titles(False)

        def get_ep_singles(self, limit=None):
            return self._titles(True)

    return _Artist


def _import_stub(
    tmp_path,
    artists,
    catalog=None,
    *,
    prefs=None,
    monkeypatch=None,
    fail=False,
    tracks=None,
    titles=None,
    releases=None,
):
    """A signed-in stub over a library of ``[(name, [tag ids per folder])]``
    whose TIDAL search answers from ``catalog`` (name -> [(id, name)]) and
    whose artists' release titles come from ``releases`` ((id, singles) ->
    [title] or an exception), each read recorded in ``s.read``."""
    s = _stub(tmp_path, prefs=prefs)
    s._library_index = SimpleNamespace(cache=_Cache(artists, fail=fail, tracks=tracks, titles=titles))
    s.read = []
    _artist = _artist_class(s, releases)
    tags = {}
    for name, per_folder in artists:
        for i, ids in enumerate(per_folder):
            tags[f"/lib/{name}/{i}/01.flac"] = list(ids)
    s.asked = []

    def search(text, models=None, limit=None):
        s.asked.append(text)
        hit = (catalog or {}).get(text)
        if isinstance(hit, Exception):
            raise hit
        return {"artists": [SimpleNamespace(id=i, name=n) for i, n in hit or []]}

    s.tidal = SimpleNamespace(session=SimpleNamespace(search=search))
    s._fav_artist_dict = lambda a: {"id": str(a.id), "name": a.name, "art": f"art/{a.id}"}
    s.sleeps = []
    if monkeypatch is not None:
        monkeypatch.setattr(rel, "read_custom_ids", lambda path, tag: tags.get(path, []))
        monkeypatch.setattr(rel.time, "sleep", s.sleeps.append)
        monkeypatch.setattr(rel, "Artist", _artist)
    return s


def _run(s):
    """Start a run (the inline pool finishes the worker at once) and land its
    outcome on the 'GUI thread'."""
    _bind(s, "startArtistImport")()
    if s._relImportDone.emits:
        _bind(s, "_on_rel_import_done")(s._relImportDone.emits[-1])


# --- guards ------------------------------------------------------------------


def test_signed_out_refuses(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("Caribou", [])], monkeypatch=monkeypatch)
    s._logged_in = False
    _run(s)
    assert s.artistImportError.emits == ["not-signed-in"] and s._rel_import_running is False


def test_no_library_refuses(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [], monkeypatch=monkeypatch)
    s._library_index = None
    _run(s)
    assert s.artistImportError.emits == ["no-library"]


def test_a_second_start_while_running_is_dropped(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("Caribou", [])], monkeypatch=monkeypatch)
    s._rel_import_running = True
    _run(s)
    assert s.asked == [] and s.artistImportReady.emits == [] and s.artistImportError.emits == []


def test_an_unreadable_library_says_so_and_frees_the_sweep(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("X", [])], monkeypatch=monkeypatch, fail=True)
    _run(s)
    assert s.artistImportError.emits == ["scan-failed"]
    assert s._rel_import_running is False and s._rel_timer.starts == [0]


# --- the two passes ------------------------------------------------------------


def test_a_tagged_artist_needs_no_request_the_rest_are_searched(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path,
        [("Caribou", [["11"]]), ("Nils Frahm", [])],
        {"Nils Frahm": [("22", "Nils Frahm")]},
        monkeypatch=monkeypatch,
    )
    _run(s)
    assert s.asked == ["Nils Frahm"]
    (payload,) = s.artistImportReady.emits
    assert [r["artist"]["id"] for r in payload["matched"]] == ["11", "22"]
    assert payload["matched"][0]["reason"] == "tag" and payload["tagged"] == 1
    assert payload["matched"][1]["artist"]["art"] == "art/22"
    stages = [e[2] for e in s.artistImportProgress.emits]
    assert stages == ["tags", "scan"]
    assert s._rel_import_running is False and s._rel_timer.starts == [0]


def test_a_collaboration_tag_proves_nothing_and_a_later_folder_can(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path,
        [("Duo", [["1", "2"]]), ("Solo", [["1", "2"], ["7"]])],
        {"Duo": [("5", "Duo")]},
        monkeypatch=monkeypatch,
    )
    _run(s)
    (payload,) = s.artistImportReady.emits
    assert s.asked == ["Duo"]
    assert sorted(r["artist"]["id"] for r in payload["matched"]) == ["5", "7"]


def test_name_searches_go_out_one_a_second(tmp_path, monkeypatch):
    names = ["A", "B", "C"]
    s = _import_stub(tmp_path, [(n, []) for n in names], {n: [(n, n)] for n in names}, monkeypatch=monkeypatch)
    _run(s)
    assert s.asked == names
    assert len(s.sleeps) == 2 and all(w > rel._REL_IMPORT_SEARCH_GAP_S - 0.1 for w in s.sleeps)


def test_followed_and_dismissed_names_are_never_searched(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("Caribou", []), ("Old Noise", []), ("Tagged", [["9"]])], monkeypatch=monkeypatch)
    s._releases.follow("1", "Caribou", "")
    s._releases.follow("9", "Someone", "")
    s._releases.tray_dismiss("old noise")
    _run(s)
    (payload,) = s.artistImportReady.emits
    assert s.asked == []
    assert payload["matched"] == [] and payload["skipped"] == 3


def test_one_artist_under_two_spellings_is_offered_once(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("Bjork", [["3"]]), ("Björk", [["3"]])], monkeypatch=monkeypatch)
    _run(s)
    (payload,) = s.artistImportReady.emits
    assert [r["artist"]["id"] for r in payload["matched"]] == ["3"]


# --- the tray --------------------------------------------------------------------


def test_unsettled_names_wait_in_the_tray_and_matched_ones_leave(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path,
        [("Bleachers", []), ("Nobody Here", []), ("Caribou", [])],
        {"Bleachers": [("1", "Bleachers"), ("2", "Bleachers")], "Caribou": [("4", "Caribou")]},
        monkeypatch=monkeypatch,
    )
    s._releases.tray_record([{"norm": "caribou", "name": "Caribou", "reason": "unmatched"}])
    _run(s)
    (payload,) = s.artistImportReady.emits
    assert [r["name"] for r in payload["fuzzy"]] == ["Bleachers"]
    assert [r["name"] for r in payload["unmatched"]] == ["Nobody Here"]
    tray = {r["norm"]: r for r in _bind(s, "artistImportTray")()}
    assert set(tray) == {"bleachers", "nobody here"}
    assert tray["bleachers"]["reason"] == "fuzzy" and len(tray["bleachers"]["candidates"]) == 2
    assert _bind(s, "artistImportTrayCount")() == 2


def test_a_tray_name_followed_since_leaves_the_tray(tmp_path):
    s = _stub(tmp_path)
    s._releases.tray_record([{"norm": "caribou", "name": "Caribou", "reason": "unmatched"}])
    s._releases.follow("4", "Caribou", "")
    assert _bind(s, "artistImportTray")() == []
    assert s._releases.tray_count() == 0


def test_a_dismissed_name_is_skipped_by_the_next_import(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("Nobody Here", [])], {}, monkeypatch=monkeypatch)
    _run(s)
    _bind(s, "dismissArtistImportTray")("nobody here")
    assert _bind(s, "artistImportTray")() == []
    s.asked.clear()
    s.artistImportReady.emits.clear()
    _run(s)
    assert s.asked == [] and s.artistImportReady.emits[0]["skipped"] == 1


# --- stopping ---------------------------------------------------------------------


def test_a_rate_limit_stops_the_run_and_pauses_the_sweep(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path,
        [("A", []), ("B", []), ("C", [])],
        {"A": [("1", "A")], "B": _http_429(), "C": [("3", "C")]},
        monkeypatch=monkeypatch,
    )
    _run(s)
    assert s.asked == ["A", "B"]
    assert s.artistImportError.emits == ["rate-limited"] and s.artistImportReady.emits == []
    assert s._rel_backoff_until > 0 and s._rel_timer.starts == [rel._REL_BACKOFF_S[0] * 1000]
    assert s._rel_import_running is False


def test_cancel_mid_run_stops_searching_and_drops_the_answer(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", []), ("B", []), ("C", [])], {}, monkeypatch=monkeypatch)
    real = s.tidal.session.search

    def search(text, **kw):
        out = real(text, **kw)
        _bind(s, "cancelArtistImport")()
        return out

    s.tidal.session.search = search
    _run(s)
    assert s.asked == ["A"]
    assert s._relImportDone.emits == [] and s.artistImportReady.emits == []
    assert s._rel_import_running is False and s._rel_timer.starts == [0]


def test_sign_out_mid_run_drops_it(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", []), ("B", [])], {}, monkeypatch=monkeypatch)
    real = s.tidal.session.search

    def search(text, **kw):
        out = real(text, **kw)
        _bind(s, "_releases_stop")()
        return out

    s.tidal.session.search = search
    _run(s)
    assert s.asked == ["A"] and s.artistImportReady.emits == [] and s._rel_import_running is False


def test_a_late_answer_from_a_cancelled_run_is_dropped(tmp_path):
    s = _stub(tmp_path)
    s._rel_import_running = True
    s._rel_import_gen = 2
    _bind(s, "_on_rel_import_done")({"gen": 0, "igen": 1, "matched": []})
    assert s.artistImportReady.emits == [] and s._rel_import_running is True


def test_the_sweep_waits_while_an_import_runs(tmp_path):
    s = _stub(tmp_path)
    s._releases.follow("A", "Artist", "")
    s._rel_import_running = True
    _bind(s, "_releases_tick")()
    assert s._rel_step_running is False and s._rel_timer.starts == []


# --- the confirm --------------------------------------------------------------------


def test_confirm_follows_in_one_go_and_never_touches_tidal(tmp_path):
    s = _stub(tmp_path, prefs={"follow_mirror_tidal": True})
    s.tidal = SimpleNamespace(session=SimpleNamespace(user=None))
    s._releases.tray_record([{"norm": "bleachers", "name": "Bleachers", "reason": "fuzzy"}])
    _bind(s, "confirmArtistImport")(
        [
            {"id": "1", "name": "Bleachers", "art": "a", "norm": "bleachers"},
            {"id": "2", "name": "Caribou", "art": "", "norm": "caribou"},
        ]
    )
    assert s.artistImportDone.emits == [2]
    rows = {r["artist_id"]: r for r in s._releases.followed_artists()}
    assert set(rows) == {"1", "2"} and {r["origin"] for r in rows.values()} == {"library"}
    assert s._releases.tray_count() == 0
    # Not one favorites write is queued, whatever the mirror pref says.
    assert s._rel_tidal_out == {} and s._rel_tidal_timer.starts == []
    assert s.releasesChanged.emits == [()] and s._rel_timer.starts == [0]


def test_confirm_unwraps_a_qml_array_and_ignores_junk(tmp_path):
    class _QJSValue:
        def __init__(self, xs):
            self._xs = xs

        def toVariant(self):
            return self._xs

    s = _stub(tmp_path)
    _bind(s, "confirmArtistImport")(_QJSValue([{"id": "42", "name": "Caribou"}, {"name": "No id"}, "junk"]))
    assert [r["artist_id"] for r in s._releases.followed_artists()] == ["42"]
    _bind(s, "confirmArtistImport")([])
    assert s.artistImportDone.emits == [1]


def test_confirm_leaves_existing_follows_as_they_are(tmp_path):
    s = _stub(tmp_path)
    s._releases.follow("1", "Caribou", "art", origin="manual")
    _bind(s, "confirmArtistImport")([{"id": "1", "name": "Caribou", "art": ""}])
    assert s.artistImportDone.emits == [0]
    assert s._releases.follow_info("1")["origin"] == "manual"


def test_a_run_that_breaks_midway_still_frees_the_sweep(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", [])], {}, monkeypatch=monkeypatch)

    def boom():
        raise RuntimeError("store closed")

    s._releases.follows = boom
    _run(s)
    assert s.artistImportError.emits == ["scan-failed"]
    assert s._rel_import_running is False and s._rel_timer.starts == [0]


# --- a run that cannot finish ------------------------------------------------


def test_an_offline_run_stops_and_forgets_nothing(tmp_path, monkeypatch):
    names = [f"N{i}" for i in range(12)]
    down = {n: OSError("no route to host") for n in names[1:]}
    s = _import_stub(tmp_path, [(n, []) for n in names], {"N0": [("0", "N0")], **down}, monkeypatch=monkeypatch)
    _run(s)
    assert s.artistImportError.emits == ["offline"]
    # Stopped after the streak, not one failure per remaining name.
    assert len(s.asked) == 1 + rel.artist_import._ERROR_STREAK
    assert _bind(s, "_releases_seen_norms")() == set() and s._releases.tray_count() == 0


def test_one_failed_search_is_left_for_the_next_run(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path, [("A", []), ("Boom", [])], {"A": [("1", "A")], "Boom": OSError("reset")}, monkeypatch=monkeypatch
    )
    _run(s)
    review = s.artistImportReady.emits[-1]
    assert [r["name"] for r in review["matched"]] == ["A"] and review["unmatched"] == []
    assert _bind(s, "_releases_seen_norms")() == {"a"} and s._releases.tray_count() == 0


def test_a_library_folder_change_ends_the_run(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", [["1"]]), ("B", [["2"]]), ("C", [["3"]])], monkeypatch=monkeypatch)
    read: list = []

    def read_ids(path, tag):
        read.append(path)
        # The user picks another library folder while the tag pass runs.
        s._library_index = SimpleNamespace(cache=_Cache([]))
        return ["1"]

    monkeypatch.setattr(rel, "read_custom_ids", read_ids)
    _run(s)
    assert len(read) == 1, "kept reading files in the old library folder"
    assert s.artistImportError.emits == ["library-changed"] and s._rel_import_running is False
    assert s._releases.followed_artists() == []


# --- the track floor, and the library's say on namesakes ---------------------


def test_artists_under_the_track_floor_are_left_out_and_come_back_when_it_drops(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path,
        [("A", []), ("B", [])],
        {"A": [("1", "A")], "B": [("2", "B")]},
        tracks={"A": 3, "B": 10},
        monkeypatch=monkeypatch,
    )
    _run(s)
    assert s.asked == ["B"]
    (payload,) = s.artistImportReady.emits
    assert [r["name"] for r in payload["matched"]] == ["B"] and payload["skipped"] == 1
    # Not remembered as looked at: a lower floor brings it round.
    assert "a" not in _bind(s, "_releases_seen_norms")()
    s._waves_prefs["releases_import_min_tracks"] = "3"
    _bind(s, "startArtistImportMore")()
    _bind(s, "_on_rel_import_done")(s._relImportDone.emits[-1])
    assert s.asked == ["B", "A"]


def test_the_floor_reads_any_shape_and_a_row_that_cannot_say_passes(tmp_path):
    s = _stub(tmp_path)
    floor = _bind(s, "_releases_min_tracks")
    enough = _bind(s, "_releases_enough_tracks")
    assert floor() == 5
    for raw, want in (("7", 7), (7.0, 7), ("junk", 5), ("0", 1), ("5000", 999)):
        s._waves_prefs["releases_import_min_tracks"] = raw
        assert floor() == want, raw
    assert enough({"tracks": 5}, 5) and not enough({"tracks": 4}, 5)
    assert enough({}, 5) and enough({"tracks": "many"}, 5)


def test_namesakes_are_weighed_against_the_library_albums_first(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path,
        [("Afterglow", [])],
        {"Afterglow": [("1", "Afterglow"), ("2", "Afterglow")]},
        titles={"afterglow": {"albums": ["Hollow"], "tracks": []}},
        releases={("1", False): ["Other"], ("2", False): ["Hollow"]},
        monkeypatch=monkeypatch,
    )
    _run(s)
    (payload,) = s.artistImportReady.emits
    (row,) = payload["matched"]
    assert row["artist"] == {"id": "2", "name": "Afterglow", "art": "art/2"}
    assert row["reason"] == "library" and row["owned"] == ["Hollow"] and row["conf"] == 100
    assert payload["fuzzy"] == []
    assert s.read == [("1", False), ("2", False)]
    # The reads keep the search's pace: one request a second across both.
    assert len(s.sleeps) == 2 and all(w > rel._REL_IMPORT_SEARCH_GAP_S - 0.1 for w in s.sleeps)


def test_a_toss_up_reaches_the_review_scored_and_the_tray_keeps_the_scores(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path,
        [("Afterglow", [])],
        {"Afterglow": [("1", "Afterglow"), ("2", "Afterglow")]},
        titles={"afterglow": {"albums": ["Hollow"], "tracks": []}},
        releases={("1", False): ["Hollow"], ("2", False): ["Hollow"], ("1", True): [], ("2", True): []},
        monkeypatch=monkeypatch,
    )
    _run(s)
    (payload,) = s.artistImportReady.emits
    (row,) = payload["fuzzy"]
    assert [c["conf"] for c in row["candidates"]] == [100, 100]
    assert s.read == [("1", False), ("2", False), ("1", True), ("2", True)]
    (kept,) = _bind(s, "artistImportTray")()
    assert kept["candidates"][0]["conf"] == 100 and kept["candidates"][0]["owned"] == ["Hollow"]


def test_a_rate_limit_while_weighing_stops_the_run(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path,
        [("Afterglow", []), ("B", [])],
        {"Afterglow": [("1", "Afterglow"), ("2", "Afterglow")], "B": [("3", "B")]},
        titles={"afterglow": {"albums": ["Hollow"], "tracks": []}},
        releases={("1", False): _http_429()},
        monkeypatch=monkeypatch,
    )
    _run(s)
    assert s.asked == ["Afterglow"] and s.read == [("1", False)]
    assert s.artistImportError.emits == ["rate-limited"] and s.artistImportReady.emits == []
    assert s._rel_backoff_until > 0 and not _bind(s, "_releases_seen_norms")()


def test_a_library_that_cannot_be_read_judges_on_names_alone(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path, [("Afterglow", [])], {"Afterglow": [("1", "Afterglow"), ("2", "Afterglow")]}, monkeypatch=monkeypatch
    )

    def closed(key):
        raise RuntimeError("closed")

    s._library_index.cache.artist_titles = closed
    _run(s)
    (payload,) = s.artistImportReady.emits
    assert len(payload["fuzzy"]) == 1 and s.read == []


def test_library_follows_under_the_floor_are_counted_and_unfollowed_in_one_go(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path, [("A", []), ("B", []), ("C", [])], tracks={"A": 2, "B": 10, "C": 1}, monkeypatch=monkeypatch
    )
    s._releases.follow_many([{"id": "1", "name": "A", "art": ""}, {"id": "2", "name": "B", "art": ""}])
    s._releases.follow("3", "C", "")  # followed by hand: the user's own, whatever the library holds
    assert _bind(s, "followsUnderFloor")() == 1
    assert _bind(s, "unfollowUnderFloor")() == 1
    assert {r["artist_id"] for r in s._releases.followed_artists()} == {"2", "3"}
    assert s.followStateChanged.emits == [("1", False)] and s.releasesChanged.emits == [()]
    assert len(s.followFloorChanged.emits) == 1
    assert _bind(s, "followsUnderFloor")() == 0
    # A lower floor lets B stay; a higher one would take it.
    s._waves_prefs["releases_import_min_tracks"] = "11"
    assert _bind(s, "followsUnderFloor")() == 1
    # No library: nothing to judge by, nothing is touched.
    s._library_index = None
    assert _bind(s, "followsUnderFloor")() == 0 and _bind(s, "unfollowUnderFloor")() == 0


def test_the_excluded_page_lists_the_artists_under_the_floor_nobody_follows(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path, [("A", []), ("B", []), ("C", [])], tracks={"A": 2, "B": 10, "C": 1}, monkeypatch=monkeypatch
    )
    s._releases.follow("3", "C", "")
    _bind(s, "loadExcluded")()
    (rows,) = s.excludedLoaded.emits
    assert rows == [
        {"name": "A", "key": "a", "albums": 1, "tracks": 2, "held": [], "covers": [], "best": "", "span": ""}
    ]
    s._library_index = None
    _bind(s, "loadExcluded")()
    assert s.excludedLoaded.emits[-1] == []


def test_a_names_run_ignores_the_floor_and_its_picks_are_the_users_own(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path,
        [("A", []), ("B", [])],
        {"A": [("1", "A")], "B": [("2", "B")]},
        tracks={"A": 2},
        monkeypatch=monkeypatch,
    )
    _bind(s, "startArtistImportNames")(["A"])
    _bind(s, "_on_rel_import_done")(s._relImportDone.emits[-1])
    assert s.asked == ["A"]
    (payload,) = s.artistImportReady.emits
    assert [r["name"] for r in payload["matched"]] == ["A"] and payload["skipped"] == 0
    _bind(s, "confirmArtistPicks")([{"id": "1", "name": "A", "art": "", "norm": "a"}])
    (row,) = s._releases.followed_artists()
    assert row["artist_id"] == "1" and row["origin"] == "manual"
    # The user's own: the floor never offers to drop it.
    assert _bind(s, "followsUnderFloor")() == 0
    assert s.artistImportDone.emits == [1]


def test_a_names_run_asks_for_a_dismissed_name_and_leaves_nothing_in_the_tray(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path,
        [("A", []), ("B", [])],
        {"A": [("1", "A")], "B": [("2", "B"), ("3", "B")]},
        tracks={"A": 2, "B": 2},
        monkeypatch=monkeypatch,
    )
    s._releases.tray_dismiss("a")
    _bind(s, "startArtistImportNames")(["A", "B"])
    _bind(s, "_on_rel_import_done")(s._relImportDone.emits[-1])
    # FOLLOW on the Excluded page is the user asking, FORGET or not.
    assert s.asked == ["A", "B"]
    (payload,) = s.artistImportReady.emits
    assert [r["name"] for r in payload["matched"]] == ["A"]
    assert [r["name"] for r in payload["fuzzy"]] == ["B"]
    # B stays open on that page; the tray (library runs only) never sees it.
    assert s._releases.tray_rows() == []


def test_the_floor_never_offers_to_drop_a_tidal_favorite(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", []), ("B", [])], tracks={"A": 2, "B": 1}, monkeypatch=monkeypatch)
    s._releases.follow_many([{"id": "1", "name": "A", "art": ""}, {"id": "2", "name": "B", "art": ""}])
    s._releases.sync_tidal_favorites([{"id": "2", "name": "B", "art": ""}], partial=True)
    assert s._releases.follow_info("2")["tidal_fav"] is True
    assert _bind(s, "followsUnderFloor")() == 1
    assert _bind(s, "unfollowUnderFloor")() == 1
    assert {r["artist_id"] for r in s._releases.follows()} == {"2"}


def test_a_floor_change_tells_the_library_card(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", [])], monkeypatch=monkeypatch)
    _bind(s, "_releases_import_floor_changed")()
    assert len(s.followFloorChanged.emits) == 1


def test_the_excluded_page_shows_each_artist_by_the_albums_the_library_holds(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", []), ("B", [])], tracks={"A": 3, "B": 10}, monkeypatch=monkeypatch)
    with_cover, bare = tmp_path / "A" / "Later", tmp_path / "A" / "Early"
    with_cover.mkdir(parents=True)
    bare.mkdir(parents=True)
    (with_cover / "Cover.JPG").write_bytes(b"x")
    albums = {
        "a": [
            {
                "folder": str(with_cover),
                "title": "Later",
                "year": "2021",
                "tracks": 2,
                "codec": "flac",
                "bits": 24,
                "rate": 96000,
                "bitrate": 0,
                "mtime": 1.0,
            },
            {
                "folder": str(bare),
                "title": "Early",
                "year": "2012",
                "tracks": 1,
                "codec": "mp3",
                "bits": 0,
                "rate": 44100,
                "bitrate": 320,
                "mtime": 1.0,
            },
        ]
    }
    asked = []
    s._library_index.cache.artist_albums = lambda keys: asked.append(list(keys)) or albums
    _bind(s, "loadExcluded")()
    # The rows go out before any folder is listed, then again with covers.
    (first,), (row,) = s.excludedLoaded.emits
    assert [h["cover"] for h in first["held"]] == ["", ""] and first["covers"] == []
    assert asked == [["a"]]
    cover = (with_cover / "Cover.JPG").as_uri()
    assert row["held"] == [
        {"title": "Later", "year": "2021", "tracks": 2, "q": "24/96 FLAC", "cover": cover},
        {"title": "Early", "year": "2012", "tracks": 1, "q": "320 MP3", "cover": ""},
    ]
    assert (row["covers"], row["best"], row["span"]) == ([cover], "24/96 FLAC", "2012\u20132021")
    # The folder is listed once and remembered until it changes.
    (bare / "folder.png").write_bytes(b"x")
    before = len(s.excludedLoaded.emits)
    _bind(s, "loadExcluded")()
    # Every folder known: one answer, covers and all.
    assert len(s.excludedLoaded.emits) == before + 1
    assert s.excludedLoaded.emits[-1][0]["held"][1]["cover"] == ""
    albums["a"][1]["mtime"] = 2.0
    _bind(s, "loadExcluded")()
    assert s.excludedLoaded.emits[-1][0]["held"][1]["cover"] == (bare / "folder.png").as_uri()


def test_an_excluded_read_asked_while_one_is_out_runs_once_more_after_it(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", [])], tracks={"A": 2}, monkeypatch=monkeypatch)
    held = []
    s.threadpool.start = lambda w: held.append(w)
    _bind(s, "loadExcluded")()
    _bind(s, "loadExcluded")()
    _bind(s, "loadExcluded")()
    # One worker: the asks that came while it was out fold into one rerun.
    assert len(held) == 1 and s.excludedLoaded.emits == []
    held[0].run()
    assert len(s.excludedLoaded.emits) == 2
    _bind(s, "loadExcluded")()
    assert len(held) == 2


def test_a_library_copy_reads_as_a_short_quality_label():
    assert rel._quality_label("flac", 16, 44100, 750) == "16/44.1 FLAC"
    assert rel._quality_label("alac", 24, 192000, 0) == "24/192 ALAC"
    # A lossy file's depth is the decoder's, not the copy's: its bitrate says it.
    assert rel._quality_label("aac", 16, 44100, 256) == "256 AAC"
    assert rel._quality_label("mp3", 0, 48000, 0) == "MP3"
    assert rel._quality_label("", 16, 44100, 0) == ""
