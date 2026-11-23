"""A track tagged with Waves' own explicit glyph still meets its library copy.

The "Mark explicit in title" setting appends METADATA_EXPLICIT (" 🅴") to the
title tag of every explicit track Waves downloads. The presence key folded the
"(Explicit)" spellings but not this glyph, so an album downloaded with the
setting on lit IN LIBRARY while every explicit row in it read as missing and
offered a download.
"""

from __future__ import annotations

from waves import matching
from waves.constants import METADATA_EXPLICIT
from waves.waves_ui import backend


def test_glyph_folds_out_of_the_track_key():
    assert matching.track_key("Sound the Alarm" + METADATA_EXPLICIT, "Adept") == matching.track_key(
        "Sound the Alarm", "Adept"
    )


def test_glyph_folds_out_of_the_title_norm():
    assert matching.norm_title("Everything Dies" + METADATA_EXPLICIT) == "everything dies"


def test_glyph_folds_out_of_the_grouping_title():
    assert backend._norm_title("Everything Dies" + METADATA_EXPLICIT) == backend._norm_title("Everything Dies")


def test_glyph_tagged_file_proves_the_track_on_screen():
    key = matching.track_key("Grow Up, Peter Pan" + METADATA_EXPLICIT, "Adept")
    index = {
        key: [
            {
                "id": "/music/Adept/[2009] Another Year of Disaster (Explicit)",
                "codec": "flac",
                "bitrate": 900,
                "bits": 16,
                "rate": 44100,
                "length": 209,
                "album": "Another Year of Disaster",
                "album_year": "2009",
                "explicit": -1,
            }
        ]
    }
    v = matching.decide_track_presence(
        "Grow Up, Peter Pan",
        "Adept",
        index,
        album="Another Year of Disaster",
        album_year="2009",
        duration=209,
        explicit=True,
    )
    assert v["present"] is True
    assert v["sure"] is True


def test_a_plain_e_letter_in_a_title_is_kept():
    assert matching.norm_title("E") == "e"
    assert matching.norm_title("Sound the Alarm E") == "sound the alarm e"
