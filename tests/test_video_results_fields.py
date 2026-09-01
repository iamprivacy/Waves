"""The video payload carries what the results grid renders.

The redesigned VIDEOS section shows each video 16:9 at grid size with its
release date and real resolution underneath, so _video_dict has to carry a
larger art URL, the release date and a resolution label. _video_spec turns
TIDAL's quality tier (MP4_1080P) into that label and stays quiet when the
tier is missing or unfamiliar, so the tag falls back to its generic spec
instead of showing a raw enum name.
"""

import datetime as _dt
from types import SimpleNamespace
from typing import ClassVar

from waves.waves_ui.backend import WavesBridge, _video_spec


def _video(**kw):
    base = {
        "id": 42,
        "name": "House On Fire",
        "duration": 213,
        "explicit": True,
        "video_quality": "MP4_1080P",
        "release_date": _dt.datetime(2018, 1, 9),
        "artist": SimpleNamespace(id=7, name="Rise Against"),
        "artists": [SimpleNamespace(id=7, name="Rise Against")],
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _dict(video):
    bridge = WavesBridge.__new__(WavesBridge)
    bridge._remember = lambda *a, **k: None
    return WavesBridge._video_dict(bridge, video)


def test_spec_reads_the_resolution_out_of_the_tier():
    assert _video_spec(_video()) == "1080p"
    assert _video_spec(_video(video_quality="MP4_720P")) == "720p"


def test_spec_is_empty_when_the_tier_is_missing_or_unknown():
    assert _video_spec(_video(video_quality="")) == ""
    assert _video_spec(_video(video_quality=None)) == ""
    assert _video_spec(_video(video_quality="AUDIO_ONLY")) == ""


def test_payload_carries_date_quality_and_big_art():
    row = _dict(_video())
    assert row["date"] == "2018-01-09"
    assert row["quality"] == "1080p"
    assert "art_big" in row
    # The row layout still needs everything it had before.
    for key in ("id", "title", "artist", "artists", "art", "duration", "explicit"):
        assert key in row


def test_payload_survives_a_video_with_no_release_date():
    row = _dict(_video(release_date=None))
    assert row["date"] == ""


# ---- video dedup: quality/edit re-listings collapse, real siblings stay ------


def _vid(title, dur, explicit=False, quality="MP4_1080P"):
    return SimpleNamespace(
        name=title,
        artist=SimpleNamespace(name="Rise Against"),
        duration=dur,
        explicit=explicit,
        video_quality=quality,
    )


def _dedup_bridge(mode="explicit"):
    b = WavesBridge.__new__(WavesBridge)
    b._waves_prefs = {"explicit_mode": mode}
    b.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=SimpleNamespace(name="")))
    return b


def test_same_video_relisted_collapses_to_one():
    b = _dedup_bridge()
    out = b._dedup_videos([_vid("Savior", 242), _vid("Savior", 242), _vid("Savior", 243)])
    assert len(out) == 1


def test_clean_and_explicit_edits_meet_and_follow_the_preference():
    b = _dedup_bridge(mode="explicit")
    out = b._dedup_videos([_vid("Help Is On The Way", 259), _vid("Help Is On The Way", 253, explicit=True)])
    assert len(out) == 1 and out[0].explicit is True


def test_same_titled_webisodes_minutes_apart_all_survive():
    b = _dedup_bridge()
    out = b._dedup_videos([_vid("Blasting Room", 257), _vid("Blasting Room", 219), _vid("Blasting Room", 385)])
    assert len(out) == 3


# ---- the still is asked for at the size the surface draws -------------------


class _Still:
    """A video whose image() behaves like tidalapi's: a (width, height) PAIR,
    only four of which exist, anything else raises."""

    PAIRS: ClassVar = [(160, 107), (480, 320), (750, 500), (1080, 720)]

    def __init__(self):
        self.asked = []

    def image(self, width=1080, height=720):
        self.asked.append((width, height))
        if (width, height) not in self.PAIRS:
            raise ValueError(f"Invalid resolution {width} x {height}")
        return f"https://img.test/v/{width}x{height}.jpg"


def test_video_stills_are_asked_for_by_pair_not_by_square_dimension():
    still = _Still()
    row = _dict(_video(image=still.image))
    # A square dimension (what _image asks for) is not a valid pair, so it
    # raised and the fallback handed back the LARGEST still there is: every
    # thumbnail in the app was a full-size download.
    assert row["art"] == "https://img.test/v/160x107.jpg", "the 78px row thumb takes the smallest still"
    assert row["art_big"] == "https://img.test/v/750x500.jpg", "the results grid takes the grid-sized still"
    assert still.asked == [(160, 107), (750, 500)], still.asked
    assert (1080, 720) not in still.asked, "no surface draws a video at 1080x720"


def test_a_video_with_no_still_still_yields_a_payload():
    def boom(width=1080, height=720):
        raise AttributeError("No cover image")

    row = _dict(_video(image=boom))
    assert row["art"] == "" and row["art_big"] == ""
