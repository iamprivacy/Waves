"""The missing-tracks report: a CSV of the queued tracks that did not land.

A long playlist can finish with a handful of tracks that failed or that TIDAL
no longer carries, and the queue drawer is the only place naming them (issue
#54). This writes them out, one line per track, so they can be looked up
elsewhere: who, what, from which release, its ISRC and its TIDAL link.

Pure: no Qt, no bridge. The bridge snapshots its per-track registry on the GUI
thread (see ``exportMissingTracks`` in waves_ui/backend.py) and hands the rows
here on a worker. The file goes under the app's own reports folder beside the
FLAC check's reports, named so one export never replaces another.
"""

from __future__ import annotations

import contextlib
import csv
import os
import pathlib
import re
import time
from collections.abc import Iterable, Mapping

# The per-track statuses that count as "did not land": the engine gave up on
# the track, or TIDAL refused it. A track the user stopped is not missing, it
# is waiting for a retry.
MISSING_STATUSES = ("failed", "unavailable")

STATUS_WORDS = {
    "failed": "Failed",
    "unavailable": "Not available on TIDAL",
}

COLUMNS = (
    "Status",
    "Artist",
    "Title",
    "Album",
    "Collection",
    "Position",
    "Duration",
    "ISRC",
    "TIDAL ID",
    "TIDAL link",
)

# A catalog name that opens with one of these characters is read as a formula
# by a spreadsheet, so the text cells write it behind an apostrophe (which the
# spreadsheet then hides).
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")

NAME_PREFIX = "missing-tracks-"
NAME_RE = re.compile(r"missing-tracks-\d{8}-\d{6}(-[2-9]|-[1-9]\d{1,2})?\.csv\Z")

# Reports written in the same second are told apart by a counter, up to this.
_SUFFIX_MAX = 999


def is_missing(status: str) -> bool:
    return str(status or "") in MISSING_STATUSES


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_LEAD) else text


def tidal_link(media_id: str, kind: str = "track") -> str:
    """The public TIDAL page for a track or video id ("" without an id)."""
    media_id = str(media_id or "").strip()
    if not media_id.isdigit():
        return ""
    return f"https://tidal.com/browse/{'video' if kind == 'video' else 'track'}/{media_id}"


def row_cells(row: Mapping) -> list[str]:
    """One CSV line for one missing track (a dict of the registry's fields
    plus ``collection``)."""
    status = str(row.get("status", "") or "")
    media_id = str(row.get("id", "") or "")
    kind = str(row.get("kind", "") or "track")
    position = row.get("num") or ""
    return [
        STATUS_WORDS.get(status, status.capitalize()),
        _cell(row.get("artist", "")),
        _cell(row.get("title", "")),
        _cell(row.get("album", "")),
        _cell(row.get("collection", "")),
        str(position),
        str(row.get("duration", "") or ""),
        str(row.get("isrc", "") or ""),
        media_id if media_id.isdigit() else "",
        tidal_link(media_id, kind),
    ]


def write_report(path: str | os.PathLike, rows: Iterable[Mapping], *, exclusive: bool = False) -> str:
    """Write the rows as a CSV (UTF-8 with a byte-order mark, so a
    spreadsheet opens accented names correctly). ``exclusive`` refuses to
    replace a file that is already there (:class:`FileExistsError`)."""
    out = pathlib.Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x" if exclusive else "w", encoding="utf-8-sig", errors="backslashreplace", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow(row_cells(row))
    return str(out)


def report_name(when: float | None = None, n: int = 1) -> str:
    """``missing-tracks-YYYYMMDD-HHMMSS.csv``; a second report in the same
    second is ``...-HHMMSS-2.csv``, and so on (``n``)."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(when))
    return f"{NAME_PREFIX}{stamp}{f'-{n}' if n > 1 else ''}.csv"


def write_new_report(folder: str | os.PathLike, rows: Iterable[Mapping], when: float | None = None) -> str:
    """Write a report under a name no earlier report has: one never
    replaces another. Returns the path."""
    when = time.time() if when is None else when
    rows = list(rows)
    for n in range(1, _SUFFIX_MAX + 1):
        with contextlib.suppress(FileExistsError):
            return write_report(os.path.join(folder, report_name(when, n)), rows, exclusive=True)
    raise FileExistsError(os.path.join(folder, report_name(when, _SUFFIX_MAX)))
