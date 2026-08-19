"""
download.py

Implements the Download class and helpers for downloading media from TIDAL, including segment merging, file moving, metadata writing, and playlist creation.

Classes:
    RequestsClient: Simple HTTP client for downloading text content.
    Download: Main class for managing downloads, segment merging, file operations, and metadata.
"""

import contextlib
import logging
import os
import pathlib
import random
import shutil
import tempfile
import time
from collections.abc import Callable
from concurrent import futures
from threading import Event, Lock
from uuid import uuid4

import certifi
import m3u8
import requests
from ffmpeg import FFmpeg
from mutagen.flac import FLAC
from pathvalidate import sanitize_filename
from requests.adapters import HTTPAdapter, Retry
from requests.exceptions import HTTPError
from rich.progress import Progress, TaskID
from tidalapi import Album, Mix, Playlist, Session, Track, UserPlaylist, Video
from tidalapi.exceptions import AssetNotAvailable, ObjectNotFound, StreamNotAvailable, TooManyRequests
from tidalapi.media import (
    AudioExtensions,
    AudioMode,
    Codec,
    Quality,
    Stream,
    StreamManifest,
    VideoExtensions,
)
from urllib3.util.ssl_ import create_urllib3_context

from tidaler.config import Settings, Tidal
from tidaler.constants import (
    CHUNK_SIZE,
    COVER_NAME,
    EXTENSION_LYRICS,
    FILENAME_LENGTH_MAX,
    METADATA_EXPLICIT,
    METADATA_LOOKUP_UPC,
    PLAYLIST_EXTENSION,
    PLAYLIST_EXTENSION_LEGACY,
    PLAYLIST_PREFIX,
    REQUESTS_TIMEOUT_SEC,
    UNIQUIFY_THRESHOLD,
    AudioExtensionsValid,
    CoverDimensions,
    DownsampleTarget,
    MediaType,
    MetadataTargetUPC,
    QualityVideo,
)
from tidaler.helper.camelot import format_initial_key
from tidaler.helper.exceptions import MediaMissing
from tidaler.helper.path import (
    PATH_LENGTH_MAX,
    check_file_exists,
    format_path_media,
    name_comparison_key,
    path_file_sanitize,
    path_file_uniquify,
    safe_filename_replacement,
    safe_filename_replacement_map,
    sanitize_name_component,
    strip_apple_double,
    truncate_to_byte_limit,
    unique_variant_name,
    url_to_filename,
)
from tidaler.helper.tidal import (
    get_album_artists,
    instantiate_media,
    items_results_all,
    name_builder_item,
    name_builder_title,
)
from tidaler.lyrics import fetch_lrclib_lyrics, lyrics_file_choice
from tidaler.metadata import Metadata, MetadataUnreadable, read_item_id
from tidaler.model.downloader import DownloadSegmentResult, TrackStreamInfo
from tidaler.model.gui_data import ProgressBars
from tidaler.waves_ui.diagnostics import content as log_content
from tidaler.waves_ui.manifest import overgenerated_tail_urls

# Characters _stage_and_swap adds around the destination name: a leading dot,
# a dot-separated uuid4 (36) and the ".tmp" suffix.
_STAGING_NAME_OVERHEAD: int = len(f"..{uuid4()}.tmp")

# The platform's cap on a WHOLE path, one under the documented maximum so the
# terminating NUL it includes is never the difference (MAX_PATH 260 on Windows,
# PATH_MAX 1024 on macOS). Windows measures UTF-16 units and POSIX measures
# bytes; the staging budget below measures the parent in fsencoded bytes, which
# is never smaller than either unit, so a name that fits the byte arithmetic
# fits the real cap too. Linux allows 4096, but a staging name has no use for
# the headroom: past this budget only the throwaway readable part shrinks.
# One number with the sanitizer's own cap (helper.path.PATH_LENGTH_MAX), so
# the path a destination is approved against and the path its staging sibling
# is budgeted against can never drift apart.
_PATH_LENGTH_MAX: int = PATH_LENGTH_MAX

# Child of "waves", so it inherits the app's handlers and its INFO records join
# the always-on breadcrumb ring crash reports are stitched from.
logger = logging.getLogger("waves.download")

# TIDAL's subStatus family for "your session, not the content": 11001 user not
# authorised, 11002 invalid token, 11003 expired token. A 401 carrying one of
# these is a login problem and must never be read as "this track is gone".
_TIDAL_SUBSTATUS_AUTH_MIN: int = 11000
_TIDAL_SUBSTATUS_AUTH_MAX: int = 11999


def _tidal_refuses_asset(error: HTTPError) -> str | None:
    """TIDAL's own words when it refuses to serve an item, or None if this is
    something else (a network hiccup, a dead session, a server error).

    The playback-info endpoint answers a track the account cannot play with a
    401 or 403 whose body says why (observed: ``subStatus 4005 "Asset is not
    ready for playback"`` for tracks greyed out in the official apps). The
    same 401 status also announces an expired or invalid token, so the body is
    the only way to tell "TIDAL will not give you this track" from "TIDAL does
    not know who you are"; tidalapi already retried the expired-token case
    once with a refresh, so what reaches here is whatever survived that.

    Args:
        error (HTTPError): The requests error raised for the playback request.

    Returns:
        str | None: TIDAL's user message (or a generic one) when this is a
            refusal of the asset itself, None otherwise.
    """
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if status not in (401, 403):
        return None
    body: dict = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:  # noqa: S110  # a body that is not JSON is still a refusal
        pass
    message = str(body.get("userMessage") or "")
    if message.startswith("The token has expired"):
        return None
    sub_status = body.get("subStatus")
    if isinstance(sub_status, int) and _TIDAL_SUBSTATUS_AUTH_MIN <= sub_status <= _TIDAL_SUBSTATUS_AUTH_MAX:
        return None
    return message or f"HTTP {status}"


def _staging_path(path_destination: pathlib.Path) -> pathlib.Path:
    """The hidden temp sibling a destination is copied through before the swap.

    The staging decoration (dot prefix, uuid, ".tmp") adds 42 characters to a
    destination name that is itself allowed to reach the filesystem's 255 cap,
    so a long track name would make every stage attempt raise ENAMETOOLONG
    deterministically (all retries fail the same way). Truncate the readable
    part; the uuid alone carries the uniqueness.

    The cap is bytes, not characters. Counting characters passed a name in CJK,
    Cyrillic or emoji straight through at three or four bytes each, so the very
    names most likely to be long were the ones that failed.

    The WHOLE staging path is capped as well, not just its name (issue #17).
    The sanitizer bounds the final path against the platform limit (260 on
    Windows), but the staging decoration adds 42 more characters that were
    never budgeted: a final path that fit by less than that put every staging
    attempt past MAX_PATH, Windows answered "no such file or directory", and
    the longest-named tracks of an album failed every retry identically. The
    final name is untouched either way; only the throwaway readable part of
    the staging name shrinks, down to nothing if the parent is deep enough.

    Args:
        path_destination (pathlib.Path): The final destination path.

    Returns:
        pathlib.Path: A fresh staging path beside the destination.
    """
    budget_name: int = FILENAME_LENGTH_MAX - _STAGING_NAME_OVERHEAD
    budget_path: int = (
        _PATH_LENGTH_MAX
        - len(os.fsencode(str(path_destination.parent)))
        - 1  # the separator between parent and name
        - _STAGING_NAME_OVERHEAD
    )
    base_name: str = truncate_to_byte_limit(path_destination.name, max(0, min(budget_name, budget_path)))
    unique: str = str(uuid4())

    # A parent so deep that even a bare ".<uuid>.tmp" overflows the cap: the
    # readable part is already gone, so the uuid itself gives ground, down to
    # its first 10 hex characters. Ten of them still take a concurrent-staging
    # collision out of the realm of the possible, and the alternative was every
    # staging attempt failing identically past the cap while the destination
    # itself fit. Below even that there is nothing left to shrink; the
    # destination's own name barely fits such a parent.
    if not base_name and budget_path < 0:
        unique = unique.replace("-", "")[: max(10, len(unique.replace("-", "")) + budget_path)]

    return path_destination.with_name(f".{base_name}.{unique}.tmp")


def _is_truncated_leftover(path_file: pathlib.Path) -> bool:
    """Whether a destination file is an interrupted write rather than content.

    A 0-byte file under a final name is what a crash, a power cut or a share
    drop leaves between creating a file and writing it: no finished download is
    ever empty. check_file_exists already reads it as nothing, so the skip gate
    downloads the track again, but the move read the same file as an occupant
    and refused to land on it. Every retry and every later run answered the same
    way, so one empty leftover kept that track out of the library for good.
    Finishing the interrupted write is not overwriting anybody's data, and the
    size is measured here, immediately before the swap, never carried in from an
    earlier check.

    Args:
        path_file (pathlib.Path): The destination file to judge.

    Returns:
        bool: True only for a file that exists and holds nothing at all.
    """
    try:
        return path_file.is_file() and path_file.stat().st_size == 0
    except OSError:
        return False


def _waves_item_id(media) -> str:
    """The id a downloaded file is filed under, which is not always ``media.id``.

    A Waves 'best of both' merge fetches a track from one edition and lands it in
    another edition's folder, so it carries ``waves_identity_id``: the id the
    whole app (queue rows, ownership, collection membership) keys that download
    by, while ``media.id`` stays the source stream being fetched. Stamping the
    source id into the file meant a later plain job over the same folder asked
    about the identity id, failed to recognise Waves' own file, and wrote a
    ``_01`` duplicate beside it instead of replacing it.

    Args:
        media: The track or video being written.

    Returns:
        str: The identity id when the item carries one, else its own id, else "".
    """
    return str(getattr(media, "waves_identity_id", "") or getattr(media, "id", "") or "")


def _waves_owned_ids(media) -> set[str]:
    """Every item id a file on disk may legitimately carry for ``media``.

    Normally just its own id. A best-of-both member is filed under the identity
    edition (see :func:`_waves_item_id`), but every build up to v0.1.21 wrote the
    SOURCE edition's id into that same file, so libraries assembled by an older
    Waves are full of merged tracks tagged the other way. Recognising both means
    a forced re-save replaces its own file, and re-tags it with the identity id
    on the way, instead of leaving a numbered duplicate beside it that the app
    will never delete.

    Args:
        media: The track or video being written.

    Returns:
        set[str]: The ids this download may treat as its own copy.
    """
    ids = (getattr(media, "waves_identity_id", ""), getattr(media, "id", ""))
    return {str(i) for i in ids if i}


# TODO: Set appropriate client string and use it for video download.
# https://github.com/globocom/m3u8#using-different-http-clients
class RequestsClient:
    """HTTP client for downloading text content from a URI."""

    def download(
        self, uri: str, timeout: int = REQUESTS_TIMEOUT_SEC, headers: dict | None = None, verify_ssl: bool = True
    ) -> tuple[str, str]:
        """Download the content of a URI as text.

        Args:
            uri (str): The URI to download.
            timeout (int, optional): Timeout in seconds. Defaults to REQUESTS_TIMEOUT_SEC.
            headers (dict | None, optional): HTTP headers. Defaults to None.
            verify_ssl (bool, optional): Whether to verify SSL. Defaults to True.

        Returns:
            tuple[str, str]: Tuple of (text content, final URL).
        """
        if not headers:
            headers = {}

        # The shared certifi-backed session, never a bare requests.get (cold
        # SSLContext per call) and never m3u8's DefaultHTTPClient (urllib
        # trusts the interpreter's compiled-in OpenSSL paths, which do not
        # exist inside a packaged build, so every fetch fails TLS verify).
        o = Download._shared_http().get(uri, timeout=timeout, headers=headers)
        o.raise_for_status()

        return o.text, o.url


class _SharedContextAdapter(HTTPAdapter):
    """HTTPAdapter that gives every pooled connection one shared, preloaded
    SSLContext.

    requests' default cert_verify hands urllib3 a CA bundle *path* per
    connection, and urllib3 then builds a fresh SSLContext and re-parses the
    whole certifi PEM corpus (~150 certificates) on every TLS connect. That
    work runs GIL-free in OpenSSL, so a burst of cold connections saturates
    every core (the CPU spike at download start, worst on modest Windows
    boxes). Loading certifi once and sharing the context leaves only the
    handshake itself per connection, which is a few milliseconds.
    """

    def __init__(self, ssl_context, **kwargs) -> None:
        self._ssl_context = ssl_context
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["ssl_context"] = self._ssl_context
        return super().init_poolmanager(connections, maxsize, block, **pool_kwargs)

    def cert_verify(self, conn, url, verify, cert) -> None:
        # For the default verify=True case, do NOT set conn.ca_certs: that is
        # what triggers urllib3's per-connection load_verify_locations(). The
        # shared context already carries certifi and CERT_REQUIRED, so
        # verification stays fully on. Custom verify paths or client certs
        # fall back to the stock (slower, per-connection) behaviour.
        if verify is True and cert is None:
            return
        super().cert_verify(conn, url, verify, cert)


