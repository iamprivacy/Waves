"""The spellings an artist folder may have on disk (waves/helper/path.py
folder_name_candidates), for the library scan's probe by name.

The probe stats each spelling in turn and stops at the first hit, so the
order is the likelihood order: raw (a library another tool organised), then
what this install writes, then older spellings. Duplicates by comparison key
would cost a wasted stat on a case-folding filesystem, and a spelling with a
separator in it can never be one folder.
"""

from __future__ import annotations

from waves.helper.path import folder_name_candidates


def test_a_plain_name_is_asked_once():
    assert folder_name_candidates("Marina") == ["Marina"]


def test_the_install_spelling_comes_before_the_older_ones():
    # The user's stand-in first, then the general one, then plain removal.
    assert folder_name_candidates("AC/DC", "_", {"/": "-"}) == ["AC-DC", "AC_DC", "ACDC"]


def test_a_separator_never_makes_a_folder_name():
    out = folder_name_candidates("AC/DC")
    assert "AC/DC" not in out
    assert out == ["ACDC"]


def test_the_prefix_gains_the_comma_form():
    assert folder_name_candidates("The Beatles") == ["The Beatles", "Beatles, The"]
    # Not for a name that merely begins with those letters.
    assert folder_name_candidates("Theory of a Deadman") == ["Theory of a Deadman"]


def test_spellings_are_deduped_by_comparison_key():
    # A map that only changes case would ask the same folder twice on APFS.
    assert folder_name_candidates("Björk", "", {"ö": "Ö"}) == ["Björk"]


def test_untidied_spacing_is_kept_as_its_own_spelling():
    # Plain removal leaves a double space where the slash stood; releases
    # before 0.1.17 wrote it that way, so it is still a spelling to try.
    out = folder_name_candidates("Sun / Moon")
    assert "Sun Moon" in out
    assert "Sun  Moon" in out
    assert out.index("Sun Moon") < out.index("Sun  Moon")


def test_the_untidied_spelling_ignores_this_install_s_stand_ins():
    # The default empty stand-in is the one value where building the untidied
    # spelling with the install's settings and building it without them agree,
    # so the pin above cannot see the difference. The write side's fourth
    # spelling (download.py's build(False)) takes NO stand-ins, and this is the
    # spelling that reaches a folder written before 0.1.17. Asking with a
    # stand-in applied asks about a folder no version ever wrote.
    out = folder_name_candidates("Sun / Moon", "-", {})
    assert "Sun  Moon" in out, f"the pre-0.1.17 folder is never stat'd: {out}"
    assert "Sun - Moon" in out


def test_empty_and_blank_names_give_nothing():
    assert folder_name_candidates("") == []
    assert folder_name_candidates("   ") == []
