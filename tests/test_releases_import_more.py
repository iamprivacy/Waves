"""The library import alongside a scan (bridge_releases): a run mid-scan says
so, the names an import looked at are remembered, a finished scan offers
only the artists found since, and the opt-in automatic import follows
confident matches by itself as the scan goes on."""

from __future__ import annotations

from test_releases_backend import _bind
from test_releases_import_backend import _import_stub

from waves.waves_ui import bridge_releases as rel


def _land(s):
    """Deliver the import worker's last outcome on the 'GUI thread'."""
    _bind(s, "_on_rel_import_done")(s._relImportDone.emits[-1])


def _land_count(s):
    _bind(s, "_on_rel_more_counted")(s._relMoreCounted.emits[-1])


def _moved(s, stamp, *, scanning=False):
    s._library_stamp = stamp
    s._library_index_building = scanning
    _bind(s, "_releases_library_moved")()


# --- a run while the scan is still going ----------------------------------------


def test_before_the_first_save_the_dialog_hears_the_scan_is_running(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [], monkeypatch=monkeypatch)
    s._library_index = None
    s._library_index_building = True
    _bind(s, "startArtistImport")()
    assert s.artistImportError.emits == ["library-scanning"]


def test_a_mid_scan_review_says_how_many_artists_it_covers(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("Caribou", [["11"]]), ("Four Tet", [["12"]])], monkeypatch=monkeypatch)
    s._library_index_building = True
    _bind(s, "startArtistImport")()
    _land(s)
    (payload,) = s.artistImportReady.emits
    assert payload["scanning"] is True and payload["artists"] == 2


# --- what an import has looked at ------------------------------------------------


