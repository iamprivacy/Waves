"""The shared-profile judge: which releases on one TIDAL profile the library's
artist owns, and when a profile holds enough other artists to set the rest
apart."""

from __future__ import annotations

from waves import release_ties as rt

PID = "100"


def _row(i, title, copy="(C) 2020 Home Label", upc="", artists=None):
    return {
        "id": str(i),
        "title": title,
        "copyright": copy,
        "upc": upc,
        "artists": artists if artists is not None else [{"id": PID, "name": "Band"}],
    }


LIB = {"albums": ["First Record", "Second Record"], "tracks": []}


def _shared_rows():
    rows = [
        _row(1, "First Record"),
        _row(2, "Second Record"),
        _row(3, "New Single"),  # tied by copyright
        _row(
            4, "Duet", copy="(C) 2021 Elsewhere", artists=[{"id": PID, "name": "Band"}, {"id": "7", "name": "Friend"}]
        ),
    ]
    rows[1]["artists"] = [{"id": PID, "name": "Band"}, {"id": "7", "name": "Friend"}]
    for i in range(5, 11):
        rows.append(_row(i, f"Stranger {i}", copy=f"2024 Other Label {i}"))
    return rows


def test_copyright_lines_meet_across_marks_and_years():
    assert rt.norm_copyright("(C) 2020 Jim Ward") == rt.norm_copyright("℗ 2022 Jim Ward") == "jim ward"
    assert rt.norm_copyright("") == ""


def test_a_profile_with_many_untied_releases_is_shared():
    v = rt.judge(PID, "Band", _shared_rows(), LIB)
    assert v.shared
    assert v.apart == frozenset(str(i) for i in range(5, 11))


def test_pinned_by_upc_with_leading_zeros():
    rows = _shared_rows()
    rows[0]["title"] = "Unknown Title"
    rows[0]["upc"] = "00012345"
    v = rt.judge(PID, "Band", rows, LIB, upcs=["12345"])
    assert v.shared and "1" not in v.apart


def test_credit_ignores_the_profile_itself_and_various_artists():
    rows = _shared_rows()
    rows[1]["artists"] = [{"id": PID, "name": "Band"}, {"id": "2935", "name": "Various Artists"}]
    rows[3]["artists"] = [{"id": "2935", "name": "Various Artists"}]
    v = rt.judge(PID, "Band", rows, LIB)
    assert "4" in v.apart


def test_a_clean_profile_with_a_few_untied_releases_stays_clean():
    rows = [_row(i, f"Record {i}") for i in range(20)]
    rows += [_row(100 + i, f"Odd {i}", copy=f"x {i}") for i in range(5)]
    lib = {"albums": ["Record 0", "Record 1"], "tracks": []}
    v = rt.judge(PID, "Band", rows, lib)
    assert v.state == "clean" and not v.apart


def test_too_few_untied_releases_is_not_shared():
    rows = _shared_rows()[:7]  # 3 strangers
    assert rt.judge(PID, "Band", rows, LIB).state == "clean"


def test_one_pinned_release_decides_nothing():
    lib = {"albums": ["First Record"], "tracks": []}
    assert rt.judge(PID, "Band", _shared_rows(), lib).state == "unknown"


def test_self_titled_albums_pin_nothing():
    lib = {"albums": ["Band", "First Record"], "tracks": []}
    rows = _shared_rows()
    rows[1]["title"] = "Band"
    assert rt.judge(PID, "Band", rows, lib).state == "unknown"


def test_rows_without_copyright_wait():
    rows = _shared_rows()
    for r in rows[2:5]:
        r["copyright"] = ""
    assert rt.judge(PID, "Band", rows, LIB).state == "unknown"


def test_ties_survive_a_round_trip_and_judge_a_new_release():
    v = rt.judge(PID, "Band", _shared_rows(), LIB)
    ties = rt.Ties.from_json(v.ties.to_json())
    assert ties == v.ties
    assert ties.keeps(_row(50, "Brand New"))
    assert ties.keeps(_row(51, "Collab", copy="zzz", artists=[{"id": "7", "name": "Friend"}]))
    assert not ties.keeps(_row(52, "Unrelated", copy="2026 Someone Else"))
    assert rt.Ties.from_json("not json") is None


def test_the_signature_changes_with_the_verdict():
    a = rt.judge(PID, "Band", _shared_rows(), LIB)
    b = rt.judge(PID, "Band", _shared_rows()[:9], LIB)
    assert a.sig != b.sig
    assert a.sig == rt.judge(PID, "Band", _shared_rows(), LIB).sig


def test_copyright_lines_in_any_script_keep_their_letters():
    assert rt.norm_copyright("℗ 2020 Студия Союз") == "студия союз"
    assert rt.norm_copyright("(P) 2019 ソニー・ミュージック") == rt.norm_copyright("℗ 2021 ソニー・ミュージック") != ""
    assert rt.norm_copyright("© 2021 Beyoncé Music") == rt.norm_copyright("(C) 2021 Beyonce Music")
    assert rt.norm_copyright("℗ 2020") == ""
    # Kana voicing marks are letters: two labels apart by one never tie.
    assert rt.norm_copyright("℗ 2020 ガレージ") != rt.norm_copyright("℗ 2020 カレージ")
    assert rt.norm_copyright("℗ 2020 ガレージ") == rt.norm_copyright("(P) 2021 ガレージ")


def test_a_profile_on_one_non_latin_label_is_clean():
    copy = "℗ 2020 Студия Союз"
    lib = {"albums": ["Первый", "Второй"], "tracks": []}
    rows = [_row(1, "Первый", copy=copy), _row(2, "Второй", copy=copy)]
    rows += [_row(i, f"Песня {i}", copy=f"℗ {2020 + i} Студия Союз") for i in range(3, 7)]
    v = rt.judge(PID, "Группа", rows, lib)
    assert v.state == "clean" and not v.apart


def test_lines_with_only_marks_and_years_count_as_no_line():
    rows = _shared_rows()
    for r in rows[2:]:
        r["copyright"] = "℗ 2024"
    v = rt.judge(PID, "Band", rows, LIB)
    assert v.state == "unknown" and not v.apart


def test_one_album_in_two_editions_pins_once():
    lib = {"albums": ["First Record"], "tracks": []}
    rows = [_row(1, "First Record"), _row(2, "First Record (Deluxe Edition)")]
    rows += [_row(i, f"Stranger {i}", copy=f"2024 Other Label {i}") for i in range(3, 9)]
    v = rt.judge(PID, "Band", rows, lib)
    assert v.state == "unknown" and not v.apart


def test_a_pin_with_no_title_counts_as_no_album():
    rows = _shared_rows()
    rows[1]["title"] = ""
    rows[1]["upc"] = "555"
    v = rt.judge(PID, "Band", rows, {"albums": ["First Record"], "tracks": []}, upcs=["555"])
    assert v.state == "unknown" and not v.apart