def pooled_session(
    pool_connections: int = 10,
    pool_maxsize: int = 10,
    pool_block: bool = False,
    max_retries: Retry | int = 0,
) -> requests.Session:
    """Build a keep-alive session whose connections share one preloaded
    SSLContext (see _SharedContextAdapter). Callers own the pool and retry
    policy; the download engine's process-wide instance lives in
    Download._shared_http()."""
    ssl_context = create_urllib3_context()
    ssl_context.load_verify_locations(certifi.where())
    session = requests.Session()
    adapter = _SharedContextAdapter(
        ssl_context,
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
        pool_block=pool_block,
        max_retries=max_retries,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# TODO: Use pathlib.Path everywhere
class Download:
    """Main class for managing downloads, segment merging, file operations, and metadata for TIDAL media."""

    _FILE_OPERATION_RETRIES: int = 5
    _FILE_OPERATION_RETRY_DELAY_SEC: float = 0.5

    # Process-wide keep-alive HTTP session (see the comment in __init__): all
    # Download instances share one warm connection pool so an album start does
    # not pay a burst of TLS handshakes on a cold per-instance pool.
    _http_shared: requests.Session | None = None
    _http_lock = Lock()

    # Hard cap on connections per host, with pool_block=True below. Worker
    # threads beyond this queue for a free connection instead of each opening
    # its own, so a cold download start opens at most this many connections
    # concurrently (the old worst case was per-track x concurrent = 60, and
    # every one of them paid TLS setup at once). Ten concurrent CDN streams
    # saturate any consumer link, so this does not gate throughput.
    _HTTP_POOL_MAXSIZE: int = 10

    @classmethod
    def _shared_http(cls) -> requests.Session:
        """Return the process-wide session, building it on first use."""
        with cls._http_lock:
            if cls._http_shared is None:
                cls._http_shared = pooled_session(
                    pool_connections=cls._HTTP_POOL_MAXSIZE,
                    pool_maxsize=cls._HTTP_POOL_MAXSIZE,
                    pool_block=True,
                    max_retries=Retry(total=5, backoff_factor=1),
                )
            return cls._http_shared

    settings: Settings
    tidal: "Tidal"
    session: Session
    skip_existing: bool = False
    fn_logger: Callable
    progress_gui: ProgressBars
    progress: Progress
    progress_overall: Progress
    event_abort: Event
    event_run: Event

    def __init__(
        self,
        tidal_obj: Tidal,  # Required for Atmos session context manager
        path_base: str,
        fn_logger: Callable,
        skip_existing: bool = False,
        progress_gui: ProgressBars | None = None,
        progress: Progress | None = None,
        progress_overall: Progress | None = None,
        event_abort: Event | None = None,
        event_run: Event | None = None,
    ) -> None:
        """Initialize the Download object and its dependencies.

        Args:
            tidal_obj (Tidal): TIDAL configuration object. Required for:
                - session: Main TIDAL API session
                - switch_to_atmos_session(): Dolby Atmos credential switching
                - restore_normal_session(): Restore original session credentials
            path_base (str): Base path for downloads.
            fn_logger (Callable): Logger function or object.
            skip_existing (bool, optional): Whether to skip existing files. Defaults to False.
            progress_gui (ProgressBars | None, optional): GUI progress bars. Defaults to None.
            progress (Progress | None, optional): Rich progress bar. Defaults to None.
            progress_overall (Progress | None, optional): Overall progress bar. Defaults to None.
            event_abort (Event | None, optional): Abort event. Defaults to None.
            event_run (Event | None, optional): Run event. Defaults to None.
        """
        self.settings = Settings()
        self.tidal = tidal_obj
        self.session = tidal_obj.session
        self.skip_existing = skip_existing
        self.fn_logger = fn_logger
        self.progress_gui = progress_gui
        self.progress = progress
        self.progress_overall = progress_overall
        self.path_base = path_base
        self.event_abort = event_abort
        self.event_run = event_run

        # Destination directories already ensured by this instance (one
        # instance = one queued item, so this resets naturally per album).
        # On a network mount every makedirs(exist_ok=True) of an existing
        # directory still costs real round-trips (mkdir->EEXIST plus a stat),
        # and the moves used to re-ensure the same album directory for the
        # audio, lyrics and cover of every track: dozens of pointless network
        # calls per album. See _ensure_directory.
        self._dirs_ensured: set[str] = set()

        # Final destination names claimed by a download that is between picking
        # its unique name and moving the file there. Nothing is on disk for that
        # stretch (metadata, the lyrics fetch and the cover all run first), so
        # without this two colliding same-name tracks both pick the same free
        # name and one silently overwrites or loses the other (issue #15
        # follow-up). One instance serves all of a queued item's concurrent
        # track workers (`items` fans `self.item` across the pool), and the GUI
        # runs one queued item at a time, so instance scope covers every
        # in-process collision.
        self._names_reserved: set[str] = set()
        # Final destination names this run has already WRITTEN, against the item
        # each one now holds. A claim covers only the stretch between picking a
        # name and moving the file there, which is the whole answer while the
        # disk is consulted: once the file lands it answers for itself. With
        # skipping off nothing consults the disk, so a landed file was invisible
        # and the next track of the same name simply took it (issue #19: six
        # same-title tracks downloaded three at a time left four files, a
        # different four each run). This ledger is what a landed file would have
        # said, and it is never released.
        self._names_written: dict[str, str] = {}
        self._names_reserved_lock: Lock = Lock()

        # One pooled, keep-alive HTTP session shared by every segment download.
        # The old code built a fresh requests.Session() per segment, which forced
        # a full TLS handshake for each one. A HiRes album is served as dozens of
        # segments per track and fanned across up to
        # downloads_simultaneous_per_track_max x downloads_concurrent_max threads,
        # so that became a storm of handshakes, and handshake crypto runs GIL-free
        # in openssl, so it saturated every CPU core (100% CPU, GUI unresponsive)
        # the moment a download started. Reusing pooled connections cuts the
        # per-segment CPU cost by roughly 16x and raises throughput.
        #
        # The session is process-wide (shared across Download instances), not
        # per-instance: the GUI builds a fresh Download per queued album, and a
        # per-instance session meant a cold pool at every album start. Even a
        # warm pool goes cold when the CDN drops idle keep-alives between
        # downloads, so the pool is small and blocking (at most
        # _HTTP_POOL_MAXSIZE connections ever get opened at once) and every
        # connection shares one preloaded SSLContext, capping the worst-case
        # TLS setup burst at download start to a blip.
        self._http = self._shared_http()

        if not self.settings.data.path_binary_ffmpeg:
            self.settings.data.path_binary_ffmpeg = shutil.which("ffmpeg")

        # True whenever ffmpeg is genuinely absent, decoupled from the two gated
        # flags: the MP4/M4A duration-repair remux also needs ffmpeg, so callers
        # (the backend) must be able to warn on pure absence, not just when FLAC
        # extraction / video convert were requested. Surfaced via _warn_if_ffmpeg_missing.
        self.ffmpeg_missing = not self.settings.data.path_binary_ffmpeg

        if self.ffmpeg_missing and (self.settings.data.video_convert_mp4 or self.settings.data.extract_flac):
            self.settings.data.video_convert_mp4 = False
            self.settings.data.extract_flac = False

            self.fn_logger.error(
                "FFmpeg not found (not set and not on $PATH). FLAC extraction, video conversion, and MP4/M4A "
                "duration repair are disabled; files may play but can report 0:00 in strict players. Install "
                "FFmpeg from Settings, or set `path_binary_ffmpeg` (or have ffmpeg on the $PATH)."
            )

    def _get_media_urls(
        self,
        media: Track | Video,
        stream_manifest: StreamManifest | None = None,
    ) -> list[str]:
        """Extract URLs for the given media item.

        Args:
            media (Track | Video): The media item to download.
            stream_manifest (StreamManifest | None, optional): Stream manifest for tracks. Defaults to None.

        Returns:
            list[str]: List of URLs for the media segments.
        """
        # Get urls for media.
        if isinstance(media, Track):
            return stream_manifest.get_urls()
        elif isinstance(media, Video):
            quality_video = self.settings.data.quality_video
            m3u8_variant: m3u8.M3U8 = m3u8.load(media.get_url(), http_client=RequestsClient())
            # Find the desired video resolution or the next best one.
            m3u8_playlist, _ = self._extract_video_stream(m3u8_variant, int(quality_video))

            return m3u8_playlist.files
        else:
            return []

    def _setup_progress(
        self,
        media_name: str,
        urls: list[str],
        progress_to_stdout: bool,
    ) -> tuple[TaskID, int | float | None, int | None]:
        """Set up the progress bar/task and compute progress total and block size.

        Args:
            media_name (str): Name of the media item.
            urls (list[str]): List of segment URLs.
            progress_to_stdout (bool): Whether to show progress in stdout.

        Returns:
            tuple[TaskID, int | float | None, int | None]: (TaskID, progress_total, block_size)
        """
        urls_count: int = len(urls)
        progress_total: int | float | None = None
        block_size: int | None = None

        # Compute total iterations for progress
        if urls_count > 1:
            progress_total: int = urls_count
            block_size: int | None = None
        elif urls_count == 1:
            block_size = 1048576
            r = None
            try:
                # Get file size and compute progress steps. Ride the shared
                # pooled session (Retry(total=5), preloaded SSLContext), never
                # a bare requests call, and follow redirects so a 3xx does not
                # read as a content-length of 0.
                r = self._shared_http().head(urls[0], timeout=REQUESTS_TIMEOUT_SEC, allow_redirects=True)
                r.raise_for_status()

                total_size_in_bytes: int = int(r.headers.get("content-length", 0))
                progress_total = total_size_in_bytes / block_size
            except Exception as error:
                # The probe only sizes the progress bar; a blip here must not
                # fail the track. Fall back to an indeterminate total.
                # fn_logger may be a plain wrapper without .exception, so this
                # stays .error (bound first, which also sidesteps TRY400).
                log_error = self.fn_logger.error
                log_error(f"Could not size the download, progress will be indeterminate: {error}")
                progress_total = None
            finally:
                if r:
                    r.close()
        else:
            raise ValueError

        # Create progress Task
        p_task: TaskID = self.progress.add_task(
            f"[blue]Item '{media_name[:30]}'",
            total=progress_total,
            visible=progress_to_stdout,
        )
        return p_task, progress_total, block_size

    def _download_segments(
        self,
        urls: list[str],
        path_base: pathlib.Path,
        block_size: int | None,
        p_task: TaskID,
        progress_to_stdout: bool,
        event_stop: Event | None = None,
        n_tail_spurious: int | None = None,
    ) -> tuple[bool, list[DownloadSegmentResult]]:
        """Download all segments with progress reporting and abort handling.

        Args:
            urls (list[str]): List of segment URLs.
            path_base (pathlib.Path): Base path for segment files.
            block_size (int | None): Block size for streaming.
            p_task (TaskID): Progress bar task ID.
            progress_to_stdout (bool): Whether to show progress in stdout.
            event_stop (Event | None, optional): Event to stop the download. Defaults to None.
            n_tail_spurious (int | None, optional): How many trailing URLs the manifest
                proves are over-generated padding (0 means none, so a failed final
                segment is a real failure). None means unproven and keeps the legacy
                last-segment leniency. Defaults to None.

        Returns:
            tuple[bool, list[DownloadSegmentResult]]: (result_segments, list of segment results)
        """
        result_segments: bool = True
        dl_segment_results: list[DownloadSegmentResult] = []

        # One pass: download each URL exactly once. Segment-level retries are
        # already handled inside _download_segment (requests Retry(total=5)), so
        # the old `while not self.progress.tasks[p_task].finished` loop that
        # re-submitted every URL added no real retry, only risk: a segment that
        # returns an empty but successful body (HTTP 200, 0 bytes) advances the
        # progress bar zero times, so `finished` (completed >= total) never flips
        # and the loop re-downloaded the whole track forever. Success is derived
        # from the per-segment results below, not from the progress counter.
        # Clamp the per-track fan-out to the shared connection pool: workers
        # beyond _HTTP_POOL_MAXSIZE can never hold a socket (pool_block=True),
        # they just sit blocked, so extra workers add threads and RAM with
        # exactly zero throughput. The old default of 20 workers x 3 concurrent
        # items produced 60 threads for 10 usable connections (a live session
        # was caught at 118 process threads and 1.6 GB RSS mid-burst).
        workers_max: int = max(1, min(self.settings.data.downloads_simultaneous_per_track_max, self._HTTP_POOL_MAXSIZE))
        with futures.ThreadPoolExecutor(max_workers=workers_max) as executor:
            # Dispatch all download tasks to worker threads
            l_futures: list[futures.Future] = [
                executor.submit(
                    self._download_segment, url, path_base, block_size, p_task, progress_to_stdout, event_stop
                )
                for url in urls
            ]

            # Report results as they become available
            for future in futures.as_completed(l_futures):
                # Retrieve result
                result_dl_segment: DownloadSegmentResult = future.result()

                dl_segment_results.append(result_dl_segment)

                # Check for a link that failed.
                if not result_dl_segment.result:
                    # On very short tracks (< 8 seconds or so) the *last* URL of a MULTI-segment
                    # track is a spurious tail (HTTP Error 500) that isn't needed; the file won't
                    # be corrupt. That is tidalapi's segment-count arithmetic over-generating one
                    # URL past the end of the audio (see waves_ui/manifest.py), so when the
                    # manifest was parseable, n_tail_spurious says exactly whether the final URL
                    # is padding (> 0) or required audio (0): a required final segment failing is
                    # a REAL failure (a silently truncated file), not a tolerable quirk. Only when
                    # the manifest proved nothing (None) does the legacy blanket leniency apply.
                    # A single-URL (BTS) track has exactly one required segment which also happens
                    # to be `urls[-1]`, so a failure there is a real failure and must NOT be
                    # exempted, otherwise a fully failed GET (expired link, 403/500, network
                    # failure) would masquerade as success.
                    is_spurious_tail: bool = (
                        len(urls) > 1
                        and result_dl_segment.url is urls[-1]
                        and (n_tail_spurious is None or n_tail_spurious > 0)
                    )

                    if not is_spurious_tail:
                        result_segments = False

                        # A deliberate Stop/Cancel makes every in-flight segment
                        # return failed by design, that's a cancellation, not
                        # corruption, so don't scream "corrupt" once per segment.
                        if not (self.event_abort.is_set() or (event_stop and event_stop.is_set())):
                            self.fn_logger.error("Something went wrong while downloading. File is corrupt!")

                # If app is terminated (CTRL+C) or item stopped
                if self.event_abort.is_set() or (event_stop and event_stop.is_set()):
                    # Cancel all not yet started tasks
                    for f in l_futures:
                        f.cancel()

                    return False, dl_segment_results

        # The progress total is only an estimate (segment count, or HEAD
        # content-length / block size) and can exceed the number of chunks
        # actually streamed, so a successful download may leave the task a hair
        # below 100%. Snap it to complete so the GUI reads 100% and any
        # `.finished` check downstream stays truthful. Only on success; a real
        # failure is left as-is for the caller to mark failed.
        task = self.progress.tasks[p_task]
        if result_segments and task.total is not None and not task.finished:
            self.progress.update(p_task, completed=task.total)

        return result_segments, dl_segment_results

    def _download_postprocess(
        self,
        result_segments: bool,
        path_file: pathlib.Path,
        dl_segment_results: list[DownloadSegmentResult],
        media: Track | Video,
        stream_manifest: StreamManifest | None = None,
        n_tail_spurious: int | None = None,
    ) -> tuple[bool, pathlib.Path]:
        """Merge the downloaded segments and return the final file path.

        Args:
            result_segments (bool): Whether all segments downloaded successfully.
            path_file (pathlib.Path): Path to the output file.
            dl_segment_results (list[DownloadSegmentResult]): List of segment download results.
            media (Track | Video): The media item.
            stream_manifest (StreamManifest | None, optional): Stream manifest for tracks. Defaults to None.
            n_tail_spurious (int | None, optional): Manifest-proven count of over-generated
                trailing URLs; see ``_download_segments``. Defaults to None.

        Returns:
            tuple[bool, pathlib.Path]: (Success, path to the downloaded file)
        """
        path_file_merged: pathlib.Path = path_file
        result_merge: bool = False

        # Only if no error happened while downloading.
        if result_segments:
            # Bring list into right order, so segments can be easily merged.
            dl_segment_results.sort(key=lambda x: x.id_segment)

            result_merge = self._segments_merge(path_file, dl_segment_results, n_tail_spurious)

            if not result_merge:
                self.fn_logger.error(
                    f"Something went wrong while writing to {log_content(media.name)}. File is corrupt!"
                )
            elif isinstance(media, Track) and stream_manifest.is_encrypted:
                # Waves does not process encrypted streams. TIDAL serves plain
                # MPEG-DASH for every quality Waves requests, so this branch is
                # not reached in normal operation. Should a stream ever arrive
                # encrypted, fail the download rather than leave an unplayable
                # file behind.
                self.fn_logger.error(
                    f"{log_content(media.name)} arrived as an encrypted stream, which Waves does not process. "
                    "The download was stopped so no unplayable file is written."
                )
                result_merge = False

        return result_merge, path_file_merged

    def _download(
        self,
        media: Track | Video,
        path_file: pathlib.Path,
        stream_manifest: StreamManifest | None = None,
        event_stop: Event | None = None,
    ) -> tuple[bool, pathlib.Path]:
        """Download a media item (track or video), handling segments and merging.

        Args:
            media (Track | Video): The media item to download.
            path_file (pathlib.Path): Path to the output file.
            stream_manifest (StreamManifest | None, optional): Stream manifest for tracks. Defaults to None.
            event_stop (Event | None, optional): Event to stop the download. Defaults to None.

        Returns:
            tuple[bool, pathlib.Path]: (Success, path to the downloaded file)
        """
        media_name: str = name_builder_item(media)

        try:
            urls: list[str] = self._get_media_urls(media, stream_manifest)
        except Exception:
            return False, path_file

        # How many trailing URLs the manifest arithmetic proves are
        # over-generated padding (tracks only; see waves_ui/manifest.py).
        # None means unproven (video m3u8, BTS, or an unparseable manifest)
        # and preserves the legacy last-segment leniency downstream.
        n_tail_spurious: int | None = overgenerated_tail_urls(stream_manifest) if stream_manifest else None

        # Set the correct progress output channel.
        if self.progress_gui is None:
            progress_to_stdout: bool = True
        else:
            progress_to_stdout: bool = False
            # Send signal to GUI with media name
            self.progress_gui.item_name.emit(media_name[:30])

        try:
            p_task, _progress_total, block_size = self._setup_progress(media_name, urls, progress_to_stdout)
        except Exception:
            return False, path_file

        # Hand the caller the task that belongs to THIS item. A caller matching
        # on the task's description instead would be matching on a display
        # string truncated to 30 characters, which several tracks of one release
        # share; the TaskID is the only per-item handle.
        self._note_progress_task(media, p_task)

        result_segments, dl_segment_results = self._download_segments(
            urls, path_file.parent, block_size, p_task, progress_to_stdout, event_stop, n_tail_spurious
        )

        result_merge, path_file_merged = self._download_postprocess(
            result_segments, path_file, dl_segment_results, media, stream_manifest, n_tail_spurious
        )

        return result_merge, path_file_merged

    def _segments_merge(
        self,
        path_file: pathlib.Path,
        dl_segment_results: list[DownloadSegmentResult],
        n_tail_spurious: int | None = None,
    ) -> bool:
        """Merge downloaded segments into a single file and clean up segment files.

        Args:
            path_file (pathlib.Path): Path to the output file.
            dl_segment_results (list[DownloadSegmentResult]): List of segment download results.
            n_tail_spurious (int | None, optional): Manifest-proven count of over-generated
                trailing URLs; see ``_download_segments``. Defaults to None.

        Returns:
            bool: True if merge succeeded, False otherwise.
        """
        result: bool = True

        # Copy the content of all segments into one file.
        try:
            with path_file.open("wb") as f_target:
                for dl_segment_result in dl_segment_results:
                    with dl_segment_result.path_segment.open("rb") as f_segment:
                        # Read and write chunks, which gives better HDD write performance
                        while segment := f_segment.read(CHUNK_SIZE):
                            f_target.write(segment)

                    # Delete segment from HDD
                    dl_segment_result.path_segment.unlink()

        except Exception:
            # Mirror the download-time leniency: only a manifest-proven (or, when the
            # manifest proved nothing, presumed) spurious *tail* segment of a
            # MULTI-segment track may be missing without corrupting the file. A merge
            # failure on the sole segment of a single-URL (BTS) track is a real failure.
            is_spurious_tail: bool = (
                len(dl_segment_results) > 1
                and dl_segment_result is dl_segment_results[-1]
                and (n_tail_spurious is None or n_tail_spurious > 0)
            )

            if not is_spurious_tail:
                result = False

        return result

    def _wait_while_paused(self, event_stop: Event | None = None) -> bool:
        """Block while paused (event_run cleared), staying responsive to abort/stop.

        A bare ``event_run.wait()`` ignores both the global abort and the per-item
        stop event, so quitting or removing a paused item would strand the segment
        thread here forever (the threads are non-daemon and hang interpreter
        shutdown). Instead poll on a short timeout and bail as soon as either abort
        or stop is set.

        Args:
            event_stop (Event | None, optional): Per-item stop event. Defaults to None.

        Returns:
            bool: True if the caller should abort (abort/stop set), False to proceed.
        """
        while not self.event_run.is_set():
            if self.event_abort.is_set() or (event_stop and event_stop.is_set()):
                return True
            # Short timeout so a resume, abort, or stop is picked up promptly.
            self.event_run.wait(0.1)

        return self.event_abort.is_set() or bool(event_stop and event_stop.is_set())

    def _download_segment(
        self,
        url: str,
        path_base: pathlib.Path,
        block_size: int | None,
        p_task: TaskID,
        progress_to_stdout: bool,
        event_stop: Event | None = None,
    ) -> DownloadSegmentResult:
        """Download a single segment of a media file.

        Args:
            url (str): URL of the segment.
            path_base (pathlib.Path): Base path for segment file.
            block_size (int | None): Block size for streaming.
            p_task (TaskID): Progress bar task ID.
            progress_to_stdout (bool): Whether to show progress in stdout.
            event_stop (Event | None, optional): Per-item stop event. Defaults to None.

        Returns:
            DownloadSegmentResult: Result of the segment download.
        """
        result: bool = False
        path_segment: pathlib.Path = path_base / url_to_filename(url)
        # Calculate the segment ID based on the file name within the URL.
        filename_stem: str = str(path_segment.stem).split("_")[-1]
        # CAUTION: This is a workaround, so BTS (LOW quality) track will work. They usually have only ONE link.
        id_segment: int = int(filename_stem) if filename_stem.isdecimal() else 0
        error: HTTPError | None = None

        # If app is terminated (CTRL+C) or item stopped
        if self.event_abort.is_set() or (event_stop and event_stop.is_set()):
            return DownloadSegmentResult(
                result=False, url=url, path_segment=path_segment, id_segment=id_segment, error=error
            )

        # Honor pause, but wake promptly on resume/abort/stop instead of blocking forever.
        if self._wait_while_paused(event_stop):
            return DownloadSegmentResult(
                result=False, url=url, path_segment=path_segment, id_segment=id_segment, error=error
            )

        # Reuse the pooled, keep-alive session built once in __init__. Retry/backoff
        # is configured on that shared adapter, so segments reuse connections instead
        # of paying a TLS handshake each (the old per-segment Session was the cause of
        # the all-core CPU spike on download).
        try:
            # Create the request object with stream=True, so the content won't be loaded into memory at once.
            # Context-manage the response: the shared pool blocks when full
            # (pool_block=True), so a connection left to garbage collection on
            # an early return would starve waiting segment workers.
            with self._http.get(url, stream=True, timeout=REQUESTS_TIMEOUT_SEC) as r:
                r.raise_for_status()

                # Write the content to disk. If `chunk_size` is set to `None` the whole file will be written at once.
                with path_segment.open("wb") as f:
                    for data in r.iter_content(chunk_size=block_size):
                        # Bail out promptly on abort (Stop/Cancel or app quit)
                        # instead of streaming the whole segment first.
                        if self.event_abort.is_set() or (event_stop and event_stop.is_set()):
                            return DownloadSegmentResult(
                                result=False, url=url, path_segment=path_segment, id_segment=id_segment, error=error
                            )
                        f.write(data)
                        # Advance progress bar.
                        self.progress.advance(p_task)

            result = True
        except Exception:
            self.progress.advance(p_task)

        # To send the progress to the GUI, we need to emit the percentage.
        if not progress_to_stdout:
            self.progress_gui.item.emit(self.progress.tasks[p_task].percentage)

        return DownloadSegmentResult(
            result=result, url=url, path_segment=path_segment, id_segment=id_segment, error=error
        )

    def extension_guess(
        self, quality_audio: Quality, metadata_tags: list[str], is_video: bool
    ) -> AudioExtensions | VideoExtensions:
        """Guess the file extension for a media item based on quality and type.

        Args:
            quality_audio (Quality): Audio quality.
            metadata_tags (list[str]): Metadata tags for the media.
            is_video (bool): Whether the media is a video.

        Returns:
            AudioExtensions | VideoExtensions: Guessed file extension.
        """
        result: AudioExtensions | VideoExtensions

        if is_video:
            result = AudioExtensions.MP4 if self.settings.data.video_convert_mp4 else VideoExtensions.TS
        else:
            result = (
                AudioExtensions.FLAC
                if len(metadata_tags) > 0  # If there are no metadata tags only lossy quality is available
                and (
                    (
                        self.settings.data.extract_flac
                        and quality_audio in (Quality.hi_res_lossless, Quality.high_lossless)
                    )
                    or (
                        "HIRES_LOSSLESS" not in metadata_tags
                        and quality_audio not in (Quality.low_96k, Quality.low_320k)
                    )
                    or quality_audio == Quality.high_lossless
                )
                else AudioExtensions.M4A
            )

        return result

    def item(
        self,
        file_template: str,
        media_id: str | None = None,
        media_type: MediaType | None = None,
        media: Track | Video | None = None,
        video_download: bool = True,
        download_delay: bool = False,
        quality_audio: Quality | None = None,
        quality_video: QualityVideo | None = None,
        is_parent_album: bool = False,
        list_position: int = 0,
        list_total: int = 0,
        keep_album: bool = False,
        event_stop: Event | None = None,
    ) -> tuple[bool, pathlib.Path | str]:
        """Download a single media item, handling file naming, skipping, and post-processing.

        Args:
            file_template (str): Template for file naming.
            media_id (str | None, optional): Media ID. Defaults to None.
            media_type (MediaType | None, optional): Media type. Defaults to None.
            media (Track | Video | None, optional): Media item. Defaults to None.
            video_download (bool, optional): Whether to allow video downloads. Defaults to True.
            download_delay (bool, optional): Whether to delay between downloads. Defaults to False.
            quality_audio (Quality | None, optional): Audio quality. Defaults to None.
            quality_video (QualityVideo | None, optional): Video quality. Defaults to None.
            is_parent_album (bool, optional): Whether this is a parent album. Defaults to False.
            list_position (int, optional): Position in list. Defaults to 0.
            list_total (int, optional): Total items in list. Defaults to 0.
            keep_album (bool, optional): Trust the album + numbering on the passed media
                instead of re-fetching the track (used by best-of-both merges). Defaults to False.
            event_stop (Event | None, optional): Event to stop the download. Defaults to None.

        Returns:
            tuple[bool, pathlib.Path | str]: (Downloaded, path to file)
        """
        # Check for stop signal before doing anything
        if self.event_abort.is_set() or (event_stop and event_stop.is_set()):
            return False, ""

        # Step 1: Validate and prepare media
        validated_media = self._validate_and_prepare_media(media, media_id, media_type, video_download, keep_album)
        if validated_media is None or not isinstance(validated_media, Track | Video):
            return False, ""

        media = validated_media

        # An Atmos-only track (TIDAL lists the Atmos version of a song as its
        # own track id, with no stereo stream behind it) is downloaded whether
        # or not Dolby Atmos downloads are on. The setting means "prefer stereo
        # where there is a choice", not "leave a hole in the album": a song you
        # cannot play today beats one you never got. _get_track_stream_info
        # takes the Atmos session for it because there is nothing else to take.
        # It used to be skipped here, before any path work, and answered
        # ok=True with an empty path, which is what put a permanent gap in every
        # discography that carried a spatial-only single.

        # Check for stop signal
        if self.event_abort.is_set() or (event_stop and event_stop.is_set()):
            return False, ""

        # Step 2: Create file paths and determine skip logic
        path_media_dst, file_extension_dummy, skip_file, skip_download = self._prepare_file_paths_and_skip_logic(
            media, file_template, quality_audio, list_position, list_total
        )

        if skip_file:
            self.fn_logger.debug(f"Download skipped, since file exists: '{path_media_dst}'")

            return True, path_media_dst

        # Step 3: Handle quality settings
        quality_audio_old, quality_video_old = self._adjust_quality_settings(quality_audio, quality_video)

        # Step 4: Download and process media.
        # The returned path reflects the TRUE stream extension and any uniquify suffix, so
        # skip/return-path/symlink below act on the file actually written, not the step-2 guess.
        download_success, path_media_dst = self._download_and_process_media(
            media,
            path_media_dst,
            skip_download,
            is_parent_album,
            file_extension_dummy,
            event_stop,
        )

        # The file and its sidecars are fully on disk here; what follows is
        # post-processing (symlinks) and the randomized politeness delay
        # before the NEXT download. The queue row must not sit on a full
        # FINISHING word through a deliberate sleep, so the delivery is
        # announced now and the delay stays invisible.
        if download_success:
            self._note_delivered(media)

        # Step 5: Post-processing
        self._perform_post_processing(
            media,
            path_media_dst,
            quality_audio,
            quality_video,
            quality_audio_old,
            quality_video_old,
            download_delay,
            skip_file,
            event_stop,
        )

        return download_success, path_media_dst

    def _validate_and_prepare_media(
        self,
        media: Track | Video | Album | Playlist | UserPlaylist | Mix | None,
        media_id: str | None,
        media_type: MediaType | None,
        video_download: bool = True,
        keep_album: bool = False,
    ) -> Track | Video | Album | Playlist | UserPlaylist | Mix | None:
        """Validate and prepare media instance for download.

        Args:
            media (Track | Video | Album | Playlist | UserPlaylist | Mix | None): Media instance.
            media_id (str | None): Media ID if creating new instance.
            media_type (MediaType | None): Media type if creating new instance.
            video_download (bool, optional): Whether video downloads are allowed. Defaults to True.

        Returns:
            Track | Video | Album | Playlist | UserPlaylist | Mix | None: Prepared media instance or None if invalid.
        """
        try:
            if media_id and media_type:
                # If no media instance is provided, we need to create the media instance.
                # Throws `tidalapi.exceptions.ObjectNotFound` if item is not available anymore.
                media = instantiate_media(self.session, media_type, media_id)
            elif isinstance(media, Track | Video):
                # Deliberately NOT gated on media.allow_streaming here. That flag
                # is a false negative for our client: TIDAL serves editions like
                # "ALICIA (With Commentary)" with allowStreaming=false on every
                # track, yet the official apps and our own account still play
                # most of them, and the ones it truly withholds answer the
                # playback request with a 401 (issue #25). So availability is
                # decided where it is authoritative, when the stream is actually
                # fetched (_get_stream_info), and a real refusal becomes the
                # UNAVAILABLE outcome there. Gating on the flag here refused
                # tracks the account could play and painted whole albums red.
                if isinstance(media, Track) and not keep_album:
                    # Re-create media instance with full album information.
                    # Skipped when keep_album is set: a best-of-both merge passes a
                    # track deliberately re-tagged under another edition's album and
                    # numbering, which this re-fetch would otherwise clobber.
                    media = self.session.track(str(media.id), with_album=True)
            elif isinstance(media, Album):
                # An album whose own allowStreaming is false is a different case
                # from the per-track flag above: the collection itself is gone,
                # so there is nothing to enumerate. (efc2549 added this for
                # region-locked albums.) Individual tracks are still let through
                # and settled at stream time.
                if not media.allow_streaming:
                    self._note_unavailable(media)
                    self.fn_logger.info(
                        f"This item is not available for listening anymore on TIDAL. Skipping: {log_content(name_builder_title(media))}"
                    )
                    return None
            elif not media:
                self._raise_media_missing()
        except (MediaMissing, Exception):
            return None

        # If video download is not allowed and this is a video, return None
        if not video_download and isinstance(media, Video):
            self.fn_logger.info(
                f"Video downloads are deactivated (see settings). Skipping video: {log_content(name_builder_item(media))}"
            )
            return None

        return media

    def _raise_media_missing(self) -> None:
        """Raise MediaMissing exception.

        Helper method to abstract raise statement as per TRY301.
        """
        raise MediaMissing

    def _illegal_replacement(self) -> str:
        """The user's illegal-character replacement, laundered for use.

        Read from settings on every call (the app runs for weeks; a settings
        change must take effect on the next download, not the next restart)
        and passed through safe_filename_replacement so nothing typed into the
        box can put an illegal character back into a name.
        """
        return safe_filename_replacement(getattr(self.settings.data, "filename_illegal_replacement", ""))

    def _illegal_map(self) -> dict[str, str]:
        """The user's per-character stand-ins, laundered for use.

        Read on every call and laundered at use for the same reasons as
        _illegal_replacement, which the characters left unnamed here fall back
        to.
        """
        return safe_filename_replacement_map(getattr(self.settings.data, "filename_illegal_map", None))

    def _collection_dir(self, relative_template: str) -> pathlib.Path:
        """The folder a collection template points at.

        The folder is everything above the last segment, whether that segment
        still carries the per-track tokens or has been filled in by a real
        item; a dummy suffix lets the normal sanitizer run over the same shape
        a real destination takes.
        """
        candidate = (pathlib.Path(self.path_base).expanduser() / (relative_template + ".x")).absolute()
        return pathlib.Path(path_file_sanitize(candidate, adapt=True)).parent

    def _keep_existing_collection_layout(self, tidied: str, *older: str, probes: list[str] | None = None) -> str:
        """The same choice as _keep_existing_layout, one level up.

        A collection's own folder is baked into the item template before any
        item is queued, so the older spelling has to be preferred here too:
        by the time an item is formatted the folder is literal text and the
        old name could no longer be recovered. Only folders exist at this
        level, so there is no file to fall back on; a spelling that dropped
        the folder outright (issue #16) points at an ancestor, which exists
        whether or not anything was ever downloaded, and is therefore no
        evidence of an older layout.

        ``probes`` is the same list of spellings (same order, preferred first)
        resolved against a real item of the collection, and is what the folder
        test is made on. A collection formats only the tokens its own type
        answers to, and the shipped album template opens with {artist_name},
        which only a track can answer: the spelling still carries it as
        literal text, the folder tested therefore contains a literal
        "{artist_name}" segment, never exists, and every library looked new.
        A pre-0.1.17 album then got a second, tidy-spelled folder beside the
        one it was already in. Without probes (an empty collection, or a
        caller that has no item yet) the spellings are tested as they are,
        which is the older behaviour.
        """
        spellings: list[str] = [tidied, *older]
        sources: list[str] = probes if probes and len(probes) == len(spellings) else spellings
        dirs: list[pathlib.Path] = [self._collection_dir(source) for source in sources]
        preferred_depth: int = len(dirs[0].parts)
        for older_relative, older_dir in zip(spellings[1:], dirs[1:], strict=True):
            if older_relative == tidied:
                continue
            if len(older_dir.parts) == preferred_depth and older_dir.is_dir():
                return older_relative
        return tidied

    def _keep_existing_layout(self, tidied: pathlib.Path, *older: pathlib.Path) -> pathlib.Path:
        """Prefer an older spelling wherever a library already uses it.

        0.1.17 stopped leaving a doubled space where an illegal character was
        stripped, so a folder saved as "The Better Life  Dead Love" would
        otherwise be superseded by "The Better Life Dead Love": the album
        would look missing, download again, and the user would own two
        folders for one album. Nothing already on disk may move or be
        duplicated, so an old spelling wins wherever it exists.

        ``older`` lists the spellings previous behavior produced, most recent
        first: with the illegal-character replacement setting on, that is the
        plain-removal spelling and then the pre-0.1.17 one, so switching the
        setting never restructures a library built before it either.

        Folder and file are decided separately, so an existing album keeps its
        folder while a track never downloaded before still gets the preferred
        name inside it. A library with no spelling present (the common case,
        and every new download) simply gets the preferred name.

        An older spelling can also lose a folder outright: a title made only of
        illegal characters (XXXTENTACION's album "?") empties out, its path
        segment is dropped, and the old destination is the ARTIST folder, one
        level above the preferred one. An ancestor almost always exists, so its
        existence is no evidence at all, and taking it scattered the album's
        tracks loose into the artist folder (issue #16). There, only a file
        already sitting in the old place counts, and only for itself.
        """
        candidates = [path for path in older if path != tidied]
        if not candidates:
            return tidied

        directory = tidied.parent
        for old in candidates:
            if old.parent == tidied.parent:
                continue
            if len(old.parent.parts) != len(tidied.parent.parts):
                if check_file_exists(old, extension_ignore=True):
                    return old
                continue
            if old.parent.is_dir():
                directory = old.parent
                break
        for old in candidates:
            if old.name != tidied.name and check_file_exists(directory / old.name, extension_ignore=True):
                return directory / old.name
        return directory / tidied.name

    def _existing_same_item_at(self, path_media_dst: pathlib.Path, media: Track | Video) -> pathlib.Path | None:
        """WHERE this item already lives at this destination, or None.

        skip_existing used to be filename-keyed, so distinct tracks whose
        sanitized names collide (an album carrying several mixes with one
        title) were silently skipped after the first one downloaded (issue
        #15). Downloads tag each file with the TIDAL item id (read_item_id);
        when the name is taken, the ids decide:

        - untagged occupant (a pre-id library, or a raw .ts video): identity
          unknown, keep the historical skip so re-downloading an old library
          cannot duplicate it wholesale;
        - occupant has this item's id: already downloaded, skip;
        - different id: look through EVERY one of the name's uniquify variants
          (stem_NN) that exists, gaps in the numbering included, since a user
          who removed stem_01 kept stem_02 where it was. This item's id there
          means skip; an untagged variant means identity unknown and skips too,
          the same answer the base occupant gets (read_item_id's contract: a
          missing id is never evidence of a DIFFERENT item). Only when every
          existing variant is tagged with some other id is this a NEW colliding
          track the caller downloads (the final move uniquifies it).

        The answer is the PATH holding the item, not a bare yes: when the item
        sits at a numbered variant, a yes alone left the caller acting on the
        base name, so the symlink and the returned track path pointed at the
        colliding stranger's file. Variant names are spelled through the same
        trimming that wrote them (a stem at the 255-byte cap gives up bytes to
        the _NN suffix, so the raw concatenation is a name that cannot exist),
        and a 0-byte variant is an interrupted write, not evidence: judging it
        identity-unknown skipped the track for good, the exact trap
        check_file_exists already refuses on the base name.

        One listing of the directory, not up to 99 stats: on a network mount
        that difference is felt per track. Names are matched literally, never
        globbed, because a stem may hold glob characters of its own.
        """
        media_id = _waves_item_id(media)
        owned_ids = _waves_owned_ids(media)
        if not media_id:
            return path_media_dst
        occupant_id = read_item_id(path_media_dst)
        if not occupant_id or occupant_id in owned_ids:
            return path_media_dst

        try:
            # Keyed the way the filesystem matches, not as exact strings: a
            # library file written in the other unicode normalization or the
            # other case is the same file, and a scan that missed it fetched the
            # track again and left a duplicate. The value keeps the on-disk
            # spelling, which is the one that can actually be opened.
            names_present: dict[str, str] = {
                name_comparison_key(name): name for name in os.listdir(path_media_dst.parent)
            }
        except OSError:
            names_present = {}

        threshold_zfill = len(str(UNIQUIFY_THRESHOLD))

        for count in range(1, UNIQUIFY_THRESHOLD + 1):
            name = unique_variant_name(path_media_dst, "_" + str(count).zfill(threshold_zfill))
            name_on_disk = names_present.get(name_comparison_key(name))

            if name_on_disk is None:
                continue

            sibling = path_media_dst.parent / name_on_disk

            if not sibling.is_file() or _is_truncated_leftover(sibling):
                continue

            sibling_id = read_item_id(sibling)

            if not sibling_id:
                self.fn_logger.debug(
                    f"Skipping as already downloaded: '{sibling}' carries no item id, so it may be this track."
                )

                return sibling

            if sibling_id in owned_ids:
                return sibling

        return None

    def _names_unavailable_to(self, media_id: str) -> set[str]:
        """The destination names this item may not take. Call under the lock.

        Two sources, one answer: a name another download is holding in flight,
        and a name this run has already written for a DIFFERENT item. The
        second is what keeps a track that starts after its same-name sibling
        landed from replacing it in overwrite mode, where the file on disk is
        never looked at.

        Only for a different item: the same track twice in one playlist still
        lands on its own file, which is what the setting asks for, and what it
        has always done.
        """
        return self._names_reserved | {
            name for name, owner in self._names_written.items() if not media_id or owner != media_id
        }

    def _is_own_copy(self, path_file: pathlib.Path, media_id: str, owned_ids: set[str] | None = None) -> bool:
        """Whether the file already at this name is this item's own to replace.

        Asked only with skipping off, where an existing file is exactly what
        the download means to replace. It may replace its own earlier copy
        (the point of the setting, and of a quality upgrade) and an untagged
        one, which read_item_id leaves unidentified and a library predating
        the tag is full of: refusing those would strew numbered copies through
        the very library the user asked to refresh. A file carrying a
        different item's id is a different song, and replacing it loses it.

        ``owned_ids`` widens "its own" to every id this item may have been
        written under (see :func:`_waves_owned_ids`), which is what lets a
        best-of-both member replace the copy an older Waves filed under the
        source edition's id.
        """
        if not media_id:
            return True

        occupant: str = read_item_id(path_file)

        return not occupant or occupant == media_id or occupant in (owned_ids or set())

    def _claim_destination(
        self, path_media_dst: pathlib.Path, media_id: str, owned_ids: set[str] | None = None
    ) -> tuple[pathlib.Path, str | None]:
        """Pick this download's final name and hold it, in one locked step.

        Picking and claiming cannot be two steps: the file only appears on disk
        at the move far below (metadata, the lyrics fetch and the cover run in
        between, seconds of it), so two colliding tracks would both find the
        same name free, and one would overwrite or lose the other.

        The claim is taken in every mode; only what counts as occupied differs,
        which is what _names_unavailable_to and _is_own_copy answer.

        Args:
            path_media_dst (pathlib.Path): The name the template produced.
            media_id (str): The item being downloaded, "" when it has no id.
            owned_ids (set[str] | None): Every id a file of this item may carry
                on disk, for a best-of-both member an older build filed under
                the source edition's id.

        Returns:
            tuple[pathlib.Path, str | None]: The name to use and the claim to
                release once the file is in place, or the name unchanged and
                None when it and all of its numbered variants are taken.
        """
        with self._names_reserved_lock:
            path_media_unique: pathlib.Path | None = path_file_uniquify(
                path_media_dst,
                names_taken=self._names_unavailable_to(media_id),
                check_disk=self.skip_existing,
                is_own_copy=lambda candidate: self._is_own_copy(candidate, media_id, owned_ids),
            )

            if path_media_unique is None:
                return path_media_dst, None

            name_reserved: str = str(path_media_unique)
            self._names_reserved.add(name_reserved)

            return path_media_unique, name_reserved

    def _record_name_written(self, path_file: pathlib.Path, media_id: str) -> None:
        """Note that this item's file now occupies this name."""
        with self._names_reserved_lock:
            self._names_written[str(path_file)] = media_id

    def _destination_path(
        self,
        media: Track | Video,
        file_template: str,
        quality_audio: Quality | None,
        list_position: int = 0,
        list_total: int = 0,
    ) -> tuple[pathlib.Path, str]:
        """WHERE this item will be written: the destination file path (before
        any same-name numbering) and the extension it was guessed with.

        One method, so anything that needs to know the destination ahead of
        the write asks the same question the write asks, and gets the same
        answer: the same template, the same sanitizing, and the same choice
        among the older spellings a library may already use
        (_keep_existing_layout). A second copy of this decision elsewhere
        would agree on a fresh library and drift on any other.

        Args:
            media (Track | Video): Media item.
            file_template (str): Template for file naming.
            quality_audio (Quality | None): Audio quality, for the extension.
            list_position (int): Position in list.
            list_total (int): Total items in list.

        Returns:
            tuple[pathlib.Path, str]: (path_media_dst, file_extension_dummy)
        """
        # Create file name and path
        metadata_tags = [] if isinstance(media, Video) else (media.media_metadata_tags or [])
        quality_for_extension = quality_audio if quality_audio is not None else Quality.high_lossless

        file_extension_dummy: str = self.extension_guess(
            quality_for_extension,
            metadata_tags=metadata_tags,
            is_video=isinstance(media, Video),
        )

        def build(tidy: bool, replacement: str = "", mapping: dict[str, str] | None = None) -> pathlib.Path:
            relative = format_path_media(
                file_template,
                media,
                self.settings.data.album_track_num_pad_min,
                list_position,
                list_total,
                delimiter_artist=self.settings.data.filename_delimiter_artist,
                delimiter_album_artist=self.settings.data.filename_delimiter_album_artist,
                use_primary_album_artist=self.settings.data.use_primary_album_artist,
                tidy_spacing=tidy,
                illegal_replacement=replacement,
                illegal_map=mapping,
            )
            candidate = (pathlib.Path(self.path_base).expanduser() / (relative + file_extension_dummy)).absolute()
            # Sanitize final path_file to fit into OS boundaries.
            return pathlib.Path(path_file_sanitize(candidate, adapt=True))

        # Older spellings, most recent first. The general stand-in with no
        # per-character overrides is one of them (a library named under 0.1.17
        # keeps its folders when overrides are added later); the two before it
        # predate the stand-in setting entirely, so they build with "" and
        # reproduce history exactly.
        path_media_dst: pathlib.Path = self._keep_existing_layout(
            build(True, self._illegal_replacement(), self._illegal_map()),
            build(True, self._illegal_replacement()),
            build(True),
            build(False),
        )
        return path_media_dst, file_extension_dummy

    def _prepare_file_paths_and_skip_logic(
        self,
        media: Track | Video,
        file_template: str,
        quality_audio: Quality | None,
        list_position: int,
        list_total: int,
    ) -> tuple[pathlib.Path, str, bool, bool]:
        """Prepare file paths and determine skip logic.

        Args:
            media (Track | Video): Media item.
            file_template (str): Template for file naming.
            quality_audio (Quality | None): Audio quality setting.
            list_position (int): Position in list.
            list_total (int): Total items in list.

        Returns:
            tuple[pathlib.Path, str, bool, bool]: (path_media_dst, file_extension_dummy, skip_file, skip_download)
        """
        path_media_dst, file_extension_dummy = self._destination_path(
            media, file_template, quality_audio, list_position, list_total
        )

        # Compute if and how downloads need to be skipped.
        skip_download: bool = False

        if self.skip_existing:
            path_media_found: pathlib.Path | None = (
                self._existing_same_item_at(path_media_dst, media)
                if check_file_exists(path_media_dst, extension_ignore=False)
                else None
            )
            skip_file: bool = path_media_found is not None
            # The item may live at a numbered variant, not the base name. The
            # path handed back from here is the one the m3u and the symlink
            # follow, so it has to be the file that actually IS this track,
            # never the colliding stranger occupying the base.
            if path_media_found is not None:
                path_media_dst = path_media_found

            if self.settings.data.symlink_to_track and not isinstance(media, Video):
                # Compute symlink tracks path, sanitize and check if file exists
                file_name_track_dir_relative: str = format_path_media(
                    self.settings.data.format_track,
                    media,
                    delimiter_artist=self.settings.data.filename_delimiter_artist,
                    delimiter_album_artist=self.settings.data.filename_delimiter_album_artist,
                    use_primary_album_artist=self.settings.data.use_primary_album_artist,
                    illegal_replacement=self._illegal_replacement(),
                    illegal_map=self._illegal_map(),
                )
                path_media_track_dir: pathlib.Path = (
                    pathlib.Path(self.path_base).expanduser() / (file_name_track_dir_relative + file_extension_dummy)
                ).absolute()
                path_media_track_dir = pathlib.Path(path_file_sanitize(path_media_track_dir, adapt=True))
                # Identity, not just the name: a DIFFERENT track whose name
                # collides used to make this one look downloaded, so nothing was
                # fetched and the playlist entry ended up pointing at the
                # stranger. The move below (media_move_and_symlink) asks the very
                # same question, and the two have to agree.
                file_exists_track_dir: bool = (
                    check_file_exists(path_media_track_dir, extension_ignore=False)
                    and self._existing_same_item_at(path_media_track_dir, media) is not None
                )
                file_exists_playlist_dir: bool = (
                    not file_exists_track_dir and skip_file and not path_media_dst.is_symlink()
                )
                skip_download = file_exists_playlist_dir or file_exists_track_dir

                # If file exists in playlist dir but not in track dir, we don't skip the file itself
                if skip_file and file_exists_playlist_dir:
                    skip_file = False
        else:
            skip_file: bool = False

        return path_media_dst, file_extension_dummy, skip_file, skip_download

    def _adjust_quality_settings(
        self, quality_audio: Quality | None, quality_video: QualityVideo | None
    ) -> tuple[Quality | None, QualityVideo | None]:
        """Adjust quality settings and return previous values.

        Args:
            quality_audio (Quality | None): Audio quality setting.
            quality_video (QualityVideo | None): Video quality setting.

        Returns:
            tuple[Quality | None, QualityVideo | None]: Previous quality settings.
        """
        quality_audio_old: Quality | None = None
        quality_video_old: QualityVideo | None = None

        if quality_audio:
            quality_audio_old = self.adjust_quality_audio(quality_audio)

        if quality_video:
            quality_video_old = self.adjust_quality_video(quality_video)

        return quality_audio_old, quality_video_old

    def _download_and_process_media(
        self,
        media: Track | Video,
        path_media_dst: pathlib.Path,
        skip_download: bool,
        is_parent_album: bool,
        file_extension_dummy: str,
        event_stop: Event | None = None,
    ) -> tuple[bool, pathlib.Path]:
        """Download and process media file.

        Args:
            media (Track | Video): Media item.
            path_media_dst (pathlib.Path): Destination file path.
            skip_download (bool): Whether to skip download.
            is_parent_album (bool): Whether this is a parent album.
            file_extension_dummy (str): Dummy file extension.
            event_stop (Event | None, optional): Event to stop the download. Defaults to None.

        Returns:
            tuple[bool, pathlib.Path]: (Whether download was successful, the FINAL destination path).
                The path reflects the true stream extension and any uniquify suffix, so the
                caller's skip/return-path/symlink logic uses the file actually written.
        """
        if skip_download:
            return True, path_media_dst

        # Get stream information and final file extension
        stream_manifest, file_extension, do_flac_extract, media_stream = self._get_stream_info(media)

        if stream_manifest is None and isinstance(media, Track):
            return False, path_media_dst

        # Update path to the TRUE stream extension. The step-2 path was guessed from a
        # possibly-None quality (the Waves UI never passes quality_audio), so the real
        # extension is only known now. Everything downstream (skip check, returned path,
        # symlink) must use this corrected path, not the guess.
        if path_media_dst.suffix != file_extension:
            path_media_dst = path_media_dst.with_suffix(file_extension)
            path_media_dst = pathlib.Path(path_file_sanitize(path_media_dst, adapt=True))

        # Re-evaluate skip-existing against the TRUE extension. The step-2 check may have
        # looked for the wrong extension (e.g. .flac while the stream is .m4a) and missed
        # an already-downloaded file.
        if self.skip_existing and check_file_exists(path_media_dst, extension_ignore=False):
            path_media_found = self._existing_same_item_at(path_media_dst, media)

            if path_media_found is not None:
                self.fn_logger.debug(f"Download skipped, since file exists: '{path_media_found}'")

                # The path the caller records, so it must be the file that IS
                # this track, which may be a numbered variant of the base name.
                return True, path_media_found

        self._ensure_directory(path_media_dst.parent)

        # Perform actual download.
        #
        # De-duplicate distinct tracks whose sanitized names collide. When skip_existing is
        # on, a pre-existing file for the SAME track was already skipped above, so any file
        # that shows up at this destination belongs to a DIFFERENT track. Move without
        # overwriting and uniquify the name on conflict, so the earlier track isn't clobbered
        # (this also covers the concurrent case where two colliding tracks are in flight at
        # once and neither existed at skip-check time). With skip_existing off the user has
        # opted into replacing, so keep the historical overwrite behavior.
        return self._perform_actual_download(
            media,
            path_media_dst,
            stream_manifest,
            do_flac_extract,
            is_parent_album,
            media_stream,
            event_stop,
        )

    def _get_stream_info(self, media: Track | Video) -> tuple[StreamManifest | None, str, bool, Stream | None]:
        """Get stream information for media.

        Args:
            media (Track | Video): Media item.

        Returns:
        tuple[StreamManifest | None, str, bool, Stream | None]: Stream info.
        """
        stream_manifest: StreamManifest | None = None
        media_stream: Stream | None = None
        do_flac_extract: bool = False
        file_extension: str = ""

        # CRITICAL: This lock is intentionally broad and serializes all
        # stream-fetching (Phase 1) to prevent a critical race condition.
        #
        # THE PROBLEM:
        # The single, shared session (self.tidal.session) must change its
        # credentials to switch between Atmos and Hi-Res/Normal streams.
        #
        # THE RACE CONDITION IT FIXES:
        # If this lock is released *before* get_stream() is called,
        # another thread could change the session (e.g., back to "Normal")
        # right after this thread switched it to "Atmos". This would
        # cause this thread to call get_stream() with the wrong credentials,
        # resulting in the API returning AAC 320 instead of Atmos.
        #
        # THE TRADEOFF:
        # This creates a "tollbooth" bottleneck, serializing the get_stream()
        # calls. However, the *actual* segment downloads (Phase 2)
        # still run in parallel, governed by `downloads_concurrent_max`.
        #
        # DO NOT "OPTIMIZE" THIS by making the lock more granular.
        # Correctness > Performance.

        with self.tidal.stream_lock:
            try:
                if isinstance(media, Track):
                    track_info = self._get_track_stream_info(media)

                    if track_info.stream_manifest is None:
                        return None, "", False, None

                    stream_manifest = track_info.stream_manifest
                    file_extension = track_info.file_extension
                    do_flac_extract = track_info.requires_flac_extraction
                    media_stream = track_info.media_stream

                elif isinstance(media, Video):
                    # Videos always require the normal session
                    if not self.tidal.restore_normal_session():
                        self.fn_logger.error(f"Failed to restore normal session for video: {media.id}")
                        return None, "", False, None

                    file_extension = AudioExtensions.MP4 if self.settings.data.video_convert_mp4 else VideoExtensions.TS

                    stream_manifest = None
                    media_stream = None
                    do_flac_extract = False

                else:
                    self.fn_logger.error(f"Unknown media type for stream info: {type(media)}")
                    return None, "", False, None

            except TooManyRequests:
                self.fn_logger.exception(
                    f"Too many requests against TIDAL backend. Skipping '{log_content(name_builder_item(media))}'. "
                    f"Consider to activate delay between downloads."
                )
                return None, "", False, None

            except (StreamNotAvailable, ObjectNotFound, AssetNotAvailable):
                # TIDAL knows the track but will not serve a stream for it: the
                # 404 / "no stream" answer for an asset that is genuinely gone.
                # A refusal, not a failure, so mark it UNAVAILABLE (issue #25).
                self._note_unavailable(media)
                self.fn_logger.info(
                    f"This item is not available for listening anymore on TIDAL. Skipping: "
                    f"{log_content(name_builder_item(media))}"
                )
                return None, "", False, None

            except HTTPError as error:
                # A 401/403 whose body says the asset itself is withheld (e.g.
                # subStatus 4005 "Asset is not ready for playback") is TIDAL
                # refusing the content, not our session, so it is UNAVAILABLE
                # too. Anything else (a dead session, a 5xx, a network error) is
                # a real failure and keeps the old "something went wrong" path.
                if _tidal_refuses_asset(error) is not None:
                    self._note_unavailable(media)
                    self.fn_logger.info(
                        f"This item is not available for listening anymore on TIDAL. Skipping: "
                        f"{log_content(name_builder_item(media))}"
                    )
                    return None, "", False, None
                self.fn_logger.exception(f"Something went wrong. Skipping '{log_content(name_builder_item(media))}'.")
                return None, "", False, None

            except Exception:
                self.fn_logger.exception(f"Something went wrong. Skipping '{log_content(name_builder_item(media))}'.")
                return None, "", False, None

        return stream_manifest, file_extension, do_flac_extract, media_stream

    def _get_track_stream_info(self, media: Track) -> TrackStreamInfo:
        """
        Gets stream info for a Track, handling Atmos/Normal session switching.

        Args:
            media: The track to get stream information for.

        Returns:
            TrackStreamInfo: Container with stream manifest, file extension,
                            FLAC extraction flag, and media stream object.
                            Returns TrackStreamInfo with None/empty values if fails.
        """
        # The Atmos session serves two cases: the user asked for Atmos and the
        # track has it, or the track has NOTHING ELSE (TIDAL lists the Atmos
        # version as its own id with no stereo stream, so the normal session
        # has no stream to offer). The second clause is what downloads an
        # Atmos-only track when the setting is off, instead of skipping it and
        # leaving a hole in the album.
        modes = getattr(media, "audio_modes", None) or []
        has_atmos = AudioMode.dolby_atmos.value in modes
        atmos_only = bool(modes) and all(mode == AudioMode.dolby_atmos.value for mode in modes)
        want_atmos = has_atmos and (self.settings.data.download_dolby_atmos or atmos_only)

        if want_atmos:
            if not self.tidal.switch_to_atmos_session():
                self.fn_logger.error(f"Failed to switch to Atmos session for track: {media.id}")
                return TrackStreamInfo(None, "", False, None)
        else:
            if not self.tidal.restore_normal_session():
                self.fn_logger.error(f"Failed to restore normal session for track: {media.id}")
                return TrackStreamInfo(None, "", False, None)

        media_stream = self.session.track(media.id).get_stream() if want_atmos else media.get_stream()

        stream_manifest = media_stream.get_stream_manifest()
        file_extension = stream_manifest.file_extension
        requires_flac_extraction = False

        if self.settings.data.extract_flac and (
            stream_manifest.codecs.upper() == Codec.FLAC and file_extension != AudioExtensions.FLAC
        ):
            file_extension = AudioExtensions.FLAC
            requires_flac_extraction = True

        return TrackStreamInfo(
            stream_manifest=stream_manifest,
            file_extension=file_extension,
            requires_flac_extraction=requires_flac_extraction,
            media_stream=media_stream,
        )

    def _note_stage(self, media: Track | Video, frac: float) -> None:
        """Post-download finalize progress (0-100) for this media item. The
        stream bytes finishing is not the work finishing: extraction, the
        remux, tagging with its lyrics and cover fetches, and the move to the
        destination all still run. A no-op here; the GUI's tracked download
        overrides it to fill the queue row's FINISHING word. Called at step
        boundaries, so the fill moves in honest jumps, not a fake glide."""

    def _note_delivered(self, media: Track | Video) -> None:
        """The item's file and sidecars are fully on disk; only post-processing
        and the deliberate inter-download delay remain. A no-op here; the GUI's
        tracked download overrides it to flip the queue row to its finished
        word right away instead of letting it sit through the delay."""

    def _note_unavailable(self, media: Track | Video | Album) -> None:
        """TIDAL refuses to stream this item, so the engine skips it. A no-op
        here; the GUI's tracked download overrides it to tell a refusal apart
        from a download that failed. They are not the same news: a failure is
        something to retry, a refusal is TIDAL saying the item is gone, and
        nothing the app does will fetch it."""

    def _note_progress_task(self, media: Track | Video, p_task: TaskID) -> None:
        """The rich progress task this item's segments report into. A no-op
        here; the GUI's tracked download overrides it so a queue row can follow
        its own item's percentage. Task descriptions are display names cut to 30
        characters, so every track of a release with a long artist credit
        registers the identical description and they cannot tell each other
        apart. The TaskID can."""

    def _finalize_plan(
        self,
        media: Track | Video,
        path_media_dst: pathlib.Path,
        do_flac_extract: bool,
        media_stream: Stream | None,
    ) -> dict[str, float]:
        """Cumulative FINISHING-fill fractions per finalize step: weight only
        the steps this item will actually run, so each step's share is awarded
        as it completes. Fixed milestones read wrong: the ffmpeg steps are
        skipped for most items, so the word opened two-thirds full and the
        slow work (tagging with its lyrics/cover fetches, the move to the
        library) was squeezed into the last quarter. The weights are rough
        step costs; a step predicted here but skipped at runtime just passes
        its share through instantly, which is invisible."""
        will_convert = isinstance(media, Video) and self.settings.data.video_convert_mp4
        will_extract = isinstance(media, Track) and self.settings.data.extract_flac and do_flac_extract
        will_downsample = (
            isinstance(media, Track)
            and self.settings.data.downsample_enabled
            and (will_extract or path_media_dst.suffix == AudioExtensions.FLAC)
        )
        will_remux = (
            isinstance(media, Track)
            and path_media_dst.suffix in (AudioExtensions.M4A, AudioExtensions.MP4)
            and not getattr(media_stream, "is_bts", False)
            and bool(self.settings.data.path_binary_ffmpeg)
        )
        plan = {
            "convert": 35 * will_convert,
            "extract": 20 * will_extract,
            "downsample": 25 * will_downsample,
            "remux": 8 * will_remux,
            "tag": 30,
            "move": 15,
        }
        total_weight = sum(plan.values())
        acc = 0.0
        cum: dict[str, float] = {}
        for step_name, weight in plan.items():
            acc += weight
            cum[step_name] = 100.0 * acc / total_weight
        return cum

    def _perform_actual_download(
        self,
        media: Track | Video,
        path_media_dst: pathlib.Path,
        stream_manifest: StreamManifest | None,
        do_flac_extract: bool,
        is_parent_album: bool,
        media_stream: Stream | None,
        event_stop: Event | None = None,
    ) -> tuple[bool, pathlib.Path]:
        """Perform the actual download and processing.

        Args:
            media (Track | Video): Media item.
            path_media_dst (pathlib.Path): Destination file path.
            stream_manifest (StreamManifest | None): Stream manifest.
            do_flac_extract (bool): Whether to extract FLAC.
            is_parent_album (bool): Whether this is a parent album.
            media_stream (Stream | None): Media stream.
            event_stop (Event | None, optional): Event to stop the download. Defaults to None.

        Returns:
            tuple[bool, pathlib.Path]: (Whether download was successful, the final destination path).
        """
        # Create a temp directory and file.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_path_dir:
            tmp_path_file: pathlib.Path = pathlib.Path(tmp_path_dir) / str(uuid4())
            tmp_path_file.touch()

            # Download media.
            result_download, tmp_path_file = self._download(
                media=media,
                stream_manifest=stream_manifest,
                path_file=tmp_path_file,
                event_stop=event_stop,
            )

            if not result_download:
                return False, path_media_dst

            cum = self._finalize_plan(media, path_media_dst, do_flac_extract, media_stream)
            self._note_stage(media, 0)

            # Convert video from TS to MP4
            if isinstance(media, Video) and self.settings.data.video_convert_mp4:
                tmp_path_file = self._video_convert(tmp_path_file)

            self._note_stage(media, cum["convert"])

            # Extract FLAC from MP4 container using ffmpeg
            if isinstance(media, Track) and self.settings.data.extract_flac and do_flac_extract:
                tmp_path_file = self._extract_flac(tmp_path_file)

            self._note_stage(media, cum["extract"])

            # Downsample FLAC to the configured target rate/depth (no-op for low-res sources)
            if (
                isinstance(media, Track)
                and self.settings.data.downsample_enabled
                and tmp_path_file.suffix == AudioExtensions.FLAC
            ):
                tmp_path_file = self._downsample_audio(tmp_path_file)

            self._note_stage(media, cum["downsample"])

            # Rebuild the MP4/M4A container so its moov/mvhd carries the real duration.
            # A DASH download is a raw byte-concatenation of fragmented-MP4 segments
            # (_segments_merge), whose top-level moov duration is 0: fragmented MP4 keeps
            # per-sample timing in the moof boxes, not the moov. Strict players (Winamp)
            # then read 0:00 and refuse to play, while lenient ones (VLC, ffmpeg) rescan
            # the fragments. FLAC-extracted tracks are already a native .flac (no moov)
            # and single-file BTS streams carry a complete moov, so both are exempt.
            # The container is keyed off the destination suffix: the temp file is the
            # bare merge output (a uuid with no extension), the true .m4a/.mp4 is only
            # known on path_media_dst.
            if (
                isinstance(media, Track)
                and path_media_dst.suffix in (AudioExtensions.M4A, AudioExtensions.MP4)
                and not getattr(media_stream, "is_bts", False)
                and self.settings.data.path_binary_ffmpeg
            ):
                tmp_path_file = self._faststart_remux(tmp_path_file, path_media_dst.suffix)

            self._note_stage(media, cum["remux"])

            # De-duplicate colliding distinct tracks (skip_existing on: same track was already
            # skipped upstream, so an occupied destination is a different track). Resolve the
            # unique name BEFORE writing lyrics/cover so those sidecars align with the final
            # audio file.
            overwrite: bool = not self.skip_existing
            media_id: str = _waves_item_id(media)
            path_media_dst = pathlib.Path(path_file_sanitize(path_media_dst, adapt=True))
            path_media_dst, name_reserved = self._claim_destination(path_media_dst, media_id, _waves_owned_ids(media))

            if name_reserved is None:
                # Every numbered variant of this name is taken. Nothing is lost
                # here, but the download has nowhere to land and must say so:
                # the old code took the last occupied candidate and the move
                # then refused it as somebody else's file.
                self.fn_logger.error(
                    f"No free name left for '{log_content(path_media_dst.name)}': "
                    f"the name and all {UNIQUIFY_THRESHOLD} of its numbered copies are taken."
                )

                return False, path_media_dst

            try:
                # Tag the temp file and hold the sidecars it produced.
                extras = self._handle_metadata_and_extras(
                    media, tmp_path_file, path_media_dst, is_parent_album, media_stream
                )

                self.fn_logger.info(f"Downloaded item '{log_content(name_builder_item(media))}'.")

                self._note_stage(media, cum["tag"])

                # Move final file to the configured destination directory.
                moved: bool = self._move_file(tmp_path_file, path_media_dst, overwrite=overwrite)

                if moved:
                    self._note_stage(media, cum["move"])
                    # Recorded before the claim is released below, so the name is
                    # never momentarily free between the two.
                    self._record_name_written(path_media_dst, media_id)

                # Sidecars only once the audio is really there. Landing them
                # first left a cover.jpg and a .lrc in the library for a track
                # that never arrived, and a later run would not clean them up
                # (the app never deletes a user-visible file).
                if moved and extras:
                    self._move_extras(extras, path_media_dst)

                return moved, path_media_dst
            finally:
                # Released either way: on success the file now answers for itself on disk,
                # on failure the name was never used and must go back to the pool.
                if name_reserved is not None:
                    with self._names_reserved_lock:
                        self._names_reserved.discard(name_reserved)

    def _handle_metadata_and_extras(
        self,
        media: Track | Video,
        tmp_path_file: pathlib.Path,
        path_media_dst: pathlib.Path,
        is_parent_album: bool,
        media_stream: Stream | None,
    ) -> tuple[pathlib.Path | None, str, pathlib.Path | None] | None:
        """Tag the downloaded temp file and hand back the sidecars it produced.

        The sidecars are NOT moved here. They used to be, which put a cover.jpg
        and a .lrc into the library before the audio they belong to, so a move
        that then failed left them orphaned there, and nothing removes them
        afterwards (the app never deletes a user-visible file). The caller moves
        them once the audio has landed.

        Args:
            media (Track | Video): Media item.
            tmp_path_file (pathlib.Path): Temporary file path.
            path_media_dst (pathlib.Path): Destination file path.
            is_parent_album (bool): Whether this is a parent album.
            media_stream (Stream | None): Media stream.

        Returns:
            tuple[pathlib.Path | None, str, pathlib.Path | None] | None: The temp
                lyrics path, its extension, and the temp cover path. None for a
                video, which has no sidecars.
        """
        if isinstance(media, Video):
            # A converted music video carries tags (metadata_write_video); a
            # raw .ts cannot, MPEG-TS has no tag atoms mutagen can write.
            # Lyrics and cover sidecars stay track/album concepts either way.
            if tmp_path_file.suffix == AudioExtensions.MP4:
                self.metadata_write_video(media, tmp_path_file)
            return None

        tmp_path_lyrics: pathlib.Path | None = None
        tmp_path_cover: pathlib.Path | None = None
        lyrics_suffix: str = EXTENSION_LYRICS

        # Write metadata to file.
        if media_stream:
            _result_metadata, tmp_path_lyrics, lyrics_suffix, tmp_path_cover = self.metadata_write(
                media, tmp_path_file, is_parent_album, media_stream
            )

        return tmp_path_lyrics, lyrics_suffix, tmp_path_cover

    def _move_extras(
        self,
        extras: tuple[pathlib.Path | None, str, pathlib.Path | None],
        path_media_dst: pathlib.Path,
    ) -> None:
        """Move the lyrics and cover sidecars beside an audio file that landed.

        Args:
            extras: The temp lyrics path, its extension, and the temp cover path.
            path_media_dst (pathlib.Path): Where the audio actually landed.
        """
        tmp_path_lyrics, lyrics_suffix, tmp_path_cover = extras

        # Move lyrics file
        if self.settings.data.lyrics_file and tmp_path_lyrics:
            self._move_lyrics(tmp_path_lyrics, path_media_dst, suffix=lyrics_suffix)

        # Move cover file
        if self.settings.data.cover_album_file and tmp_path_cover:
            self._move_cover(tmp_path_cover, path_media_dst)

    def _perform_post_processing(
        self,
        media: Track | Video,
        path_media_dst: pathlib.Path,
        quality_audio: Quality | None,
        quality_video: QualityVideo | None,
        quality_audio_old: Quality | None,
        quality_video_old: QualityVideo | None,
        download_delay: bool,
        skip_file: bool,
        event_stop: Event | None = None,
    ) -> None:
        """Perform post-processing tasks.

        Args:
            media (Track | Video): Media item.
            path_media_dst (pathlib.Path): Destination file path.
            quality_audio (Quality | None): Audio quality setting.
            quality_video (QualityVideo | None): Video quality setting.
            quality_audio_old (Quality | None): Previous audio quality.
            quality_video_old (QualityVideo | None): Previous video quality.
            download_delay (bool): Whether to apply download delay.
            skip_file (bool): Whether file was skipped.
            event_stop (Event | None, optional): Event to stop the download. Defaults to None.
        """
        # If files needs to be symlinked, do postprocessing here.
        if self.settings.data.symlink_to_track and not isinstance(media, Video):
            # Determine file extension for symlink
            file_extension = path_media_dst.suffix
            self.media_move_and_symlink(media, path_media_dst, file_extension)

        # Reset quality settings
        if quality_audio_old is not None:
            self.adjust_quality_audio(quality_audio_old)

        if quality_video_old is not None:
            self.adjust_quality_video(quality_video_old)

        # Apply download delay if needed
        if download_delay and not skip_file:
            time_sleep: float = round(
                random.SystemRandom().uniform(
                    self.settings.data.download_delay_sec_min, self.settings.data.download_delay_sec_max
                ),
                1,
            )

            self.fn_logger.debug(f"Next download will start in {time_sleep} seconds.")

            # Use event_stop or event_abort for interruptible sleep
            if event_stop:
                event_stop.wait(time_sleep)
            elif self.event_abort:
                self.event_abort.wait(time_sleep)
            else:
                time.sleep(time_sleep)

    def media_move_and_symlink(
        self, media: Track | Video, path_media_src: pathlib.Path, file_extension: str
    ) -> pathlib.Path:
        """Move a media file and create a symlink if required.

        Args:
            media (Track | Video): Media item.
            path_media_src (pathlib.Path): Source file path.
            file_extension (str): File extension.

        Returns:
            pathlib.Path: Destination path.
        """
        # Compute tracks path, sanitize and ensure path exists
        # Same spelling as the existence check in
        # _prepare_file_paths_and_skip_logic's symlink branch: the two must
        # agree or a track could be checked in one folder and moved to another.
        file_name_relative: str = format_path_media(
            self.settings.data.format_track,
            media,
            delimiter_artist=self.settings.data.filename_delimiter_artist,
            delimiter_album_artist=self.settings.data.filename_delimiter_album_artist,
            use_primary_album_artist=self.settings.data.use_primary_album_artist,
            illegal_replacement=self._illegal_replacement(),
            illegal_map=self._illegal_map(),
        )
        path_media_dst: pathlib.Path = (
            pathlib.Path(self.path_base).expanduser() / (file_name_relative + file_extension)
        ).absolute()
        path_media_dst = pathlib.Path(path_file_sanitize(path_media_dst, adapt=True))

        self._ensure_directory(path_media_dst.parent)

        # Move item and symlink it
        if path_media_dst != path_media_src:
            # The same three decisions the plain download path makes (see
            # _perform_actual_download): is the destination really THIS item,
            # claim the name before anything else can pick it, and overwrite
            # only when the user turned skip-existing off. Deciding by filename
            # alone used to hand a colliding stranger's file to this track: the
            # audio just downloaded was unlinked and the playlist entry pointed
            # at somebody else's track.
            overwrite: bool = not self.skip_existing
            name_reserved: str | None = None
            media_id: str = _waves_item_id(media)

            if self.skip_existing:
                path_media_found: pathlib.Path | None = (
                    self._existing_same_item_at(path_media_dst, media)
                    if check_file_exists(path_media_dst, extension_ignore=False)
                    else None
                )
                skip_file: bool = path_media_found is not None
                # The track may already live at a NUMBERED VARIANT of the base
                # name. Skipping on that evidence while symlinking to the base
                # deleted the fresh audio and pointed the playlist entry at the
                # colliding stranger's file, so the symlink target must be the
                # file that answered the identity question.
                if path_media_found is not None:
                    path_media_dst = path_media_found
                skip_symlink: bool = path_media_src.is_symlink()
            else:
                skip_file: bool = False
                skip_symlink: bool = False

            if not skip_file:
                # The same claim the plain download path takes, for the same
                # reason: a sibling track of this collection reaches this move
                # on another thread and would otherwise pick the very same name.
                path_media_dst, name_reserved = self._claim_destination(
                    path_media_dst, media_id, _waves_owned_ids(media)
                )

                if name_reserved is None:
                    # No free name in the track folder. The audio is safe where
                    # it is (the playlist folder), so leave it there rather than
                    # move it onto a stranger, and say why.
                    self.fn_logger.error(
                        f"No free name left in the track folder for '{log_content(path_media_dst.name)}': "
                        f"the track stays in the playlist folder."
                    )

                    return path_media_src

            try:
                self._symlink_after_move(path_media_src, path_media_dst, skip_file, skip_symlink, overwrite)

                if not skip_file:
                    self._record_name_written(path_media_dst, media_id)
            finally:
                if name_reserved is not None:
                    with self._names_reserved_lock:
                        self._names_reserved.discard(name_reserved)

        return path_media_dst

    def _symlink_after_move(
        self,
        path_media_src: pathlib.Path,
        path_media_dst: pathlib.Path,
        skip_file: bool,
        skip_symlink: bool,
        overwrite: bool,
    ) -> None:
        """Move the playlist copy into the track folder, leaving a symlink behind.

        Args:
            path_media_src (pathlib.Path): The playlist-folder copy.
            path_media_dst (pathlib.Path): The claimed track-folder destination.
            skip_file (bool): Whether this very item is already at the destination.
            skip_symlink (bool): Whether the source is already a symlink.
            overwrite (bool): Whether an occupied destination may be replaced.
        """
        # Whether the destination is in place: either it was skipped (already present) or moved successfully.
        moved: bool = skip_file

        if not skip_file:
            self.fn_logger.debug(f"Move: {path_media_src} -> {path_media_dst}")
            moved = self._move_file(path_media_src, path_media_dst, overwrite=overwrite)

        # Only replace the source with a symlink once the destination actually exists, otherwise a failed
        # move would leave a broken symlink pointing at a missing file (and the source already unlinked).
        if not moved or skip_symlink:
            return

        self.fn_logger.debug(f"Symlink: {path_media_src} -> {path_media_dst}")
        path_media_dst_relative: pathlib.Path = path_media_dst.relative_to(path_media_src.parent, walk_up=True)

        # The playlist directory may not exist yet: when the track was
        # found already in the track dir the download (and with it the
        # only ensure of this parent) was skipped entirely, so the
        # symlink would raise FileNotFoundError and fail the whole
        # playlist job. Ensure it, and never let a failed symlink (a
        # convenience pointer, the audio is safe in the track dir)
        # crash the run.
        try:
            self._ensure_directory(path_media_src.parent)
            if self._unlink_with_retry(path_media_src):
                path_media_src.symlink_to(path_media_dst_relative)
            else:
                self.fn_logger.error(f"Unable to replace source with symlink: {path_media_src}")
        except OSError as error:
            # fn_logger may be a plain callable wrapper without .exception
            self.fn_logger.error(f"Unable to create playlist symlink {path_media_src}: {error}")  # noqa: TRY400

    def adjust_quality_audio(self, quality: Quality) -> Quality:
        """Temporarily set audio quality and return the previous value.

        Args:
            quality (Quality): New audio quality.

        Returns:
            Quality: Previous audio quality.
        """
        # Save original quality settings
        quality_old: Quality = self.session.audio_quality
        self.session.audio_quality = quality

        return quality_old

    def adjust_quality_video(self, quality: QualityVideo) -> QualityVideo:
        """Temporarily set video quality and return the previous value.

        Args:
            quality (QualityVideo): New video quality.

        Returns:
            QualityVideo: Previous video quality.
        """
        quality_old: QualityVideo = self.settings.data.quality_video

        self.settings.data.quality_video = quality

        return quality_old

    def _file_operation_retry_delay(self, attempt: int) -> float:
        """Delay before retrying a file operation.

        Args:
            attempt (int): Zero-based retry attempt number.

        Returns:
            float: Delay in seconds before the next attempt.
        """
        return self._FILE_OPERATION_RETRY_DELAY_SEC * (attempt + 1)

    def _retry_file_operation(self, operation: Callable[[], bool], description: str) -> bool:
        """Retry a file operation

        Args:
            operation (Callable[[], bool]): Operation to run.
            description (str): Human-readable operation description for logs.

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        error_last: OSError | None = None

        for attempt in range(self._FILE_OPERATION_RETRIES):
            try:
                return operation()
            except OSError as error:
                error_last = error
                if attempt == self._FILE_OPERATION_RETRIES - 1:
                    break
                delay_sec: float = self._file_operation_retry_delay(attempt)
                self.fn_logger.debug(f"File operation failed ({description}); retrying in {delay_sec:.1f}s: {error}")
                time.sleep(delay_sec)

        self.fn_logger.error(f"File operation failed after retries ({description}): {error_last}")
        return False

    def _unlink_with_retry(self, path_file: pathlib.Path) -> bool:
        """Unlink a file with retries for transient file locks.

        Args:
            path_file (pathlib.Path): File path to unlink.

        Returns:
            bool: True if the file is absent or was removed, False otherwise.
        """

        def operation() -> bool:
            path_file.unlink(missing_ok=True)
            return True

        return self._retry_file_operation(operation, f"unlink {path_file}")

    def _makedirs_with_retry(self, path_dir: pathlib.Path) -> None:
        """Create a destination directory, retrying transient failures.

        A network mount (macOS SMB in particular) can return a spurious EACCES
        or EIO for a short window while it reconnects under I/O load; a bare
        makedirs turns that blip into a whole failed track or album. Retries on
        the shared file-operation cadence and re-raises the final error, so a
        genuinely unwritable destination still fails the download loudly.

        Args:
            path_dir (pathlib.Path): Directory to create (parents included).
        """
        error_last: OSError | None = None

        for attempt in range(self._FILE_OPERATION_RETRIES):
            try:
                os.makedirs(path_dir, exist_ok=True)
            except OSError as error:
                error_last = error
                if attempt == self._FILE_OPERATION_RETRIES - 1:
                    break
                delay_sec: float = self._file_operation_retry_delay(attempt)
                self.fn_logger.debug(f"Could not create directory ({path_dir}); retrying in {delay_sec:.1f}s: {error}")
                time.sleep(delay_sec)
            else:
                return

        raise error_last

    def _ensure_directory(self, path_dir: pathlib.Path) -> None:
        """Create a destination directory once per instance, then remember it.

        Wraps :meth:`_makedirs_with_retry` with a memo so repeat calls for the
        same directory (every track of an album ensures the album directory,
        and each move re-ensures it for audio, lyrics and cover) skip the
        filesystem entirely. If the directory vanishes mid-download the move's
        FileNotFoundError handler evicts the memo entry, so the retry recreates
        it (see :meth:`_move_file`).

        Args:
            path_dir (pathlib.Path): Directory to ensure (parents included).
        """
        key = str(path_dir)
        if key in self._dirs_ensured:
            return
        self._makedirs_with_retry(path_dir)
        self._dirs_ensured.add(key)

    # Buffer for bulk copies onto the destination. shutil's default POSIX
    # buffer is 64 KiB, which turns a 40 MB track into ~640 sequential write
    # round-trips on a network mount (SMB especially); 8 MiB lets the mount's
    # client coalesce the same file into a handful of large writes. Worst-case
    # transient RAM is one buffer per concurrent move (downloads_concurrent_max,
    # so typically 3 x 8 MiB).
    _COPY_BUFFER_BYTES: int = 8 * 1024 * 1024

    def _copy_file_contents(self, path_source: pathlib.Path, path_destination: pathlib.Path) -> None:
        """Copy file bytes with a large buffer, without copying metadata.

        Replaces shutil.copy2 for the temp-to-destination copy: copy2's
        copystat tail issues chmod/utime/xattr calls the destination (a network
        share) often cannot honor, and the source metadata is a meaningless
        local temp-file timestamp anyway, so those round-trips bought nothing.

        Args:
            path_source (pathlib.Path): Local source file.
            path_destination (pathlib.Path): Destination file (created/truncated).
        """
        with path_source.open("rb") as file_source, path_destination.open("wb") as file_destination:
            shutil.copyfileobj(file_source, file_destination, length=self._COPY_BUFFER_BYTES)
            # Flush to stable storage before the caller renames this over the
            # final name: the swap's crash-safety promise (a power cut leaves
            # at most the throwaway temp, never a truncated file under the
            # real name) holds only if the bytes are durable before rename.
            file_destination.flush()
            os.fsync(file_destination.fileno())

    def _move_file(
        self,
        path_file_source: pathlib.Path,
        path_file_destination: str | pathlib.Path,
        overwrite: bool = True,
        skip_if_exists: bool = False,
    ) -> bool:
        """Move a file from source to destination.

        Args:
            path_file_source (pathlib.Path): Source file path.
            path_file_destination (str | pathlib.Path): Destination file path.
            overwrite (bool): Whether to overwrite an existing destination file.
            skip_if_exists (bool): Whether an existing destination should be treated as success.

        Returns:
            bool: True if moved, False otherwise.
        """
        path_destination: pathlib.Path = pathlib.Path(path_file_destination)

        # Check if the file was downloaded
        if not path_file_source or not path_file_source.is_file():
            return False

        def operation() -> bool:
            # Ensured inside the retried operation (memoized, usually free): if
            # the directory vanishes mid-download, the FileNotFoundError in
            # _stage_and_swap evicts the memo and the next retry recreates it.
            self._ensure_directory(path_destination.parent)

            if skip_if_exists and path_destination.exists() and not _is_truncated_leftover(path_destination):
                path_file_source.unlink(missing_ok=True)
                return True

            if path_destination.exists():
                if not overwrite and not _is_truncated_leftover(path_destination):
                    # Nothing raised, so the retry wrapper has nothing to log: say it here or
                    # a finished download disappears without a word (issue #15 follow-up).
                    # In-process collisions cannot reach this any more (the name was claimed
                    # before the metadata step), so this means another writer, and guessing a
                    # new name would strand the lyrics and cover already written for this one.
                    self.fn_logger.error(
                        f"Destination is already occupied by another writer, "
                        f"leaving the download out of the library: '{path_destination}'"
                    )

                    return False
                return self._stage_and_swap(path_file_source, path_destination, skip_if_exists)

            # Fresh destination. Prefer a single atomic rename within the same
            # filesystem. When the download temp dir and the library live on
            # different filesystems (e.g. an external drive or network mount), a
            # rename can't cross them, so copy the bytes into a hidden temp file in
            # the destination directory and atomically swap it into the final name.
            # Either way the real path only ever appears complete: an interrupted
            # move (crash, power loss) leaves at most the throwaway temp file, never
            # a half-written file under the real name that a later run would mistake
            # for a finished download and skip.
            #
            # replace() overwrites whatever appeared since the exists() check above.
            # No download of ours can be there (colliding names are claimed before the
            # metadata step), so that window is another writer's, and it is not one
            # this process can close on a network mount.
            try:
                path_file_source.replace(path_destination)
            except OSError as error:
                # The fallback below handles every one of these, and narrowing
                # the catch to EXDEV would regress the network-mount behavior it
                # was hard-won for. But swallowing the errno silently left field
                # reports with nothing to go on: a destination that is read-only
                # (EACCES) or out of space (ENOSPC) looks exactly like a plain
                # cross-filesystem move until the copy fails too. Leave a
                # breadcrumb naming the errno, and the file only by its name.
                logger.info(
                    "Rename onto the destination failed (errno %s); copying instead: %s",
                    error.errno,
                    log_content(path_destination.name),
                )
            else:
                return True

            return self._stage_and_swap(path_file_source, path_destination, skip_if_exists)

        moved: bool = self._retry_file_operation(operation, f"move {path_file_source} -> {path_destination}")

        # The file is in place; drop the xattrs macOS attached on creation so
        # WebDAV-backed destinations do not keep a ._ AppleDouble ghost per file.
        if moved:
            strip_apple_double(path_destination)

        return moved

    def _stage_and_swap(
        self,
        path_file_source: pathlib.Path,
        path_destination: pathlib.Path,
        skip_if_exists: bool,
    ) -> bool:
        """Copy into a hidden temp sibling and atomically swap it into place.

        On failure the temp file is cleaned up best-effort so no partial ever
        sits under a name a later run would trust. A FileNotFoundError also
        evicts the destination directory from the ensured-dirs memo (the user
        deleted it mid-download; the caller's retry recreates it). Losing a
        rename race when skip_if_exists is set counts as success: two tracks
        of one album can land the shared cover.jpg together, and some network
        filesystems answer rename-over-existing with EEXIST instead of
        replacing, but the file we wanted is there either way.

        Args:
            path_file_source (pathlib.Path): Local source file (consumed on success).
            path_destination (pathlib.Path): Final destination path.
            skip_if_exists (bool): Whether an existing destination counts as success.

        Returns:
            bool: True if the destination file is in place.
        """
        path_destination_tmp: pathlib.Path = _staging_path(path_destination)
        try:
            self._copy_file_contents(path_file_source, path_destination_tmp)
            path_destination_tmp.replace(path_destination)
            path_file_source.unlink(missing_ok=True)
        except OSError as error:
            if isinstance(error, FileNotFoundError):
                self._dirs_ensured.discard(str(path_destination.parent))
            # Best-effort cleanup: never let a failing cleanup mask the original error or leak the tmp file.
            with contextlib.suppress(OSError):
                path_destination_tmp.unlink(missing_ok=True)
            if isinstance(error, FileExistsError) and skip_if_exists:
                path_file_source.unlink(missing_ok=True)
                return True
            raise
        return True

    def _move_lyrics(
        self, path_lyrics: pathlib.Path, file_media_dst: pathlib.Path, suffix: str = EXTENSION_LYRICS
    ) -> bool:
        """Move a lyrics file to the destination.

        Args:
            path_lyrics (pathlib.Path): Source lyrics file.
            file_media_dst (pathlib.Path): Destination media file path.
            suffix (str): Lyrics file extension, ".lrc" (timed) or ".txt" (untimed).

        Returns:
            bool: True if moved, False otherwise.
        """
        # Build tmp lyrics filename
        path_file_lyrics: pathlib.Path = file_media_dst.with_suffix(suffix)
        # The same rule the audio follows: with skipping on nothing already in
        # the library is written over, with it off replacing is what was asked
        # for. Landing with overwrite on regardless meant a re-fetch replaced a
        # .lrc somebody had timed by hand. skip_if_exists keeps an existing
        # sidecar quietly (as the cover does): it is a normal outcome, not the
        # occupied-destination collision _move_file otherwise reports.
        result: bool = self._move_file(
            path_lyrics,
            path_file_lyrics,
            overwrite=not self.skip_existing,
            skip_if_exists=self.skip_existing,
        )

        return result

    def _move_cover(self, path_cover: pathlib.Path, file_media_dst: pathlib.Path) -> bool:
        """Move a cover file to the destination.

        Args:
            path_cover (pathlib.Path): Source cover file.
            file_media_dst (pathlib.Path): Destination media file path.

        Returns:
            bool: True if moved, False otherwise.
        """
        # Build tmp lyrics filename
        path_file_cover: pathlib.Path = file_media_dst.parent / COVER_NAME
        result: bool = self._move_file(path_cover, path_file_cover, overwrite=False, skip_if_exists=True)

        return result

    def lyrics_to_file(self, dir_destination: pathlib.Path, lyrics: str) -> str:
        """Write lyrics to a temporary file.

        Args:
            dir_destination (pathlib.Path): Directory for the temp file.
            lyrics (str): Lyrics content.

        Returns:
            str: Path to the temp file.
        """
        return self.write_to_tmp_file(dir_destination, mode="x", content=lyrics)

    def cover_to_file(self, dir_destination: pathlib.Path, image: bytes) -> str:
        """Write cover image to a temporary file.

        Args:
            dir_destination (pathlib.Path): Directory for the temp file.
            image (bytes): Image data.

        Returns:
            str: Path to the temp file.
        """
        return self.write_to_tmp_file(dir_destination, mode="xb", content=image)

    def write_to_tmp_file(self, dir_destination: pathlib.Path, mode: str, content: str | bytes) -> str:
        """Write content to a temporary file.

        Args:
            dir_destination (pathlib.Path): Directory for the temp file.
            mode (str): File open mode.
            content (str | bytes): Content to write.

        Returns:
            str: Path to the temp file.
        """
        result: pathlib.Path = dir_destination / str(uuid4())
        encoding: str | None = "utf-8" if isinstance(content, str) else None

        try:
            with open(result, mode=mode, encoding=encoding) as f:
                f.write(content)
        except OSError:
            result = ""

        return result

    @staticmethod
    def cover_data(url: str | None = None, path_file: str | None = None) -> str | bytes:
        """Retrieve cover image data from a URL or file.

        Args:
            url (str | None, optional): URL to download image from. Defaults to None.
            path_file (str | None, optional): Path to image file. Defaults to None.

        Returns:
            str | bytes: Image data or empty string on failure.
        """
        result: str | bytes = ""

        if url:
            response = None
            try:
                # Shared pooled session: cover art rides an existing warm
                # connection instead of paying TLS setup per track.
                response = Download._shared_http().get(url, timeout=REQUESTS_TIMEOUT_SEC)
                response.raise_for_status()
                result = response.content
            except requests.RequestException:
                # Silently handle download errors (static method has no logger access)
                pass
            finally:
                if response:
                    response.close()
        elif path_file:
            try:
                with open(path_file, "rb") as f:
                    result = f.read()
            except OSError:
                # Silently handle file read errors (static method has no logger access)
                pass

        return result

    @staticmethod
    def _want_cover_file(save_cover: bool, is_parent_album: bool, single_track: bool) -> bool:
        """Whether to write the separate cover.jpg. The master toggle (save_cover)
        must be on; then album/collection downloads always qualify, and a lone
        single-track download qualifies only when the user opted in (single_track)."""
        return bool(save_cover) and (bool(is_parent_album) or bool(single_track))

    @staticmethod
    def _cover_file_dimension(embedded: CoverDimensions, pref: str) -> CoverDimensions:
        """Resolve the saved cover.jpg size. 'follow' (or an unknown value) uses
        the embedded size; otherwise a CoverDimensions member name."""
        if pref == "follow":
            return embedded
        try:
            return CoverDimensions[pref]
        except KeyError:
            return embedded

    def _album_cover_file_data(
        self, track: Track, embedded_data, embedded_dim: CoverDimensions, file_dim: CoverDimensions
    ):
        """Cover bytes for the separate cover.jpg at ``file_dim``, reusing the
        already-fetched embedded bytes when the two sizes match (no re-download)."""
        if file_dim == CoverDimensions.PxORIGIN:
            return self.cover_data(url=track.album.image(CoverDimensions.PxORIGIN))
        if file_dim == embedded_dim:
            return embedded_data
        return self.cover_data(url=track.album.image(int(file_dim)))

    def _retrieve_lyrics(self, track: Track) -> tuple[str, str, str]:
        """Fetch lyrics for a track, LRCLIB first, TIDAL as the fallback.

        TIDAL serves machine-transcribed lyrics for track IDs without
        human-submitted text (recent re-recordings especially), while LRCLIB
        carries community-synced lyrics, so LRCLIB wins when the preference is
        on. A miss or outage there falls through to TIDAL's own lyrics, so the
        worst case is exactly the historical behaviour.

        Returns:
            tuple[str, str, str]: (lyrics, synced, unsynced); lyrics is the
            best available form (synced over unsynced), all empty when neither
            source has any.
        """
        lyrics: str = ""
        lyrics_synced: str = ""
        lyrics_unsynced: str = ""

        if getattr(self.settings.data, "lyrics_prefer_lrclib", True):
            lyrics_synced, lyrics_unsynced = fetch_lrclib_lyrics(
                self._shared_http(),
                artist=track.artist.name if track.artist else "",
                title=name_builder_title(track),
                album=track.album.name if track.album else "",
                duration=track.duration or 0,
                title_bare=track.name,
            )
            lyrics = lyrics_synced or lyrics_unsynced

        if not lyrics:
            try:
                lyrics_obj = track.lyrics()

                if lyrics_obj.text:
                    lyrics_unsynced = lyrics_obj.text
                    lyrics = lyrics_unsynced
                if lyrics_obj.subtitles:
                    lyrics_synced = lyrics_obj.subtitles
                    lyrics = lyrics_synced
            except Exception:
                lyrics = ""
                self.fn_logger.debug(f"Could not retrieve lyrics for `{log_content(name_builder_item(track))}`.")

        return lyrics, lyrics_synced, lyrics_unsynced

    def metadata_write(
        self, track: Track, path_media: pathlib.Path, is_parent_album: bool, media_stream: Stream
    ) -> tuple[bool, pathlib.Path | None, str, pathlib.Path | None]:
        """Write metadata, lyrics, and cover to a media file.

        Args:
            track (Track): Track object.
            path_media (pathlib.Path): Path to media file.
            is_parent_album (bool): Whether this is a parent album.
            media_stream (Stream): Stream object.

        Returns:
            tuple[bool, pathlib.Path | None, str, pathlib.Path | None]: (Success,
            path to lyrics, lyrics file suffix, path to cover). The suffix is
            ".lrc" for timed lyrics and ".txt" for untimed ones.
        """
        result: bool = False
        path_lyrics: pathlib.Path | None = None
        lyrics_suffix: str = EXTENSION_LYRICS
        path_cover: pathlib.Path | None = None
        release_date: str = (
            track.album.available_release_date.strftime("%Y-%m-%d")
            if track.album.available_release_date
            else track.album.release_date.strftime("%Y-%m-%d") if track.album.release_date else ""
        )
        copy_right: str = track.copyright if hasattr(track, "copyright") and track.copyright else ""
        isrc: str = track.isrc if hasattr(track, "isrc") and track.isrc else ""
        lyrics_synced: str = ""
        lyrics_unsynced: str = ""
        cover_data: bytes = None
        release_type: str = (
            track.album.type.lower()
            if hasattr(track, "album") and hasattr(track.album, "type") and track.album.type
            else ""
        )

        if self.settings.data.lyrics_embed or self.settings.data.lyrics_file:
            _lyrics, lyrics_synced, lyrics_unsynced = self._retrieve_lyrics(track)

        if self.settings.data.lyrics_file:
            file_lyrics, lyrics_suffix = lyrics_file_choice(
                lyrics_synced,
                lyrics_unsynced,
                getattr(self.settings.data, "lyrics_file_synced_only", False),
            )
            if file_lyrics:
                path_lyrics = self.lyrics_to_file(path_media.parent, file_lyrics)

        cover_dimension = self.settings.data.metadata_cover_dimension
        # The separately-saved cover.jpg can use its own size (see the helpers
        # above); "follow" keeps the historical behaviour of matching embedded.
        cover_file_pref = getattr(self.settings.data, "metadata_cover_file_dimension", "follow") or "follow"
        cover_file_dimension = self._cover_file_dimension(cover_dimension, cover_file_pref)
        want_cover_file = self._want_cover_file(
            self.settings.data.cover_album_file,
            is_parent_album,
            getattr(self.settings.data, "cover_single_track_file", False),
        )

        if self.settings.data.metadata_cover_embed or want_cover_file:
            # Do not write CoverDimensions.PxORIGIN to metadata, since it can exceed max metadata file size (>16Mb)
            url_cover = track.album.image(
                int(cover_dimension) if cover_dimension != CoverDimensions.PxORIGIN else int(CoverDimensions.Px1280)
            )
            cover_data = self.cover_data(url=url_cover)

        if cover_data and want_cover_file:
            cover_data_album_file = self._album_cover_file_data(
                track, cover_data, cover_dimension, cover_file_dimension
            )
            path_cover = self.cover_to_file(path_media.parent, cover_data_album_file)

        metadata_target_upc = MetadataTargetUPC(self.settings.data.metadata_target_upc)
        target_upc: dict[str, str] = METADATA_LOOKUP_UPC[metadata_target_upc]
        explicit: bool = track.explicit if hasattr(track, "explicit") else False
        title = name_builder_title(track)
        title += METADATA_EXPLICIT if explicit and self.settings.data.mark_explicit else ""

        # `None` values are not allowed.
        m: Metadata = Metadata(
            path_file=path_media,
            target_upc=target_upc,
            lyrics=lyrics_synced,
            lyrics_unsynced=lyrics_unsynced,
            copy_right=copy_right,
            title=title,
            artists=[a.name for a in track.artists],
            album=track.album.name if track.album else "",
            tracknumber=track.track_num,
            date=release_date,
            isrc=isrc,
            albumartist=get_album_artists(track),
            totaltrack=track.album.num_tracks if track.album and track.album.num_tracks else 1,
            totaldisc=track.album.num_volumes if track.album and track.album.num_volumes else 1,
            discnumber=track.volume_num if track.volume_num else 1,
            cover_data=cover_data if self.settings.data.metadata_cover_embed else None,
            album_replay_gain=media_stream.album_replay_gain,
            album_peak_amplitude=media_stream.album_peak_amplitude,
            track_replay_gain=media_stream.track_replay_gain,
            track_peak_amplitude=media_stream.track_peak_amplitude,
            url_share=track.share_url if track.share_url and self.settings.data.metadata_write_url else "",
            replay_gain_write=self.settings.data.metadata_replay_gain,
            upc=track.album.upc if track.album and track.album.upc else "",
            explicit=explicit,
            bpm=track.bpm if track.bpm else 0,
            initial_key=format_initial_key(track.key, track.key_scale, self.settings.data.initial_key_format),
            release_type=release_type,
            item_id=_waves_item_id(track),
        )

        try:
            m.save()
            result = True
        except MetadataUnreadable:
            # A truncated/unidentifiable file (e.g. a failed download) can't be tagged.
            # Fail only this item's tagging instead of aborting the whole collection.
            self.fn_logger.exception(
                f"Could not write metadata; file is unreadable: {log_content(name_builder_item(track))}"
            )
            result = False

        return result, path_lyrics, lyrics_suffix, path_cover

    def metadata_write_video(self, video: Video, path_media: pathlib.Path) -> bool:
        """Tag a music video's MP4 container with everything a video has:
        title, artists, release date, explicit rating, thumbnail cover, the
        share URL (when the URL tag is enabled) and the iTunes music-video
        media kind. Only called for converted MP4s (a raw .ts has no tag
        atoms); a tagging failure is logged and never fails the download.
        """
        release_date: str = video.release_date.strftime("%Y-%m-%d") if getattr(video, "release_date", None) else ""
        explicit: bool = bool(getattr(video, "explicit", False))
        title: str = name_builder_title(video)
        title += METADATA_EXPLICIT if explicit and self.settings.data.mark_explicit else ""
        artists: list[str] = [a.name for a in getattr(video, "artists", None) or []]
        primary = getattr(video, "artist", None)
        albumartist: list[str] = [primary.name] if primary is not None and getattr(primary, "name", "") else artists[:1]
        # A video's album is usually an unparsed placeholder; only a real
        # name is worth writing.
        album_name: str = getattr(getattr(video, "album", None), "name", "") or ""

        cover_data: bytes | None = None
        if self.settings.data.metadata_cover_embed and getattr(video, "cover", None):
            try:
                # 1080x720 is the largest thumbnail TIDAL serves for videos.
                cover_data = self.cover_data(url=video.image(1080, 720))
            except Exception:
                self.fn_logger.debug(f"No usable thumbnail for video {video.id}; tagging without a cover.")

        metadata_target_upc = MetadataTargetUPC(self.settings.data.metadata_target_upc)
        m: Metadata = Metadata(
            path_file=path_media,
            target_upc=METADATA_LOOKUP_UPC[metadata_target_upc],
            title=title,
            artists=artists,
            albumartist=albumartist,
            album=album_name,
            date=release_date,
            cover_data=cover_data,
            url_share=(
                video.share_url if getattr(video, "share_url", "") and self.settings.data.metadata_write_url else ""
            ),
            replay_gain_write=False,
            explicit=explicit,
            is_video=True,
            item_id=str(video.id),
        )

        try:
            m.save()
        except MetadataUnreadable:
            self.fn_logger.exception(
                f"Could not write metadata; file is unreadable: {log_content(name_builder_item(video))}"
            )
            return False
        except Exception:
            # Tagging is strictly best-effort for videos; the file itself is
            # complete and playable either way.
            self.fn_logger.exception(f"Could not tag video: {log_content(name_builder_item(video))}")
            return False
        return True

    def items(
        self,
        file_template: str,
        media: Album | Playlist | UserPlaylist | Mix | None = None,
        media_id: str | None = None,
        media_type: MediaType | None = None,
        video_download: bool = False,
        download_delay: bool = True,
        quality_audio: Quality | None = None,
        quality_video: QualityVideo | None = None,
        event_stop: Event | None = None,
    ) -> None:
        """Download all items in an album, playlist, or mix.

        Args:
            file_template (str): Template for file naming.
            media (Album | Playlist | UserPlaylist | Mix | None, optional): Media item. Defaults to None.
            media_id (str | None, optional): Media ID. Defaults to None.
            media_type (MediaType | None, optional): Media type. Defaults to None.
            video_download (bool, optional): Whether to allow video downloads. Defaults to False.
            download_delay (bool, optional): Whether to delay between downloads. Defaults to True.
            quality_audio (Quality | None, optional): Audio quality. Defaults to None.
            quality_video (QualityVideo | None, optional): Video quality. Defaults to None.
            event_stop (Event | None, optional): Event to stop the download. Defaults to None.
        """
        # Validate and prepare media collection
        validated_media = self._validate_and_prepare_media(media, media_id, media_type, video_download)
        if validated_media is None or not isinstance(validated_media, Album | Playlist | UserPlaylist | Mix):
            return

        media = validated_media

        # Set up download context
        download_context = self._setup_collection_download_context(media, file_template, video_download)
        file_name_relative, list_media_name, list_media_name_short, items, progress_stdout = download_context

        # Set up progress tracking
        progress: Progress = self.progress_overall if self.progress_overall else self.progress
        progress_task: TaskID = progress.add_task(
            f"[green]List '{list_media_name_short}'", total=len(items), visible=progress_stdout
        )

        # Download configuration
        is_album: bool = isinstance(media, Album)
        list_total: int = len(items)

        # Execute downloads
        result_paths: list[pathlib.Path] = self._execute_collection_downloads(
            items,
            file_name_relative,
            quality_audio,
            quality_video,
            download_delay,
            is_album,
            list_total,
            progress,
            progress_task,
            progress_stdout,
            event_stop,
        )

        # Create playlist file if requested.
        self._playlist_for_collection(media, file_name_relative, result_paths)

        self.fn_logger.info(f"Finished list '{log_content(list_media_name)}'.")

    def _playlist_for_collection(
        self,
        media: Album | Playlist | UserPlaylist | Mix,
        file_template: str,
        result_paths: list[pathlib.Path],
    ) -> None:
        """The last step of a collection download: the m3u8 the "Create .m3u8
        playlist" setting promises, written from the landed paths.

        ``items()`` ends with this, and so must any fan-out that stands in for
        ``items()`` over an explicit track list (the best-of-both merge in the
        Waves bridge): an album gets its playlist file the same way however its
        tracks were sourced. Kept as one method so the two cannot drift on what
        the file is called, whether it sorts by number, or what it lists.

        The landed paths carry the list's own track order, so the m3u plays
        back in TIDAL's order (issue #22); the directory set alone cannot say
        which track comes first.

        ``file_template`` may be the raw template or the collection-formatted
        one: the item tokens the sort decision reads ({album_track_num},
        {list_pos}) are only substituted per track, so both spellings answer
        alike.
        """
        if not self.settings.data.playlist_create:
            return

        self.playlist_populate(
            {p.parent for p in result_paths},
            name_builder_title(media),
            isinstance(media, Album),
            bool("album_track_num" in file_template or "list_pos" in file_template),
            paths_ordered=result_paths,
        )

    def _setup_collection_download_context(
        self,
        media: Album | Playlist | UserPlaylist | Mix,
        file_template: str,
        video_download: bool,
    ) -> tuple[str, str, str, list, bool]:
        """Set up download context for media collection.

        Args:
            media (Album | Playlist | UserPlaylist | Mix): Media collection.
            file_template (str): Template for file naming.
            video_download (bool): Whether to allow video downloads.

        Returns:
            tuple[str, str, str, list, bool]: (file_name_relative, list_media_name, list_media_name_short, items, progress_stdout)
        """

        # Get all items of the list. Fetched before the folder is chosen: the
        # choice is made on where a real item of this collection would land
        # (see below), and one of them has to be in hand to ask that.
        items = items_results_all(media, videos_include=video_download)

        # Create file name and path. The collection's own folder is baked into
        # the template here, before any item is queued, so the pre-0.1.17
        # spelling has to be preferred at THIS level too: by the time an item
        # is formatted the folder is literal text and the old name could no
        # longer be recovered (see _keep_existing_layout).
        def build_collection(tidy: bool, replacement: str = "", mapping: dict[str, str] | None = None) -> str:
            return format_path_media(
                file_template,
                media,
                delimiter_artist=self.settings.data.filename_delimiter_artist,
                delimiter_album_artist=self.settings.data.filename_delimiter_album_artist,
                use_primary_album_artist=self.settings.data.use_primary_album_artist,
                tidy_spacing=tidy,
                illegal_replacement=replacement,
                illegal_map=mapping,
            )

        # The same spelling, finished off by a real item of this collection.
        # A collection answers only its own tokens, so the shipped album
        # template's opening {artist_name} (a track's question) survives as
        # literal text, and a folder tested with that in it never exists. The
        # folders compared have to be the ones items actually land in.
        sample = next((item for item in items if isinstance(item, Track | Video)), None)

        def build_probe(tidy: bool, replacement: str = "", mapping: dict[str, str] | None = None) -> str:
            return format_path_media(
                build_collection(tidy, replacement, mapping),
                sample,
                self.settings.data.album_track_num_pad_min,
                1,
                len(items),
                delimiter_artist=self.settings.data.filename_delimiter_artist,
                delimiter_album_artist=self.settings.data.filename_delimiter_album_artist,
                use_primary_album_artist=self.settings.data.use_primary_album_artist,
                tidy_spacing=tidy,
                illegal_replacement=replacement,
                illegal_map=mapping,
            )

        spellings: list[tuple[bool, str, dict[str, str] | None]] = [
            (True, self._illegal_replacement(), self._illegal_map()),
            (True, self._illegal_replacement(), None),
            (True, "", None),
            (False, "", None),
        ]

        # Older spellings (most recent first) win when their folder exists,
        # exactly as in _keep_existing_layout; the stand-in settings only name
        # folders that do not exist yet.
        file_name_relative: str = self._keep_existing_collection_layout(
            *(build_collection(*spelling) for spelling in spellings),
            probes=[build_probe(*spelling) for spelling in spellings] if sample is not None else None,
        )

        # Get the name of the list and check, if videos should be included.
        list_media_name: str = name_builder_title(media)
        list_media_name_short: str = list_media_name[:30]

        # Determine where to redirect the progress information.
        if self.progress_gui is None:
            progress_stdout: bool = True
        else:
            progress_stdout: bool = False

            self.progress_gui.list_name.emit(list_media_name_short)

        return file_name_relative, list_media_name, list_media_name_short, items, progress_stdout

    def _execute_collection_downloads(
        self,
        items: list,
        file_name_relative: str,
        quality_audio: Quality | None,
        quality_video: QualityVideo | None,
        download_delay: bool,
        is_album: bool,
        list_total: int,
        progress: Progress,
        progress_task: TaskID,
        progress_stdout: bool,
        event_stop: Event | None = None,
    ) -> list[pathlib.Path]:
        """Execute downloads for all items in the collection.

        Args:
            items (list): List of media items to download.
            file_name_relative (str): Relative file name template.
            quality_audio (Quality | None): Audio quality setting.
            quality_video (QualityVideo | None): Video quality setting.
            download_delay (bool): Whether to apply download delay.
            is_album (bool): Whether this is an album.
            list_total (int): Total number of items.
            progress (Progress): Progress bar instance.
            progress_task (TaskID): Progress task ID.
            progress_stdout (bool): Whether to show progress in stdout.
            event_stop (Event | None, optional): Event to stop the download. Defaults to None.

        Returns:
            list[pathlib.Path]: The landed file paths in list order (see
            _process_download_futures).
        """
        result_paths: list[pathlib.Path] = []

        # Check if items list is empty
        if not items:
            # Mark progress as complete for empty lists
            progress.update(progress_task, completed=progress.tasks[progress_task].total)

            if not progress_stdout and self.progress_gui:
                self.progress_gui.list_item.emit(100.0)

            return result_paths

        # Iterate through list items. Gate on THIS collection's own task, never on
        # `progress.finished`: that is `all(task.finished)` over every task on the
        # bar, including the per-track ones, and a track task only completes on
        # success. A single failed track therefore left the whole item list being
        # re-submitted forever, re-downloading every sibling on each pass.
        while not progress.tasks[progress_task].finished:
            with futures.ThreadPoolExecutor(max_workers=self.settings.data.downloads_concurrent_max) as executor:
                # Dispatch all download tasks to worker threads
                download_futures: list[futures.Future] = [
                    executor.submit(
                        self.item,
                        media=item_media,
                        file_template=file_name_relative,
                        quality_audio=quality_audio,
                        quality_video=quality_video,
                        download_delay=download_delay,
                        is_parent_album=is_album,
                        list_position=count + 1,
                        list_total=list_total,
                        event_stop=event_stop,
                    )
                    for count, item_media in enumerate(items)
                ]

                # Process download results
                result_paths = self._process_download_futures(
                    download_futures, progress, progress_task, progress_stdout
                )

                # Check for abort signal
                if self.event_abort.is_set() or (event_stop and event_stop.is_set()):
                    return result_paths

        return result_paths

    def _process_download_futures(
        self,
        futures_list: list[futures.Future],
        progress: Progress,
        progress_task: TaskID,
        progress_stdout: bool,
    ) -> list[pathlib.Path]:
        """Process download futures and collect results.

        Args:
            futures_list (list[futures.Future]): List of download futures.
            progress (Progress): Progress bar instance.
            progress_task (TaskID): Progress task ID.
            progress_stdout (bool): Whether to show progress in stdout.

        Returns:
            list[pathlib.Path]: The items' landed file paths, in LIST ORDER
            (the submission order of the futures), one per item that produced a
            file. This order is the collection's own track order, which the m3u
            writer reproduces; a set of directories would lose it (issue #22).
        """
        # Report results as they become available
        for future in futures.as_completed(futures_list):
            # If app is terminated (CTRL+C). Checked BEFORE the result below,
            # so a stop is a stop: an item that crashed on its way out reached
            # this loop in whatever order it finished, and surfacing it turned
            # the user's Cancel into a job failure or not depending on which
            # future as_completed happened to yield first.
            if self.event_abort.is_set():
                # Cancel all not yet started tasks
                for f in futures_list:
                    f.cancel()

                break

            # Surface a crashed item's exception now, as this always did.
            future.result()

            # Advance progress bar.
            progress.advance(progress_task)

            if not progress_stdout:
                self.progress_gui.list_item.emit(progress.tasks[progress_task].percentage)

        return self._landed_paths(futures_list)

    @staticmethod
    def _landed_paths(futures_list: list[futures.Future]) -> list[pathlib.Path]:
        """The files a fan-out of ``item()`` futures produced, in SUBMISSION
        order (the collection's own track order), one per item that produced a
        file. This is what ``playlist_populate`` is handed as ``paths_ordered``;
        the same rule serves any fan-out that stands in for ``items()``.

        Submission order, not completion order. On abort the cancelled and
        still-running tail is skipped, exactly the items a reporting loop over
        ``as_completed`` never got to either. An item that crashed produced no
        file, and its exception was either already raised by that loop or never
        reported at all (an abort broke out of it first): re-raising here would
        turn the user's Cancel into a job failure, which a Cancel must never
        be. Only an item that reports success AND a path landed one: an
        exclusion or a copy owned elsewhere answers ``(True, "")``, and a
        refusal, a failed fetch, a stopped download or a name with no free
        variant answers ``(False, <the path it was aiming for>)`` from before
        anything was written there. Neither is a landed file, so neither hands
        the playlist writer a folder this run did not fill; a first-time album
        that lost every track used to fail on the playlist's own temp file in a
        folder that was never created, and that error hid the real reason.
        """
        result_paths: list[pathlib.Path] = []

        for future in futures_list:
            if future.cancelled() or not future.done():
                continue
            if future.exception() is not None:
                continue

            status, result_path_file = future.result()

            if status and result_path_file:
                result_paths.append(pathlib.Path(result_path_file))

        return result_paths

    def playlist_populate(
        self,
        dirs_scoped: set[pathlib.Path],
        name_list: str,
        is_album: bool,
        sort_alphabetically: bool,
        paths_ordered: list[pathlib.Path] | None = None,
    ) -> list[pathlib.Path]:
        """Create playlist files (m3u8) for downloaded tracks in each directory.

        Args:
            dirs_scoped (set[pathlib.Path]): Set of directories containing tracks.
            name_list (str): Name of the playlist.
            is_album (bool): Whether this is an album.
            sort_alphabetically (bool): Whether to sort tracks alphabetically.
            paths_ordered (list[pathlib.Path] | None, optional): The collection's
                landed file paths in ITS OWN track order. When given, each
                directory's playlist lists exactly these files in this order,
                which is what makes a downloaded TIDAL playlist play back in
                TIDAL's order (issue #22). Without it the directory is globbed
                and sorted by name or file age, which reconstructs an album's
                order from numbered filenames but knows nothing of a playlist's.

        Returns:
            list[pathlib.Path]: List of created playlist file paths.
        """
        result: list[pathlib.Path] = []

        # For each dir, which contains tracks
        for dir_scoped in dirs_scoped:
            # Spelled like every other name in the library: the per-character
            # stand-ins first, then the general one, then the spacing tidy. The
            # old call handed the whole "_<name>.m3u" string to pathvalidate
            # with no stand-ins at all, so a playlist called "?" came out as the
            # bare prefix while an album called "?" kept its name (issue #16).
            name_sanitized: str = sanitize_name_component(name_list, self._illegal_replacement(), self._illegal_map())
            path_playlist = dir_scoped / sanitize_filename(PLAYLIST_PREFIX + name_sanitized + PLAYLIST_EXTENSION)
            path_playlist = pathlib.Path(path_file_sanitize(path_playlist, adapt=True))

            # A playlist file the library already holds keeps its name: older
            # versions wrote .m3u (and, before 0.1.18, a spelling with no
            # stand-ins that left the doubled space a removed character
            # created). Writing the new name beside an old one left two files
            # for one playlist, both ingested by a library scanner, and the
            # stale one can never be removed (prevention-only cleanup). Same
            # answer _keep_existing_layout gives every other name; most recent
            # spelling first.
            for stem, extension in (
                (name_sanitized, PLAYLIST_EXTENSION_LEGACY),
                (name_list, PLAYLIST_EXTENSION_LEGACY),
            ):
                if path_playlist.is_file():
                    break

                path_playlist_legacy = dir_scoped / sanitize_filename(PLAYLIST_PREFIX + stem + extension)
                path_playlist_legacy = pathlib.Path(path_file_sanitize(path_playlist_legacy, adapt=True))

                if path_playlist_legacy.name != path_playlist.name and path_playlist_legacy.is_file():
                    path_playlist = path_playlist_legacy
                    break

            # The NAME only, and as content: the full path spells out the
            # download root, which normally sits under the user's home.
            self.fn_logger.debug(f"Playlist: Creating {log_content(path_playlist.name)}")

            path_tracks: list[pathlib.Path] = self._playlist_entries(
                dir_scoped, is_album, sort_alphabetically, paths_ordered
            )

            # Write the m3u the way every other file reaches the library: into a
            # hidden temp sibling, flushed to stable storage, then swapped into
            # the real name. Opening the real name in truncating mode meant a
            # crash, a full disk or a share going away mid-write left the user
            # with an emptied or half-written playlist where a complete one had
            # been. This is the only file the engine writes at its destination
            # rather than moving into place, so it needs the swap spelled out
            # here (the pattern mirrors BaseConfig.save and _stage_and_swap).
            # Through _staging_path, never hand-decorated: the sanitizer fits
            # the FINAL name to the caps, and 42 unbudgeted characters on top
            # of a name at the cap made the temp unopenable, failing the whole
            # job at its very last step with every track already landed.
            path_playlist_tmp: pathlib.Path = _staging_path(path_playlist)

            try:
                with path_playlist_tmp.open(mode="w", encoding="utf-8") as f:
                    for path_track in path_tracks:
                        # If it's a symlink write the relative file path to the actual track into the playlist file
                        if path_track.is_symlink():
                            media_file_target = path_track.resolve().relative_to(path_track.parent, walk_up=True)
                        else:
                            media_file_target = path_track.name

                        # Write a plain '\n'; text mode ('w') translates it to the platform
                        # line ending. Using os.linesep here would double-translate on Windows
                        # ('\r\n' -> '\r\r\n') and corrupt the entries.
                        f.write(str(media_file_target) + "\n")

                    f.flush()
                    os.fsync(f.fileno())

                path_playlist_tmp.replace(path_playlist)
            except OSError:
                # Never leave the throwaway behind, and never let a failing
                # cleanup mask what actually went wrong.
                with contextlib.suppress(OSError):
                    path_playlist_tmp.unlink(missing_ok=True)

                raise

            # Written directly at the destination, so it needs the same
            # AppleDouble cleanup the moved files get.
            strip_apple_double(path_playlist)

            result.append(path_playlist)

        return result

    @staticmethod
    def _playlist_entries(
        dir_scoped: pathlib.Path,
        is_album: bool,
        sort_alphabetically: bool,
        paths_ordered: list[pathlib.Path] | None,
    ) -> list[pathlib.Path]:
        """One directory's playlist lines, in playback order (see
        playlist_populate for the two modes)."""
        # Every audio file the directory holds, which is what an m3u describes.
        # The ordered list below is only allowed to REORDER this, never to
        # shorten it.
        path_tracks: list[pathlib.Path] = []

        for extension_audio in AudioExtensionsValid:
            # pathlib's glob matches hidden files, so filter dotfiles: macOS
            # AppleDouble ghosts (._Track.flac) must never become playlist entries.
            path_tracks = path_tracks + [
                p for p in dir_scoped.glob(f"*{extension_audio!s}") if not p.name.startswith(".")
            ]

        # Sort alphabetically, e.g. if items are prefixed with numbers
        if sort_alphabetically:
            path_tracks.sort()
        elif not is_album:
            # If it is not an album sort by creation time
            def _born(p: pathlib.Path) -> float:
                try:
                    st = p.stat()
                except OSError:
                    # A playlist-folder entry is a symlink into the track tree,
                    # and a target on a share that is away cannot be stat'd. It
                    # still belongs in the list, so it sorts oldest-first rather
                    # than taking the whole write down with it.
                    return 0.0
                return float(getattr(st, "st_birthtime", st.st_ctime))

            path_tracks.sort(key=_born)

        if paths_ordered is None:
            return path_tracks

        # The collection's own order (issue #22), but only when this run can
        # account for every file in the folder. A run reports back only what it
        # actually fetched: a re-download skips the tracks you already have, a
        # cancelled run stops partway, an item can fail. Writing just those, on
        # a folder that already held a full playlist, REPLACED a complete m3u
        # with a one-line one, which is the file equivalent of losing the
        # playlist. So the folder is the truth about what belongs in the list,
        # and this run's order is applied to it only when the two agree on the
        # contents; otherwise the folder listing stands, exactly as it did
        # before order was known at all.
        here = set(path_tracks)
        # First occurrences only: a playlist can carry the same track twice,
        # and both occurrences land on ONE file (the name ledger lets an item
        # retake its own name), so the raw ordered list held that path twice,
        # could never match the folder's count, and the order fix silently
        # stood down for exactly those playlists. One entry per file keeps the
        # order; the duplicate spin is the playlist's business, not the m3u's.
        seen: set[pathlib.Path] = set()
        ordered = [p for p in paths_ordered if p in here and not (p in seen or seen.add(p))]
        return ordered if len(ordered) == len(path_tracks) else path_tracks

    def _video_convert(self, path_file: pathlib.Path) -> pathlib.Path:
        """Convert a TS video file to MP4 using ffmpeg.

        Args:
            path_file (pathlib.Path): Path to the TS file.

        Returns:
            pathlib.Path: Path to the converted MP4 file.
        """
        path_file_out: pathlib.Path = path_file.with_suffix(AudioExtensions.MP4)

        self.fn_logger.debug(f"Converting video: {path_file.name} -> {path_file_out.name}")

        ffmpeg = (
            FFmpeg(executable=self.settings.data.path_binary_ffmpeg)
            .option("y")
            .option("hide_banner")
            .option("nostdin")
            .input(url=path_file)
            .output(url=path_file_out, codec="copy", map=0, loglevel="quiet")
        )

        ffmpeg.execute()

        self.fn_logger.debug(f"Video conversion complete: {path_file_out.name}")

        return path_file_out

    def _extract_flac(self, path_media_src: pathlib.Path) -> pathlib.Path:
        """Extract FLAC audio from a media file using ffmpeg.

        Args:
            path_media_src (pathlib.Path): Path to the source media file.

        Returns:
            pathlib.Path: Path to the extracted FLAC file.
        """
        path_media_out = path_media_src.with_suffix(AudioExtensions.FLAC)

        ffmpeg = (
            FFmpeg(executable=self.settings.data.path_binary_ffmpeg)
            .option("hide_banner")
            .option("nostdin")
            .input(url=path_media_src)
            .output(
                url=path_media_out,
                map=0,
                movflags="use_metadata_tags",
                acodec="copy",
                map_metadata="0:g",
                loglevel="quiet",
            )
        )

        ffmpeg.execute()

        return path_media_out

    def _faststart_remux(self, path_file: pathlib.Path, container_ext: str) -> pathlib.Path:
        """Rebuild an MP4/M4A container so its moov/mvhd carries the real duration.

        A DASH download is a raw concatenation of fragmented-MP4 segments, whose
        top-level moov duration is 0 (timing lives in the per-fragment moof boxes).
        A ``-c copy`` remux with ``+faststart`` writes a fresh non-fragmented moov
        with the correct mvhd/mdhd/tkhd duration, keeping the audio bitstream bit
        for bit identical (no re-encode). The remux writes to a sibling temp and
        only replaces the original on success, so a failed remux never loses the
        file (it stays playable in lenient players, just 0:00 in strict ones).

        ``container_ext`` (``.m4a``/``.mp4``) is given explicitly because the input
        is the bare merge temp with no extension, so ffmpeg needs it to pick the
        MP4 muxer for the output.

        Args:
            path_file (pathlib.Path): The merged (extensionless) temp file.
            container_ext (str): The destination container extension.

        Returns:
            pathlib.Path: The finalized file (same path as the input).
        """
        path_out = path_file.with_name(f"{path_file.stem}.faststart{container_ext}")

        try:
            (
                FFmpeg(executable=self.settings.data.path_binary_ffmpeg)
                .option("y")
                .option("hide_banner")
                .option("nostdin")
                .input(url=path_file)
                .output(
                    url=path_out,
                    map=0,
                    codec="copy",
                    movflags="+faststart",
                    loglevel="quiet",
                )
            ).execute()
        except Exception:
            # Never lose the download: the raw file still plays in lenient players,
            # it just reports 0:00 in strict ones. Drop the partial and keep it.
            self.fn_logger.exception(
                "Container remux failed; keeping the raw file (may report 0:00 in strict players)."
            )
            with contextlib.suppress(OSError):
                path_out.unlink()

            return path_file

        os.replace(path_out, path_file)

        return path_file

    def _downsample_audio(self, path_file: pathlib.Path) -> pathlib.Path:
        """Downsample a FLAC file toward the configured target rate/depth using ffmpeg.

        Each dimension (sample rate, bit depth) is reduced independently and
        never upsampled, a 24/44.1 source with a 16/48 target becomes 16/44.1.
        Returns the original path if neither dimension needs to change.
        """
        target = DownsampleTarget(self.settings.data.downsample_target)
        target_rate = target.sample_rate
        target_depth = target.bit_depth

        info = FLAC(path_file).info
        src_rate = info.sample_rate
        src_depth = info.bits_per_sample

        out_rate = min(src_rate, target_rate)
        out_depth = min(src_depth, target_depth)

        if out_rate == src_rate and out_depth == src_depth:
            self.fn_logger.info(
                f"Downsample skipped: source {src_depth}-bit/{src_rate} Hz "
                f"already at-or-below target {target_depth}-bit/{target_rate} Hz."
            )
            return path_file

        self.fn_logger.info(
            f"Downsampling {src_depth}-bit/{src_rate} Hz -> {out_depth}-bit/{out_rate} Hz "
            f"(target {target_depth}-bit/{target_rate} Hz)."
        )

        # only run the format-conversion pieces of the filter when the
        # corresponding dimension is actually changing. If only the rate is
        # dropping, leave bit depth untouched (no osf, no dither). If only the
        # depth is dropping, skip the resampler entirely.
        sample_fmt = "s16" if out_depth == 16 else "s32"
        rate_changing = out_rate != src_rate
        depth_changing = out_depth != src_depth

        # mostly equivalent to the redacted sox command but no clipping protection
        # `sox -S input.flac -R -G -b 16 output.flac rate -v -L 48000 dither`
        # i dont really know much about dithering methods but i trust those guys
        filter_parts = ["resampler=soxr", "precision=33"]
        if rate_changing:
            filter_parts.append(f"osr={out_rate}")
        if depth_changing:
            filter_parts.append(f"osf={sample_fmt}")
            filter_parts.append("dither_method=triangular")
        af = "aresample=" + ":".join(filter_parts)

        output_kwargs: dict = {
            "af": af,
            "acodec": "flac",
            "map_metadata": "0",
            "loglevel": "error",
        }
        if out_depth == 24:
            # ffmpeg's FLAC encoder needs s32 + an explicit bits_per_raw_sample
            # to mark the stream as 24-bit; otherwise it'll be tagged as 32-bit.
            output_kwargs["bits_per_raw_sample"] = 24

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_path_dir:
            path_out = pathlib.Path(tmp_path_dir) / path_file.name

            ffmpeg = (
                FFmpeg(executable=self.settings.data.path_binary_ffmpeg)
                .option("y")
                .option("hide_banner")
                .option("nostdin")
                .input(url=path_file)
                .output(url=path_out, **output_kwargs)
            )
            ffmpeg.execute()

            if not self._move_file(path_out, path_file, overwrite=True):
                self.fn_logger.error(f"Unable to replace downsampled file: {path_file}")
                raise OSError(f"Unable to replace downsampled file: {path_file}")

        return path_file

    def _extract_video_stream(self, m3u8_variant: m3u8.M3U8, quality: int) -> tuple[m3u8.M3U8 | bool, str]:
        """Extract the best matching video stream from an m3u8 variant playlist.

        Args:
            m3u8_variant (m3u8.M3U8): The m3u8 variant playlist.
            quality (int): Desired video quality (vertical resolution).

        Returns:
            tuple[m3u8.M3U8 | bool, str]: (Selected m3u8 playlist or False, codecs string)
        """
        m3u8_playlist: m3u8.M3U8 | bool = False
        resolution_best: int = 0
        mime_type: str = ""

        if m3u8_variant.is_variant:
            for playlist in m3u8_variant.playlists:
                if resolution_best < playlist.stream_info.resolution[1]:
                    resolution_best = playlist.stream_info.resolution[1]
                    m3u8_playlist = m3u8.load(playlist.uri, http_client=RequestsClient())
                    mime_type = playlist.stream_info.codecs

                    if quality == playlist.stream_info.resolution[1]:
                        break

        return m3u8_playlist, mime_type
