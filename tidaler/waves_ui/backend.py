"""Bridge between the tidaler backend and the Waves QML UI.

Everything the QML layer needs is exposed here as Qt properties, slots and
signals on a single ``WavesBridge`` QObject. The bridge wraps the existing
backend objects (``Settings``, ``Tidal``, ``Download``) and runs blocking
calls (login, search, downloads, artist pages) on a ``QThreadPool`` so the UI
never freezes.

Search results are grouped by type and flattened into plain dicts carrying
cover-art URLs, popularity and inline metadata, so the QML stays declarative
and never touches a tidalapi object directly.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event, Lock

from PySide6 import QtCore, QtGui
from PySide6.QtCore import Property, QEvent, QObject, Qt, QTimer, Signal, Slot
from rich.progress import Progress
from tidalapi import page as tidal_page
from tidalapi.album import Album
from tidalapi.artist import Artist
from tidalapi.media import Quality, Track, Video
from tidalapi.mix import Mix
from tidalapi.playlist import Playlist

import tidaler.download as _tidaler_download
from tidaler.config import Settings, Tidal
from tidaler.constants import (
    CoverDimensions,
    DownsampleTarget,
    InitialKey,
    MediaType,
    MetadataTargetUPC,
    QualityVideo,
)
from tidaler.download import Download
from tidaler.helper.tidal import (
    get_tidal_media_id,
    get_tidal_media_type,
    instantiate_media,
    name_builder_album_artist,
    name_builder_artist,
    name_builder_item,
    name_builder_title,
    quality_audio_highest,
    search_results_all,
    user_media_lists,
)
from tidaler.model.cfg import HelpSettings
from tidaler.model.gui_data import ProgressBars
from tidaler.waves_ui import proc
from tidaler.worker import Worker

from . import __version__ as _WAVES_VERSION
from . import devlog
from .ffmpeg_manager import FfmpegCancelled, FfmpegManager
from .updater import AppUpdater, UpdateCancelled

logger = logging.getLogger("waves")

# The trackpad back-gesture (horizontal scroll → navigate back) is a macOS-only
# convention; on Linux/Windows a horizontal wheel is ordinary scrolling.
_IS_MACOS = sys.platform == "darwin"

# Type registries, which coercion each settings key needs. settingsSchema()
# arranges these into task-based sections for the page; the lists below only
# decide how a value is read from / written back to the config.
_FLAG_FIELDS = [
    "video_download",
    "video_convert_mp4",
    "lyrics_embed",
    "lyrics_file",
    "download_delay",
    "extract_flac",
    "metadata_cover_embed",
    "cover_album_file",
    "skip_existing",
    "symlink_to_track",
    "playlist_create",
    "mark_explicit",
    "use_primary_album_artist",
    "download_dolby_atmos",
    # Advanced
    "downsample_enabled",
    "metadata_replay_gain",
    "metadata_write_url",
]
_CHOICE_FIELDS = [
    ("quality_audio", Quality),
    ("quality_video", QualityVideo),
    ("metadata_cover_dimension", CoverDimensions),
    # Advanced
    ("downsample_target", DownsampleTarget),
    ("metadata_target_upc", MetadataTargetUPC),
    ("initial_key_format", InitialKey),
]
_NUMBER_FIELDS = [
    "album_track_num_pad_min",
    "downloads_concurrent_max",
    # Advanced
    "downloads_simultaneous_per_track_max",
    "api_rate_limit_batch_size",
]
# Second-scale floats (Advanced), rendered as a decimal stepper.
_FLOAT_FIELDS = ["download_delay_sec_min", "download_delay_sec_max", "api_rate_limit_delay_sec"]
# Per-bucket cap for the live tidalapi object cache (_objs). A new search clears
# the buckets, but browsing artists/albums without searching keeps appending, so
# cap each bucket far above any realistic single view and evict oldest-first.
_MAX_OBJS_PER_BUCKET = 2000
_PATH_FIELDS = [
    "download_base_path",
    "format_track",
    "format_video",
    "format_album",
    "format_playlist",
    "format_mix",
    "filename_delimiter_artist",
    "filename_delimiter_album_artist",
    # Surfaced under Advanced as a power-user override. The Settings "FFmpeg"
    # card normally manages the binary; an explicit path here wins over the
    # managed copy (see _resolve_ffmpeg).
    "path_binary_ffmpeg",
]
_BROWSE = {"download_base_path": "dir", "path_binary_ffmpeg": "file"}
_ENUM_BY_FIELD = dict(_CHOICE_FIELDS)
# Flags that do nothing without FFmpeg, greyed out on the page when it's absent.
_FFMPEG_DEPENDENT = {"video_convert_mp4", "extract_flac"}

# Human field titles, overriding the auto-prettified key (e.g. "Api rate limit
# delay sec"). Anything not listed falls back to _pretty(key).
_FIELD_LABELS = {
    # Downloads
    "download_base_path": "Download folder",
    "quality_audio": "Audio quality",
    "quality_video": "Video quality",
    "downloads_concurrent_max": "Concurrent downloads",
    "download_dolby_atmos": "Download Dolby Atmos",
    # File organization
    "format_track": "Track path & name",
    "format_album": "Album path & name",
    "format_playlist": "Playlist path & name",
    "format_video": "Video path & name",
    "format_mix": "Mix path & name",
    "album_track_num_pad_min": "Track-number padding",
    "filename_delimiter_artist": "Artist separator",
    "filename_delimiter_album_artist": "Album-artist separator",
    "use_primary_album_artist": "Primary album artist for folders",
    "symlink_to_track": "Symlink into track folder",
    "playlist_create": "Create .m3u8 playlist",
    # Metadata & artwork
    "metadata_cover_dimension": "Embedded cover size",
    "metadata_cover_embed": "Embed cover art",
    "cover_album_file": "Save cover.jpg",
    "lyrics_embed": "Embed lyrics",
    "lyrics_file": "Save .lrc file",
    "mark_explicit": "Mark explicit in title",
    # Advanced
    "path_binary_ffmpeg": "FFmpeg binary path",
    "downsample_target": "Downsample target",
    "downloads_simultaneous_per_track_max": "Parallel chunks per track",
    "download_delay_sec_min": "Minimum download delay (s)",
    "download_delay_sec_max": "Maximum download delay (s)",
    "metadata_target_upc": "UPC tag field",
    "initial_key_format": "Initial-key tag format",
    "api_rate_limit_batch_size": "API rate-limit batch size",
    "api_rate_limit_delay_sec": "API rate-limit delay (s)",
    "downsample_enabled": "Downsample hi-res FLAC",
    "metadata_replay_gain": "Write ReplayGain tags",
    "metadata_write_url": "Write source URL tag",
}

# Human labels for enum dropdown values, keyed by field then by enum member
# name (the stored value). Unmapped members fall back to the raw name.
_ENUM_LABELS = {
    "quality_audio": {
        "low_96k": "Low (96 kbps)",
        "low_320k": "High (320 kbps)",
        "high_lossless": "Lossless (16-bit)",
        "hi_res_lossless": "Max · Hi-Res (24-bit)",
    },
    "quality_video": {"P360": "360p", "P480": "480p", "P720": "720p", "P1080": "1080p"},
    "metadata_cover_dimension": {
        "Px80": "80×80",
        "Px160": "160×160",
        "Px320": "320×320",
        "Px640": "640×640",
        "Px1280": "1280×1280",
        "PxORIGIN": "Original",
    },
    "downsample_target": {"BIT16_48": "16-bit / 48 kHz", "BIT24_48": "24-bit / 48 kHz"},
    "metadata_target_upc": {"UPC": "UPC", "BARCODE": "Barcode", "EAN": "EAN"},
    "initial_key_format": {"ALPHANUMERIC": "Alphanumeric (Camelot)", "CLASSIC": "Classic"},
    "explicit_mode": {"explicit": "Explicit", "clean": "Clean", "both": "Both"},
    "edition_conflict": {
        "keep_both": "Keep both",
        "completeness": "Most complete",
        "quality": "Highest quality",
        "merge": "Best of both",
    },
    "update_cadence": {"launch": "Every launch", "daily": "Once a day"},
}


def _enum_options(key: str, members) -> list:
    """Build [{value, label}] dropdown options for an enum field. ``members``
    may be an enum class (uses each member's ``name``) or a list of value
    strings (for the Waves prefs, which aren't backed by a Python enum)."""
    labels = _ENUM_LABELS.get(key, {})
    out = []
    for m in members:
        v = getattr(m, "name", m)
        out.append({"value": v, "label": labels.get(v, v)})
    return out


# Batch size for "My Tidal" infinite scroll. Each category is fetched one page
# at a time (with a network offset) and QML renders the rows lazily in a
# virtualised ListView, prefetching the next page before the user hits the
# bottom, so even a multi-thousand-item library loads smoothly and never builds
# thousands of delegates at once.
_LIBRARY_PAGE = 100


def _pretty(key: str) -> str:
    return key.replace("_", " ").capitalize()


class _ProgressSignals(QObject):
    """Per-download relay carrying the signals ``Download`` expects.

    ``Download`` emits ``item``/``list_item`` from its own worker threads (the
    ``concurrent.futures`` pool inside ``_execute_collection_downloads``). We
    route the relevant one (``list_item`` per finished track for collections,
    ``item`` for single media) to a **bound slot on this QObject** so the
    cross-thread emit is delivered as a queued call on the GUI thread and the
    receiver can't be garbage-collected while the download runs, a bare closure
    connected to the signal proved unreliable and the per-track progress never
    reached the UI (it jumped straight 0% → 100%)."""

    item = Signal(float)
    item_name = Signal(str)
    list_item = Signal(float)
    list_name = Signal(str)
    # Per-track lifecycle (emitted by _TrackedDownload from its worker threads);
    # the dict payload carries id/title/num/vol/duration/desc/status.
    track_event = Signal("QVariant")

    def __init__(self, bridge: WavesBridge, qid: int, media_id: str, collection: bool) -> None:
        super().__init__(bridge)  # parent => GUI-thread affinity
        self._bridge = bridge
        self._qid = qid
        self._media_id = media_id
        (self.list_item if collection else self.item).connect(self._on_pct)
        self.track_event.connect(self._on_track_event)

    @Slot(float)
    def _on_pct(self, pct: float) -> None:
        self._bridge._report_pct(self._media_id, self._qid, float(pct))

    @Slot("QVariant")
    def _on_track_event(self, ev) -> None:
        self._bridge._track_lifecycle(self._qid, dict(ev))


class _TrackedDownload(Download):
    """``Download`` that reports each track's lifecycle to the queue drawer.

    ``Download.items`` fans every collection track through ``self.item`` on a
    worker pool, so overriding ``item`` observes exact per-track state without
    touching tidaler's download.py. Each event also carries the description
    string download.py registers on its rich ``Progress`` task, letting the
    bridge's poller read live per-track percentages out of ``self.progress``.
    """

    def __init__(self, *args, track_signals: _ProgressSignals | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._track_signals = track_signals

    def item(self, *args, media=None, event_stop=None, **kwargs):
        relay = self._track_signals
        if relay is None or media is None or getattr(media, "id", None) is None:
            return super().item(*args, media=media, event_stop=event_stop, **kwargs)
        name = name_builder_item(media)
        base = {
            "id": str(media.id),
            "title": name_builder_title(media),
            "num": int(getattr(media, "track_num", 0) or 0),
            "vol": int(getattr(media, "volume_num", 1) or 1),
            "duration": _fmt_duration(getattr(media, "duration", 0)),
            # Must mirror _setup_progress()'s add_task description exactly.
            "desc": f"[blue]Item '{name[:30]}'",
        }
        relay.track_event.emit({**base, "status": "running"})
        try:
            ok, path = super().item(*args, media=media, event_stop=event_stop, **kwargs)
        except Exception:
            relay.track_event.emit({**base, "status": "failed"})
            raise
        aborted = self.event_abort.is_set() or (event_stop is not None and event_stop.is_set())
        status = "done" if ok else ("cancelled" if aborted else "failed")
        relay.track_event.emit({**base, "status": status})
        return ok, path


def _fmt_duration(seconds: int | None) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _image(obj, dimension: int = 320) -> str:
    """Best-effort cover/picture URL for an album, artist or track.

    Falls back to the library default size if the requested dimension is
    rejected (artist art only allows 160/320/480/750, so e.g. 640 raises).
    """
    target = obj if hasattr(obj, "image") else getattr(obj, "album", None)
    if target is None or not hasattr(target, "image"):
        return ""
    for call in (lambda: target.image(dimension), lambda: target.image()):
        try:
            url = call()
        except Exception:
            url = ""
        if url:
            return url
    return ""


def _artist_roles(artist) -> str:
    roles = getattr(artist, "roles", None) or []
    names = []
    for role in roles:
        name = getattr(role, "name", None) or str(role)
        names.append(name.replace("_", " ").title())
    # de-duplicate while preserving order
    return ", ".join(dict.fromkeys(names)) or "Artist"


def _artist_popularity(artist) -> int:
    """Best-effort popularity from the raw artist endpoint (-1 if absent)."""
    try:
        payload = artist.request.request("GET", f"artists/{artist.id}").json()
        value = payload.get("popularity")
        return max(0, min(100, int(value))) if value is not None else -1
    except Exception:
        return -1


def _release_obj(obj):
    # Some payloads (e.g. an artist's top tracks) omit release_date on both the
    # track and its album stub but still carry tidal_release_date on the track.
    album = getattr(obj, "album", None)
    for source, attr in (
        (obj, "release_date"),
        (album, "release_date"),
        (obj, "tidal_release_date"),
        (album, "tidal_release_date"),
    ):
        date = getattr(source, attr, None)
        if date is not None:
            return date
    return None


def _year(obj) -> str:
    date = _release_obj(obj)
    return str(date.year) if date is not None else ""


def _release_date(obj) -> str:
    date = _release_obj(obj)
    if date is None:
        return ""
    try:
        return date.strftime("%Y-%m-%d")
    except Exception:
        return str(date)


def _quality_label(obj) -> str:
    # Prefer the true highest available quality (from media_metadata_tags),
    # since audio_quality alone reports LOSSLESS even when hi-res is available.
    name = ""
    try:
        if hasattr(obj, "media_metadata_tags"):
            name = getattr(quality_audio_highest(obj), "name", "")
    except Exception:
        name = ""
    if not name:
        aq = getattr(obj, "audio_quality", None)
        name = getattr(aq, "name", "") or (str(aq) if aq else "")
    lowered = name.lower()
    if "hi_res" in lowered or "hires" in lowered:
        return "HI-RES"
    if "lossless" in lowered:
        return "LOSSLESS"
    if "320" in lowered or lowered == "high":
        return "HIGH"
    if "96" in lowered or lowered == "low":
        return "LOW"
    return name.replace("_", " ").upper() if name else ""


def _track_count(obj) -> int:
    return int(getattr(obj, "num_tracks", 0) or 0) + int(getattr(obj, "num_videos", 0) or 0)


def _popularity(obj) -> int:
    try:
        return max(0, min(100, int(getattr(obj, "popularity", 0) or 0)))
    except Exception:
        return 0


def _artist_id(obj) -> str:
    artist = getattr(obj, "artist", None)
    if artist is None:
        artists = getattr(obj, "artists", None) or []
        artist = artists[0] if artists else None
    return str(getattr(artist, "id", "")) if artist is not None else ""


def _primary_artist_name(obj) -> str:
    """Name of the primary credited artist."""
    artist = getattr(obj, "artist", None)
    if artist is None:
        artists = getattr(obj, "artists", None) or []
        artist = artists[0] if artists else None
    return getattr(artist, "name", "") or ""


def _norm_artist(name: str) -> str:
    """Lowercased, whitespace-collapsed artist name for stable grouping."""
    return re.sub(r"\s+", " ", name or "").strip().lower()


# TIDAL's canonical "Various Artists" entity is id 2935, but localized markets
# serve a compilation's credit under a different id with a translated name (e.g.
# id 9174206 for the Japanese "ヴァリアス・アーティスト"), so we match the id OR a
# multilingual name marker. The shared placeholder image is the generic "no
# picture" art (used by obscure real artists too), so it is deliberately not a
# signal here.
_VARIOUS_ARTISTS_IDS = {2935}
_VARIOUS_ARTISTS_RE = re.compile(
    r"various\s+artist|verschiedene\s+interpreten|multi[\s-]?interpr|varios\s+artistas"
    r"|v[áa]rios\s+artistas|artisti\s+vari|ヴァリアス|群星",
    re.IGNORECASE,
)


def _is_album_entity(obj) -> bool:
    """True only for album releases (albums / EPs / singles / compilations). A
    discography download must never queue playlists or mixes, those are their
    own section of the app and would be redundant here. tidalapi's artist
    release getters already return only albums, but this makes the invariant
    explicit and guards against any future leakage."""
    return isinstance(obj, Album)


def _artist_on_track(track, artist_id: str) -> bool:
    """True when the artist appears in a track's credits (main or featured)."""
    aid = str(artist_id)
    arts = list(getattr(track, "artists", None) or [])
    solo = getattr(track, "artist", None)
    if solo is not None:
        arts.append(solo)
    return any(str(getattr(a, "id", "")) == aid for a in arts)


def _is_compilation_release(album) -> bool:
    """True when a release's PRIMARY credit is a 'Various Artists' placeholder, a
    multi-artist compilation / soundtrack ('Appears on'), as opposed to a specific
    named artist on whose release the target is a featured guest ('Featured')."""
    artist = getattr(album, "artist", None)
    if artist is None:
        artists = getattr(album, "artists", None) or []
        artist = artists[0] if artists else None
    if artist is None:
        return True  # no single credited artist → treat as a compilation
    if getattr(artist, "id", None) in _VARIOUS_ARTISTS_IDS:
        return True
    return bool(_VARIOUS_ARTISTS_RE.search(getattr(artist, "name", "") or ""))


# --- Album-artist metadata cleaning (keep only the primary artist) -------------
# The album-artist METADATA tag is written from ``get_album_artists`` and ONLY
# there (download.py:1524). Folder paths use a different binding
# (``name_builder_album_artist``), so wrapping this one symbol affects the tag
# alone. The module flag starts False (upstream behavior) until the pref is
# read; the pref itself defaults on for new installs. Plex (and some other
# libraries) mis-read a multi-value album-artist field, so the option collapses
# it to just the primary artist.
_orig_get_album_artists = _tidaler_download.get_album_artists
_clean_album_artist_tag = False


def _clean_album_artists(names: list) -> list:
    """Reduce an album-artist list to only the primary (first) artist."""
    return [names[0]] if names else names


def _set_clean_album_artist(enabled: bool) -> None:
    """Toggle whether the album-artist metadata tag is collapsed to the primary."""
    global _clean_album_artist_tag
    _clean_album_artist_tag = bool(enabled)


def _album_artists_for_metadata(media):
    """Album-artist values written to the file's metadata tag (wraps the upstream
    helper; honours the opt-in 'clean_album_artist' setting)."""
    names = _orig_get_album_artists(media)
    return _clean_album_artists(names) if _clean_album_artist_tag else names


_tidaler_download.get_album_artists = _album_artists_for_metadata


_VERSION_TOKEN_RE = re.compile(r"[\[(]\s*(explicit|clean|e)\s*[\])]", re.IGNORECASE)
_QUALITY_RANK = {"hi_res_lossless": 4, "high_lossless": 3, "low_320k": 2, "low_96k": 1}


def _norm_title(title: str) -> str:
    """Title with explicit/clean markers stripped (deluxe/remaster kept)."""
    text = _VERSION_TOKEN_RE.sub("", title or "").lower()
    return re.sub(r"\s+", " ", text).strip(" -.\u2013\u2014")


_WIMP_RE = re.compile(r"\[wimpLink[^\]]*\](.*?)\[/wimpLink\]", re.IGNORECASE | re.DOTALL)


def _clean_bio(text: str) -> str:
    """Strip TIDAL's [wimpLink ...]…[/wimpLink] markup, keeping the linked text."""
    if not text:
        return ""
    cleaned = _WIMP_RE.sub(r"\1", text)
    cleaned = re.sub(r"\[/?wimpLink[^\]]*\]", "", cleaned)
    return cleaned.strip()


def _quality_rank(obj) -> int:
    try:
        return _QUALITY_RANK.get(getattr(quality_audio_highest(obj), "name", ""), 0)
    except Exception:
        return 0


def _dedup_versions(items, key_fn, mode: str, max_rank: int = 4) -> list:
    """Collapse duplicate editions of the same album/track down to one row.

    Items are grouped by ``key_fn`` (title + artist), then within each group we
    keep the single best version: the highest audio quality that does not exceed
    the user's cap (``max_rank``), falling back to the lowest available if every
    version is above the cap (so the item still appears). This is what reduces
    the dozen near-identical "same album" rows to one.

    ``mode`` controls explicit/clean handling: 'explicit' prefers the explicit
    cut, 'clean' the censored one, 'both' keeps one of each side.
    """

    def best(candidates):
        ranked = sorted(candidates, key=_quality_rank, reverse=True)
        within_cap = [c for c in ranked if _quality_rank(c) <= max_rank]
        if within_cap:
            return within_cap[0]
        return ranked[-1] if ranked else None

    groups: dict = {}
    order: list = []
    for item in items:
        key = key_fn(item)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    out = []
    for key in order:
        group = groups[key]
        best_explicit = best([i for i in group if getattr(i, "explicit", False)])
        best_clean = best([i for i in group if not getattr(i, "explicit", False)])
        if mode == "clean":
            out.append(best_clean or best_explicit)
        elif mode == "both":
            out.extend(x for x in (best_explicit, best_clean) if x is not None)
        else:  # "explicit"
            out.append(best_explicit or best_clean)
    return [x for x in out if x is not None]


# --- Album-edition collapsing (opt-in: keep only the most complete edition) ----
# Qualifiers that mark a genuinely DIFFERENT release; an edition whose qualifier
# matches one of these is never collapsed into another (it keeps its own group).
_EDITION_KEEP_RE = re.compile(
    r"remaster|remix|\bmix\b|re-?record|taylor'?s version|anniversar|special edition"
    r"|collector|\blive\b|acoustic|unplugged|instrumental|\bdemo|\bmono\b|\bstereo\b"
    r"|reissue|re-?release|karaoke|commentary",
    re.IGNORECASE,
)
# A trailing parenthetical / bracketed group. We deliberately do NOT treat a
# trailing " - …" as a qualifier, many real titles contain a dash (year ranges
# like "1967 – 1970", "Live - 1970"), and stripping it would mangle the base.
_EDITION_QUAL_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")
# Track-title qualifiers stripped so the same song matches across editions.
_TRACK_QUAL_RE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(?:feat|featuring|remaster(?:ed)?|version|mix|edit|mono|stereo|live|acoustic)"
    r"\b[^\)\]]*[\)\]]",
    re.IGNORECASE,
)


def _strip_edition_quals(title: str) -> str:
    """Peel trailing parenthetical / bracketed edition qualifiers so every edition
    variant of one album shares a base title, UNLESS a qualifier names a
    genuinely different release (remaster / anniversary / live / …), which is
    kept so it groups (and downloads) separately."""
    text, prev = title or "", None
    while text != prev:
        prev = text
        m = _EDITION_QUAL_RE.search(text)
        if not m or _EDITION_KEEP_RE.search(m.group(0)):
            break
        text = text[: m.start()]
    return re.sub(r"\s+", " ", text).strip(" -.–—")


def _edition_base_key(album):
    """Grouping key for edition collapsing: base title (edition qualifiers
    stripped, keep-markers preserved) + normalised primary-artist name."""
    artist = _primary_artist_name(album) or name_builder_album_artist(album)
    return (_strip_edition_quals(_norm_title(name_builder_title(album))), _norm_artist(artist))


def _norm_track_title(name: str) -> str:
    """Normalised track title for cross-edition matching (feat./version/remaster
    qualifiers stripped, lowercased)."""
    text = _TRACK_QUAL_RE.sub("", name or "").lower()
    return re.sub(r"\s+", " ", text).strip(" -.–—")


def _tracks_subset(small, big, tol: int = 2) -> bool:
    """True if every (title, duration) in ``small`` has a DISTINCT match in
    ``big``, same normalised title AND duration within ``tol`` seconds. Matching
    on length as well as title means a same-titled but different recording (an
    alternate take, an extended cut, a half-length radio snippet) is NOT treated
    as the same song, so it is never collapsed away. A ``None`` duration on
    either side falls back to a title-only match."""
    pool = list(big)
    for title, dur in small:
        for i, (t2, d2) in enumerate(pool):
            if title == t2 and (dur is None or d2 is None or abs(dur - d2) <= tol):
                del pool[i]
                break
        else:
            return False
    return True


def _collapse_album_editions(albums, tracks_of, quality_of, conflict: str = "keep_both") -> list:
    """Keep only the most complete edition of each album.

     Albums are grouped by ``_edition_base_key``. Within a group, an edition is
     dropped ONLY when its tracks are a strict subset of a more complete edition's
    , matched by (title, duration), so a same-titled but different-length
     recording counts as a distinct track and blocks the collapse. Everything
     else is kept ("keep both when unsure"). ``conflict`` decides the case where
     the more complete edition is a LOWER audio-quality tier than the subset it
     would absorb: 'keep_both' (drop neither), 'completeness' (keep the most
     complete), 'quality' (keep the highest quality). Input order is preserved.

     ``tracks_of`` maps album -> list[(title, duration|None)] (the caller fetches
     / caches these; an empty list means "unknown" -> keep). ``quality_of`` maps
     album -> int audio-quality rank. Both are injected so this stays pure and
     unit-testable without network or Qt.
    """
    groups: dict = {}
    order: list = []
    for a in albums:
        key = _edition_base_key(a)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(a)

    drop: set = set()
    for key in order:
        group = groups[key]
        if len(group) < 2:
            continue  # singleton album -> nothing to collapse, no track fetch
        tracks = {id(a): tracks_of(a) for a in group}
        for a in group:
            ta = tracks[id(a)]
            if not ta:
                continue  # unknown content -> keep this edition
            for b in group:
                if a is b:
                    continue
                tb = tracks[id(b)]
                if len(ta) < len(tb) and _tracks_subset(ta, tb):  # a strictly contained in b
                    if quality_of(b) < quality_of(a):  # but the complete one is lower quality
                        if conflict == "completeness":
                            drop.add(id(a))
                        elif conflict == "quality":
                            drop.add(id(b))
                        # keep_both: drop neither
                    else:
                        drop.add(id(a))
                    break
    return [a for a in albums if id(a) not in drop]


# --- Best-of-both-worlds merge: assemble one album from several editions -------
# When a higher-quality edition is a subset of a lower-quality "complete" edition,
# the merge takes each shared recording from the highest-quality edition that has
# it and the exclusive tracks from the complete edition, presenting them all under
# the complete edition's identity (title, cover, numbering). Pure + injectable so
# the plan can be unit-tested without network or Qt.
_MergeRec = namedtuple("_MergeRec", "obj title dur isrc explicit", defaults=(False,))


def _track_isrc(track) -> str | None:
    """Normalised ISRC for cross-edition matching, or None when absent."""
    value = getattr(track, "isrc", None)
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def _align_edition(template: list, other: list) -> dict:
    """Map template index -> matching ``other`` rec for the SAME recording.

    Matching is deliberately strict: a *missed* match only forgoes a quality
    upgrade, but a *wrong* match could drop a unique track or substitute the wrong
    audio, so we never guess.
      * ISRC first (the reliable cross-edition key). A differing ISRC is positive
        proof of a DIFFERENT recording and vetoes any weaker match.
      * otherwise a confident match only: identical normalised title AND a real
        duration on BOTH sides within 1 s. A missing duration never matches, so an
        unconfirmable track leaves the template short and the caller's superset
        guard keeps the editions intact instead of risking a drop.
    Explicit and clean cuts never match each other (same title/length, different
    recording), and each ``other`` rec is consumed at most once so duplicate
    titles can't double-match."""
    result: dict = {}
    used = [False] * len(other)
    by_isrc: dict = {}
    for j, rec in enumerate(other):
        if rec.isrc:
            by_isrc.setdefault(rec.isrc, []).append(j)
    for i, tr in enumerate(template):
        if not tr.isrc:
            continue
        for j in by_isrc.get(tr.isrc, ()):
            if not used[j] and other[j].explicit == tr.explicit:
                used[j] = True
                result[i] = other[j]
                break
    for i, tr in enumerate(template):
        if i in result:
            continue
        for j, rec in enumerate(other):
            if used[j] or rec.explicit != tr.explicit:
                continue
            if tr.isrc and rec.isrc and tr.isrc != rec.isrc:
                continue  # ISRC proves a different recording, never override by title/duration
            if tr.title == rec.title and tr.dur is not None and rec.dur is not None and abs(tr.dur - rec.dur) <= 1:
                used[j] = True
                result[i] = rec
                break
    return result


def _build_merge_plan(group: list, recs_of, rank_of):
    """Build a best-of-both plan for one edition group (>= 2 editions of a release).

    ``recs_of`` maps album -> list[_MergeRec]; ``rank_of`` maps album -> int audio
    rank. Returns ``(identity_album, plan)`` where ``identity_album`` is the most
    complete edition and ``plan`` is a list of ``(source_track, track_num,
    volume_num)`` over that edition's track layout, each shared track sourced from
    the highest-quality edition that carries it, exclusives from the complete
    edition. Returns ``(None, None)`` when no quality upgrade is available, **or
    when the most complete edition is not a strict superset of every other edition
    in the group**, so the caller can fall back to a lossless behaviour.

    SAFETY INVARIANT (never lose a song): the merged album is built from the
    template's track list, so any track that lives only on a *non-template* edition
    would be silently dropped. We therefore refuse to merge unless the template
    contains every track of every edition in the group, if even one edition has a
    track that doesn't align into the template, we bail and let the caller keep the
    editions intact instead. Conservative over clever."""
    recs = {id(a): recs_of(a) for a in group}
    template = max(group, key=lambda a: (len(recs[id(a)]), rank_of(a)))
    trecs = recs[id(template)]
    if not trecs:
        return None, None
    aligns = {id(a): _align_edition(trecs, recs[id(a)]) for a in group if a is not template}
    # Superset guard: bail if any edition has a track the template doesn't cover.
    for a in group:
        if a is not template and len(aligns[id(a)]) < len(recs[id(a)]):
            return None, None
    plan: list = []
    upgraded = False
    for i, tr in enumerate(trecs):
        src, best_rank = tr.obj, rank_of(template)
        for a in group:
            if a is template:
                continue
            other = aligns[id(a)].get(i)
            if other is not None and rank_of(a) > best_rank:
                src, best_rank, upgraded = other.obj, rank_of(a), True
        track_num = getattr(tr.obj, "track_num", None) or (i + 1)
        volume_num = getattr(tr.obj, "volume_num", None) or 1
        plan.append((src, track_num, volume_num))
    if not upgraded:
        return None, None
    return template, plan


def _as_member_of(track, identity_album, track_num: int, volume_num: int):
    """A shallow copy of ``track`` re-tagged as ``track_num`` of ``identity_album``.

    Tags and the output path are read from ``track.album`` / ``track.track_num`` at
    download time, so re-pointing them on a COPY makes a borrowed (higher-quality)
    track land in the target album's folder with that album's title, cover and
    totals, without ever mutating the cached original."""
    member = copy.copy(track)
    member.album = identity_album
    member.track_num = track_num
    member.volume_num = volume_num
    return member


def _artists_list(obj) -> list[dict]:
    """All credited artists as {name, id} so each can be opened individually."""
    artists = getattr(obj, "artists", None) or []
    if not artists:
        primary = getattr(obj, "artist", None)
        artists = [primary] if primary is not None else []
    out = []
    for artist in artists:
        name = getattr(artist, "name", "")
        if name:
            out.append({"name": name, "id": str(getattr(artist, "id", ""))})
    return out


class WavesBridge(QObject):
    """The single object exposed to QML as the ``waves`` context property.

    State model at a glance (each field is documented where it is created in
    ``__init__``; this is the map of how they relate):

    * ``_objs``: per-kind buckets (album/artist/track/playlist/video/mix) of
      the *live tidalapi objects* behind whatever QML is currently showing.
      QML only ever holds plain dicts and ids; when it asks to download or
      expand something, the slot looks the real object up here by id. A new
      search replaces the buckets, and each bucket is FIFO-capped at
      ``_MAX_OBJS_PER_BUCKET``, so an id can be evicted; download slots
      recover by re-fetching the object by id (``_mediaRefetched``).
    * ``_queue``: the download queue as a list of plain dicts
      ``{qid, media_id, type, title, status, prog, ...}``. Every mutation
      goes through ``_emit_queue()``, which ships a copy to QML via
      ``queueChanged`` (coalesced during batch enqueues). Per-job companions
      keyed by qid: ``_job_aborts`` (cancel one download without stopping
      the rest), ``_job_signals`` (the GUI-thread progress relay), and
      ``_job_tracks`` (per-track rows behind the queue drawer expansion).
    * Session caches: ``_lib_cache`` (My Tidal pages + scroll offsets),
      ``_browse_root_cache``/``_browse_pages`` (editorial pages), and
      ``_artist_cache`` (stale-while-revalidate artist pages). A snapshot of
      these is persisted to ``page_cache.json`` so the next launch starts
      warm; the file is account-tagged and deleted on logout.
    * Threading: slots that hit the network wrap the work in ``Worker`` and
      run it on ``threadpool`` (search/metadata) or ``dl_pool`` (downloads,
      sized by the concurrency setting), then hand results back to the GUI
      thread by emitting signals (Qt auto-queues cross-thread emissions).
      Nothing below ever touches QML state from a worker thread.
    """

    loggedInChanged = Signal()
    sessionResolvedChanged = Signal()
    statusChanged = Signal()
    busyChanged = Signal()
    loginUrlReady = Signal(str)
    searchResults = Signal("QVariant")
    albumTracksLoaded = Signal(str, "QVariantList")
    artistLoaded = Signal("QVariant")
    artistMetaLoaded = Signal(str, int)
    libraryLoaded = Signal(str, "QVariant", bool)  # category, items (replace), hasMore
    libraryMore = Signal(str, "QVariant", bool)  # category, items (append), hasMore
    # Browse (TIDAL editorial pages). browseLoaded carries the landing payload
    # {sections, genres, moods, decades, error}; browsePageLoaded carries one
    # drilled-into page {key, title, sections, error} where key is the page's
    # TIDAL api path (also the cache key QML echoes back to openBrowsePage).
    browseLoaded = Signal("QVariant")
    browsePageLoaded = Signal("QVariant")
    browseSectionMore = Signal("QVariant")
    # Cover-mosaic art for one genre/mood/decade tile: the page's api path
    # plus up to four cover URLs sampled from that page's contents. Emitted
    # progressively by a background worker after the landing loads.
    browseTileArt = Signal(str, "QVariantList")
    queueChanged = Signal("QVariantList")
    queueItemProgress = Signal(int, float)
    # Per-track view of a queued album (queue drawer row expansion):
    # queueTracksLoaded delivers the full ordered snapshot for a qid;
    # queueTrackState streams one track's lifecycle change; queueTrackPct
    # batches live percentages for the tracks currently downloading.
    queueTracksLoaded = Signal(int, "QVariantList")
    queueTrackState = Signal(int, "QVariant")
    queueTrackPct = Signal(int, "QVariantMap")
    # Internal: an album's ordered track list is fetched off the GUI thread,
    # then merged with the live per-track registry ON the GUI thread (so a
    # lifecycle event can't race the snapshot).
    _queueTracksFetched = Signal(int, "QVariantList")
    pausedChanged = Signal()
    motionBgChanged = Signal()  # motion_background pref flipped; Main.qml re-reads it
    downloadProgress = Signal(str, float)
    downloadState = Signal(str, str)
    # In-app audio preview. A preview is addressed by (kind, id) where kind is
    # "track" (id = track id) or "artist" (id = artist id, plays its top track),
    # so the same signals drive both the track-row button and the artist-artwork
    # overlay. previewState carries the resolve lifecycle; previewReady hands the
    # QML MediaPlayer a directly-streamable URL; previewMeta feeds the optional
    # 'now previewing' label.
    previewReady = Signal(str, str, str)  # kind, id, url
    previewState = Signal(str, str, str)  # kind, id, state ("loading" | "error" | "")
    # In-app video playback: {id, title, artist, url, error}. The URL is the
    # stream tidalapi resolves for the video (HLS or direct); QML's overlay
    # MediaPlayer plays it as-is (Qt Multimedia's ffmpeg backend speaks HLS).
    videoReady = Signal("QVariant")
    # kind, id, title, artist, art, artistId, albumId, trackId, artists, the ids
    # let the now-playing bar open the artist page (artist name) or the track's
    # album page with the track highlighted (track name). trackId is the actual
    # sounding track, which differs from `id` for artist/album/playlist/mix
    # previews; artists is the full [{name, id}] credit list so each collaborator
    # is individually clickable (the `artist` string stays for a plain label).
    previewMeta = Signal(str, str, str, str, str, str, str, str, "QVariant")
    # Flips when the 'best of both' edition-merge opt-in changes, so the album
    # page can show/hide its merge action.
    editionMergeChanged = Signal()
    # In-app FFmpeg manager (Settings → FFmpeg card).
    ffmpegStatusChanged = Signal()
    ffmpegProgress = Signal(float)
    ffmpegStateChanged = Signal(str, str)  # state, message
    ffmpegUpdateChecked = Signal(bool, str, str)  # available, current, latest
    appUpdateStatusChanged = Signal()
    appUpdateProgress = Signal(float)
    appUpdateStateChanged = Signal(str, str)  # state, message
    appUpdateChecked = Signal(bool, str, str)  # available, current, latest
    # Internal: marshal a *batch* of album enqueues onto the GUI thread. A
    # discography download resolves its albums on a worker thread, then emits
    # this once with the whole list so (a) every album's progress relay
    # (_ProgressSignals) gets GUI-thread affinity and per-track ticks are
    # delivered, and (b) the queue appears in a single update instead of
    # trickling in album-by-album (which read as a sudden 0 → N jump).
    _albumsQueued = Signal("QVariantList")
    # Internal: same batch marshalling for individual tracks (an artist's guest
    # appearances on other artists' releases).
    _tracksQueued = Signal("QVariantList")
    # Internal: a download was requested for an id whose live object had been
    # evicted from _objs (a new search clears every bucket). The object is
    # re-fetched by id on a worker, then this queued hop re-dispatches the
    # download slot on the GUI thread (downloads must start with GUI affinity
    # so their progress relays get GUI-thread delivery, see _albumsQueued).
    _mediaRefetched = Signal(str, str)  # bucket, media_id
    backRequested = Signal()

    def __init__(self, tidal: Tidal | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # A missing settings file means a brand-new install; Settings() writes
        # the file as a side effect, so the check has to happen first.
        from tidaler.helper.path import path_file_settings

        fresh_install = not os.path.isfile(path_file_settings())
        self.settings = Settings()
        if fresh_install:
            self._apply_first_run_defaults()
        # Dev timing/diagnostics log lands next to the app's settings file so
        # it's easy to find; see tidaler.waves_ui.devlog (WAVES_DEBUG to toggle).
        log_path = devlog.init(log_dir=os.path.dirname(self.settings.file_path))
        devlog.event("app", "WavesBridge starting", log=str(log_path or "stderr"))
        self._help = HelpSettings()
        self.tidal = tidal or Tidal(self.settings)
        # Quick metadata/UI work (search, album tracks, artist pages) runs on
        # one pool; downloads run on a separate pool so a long album download
        # can never starve the UI of threads.
        self.threadpool = QtCore.QThreadPool()
        self.dl_pool = QtCore.QThreadPool()
        self.dl_pool.setMaxThreadCount(max(2, int(self.settings.data.downloads_concurrent_max or 3)))
        # Aggregate progress for "download discography": artist_id -> {keys, done,
        # failed, prog} so the artist button shows a bar averaged over its albums.
        self._artist_groups: dict[str, dict] = {}
        self._artist_lock = Lock()
        # In-app FFmpeg manager: downloads/updates a trusted static ffmpeg into
        # the app data dir so users don't have to install one manually.
        self._ffmpeg = FfmpegManager(os.path.dirname(self.settings.file_path))
        self._ffmpeg_abort = Event()
        # In-app self-updater (dormant until its repo slug is configured).
        self._updater = AppUpdater(os.path.dirname(self.settings.file_path), _WAVES_VERSION)
        self._app_update_abort = Event()
        # Pristine (on-disk) values of the ffmpeg-dependent flags, captured before
        # any Download init can disable them in-memory when ffmpeg is absent; we
        # restore these once ffmpeg gets installed (see _restore_ffmpeg_flags).
        self._ffmpeg_flag_prefs = {
            k: bool(getattr(self.settings.data, k, False)) for k in ("video_convert_mp4", "extract_flac")
        }
        # The user's *explicit* ffmpeg override, snapshotted from disk here,
        # before login can trigger an in-memory injection into path_binary_ffmpeg
        # (the managed path via _resolve_ffmpeg, or a $PATH location via
        # Download.__init__). Both are transient and never persisted, so capturing
        # the on-disk value up front is what keeps them from being misread as a
        # user choice. Updated on save in applySettings.
        self._ffmpeg_user_path = (self.settings.data.path_binary_ffmpeg or "").strip()
        # The global run/abort gates are created ONCE and shared by every
        # Download built this session. In-flight workers park in
        # ``event_run.wait()`` and check ``event_abort`` per chunk, so these
        # objects must never be swapped out from under them, re-init (on save /
        # after installFfmpeg) reuses these instances rather than replacing them
        # (a swap orphaned paused workers on a dead event and broke cancel).
        # event_run starts "set" so downloads run; clearing it pauses them.
        self._event_abort = Event()
        self._event_run = Event()
        self._event_run.set()
        self._dl: Download | None = None
        # Temp .m4a of the current preview clip (deleted when the next resolves).
        self._preview_tmp: str | None = None
        self._logged_in = False
        # False until the startup cached-token check concludes (either way).
        # The QML login overlay is gated on this so it doesn't flash over the
        # UI while the token check is still in flight for an already-signed-in
        # user (the check is a network call on a worker thread).
        self._session_resolved = False
        self._busy = False
        self._status = "Starting…"
        # Live tidalapi objects from the last search/artist page, keyed by id,
        # so QML can expand an album, open an artist, or queue a download by id.
        self._objs: dict[str, dict[str, object]] = {
            "album": {},
            "artist": {},
            "track": {},
            "playlist": {},
            "video": {},
            "mix": {},
        }
        self._objs_max = _MAX_OBJS_PER_BUCKET
        # Accumulated library rows keyed by category, {category: {"items": [...],
        # "offset": int, "more": bool}}, so re-opening a category restores
        # everything scrolled so far instantly, and infinite scroll knows where
        # to fetch the next page from. `_lib_loading` guards against firing a
        # second page request for a category while one is already in flight.
        self._lib_cache: dict[str, dict] = {}
        self._lib_loading: set[str] = set()
        self._lib_gen = 0  # bumped per first-page load to drop stale category loads
        self._search_gen = 0  # bumped per search / open-link to drop stale results
        # Browse (TIDAL editorial pages): the landing payload plus every page
        # drilled into so far, cached for the session. `_browse_loading` de-dupes
        # in-flight loads (keys: "root" or the page's api path); `_browse_lock`
        # serializes page fetches, each runs on a private tidalapi Page because
        # the shared session.page parser mutates itself on every parse and is
        # not safe to use from concurrent workers.
        self._browse_root_cache: dict | None = None
        self._browse_pages: dict[str, dict] = {}
        self._browse_loading: set[str] = set()
        self._browse_gen = 0  # bumped on logout so in-flight loads can't cache
        self._browse_lock = Lock()
        # Artist pages, cached for the session like browse pages so revisits
        # render instantly; every visit still revalidates in the background
        # (stale-while-revalidate) so a new release shows up on return.
        self._artist_cache: dict[str, dict] = {}
        self._artist_loading: set[str] = set()
        # Disk snapshot of the page caches (browse / artist / library first
        # pages) so the next launch starts warm instead of spinner-first. The
        # file is account-tagged and deleted on logout, browse embeds
        # personalized For You rows that must not leak across accounts.
        self._page_cache_path = os.path.join(os.path.dirname(self.settings.file_path), "page_cache.json")
        self._page_cache_lock = Lock()
        # Video streaming quality. The persisted Video-quality setting is the
        # ceiling; until the user touches it (this run or a previous one is
        # indistinguishable, so: this run), the first video also gauges the
        # connection and starts lower if the pipe can't carry the ceiling.
        self._video_auto_cap: int | None = None  # measured height cap, None = not probed yet
        self._video_user_quality = False  # True once the user edits Video quality this run
        # Tile cover mosaics: api path -> up to 4 cover URLs, sampled from each
        # genre/mood/decade page by a single background worker (serialized, one
        # page at a time) and persisted with a TTL so later launches don't
        # re-crawl ~46 editorial pages. Not account-specific, kept on logout.
        self._tile_art_mem: dict[str, list[str]] = {}
        self._tile_art_running = False
        self._tile_art_path = os.path.join(os.path.dirname(self.settings.file_path), "browse_tile_art.json")
        # In-flight re-fetches of evicted download targets, keyed (bucket, id),
        # so a double-click can't spawn two network fetches for the same item.
        self._refetch_inflight: set[tuple[str, str]] = set()
        self._mediaRefetched.connect(self._on_media_refetched)
        self._queue: list[dict] = []
        self._queue_seq = 0
        self._paused = False
        # Per-job abort events keyed by queue id, so a single running download
        # can be cancelled (the global _event_abort would stop everything).
        self._job_aborts: dict[int, Event] = {}
        # Strong refs to each job's progress relay so its bound slot stays
        # connected for the whole download (dropped in _download's finally).
        self._job_signals: dict[int, _ProgressSignals] = {}
        # Per-job track registry (qid -> {track_id: row}) behind the queue
        # drawer's album expansion. Mutated only on the GUI thread (via the
        # relay's queued track_event); kept after a job ends so an expanded
        # done row still shows its tracks, pruned with the queue rows.
        self._job_tracks: dict[int, dict[str, dict]] = {}
        # Live Download objects per running job, the poll timer reads their
        # rich Progress tasks for per-track percentages (thread-safe: rich
        # guards its task list with an internal lock).
        self._job_dls: dict[int, Download] = {}
        self._track_poll = QTimer(self)
        self._track_poll.setInterval(500)
        self._track_poll.timeout.connect(self._poll_track_progress)
        self._queueTracksFetched.connect(self._merge_queue_tracks)
        # Best-of-both merge plans awaiting download, keyed by the synthetic album
        # key that downloadAlbum() will route through _download(merge_plan=…).
        self._merge_plans: dict[str, list] = {}
        # Album ids already run through (or exempt from) the automatic
        # best-of-both scan, so downloadAlbum never scans the same id twice.
        self._merge_scanned: set[str] = set()
        # When set, _emit_queue() coalesces, used while enqueueing a batch so
        # QML receives a single queueChanged for the whole discography.
        self._queue_emit_suspended = False
        # Queued connection: a discography's albums (resolved off the GUI
        # thread) are enqueued together on the GUI thread.
        self._albumsQueued.connect(self._enqueue_albums)
        self._tracksQueued.connect(self._enqueue_tracks)
        self._waves_prefs_path = os.path.join(os.path.dirname(self.settings.file_path), "waves.json")
        self._waves_prefs = self._load_waves_prefs()
        _set_clean_album_artist(self._waves_pref_bool("clean_album_artist"))
        self._try_token_login()

    def eventFilter(self, obj, event) -> bool:
        """App-level, non-consuming filter for the macOS back gesture.

        Only the discrete three-finger swipe (NativeGesture) maps to "back".
        Two-finger horizontal scrolling is deliberately NOT treated as back,
        the browse shelves scroll horizontally, and a scroll→back mapping
        hijacks them. Always returns False so scrolling is never affected.
        (NativeGesture events only ever fire on macOS.)
        """
        if not _IS_MACOS:
            return False
        try:
            if (
                event.type() == QEvent.Type.NativeGesture
                and event.gestureType() == Qt.NativeGestureType.SwipeNativeGesture
                and event.value() > 0
            ):
                self.backRequested.emit()
        except Exception:
            logger.debug("Gesture filter error", exc_info=True)
        return False

    # ----- Qt properties -------------------------------------------------

    def _get_logged_in(self) -> bool:
        return self._logged_in

    def _get_session_resolved(self) -> bool:
        return self._session_resolved

    def _get_busy(self) -> bool:
        return self._busy

    def _get_status(self) -> str:
        return self._status

    loggedIn = Property(bool, _get_logged_in, notify=loggedInChanged)
    sessionResolved = Property(bool, _get_session_resolved, notify=sessionResolvedChanged)
    busy = Property(bool, _get_busy, notify=busyChanged)
    status = Property(str, _get_status, notify=statusChanged)

    # ----- internal state helpers ---------------------------------------

    def _set_logged_in(self, value: bool) -> None:
        if value != self._logged_in:
            self._logged_in = value
            self.loggedInChanged.emit()

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busyChanged.emit()

    def _set_status(self, message: str) -> None:
        self._status = message
        self.statusChanged.emit()

    def _resolve_ffmpeg(self) -> None:
        """Point ``path_binary_ffmpeg`` at the managed binary when the user has
        no explicit override, so ``Download`` finds ffmpeg without the user
        installing one. In-memory only (never persisted): the precedence is
        explicit override → managed copy → PATH (download.py's own shutil.which).
        """
        if self.settings.data.path_binary_ffmpeg:
            return  # power-user override wins
        if self._ffmpeg.is_installed():
            self.settings.data.path_binary_ffmpeg = str(self._ffmpeg.binary_path)

    def _user_ffmpeg_path(self) -> str:
        """The user's *explicit* FFmpeg override, or "" if none.

        Reads the startup snapshot (``_ffmpeg_user_path``), NOT the live
        ``settings.data.path_binary_ffmpeg``, the latter is mutated in-memory by
        both ``_resolve_ffmpeg`` (managed path) and ``Download.__init__``
        (``shutil.which`` $PATH location) when no override is set, and neither is
        a user choice. The abspath guard additionally drops a managed path that an
        older build may have persisted. Used for the status (a genuine override is
        an unmanaged binary → yellow) and to keep the path box empty unless the
        user has linked something of their own.
        """
        p = self._ffmpeg_user_path
        if not p:
            return ""
        try:
            # normcase so a case/sep difference on Windows (paths are
            # case-insensitive there; abspath doesn't case-fold) still matches.
            def _norm(x: str) -> str:
                return os.path.normcase(os.path.abspath(x))

            if _norm(p) == _norm(str(self._ffmpeg.binary_path)):
                return ""  # the managed copy (persisted by a prior build), not a user override
        except Exception:
            logger.debug("ffmpeg path compare failed", exc_info=True)
        return p

    def _init_download(self) -> None:
        # Reuse the shared run/abort gates (created once in __init__): swapping
        # them here would strand any in-flight worker parked on the old event.
        # Re-init happens on every applySettings save and after installFfmpeg, so
        # the events MUST outlive it. Downloads run while _event_run is set.
        self._resolve_ffmpeg()
        self._dl = Download(
            tidal_obj=self.tidal,
            path_base=self.settings.data.download_base_path,
            fn_logger=logger,
            skip_existing=self.settings.data.skip_existing,
            progress=Progress(),
            event_abort=self._event_abort,
            event_run=self._event_run,
        )

    def _try_token_login(self) -> None:
        """Attempt a cached-token login OFF the GUI thread.

        ``login_token`` performs a synchronous, no-timeout network GET; running
        it in ``__init__`` on the GUI thread hung the app at launch whenever the
        network was offline or black-holed (the window couldn't even appear). We
        fan it out to the thread pool like every other blocking call, so the
        window shows immediately and flips to 'Signed in' once the token check
        returns. Emits are thread-safe (queued to the GUI)."""
        self._set_status("Signing in…")

        def work() -> None:
            try:
                ok = bool(self.tidal.login_token())
            except Exception:
                logger.exception("Cached token login failed")
                ok = False
            if ok:
                self._load_page_cache()  # before loggedIn flips: first loads hit a warm cache
                self._set_logged_in(True)
                self._set_status("Signed in")
            self._session_resolved = True
            self.sessionResolvedChanged.emit()
            if ok:
                self._init_download()
                self._prefetch_tile_art()
            else:
                self._set_status("Not signed in")

        self.threadpool.start(Worker(work))

    def _remember(self, bucket: str, key: str, obj) -> None:
        """Cache a tidalapi object for later download/navigation, FIFO-capped so
        a long browse session (which, unlike a new search, never clears the
        buckets) can't grow the cache without bound. If a very old item is acted
        on after eviction, the slot's ``.get()`` returns None and the action
        no-ops, never a crash."""
        d = self._objs[bucket]
        d[key] = obj
        if len(d) > self._objs_max:
            del d[next(iter(d))]  # evict oldest insert (dicts keep insertion order)

    # ----- result dict builders -----------------------------------------

    def _album_dict(self, album) -> dict:
        key = str(getattr(album, "id", id(album)))
        self._remember("album", key, album)
        return {
            "id": key,
            "title": name_builder_title(album),
            "artist": name_builder_album_artist(album),
            "artist_id": _artist_id(album),
            "artists": _artists_list(album),
            "art": _image(album),
            "year": _year(album),
            "date": _release_date(album),
            "tracks": _track_count(album),
            "quality": _quality_label(album),
            "popularity": _popularity(album),
            "explicit": bool(getattr(album, "explicit", False)),
        }

    def _track_dict(self, track) -> dict:
        key = str(getattr(track, "id", id(track)))
        self._remember("track", key, track)
        return {
            "id": key,
            "title": name_builder_title(track),
            "artist": name_builder_artist(track),
            "artist_id": _artist_id(track),
            "artists": _artists_list(track),
            "album": getattr(getattr(track, "album", None), "name", ""),
            "album_id": str(getattr(getattr(track, "album", None), "id", "") or ""),
            "num": int(getattr(track, "track_num", 0) or 0),
            "vol": int(getattr(track, "volume_num", 1) or 1),
            "art": _image(track, 160),
            "year": _year(track),
            "date": _release_date(track),
            "duration": _fmt_duration(getattr(track, "duration", 0)),
            "quality": _quality_label(track),
            "popularity": _popularity(track),
            "explicit": bool(getattr(track, "explicit", False)),
        }

    def _video_dict(self, video) -> dict:
        key = str(getattr(video, "id", id(video)))
        self._remember("video", key, video)
        return {
            "id": key,
            "title": name_builder_title(video),
            "artist": name_builder_artist(video),
            "artists": _artists_list(video),
            "art": _image(video, 320),
            "duration": _fmt_duration(getattr(video, "duration", 0)),
            "explicit": bool(getattr(video, "explicit", False)),
        }

    def _playlist_dict(self, playlist) -> dict:
        key = str(getattr(playlist, "id", id(playlist)))
        self._remember("playlist", key, playlist)
        creator = getattr(playlist, "creator", None)
        return {
            "id": key,
            "title": name_builder_title(playlist),
            "art": _image(playlist),
            "tracks": int(getattr(playlist, "num_tracks", 0) or 0),
            "creator": str(getattr(creator, "name", "") or "") if creator is not None else "",
        }

    def _mix_dict(self, mix) -> dict:
        key = str(getattr(mix, "id", id(mix)))
        self._remember("mix", key, mix)
        return {
            "id": key,
            "title": name_builder_title(mix),
            "art": _image(mix),
            "subtitle": str(getattr(mix, "sub_title", "") or getattr(mix, "short_subtitle", "") or ""),
        }

    def _get_artist(self, artist_id: str):
        artist = self._objs["artist"].get(artist_id)
        if artist is None:
            try:
                artist = self.tidal.session.artist(int(artist_id))
            except Exception:
                logger.exception("Could not fetch artist %s", artist_id)
                return None
            self._remember("artist", artist_id, artist)
        return artist

    # ----- auth slots ----------------------------------------------------

    def _reset_tidal_session(self) -> None:
        """Recreate the underlying tidalapi session after a sign-out.

        tidaler's ``Tidal.logout()`` deletes the session object outright (a CLI
        assumption, the process exits right after logging out). The GUI is
        long-running and lets the user sign back in, so we rebuild a clean
        session, mirroring ``Tidal.__init__``, and reapply the configured
        quality. Without this, a sign-out leaves ``self.tidal`` with no
        ``session`` and the next login, or any session call, raises
        ``AttributeError``.
        """
        import tidalapi

        self.tidal.session = tidalapi.Session(tidalapi.Config(item_limit=10000))
        self.tidal.original_client_id = self.tidal.session.config.client_id
        self.tidal.original_client_secret = self.tidal.session.config.client_secret
        self.tidal.is_atmos_session = False
        self.tidal.settings_apply()

    @Slot()
    def beginLogin(self) -> None:
        def work() -> None:
            try:
                # A prior sign-out tears the session down; rebuild it so a fresh
                # PKCE login can start.
                if getattr(self.tidal, "session", None) is None:
                    self._reset_tidal_session()
                url = self.tidal.session.pkce_login_url()
            except Exception:
                logger.exception("Could not obtain login URL")
                self._set_status("Could not start login")
                return
            self.loginUrlReady.emit(url)
            self._set_status("Finish signing in, then paste the URL back")

        self.threadpool.start(Worker(work))

    @Slot(str)
    def completeLogin(self, redirect_url: str) -> None:
        redirect_url = (redirect_url or "").strip()
        if not redirect_url:
            return
        self._set_busy(True)

        def work() -> None:
            try:
                token = self.tidal.session.pkce_get_auth_token(redirect_url)
                self.tidal.session.process_auth_token(token, is_pkce_token=True)
                ok = bool(self.tidal.login_finalize())
            except Exception:
                logger.exception("Login finalize failed")
                ok = False
            if ok:
                self._load_page_cache()
                self._set_logged_in(True)
                self._set_status("Signed in")
                self._init_download()
                self._prefetch_tile_art()
            else:
                self._set_status("Sign-in failed. Try again.")
            self._set_busy(False)

        self.threadpool.start(Worker(work))

    @Slot()
    def logout(self) -> None:
        try:
            self.tidal.logout()
            # tidaler's logout() deletes the session object; restore a fresh one
            # so the user can sign back in without restarting the app.
            self._reset_tidal_session()
        except Exception:
            logger.exception("Logout failed")
        # Drop the cached library and browse pages so a different account
        # doesn't see stale (or the previous user's personalized) rows, and bump
        # the load generations so an in-flight pre-logout page can't re-poison
        # the freshly-cleared caches for the next account.
        self._lib_cache.clear()
        self._lib_loading.clear()
        self._lib_gen += 1
        self._browse_root_cache = None
        self._browse_pages.clear()
        self._browse_loading.clear()
        self._browse_gen += 1
        self._artist_cache.clear()
        self._artist_loading.clear()
        # The disk snapshot holds the old account's personalized pages, drop it.
        with contextlib.suppress(OSError):
            os.remove(self._page_cache_path)
        self._set_logged_in(False)
        self._set_status("Signed out")

    # ----- page-cache persistence ----------------------------------------

    _ARTIST_CACHE_MAX = 60  # ~30-80 KB each, worst case a few MB on disk

    def _cache_user_id(self) -> str:
        try:
            return str(getattr(self.tidal.session.user, "id", "") or "")
        except Exception:
            return ""

    def _save_page_cache(self) -> None:
        """Snapshot the in-memory page caches to disk (atomic replace).

        Called from worker threads right after a cache write; the payloads are
        plain JSON-safe dicts by construction (they cross the QML bridge).
        Library categories persist only their first page, the accumulated
        infinite-scroll tail can be huge and re-pages naturally."""
        if not self._logged_in:
            return
        lib = {
            cat: {
                "items": e["items"][:_LIBRARY_PAGE],
                "offset": _LIBRARY_PAGE,
                "more": e["more"] or len(e["items"]) > _LIBRARY_PAGE,
            }
            for cat, e in self._lib_cache.items()
        }
        data = {
            "version": 1,
            "user": self._cache_user_id(),
            "browse_root": self._browse_root_cache,
            "browse_pages": self._browse_pages,
            "artists": self._artist_cache,
            "library": lib,
        }
        try:
            with self._page_cache_lock:
                tmp = self._page_cache_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
                os.replace(tmp, self._page_cache_path)
        except Exception:
            logger.debug("page cache save failed", exc_info=True)

    def _load_page_cache(self) -> None:
        """Warm the page caches from the last session's snapshot.

        Runs in the login worker BEFORE loggedIn flips true, so the very first
        loadBrowse/loadArtist/loadLibrary of the launch hits a warm cache and
        paints instantly (each then revalidates in the background). A snapshot
        written by a different account is discarded."""
        try:
            with self._page_cache_lock, open(self._page_cache_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return
        except Exception:
            logger.debug("page cache load failed", exc_info=True)
            return
        if not isinstance(data, dict) or data.get("version") != 1:
            return
        if str(data.get("user", "")) != self._cache_user_id():
            return
        # Populate only what's still empty, never clobber fresher live data.
        if self._browse_root_cache is None and isinstance(data.get("browse_root"), dict):
            self._browse_root_cache = data["browse_root"]
        for key, page in (data.get("browse_pages") or {}).items():
            if isinstance(page, dict):
                self._browse_pages.setdefault(str(key), page)
        for key, page in (data.get("artists") or {}).items():
            if isinstance(page, dict):
                self._artist_cache.setdefault(str(key), page)
        for cat, entry in (data.get("library") or {}).items():
            if isinstance(entry, dict) and isinstance(entry.get("items"), list):
                self._lib_cache.setdefault(str(cat), entry)
        devlog.event("cache", "page cache restored", pages=len(self._browse_pages), artists=len(self._artist_cache))

    def _remember_artist_page(self, artist_id: str, payload: dict) -> None:
        d = self._artist_cache
        d[artist_id] = payload
        while len(d) > self._ARTIST_CACHE_MAX:
            del d[next(iter(d))]  # evict oldest insert

    # ----- search --------------------------------------------------------

    def _open_url(self, url: str) -> None:
        """Resolve a pasted TIDAL share URL into a single result."""
        self._search_gen += 1
        gen = self._search_gen
        self._set_busy(True)
        self._set_status("Opening link…")
        for bucket in self._objs.values():
            bucket.clear()

        def work() -> None:
            try:
                media_type = get_tidal_media_type(url)
                media_id = get_tidal_media_id(url)
                media = instantiate_media(self.tidal.session, media_type, media_id)
            except Exception:
                logger.exception("Could not open link")
                if gen == self._search_gen:
                    self._set_status("Could not open that link")
                    self._set_busy(False)
                return
            if gen != self._search_gen:
                return  # a newer search/link superseded this one
            payload = {"artists": [], "albums": [], "tracks": [], "videos": [], "playlists": [], "mixes": []}
            if media_type == MediaType.ALBUM:
                payload["albums"] = [self._album_dict(media)]
            elif media_type == MediaType.TRACK:
                payload["tracks"] = [self._track_dict(media)]
            elif media_type == MediaType.VIDEO:
                payload["videos"] = [self._video_dict(media)]
            elif media_type == MediaType.PLAYLIST:
                payload["playlists"] = [self._playlist_dict(media)]
            elif media_type == MediaType.MIX:
                payload["mixes"] = [self._mix_dict(media)]
            elif media_type == MediaType.ARTIST:
                key = str(getattr(media, "id", id(media)))
                self._remember("artist", key, media)
                payload["artists"] = [
                    {
                        "id": key,
                        "name": getattr(media, "name", ""),
                        "art": _image(media, 320),
                        "roles": _artist_roles(media),
                        "popularity": -1,
                    }
                ]
            if gen != self._search_gen:
                return  # superseded while building the payload
            self.searchResults.emit(payload)
            self._set_status("Opened link")
            self._set_busy(False)

        self.threadpool.start(Worker(work))

    @Slot(str)
    def search(self, needle: str) -> None:
        needle = (needle or "").strip()
        if not needle:
            return
        if not self._logged_in:
            self._set_status("Sign in to search")
            return
        if "tidal.com" in needle or needle.startswith("http"):
            self._open_url(needle)
            return
        # Bump the search generation so a slower earlier search can't overwrite a
        # newer one's results (or re-fire its busy/status) once it finally returns.
        self._search_gen += 1
        gen = self._search_gen
        devlog.event("search", f"begin needle={needle}")
        self._set_busy(True)
        self._set_status(f"Searching “{needle}”…")
        for bucket in self._objs.values():
            bucket.clear()

        def work() -> None:
            t0 = devlog.clock()
            try:
                results = search_results_all(self.tidal.session, needle)
            except Exception:
                logger.exception("Search failed")
                results = {}
            api = devlog.clock() - t0
            if gen != self._search_gen:
                return  # a newer search superseded this one; drop its results

            artists = []
            artist_objs = []
            for artist in (results.get("artists") or [])[:12]:
                key = str(getattr(artist, "id", id(artist)))
                self._remember("artist", key, artist)
                artist_objs.append((key, artist))
                artists.append(
                    {
                        "id": key,
                        "name": getattr(artist, "name", ""),
                        "art": _image(artist, 320),
                        "roles": _artist_roles(artist),
                        "popularity": -1,  # enriched in the background below
                    }
                )

            albums = [self._album_dict(a) for a in self._dedup_albums((results.get("albums") or [])[:60])[:40]]
            tracks = [self._track_dict(t) for t in self._dedup_tracks((results.get("tracks") or [])[:80])[:60]]

            videos = [self._video_dict(v) for v in (results.get("videos") or [])[:30]]
            playlists = [self._playlist_dict(p) for p in (results.get("playlists") or [])[:20]]
            mixes = [self._mix_dict(m) for m in (results.get("mixes") or [])[:20]]

            if gen != self._search_gen:
                return  # superseded while building the payload
            self.searchResults.emit(
                {
                    "artists": artists,
                    "albums": albums,
                    "tracks": tracks,
                    "videos": videos,
                    "playlists": playlists,
                    "mixes": mixes,
                }
            )
            total = len(artists) + len(albums) + len(tracks) + len(videos) + len(playlists) + len(mixes)
            self._set_status(f"{total} results")
            self._set_busy(False)
            elapsed = devlog.clock() - t0
            devlog.done(
                "search",
                f"needle={needle}",
                elapsed,
                api=devlog.fmt_dur(api),
                proc=devlog.fmt_dur(elapsed - api),
                n=total,
                artists=len(artists),
                albums=len(albums),
                tracks=len(tracks),
            )

            # Enrich artist cards with popularity after results are on screen,
            # so the search itself stays fast. Each artist needs its own HTTP
            # request, so fan them out (bounded) rather than walking the list
            # serially, the badges then fill near-together instead of one slow
            # round-trip at a time. Emits are thread-safe (queued to the GUI).
            def _enrich(item) -> None:
                key, artist = item
                pop = _artist_popularity(artist)
                if pop >= 0 and gen == self._search_gen:
                    self.artistMetaLoaded.emit(key, pop)

            if artist_objs and gen == self._search_gen:
                with ThreadPoolExecutor(max_workers=min(6, len(artist_objs))) as pool:
                    list(pool.map(_enrich, artist_objs))

        self.threadpool.start(Worker(work))

    @Slot(str)
    def loadAlbumTracks(self, album_id: str) -> None:
        album = self._objs["album"].get(album_id)
        if album is None:
            return

        def work() -> None:
            t0 = devlog.clock()
            try:
                items = album.tracks()
            except Exception:
                logger.exception("Could not load album tracks")
                items = []
            out = []
            for i, track in enumerate(items, start=1):
                key = str(getattr(track, "id", id(track)))
                self._remember("track", key, track)
                out.append(
                    {
                        "id": key,
                        "num": i,
                        "title": name_builder_title(track),
                        "duration": _fmt_duration(getattr(track, "duration", 0)),
                        "popularity": _popularity(track),
                        "explicit": bool(getattr(track, "explicit", False)),
                    }
                )
            self.albumTracksLoaded.emit(album_id, out)
            devlog.done("album", f"tracks id={album_id}", devlog.clock() - t0, n=len(out))

        self.threadpool.start(Worker(work))

    @Slot(str)
    def loadArtist(self, artist_id: str) -> None:
        """Build a rich artist page: bio, albums, EPs/singles, top tracks.

        Stale-while-revalidate: a cached page (session or restored from disk)
        is emitted immediately so navigation is instant, then the page is
        re-fetched in the background and re-emitted, flagged ``refresh`` so
        the QML updates it in place, only if something actually changed
        (e.g. a new album released since the page was cached)."""
        artist_id = str(artist_id or "")
        cached = self._artist_cache.get(artist_id)
        if cached is not None:
            self.artistLoaded.emit(cached)
            self._set_status(cached.get("name") or "Artist")
        if not artist_id or artist_id in self._artist_loading:
            return
        self._artist_loading.add(artist_id)
        refresh = cached is not None
        gen = self._browse_gen  # account generation, bumped on logout
        devlog.event("artist", f"begin id={artist_id}" + (" (revalidate)" if refresh else ""))
        if not refresh:
            self._set_busy(True)
            self._set_status("Loading artist…")

        def work() -> None:
            t0 = devlog.clock()
            artist = self._get_artist(artist_id)
            if artist is None:
                self._artist_loading.discard(artist_id)
                if not refresh:
                    self._set_status("Could not load artist")
                    self._set_busy(False)
                return
            try:
                bio = _clean_bio(artist.get_bio() or "")
            except Exception:
                bio = ""
            try:
                albums = artist.get_albums()
            except Exception:
                logger.exception("artist albums failed")
                albums = []
            try:
                eps = artist.get_ep_singles()
            except Exception:
                eps = []
            try:
                tops = artist.get_top_tracks(limit=10)
            except Exception:
                tops = []

            payload = {
                "id": artist_id,
                "name": getattr(artist, "name", ""),
                "art": _image(artist, 480),
                "bio": bio,
                # Collapse duplicate editions and apply the Settings quality cap,
                # exactly as the search path does, otherwise an artist's page
                # lists every regional/quality edition of the same release.
                "albums": [self._album_dict(a) for a in self._dedup_albums(albums)],
                "eps": [self._album_dict(a) for a in self._dedup_albums(eps)],
                "tracks": [self._track_dict(t) for t in self._dedup_tracks(tops)],
            }
            self._artist_loading.discard(artist_id)
            if gen != self._browse_gen:
                return  # logged out mid-fetch, see loadBrowse's work()
            changed = payload != cached
            # A page whose every section came back empty is more likely a
            # transient fetch failure than a real artist with no catalogue,
            # show it (first load) but never cache it or overwrite good data.
            if changed and (payload["albums"] or payload["eps"] or payload["tracks"]):
                self._remember_artist_page(artist_id, payload)
                self._save_page_cache()
            elif refresh:
                return
            if refresh:
                if changed:
                    # In-place update: the QML drops this if the user has
                    # since navigated away (see onArtistLoaded).
                    self.artistLoaded.emit({**payload, "refresh": True})
            else:
                self.artistLoaded.emit(payload)
                self._set_status(getattr(artist, "name", "Artist"))
                self._set_busy(False)
            devlog.done(
                "artist",
                f"id={artist_id}",
                devlog.clock() - t0,
                albums=len(payload["albums"]),
                eps=len(payload["eps"]),
                tracks=len(payload["tracks"]),
            )

        self.threadpool.start(Worker(work))

    def _fav_artist_dict(self, artist) -> dict:
        key = str(getattr(artist, "id", id(artist)))
        self._remember("artist", key, artist)
        return {
            "id": key,
            "name": getattr(artist, "name", ""),
            "art": _image(artist, 320),
            "roles": _artist_roles(artist),
            "popularity": -1,
        }

    def _library_page(self, category: str, offset: int, limit: int) -> tuple[list, bool]:
        """Build one page of a library category for the API window
        ``[offset, offset+limit)``. Returns the rows and whether more items
        exist beyond this window.

        Important tidalapi quirks handled here:
        - ``offset`` indexes the *unfiltered* favourites list and must advance by
          the requested ``limit`` each page (the windows are disjoint).
        - A ``limit``-N request can return *fewer* than N rows because tidalapi
          drops unavailable items within the window, so "more" must be derived
          from the total ``get_*_count``, not the returned length.
        Playlists and mixes come back as one (small) list, paged locally."""
        session = self.tidal.session
        if category in ("playlists", "mixes"):
            if category == "playlists":
                full = [p for p in user_media_lists(session).get("playlists", []) if hasattr(p, "num_tracks")]
                builder = self._playlist_dict
            else:
                full = user_media_lists(session).get("mixes", [])
                builder = self._mix_dict
            page = full[offset : offset + limit]
            return [builder(m) for m in page], offset + limit < len(full)
        # (favourites method, total-count method, row builder)
        specs = {
            "tracks": ("tracks", "get_tracks_count", self._track_dict),
            "albums": ("albums", "get_albums_count", self._album_dict),
            "artists": ("artists", "get_artists_count", self._fav_artist_dict),
            "videos": ("videos", "get_videos_count", self._video_dict),
        }
        spec = specs.get(category)
        if spec is None:
            return [], False
        method_name, count_name, builder = spec
        favorites = session.user.favorites
        try:
            raw = getattr(favorites, method_name)(limit=limit, offset=offset) or []
        except TypeError:
            # Older tidalapi without limit/offset kwargs: fetch-all + slice.
            raw = (getattr(favorites, method_name)() or [])[offset : offset + limit]
        try:
            more = offset + limit < int(getattr(favorites, count_name)())
        except Exception:
            # No count available: keep paging until a window comes back empty.
            more = len(raw) > 0
        return [builder(o) for o in raw], more

    def _lib_status(self, category: str, count: int, more: bool) -> str:
        return f"{count}{'+' if more else ''} {category}"

    @Slot(str)
    def loadLibrary(self, category: str) -> None:
        """Load the first page of a library category (or restore everything
        already loaded this session from cache). Subsequent pages come from
        :meth:`loadMoreLibrary` as the user scrolls."""
        if not self._logged_in:
            self._set_status("Sign in to view your library")
            return

        # Bump the load generation so a slower in-flight first-page load for a
        # category the user has since switched away from can't publish stale
        # rows or clear the busy state out from under the newly-chosen category.
        self._lib_gen += 1
        gen = self._lib_gen

        cached = self._lib_cache.get(category)
        if cached is not None:
            self._set_busy(False)
            devlog.event("library", f"{category} from cache", n=len(cached["items"]))
            self.libraryLoaded.emit(category, cached["items"], cached["more"])
            self._set_status(self._lib_status(category, len(cached["items"]), cached["more"]))
            # Stale-while-revalidate, but only while the user is still on the
            # first page: re-emitting a fresh first page after infinite scroll
            # has appended more would truncate the list out from under them.
            if cached["offset"] != _LIBRARY_PAGE or category in self._lib_loading:
                return
            revalidate = True
        else:
            revalidate = False
            self._set_busy(True)
            self._set_status("Loading library…")

        def work() -> None:
            t0 = devlog.clock()
            try:
                items, more = self._library_page(category, 0, _LIBRARY_PAGE)
            except Exception:
                logger.exception("Could not load library category %s", category)
                if revalidate:
                    self._lib_loading.discard(category)
                    return  # keep showing the cached page, never repaint with an error
                items, more = [], False
            # Guard the cache write with the generation too: a load that started
            # before a logout (which clears the cache and bumps _lib_gen) must not
            # re-populate the cache for the next account. offset stores the *next*
            # API window to fetch (advances by the page size, since the window is
            # unfiltered-indexed, see _library_page).
            if revalidate:
                self._lib_loading.discard(category)
                entry = self._lib_cache.get(category)
                if (
                    gen == self._lib_gen
                    and entry is not None
                    and entry["offset"] == _LIBRARY_PAGE
                    and (items != entry["items"] or more != entry["more"])
                ):
                    self._lib_cache[category] = {"items": items, "offset": _LIBRARY_PAGE, "more": more}
                    self._save_page_cache()
                    self.libraryLoaded.emit(category, items, more)
                    self._set_status(self._lib_status(category, len(items), more))
                devlog.done("library", f"{category} revalidate", devlog.clock() - t0, n=len(items))
                return
            if gen == self._lib_gen:
                self._lib_cache[category] = {"items": items, "offset": _LIBRARY_PAGE, "more": more}
                self._save_page_cache()
                self.libraryLoaded.emit(category, items, more)
                self._set_status(self._lib_status(category, len(items), more))
                self._set_busy(False)
            devlog.done("library", category, devlog.clock() - t0, n=len(items), more=more)

        if revalidate:
            # Blocks loadMoreLibrary for the category while the first-page
            # revalidation is in flight (appending to a list that's about to
            # be replaced would interleave two windows).
            self._lib_loading.add(category)
        self.threadpool.start(Worker(work))

    @Slot(str)
    def loadMoreLibrary(self, category: str) -> None:
        """Fetch and append the next page of a category for infinite scroll."""
        if not self._logged_in:
            return
        cached = self._lib_cache.get(category)
        if cached is None or not cached["more"] or category in self._lib_loading:
            return
        self._lib_loading.add(category)
        offset = cached["offset"]

        def work() -> None:
            t0 = devlog.clock()
            failed = False
            try:
                items, more = self._library_page(category, offset, _LIBRARY_PAGE)
            except Exception:
                # A transient fetch error must NOT mark the category exhausted,
                # that would permanently kill infinite scroll for it after one
                # blip. Leave 'more' truthy and don't advance the offset, so the
                # next scroll retries the same window.
                logger.exception("Could not load more of library category %s", category)
                items, more, failed = [], True, True
            entry = self._lib_cache.get(category)
            if entry is not None:
                entry["items"].extend(items)
                if not failed:
                    entry["offset"] = offset + _LIBRARY_PAGE
                entry["more"] = more
                shown = len(entry["items"])
            else:
                shown = len(items)
            self._lib_loading.discard(category)
            self.libraryMore.emit(category, items, more)
            self._set_status(self._lib_status(category, shown, more))
            devlog.done("library", f"{category} page@{offset}", devlog.clock() - t0, n=len(items), more=more)

        self.threadpool.start(Worker(work))

    # ----- browse (TIDAL editorial pages) --------------------------------

    def _browse_card(self, obj) -> dict | None:
        """Normalize one page item into a flat card dict: ``kind`` plus the
        same keys the search sections already use, built through the existing
        ``_*_dict`` helpers so the live object is remembered and the existing
        download slots resolve its id. Returns None for kinds Browse doesn't
        show (videos, promo banners, unmodelled entries) and for MixV2,
        tidaler's ``Download.items()`` silently rejects MixV2, so surfacing it
        would produce a dead download button."""
        if isinstance(obj, Album):
            return {"kind": "album", **self._album_dict(obj)}
        if isinstance(obj, Artist):
            card = self._fav_artist_dict(obj)
            return {"kind": "artist", "title": card["name"], **card}
        if isinstance(obj, Playlist):  # covers UserPlaylist
            return {"kind": "playlist", **self._playlist_dict(obj)}
        if isinstance(obj, Mix):
            return {"kind": "mix", **self._mix_dict(obj)}
        if isinstance(obj, Video):
            return None
        if isinstance(obj, Track):
            return {"kind": "track", **self._track_dict(obj)}
        return None

    def _page_rows(self, page) -> list[dict]:
        """Flatten a tidalapi Page into renderable rows. Card/track rows carry
        normalized item dicts; link rows carry {title, path} chips that drill
        into another page. Categories the UI doesn't render (text blocks,
        promo banners, unmodelled lists) are dropped, as is any category whose
        items all normalize away. One broken category never kills the page."""
        rows: list[dict] = []
        for cat in list(getattr(page, "categories", None) or []):
            try:
                # TIDAL Magazine is editorial articles: no downloadable music
                # and no page Waves can render (it drills into a blank). Drop it
                # wherever it appears, as a content row or a link tile.
                if "magazine" in str(getattr(cat, "title", "") or "").lower():
                    continue
                if isinstance(cat, tidal_page.PageLinks):
                    links = [
                        {"title": str(link.title or ""), "path": str(link.api_path or "")}
                        for link in cat.items or []
                        if getattr(link, "api_path", None) and str(link.title or "").strip()
                    ]
                    if links:
                        rows.append({"rowKind": "links", "title": str(cat.title or ""), "items": links})
                    continue
                items = getattr(cat, "items", None)
                # Not a plain list => TextBlock text, a bare MIX_HEADER Mix
                # (whose .items is a method), headers, nothing to render.
                if not isinstance(items, list):
                    continue
                cards = [c for c in (self._browse_card(o) for o in items if o is not None) if c is not None]
                if not cards:
                    continue
                kind = "tracks" if all(c["kind"] == "track" for c in cards) else "cards"
                title = str(getattr(cat, "title", "") or "")
                # TIDAL's own "show more" / "view all" path for this row, when
                # it has one, the headline drills into the full listing.
                more = str(getattr(getattr(cat, "_more", None), "api_path", "") or "")
                prev = rows[-1] if rows else None
                if prev is not None and prev["rowKind"] == kind and prev["title"] == title:
                    # The For You page splits "Custom mixes" into two rows.
                    prev["items"].extend(cards)
                    if not prev.get("more"):
                        prev["more"] = more
                else:
                    row_dict = {"rowKind": kind, "title": title, "items": cards, "more": more}
                    # Endless scroll: rows whose TIDAL paged list holds more
                    # than the first window carry their paging handle. The
                    # offset counts RAW module items (some normalize away), so
                    # later fetches resume exactly where TIDAL's window ended.
                    pl = getattr(cat, "_waves_pl", None) or {}
                    if pl.get("data") and pl.get("total", 0) > pl.get("n", 0):
                        row_dict.update(
                            {"data": pl["data"], "total": pl["total"], "offset": pl["n"], "modType": pl["modType"]}
                        )
                    rows.append(row_dict)
            except Exception:
                logger.exception("Skipped a browse category")
        return rows

    def _browse_fetch(self, title: str, api_path: str):
        """Fetch one TIDAL editorial page on a private Page instance (the
        shared ``session.page`` parser mutates itself on every parse and is
        not safe under concurrent workers); the lock serializes our fetches.

        Row parsing is a tolerant re-do of tidalapi's ``Page.parse``: upstream
        raises NotImplementedError on the first module type it doesn't know,
        so one new TIDAL module would otherwise turn the whole page (and on
        Explore, the whole Browse landing) into an error state. Here an
        unparseable row is dropped and logged; the rest of the page lives."""
        with self._browse_lock:
            page = tidal_page.Page(self.tidal.session, title)
            json_obj = page.request.request("GET", api_path, params={"deviceType": "BROWSER"}).json()
            if "rows" not in json_obj:
                # V2 home-feed shape, Browse never requests it, but degrade
                # to the stock parser rather than misreading the payload.
                return page.parse(json_obj)
            page.title = str(json_obj.get("title") or "") or title
            categories = []
            for row in json_obj.get("rows") or []:
                try:
                    modules = row.get("modules") or []
                    if modules:
                        cat = page.page_category.parse(modules[0])
                        # Stash the module's raw paging handle on the parsed
                        # category: dataApiPath + totals let a row load further
                        # pages later (endless scroll), tidalapi's own objects
                        # drop this information.
                        pl = modules[0].get("pagedList") or {}
                        cat._waves_pl = {
                            "data": str(pl.get("dataApiPath") or ""),
                            "total": int(pl.get("totalNumberOfItems") or 0),
                            "n": len(pl.get("items") or []),
                            "modType": str(modules[0].get("type") or ""),
                        }
                        categories.append(cat)
                except Exception:
                    logger.debug("Skipped an unparseable browse module", exc_info=True)
            page.categories = categories
            return page

    @staticmethod
    def _chips_from_explore(explore) -> tuple[dict, dict]:
        """Split the Explore page's PageLinks rows into the Genres / Moods /
        Decades chip sets plus the untitled tail row's quick links (New / Top
        / Videos / HiRes). Shared by the landing build and the tile-art
        prefetch (which needs only the chip paths)."""
        chips: dict[str, list] = {"genres": [], "moods": [], "decades": []}
        quick: dict[str, str] = {}
        for cat in list(explore.categories or []):
            if not isinstance(cat, tidal_page.PageLinks):
                continue
            title = str(getattr(cat, "title", "") or "").strip().lower()
            links = [
                {"title": str(link.title or ""), "path": str(link.api_path or "")}
                for link in cat.items or []
                if getattr(link, "api_path", None) and str(link.title or "").strip()
            ]
            if title == "genres":
                chips["genres"] = links
            elif title.startswith("moods"):
                chips["moods"] = links
            elif title == "decades":
                chips["decades"] = links
            else:
                quick.update({link["title"]: link["path"] for link in links})
        return chips, quick

    def _browse_root(self) -> dict:
        """Assemble the Browse landing payload: the Genres / Moods / Decades
        chip sets from the Explore page, the New and Top editorial pages
        inlined as content rows, then the personalized For You rows. (The V2
        home feed is deliberately not used: its mixes come back as MixV2,
        which tidaler can't download, For You carries the same personalized
        rows as real Mix objects.)"""
        explore = self._browse_fetch("Explore", "pages/explore")
        chips, quick = self._chips_from_explore(explore)
        sections: list[dict] = []
        for name in ("New", "Top"):
            path = quick.get(name)
            if not path:
                continue
            try:
                sections.extend(self._page_rows(self._browse_fetch(name, path)))
            except Exception:
                logger.exception("Browse: could not inline the %s page", name)
        try:
            sections.extend(self._page_rows(self._browse_fetch("For You", "pages/for_you")))
        except Exception:
            logger.exception("Browse: could not load the For You page")
        # A links row inside the landing sections would duplicate the chip sets.
        sections = [r for r in sections if r["rowKind"] != "links"]
        return {"sections": sections, **chips, "error": False}

    @Slot()
    def loadBrowse(self) -> None:
        """Load the Browse landing page, or restore it from the session cache."""
        if not self._logged_in:
            self._set_status("Sign in to browse")
            return
        cached = self._browse_root_cache
        if cached is not None:
            self.browseLoaded.emit(cached)
            self._start_tile_art(cached, self._browse_gen)
        if "root" in self._browse_loading:
            return
        self._browse_loading.add("root")
        revalidate = cached is not None
        gen = self._browse_gen
        if not revalidate:
            self._set_busy(True)
            self._set_status("Loading browse…")

        def work() -> None:
            t0 = devlog.clock()
            try:
                payload = self._browse_root()
            except Exception:
                logger.exception("Could not load the browse page")
                payload = {"sections": [], "genres": [], "moods": [], "decades": [], "error": True}
            if gen != self._browse_gen:
                # Logged out (maybe back in as someone else) while this load
                # was in flight: the payload belongs to the previous account.
                # Drop it entirely, emitting would repaint the old account's
                # personalized rows, and touching busy/status/_browse_loading
                # would stomp the replacement load started after re-login.
                return
            self._browse_loading.discard("root")
            if revalidate:
                # Silent background refresh of a cached landing: re-emit (and
                # re-persist) only if the editorial content actually changed;
                # never repaint over good data with an error/empty payload.
                if not payload["error"] and payload["sections"] and payload != cached:
                    self._browse_root_cache = payload
                    self._save_page_cache()
                    self.browseLoaded.emit(payload)
                    self._start_tile_art(payload, gen)
                devlog.done("browse", "root revalidate", devlog.clock() - t0, n=len(payload["sections"]))
                return
            if not payload["error"] and payload["sections"]:
                # An all-empty landing (Explore ok, every content page failed)
                # is shown but NOT cached, so the next visit retries instead of
                # pinning a chips-only page for the rest of the session.
                self._browse_root_cache = payload
                self._save_page_cache()
            self.browseLoaded.emit(payload)
            self._set_status(
                f"Browse · {len(payload['sections'])} sections" if not payload["error"] else "Browse failed to load"
            )
            self._set_busy(False)
            devlog.done("browse", "root", devlog.clock() - t0, n=len(payload["sections"]))
            if not payload["error"]:
                self._start_tile_art(payload, gen)

        self.threadpool.start(Worker(work))

    @Slot(str, str, int, str, str)
    def loadBrowseSectionMore(self, page_key: str, data_path: str, offset: int, mod_type: str, title: str) -> None:
        """Endless scroll: fetch the next window of one browse row's paged list
        (``pages/data/<id>``, needs the ``locale`` param or TIDAL 400s) and
        emit the new cards. Every cached copy of the row (landing + drilled
        pages share data paths) is extended too, so revisits keep the growth."""
        data_path = str(data_path or "")
        if not self._logged_in or not data_path.startswith("pages/data/"):
            return
        load_key = "more:" + data_path
        if load_key in self._browse_loading:
            return
        self._browse_loading.add(load_key)
        gen = self._browse_gen

        def work() -> None:
            t0 = devlog.clock()
            payload = {"key": page_key, "data": data_path, "items": [], "offset": offset, "more": False, "error": True}
            try:
                with self._browse_lock:
                    page = tidal_page.Page(self.tidal.session, title)
                    j = page.request.request(
                        "GET",
                        data_path,
                        params={"deviceType": "BROWSER", "locale": "en_US", "offset": offset, "limit": 50},
                    ).json()
                    raw = j.get("items") or []
                    cat = page.page_category.parse({"type": mod_type, "title": title, "pagedList": {"items": raw}})
                cards = [c for c in (self._browse_card(o) for o in cat.items or [] if o is not None) if c is not None]
                total = int(j.get("totalNumberOfItems") or 0)
                new_off = offset + len(raw)
                payload = {
                    "key": page_key,
                    "data": data_path,
                    "items": cards,
                    "reqOffset": offset,
                    "offset": new_off,
                    "more": bool(raw) and new_off < total,
                    "error": False,
                }
            except Exception:
                logger.exception("Could not grow browse row %s", data_path)
            if gen != self._browse_gen:
                return  # cross-account stale load, drop silently (see loadBrowse)
            if not payload["error"]:
                self._browse_grow_cached(data_path, offset, payload["items"], payload["offset"], payload["more"])
            self._browse_loading.discard(load_key)
            self.browseSectionMore.emit(payload)
            devlog.done("browse", load_key, devlog.clock() - t0, n=len(payload["items"]))

        self.threadpool.start(Worker(work))

    def _browse_grow_cached(self, data_path: str, req_offset: int, cards: list, new_offset: int, more: bool) -> None:
        """Extend every cached row that pages through ``data_path`` AND sits at
        the offset this fetch resumed from. The landing shelf and its drilled
        'show more' page share a data path but hold different windows (e.g. 12
        vs 50 items), extending a row at a different offset would leave a gap
        in its listing, so those are left alone."""
        caches = [self._browse_root_cache, *self._browse_pages.values()]
        for payload in caches:
            for row in (payload or {}).get("sections") or []:
                if row.get("data") == data_path and row.get("offset") == req_offset:
                    row["items"] = list(row["items"]) + cards
                    row["offset"] = new_offset
                    if not more:
                        row["total"] = new_offset  # exhausted: QML stops asking

    @Slot(str, str)
    def openBrowsePage(self, api_path: str, title: str) -> None:
        """Drill into one editorial page (a genre / mood / decade chip)."""
        api_path = str(api_path or "")
        title = str(title or "")
        if not self._logged_in or not api_path.startswith("pages/"):
            return
        cached = self._browse_pages.get(api_path)
        if cached is not None:
            self.browsePageLoaded.emit(cached)
        if api_path in self._browse_loading:
            return
        self._browse_loading.add(api_path)
        revalidate = cached is not None
        gen = self._browse_gen
        if not revalidate:
            self._set_busy(True)
            self._set_status(f"Loading {title}…" if title else "Loading…")

        def work() -> None:
            t0 = devlog.clock()
            try:
                page = self._browse_fetch(title, api_path)
                payload = {
                    "key": api_path,
                    "title": str(getattr(page, "title", "") or "") or title,
                    "sections": self._page_rows(page),
                    "error": False,
                }
            except Exception:
                logger.exception("Could not load browse page %s", api_path)
                payload = {"key": api_path, "title": title, "sections": [], "error": True}
            if gen != self._browse_gen:
                # Stale cross-account load, see loadBrowse's work() for why
                # this returns without emitting or touching shared state.
                return
            self._browse_loading.discard(api_path)
            if revalidate:
                # Silent refresh of a cached page: re-emit only on real change
                # (the QML's key guard drops it if the user already left).
                if not payload["error"] and payload["sections"] and payload != cached:
                    self._browse_pages[api_path] = payload
                    self._save_page_cache()
                    self.browsePageLoaded.emit(payload)
                devlog.done("browse", f"{api_path} revalidate", devlog.clock() - t0, n=len(payload["sections"]))
                return
            if not payload["error"] and payload["sections"]:
                # Same no-empty-cache rule as the landing: a page whose rows
                # all failed to normalize shouldn't be pinned for the session.
                self._browse_pages[api_path] = payload
                self._save_page_cache()
            self.browsePageLoaded.emit(payload)
            self._set_status(payload["title"] if not payload["error"] else f"Could not load {title}")
            self._set_busy(False)
            devlog.done("browse", api_path, devlog.clock() - t0, n=len(payload["sections"]))
            # Link tiles (e.g. Record Labels) carry no image of their own, so
            # sample cover mosaics for them the same way the landing chips fill.
            if not payload["error"]:
                link_tiles = [
                    (str(it.get("title", "")), str(it.get("path", "")))
                    for section in payload["sections"]
                    if section.get("rowKind") == "links"
                    for it in section.get("items", [])
                    if it.get("path")
                ]
                if link_tiles:
                    self._sample_links_art(link_tiles, gen)

        self.threadpool.start(Worker(work))

    @Slot(str, str)
    def openBrowseItem(self, kind: str, media_id: str) -> None:
        """Open one playlist / mix / album as a synthesized browse page: an
        art header plus its full track list, rendered by the same drill-in
        pane as the editorial pages. Clicking a card's art has to land
        somewhere, and the app has no standalone playlist/mix page otherwise
        (album cards route to the artist page instead, see the QML)."""
        kind = str(kind or "")
        media_id = str(media_id or "")
        if not self._logged_in or kind not in ("playlist", "mix", "album"):
            return
        key = f"item:{kind}:{media_id}"
        cached = self._browse_pages.get(key)
        if cached is not None:
            self.browsePageLoaded.emit(cached)
        if key in self._browse_loading:
            return
        self._browse_loading.add(key)
        revalidate = cached is not None
        gen = self._browse_gen
        if not revalidate:
            self._set_busy(True)
            self._set_status("Opening…")

        def work() -> None:
            t0 = devlog.clock()
            try:
                obj = self._objs[kind].get(media_id)
                if obj is None:
                    session = self.tidal.session
                    if kind == "playlist":
                        obj = session.playlist(media_id)
                    elif kind == "album":
                        obj = session.album(int(media_id))
                    else:
                        with self._browse_lock:  # Mix construction parses via the shared session.page
                            obj = session.mix(media_id)
                    self._remember(kind, media_id, obj)
                desc = ""
                artist_id = ""
                if kind == "mix":
                    with self._browse_lock:  # lazy Mix.items() also parses a page
                        raw = obj.items() or []
                    tracks = [t for t in raw if isinstance(t, Track | Video)]
                    subtitle = str(getattr(obj, "sub_title", "") or "Mix")
                elif kind == "playlist":
                    # items() (not tracks()) so VIDEO entries keep their type:
                    # a video playlist's rows must play/download as videos, not
                    # as their "Audio from video" shadow tracks. Paged because
                    # the endpoint caps at 100 per call.
                    tracks = []
                    for off in (0, 100):
                        page_items = obj.items(limit=100, offset=off) or []
                        tracks.extend(m for m in page_items if isinstance(m, Track | Video))
                        if len(page_items) < 100:
                            break
                else:
                    tracks = list(obj.tracks(limit=200) or [])
                if kind == "playlist":
                    creator = getattr(obj, "creator", None)
                    cname = str(getattr(creator, "name", "") or "") if creator is not None else ""
                    # The hero's eyebrow already reads PLAYLIST, no creator, no line.
                    subtitle = f"By {cname}" if cname else ""
                    desc = str(getattr(obj, "description", "") or "")
                elif kind == "album":
                    subtitle = name_builder_album_artist(obj) + (f"  ·  {_year(obj)}" if _year(obj) else "")
                    artist_id = _artist_id(obj)
                # "N tracks · 2 hr 14 min", fills the header's stats line.
                total = sum(int(getattr(t, "duration", 0) or 0) for t in tracks)
                dur = f"{total // 3600} hr {total % 3600 // 60} min" if total >= 3600 else f"{total // 60} min"
                n_label = f"{len(tracks)} track" + ("s" if len(tracks) != 1 else "")
                stats = f"{n_label}  ·  {dur}"
                if kind == "album":
                    # Mixed-tier albums spell out the split ("9× HI-RES / 3× LOSSLESS")
                    # instead of the single (misleading) album-level tier.
                    tiers: dict[str, int] = {}
                    for t in tracks:
                        tq = _quality_label(t)
                        if tq:
                            tiers[tq] = tiers.get(tq, 0) + 1
                    if len(tiers) > 1:
                        order = {"HI-RES": 0, "LOSSLESS": 1, "HIGH": 2}
                        mix = sorted(tiers.items(), key=lambda kv: order.get(kv[0], 9))
                        stats += "  ·  " + " / ".join(f"{n}× {tq}" for tq, n in mix)
                    else:
                        q = _quality_label(obj)
                        if q:
                            stats += f"  ·  {q}"
                # Videos keep their type through the row dicts ("kind": "video")
                # so the QML can label the button Download video and route the
                # click to the video player instead of the album page.
                items = []
                for t in tracks:
                    if isinstance(t, Video):
                        row = self._video_dict(t)
                        row.update(
                            {
                                "kind": "video",
                                "album": "",
                                "album_id": "",
                                "year": "",
                                "date": "",
                                "quality": "VIDEO",
                                "num": 0,
                                "vol": 1,
                            }
                        )
                        row.setdefault("popularity", -1)
                    else:
                        row = self._track_dict(t)
                    items.append(row)
                if kind == "album":
                    # Multi-disc albums get one section per disc; the album's
                    # own track numbers come along in each row's "num".
                    vols = sorted({it["vol"] for it in items})
                    if len(vols) > 1:
                        sections = [
                            {
                                "rowKind": "tracks",
                                "title": f"Disc {v}",
                                "items": [it for it in items if it["vol"] == v],
                            }
                            for v in vols
                        ]
                    else:
                        sections = [{"rowKind": "tracks", "title": n_label if items else "Tracks", "items": items}]
                else:
                    # Playlists/mixes number by position in the list.
                    for i, it in enumerate(items):
                        it["num"] = i + 1
                    sections = [{"rowKind": "tracks", "title": n_label if items else "Tracks", "items": items}]
                payload = {
                    "key": key,
                    "title": name_builder_title(obj),
                    "header": {
                        "kind": kind,
                        "id": media_id,
                        "title": name_builder_title(obj),
                        "subtitle": subtitle,
                        "desc": desc,
                        "stats": stats,
                        "artist_id": artist_id,
                        "art": _image(obj, 480),
                    },
                    "sections": sections,
                    "error": False,
                }
            except Exception:
                logger.exception("Could not open browse item %s", key)
                payload = {"key": key, "title": "", "sections": [], "error": True}
            if gen != self._browse_gen:
                return  # cross-account stale load, drop silently (see loadBrowse)
            self._browse_loading.discard(key)
            has_items = not payload["error"] and any(s["items"] for s in payload["sections"])
            if revalidate:
                # Silent refresh (e.g. a playlist gained tracks since caching).
                if has_items and payload != cached:
                    self._browse_pages[key] = payload
                    self._save_page_cache()
                    self.browsePageLoaded.emit(payload)
                devlog.done("browse", f"{key} revalidate", devlog.clock() - t0)
                return
            if has_items:
                self._browse_pages[key] = payload
                self._save_page_cache()
            self.browsePageLoaded.emit(payload)
            self._set_status(payload["title"] if not payload["error"] else "Could not open that item")
            self._set_busy(False)
            devlog.done("browse", key, devlog.clock() - t0)

        self.threadpool.start(Worker(work))

    # ----- browse tile art (cover mosaics) --------------------------------

    _TILE_ART_TTL = 7 * 24 * 3600  # editorial pages shuffle slowly; a week is fine
    _TILE_ART_V = 3  # bump to invalidate cached samples when the sampler changes

    @staticmethod
    def _art_identity(obj) -> tuple | None:
        """Who a cover 'belongs to', for per-tile dedup: one cover per artist
        (an artist portrait and two of their albums must not share a tile),
        falling back to the item's own id when no artist is attached."""
        if isinstance(obj, Artist):
            return ("ar", str(getattr(obj, "id", "") or id(obj)))
        if isinstance(obj, Album | Track):
            artist = getattr(obj, "artist", None)
            aid = getattr(artist, "id", None) if artist is not None else None
            if aid is not None:
                return ("ar", str(aid))
            return ("it", str(getattr(obj, "id", "") or id(obj)))
        if isinstance(obj, Mix | Playlist):
            return ("md", str(getattr(obj, "id", "") or id(obj)))
        return None

    def _page_art_sample(self, page, want: int = 12) -> list[str]:
        """Sample up to ``want`` cover URLs from a page for its tile mosaic,
        four show at once, the rest feed the tile's slow rotation.

        Diversity beats adjacency: covers are drawn round-robin ACROSS the
        page's rows (one per row per pass), so the mosaic mixes Top Artists,
        New/Classic Albums, Essentials… instead of four neighbours from one
        list. Within a row, artist portraits and album covers outrank track
        art and text-heavy editorial playlist covers; rows whose best item is
        an artist/album get first pick. The pool is identity-unique (see
        _art_identity): no two covers from the same artist/album/track can
        ever share a tile, no matter how the rotation lands. Deliberately
        does NOT go through the ``_*_dict`` builders: sampling ~45 pages
        through them would flood the ``_objs`` registry and evict live
        search results."""
        rows: list[list[tuple[int, str, tuple]]] = []
        for cat in list(getattr(page, "categories", None) or []):
            items = getattr(cat, "items", None)
            if not isinstance(items, list):
                continue
            row: list[tuple[int, str, tuple]] = []
            for obj in items:
                if isinstance(obj, Artist):
                    rank = 0
                elif isinstance(obj, Album):
                    rank = 1
                elif isinstance(obj, Track):
                    rank = 2  # a track's art IS its album cover
                elif isinstance(obj, Mix):
                    rank = 3
                elif isinstance(obj, Playlist):
                    rank = 4
                else:
                    continue
                ident = self._art_identity(obj)
                url = _image(obj, 320)
                if url and ident is not None:
                    row.append((rank, url, ident))
            if row:
                row.sort(key=lambda t: t[0])
                rows.append(row)
        rows.sort(key=lambda r: r[0][0])  # artist/album-led rows pick first
        out: list[str] = []
        seen_urls: set[str] = set()
        seen_ids: set[tuple] = set()
        i = 0
        while len(out) < want:
            progressed = False
            for row in rows:
                if i >= len(row):
                    continue
                progressed = True
                _, url, ident = row[i]
                if url not in seen_urls and ident not in seen_ids:
                    seen_urls.add(url)
                    seen_ids.add(ident)
                    out.append(url)
                    if len(out) >= want:
                        break
            if not progressed:
                break
            i += 1
        return out

    def _tile_art_disk(self) -> dict:
        try:
            with open(self._tile_art_path, encoding="utf-8") as handle:
                stored = json.load(handle)
            # Drop entries written by an older sampler (e.g. the 4-cover v1).
            return {k: v for k, v in stored.items() if isinstance(v, dict) and v.get("v") == self._TILE_ART_V}
        except Exception:
            logger.debug("No tile-art cache to load", exc_info=True)
            return {}

    def _start_tile_art(self, payload: dict, gen: int) -> None:
        """Fill the landing's genre/mood/decade tiles with cover mosaics.

        Serves everything already known (memory, then the disk cache within
        TTL) immediately, then walks the remaining pages on ONE background
        worker, serialized and politely paced, so the mosaic crawl can never
        stampede TIDAL or starve the metadata pool."""
        links = [
            (str(link.get("title", "")), str(link.get("path", "")))
            for group in ("genres", "moods", "decades")
            for link in payload.get(group, [])
            if link.get("path")
        ]
        if not links:
            return
        disk = self._tile_art_disk()
        # Persist the chip list itself so the login-time prefetch can judge
        # cache freshness (and know what to crawl) without any network.
        disk["_paths"] = {"links": links, "ts": time.time(), "v": self._TILE_ART_V}
        self._sample_links_art(links, gen, disk)

    def _sample_links_art(self, links: list[tuple[str, str]], gen: int, disk: dict | None = None) -> None:
        """Fill a set of link tiles with cover mosaics: serve everything cached
        (memory, then disk within TTL) immediately, then sample the rest on the
        single serialized tile-art worker. Shared by the landing's genre/mood/
        decade chips and drilled link pages (e.g. Record Labels, which carry no
        image of their own), so every tile grid fills the same way."""
        if not links:
            return
        if disk is None:
            disk = self._tile_art_disk()
        now = time.time()
        missing: list[tuple[str, str]] = []
        for title, path in links:
            arts = self._tile_art_mem.get(path)
            if arts is None:
                entry = disk.get(path)
                if entry and now - float(entry.get("ts", 0)) < self._TILE_ART_TTL:
                    arts = [str(u) for u in entry.get("arts", [])]
                    self._tile_art_mem[path] = arts
            if arts:
                self.browseTileArt.emit(path, arts)
            elif arts is None:
                missing.append((title, path))
        if not missing or self._tile_art_running:
            return
        self._tile_art_running = True

        def work() -> None:
            fetched = 0
            try:
                for title, path in missing:
                    if gen != self._browse_gen or not self._logged_in:
                        return
                    try:
                        arts = self._page_art_sample(self._browse_fetch(title, path))
                    except Exception:
                        logger.debug("Tile art fetch failed for %s", path, exc_info=True)
                        continue
                    # Remember misses too (as []) so a page with no usable
                    # covers isn't re-crawled every session within the TTL.
                    self._tile_art_mem[path] = arts
                    disk[path] = {"arts": arts, "ts": time.time(), "v": self._TILE_ART_V}
                    fetched += 1
                    if arts:
                        self.browseTileArt.emit(path, arts)
                    time.sleep(0.1)  # polite pacing between page fetches
            finally:
                self._tile_art_running = False
                if fetched:
                    try:
                        with open(self._tile_art_path, "w", encoding="utf-8") as handle:
                            json.dump(disk, handle, indent=1)
                    except Exception:
                        logger.exception("Could not save the tile-art cache")

        self.threadpool.start(Worker(work))

    def _prefetch_tile_art(self) -> None:
        """Warm the tile-art cache right after login so the Browse mosaics
        paint instantly instead of trickling in on first open.

        Network-frugal by design: when the disk cache already covers every
        known chip page within TTL this does NOTHING (the chip list itself is
        persisted, so freshness is judged offline); otherwise it spends one
        Explore fetch to learn the chip paths and then crawls only the
        missing pages via the usual serialized worker."""
        disk = self._tile_art_disk()
        now = time.time()
        stored = disk.get("_paths") or {}
        links = [(str(t), str(p)) for t, p in stored.get("links", [])]
        if links and now - float(stored.get("ts", 0)) < self._TILE_ART_TTL:
            fresh = all(
                (e := disk.get(path)) is not None and now - float(e.get("ts", 0)) < self._TILE_ART_TTL
                for _, path in links
            )
            if fresh:
                return  # everything cached, zero network spent
        gen = self._browse_gen

        def work() -> None:
            try:
                chips, _ = self._chips_from_explore(self._browse_fetch("Explore", "pages/explore"))
            except Exception:
                logger.debug("Tile-art prefetch skipped (explore fetch failed)", exc_info=True)
                return
            if gen != self._browse_gen or not self._logged_in:
                return
            self._start_tile_art(chips, gen)

        self.threadpool.start(Worker(work))

    # ----- downloads -----------------------------------------------------

    def _emit_queue(self) -> None:
        if self._queue_emit_suspended:
            return
        self._prune_job_tracks()  # registries follow their queue rows out
        self.queueChanged.emit(list(self._queue))

    def _enqueue_albums(self, keys) -> None:
        """Enqueue a batch of album downloads as a single queue update.

        Runs on the GUI thread (via the queued ``_albumsQueued`` signal), so
        each album's progress relay keeps GUI-thread affinity. Per-item
        ``queueChanged`` emits are coalesced into one so the whole discography
        appears at once rather than the queue visibly jumping 0 → N."""
        self._queue_emit_suspended = True
        try:
            for key in keys:
                self.downloadAlbum(str(key))
        finally:
            self._queue_emit_suspended = False
        self._emit_queue()

    def _enqueue_tracks(self, keys) -> None:
        """Batch counterpart of _enqueue_albums for individual tracks (guest
        appearances from a discography download). Same GUI-thread affinity and
        coalesced queueChanged rationale."""
        self._queue_emit_suspended = True
        try:
            for key in keys:
                self.downloadTrack(str(key))
        finally:
            self._queue_emit_suspended = False
        self._emit_queue()

    def _enqueue(
        self,
        name: str,
        type_media: str,
        media_id: str = "",
        template: str = "",
        collection: bool = False,
        artist: str = "",
        tracks: int = 0,
        art: str = "",
    ) -> int:
        self._queue_seq += 1
        qid = self._queue_seq
        self._queue.append(
            {
                "qid": qid,
                "name": name,
                "type": type_media,
                "status": "queued",
                "progress": 0.0,
                "media_id": media_id,
                "template": template,
                "collection": collection,
                # Shown in the queue row ("artist · done/total tracks"); the QML
                # derives the done count from progress and the track total.
                "artist": artist,
                "tracks": tracks,
                # Cover/thumb URL for the queue card (empty when unavailable).
                "art": art,
            }
        )
        self._emit_queue()
        return qid

    def _queue_item(self, qid: int) -> dict | None:
        return next((it for it in self._queue if it["qid"] == qid), None)

    def _set_queue_status(self, qid: int, status: str) -> None:
        item = self._queue_item(qid)
        if item is not None:
            item["status"] = status
            self._emit_queue()

    def _set_queue_progress(self, qid: int, pct: float) -> None:
        item = self._queue_item(qid)
        if item is not None:
            item["progress"] = pct
            self.queueItemProgress.emit(qid, float(pct))

    def _report_pct(self, media_id: str, qid: int, pct: float) -> None:
        """Fan a per-track progress tick out to the media button, the queue row
        and any artist-discography aggregate. Called on the GUI thread via the
        _ProgressSignals bound slot."""
        self.downloadProgress.emit(media_id, float(pct))
        self._set_queue_progress(qid, float(pct))
        self._bump_artist_group(media_id, float(pct), None)

    # ----- per-track queue view (queue drawer album expansion) ------------

    def _track_lifecycle(self, qid: int, ev: dict) -> None:
        """Record one track's state change and stream it to QML. Called on the
        GUI thread via _ProgressSignals.track_event (queued connection)."""
        reg = self._job_tracks.setdefault(qid, {})
        row = reg.get(ev["id"])
        if row is None:
            row = {**ev, "pct": 0.0}
            reg[ev["id"]] = row
        else:
            row["status"] = ev["status"]
            if ev.get("desc"):
                row["desc"] = ev["desc"]
        if ev["status"] == "done":
            row["pct"] = 100.0
        self.queueTrackState.emit(qid, dict(row))

    @Slot()
    def _poll_track_progress(self) -> None:
        """Read live per-track percentages out of each running job's rich
        Progress (tasks are keyed by the description _TrackedDownload mirrors)."""
        if not self._job_dls:
            self._track_poll.stop()
            return
        for qid, dl in list(self._job_dls.items()):
            reg = self._job_tracks.get(qid)
            if not reg:
                continue
            try:
                tasks = {t.description: t.percentage for t in dl.progress.tasks}
            except Exception:
                # Transient: rich mutates its task list from worker threads;
                # skip this tick and read a consistent snapshot next time.
                logger.debug("Skipped a track-progress poll tick", exc_info=True)
                continue
            ticks: dict[str, float] = {}
            for tid, row in reg.items():
                if row.get("status") != "running":
                    continue
                pct = tasks.get(row.get("desc", ""))
                if pct is None:
                    continue
                pct = max(0.0, min(100.0, float(pct)))
                if abs(pct - float(row.get("pct", 0.0))) >= 0.5:
                    row["pct"] = pct
                    ticks[tid] = pct
            if ticks:
                self.queueTrackPct.emit(qid, ticks)

    @Slot(int)
    def loadQueueTracks(self, qid: int) -> None:
        """Fetch a queued album's ordered track list for the drawer expansion.

        The (possibly network-bound) fetch runs on a worker; the merge with the
        live per-track registry happens back on the GUI thread so a lifecycle
        event can't slip between snapshot and delivery."""
        qid = int(qid)
        item = self._queue_item(qid)
        if item is None:
            self.queueTracksLoaded.emit(qid, [])
            return
        album = self._objs["album"].get(str(item.get("media_id", "")))

        def work() -> None:
            tracks = []
            if album is not None:
                try:
                    tracks = album.tracks() or []
                except Exception:
                    logger.exception("Could not load queue album tracks")
            out = []
            for i, tr in enumerate(tracks, start=1):
                out.append(
                    {
                        "id": str(getattr(tr, "id", i)),
                        "num": i,
                        "title": name_builder_title(tr),
                        "duration": _fmt_duration(getattr(tr, "duration", 0)),
                    }
                )
            self._queueTracksFetched.emit(qid, out)

        self.threadpool.start(Worker(work))

    def _merge_queue_tracks(self, qid: int, fetched) -> None:
        """GUI thread: overlay live track states onto the fetched album order
        (falling back to the registry alone when the fetch came back empty)."""
        reg = self._job_tracks.get(int(qid), {})
        rows: list[dict] = []
        if fetched:
            for entry in fetched:
                st = reg.get(str(entry["id"])) or {}
                rows.append(
                    {
                        **entry,
                        "status": st.get("status", "pending"),
                        "pct": float(st.get("pct", 0.0)),
                    }
                )
        else:
            for st in sorted(reg.values(), key=lambda r: (r.get("vol", 1), r.get("num", 0))):
                rows.append(
                    {
                        "id": st.get("id", ""),
                        "num": 0,
                        "title": st.get("title", ""),
                        "duration": st.get("duration", ""),
                        "status": st.get("status", "pending"),
                        "pct": float(st.get("pct", 0.0)),
                    }
                )
            for i, row in enumerate(rows, start=1):
                row["num"] = i
        self.queueTracksLoaded.emit(int(qid), rows)

    def _prune_job_tracks(self) -> None:
        """Drop per-track registries whose queue rows are gone."""
        live = {it["qid"] for it in self._queue}
        for qid in list(self._job_tracks):
            if qid not in live:
                self._job_tracks.pop(qid, None)

    # ----- Waves-only preferences (kept out of tidaler's Settings) -------

    def _apply_first_run_defaults(self) -> None:
        """Waves' opinionated defaults for a brand-new install, layered over
        tidaler's stock dataclass defaults and persisted. Only called when no
        settings file existed yet, an existing user's choices are never touched."""
        d = self.settings.data
        d.use_primary_album_artist = True  # library-friendly Artist/Album folders
        d.video_download = False  # audio-first out of the box
        d.quality_video = QualityVideo.P720
        d.mark_explicit = True
        d.metadata_write_url = False
        self.settings.save()

    def _load_waves_prefs(self) -> dict:
        prefs = {
            "explicit_mode": "explicit",
            "collapse_editions": True,
            "edition_conflict": "merge",
            "disco_albums": True,
            "disco_eps": True,
            "disco_featured": True,
            "disco_appears_on": False,
            "clean_album_artist": True,
            # Updates: opt-in, off by default (preserves the no-phone-home-by-
            # default promise). update_last_check is housekeeping state, not a
            # user-facing setting, so it isn't in settingsSchema.
            "auto_update": False,
            "update_cadence": "daily",
            "update_last_check": 0,
            # Browse landing presentation: "art" (artwork-first, hover
            # controls) or "console" (chip sets + framed cards).
            "browse_style": "art",
            # Ambient wave-loop video behind the UI; on by default, the toggle
            # fully stops the decode pipeline (not just hides it).
            "motion_background": True,
        }
        try:
            with open(self._waves_prefs_path, encoding="utf-8") as handle:
                stored = json.load(handle)
            prefs.update({k: v for k, v in stored.items() if k in prefs})
        except Exception:
            logger.debug("No Waves prefs to load", exc_info=True)
        return prefs

    def _save_waves_prefs(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._waves_prefs_path), exist_ok=True)
            with open(self._waves_prefs_path, "w", encoding="utf-8") as handle:
                json.dump(self._waves_prefs, handle, indent=2)
        except Exception:
            logger.exception("Could not save Waves prefs")

    @Slot(str, result="QVariant")
    def wavesPref(self, key: str):
        """Read one Waves-only pref (whitelisted in _load_waves_prefs)."""
        return self._waves_prefs.get(key)

    @Slot(str, "QVariant")
    def setWavesPref(self, key: str, value) -> None:
        if key not in self._waves_prefs:
            return
        # Preserve the pref's type, a bool stored via str() becomes the truthy
        # string "False", so coerce against the existing default's type.
        if isinstance(self._waves_prefs[key], bool):
            value = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
        else:
            value = str(value)
        self._waves_prefs[key] = value
        self._save_waves_prefs()
        if key == "motion_background":
            self.motionBgChanged.emit()

    def _waves_pref_bool(self, key: str) -> bool:
        v = self._waves_prefs.get(key, False)
        return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")

    def _album_key(self, album):
        # Group by normalised title + normalised primary-artist NAME + track count.
        # We use the artist *name* from a single consistent source, mixing an id
        # with a name fallback meant the same album keyed differently depending on
        # whether its artist relationship happened to be populated, so dupes slipped
        # through. Track count is part of the key on purpose: this dedup runs BEFORE
        # the track-aware edition stage and only keeps the highest-quality version
        # per key, so collapsing two same-titled editions that differ in track count
        # would silently drop the extra edition's unique songs. Quality/region
        # duplicates of ONE release share a track count and still collapse to the
        # best version; a more-complete same-titled edition now survives to the
        # edition stage, which decides losslessly. (A deluxe already keeps its own
        # title and stays separate regardless.)
        artist = _primary_artist_name(album) or name_builder_album_artist(album)
        return (_norm_title(name_builder_title(album)), _norm_artist(artist), int(getattr(album, "num_tracks", 0) or 0))

    def _track_key(self, track):
        artist = _primary_artist_name(track) or name_builder_artist(track)
        return (_norm_title(name_builder_title(track)), _norm_artist(artist))

    def _max_quality_rank(self) -> int:
        """Rank of the user's configured maximum audio quality (the cap that
        search results are filtered down to)."""
        return _QUALITY_RANK.get(getattr(self.settings.data.quality_audio, "name", ""), 4)

    def _dedup_albums(self, albums: list) -> list:
        mode = self._waves_prefs.get("explicit_mode", "explicit")
        out = _dedup_versions(albums, self._album_key, mode, self._max_quality_rank())
        devlog.event("dedup", "albums", inp=len(albums), out=len(out), mode=mode)
        return out

    def _dedup_tracks(self, tracks: list) -> list:
        mode = self._waves_prefs.get("explicit_mode", "explicit")
        out = _dedup_versions(tracks, self._track_key, mode, self._max_quality_rank())
        devlog.event("dedup", "tracks", inp=len(tracks), out=len(out), mode=mode)
        return out

    def _collapse_editions(self, albums: list) -> list:
        """Filter a discography down to the most complete edition of each album,
        per the ``edition_conflict`` preference. Track lists are fetched only for
        the albums that share a base title (cached per call); a fetch failure
        keeps both editions rather than guessing."""
        conflict = self._waves_prefs.get("edition_conflict", "keep_both")
        cache: dict = {}

        def tracks_of(album):
            aid = id(album)
            if aid not in cache:
                try:
                    cache[aid] = [
                        (_norm_track_title(getattr(t, "name", "")), getattr(t, "duration", None))
                        for t in album.tracks()
                        if _norm_track_title(getattr(t, "name", ""))
                    ]
                except Exception:
                    logger.debug("Could not load tracks for edition compare", exc_info=True)
                    cache[aid] = []
            return cache[aid]

        out = _collapse_album_editions(albums, tracks_of, _quality_rank, conflict)
        devlog.event("collapse_editions", inp=len(albums), out=len(out), conflict=conflict)
        return out

    def _merge_recs_factory(self):
        """A per-call caching ``recs_of`` closure: album -> list[_MergeRec]
        (track object + normalised title + duration + ISRC + explicit flag) for
        merge planning. A fetch failure yields an empty list so the planner skips
        that edition."""
        cache: dict = {}

        def recs_of(album):
            aid = id(album)
            if aid not in cache:
                try:
                    cache[aid] = [
                        _MergeRec(
                            t,
                            _norm_track_title(getattr(t, "name", "")),
                            getattr(t, "duration", None),
                            _track_isrc(t),
                            bool(getattr(t, "explicit", False)),
                        )
                        for t in album.tracks()
                        if _norm_track_title(getattr(t, "name", ""))
                    ]
                except Exception:
                    logger.debug("Could not load tracks for merge planning", exc_info=True)
                    cache[aid] = []
            return cache[aid]

        return recs_of

    def _merge_editions(self, albums: list) -> tuple[list, list]:
        """Plan a 'best of both' discography. Returns ``(plain_albums, plans)``:
        albums to download whole, and ``(identity_album, plan)`` merges for edition
        groups where a higher-quality edition is a subset of a more complete one.
        Groups with no quality upgrade collapse to the most complete edition (so
        the user still gets the fullest version, just without a merge)."""
        recs_of = self._merge_recs_factory()

        def tracks_of(album):  # (title, duration) view for the completeness fallback
            return [(r.title, r.dur) for r in recs_of(album)]

        groups: dict = {}
        order: list = []
        for a in albums:
            key = _edition_base_key(a)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(a)

        plain: list = []
        plans: list = []
        for key in order:
            group = groups[key]
            if len(group) < 2:
                plain.extend(group)
                continue
            identity, plan = _build_merge_plan(group, recs_of, _quality_rank)
            if plan:
                plans.append((identity, plan))
            else:
                plain.extend(_collapse_album_editions(group, tracks_of, _quality_rank, "completeness"))
        devlog.event("merge_editions", inp=len(albums), plain=len(plain), plans=len(plans))
        return plain, plans

    def _sibling_editions(self, album) -> list:
        """All editions of ``album`` by the same artist (sharing its edition base
        key), for a single-album best-of-both. Reuses the artist's album buckets;
        always includes ``album`` itself."""
        base = _edition_base_key(album)
        artist_id = str(getattr(getattr(album, "artist", None), "id", "") or "")
        artist = self._get_artist(artist_id) if artist_id else None
        out: list = []
        seen: set = set()
        own_id = getattr(album, "id", None)
        if own_id is not None:
            seen.add(own_id)
        out.append(album)
        if artist is not None:
            for getter in ("get_albums", "get_ep_singles", "get_other"):
                try:
                    candidates = getattr(artist, getter)() or []
                except Exception:
                    candidates = []
                for a in candidates:
                    aid = getattr(a, "id", None)
                    if aid in seen or not _is_album_entity(a):
                        continue
                    seen.add(aid)
                    if _edition_base_key(a) == base:
                        out.append(a)
        return out

    def _build_download(self, signals: _ProgressSignals, event_abort: Event | None = None) -> Download:
        self._resolve_ffmpeg()
        progress_gui = ProgressBars(
            item=signals.item,
            item_name=signals.item_name,
            list_item=signals.list_item,
            list_name=signals.list_name,
        )
        return _TrackedDownload(
            tidal_obj=self.tidal,
            path_base=self.settings.data.download_base_path,
            fn_logger=logger,
            skip_existing=self.settings.data.skip_existing,
            progress=Progress(),
            progress_gui=progress_gui,
            event_abort=event_abort or self._event_abort,
            event_run=self._event_run,
            track_signals=signals,
        )

    def _download(
        self,
        obj,
        type_media: str,
        name: str,
        file_template: str,
        collection: bool,
        media_id: str,
        merge_plan: list | None = None,
    ) -> None:
        if not self._logged_in:
            self._set_status("Sign in before downloading")
            return
        # Artist + total track count for the queue row label. Collections report
        # their track total; a single track/video counts as one.
        artist = _primary_artist_name(obj)
        tracks = len(merge_plan) if merge_plan is not None else (_track_count(obj) if collection else 1)
        qid = self._enqueue(name, type_media, media_id, file_template, collection, artist, tracks, _image(obj, 160))
        # Per-job abort event so this one download can be cancelled on its own
        # (the shared _event_abort would stop every concurrent download).
        job_abort = Event()
        self._job_aborts[qid] = job_abort
        # Each job gets its own relay so concurrent downloads don't cross-talk.
        # The relay wires the per-track signal to a bound slot (see
        # _ProgressSignals); hold a strong ref so it lives for the whole job.
        signals = _ProgressSignals(self, qid, media_id, collection)
        self._job_signals[qid] = signals
        dl = self._build_download(signals, event_abort=job_abort)
        if collection or merge_plan is not None:
            # Seed the per-track registry. A merge plan knows its exact track
            # list up front; a plain collection fills in as tracks start.
            reg: dict[str, dict] = {}
            for tnum_i, entry in enumerate(merge_plan or [], 1):
                src, tnum, vnum = entry
                tid = str(getattr(src, "id", "") or f"plan-{tnum_i}")
                reg[tid] = {
                    "id": tid,
                    "title": name_builder_title(src),
                    "num": int(tnum or tnum_i),
                    "vol": int(vnum or 1),
                    "duration": _fmt_duration(getattr(src, "duration", 0)),
                    "desc": "",
                    "status": "pending",
                    "pct": 0.0,
                }
            self._job_tracks[qid] = reg
            self._job_dls[qid] = dl
            if not self._track_poll.isActive():
                self._track_poll.start()

        def work() -> None:
            # Cancelled before it even started (still sitting in the pool). Run
            # the same teardown the finally clause does, and settle the queue row
            # + any artist-discography aggregate, otherwise the group counts this
            # album as forever-running and its _ProgressSignals relay leaks.
            if job_abort.is_set():
                self._set_queue_status(qid, "cancelled")
                self.downloadState.emit(media_id, "")
                self._bump_artist_group(media_id, None, "failed")
                self._job_aborts.pop(qid, None)
                self._job_signals.pop(qid, None)
                # Drop the track-poll registration too, or the 500 ms per-track
                # progress timer keeps polling this dead job forever.
                self._job_dls.pop(qid, None)
                return
            self._set_queue_status(qid, "running")
            self.downloadState.emit(media_id, "running")
            self._set_status(f"Downloading {name}…")
            devlog.event("download", "start", type=type_media, id=media_id, qid=qid)
            t0 = devlog.clock()
            try:
                if merge_plan is not None:
                    self._download_merge_plan(dl, signals, job_abort, obj, file_template, merge_plan)
                elif collection:
                    dl.items(file_template=file_template, media=obj)
                else:
                    dl.item(file_template=file_template, media=obj)
                if job_abort.is_set():
                    # Cancelled mid-download, don't report success.
                    self.downloadState.emit(media_id, "")
                    self._set_queue_status(qid, "cancelled")
                    self._bump_artist_group(media_id, None, "failed")
                    self._set_status(f"Cancelled {name}")
                else:
                    # Merge succeeded → the stashed plan (kept for a possible
                    # retry) is no longer needed; drop it now.
                    if merge_plan is not None:
                        self._merge_plans.pop(media_id, None)
                    self.downloadProgress.emit(media_id, 100.0)
                    self._set_queue_progress(qid, 100.0)
                    self.downloadState.emit(media_id, "done")
                    self._set_queue_status(qid, "done")
                    self._bump_artist_group(media_id, 100.0, "done")
                    self._set_status(f"Finished {name}")
                    devlog.done("download", f"done {type_media} id={media_id}", devlog.clock() - t0)
            except Exception:
                if job_abort.is_set():
                    self.downloadState.emit(media_id, "")
                    self._set_queue_status(qid, "cancelled")
                    self._bump_artist_group(media_id, None, "failed")
                    self._set_status(f"Cancelled {name}")
                else:
                    logger.exception("Download failed for %s", name)
                    self.downloadState.emit(media_id, "failed")
                    self._set_queue_status(qid, "failed")
                    self._bump_artist_group(media_id, None, "failed")
                    self._set_status(f"Failed {name}")
                    devlog.done("download", f"FAILED {type_media} id={media_id}", devlog.clock() - t0)
            finally:
                self._job_aborts.pop(qid, None)
                self._job_signals.pop(qid, None)
                # Worker-thread pop is safe (the GUI poller iterates a list()
                # snapshot); the poll timer stops itself once this is empty.
                self._job_dls.pop(qid, None)

        self.dl_pool.start(Worker(work))

    def _download_merge_plan(self, dl, signals, job_abort, identity_album, file_template, plan) -> None:
        """Download a synthesized 'best of both' album.

        Each plan entry is fetched through the public ``Download.item`` (so the
        per-track audio is whatever its source edition offers) and re-tagged as a
        member of ``identity_album`` via :func:`_as_member_of`. This mirrors how
        ``Download.items`` fans tracks out on a pool and reports list-level
        progress, but over an explicit track list, keeping ``download.py``
        untouched."""
        total = len(plan)
        if not total:
            return
        max_workers = max(1, int(self.settings.data.downloads_concurrent_max or 3))
        done = 0
        failures = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = [
                pool.submit(
                    dl.item,
                    file_template=file_template,
                    media=_as_member_of(src, identity_album, tnum, vnum),
                    is_parent_album=True,
                    list_position=i,
                    list_total=total,
                    keep_album=True,  # trust the deluxe identity we re-tagged onto the track
                    event_stop=job_abort,
                )
                for i, (src, tnum, vnum) in enumerate(plan, 1)
            ]
            for fut in as_completed(futs):
                try:
                    ok, _path = fut.result()
                    if not ok:
                        failures += 1
                except Exception:
                    failures += 1
                    logger.exception("Merge-plan track download failed")
                done += 1
                signals.list_item.emit(100.0 * done / total)
                if job_abort.is_set():
                    for f in futs:
                        f.cancel()
                    break
        # A partially-failed merge must NOT be reported as a clean success (that
        # would silently leave the user short a song). Raise so the caller marks
        # the job failed/retryable, unless the user aborted, which is handled
        # separately as a cancellation.
        if failures and not job_abort.is_set():
            raise RuntimeError(f"{failures}/{total} merged tracks failed to download")  # noqa: TRY003

    def _bump_artist_group(self, media_id: str, pct, state) -> None:
        """Roll an album's progress into any 'download discography' group it
        belongs to, emitting the averaged progress under the artist id so the
        artist button shows a real bar. Cheap no-op for non-grouped downloads."""
        if not self._artist_groups:
            return
        with self._artist_lock:
            aid = next((a for a, g in self._artist_groups.items() if media_id in g["keys"]), None)
            if aid is None:
                return
            grp = self._artist_groups[aid]
            if state == "done":
                grp["prog"][media_id] = 100.0
                grp["done"].add(media_id)
            elif state == "failed":
                grp["done"].add(media_id)
                grp["failed"].add(media_id)
            elif pct is not None:
                grp["prog"][media_id] = float(pct)
            total = len(grp["keys"]) or 1
            agg = sum(grp["prog"].get(k, 0.0) for k in grp["keys"]) / total
            finished = len(grp["done"]) >= len(grp["keys"])
            any_failed = bool(grp["failed"])
            if finished:
                del self._artist_groups[aid]
        if finished:
            if any_failed:
                self.downloadState.emit(aid, "failed")
            else:
                self.downloadProgress.emit(aid, 100.0)
                self.downloadState.emit(aid, "done")
        else:
            self.downloadProgress.emit(aid, float(agg))
            self.downloadState.emit(aid, "running")

    def _preview_source(self, track, whole: bool = False) -> str:
        """Produce a small, **seekable** local ``.m4a`` for ``track`` and return
        its ``file://`` URL.

        TIDAL serves segmented DASH/HLS (BTS single-file streams are gone for
        most accounts). QMediaPlayer can *play* an HLS stream but cannot *seek*
        it, its FFmpeg backend blocks on ``setPosition``, which kills the
        scrubber. So instead of streaming the playlist to the player, our bundled
        ffmpeg fetches + remuxes the LOW/AAC segments into one faststart MP4 that
        the player scrubs freely. ffmpeg (unlike QMediaPlayer) accepts a protocol
        whitelist, so it reads the ``https`` segments from a local ``.m3u8``.

        ``whole`` remuxes the entire track (the artist scrubber wants the full
        song); otherwise only the first ~35s, a quick track taste. At LOW/AAC a
        whole track is a couple of MB, so the remux is ~1s.

        The stream fetch holds ``stream_lock`` and restores the session's
        configured quality in ``finally`` (``restore_normal_session`` early-returns
        without touching quality in normal mode, per config.py), so a concurrent
        or subsequent download is never silently downgraded. The slower ffmpeg
        fetch/remux runs *outside* the lock.
        """
        ffmpeg = self._preview_ffmpeg_bin()
        if not ffmpeg:
            raise RuntimeError("preview: ffmpeg unavailable")  # noqa: TRY003
        with self.tidal.stream_lock:
            try:
                if not self.tidal.restore_normal_session():
                    raise RuntimeError("preview: could not normalise session")  # noqa: TRY003
                self.tidal.session.audio_quality = Quality.low_96k
                stream = track.get_stream()
                manifest = stream.get_stream_manifest()
                if manifest.is_encrypted:
                    # Encrypted streams need the download+decrypt path; not a
                    # target for lightweight preview streaming.
                    raise RuntimeError("preview: encrypted stream is not previewable")  # noqa: TRY003
                # BTS (a single https file) is directly seekable; hand it straight
                # to ffmpeg too so every path yields a uniform local clip.
                hls = None if stream.is_bts else manifest.get_hls()
                src = manifest.get_urls()[0] if stream.is_bts else None
            finally:
                # Canonical resting quality, NOT restore_normal_session(), which
                # leaves quality untouched in normal mode (config.py).
                self.tidal.session.audio_quality = Quality(self.settings.data.quality_audio)
        return self._remux_preview(ffmpeg, src, hls, whole)

    def _preview_ffmpeg_bin(self) -> str | None:
        """Path to an ffmpeg binary for the preview remux (managed → PATH)."""
        self._resolve_ffmpeg()  # points settings at the managed copy if present
        return self.settings.data.path_binary_ffmpeg or shutil.which("ffmpeg")

    def _remux_preview(self, ffmpeg: str, src_url: str | None, hls: str | None, whole: bool) -> str:
        """Fetch + remux a preview into a faststart local ``.m4a``; return file URL.

        Exactly one preview plays at a time, so the previous clip is deleted when
        a new one is produced. ``-c copy`` keeps it fast (no re-encode); the HLS
        input needs the protocol whitelist so ffmpeg may open the https segments.
        """
        prev = self._preview_tmp
        if prev:
            with contextlib.suppress(OSError):
                os.remove(prev)
            self._preview_tmp = None
        m3u_path = None
        if hls is not None:
            fd, m3u_path = tempfile.mkstemp(prefix="waves_preview_", suffix=".m3u8")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(hls)
        fd_out, out_path = tempfile.mkstemp(prefix="waves_preview_", suffix=".m4a")
        os.close(fd_out)
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y"]
        if m3u_path is not None:
            cmd += ["-protocol_whitelist", "file,crypto,data,https,tls,tcp", "-i", m3u_path]
        else:
            cmd += ["-i", src_url]
        if not whole:
            cmd += ["-t", "30"]  # a quick 30s taste (the clip length == what plays)
        cmd += ["-c", "copy", "-movflags", "+faststart", out_path]
        try:
            # Fixed ffmpeg argument list (no shell, no user-supplied flags); the
            # only variable inputs are our own temp paths and a TIDAL CDN URL.
            subprocess.run(cmd, check=True, capture_output=True, timeout=90, creationflags=proc.NO_WINDOW)  # noqa: S603
        finally:
            if m3u_path is not None:
                with contextlib.suppress(OSError):
                    os.remove(m3u_path)
        self._preview_tmp = out_path
        return pathlib.Path(out_path).as_uri()

    def _probe_video_mbps(self, seg_url: str) -> float:
        """Measure downstream throughput by timing ~1.5 MB of a real stream
        segment (or 4 s, whichever comes first). Returns Mbps, or -1."""
        import requests

        try:
            t0 = time.monotonic()
            n = 0
            with requests.get(seg_url, stream=True, timeout=10) as r:  # , timeout set
                r.raise_for_status()
                for chunk in r.iter_content(65536):
                    n += len(chunk)
                    if n >= 1_500_000 or time.monotonic() - t0 > 4:
                        break
            dt = time.monotonic() - t0
            return (n * 8 / 1e6) / dt if dt > 0 and n > 0 else -1
        except Exception:
            logger.debug("video bandwidth probe failed", exc_info=True)
            return -1

    def _pick_video_stream(self, master_url: str) -> tuple[str, int, list[int]]:
        """Choose one variant from the master HLS playlist by resolution.

        The persisted Video-quality setting caps the height. Until the user
        explicitly picks a quality, the first video of the run also probes the
        connection (~1.5 MB of the top variant's first segment) and lowers the
        cap so the initial experience matches the pipe: >=12 Mbps 1080p,
        >=6 720p, >=3 480p, else 360p. Returns (variant_url, height,
        available_heights) so the player's quality menu only offers what
        this video actually has; falls back to the master URL untouched if
        anything about the playlist is unexpected (the player then does its
        own default selection). The persisted setting is never lowered here:
        a video that lacks the preferred quality plays the next one down,
        the next video tries the preference again."""
        import m3u8

        try:
            master = m3u8.load(master_url)
            if not master.is_variant:
                return master_url, 0, []
            cands = sorted(
                (int(p.stream_info.resolution[1]), str(p.absolute_uri))
                for p in master.playlists
                if p.stream_info and p.stream_info.resolution
            )
            if not cands:
                return master_url, 0, []
            cap = int(self.settings.data.quality_video)
            if not self._video_user_quality:
                if self._video_auto_cap is None:
                    mbps = self._probe_video_mbps(self._first_segment_url(cands[-1][1]))
                    if mbps > 0:
                        self._video_auto_cap = 1080 if mbps >= 12 else 720 if mbps >= 6 else 480 if mbps >= 3 else 360
                        devlog.event("video", f"probe {mbps:.1f} Mbps -> {self._video_auto_cap}p cap")
                if self._video_auto_cap is not None:
                    cap = min(cap, self._video_auto_cap)
            under = [c for c in cands if c[0] <= cap]
            height, url = under[-1] if under else cands[0]
            heights = sorted({c[0] for c in cands}, reverse=True)
        except Exception:
            logger.debug("video variant selection failed; using master playlist", exc_info=True)
            return master_url, 0, []
        return url, height, heights

    @staticmethod
    def _first_segment_url(playlist_url: str) -> str:
        """First media-segment URI of a variant playlist (for the probe)."""
        import m3u8

        pl = m3u8.load(playlist_url)
        if not pl.segments:
            raise RuntimeError("probe: empty media playlist")  # noqa: TRY003
        return str(pl.segments[0].absolute_uri)

    @Slot(int)
    def setVideoQuality(self, height: int) -> None:
        """Persist a video resolution picked in the player's quality menu.

        Same setting the Settings page writes (quality_video), so it applies
        to every later video and download until changed again. Mirrors
        applySettings' save dance: the transient ffmpeg injections must be
        restored BEFORE save() or they'd be serialised (see _restore_ffmpeg_*)."""
        try:
            self.settings.data.quality_video = QualityVideo(str(int(height)))
        except Exception:
            logger.exception("Bad video quality %r", height)
            return
        self._video_user_quality = True  # explicit choice beats the bandwidth auto-cap
        self._restore_ffmpeg_flags()
        self._restore_ffmpeg_path()
        self.settings.save()
        if self._logged_in:
            self._init_download()  # downloads honour the new resolution too
        self._set_status(f"Video quality: {int(height)}p")

    def _video_album_fallback(self, title: str, artist: str) -> tuple[str, str]:
        """Best-effort (album_id, track_id) for a music video with no album link.

        TIDAL rarely ties a video to a release, but the song itself almost
        always exists, search it and take the first track whose title and
        primary artist line up, so the player's title link can always land on
        the album page. Empty strings when nothing matches confidently."""
        try:
            # Strip video-only decorations: "(Official Video)", "[Lyric Video]"…
            q = re.sub(r"\s*[([][^)\]]*video[^)\]]*[)\]]", "", title, flags=re.IGNORECASE).strip()
            primary = artist.split(",")[0].strip().lower()
            if not q or not primary:
                return "", ""
            res = self.tidal.session.search(f"{q} {primary}"[:99], models=[Track], limit=10)
            for tr in res.get("tracks") or []:
                tt = str(getattr(tr, "name", "") or "").lower()
                ta = name_builder_artist(tr).lower()
                if q.lower() in tt and primary in ta:
                    album_id = str(getattr(getattr(tr, "album", None), "id", "") or "")
                    if album_id:
                        tid = str(getattr(tr, "id", "") or "")
                        self._remember("track", tid, tr)
                        return album_id, tid
        except Exception:
            logger.debug("video album fallback failed", exc_info=True)
        return "", ""

    @Slot(str)
    def playVideo(self, video_id: str) -> None:
        """Resolve a video's stream for the in-app overlay player, off the GUI
        thread: master playlist via tidalapi, one variant picked by the Video
        quality setting (bandwidth-capped on first use, see
        _pick_video_stream), streamed directly by the QML MediaPlayer."""
        video_id = str(video_id or "")
        if not video_id or not self._logged_in:
            return

        def work() -> None:
            payload = {
                "id": video_id,
                "title": "",
                "artist": "",
                "artists": [],
                "artist_id": "",
                "album_id": "",
                "track_id": "",
                "url": "",
                "res": 0,
                "heights": [],
                "error": True,
            }
            try:
                obj = self._objs["video"].get(video_id)
                if obj is None:
                    obj = self.tidal.session.video(int(video_id))
                    self._remember("video", video_id, obj)
                payload["title"] = name_builder_title(obj)
                payload["artist"] = name_builder_artist(obj)
                payload["artists"] = _artists_list(obj)
                payload["artist_id"] = _artist_id(obj)
                payload["album_id"] = str(getattr(getattr(obj, "album", None), "id", "") or "")
                if not payload["album_id"]:
                    # Music videos usually carry no album link, but the song
                    # exists, find it so the title always leads somewhere.
                    payload["album_id"], payload["track_id"] = self._video_album_fallback(
                        payload["title"], payload["artist"]
                    )
                url = str(obj.get_url() or "")
                if url:
                    stream_url, height, heights = self._pick_video_stream(url)
                    payload["url"] = stream_url
                    payload["res"] = height
                    payload["heights"] = heights
                    payload["error"] = False
            except Exception:
                logger.exception("Could not resolve video %s", video_id)
            self.videoReady.emit(payload)

        self.threadpool.start(Worker(work))

    @Slot(str)
    def previewTrack(self, track_id: str) -> None:
        """Stream a single track. Resolves the URL off the GUI thread and hands
        it to QML via previewReady; the shared MediaPlayer does the rest."""
        track = self._objs["track"].get(track_id)
        if track is None:
            self.previewState.emit("track", track_id, "")
            return
        self.previewState.emit("track", track_id, "loading")

        def work() -> None:
            # Worker.run() does not catch, guarantee a terminal state so the
            # button can never stick spinning.
            try:
                url = self._preview_source(track, whole=True)  # full track, seekable
                self.previewMeta.emit(
                    "track",
                    track_id,
                    name_builder_title(track),
                    name_builder_artist(track),
                    _image(track, 160),
                    _artist_id(track),
                    str(getattr(getattr(track, "album", None), "id", "") or ""),
                    track_id,
                    _artists_list(track),
                )
                self.previewReady.emit("track", track_id, url)
            except Exception:
                logger.exception("Preview failed for track %s", track_id)
                self.previewState.emit("track", track_id, "error")

        self.threadpool.start(Worker(work))

    @Slot(str)
    def previewArtist(self, artist_id: str) -> None:
        """Stream an artist's top track. The preview stays addressed to the
        artist id so the artwork overlay lights up while the song plays."""
        artist = self._get_artist(artist_id)
        if artist is None:
            self.previewState.emit("artist", artist_id, "")
            return
        self.previewState.emit("artist", artist_id, "loading")

        def work() -> None:
            try:
                tops = artist.get_top_tracks(limit=1)
                if not tops:
                    self.previewState.emit("artist", artist_id, "error")
                    return
                top = tops[0]
                self._remember("track", str(getattr(top, "id", id(top))), top)
                url = self._preview_source(top, whole=True)  # full track for the scrubber
                self.previewMeta.emit(
                    "artist",
                    artist_id,
                    name_builder_title(top),
                    name_builder_artist(top),
                    _image(top, 160),
                    artist_id,
                    str(getattr(getattr(top, "album", None), "id", "") or ""),
                    str(getattr(top, "id", "") or ""),
                    _artists_list(top),
                )
                self.previewReady.emit("artist", artist_id, url)
            except Exception:
                logger.exception("Preview failed for artist %s", artist_id)
                self.previewState.emit("artist", artist_id, "error")

        self.threadpool.start(Worker(work))

    @Slot(str, str)
    def previewMedia(self, kind: str, media_id: str) -> None:
        """Preview an album / playlist / mix by streaming one of its tracks,
        picked at random, addressed to the collection so its card lights up."""
        kind = str(kind or "")
        media_id = str(media_id or "")
        if kind not in ("album", "playlist", "mix") or not self._logged_in:
            return
        self.previewState.emit(kind, media_id, "loading")

        def work() -> None:
            try:
                obj = self._objs[kind].get(media_id)
                if obj is None:
                    session = self.tidal.session
                    if kind == "playlist":
                        obj = session.playlist(media_id)
                    elif kind == "album":
                        obj = session.album(int(media_id))
                    else:
                        with self._browse_lock:  # Mix construction parses via the shared session.page
                            obj = session.mix(media_id)
                    self._remember(kind, media_id, obj)
                if kind == "mix":
                    with self._browse_lock:  # lazy Mix.items() also parses a page
                        raw = obj.items() or []
                    tracks = [t for t in raw if isinstance(t, Track)]
                else:
                    tracks = list(obj.tracks(limit=50) or [])
                if not tracks:
                    self.previewState.emit(kind, media_id, "error")
                    return
                pick = random.choice(tracks)  # noqa: S311, a taste, not crypto
                self._remember("track", str(getattr(pick, "id", id(pick))), pick)
                url = self._preview_source(pick, whole=True)  # full track for the scrubber
                self.previewMeta.emit(
                    kind,
                    media_id,
                    name_builder_title(pick),
                    name_builder_artist(pick),
                    _image(pick, 160),
                    _artist_id(pick),
                    str(getattr(getattr(pick, "album", None), "id", "") or ""),
                    str(getattr(pick, "id", "") or ""),
                    _artists_list(pick),
                )
                self.previewReady.emit(kind, media_id, url)
            except Exception:
                logger.exception("Preview failed for %s %s", kind, media_id)
                self.previewState.emit(kind, media_id, "error")

        self.threadpool.start(Worker(work))

    def _refetch_for_download(self, bucket: str, media_id: str) -> None:
        """A download was requested for an id whose live object is gone from
        ``_objs`` (a new search clears every bucket, and Browse rows outlive
        searches). Re-fetch it by id on a worker, re-remember it, then hop back
        to the GUI thread via ``_mediaRefetched`` to start the download,
        second time around the registry hits."""
        key = (bucket, media_id)
        if key in self._refetch_inflight or not self._logged_in:
            return
        self._refetch_inflight.add(key)
        gen = self._browse_gen
        # Immediate button feedback that doubles as a re-click guard: DownIcon
        # refuses clicks while "running", and _download re-emits "running" when
        # the real job starts, so the state hands over seamlessly.
        self.downloadState.emit(media_id, "running")
        self._set_status("Fetching item…")

        def work() -> None:
            obj = None
            try:
                session = self.tidal.session
                fetch = {
                    "album": lambda: session.album(int(media_id)),
                    "track": lambda: session.track(int(media_id)),
                    "video": lambda: session.video(int(media_id)),
                    "playlist": lambda: session.playlist(media_id),
                    "mix": lambda: session.mix(media_id),
                }.get(bucket)
                if fetch is not None:
                    # Under the browse lock: session.mix() parses through the
                    # SHARED session.page instance (the same non-thread-safe
                    # parser _browse_fetch guards against); the other fetchers
                    # don't need it but holding it uniformly is harmless.
                    with self._browse_lock:
                        obj = fetch()
            except Exception:
                logger.exception("Could not re-fetch %s %s for download", bucket, media_id)
            if gen != self._browse_gen:
                # Account changed while fetching, don't start a download the
                # new user never asked for.
                self._refetch_inflight.discard(key)
                self.downloadState.emit(media_id, "")
                return
            if obj is None:
                self._refetch_inflight.discard(key)
                self.downloadState.emit(media_id, "failed")
                self._set_status("That item is no longer available")
                return
            self._remember(bucket, media_id, obj)
            self._mediaRefetched.emit(bucket, media_id)

        self.threadpool.start(Worker(work))

    def _on_media_refetched(self, bucket: str, media_id: str) -> None:
        # The in-flight marker lives until this GUI-thread dispatch, so a rapid
        # second click can't slip into the gap between the worker finishing and
        # the queued re-dispatch and double-queue the download.
        self._refetch_inflight.discard((bucket, media_id))
        dispatch = {
            "album": self.downloadAlbum,
            "track": self.downloadTrack,
            "video": self.downloadVideo,
            "playlist": self.downloadPlaylist,
            "mix": self.downloadMix,
        }.get(bucket)
        if dispatch is not None:
            dispatch(media_id)

    @Slot(str)
    def downloadTrack(self, track_id: str) -> None:
        obj = self._objs["track"].get(track_id)
        if obj is None:
            self._refetch_for_download("track", track_id)
            return
        self._download(obj, "track", name_builder_title(obj), self.settings.data.format_track, False, track_id)

    @Slot(str)
    def downloadAlbum(self, album_id: str) -> None:
        obj = self._objs["album"].get(album_id)
        if obj is None:
            self._refetch_for_download("album", album_id)
            return
        # A queued 'best of both' merge stashes its plan here; otherwise this
        # is a plain whole-album download. Peek (don't pop): the plan is only
        # dropped once the download SUCCEEDS (see _download), so a failed
        # merge can be retried as a merge instead of silently degrading to a
        # plain album (which could overwrite higher-quality tracks).
        plan = self._merge_plans.get(album_id)
        # With the merge preference on, a plain download-album click silently
        # runs the best-of-both scan first, no separate button. _merge_scanned
        # keeps the fallback re-queue (and discography keys, already merged
        # upstream) from scanning again.
        if (
            plan is None
            and album_id not in self._merge_scanned
            and self._waves_pref_bool("collapse_editions")
            and self._waves_prefs.get("edition_conflict") == "merge"
        ):
            self._merge_scanned.add(album_id)
            self.downloadAlbumBestOfBoth(album_id)
            return
        self._download(
            obj, "album", name_builder_title(obj), self.settings.data.format_album, True, album_id, merge_plan=plan
        )

    @Slot(str)
    def downloadAlbumBestOfBoth(self, album_id: str) -> None:
        """Download this album as a 'best of both': the most complete edition's
        track list, with each shared recording pulled from the highest-quality
        edition that has it. Falls back to a plain album download when there is no
        richer sibling edition to merge with."""
        obj = self._objs["album"].get(album_id)
        if obj is None or self._dl is None:
            return
        self._set_status("Scanning editions…")

        def work() -> None:
            group = self._sibling_editions(obj)
            identity, plan = (None, None)
            if len(group) >= 2:
                identity, plan = _build_merge_plan(group, self._merge_recs_factory(), _quality_rank)
            if plan:
                key = str(getattr(identity, "id", id(identity)))
                self._remember("album", key, identity)
                self._merge_plans[key] = plan
                self._albumsQueued.emit([key])
                self._set_status(f"Best of both: {name_builder_title(identity)}")
            else:
                self._albumsQueued.emit([album_id])
                self._set_status("No richer edition found; downloading this album")

        self.threadpool.start(Worker(work))

    @Slot(str)
    def downloadPlaylist(self, playlist_id: str) -> None:
        obj = self._objs["playlist"].get(playlist_id)
        if obj is None:
            self._refetch_for_download("playlist", playlist_id)
            return
        self._download(obj, "playlist", name_builder_title(obj), self.settings.data.format_playlist, True, playlist_id)

    @Slot(str)
    def downloadVideo(self, video_id: str) -> None:
        obj = self._objs["video"].get(video_id)
        if obj is None:
            self._refetch_for_download("video", video_id)
            return
        self._download(obj, "video", name_builder_title(obj), self.settings.data.format_video, False, video_id)

    @Slot(str)
    def downloadMix(self, mix_id: str) -> None:
        obj = self._objs["mix"].get(mix_id)
        if obj is None:
            self._refetch_for_download("mix", mix_id)
            return
        self._download(obj, "mix", name_builder_title(obj), self.settings.data.format_mix, True, mix_id)

    def _artist_releases(self, artist) -> tuple[list, list]:
        """Gather an artist's releases for a discography download, per the user's
        per-source toggles (all but appears-on default on): studio albums, EPs & singles,
        featured guest spots, and various-artists compilations. De-duplicated by
        release id (a release can show up under more than one source).

        Returns ``(own, guest)``: the artist's own releases are downloaded whole;
        guest releases (someone else's album the artist appears on) contribute
        only the artist's own tracks, never the full album.

        TIDAL exposes 'featured' and 'appears-on' as a single bucket (``get_other``
        / COMPILATIONS); we fetch it once and partition by the primary credit,
        a named artist → 'Featured', a Various-Artists placeholder → 'Appears on'."""
        own: list = []
        guest: list = []
        seen: set = set()

        def add(a, into: list) -> None:
            if not _is_album_entity(a):
                return  # albums / EPs / singles only, never playlists or mixes
            aid = str(getattr(a, "id", id(a)))
            if aid not in seen:
                seen.add(aid)
                into.append(a)

        # Sources that map one-to-one onto a tidalapi getter.
        for pref, name in (("disco_albums", "get_albums"), ("disco_eps", "get_ep_singles")):
            fn = getattr(artist, name, None)
            if fn is None or not self._waves_pref_bool(pref):
                continue
            try:
                for a in fn() or []:
                    add(a, own)
            except Exception:
                logger.exception("Could not load artist releases for %s", pref)

        # The shared 'other' bucket, split into featured (named artist) vs
        # appears-on (various-artists compilation) by the primary credit.
        want_featured = self._waves_pref_bool("disco_featured")
        want_appears = self._waves_pref_bool("disco_appears_on")
        if want_featured or want_appears:
            fn = getattr(artist, "get_other", None)
            try:
                others = (fn() if fn else []) or []
            except Exception:
                logger.exception("Could not load artist releases for appears-on")
                others = []
            for a in others:
                is_comp = _is_compilation_release(a)
                if want_appears if is_comp else want_featured:
                    add(a, guest)

        devlog.event("artist_releases", own=len(own), guest=len(guest))
        return own, guest

    @Slot(str)
    def downloadArtist(self, artist_id: str) -> None:
        """Queue every album of an artist for download."""
        artist = self._get_artist(artist_id)
        if artist is None or self._dl is None:
            return
        self._set_status("Loading artist discography…")
        self.downloadProgress.emit(artist_id, 0.0)
        self.downloadState.emit(artist_id, "running")

        def work() -> None:
            albums, guest = self._artist_releases(artist)
            deduped = self._dedup_albums(albums)
            plans: list = []
            if self._waves_pref_bool("collapse_editions"):
                self._set_status("Scanning editions…")
                if self._waves_prefs.get("edition_conflict") == "merge":
                    deduped, plans = self._merge_editions(deduped)
                else:
                    deduped = self._collapse_editions(deduped)
            keys: list[str] = []
            for album in deduped:
                key = str(getattr(album, "id", id(album)))
                self._remember("album", key, album)
                keys.append(key)
            # Queue each best-of-both merge under its complete edition's key;
            # downloadAlbum() picks the stashed plan back up.
            for identity, plan in plans:
                key = str(getattr(identity, "id", id(identity)))
                self._remember("album", key, identity)
                self._merge_plans[key] = plan
                keys.append(key)
            # Guest releases (featured / appears-on): pull only the tracks the
            # artist is actually credited on, never the whole other-artist album.
            track_keys: list[str] = []
            if guest:
                self._set_status("Scanning guest appearances…")
                gtracks: list = []
                for rel in guest:
                    try:
                        for t in rel.tracks():
                            if _artist_on_track(t, artist_id):
                                gtracks.append(t)
                    except Exception:
                        logger.exception("Could not load tracks for a guest release")
                for t in self._dedup_tracks(gtracks):
                    tkey = str(getattr(t, "id", id(t)))
                    self._remember("track", tkey, t)
                    track_keys.append(tkey)
                devlog.event("guest_tracks", releases=len(guest), tracks=len(track_keys))
            if not keys and not track_keys:
                self.downloadState.emit(artist_id, "")
                self._set_status("No albums to download")
                return
            # Register an aggregate group BEFORE queueing so each album's (and
            # guest track's) progress/completion rolls up into the artist
            # button's bar (see _bump_artist_group); it flips to done when all
            # members finish.
            with self._artist_lock:
                self._artist_groups[artist_id] = {
                    "keys": set(keys) | set(track_keys),
                    "done": set(),
                    "failed": set(),
                    "prog": {},
                }
            self.downloadProgress.emit(artist_id, 0.0)
            self.downloadState.emit(artist_id, "running")
            # One batch emit → all albums enqueued together on the GUI thread
            # (keeps each album's progress relay GUI-affine and avoids the queue
            # appearing to jump 0 → N as albums trickle in one at a time).
            if keys:
                # Edition handling already ran above; exempt these from
                # downloadAlbum's automatic best-of-both scan.
                self._merge_scanned.update(keys)
                self._albumsQueued.emit(keys)
            if track_keys:
                self._tracksQueued.emit(track_keys)
            parts = []
            if keys:
                parts.append(f"{len(keys)} albums")
            if track_keys:
                parts.append(f"{len(track_keys)} guest tracks")
            self._set_status("Downloading " + " + ".join(parts) + "…")

        self.threadpool.start(Worker(work))

    @Slot()
    def stopAll(self) -> None:
        """Hard-stop: abort every running/queued download and empty the queue."""
        for ev in list(self._job_aborts.values()):
            ev.set()
        # Wake any paused workers so they reach the abort check, and un-pause.
        self._event_run.set()
        if self._paused:
            self._paused = False
            self.pausedChanged.emit()
        # Reset every media button (album/track/etc.) back to idle so nothing is
        # left showing a stale progress bar.
        for it in self._queue:
            mid = str(it.get("media_id", ""))
            if mid:
                self.downloadState.emit(mid, "")
        # Drop artist-discography aggregates and reset their buttons too.
        with self._artist_lock:
            artist_ids = list(self._artist_groups.keys())
            self._artist_groups.clear()
        for aid in artist_ids:
            self.downloadState.emit(aid, "")
        self._queue = []
        self._emit_queue()
        self._set_status("Downloads stopped")

    def shutdown(self) -> None:
        """Abort downloads and drain the worker pools so the app can exit.

        Wired to ``QGuiApplication.aboutToQuit``. Without it, quitting blocks in
        the ``QThreadPool`` destructors' ``waitForDone()`` on a worker parked in
        a network read, so the window hangs and has to be force-quit. We signal
        every abort event (segment loops check it per chunk, see
        ``download._download_segment``), drop work that has not started, then
        wait a bounded moment for in-flight jobs to unwind."""
        try:
            if getattr(self, "_event_abort", None) is not None:
                self._event_abort.set()
        except Exception:
            logger.debug("shutdown: no global abort event", exc_info=True)
        for ev in list(self._job_aborts.values()):
            ev.set()
        if getattr(self, "_event_run", None) is not None:
            self._event_run.set()  # release any paused worker so it hits the abort
        for pool in (self.dl_pool, self.threadpool):
            pool.clear()
        self.dl_pool.waitForDone(4000)
        self.threadpool.waitForDone(1000)

    # ----- in-app FFmpeg manager ----------------------------------------- #
    def _restore_ffmpeg_flags(self) -> None:
        """Re-enable video/FLAC features that ``Download`` may have disabled
        in-memory when ffmpeg was missing, now that it's installed."""
        for key, value in self._ffmpeg_flag_prefs.items():
            setattr(self.settings.data, key, value)

    def _restore_ffmpeg_path(self) -> None:
        """Undo the in-memory ffmpeg path injection before a settings.save().

        ``_resolve_ffmpeg`` writes the *managed* binary path into
        ``settings.data.path_binary_ffmpeg`` so ``Download`` can find it, but the
        contract is that this key is transient, only a genuine user override is
        ever persisted. ``settings.save()`` serialises the whole data object, so
        this must run first (mirroring ``_restore_ffmpeg_flags``) to keep the
        managed path off disk. The user's real value is the startup snapshot,
        already refreshed from the edit map in applySettings."""
        self.settings.data.path_binary_ffmpeg = self._ffmpeg_user_path

    @Slot(result="QVariant")
    def ffmpegStatus(self) -> dict:
        # Pass the user's *explicit* override (if any) so a linked binary that
        # isn't on $PATH still reports as available (unmanaged → yellow).
        return self._ffmpeg.status(self._user_ffmpeg_path())

    @Slot()
    def checkFfmpegUpdate(self) -> None:
        def work() -> None:
            try:
                available, current, latest = self._ffmpeg.update_available()
            except Exception:
                logger.debug("ffmpeg update check failed", exc_info=True)
                self.ffmpegUpdateChecked.emit(False, "", "")
                return
            self.ffmpegUpdateChecked.emit(bool(available), current, latest)

        self.threadpool.start(Worker(work))

    @Slot()
    def installFfmpeg(self) -> None:
        """Download (or update) the managed ffmpeg on a worker thread."""

        def work() -> None:
            self._ffmpeg_abort.clear()
            self.ffmpegStateChanged.emit("downloading", "Downloading FFmpeg…")
            try:
                status = self._ffmpeg.install(
                    progress_cb=lambda p: self.ffmpegProgress.emit(float(p)),
                    log_cb=lambda m: self.ffmpegStateChanged.emit("downloading", m),
                    abort=self._ffmpeg_abort,
                )
            except FfmpegCancelled:
                self.ffmpegStateChanged.emit("cancelled", "Cancelled")
                self.ffmpegStatusChanged.emit()
                return
            except Exception as exc:
                logger.exception("FFmpeg install failed")
                self.ffmpegStateChanged.emit("failed", str(exc) or "Install failed")
                return
            # ffmpeg is available now, undo any in-memory feature disabling and
            # rebuild the Download so the new binary is used immediately.
            self._restore_ffmpeg_flags()
            if self._logged_in:
                self._init_download()
            self.ffmpegStateChanged.emit("done", f"FFmpeg {status.get('version', '')} ready")
            self.ffmpegStatusChanged.emit()

        self.threadpool.start(Worker(work))

    @Slot()
    def cancelFfmpeg(self) -> None:
        self._ffmpeg_abort.set()

    @Slot()
    def removeFfmpeg(self) -> None:
        self._ffmpeg.remove()
        # The managed binary is gone; a prior _resolve_ffmpeg may have injected
        # its (now dangling) path in-memory. Reset the live value to the user's
        # real override (empty when none), so downloads/previews don't keep
        # spawning a deleted executable, then rebuild Download without it (which
        # also re-gates the ffmpeg-dependent flags via its own construction).
        self._restore_ffmpeg_path()
        if self._logged_in:
            self._init_download()
        self.ffmpegStatusChanged.emit()

    # ----- in-app updater ----------------------------------------------- #
    @Slot(result="QVariant")
    def appUpdateStatus(self) -> dict:
        return self._updater.status()

    @Slot()
    def checkAppUpdate(self) -> None:
        """User- or startup-initiated check. Best-effort, off the GUI thread;
        emits ``appUpdateChecked``. Never downloads, a found update is only
        surfaced as a badge until the user clicks Install."""

        def work() -> None:
            try:
                available, current, latest = self._updater.update_available()
            except Exception:
                logger.debug("app update check failed", exc_info=True)
                self.appUpdateChecked.emit(False, "", "")
                return
            self.appUpdateChecked.emit(bool(available), current, latest)

        self.threadpool.start(Worker(work))

    @Slot()
    def startupUpdateCheck(self) -> None:
        """Throttled, opt-in check fired once from QML at startup. No-ops unless
        ``auto_update`` is on; with the ``daily`` cadence it also skips if the
        last check was under 24h ago. This is the only automatic outbound
        request the app ever makes, and only when the user has enabled it."""
        if not self._waves_pref_bool("auto_update") or not self._updater.is_configured():
            return
        cadence = self._waves_prefs.get("update_cadence", "daily")
        try:
            last = int(self._waves_prefs.get("update_last_check", 0))
        except (TypeError, ValueError):
            last = 0
        now = int(time.time())
        if cadence == "daily" and (now - last) < 86400:
            return
        # Stamp before firing so a slow check can't double-trigger.
        self._waves_prefs["update_last_check"] = now
        self._save_waves_prefs()
        self.checkAppUpdate()

    @Slot()
    def installAppUpdate(self) -> None:
        """Download, verify and stage the newest build on a worker thread. On
        success the UI offers a restart (see ``restartForUpdate``)."""

        def work() -> None:
            self._app_update_abort.clear()
            self.appUpdateStateChanged.emit("downloading", "Downloading update…")
            try:
                result = self._updater.install(
                    progress_cb=lambda p: self.appUpdateProgress.emit(float(p)),
                    log_cb=lambda m: self.appUpdateStateChanged.emit("downloading", m),
                    abort=self._app_update_abort,
                )
            except UpdateCancelled:
                self.appUpdateStateChanged.emit("cancelled", "Cancelled")
                return
            except Exception as exc:
                logger.exception("App update failed")
                self.appUpdateStateChanged.emit("failed", str(exc) or "Update failed")
                return
            self.appUpdateStateChanged.emit("done", f"Updated to {result.get('version', '')}. Restart to finish.")
            self.appUpdateStatusChanged.emit()

        self.threadpool.start(Worker(work))

    @Slot()
    def cancelAppUpdate(self) -> None:
        self._app_update_abort.set()

    @Slot()
    def restartForUpdate(self) -> None:
        """Relaunch into the freshly-installed build. On non-Windows we exec the
        new binary in place; on Windows the detached helper swaps + relaunches
        after we exit."""
        try:
            self.shutdown()
        except Exception:
            logger.debug("shutdown before relaunch failed", exc_info=True)
        if self._updater.os_key != "windows":
            self._updater.relaunch()  # os.execv replaces this process
        QtGui.QGuiApplication.quit()

    @Slot()
    def openReleasesPage(self) -> None:
        url = self._updater.releases_url()
        if url:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    @Slot(int)
    def cancelQueueItem(self, qid: int) -> None:
        """Cancel one download (running or queued) and drop it from the queue."""
        ev = self._job_aborts.get(qid)
        if ev is not None:
            # Set only this job's abort gate. Do NOT set the global _event_run
            # here: while paused it would resume EVERY other worker (they park in
            # event_run.wait()) yet leave _paused True and the UI showing paused.
            # The per-job abort is honoured by the timeout-aware event_run.wait()
            # (download.py), which wakes the parked worker to see its abort even
            # while the global gate stays cleared, so the whole queue stays paused.
            ev.set()
        item = self._queue_item(qid)
        if item is not None:
            self.downloadState.emit(str(item.get("media_id", "")), "")
        self._queue = [q for q in self._queue if q["qid"] != qid]
        self._emit_queue()

    @Slot()
    def clearFinished(self) -> None:
        self._queue = [q for q in self._queue if q["status"] not in {"done", "failed", "cancelled"}]
        self._emit_queue()

    @Slot()
    def clearQueue(self) -> None:
        """Clear the whole list (running downloads keep going in the background)."""
        # A 'queued' row may already have a Worker submitted to dl_pool that
        # hasn't started; dropping the row without aborting it would let it
        # download invisibly. Abort every removed non-running item so its pooled
        # Worker early-returns (see _download.work()'s pre-start abort check).
        for q in self._queue:
            if q["status"] != "running":
                ev = self._job_aborts.get(q["qid"])
                if ev is not None:
                    ev.set()
        self._queue = [q for q in self._queue if q["status"] == "running"]
        self._emit_queue()

    @Slot(int)
    def removeQueueItem(self, qid: int) -> None:
        self._queue = [q for q in self._queue if q["qid"] != qid]
        self._emit_queue()

    @Slot(int)
    def retryQueueItem(self, qid: int) -> None:
        item = self._queue_item(qid)
        if item is None or item["status"] != "failed":
            return
        obj = self._objs.get(item["type"], {}).get(item["media_id"])
        if obj is None:
            return
        self._queue = [q for q in self._queue if q["qid"] != qid]
        self._emit_queue()
        # Preserve a failed 'best of both' merge as a merge on retry, its plan
        # is kept stashed (only dropped on success), so a retried album isn't
        # silently degraded to a plain download.
        plan = self._merge_plans.get(item["media_id"]) if item["type"] == "album" else None
        self._download(
            obj, item["type"], item["name"], item["template"], item["collection"], item["media_id"], merge_plan=plan
        )

    @Slot(str, str)
    def copyShareUrl(self, bucket: str, media_id: str) -> None:
        obj = self._objs.get(bucket, {}).get(media_id)
        if obj is None:
            return
        url = getattr(obj, "share_url", "") or ""
        if not url and hasattr(obj, "get_url"):
            try:
                url = obj.get_url() or ""
            except Exception:
                url = ""
        if url:
            QtGui.QGuiApplication.clipboard().setText(url)
            self._set_status("Link copied")

    def _get_paused(self) -> bool:
        return self._paused

    paused = Property(bool, _get_paused, notify=pausedChanged)

    def _get_edition_merge(self) -> bool:
        return self._waves_prefs.get("edition_conflict") == "merge"

    editionMergeEnabled = Property(bool, _get_edition_merge, notify=editionMergeChanged)

    @Slot()
    def pauseQueue(self) -> None:
        self._event_run.clear()
        self._paused = True
        self.pausedChanged.emit()
        self._set_status("Downloads paused")

    @Slot()
    def resumeQueue(self) -> None:
        self._event_run.set()
        self._paused = False
        self.pausedChanged.emit()
        self._set_status("Downloads resumed")

    # ----- settings ------------------------------------------------------

    def _help_for(self, key: str) -> str:
        # Pull the upstream help text, normalising any em dash to plain
        # punctuation so the settings descriptions read consistently.
        return str(getattr(self._help, key, "") or "").replace(", ", "; ")

    @Slot(result="QVariant")
    def settingsSchema(self) -> list:
        """Settings for the QML page, arranged into task-based, collapsible
        sections rather than raw tidaler field types.

        Each group carries ``id``/``open``/``desc`` for the collapsible UI, and
        ``card: "ffmpeg"`` injects the FFmpeg manager card at the top of that
        section. Per-field hints (``requires_ffmpeg``, ``depends_on`` +
        ``depends_on_value``) let the page grey-out or hide a control without
        hard-coding key names in QML.
        """
        d = self.settings.data

        def field(key: str, ftype: str, value, extra: dict | None = None) -> dict:
            out = {
                "key": key,
                "label": _FIELD_LABELS.get(key) or _pretty(key),
                "help": self._help_for(key),
                "type": ftype,
                "value": value,
            }
            if extra:
                out.update(extra)
            return out

        def auto_field(key: str) -> dict:
            """Build a field dict for a tidaler ``Settings`` key, choosing the
            control type from the registries above."""
            if key in _ENUM_BY_FIELD:
                enum = _ENUM_BY_FIELD[key]
                current = getattr(d, key)
                return field(key, "enum", getattr(current, "name", str(current)), {"options": _enum_options(key, enum)})
            if key in _FLOAT_FIELDS:
                return field(
                    key,
                    "float",
                    float(getattr(d, key)),
                    {"minimum": 0, "maximum": 60, "step": 0.5, "decimals": 1},
                )
            if key in _NUMBER_FIELDS:
                return field(key, "int", int(getattr(d, key)))
            if key in _FLAG_FIELDS:
                return field(key, "bool", bool(getattr(d, key)))
            return field(key, "str", str(getattr(d, key)), {"browse": _BROWSE.get(key, "")})

        # Waves-only prefs (stored in waves.json) keep their hand-written labels
        # and help; indexed by key so sections can pick them in any order.
        waves_fields = {
            f["key"]: f
            for f in [
                {
                    "key": "explicit_mode",
                    "label": "Explicit versions",
                    "help": (
                        "When an album or track exists as both explicit and clean: 'explicit' keeps the explicit "
                        "version, 'clean' keeps the censored one, 'both' keeps both. Applies to search results "
                        "and downloads."
                    ),
                    "type": "enum",
                    "value": self._waves_prefs.get("explicit_mode", "explicit"),
                    "options": _enum_options("explicit_mode", ["explicit", "clean", "both"]),
                },
                {
                    "key": "collapse_editions",
                    "label": "Most-complete edition only",
                    "help": (
                        "On 'Download discography', download only the most complete edition of each album "
                        "(e.g. Deluxe or Complete) instead of every edition. Remasters, re-releases, "
                        "anniversary/special editions and live/acoustic versions are always kept separately."
                    ),
                    "type": "bool",
                    "value": self._waves_pref_bool("collapse_editions"),
                },
                {
                    "key": "edition_conflict",
                    "label": "If the complete edition is lower quality",
                    "help": (
                        "Used by 'Most-complete edition only' when the most complete edition is a lower audio "
                        "quality than a smaller one: 'Keep both' downloads both, 'Most complete' keeps the most "
                        "complete, 'Highest quality' keeps the highest quality, and 'Best of both' builds one album "
                        "from the complete edition's track list with each shared song pulled at the highest quality "
                        "available (the exclusive bonus tracks stay at the complete edition's quality)."
                    ),
                    "type": "enum",
                    "value": self._waves_prefs.get("edition_conflict", "keep_both"),
                    "options": _enum_options("edition_conflict", ["keep_both", "completeness", "quality", "merge"]),
                },
                {
                    "key": "clean_album_artist",
                    "label": "Clean album-artist tag",
                    "help": (
                        "Write only the primary artist to the album-artist metadata field instead of every "
                        "credited album artist. Important for Plex, which doesn't read a multi-artist album-artist "
                        "field correctly and can split or misfile albums. Affects the metadata tag only; folder "
                        "names are unchanged."
                    ),
                    "type": "bool",
                    "value": self._waves_pref_bool("clean_album_artist"),
                },
                {
                    "key": "disco_albums",
                    "label": "Albums",
                    "help": "Studio albums and the artist's own compilations (e.g. greatest-hits).",
                    "type": "bool",
                    "value": self._waves_pref_bool("disco_albums"),
                },
                {
                    "key": "disco_eps",
                    "label": "EPs & singles",
                    "help": "The artist's own EPs and singles.",
                    "type": "bool",
                    "value": self._waves_pref_bool("disco_eps"),
                },
                {
                    "key": "disco_featured",
                    "label": "Featured on",
                    "help": (
                        "Other artists' releases the artist is a featured guest on (e.g. a duet or a "
                        "guest verse); only the tracks the artist appears on are downloaded, not the "
                        "whole release."
                    ),
                    "type": "bool",
                    "value": self._waves_pref_bool("disco_featured"),
                },
                {
                    "key": "disco_appears_on",
                    "label": "Appears on",
                    "help": (
                        "Various-artists compilations and soundtracks the artist appears on; only "
                        "the tracks the artist appears on are downloaded, not the whole release."
                    ),
                    "type": "bool",
                    "value": self._waves_pref_bool("disco_appears_on"),
                },
                {
                    "key": "motion_background",
                    "label": "Motion background",
                    "help": (
                        "Show the slow ocean loop behind the interface. Turning it off stops video "
                        "playback entirely and keeps a flat background (saves a little battery)."
                    ),
                    "type": "bool",
                    "value": self._waves_pref_bool("motion_background"),
                },
                {
                    "key": "auto_update",
                    "label": "Check for updates automatically",
                    "help": (
                        "Off by default. When on, Waves checks the releases page for a newer version "
                        "(at launch or once a day) and only notifies you; nothing downloads until you "
                        "click Update. The check sends none of your data."
                    ),
                    "type": "bool",
                    "value": self._waves_pref_bool("auto_update"),
                },
                {
                    "key": "update_cadence",
                    "label": "How often to check",
                    "help": "Run the automatic check on every launch, or at most once a day.",
                    "type": "enum",
                    "value": self._waves_prefs.get("update_cadence", "daily"),
                    "options": _enum_options("update_cadence", ["launch", "daily"]),
                },
            ]
        }

        def get_field(key: str) -> dict:
            f = dict(waves_fields[key]) if key in waves_fields else auto_field(key)
            if key in ("auto_update", "update_cadence"):
                # Rendered inside the updater card (toggle + cadence segment),
                # not as the generic tile/row controls.
                f["embedded"] = True
            if key in _FFMPEG_DEPENDENT:
                f["requires_ffmpeg"] = True
                # Report the user's *real* preference, not the in-memory value
                # Download force-disables while ffmpeg is missing, the page
                # greys the toggle (requires_ffmpeg) and animates it back to this
                # value once ffmpeg arrives, with no schema rebuild.
                f["value"] = bool(self._ffmpeg_flag_prefs.get(key, f.get("value", False)))
            if key == "path_binary_ffmpeg":
                # Surface a genuine user override first. With none set, prefill
                # the binary detected on the system PATH so the box shows what
                # Waves is actually using (and Browse opens beside it); the box
                # is empty only when nothing is detected. The managed copy is
                # never shown here, it has its own card above, and this stays a
                # display prefill: nothing persists unless the user edits/saves.
                val = self._user_ffmpeg_path()
                if not val:
                    try:
                        st = self._ffmpeg.status(val)
                        if st.get("state") == "path":
                            val = str(st.get("path") or "")
                    except Exception:
                        logger.debug("Could not probe ffmpeg for the settings prefill", exc_info=True)
                f["value"] = val
                f["label"] = "Or link your own FFmpeg"
                f["help"] = (
                    "Point Waves at an FFmpeg binary you already have instead of the managed copy. "
                    "Leave empty to use the managed copy above, or one found on your system PATH."
                )
            if key == "edition_conflict":
                f["depends_on"] = "collapse_editions"
                f["depends_on_value"] = self._waves_pref_bool("collapse_editions")
            elif key == "update_cadence":
                f["depends_on"] = "auto_update"
                f["depends_on_value"] = self._waves_pref_bool("auto_update")
            elif key == "downsample_target":
                f["depends_on"] = "downsample_enabled"
                f["depends_on_value"] = bool(d.downsample_enabled)
            return f

        sections = [
            {
                "group": "Downloads",
                "id": "downloads",
                "open": True,
                "desc": "Where your music is saved and how good it sounds.",
                "fields": [
                    "download_base_path",
                    "quality_audio",
                    "quality_video",
                    "downloads_concurrent_max",
                    "video_download",
                    "download_dolby_atmos",
                    "skip_existing",
                    "download_delay",
                ],
            },
            {
                "group": "File organization",
                "id": "files",
                "desc": "Folder layout, file-name templates and how multiple artists are joined.",
                "fields": [
                    "format_track",
                    "format_album",
                    "format_playlist",
                    "format_video",
                    "format_mix",
                    "album_track_num_pad_min",
                    "filename_delimiter_artist",
                    "filename_delimiter_album_artist",
                    "use_primary_album_artist",
                    "symlink_to_track",
                    "playlist_create",
                ],
            },
            {
                "group": "Metadata & artwork",
                "id": "metadata",
                "desc": "Tags, cover art and lyrics written into your files.",
                "fields": [
                    "metadata_cover_dimension",
                    "metadata_cover_embed",
                    "cover_album_file",
                    "lyrics_embed",
                    "lyrics_file",
                    "mark_explicit",
                    "clean_album_artist",
                ],
            },
            {
                "group": "Processing (FFmpeg)",
                "id": "processing",
                "card": "ffmpeg",
                "desc": "Post-processing that relies on the FFmpeg tool below.",
                # path_binary_ffmpeg is a str field → renders as a labelled box
                # with a Browse… button right under the card (before the bool
                # toggles), so linking your own binary lives beside its status.
                "fields": ["path_binary_ffmpeg", "video_convert_mp4", "extract_flac"],
            },
            {
                "group": "Discography & editions",
                "id": "discography",
                "desc": "What 'Download discography' pulls in, and how duplicate editions are resolved.",
                "fields": [
                    "explicit_mode",
                    "edition_conflict",
                    "disco_albums",
                    "disco_eps",
                    "disco_featured",
                    "disco_appears_on",
                    "collapse_editions",
                ],
            },
            {
                "group": "Updates",
                "id": "updates",
                "card": "updates",
                "desc": "Keep Waves current. Checks are off by default and never send any of your data.",
                "fields": ["auto_update", "update_cadence"],
            },
            {
                "group": "Advanced",
                "id": "advanced",
                "desc": "Power-user knobs. The defaults are right for almost everyone.",
                "fields": [
                    "motion_background",
                    "downsample_target",
                    "downloads_simultaneous_per_track_max",
                    "download_delay_sec_min",
                    "download_delay_sec_max",
                    "metadata_target_upc",
                    "initial_key_format",
                    "api_rate_limit_batch_size",
                    "api_rate_limit_delay_sec",
                    "downsample_enabled",
                    "metadata_replay_gain",
                    "metadata_write_url",
                ],
            },
        ]
        for sec in sections:
            sec["fields"] = [get_field(k) for k in sec["fields"]]
        return sections

    @Slot("QVariant")
    def applySettings(self, values) -> None:
        """Apply only the changed keys from the settings page, then persist."""
        t0 = devlog.clock()
        # QML passes the edit map as a QJSValue, which dict() can't iterate.
        if hasattr(values, "toVariant"):
            values = values.toVariant()
        values = dict(values or {})
        data = self.settings.data
        for key, value in values.items():
            if key in self._waves_prefs:
                self.setWavesPref(key, value)
                continue
            if not hasattr(data, key):
                continue
            try:
                if key in _ENUM_BY_FIELD:
                    setattr(data, key, _ENUM_BY_FIELD[key][value])
                elif key in _FLOAT_FIELDS:
                    setattr(data, key, float(value))
                elif key in _NUMBER_FIELDS:
                    setattr(data, key, int(value))
                elif key in _FLAG_FIELDS:
                    setattr(data, key, bool(value))
                    # Track the user's real preference for ffmpeg-gated toggles
                    # so a later force-disable can be undone to the right value.
                    if key in self._ffmpeg_flag_prefs:
                        self._ffmpeg_flag_prefs[key] = bool(value)
                else:
                    setattr(data, key, str(value))
            except Exception:
                logger.exception("Could not set setting %s", key)
        # Refresh the explicit-override snapshot if the user edited their path, so
        # status + the path box reflect the new choice (never a transient
        # in-memory injection), and so the restore below sees current ffmpeg.
        if "path_binary_ffmpeg" in values:
            self._ffmpeg_user_path = str(values.get("path_binary_ffmpeg") or "").strip()
        # An explicit Video-quality choice overrides the bandwidth auto-cap for
        # the rest of the run (and persists like any other setting).
        if "quality_video" in values:
            self._video_user_quality = True
        # The gated flags (video_convert_mp4 / extract_flac) get force-disabled in
        # memory by Download when ffmpeg is absent; persist the user's *real*
        # preference (tracked in _ffmpeg_flag_prefs), not that transient value, so
        # it survives a relaunch. MUST run before save(), restoring afterward
        # would write the force-disabled value to disk and lose the preference.
        self._restore_ffmpeg_flags()
        # Same transient-injection trap for the ffmpeg *path*: _resolve_ffmpeg
        # injects the managed binary path in-memory, and save() would serialise
        # it. Restore the user's real value (empty or their own override) first;
        # _init_download() below re-injects the managed path if still needed.
        self._restore_ffmpeg_path()
        self.settings.save()
        # Keep the album-artist metadata filter in sync with its pref.
        _set_clean_album_artist(self._waves_pref_bool("clean_album_artist"))
        # Let the album page re-evaluate whether to offer the merge action.
        self.editionMergeChanged.emit()
        # If the user linked/cleared their own ffmpeg path, tell the UI to re-read
        # status so the glyph + toggles update live (no reopen needed).
        if "path_binary_ffmpeg" in values:
            self.ffmpegStatusChanged.emit()
        # Resize the download pool to the (possibly changed) concurrency cap; it
        # was only sized once at startup, so a saved change had no effect before.
        self.dl_pool.setMaxThreadCount(max(2, int(self.settings.data.downloads_concurrent_max or 3)))
        # Quality / path / ffmpeg changes only take effect on a fresh Download.
        if self._logged_in:
            self._init_download()
        self._set_status("Settings saved")
        devlog.done("save", f"{len(values)} keys", devlog.clock() - t0, keys=",".join(values))

    @Slot(str, str, float)
    def uiLog(self, category: str, message: str, ms: float = -1.0) -> None:
        """Logging hook for the QML layer. ``ms`` >= 0 is treated as a measured
        duration (e.g. click-to-rendered-frame for a section switch); a negative
        value logs a point-in-time event. Routes into the same dev log so UI and
        backend timings interleave on one timeline."""
        if ms is not None and ms >= 0:
            devlog.done(category, message, ms / 1000.0)
        else:
            devlog.event(category, message)
