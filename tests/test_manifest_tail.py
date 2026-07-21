"""Regression guard: DASH tail-segment arithmetic (rework phase 1e).

THE BUG
-------
tidalapi's ``DashInfo.get_urls`` counts segment URLs as ``2 + sum(r if r else 1)``
over the MPD SegmentTimeline, but per the DASH spec an ``<S r="N">`` element
describes ``N + 1`` segments, so the correct count is ``1 + sum(r + 1)`` (one
init segment plus the media segments). The formulas differ by
``1 - (number of S elements with r > 0)``. On very short tracks (a single S
with r=0) tidalapi emits one URL past the end of the audio; requesting it
returns HTTP 500. The download loop historically tolerated ANY failed final
segment of a multi-segment track to absorb that quirk, which silently
truncated a real track whose genuinely required last segment failed (expired
link, 500, dropped connection) and reported it as a clean success.

THE FIX re-derives the correct count from the manifest
(``waves_ui.manifest.overgenerated_tail_urls``) and only exempts a failed
final segment when the manifest PROVES it is over-generated padding. When the
manifest proves nothing (video m3u8, BTS, unparseable), the legacy leniency is
kept so unproven cases cannot regress into false failures.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

from tidaler.waves_ui.manifest import overgenerated_tail_urls

_MPD_TEMPLATE = """<?xml version='1.0' encoding='UTF-8'?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT10S">
  <Period id="0">
    <AdaptationSet id="0" contentType="audio" mimeType="audio/mp4">
      <Representation id="0" codecs="flac" audioSamplingRate="44100" bandwidth="1000000">
        <SegmentTemplate initialization="https://x.invalid/init.mp4" media="https://x.invalid/seg_$Number$.mp4" startNumber="1" timescale="44100">
          <SegmentTimeline>{s_elements}</SegmentTimeline>
        </SegmentTemplate>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>"""


def _manifest(s_elements: str, n_urls: int, mime: str = "application/dash+xml") -> SimpleNamespace:
    xml = _MPD_TEMPLATE.format(s_elements=s_elements)
    return SimpleNamespace(
        manifest=base64.b64encode(xml.encode("utf-8")).decode("ascii"),
        manifest_mime_type=mime,
        urls=[f"https://x.invalid/seg_{i}.mp4" for i in range(n_urls)],
    )


def _tidalapi_url_count(repeats: list[int]) -> int:
    """The URL count tidalapi's DashInfo.get_urls would generate."""
    return 2 + sum(r if r else 1 for r in repeats)


def test_short_track_single_s_is_overgenerated_by_one():
    """A single <S> with no repeat (the very-short-track shape): tidalapi emits
    3 URLs but only init + 1 media segment exist, so exactly the final URL is
    spurious padding."""
    sm = _manifest('<S d="180224" />', n_urls=_tidalapi_url_count([0]))

    assert overgenerated_tail_urls(sm) == 1


def test_typical_track_repeated_s_plus_short_tail_is_exact():
    """The dominant real-world shape (one repeated S, one short final S):
    tidalapi's count is exact, so EVERY URL is required audio and a failed
    final segment is a real failure."""
    sm = _manifest('<S d="180224" r="3" /><S d="45056" />', n_urls=_tidalapi_url_count([3, 0]))

    assert overgenerated_tail_urls(sm) == 0


def test_single_repeated_s_is_exact():
    """A track that is an exact multiple of the segment duration (single S,
    r > 0): tidalapi's count is exact."""
    sm = _manifest('<S d="180224" r="5" />', n_urls=_tidalapi_url_count([5]))

    assert overgenerated_tail_urls(sm) == 0


def test_two_repeated_s_elements_undergenerate():
    """Two S elements with r > 0: tidalapi emits one URL too FEW (audio is
    missing regardless of failures). Reported as negative so the caller treats
    the final URL as required and the helper logs a warning."""
    sm = _manifest(
        '<S d="180224" r="2" /><S d="90112" r="3" /><S d="45056" />',
        n_urls=_tidalapi_url_count([2, 3, 0]),
    )

    assert overgenerated_tail_urls(sm) == -1


def test_non_dash_manifest_is_unproven():
    """A BTS (JSON) manifest proves nothing: None keeps the legacy leniency."""
    sm = SimpleNamespace(
        manifest=base64.b64encode(b'{"urls": ["u"]}').decode("ascii"),
        manifest_mime_type="application/vnd.tidal.bts",
        urls=["u"],
    )

    assert overgenerated_tail_urls(sm) is None


def test_unparseable_manifest_is_unproven():
    """Garbage bytes must not raise; None keeps the legacy leniency."""
    sm = SimpleNamespace(
        manifest="not-base64!!!",
        manifest_mime_type="application/dash+xml",
        urls=["u1", "u2"],
    )

    assert overgenerated_tail_urls(sm) is None


def test_manifest_without_timeline_is_unproven():
    xml = _MPD_TEMPLATE.format(s_elements="")
    xml = xml.replace("<SegmentTimeline></SegmentTimeline>", "")
    sm = SimpleNamespace(
        manifest=base64.b64encode(xml.encode("utf-8")).decode("ascii"),
        manifest_mime_type="application/dash+xml",
        urls=["u1", "u2"],
    )

    assert overgenerated_tail_urls(sm) is None
