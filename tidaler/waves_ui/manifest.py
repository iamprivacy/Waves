"""Waves-owned DASH manifest arithmetic.

tidalapi's ``DashInfo.get_urls`` derives the segment URL count from the MPD
SegmentTimeline as ``2 + sum(r if r else 1)``: one for the init segment, one
for "the very first media segment", then the repeat counts. Per the DASH spec
an ``<S r="N">`` element describes ``N + 1`` segments (r is the number of
*additional* repeats), so the correct count is ``1 + sum(r + 1)``. The two
formulas differ by ``1 - (number of S elements with r > 0)``:

* a typical track (one repeated S plus one short final S): difference 0,
  every generated URL is real audio;
* a very short track (a single S with r=0): difference +1, tidalapi emits one
  URL past the end of the audio. Requesting it returns HTTP 500. This is the
  "spurious tail" the download loop has historically tolerated;
* two or more repeated S elements: difference negative, tidalapi emits too
  FEW URLs and the download is missing audio regardless of segment failures.

Re-deriving the correct count from the manifest turns the download loop's
blanket last-segment leniency into an exact decision: a failed final segment
is harmless if and only if it is over-generated padding.

This lives in ``waves_ui`` rather than ``download.py`` so a future
tidal-dl-ng bump merges the shared files cleanly (the backend rework's
patchability constraint).
"""

from __future__ import annotations

import base64
import logging
import xml.etree.ElementTree as ET

logger = logging.getLogger("waves.manifest")


def overgenerated_tail_urls(stream_manifest) -> int | None:
    """How many trailing URLs in ``stream_manifest.urls`` are past the audio.

    Returns:
        int | None: ``> 0``: that many trailing URLs are over-generated
        padding whose failure is harmless. ``0``: every URL is required
        audio. ``< 0``: the URL list is short of the timeline (missing
        audio; logged as a warning). ``None``: not a DASH manifest or it
        could not be parsed, so nothing is proven either way.
    """
    try:
        mime = getattr(stream_manifest, "manifest_mime_type", "") or ""
        if "dash+xml" not in mime:
            return None

        xml_text = base64.b64decode(stream_manifest.manifest).decode("utf-8")
        # The payload comes from TIDAL's authenticated API over TLS and the
        # exact same bytes are already parsed with a stdlib-xml-based parser
        # inside tidalapi (mpegdash), so stdlib ElementTree adds no new
        # exposure here.
        root = ET.fromstring(xml_text)  # noqa: S314

        # Mirror tidalapi's traversal: the first SegmentTimeline in document
        # order belongs to the representation the URL list was built from.
        timeline = next((el for el in root.iter() if el.tag.endswith("SegmentTimeline")), None)
        if timeline is None:
            return None

        s_elements = [el for el in timeline if el.tag.endswith("}S") or el.tag == "S"]
        if not s_elements:
            return None

        # Init segment + (r + 1) media segments per S element.
        count_required = 1 + sum(int(s.get("r") or 0) + 1 for s in s_elements)
    except Exception:
        # An unexpected manifest shape proves nothing; the caller falls back
        # to the legacy leniency rather than failing good downloads.
        logger.warning("Could not derive the segment count from the DASH manifest")
        return None

    overgenerated = len(stream_manifest.urls) - count_required

    if overgenerated < 0:
        logger.warning(
            "Manifest timeline promises %d more segment(s) than the generated URL list; audio may be truncated",
            -overgenerated,
        )

    return overgenerated
