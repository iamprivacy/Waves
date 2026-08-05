"""LRCLIB lyrics lookup.

TIDAL serves machine-transcribed lyrics for tracks whose IDs have no
human-submitted text yet (recent re-recordings and reissues especially), and
those transcriptions are often garbled. LRCLIB (lrclib.net, the community
database behind LRCGet) carries human-submitted synced lyrics, so it is tried
first when the preference is on; TIDAL's own lyrics stay as the fallback.

The lookup is two requests at most: the exact-signature endpoint (artist,
title, album, duration), then one search filtered by duration. Any miss or
network problem returns empty strings so the caller falls through to TIDAL;
this module never raises.
"""

import logging

import requests

from tidaler import version_app
from tidaler.waves_ui.diagnostics import content as log_content

logger = logging.getLogger("waves.lyrics")

LRCLIB_API: str = "https://lrclib.net/api"
# (connect, read) timeouts: a lyrics lookup must never stall a download for long.
_TIMEOUT: tuple[float, float] = (5.0, 10.0)
# A search hit only counts when its recording length matches ours this closely,
# so a live take or a re-recording never donates mistimed sync points.
_DURATION_TOLERANCE_SEC: int = 2

_user_agent: str | None = None


def _headers() -> dict[str, str]:
    # LRCLIB asks clients to identify themselves; built lazily because
    # version_app() reads package metadata.
    global _user_agent
    if _user_agent is None:
        _user_agent = f"Waves/{version_app()} (https://github.com/iamprivacy/Waves)"
    return {"User-Agent": _user_agent}


def _extract(record: dict) -> tuple[str, str]:
    """Pull (synced, plain) lyrics out of one LRCLIB record."""
    if not isinstance(record, dict) or record.get("instrumental"):
        return "", ""
    synced = record.get("syncedLyrics") or ""
    plain = record.get("plainLyrics") or ""
    return synced, plain


def _get_exact(session: requests.Session, artist: str, title: str, album: str, duration: int) -> tuple[str, str]:
    response = session.get(
        f"{LRCLIB_API}/get",
        params={
            "artist_name": artist,
            "track_name": title,
            "album_name": album,
            "duration": duration,
        },
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        return "", ""
    return _extract(response.json())


def _search(session: requests.Session, artist: str, title: str, duration: int) -> tuple[str, str]:
    response = session.get(
        f"{LRCLIB_API}/search",
        params={"track_name": title, "artist_name": artist},
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        return "", ""

    results = response.json()
    if not isinstance(results, list):
        return "", ""

    # Keep only recordings of (near enough) the same length, then prefer a
    # synced hit over a plain-text one.
    candidates: list[tuple[str, str]] = []
    for record in results:
        if not isinstance(record, dict):
            continue
        length = record.get("duration")
        if not isinstance(length, int | float) or abs(length - duration) > _DURATION_TOLERANCE_SEC:
            continue
        synced, plain = _extract(record)
        if synced or plain:
            candidates.append((synced, plain))

    for synced, plain in candidates:
        if synced:
            return synced, plain
    return candidates[0] if candidates else ("", "")


def lyrics_file_choice(synced: str, plain: str, synced_only: bool) -> tuple[str, str]:
    """Pick what the lyrics sidecar file should contain and its extension.

    Timed lyrics always win and belong in a ``.lrc``; untimed text goes to a
    ``.txt`` instead (a bare text dump inside a ``.lrc`` pretends to be synced
    and confuses players). With ``synced_only`` on, untimed lyrics produce no
    file at all.

    Returns:
        ``(content, suffix)``; both empty when nothing should be written.
    """
    if synced:
        return synced, ".lrc"
    if plain and not synced_only:
        return plain, ".txt"
    return "", ""


def fetch_lrclib_lyrics(
    session: requests.Session,
    artist: str,
    title: str,
    album: str,
    duration: int,
    title_bare: str | None = None,
) -> tuple[str, str]:
    """Fetch lyrics for one track from LRCLIB.

    Args:
        session: Pooled HTTP session to reuse.
        artist: Primary artist name.
        title: Full track title (including any version suffix), used for the
            exact-signature lookup.
        album: Album name.
        duration: Track length in whole seconds.
        title_bare: Title without the version suffix, used for the search
            fallback (defaults to ``title``).

    Returns:
        ``(synced, plain)`` lyrics text; both empty when there is no confident
        match or the service is unreachable. Never raises.
    """
    if not artist or not title or not duration:
        return "", ""

    try:
        synced, plain = _get_exact(session, artist, title, album, duration)
        if not (synced or plain):
            synced, plain = _search(session, artist, title_bare or title, duration)
    except Exception:
        logger.debug("LRCLIB lookup failed for `%s`.", log_content(f"{artist} - {title}"), exc_info=True)
        return "", ""

    if synced or plain:
        logger.info(
            "LRCLIB lyrics found for `%s` (%s).",
            log_content(f"{artist} - {title}"),
            "synced" if synced else "plain",
        )
    return synced, plain
