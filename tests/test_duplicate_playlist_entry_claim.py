"""A playlist may list the same track twice, and both copies must land on ONE file.

TIDAL allows duplicate playlist entries. With several workers both occurrences
run in one batch, both pass the skip checks before either lands, and then both
claim a destination name. The claim held names against everybody, this item
included, so the second occurrence stepped aside onto "Song_01.flac": two
identical files, both tagged with the same item id. Nothing ever cleans the
twin up (the app never deletes user files) and every later run skips both to
the base name, so it sits there orphaned forever.

A name is now held against the ITEM that holds it. An item never has to make
way for itself; the post-stream existing-file check is what settles which of
the two occurrences actually writes the file, which is what the engine's own
comment already said happened.
"""

from __future__ import annotations

import pathlib
import threading
from unittest.mock import MagicMock

from waves.download import Download


def _make_download(tmp_path: pathlib.Path, *, skip_existing: bool = True) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=skip_existing,
        path_base=str(tmp_path),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.filename_illegal_replacement = ""
    dl.settings.data.filename_illegal_map = None
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()

    return dl


def test_the_same_track_twice_claims_one_name(tmp_path):
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"

    first, first_claim = dl._claim_destination(dst, "42")
    second, second_claim = dl._claim_destination(dst, "42")

    assert first == dst
    assert second == dst, "the twin was pushed onto a numbered name of its own"
    assert first_claim == second_claim


def test_a_different_track_of_the_same_name_still_steps_aside(tmp_path):
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"

    first, _ = dl._claim_destination(dst, "42")
    other, _ = dl._claim_destination(dst, "43")

    assert first == dst
    assert other == tmp_path / "Song_01.flac"


def test_an_item_with_no_id_makes_way_for_everyone(tmp_path):
    """No id means nothing can be proved about ownership, so nothing is exempt."""
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"

    first, _ = dl._claim_destination(dst, "")
    second, _ = dl._claim_destination(dst, "")

    assert first == dst
    assert second == tmp_path / "Song_01.flac"


def test_the_name_is_held_until_the_last_of_its_holders_lets_go(tmp_path):
    """Otherwise the first twin to finish hands the name to a stranger while
    the second is still writing there."""
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"

    _first, claim = dl._claim_destination(dst, "42")
    dl._claim_destination(dst, "42")

    dl._release_name(claim)
    stranger, _ = dl._claim_destination(dst, "43")
    assert stranger == tmp_path / "Song_01.flac", "the name was handed over mid-write"

    dl._release_name(claim)
    assert dl._names_reserved == {str(tmp_path / "Song_01.flac"): ("43", 1)}


def test_releasing_a_name_nobody_holds_is_harmless(tmp_path):
    dl = _make_download(tmp_path)

    dl._release_name(str(tmp_path / "Nothing.flac"))

    assert dl._names_reserved == {}


def test_a_landed_file_still_holds_its_name_against_a_stranger(tmp_path):
    """The written ledger, which outlives the claim, is unchanged by all this."""
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"

    _path, claim = dl._claim_destination(dst, "42")
    dl._record_name_written(dst, "42")
    dl._release_name(claim)

    assert dl._claim_destination(dst, "43")[0] == tmp_path / "Song_01.flac"
    assert dl._claim_destination(dst, "42")[0] == dst, "an item may retake a name it wrote itself"