def test_the_more_run_searches_only_artists_found_since(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", [])], {"A": [("1", "A")], "B": [("2", "B")]}, monkeypatch=monkeypatch)
    _bind(s, "startArtistImport")()
    _land(s)
    assert s.asked == ["A"]
    s._library_index.cache._artists = [("A", []), ("B", [])]
    _bind(s, "startArtistImportMore")()
    _land(s)
    assert s.asked == ["A", "B"]
    assert [r["name"] for r in s.artistImportReady.emits[-1]["matched"]] == ["B"]


def test_a_rate_limited_run_leaves_its_artists_unseen(tmp_path, monkeypatch):
    from test_releases_backend import _http_429

    s = _import_stub(tmp_path, [("A", [])], {"A": _http_429()}, monkeypatch=monkeypatch)
    _bind(s, "startArtistImport")()
    _land(s)
    assert _bind(s, "_releases_seen_norms")() == set()


# --- the offer on the tab ---------------------------------------------------------


def test_no_offer_before_any_import(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", [])], monkeypatch=monkeypatch)
    _moved(s, 1)
    _land_count(s)
    assert s.artistImportNotice.emits == []


def test_a_finished_scan_offers_the_new_artists_and_not_now_retires_them(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", [])], {"A": [("1", "A")]}, monkeypatch=monkeypatch)
    _bind(s, "startArtistImport")()
    _land(s)
    counted = len(s._relMoreCounted.emits)  # the landing looks once at the library as it stands
    s._library_index.cache._artists = [("A", []), ("B", []), ("C", [])]
    _moved(s, 2, scanning=True)
    assert len(s._relMoreCounted.emits) == counted  # nothing is counted while it scans
    _moved(s, 3)
    _land_count(s)
    assert s.artistImportNotice.emits[-1] == {"kind": "more", "n": 2}
    _moved(s, 3)  # the same publish is looked at once
    assert len(s._relMoreCounted.emits) == counted + 1
    _bind(s, "dismissArtistImportNotice")()
    assert s.artistImportNotice.emits[-1] == {"kind": "", "n": 0}
    _moved(s, 4)
    _land_count(s)
    assert s._relMoreCounted.emits[-1]["norms"] == []


def test_importing_the_more_artists_clears_the_offer(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", [])], {"A": [("1", "A")], "B": [("2", "B")]}, monkeypatch=monkeypatch)
    _bind(s, "startArtistImport")()
    _land(s)
    s._library_index.cache._artists = [("A", []), ("B", [])]
    _moved(s, 2)
    _land_count(s)
    assert s.artistImportNotice.emits[-1] == {"kind": "more", "n": 1}
    _bind(s, "startArtistImportMore")()
    _land(s)
    assert s.artistImportNotice.emits[-1] == {"kind": "", "n": 0}


# --- the automatic import ------------------------------------------------------------


def _auto_stub(tmp_path, monkeypatch, artists, catalog=None):
    return _import_stub(tmp_path, artists, catalog, prefs={"releases_auto_import": True}, monkeypatch=monkeypatch)


def test_automatic_import_follows_confident_matches_and_leaves_the_rest(tmp_path, monkeypatch):
    s = _auto_stub(
        tmp_path,
        monkeypatch,
        [("Caribou", [["11"]]), ("Nils Frahm", []), ("Twin", [])],
        {"Nils Frahm": [("22", "Nils Frahm")], "Twin": [("1", "Twin"), ("2", "Twin")]},
    )
    _moved(s, 1)
    _land(s)
    assert {r["artist_id"] for r in s._releases.followed_artists()} == {"11", "22"}
    assert s._releases.tray_count() == 1
    assert s.artistImportNotice.emits[-1] == {"kind": "look", "n": 1}
    # Nothing reaches the dialog: no review, no progress, no error.
    assert s.artistImportReady.emits == [] and s.artistImportProgress.emits == []
    assert s.artistImportDone.emits == [2]
    assert s.releasesChanged.emits


def test_automatic_import_mid_scan_is_spaced_and_takes_only_new_artists(tmp_path, monkeypatch):
    s = _auto_stub(tmp_path, monkeypatch, [("A", [])], {"A": [("1", "A")], "B": [("2", "B")]})
    clock = [1000.0]
    monkeypatch.setattr(rel.time, "monotonic", lambda: clock[0])
    _moved(s, 1, scanning=True)
    _land(s)
    assert s.asked == ["A"]
    s._library_index.cache._artists = [("A", []), ("B", [])]
    clock[0] += 30
    _moved(s, 2, scanning=True)  # too soon after the last run
    assert s.asked == ["A"]
    _moved(s, 3)  # the scan's end always gets its run
    _land(s)
    assert s.asked == ["A", "B"]
    assert {r["artist_id"] for r in s._releases.followed_artists()} == {"1", "2"}


def test_a_hand_run_sets_the_automatic_one_aside(tmp_path, monkeypatch):
    s = _auto_stub(tmp_path, monkeypatch, [("A", [])], {"A": [("1", "A")]})
    s._rel_import_running = True
    s._rel_import_auto = True
    gen_before = s._rel_import_gen
    _bind(s, "startArtistImport")()
    # One bump drops the automatic run's answer, one starts the hand run.
    assert s._rel_import_gen == gen_before + 2 and s._rel_import_auto is False
    _land(s)
    assert len(s.artistImportReady.emits) == 1


def test_a_hand_run_in_flight_is_never_set_aside(tmp_path, monkeypatch):
    s = _auto_stub(tmp_path, monkeypatch, [("A", [])], {"A": [("1", "A")]})
    s._rel_import_running = True
    _moved(s, 1)
    assert s.asked == [] and s._relImportDone.emits == []


def test_automatic_errors_stay_off_the_dialog(tmp_path, monkeypatch):
    s = _auto_stub(tmp_path, monkeypatch, [("X", [])])
    s._library_index.cache._fail = True
    _moved(s, 1)
    _land(s)
    assert s.artistImportError.emits == []


def test_switching_it_on_looks_at_once_and_retires_the_offer(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", [])], {"A": [("1", "A")]}, monkeypatch=monkeypatch)
    s._library_stamp = 5
    s._library_index_building = False
    s._rel_lib_stamp_seen = 5
    s._rel_notice = {"kind": "more", "n": 1}
    s._waves_prefs["releases_auto_import"] = True
    _bind(s, "_releases_auto_import_switched")(True)
    assert s.artistImportNotice.emits[0] == {"kind": "", "n": 0}
    _land(s)
    assert {r["artist_id"] for r in s._releases.followed_artists()} == {"1"}


def test_signed_out_or_before_the_reveal_nothing_runs(tmp_path, monkeypatch):
    s = _auto_stub(tmp_path, monkeypatch, [("A", [])], {"A": [("1", "A")]})
    s._rel_revealed = False
    _moved(s, 1)
    s._rel_revealed = True
    s._logged_in = False
    _moved(s, 2)
    assert s.asked == [] and s._relImportDone.emits == []


def test_stop_clears_the_offer_and_looks_again_next_time(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", [])], monkeypatch=monkeypatch)
    s._rel_notice = {"kind": "more", "n": 3}
    s._rel_lib_stamp_seen = 7
    _bind(s, "_releases_stop")()
    assert s.artistImportNotice.emits[-1] == {"kind": "", "n": 0}
    assert s._rel_lib_stamp_seen == -1


# --- the automatic import gives way ---------------------------------------------


def test_the_automatic_import_waits_out_a_rate_limit(tmp_path, monkeypatch):
    s = _auto_stub(tmp_path, monkeypatch, [("A", [])], {"A": [("1", "A")]})
    clock = [1000.0]
    monkeypatch.setattr(rel.time, "monotonic", lambda: clock[0])
    s._rel_backoff_until = clock[0] + 900
    _moved(s, 1, scanning=True)
    _moved(s, 2)  # the scan's end, still inside the pause
    assert s.asked == [] and s._relImportDone.emits == []
    # The pause ends and a sweep step lands: the deferred look runs then.
    clock[0] += 901
    s._library_index_building = False
    _bind(s, "_on_rel_step_done")({"artist_id": "Z", "name": "", "fresh": []})
    _land(s)
    assert s.asked == ["A"] and {r["artist_id"] for r in s._releases.followed_artists()} == {"1"}


def test_switching_it_off_drops_a_run_in_flight(tmp_path, monkeypatch):
    s = _auto_stub(tmp_path, monkeypatch, [("A", [])], {"A": [("1", "A")]})
    _moved(s, 1)
    done = s._relImportDone.emits[-1]
    s._rel_import_running, s._rel_import_auto = True, True  # still in flight
    s._waves_prefs["releases_auto_import"] = False
    _bind(s, "_releases_auto_import_switched")(False)
    assert s._rel_import_running is False
    _bind(s, "_on_rel_import_done")(done)
    assert s._releases.followed_artists() == []


def test_the_automatic_import_never_brings_back_an_unfollowed_favorite(tmp_path, monkeypatch):
    s = _auto_stub(tmp_path, monkeypatch, [("X", [["7"]]), ("Y", [["8"]])])
    s._releases.sync_tidal_favorites([{"id": "7", "name": "X"}])
    s._releases.unfollow("7", remember_tidal=True)
    _moved(s, 1)
    _land(s)
    assert {r["artist_id"] for r in s._releases.followed_artists()} == {"8"}
    assert s._releases.tidal_unfollowed_ids() == {"7"}
    # The hand review still offers it, flagged so it starts unticked.
    s._waves_prefs["releases_auto_import"] = False
    _bind(s, "startArtistImport")()
    _land(s)
    review = s.artistImportReady.emits[-1]["matched"]
    assert [(r["artist"]["id"], bool(r.get("unfollowed"))) for r in review] == [("7", True)]


def test_a_scan_that_ends_during_a_hand_run_is_offered_when_it_lands(tmp_path, monkeypatch):
    s = _import_stub(tmp_path, [("A", [])], {"A": [("1", "A")], "B": [("2", "B")]}, monkeypatch=monkeypatch)
    s._library_stamp = 1
    _bind(s, "_releases_seen_add")(["a"])  # an earlier import looked at A
    s._rel_import_running = True  # a hand run is searching
    s._library_index.cache._artists = [("A", []), ("B", [])]
    _moved(s, 2)  # the scan ends meanwhile: nothing may start yet
    assert s._relMoreCounted.emits == []
    _bind(s, "cancelArtistImport")()  # the user stops the hand run
    _land_count(s)
    assert s.artistImportNotice.emits[-1] == {"kind": "more", "n": 1}


def test_a_hand_start_that_cannot_run_leaves_the_automatic_run_alone(tmp_path, monkeypatch):
    s = _auto_stub(tmp_path, monkeypatch, [("A", [])], {"A": [("1", "A")]})
    s._rel_import_running = True
    s._rel_import_auto = True
    gen = s._rel_import_gen
    s._library_index = None  # the library was turned off
    _bind(s, "startArtistImport")()
    assert s._rel_import_running is True and s._rel_import_gen == gen
    assert s.artistImportError.emits == ["no-library"]


def test_automatic_import_follows_what_the_library_vouches_for(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path,
        [("Afterglow", [])],
        {"Afterglow": [("1", "Afterglow"), ("2", "Afterglow")]},
        prefs={"releases_auto_import": True},
        monkeypatch=monkeypatch,
        titles={"afterglow": {"albums": ["Hollow"], "tracks": []}},
        releases={("1", False): [], ("2", False): ["Hollow"]},
    )
    _moved(s, 1)
    _land(s)
    assert {r["artist_id"] for r in s._releases.followed_artists()} == {"2"}
    assert s._releases.tray_count() == 0


def test_the_offer_leaves_out_artists_under_the_floor_and_a_lower_floor_looks_again(tmp_path, monkeypatch):
    s = _import_stub(
        tmp_path, [("A", [])], {"A": [("1", "A")]}, tracks={"A": 10, "B": 2, "C": 9}, monkeypatch=monkeypatch
    )
    _bind(s, "startArtistImport")()
    _land(s)
    s._library_index.cache._artists = [("A", []), ("B", []), ("C", [])]
    _moved(s, 2)
    _land_count(s)
    assert s.artistImportNotice.emits[-1] == {"kind": "more", "n": 1}
    s._waves_prefs["releases_import_min_tracks"] = "1"
    _bind(s, "_releases_import_floor_changed")()
    _land_count(s)
    assert s.artistImportNotice.emits[-1] == {"kind": "more", "n": 2}
