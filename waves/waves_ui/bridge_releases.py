"""The Releases pane: the artists you follow and what they have released.

The store underneath (``waves.follows``) is pure sqlite; this mixin is the Qt
layer over it: a paced, rolling sweep of the followed artists' catalogs on
a worker thread, the feed the pane renders, the follow and unfollow actions,
and the unseen count behind the nav badge.

The sweep, one artist per step. A single-shot timer re-armed after every
step picks the followed artist whose last check is the oldest and fetches
that artist's newest albums and EPs (and, about once a day, its music
videos: see ``FollowStore.video_due``) on the family's own one-thread pool,
records the result (``FollowStore.record_check``: baseline, feed window,
pruning) and posts the outcome back to the GUI thread through
``_relStepDone``. The step pace adapts: a burst while artists have never
been checked (a fresh import), slower while the download queue is busy
(they share the HTTP pool), and otherwise spread so every follow is seen
again inside ``_REL_ROUND_S``. A rate limit answers with a pause of minutes,
not a retry. The sweep runs only once the launch overlay has revealed the
interface AND the user is signed in; it stops on sign-out, factory reset
and quit, and keeps going while the window is hidden (its whole point is
staying current over days).

Each step carries the generation it was dispatched under; a step from a
previous account or a reset is dropped on arrival.

TIDAL favorites. They are always READ into the follow list (a paced walk
at the start of each round, never more than every half hour, plus whatever
the My Tidal artists tab happens to load). They are WRITTEN only when the
user asks: the opt-in ``follow_mirror_tidal`` pref, or the artist page's
ALSO FOLLOW ON TIDAL. Writes are rationed so no setting and no list size
can turn into a burst against the account: only a single hand follow or
unfollow is ever sent (turning the pref on never copies the existing list,
and a bulk follow such as the library import never mirrors), changes are
coalesced per artist and follows batched into one request, one request
goes out at most every few seconds, an hourly ceiling backs that up, and a
rate limit pauses the writes for minutes.

The library import. Every artist the scanned library holds is offered in
one review: a folder whose files carry the TIDAL artist id a Waves download
writes is matched from the tag with no request at all, and the rest are
searched by name one a second, the sweep pausing meanwhile so the two never
add up. Confirming follows the picks in one transaction and never touches
TIDAL favorites, whatever the mirror pref says; names it could not settle
wait in a tray for a later look. The import runs mid-scan on whatever the
scan has saved (it reads one album per artist folder first), and remembers
the names it looked at: a finished scan then offers only the artists found
since. With ``releases_auto_import`` (on by default) the scan's progress drives
the same run by itself, following only confident matches (a tag id, or one
TIDAL artist of exactly the same name).

New releases. Whatever the steps find over a minute is announced in one OS
banner (``releases_notify``, armed only while the ``notify_enabled`` master
switch is on; both ride the shared ``_notify_send`` gate), sent through the
bridge's shared notify pool because a send can block (see ``notify``). With the opt-in
``releases_auto_download`` each new release is queued through the normal
album path the day it is out; one dated ahead waits for its day. Only an
artist whose catalog is already known can produce a new release, so a
fresh follow or a whole library import never floods the queue.

The MusicBrainz check (``releases_musicbrainz``, off by default: it sends
the names of followed artists to musicbrainz.org). On the same pacer,
after the deep read's turn, every other remaining beat takes the follow
whose check is the oldest (none within a month), resolves it to a
MusicBrainz artist and pages its release groups at MusicBrainz's own one
request a second (``waves.musicbrainz``), and records which groups TIDAL
has no row for: the red marks on the Artists timeline. Switching the
setting off stops the checks and hides the marks; the rows stay.

The fresh-releases hint (``releases_fresh_hints``, on by default: the
ListenBrainz request carries nothing about the user). Every few hours, and
every half hour on release night (Thursday 22:00 to Friday 06:00 local),
one read of ListenBrainz's list of releases out in the last week and one
of TIDAL's own feed (the favorite artists' new releases, read only, never
marked seen) name the follows they credit (``waves.fresh_releases``);
those are checked on TIDAL next, ahead of the round, at the burst pace. A light timer keeps the schedule and
notices the machine waking from sleep (its wall clock jumped past the
tick), which reads the list at once. Plain Qt timers and the wall clock,
so it behaves the same on every platform.

A mixin over WavesBridge like ``bridge_library``; this module must never
import backend.
"""

from __future__ import annotations

import collections
import contextlib
import datetime as _dt
import hashlib
import json
import logging
import os
import pathlib
import threading
import time

from PySide6 import QtCore
from PySide6.QtCore import Signal, Slot
from tidalapi.artist import Artist

from waves import artist_import, fresh_releases, musicbrainz
from waves.follows import DL_QUEUED, DL_WAITING, ORIGIN_LIBRARY, ORIGIN_MANUAL, ORIGIN_TIDAL
from waves.mb_service import default_user_agent
from waves.metadata import ALBUM_ARTIST_ID_TAG, read_custom_ids
from waves.release_delta import collapse_atmos_twins
from waves.waves_ui import diagnostics
from waves.worker import Worker

logger = logging.getLogger("waves.releases")

# Every followed artist is checked again at least this often. New releases
# mostly go live at midnight, so two checks a day still catch them that day.
_REL_ROUND_S = 12 * 3600
# The step while never-checked artists exist (a fresh follow or import), or
# while a Check now goes through every follow. A step is two requests
# (albums, then EPs and singles; a third for the music videos when they are
# due), so about one request a second: 681 artists in about 34 minutes.
_REL_BURST_MS = 3000
# The burst step while the download queue is working (same HTTP pool).
_REL_BURST_BUSY_MS = 5000
_REL_MIN_STEP_MS = 1500
_REL_MAX_STEP_MS = 60_000
# Newest releases per section (albums, EPs and singles) a step fetches.
_REL_FETCH_LIMIT = 30
# The deep read: a section that came back full is read on past the head,
# a page at a time with a pause between pages, one artist in flight, on
# every other pacer beat once the first round has seeded every follow. A
# catalog read to its end is not read again for a season.
_REL_DEEP_PAGE = 50
_REL_DEEP_MAX_PAGES = 40
_REL_DEEP_PAGE_GAP_S = 1.5
_REL_DEEP_AGAIN_S = 90 * 86400
# Check now runs at most this often.
_REL_CHECK_NOW_GAP_S = 3600
# A deep read a failed page left unfinished goes again after this long.
_REL_DEEP_RETRY_S = 86400
# The MusicBrainz check: a follow is checked again after a month; one that
# could not be checked (the service down, a pause asked for) goes again
# after an hour rather than on the next beat.
_REL_MB_AGAIN_S = 30 * 86400
_REL_MB_RETRY_S = 3600
# The widest feed window the setting offers. The store keeps every row it
# was given (the fetch caps them per artist), so widening shows them at once.
_REL_WINDOW_MAX_MONTHS = 120
# A rate limit pauses the whole sweep: 15 minutes, then 30, then 60.
_REL_BACKOFF_S = (900, 1800, 3600)

# The fresh-releases hint (see the module docstring): its timer's beat (no
# request, only a look at the clock), how often the list is read by day and
# on release night, the window it reads, how many follows one read may put
# ahead of the round, and the wall-clock jump past a beat that reads as the
# machine waking from sleep. The service caches the list five minutes.
_REL_FRESH_TICK_MS = 5 * 60_000
_REL_FRESH_EVERY_S = 3 * 3600
_REL_FRESH_NIGHT_S = 30 * 60
_REL_FRESH_DAYS = 7
_REL_FRESH_HINT_CAP = 60
_REL_FRESH_WAKE_S = 15 * 60
# The release groups a read saw are remembered this many days after a list
# last showed them, so the next read can tell which are new to the list.
_REL_FRESH_KEEP_DAYS = 10
# New releases found over a minute are announced in one notification,
# which names at most two artists.
_REL_NOTIFY_COALESCE_MS = 60_000
_REL_NOTIFY_KEEP = 500
# An auto-download whose album could not even be fetched (a dropped
# connection, a rate limit) goes back in line this many times per launch.
_REL_AUTO_DL_TRIES = 3
# A followed row's stamps of when it was last checked: every sweep step
# writes them and the page never shows them (see _releases_followed_fresh).
_FOLLOW_STAMPS = frozenset({"last_checked", "mb_checked"})
# What the auto-download takes, in the order the setting lists them: the
# artist's own albums, EPs and singles as TIDAL files them, and "featured",
# another artist's release a follow is a guest on (only the follow's tracks
# download, as a discography's Featured on source does). Featured costs one
# more request per follow a day, so it starts off.
REL_AUTO_KINDS = ("album", "ep", "single", "featured")
REL_AUTO_KINDS_DEFAULT = "album,ep,single"


def releases_auto_kinds_text(value) -> str:
    """The auto-download kinds as the pref stores them: the known ones,
    comma-joined in REL_AUTO_KINDS order."""
    if isinstance(value, list | tuple | set | frozenset):
        picked = {str(v).strip().lower() for v in value}
    else:
        picked = {v.strip().lower() for v in str(value or "").split(",")}
    return ",".join(k for k in REL_AUTO_KINDS if k in picked)


_REL_KIND_WORD = {"album": "album", "ep": "EP", "single": "single", "video": "video"}
# The favorites walk: pages of 100, one a second, at most 50 of them
# (5,000 artists; a longer list is read as a partial walk that only adds),
# and never again within half an hour (a relaunch does not re-walk).
_REL_WALK_PAGE = 100
_REL_WALK_MAX_PAGES = 50
_REL_WALK_PAGE_GAP_S = 1.0
_REL_WALK_MIN_GAP_S = 1800
# A walk that read nothing (TIDAL or the network failed on its first page, or
# a rate limit) is not one: the idle sweep tries it again after 5, 15, 30,
# then every 60 minutes, so an account whose follows all come from its
# favorites is not left empty until a relaunch.
_REL_WALK_RETRY_S = (300, 900, 1800, 3600)
# Favorites writes. Changes settle for a moment so a run of clicks goes
# out together, one request carries up to 50 follows (TIDAL takes a list),
# requests are spaced at least 3 seconds apart, never more than 60 in an
# hour, and a rate limit pauses them for 15 minutes.
_REL_TIDAL_SETTLE_MS = 1500
_REL_TIDAL_GAP_MS = 3000
_REL_TIDAL_ADD_BATCH = 50
_REL_TIDAL_PER_HOUR = 60
_REL_TIDAL_BACKOFF_S = 900
# Artists the follow dialog lists for one search. Wider than a dozen: a
# small artist sharing a famous name ranks well below the first few.
_REL_SEARCH_LIMIT = 25
# Releases the feed search sends at most, newest first: a name shared by
# thousands of stored releases still lays out quickly.
_REL_FIND_LIMIT = 400
# An excluded artist's albums shown on the Excluded page, and the cover
# files looked for in each album folder, in order (lower case).
_REL_HELD_CAP = 8
_REL_COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png")


_LOSSLESS_CODECS = frozenset({"flac", "alac", "wav", "aiff", "ape", "wavpack", "wv", "dsd"})


def _quality_label(codec, bits, rate, bitrate) -> str:
    """A library album's copy in a few characters: "24/96 FLAC" for
    lossless with a known depth and rate, "320 MP3" for lossy, else the
    codec alone ("" when unknown)."""
    name = str(codec or "").upper()
    if not name:
        return ""
    bits, rate, bitrate = int(bits or 0), int(rate or 0), int(bitrate or 0)
    if name.lower() in _LOSSLESS_CODECS:
        return f"{bits}/{rate / 1000:g} {name}" if bits and rate else name
    # The index keeps a lossy file's bitrate in kbps.
    return f"{bitrate} {name}" if bitrate else name


# The library import searches TIDAL by name one artist a second (the sweep
# waits meanwhile, so the account never sees more than that), asking for
# ten candidates each. The tag pass reports progress every few folders.
_REL_IMPORT_SEARCH_GAP_S = 1.0
_REL_IMPORT_SEARCH_LIMIT = 10
# Release titles read per candidate when namesakes are weighed against the
# library (albums, then EPs and singles), and the import's track floor: an
# artist with fewer tracks in the library is not followed (the pref
# ``releases_import_min_tracks``, 5 by default, at most this maximum).
_REL_IMPORT_CATALOG_LIMIT = 50
_REL_IMPORT_MIN_TRACKS = 5
_REL_IMPORT_MIN_TRACKS_MAX = 999
_REL_IMPORT_TAG_PROGRESS_EVERY = 10
# The library norms some import has already looked at (store meta, a JSON
# list): what makes a later run, or the "more artists" offer, cover only
# the artists the library gained since.
_REL_IMPORT_SEEN_KEY = "import_seen"
# While a library scan runs, the automatic import starts at most this often
# (each run takes only the artists found since the one before); the scan's
# end always gets a run of its own.
_REL_AUTO_IMPORT_GAP_S = 120.0


def _is_rate_limit(exc: BaseException) -> bool:
    """A 429 from the API, whichever layer raised it (tidalapi's
    TooManyRequests, or a requests error carrying the response)."""
    if type(exc).__name__ == "TooManyRequests":
        return True
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None) == 429


def _releases_banner(count: int, items: list) -> tuple[str, str]:
    """The banner for ``count`` new releases; ``items`` are {artist, title,
    rtype} for (the first few hundred of) them. One release names itself,
    one artist names the artist, several name at most two."""
    artists = list(dict.fromkeys(a for a in (str(i.get("artist") or "").strip() for i in items) if a))
    if count == 1 and items:
        kind = _REL_KIND_WORD.get(str(items[0].get("rtype") or "").lower(), "release")
        title = f"New {kind} from {artists[0]}" if artists else f"New {kind}"
        return title, str(items[0].get("title") or "").strip() or "From an artist you follow"
    if len(artists) == 1:
        return f"New from {artists[0]}", f"{count} new releases"
    title = f"{count} new release" + ("" if count == 1 else "s")
    if not artists:
        return title, "From artists you follow"
    if len(artists) == 2:
        return title, f"From {artists[0]} and {artists[1]}"
    return title, f"From {artists[0]}, {artists[1]} and {len(artists) - 2} more"


def _releases_fresh_known(seen: set, kept, today: str) -> dict:
    """The release groups a fresh-releases read remembers, keyed to the day
    a list last showed them: what this read saw, and what earlier ones saw
    until no list has shown it for a while. Keyed to the listing day, not
    the release's own: TIDAL's feed shows a month, and a release its date
    had aged out of would read as new on every read."""
    floor = (_dt.date.fromisoformat(today) - _dt.timedelta(days=_REL_FRESH_KEEP_DAYS)).isoformat()
    days = dict.fromkeys(seen, today)
    if isinstance(kept, dict):
        for rgid, day in kept.items():
            days.setdefault(str(rgid), str(day))
    return {k: v for k, v in days.items() if v >= floor}


def _tagged_artist_id(cache, folders: list) -> str:
    """The one TIDAL album-artist id an artist's album files carry (the tag a
    Waves download writes), trying the first file of up to three of its
    folders. A file naming two artists (a collaboration) proves neither."""
    for folder in folders:
        path = cache.first_audio_file(str(folder))
        if not path:
            continue
        ids = {i for i in read_custom_ids(path, ALBUM_ARTIST_ID_TAG) if str(i).isdigit()}
        if len(ids) == 1:
            return next(iter(ids))
    return ""


class ReleasesMixin:
    # The feed or the follow list changed (a step landed, a follow flipped):
    # the badge re-reads, an open pane refetches.
    releasesChanged = Signal()
    # The dressed feed rows for the pane, whether older ones wait past them,
    # and the months asked for (a load that landed late for an older ask is
    # told apart).
    releasesLoaded = Signal("QVariant", bool, int)
    # The feed search's answer: (the needle it answers, dressed rows, more
    # matched than were sent).
    releasesSearched = Signal(str, "QVariant", bool)
    releasesProgress = Signal(int, int)  # (checked this round, followed)
    followStateChanged = Signal(str, bool)  # (artist id, followed)
    followedLoaded = Signal("QVariant")  # the followed artists for the manage grid
    # The follow dialog's answer: (the needle it answers, artist rows, ok).
    artistSearchResults = Signal(str, "QVariant", bool)
    _relStepDone = Signal("QVariant")  # internal: one sweep step's outcome
    _relMirrorDone = Signal(int, int, bool)  # internal: the walk's (added, removed, rate limited)
    _relFreshDone = Signal("QVariant")  # internal: one fresh-releases read's outcome
    # (artist id, a TIDAL favorite now): after every favorites write, done
    # or not, so the artist page's ALSO FOLLOW ON TIDAL settles.
    followTidalChanged = Signal(str, bool)
    # The track floor moved, or the library follows under it were dropped:
    # the Library card re-reads how many it would leave out today.
    followFloorChanged = Signal()
    # The library artists the track floor leaves out, for the Excluded page.
    excludedLoaded = Signal("QVariant")
    _relTidalDone = Signal("QVariant")  # internal: one favorites write's outcome
    # The library import: (done, total, stage) while it runs, then the
    # review payload or an error word; (followed) once confirmed.
    artistImportProgress = Signal(int, int, str)
    artistImportReady = Signal("QVariant")
    artistImportError = Signal(str)
    artistImportDone = Signal(int)
    _relImportDone = Signal("QVariant")  # internal: the import worker's outcome
    # The Releases tab's library line: {kind: "" | "more" | "look", n}. "more"
    # offers the artists the library gained since the last import, "look"
    # the names an automatic import could not settle.
    artistImportNotice = Signal("QVariant")
    _relMoreCounted = Signal("QVariant")  # internal: the "more" count's outcome
    _relStartAsked = Signal()  # internal: a start asked off the GUI thread (a sign-in worker)

    def _releases_init(self) -> None:
        """Create the family's state (called from WavesBridge.__init__, on
        the GUI thread: the timers must live there to ever fire)."""
        self._rel_pool = QtCore.QThreadPool()
        self._rel_pool.setMaxThreadCount(1)
        diagnostics.register_pool("releases", self._rel_pool)
        self._rel_gen = 0
        self._rel_step_running = False
        self._rel_import_running = False
        self._rel_backoff_until = 0.0
        self._rel_backoff_level = 0
        self._rel_revealed = False
        self._rel_mirror_due = False
        self._rel_notify_pending = 0
        self._rel_notify_items: list = []
        # TIDAL favorites writes: never behind a long library import or a
        # favorites walk on the sweep's pool (the rationing serializes them).
        self._rel_tidal_pool = QtCore.QThreadPool()
        self._rel_tidal_pool.setMaxThreadCount(1)
        diagnostics.register_pool("releases-tidal", self._rel_tidal_pool)
        self._rel_round_done = 0
        self._rel_search_gen = 0
        # Follows whose head fetch came back full, waiting for a deep read:
        # (artist_id, name, sections). A deep read takes the extra beat
        # between two steps, so the sweep keeps its pace meanwhile.
        self._rel_deep_queue: collections.deque = collections.deque()
        self._rel_deep_turn = False
        # The MusicBrainz check shares the extra beat with the deep read (one
        # per step slot, see _releases_tick); its client is made on first
        # use, only once the setting is on.
        self._rel_extra_spent = False
        self._rel_step_at = 0.0
        self._rel_mb_client = None
        self._rel_timer = QtCore.QTimer(self)
        self._rel_timer.setSingleShot(True)
        self._rel_timer.timeout.connect(self._releases_tick)
        self._rel_notify_timer = QtCore.QTimer(self)
        self._rel_notify_timer.setSingleShot(True)
        self._rel_notify_timer.setInterval(_REL_NOTIFY_COALESCE_MS)
        self._rel_notify_timer.timeout.connect(self._on_rel_notify)
        self._relStepDone.connect(self._on_rel_step_done)
        # The fresh-releases hint: its own one-thread pool (the read is not a
        # TIDAL request, so it never waits behind a step), its beat, and the
        # wall-clock time of the last beat (the sleep detector).
        self._rel_fresh_pool = QtCore.QThreadPool()
        self._rel_fresh_pool.setMaxThreadCount(1)
        diagnostics.register_pool("releases-fresh", self._rel_fresh_pool)
        self._rel_fresh_running = False
        self._rel_fresh_beat_at = 0.0
        self._rel_fresh_woke = False
        self._rel_fresh_backoff_until = 0.0
        self._rel_feed_backoff_until = 0.0
        self._rel_fresh_timer = QtCore.QTimer(self)
        self._rel_fresh_timer.setInterval(_REL_FRESH_TICK_MS)
        self._rel_fresh_timer.timeout.connect(self._releases_fresh_tick)
        self._relFreshDone.connect(self._on_rel_fresh_done)
        self._relMirrorDone.connect(self._on_rel_mirror_done)
        # The favorites writes: wanted state per artist, in click order.
        self._rel_tidal_out: dict = {}
        self._rel_tidal_inflight: set = set()
        self._rel_tidal_gen = 0
        self._rel_tidal_last = 0.0
        self._rel_tidal_sent: collections.deque = collections.deque()
        self._rel_tidal_backoff_until = 0.0
        # Asks made by hand (ALSO FOLLOW ON TIDAL): they outlive the mirror pref.
        self._rel_tidal_hand: set = set()
        # Per artist with an ask waiting: whether TIDAL held it before the first.
        self._rel_tidal_base: dict = {}
        # The generation whose favorites the store's tidal_fav flags describe
        # (a walk of this session's account finished, or was recent enough to
        # skip); -1 until then.
        self._rel_fav_known_gen = -1
        self._rel_tidal_timer = QtCore.QTimer(self)
        self._rel_tidal_timer.setSingleShot(True)
        self._rel_tidal_timer.timeout.connect(self._releases_tidal_drain)
        self._relTidalDone.connect(self._on_rel_tidal_done)
        self._rel_import_gen = 0
        self._relImportDone.connect(self._on_rel_import_done)
        # The library side: the run in flight is the automatic one, the
        # library publish the last look covered, when the automatic import
        # last started, and the tab's notice.
        self._rel_import_auto = False
        self._rel_lib_stamp_seen = -1
        self._rel_auto_last = 0.0
        self._rel_notice: dict = {"kind": "", "n": 0}
        self._rel_more_norms: list = []
        self._rel_lib_look_deferred = False
        self._relMoreCounted.connect(self._on_rel_more_counted)
        # Queued whatever thread emits: the start arms GUI-thread timers.
        self._relStartAsked.connect(self._releases_maybe_start, QtCore.Qt.ConnectionType.QueuedConnection)
        # The auto-downloads still waiting to hear whether their album reached
        # the queue, and how often each one failed to.
        self._rel_auto_watch: set = set()
        self._rel_auto_tries: dict = {}
        # Which of those are music videos (their store ids carry a prefix).
        self._rel_auto_videos: set = set()
        download_state = getattr(self, "downloadState", None)
        if download_state is not None:
            download_state.connect(self._releases_on_download_state)
        scan_status = getattr(self, "libraryScanStatusChanged", None)
        if scan_status is not None:
            # Fires on every scan progress tick and at its end, queued to
            # this thread; the handler is cheap until the library moved.
            scan_status.connect(self._releases_library_moved)

    # ----- gating -----------------------------------------------------------

    def _releases_on_gui_thread(self) -> bool:
        """Whether the caller runs on this object's own (GUI) thread. A
        partial test stub with no Qt thread counts as on it."""
        owner = getattr(self, "thread", None)
        if not callable(owner):
            return True
        return QtCore.QThread.currentThread() is owner()

    @Slot()
    def _releases_maybe_start(self) -> None:
        """Start (or resume) the pacer once both gates are open: signed in and
        the launch overlay gone. Harmless to call more than once.

        A sign-in completes on a pool worker, and a QTimer started from
        another thread never fires (Qt only warns), so a call from off the
        GUI thread is posted back to it first."""
        if not self._releases_on_gui_thread():
            self._relStartAsked.emit()
            return
        if not (getattr(self, "_logged_in", False) and getattr(self, "_rel_revealed", False)):
            return
        self._rel_step_running = False
        self._rel_mirror_due = True
        self._releases_rearm(0)
        self._releases_library_moved()
        # A read in flight keeps its flag: a second start while it runs
        # must not queue another (a stop clears it, see _releases_stop).
        self._rel_fresh_beat_at = time.time()
        with contextlib.suppress(Exception):
            self._rel_fresh_timer.start()

    def _releases_stop(self) -> None:
        """Sign-out, reset or quit: no step may start, and one in flight is
        dropped on arrival (its generation no longer matches)."""
        self._rel_gen = getattr(self, "_rel_gen", 0) + 1
        self._rel_step_running = False
        self._rel_round_done = 0
        self._rel_walk_fails = 0
        self._rel_deep_queue = collections.deque()
        self._rel_extra_spent = False
        self._rel_step_at = 0.0
        with contextlib.suppress(Exception):
            self._rel_timer.stop()
        with contextlib.suppress(Exception):
            self._rel_notify_timer.stop()
        with contextlib.suppress(Exception):
            self._rel_fresh_timer.stop()
        self._rel_fresh_running = False
        self._rel_notify_pending = 0
        self._rel_notify_items = []
        # A library import in flight is dropped the same way.
        self._rel_import_gen = getattr(self, "_rel_import_gen", 0) + 1
        self._rel_import_running = False
        self._rel_import_auto = False
        # The next sign-in looks at the library afresh.
        self._rel_lib_stamp_seen = -1
        self._rel_more_norms = []
        with contextlib.suppress(Exception):
            self._releases_set_notice("", 0)
        # Pending favorites writes belonged to the account that asked.
        self._rel_tidal_out = {}
        self._rel_tidal_inflight = set()
        self._rel_tidal_hand = set()
        self._rel_tidal_base = {}
        with contextlib.suppress(Exception):
            self._rel_tidal_timer.stop()

    def _releases_signed_out(self) -> None:
        """A sign-out (not a quit): the next account to sign in walks its own
        favorites at once, whatever the last walk's age. The store's
        tidal_fav flags still describe the account that left until then."""
        with contextlib.suppress(Exception):
            self._releases.meta_set("last_walk", "0")

    def _releases_rearm(self, ms: int) -> None:
        with contextlib.suppress(Exception):
            self._rel_timer.start(max(0, int(ms)))

    def _releases_queue_busy(self) -> bool:
        return bool(getattr(self, "_job_aborts", None))

    # ----- the pacer --------------------------------------------------------

    def _releases_due_from(self) -> int:
        """The check time a follow must be older than to be due on top of
        the round: never checked (0), or before the last Check now."""
        return max(1, self._releases.rescan_at())

    def _releases_step_ms(self) -> int:
        """How long until the next step: a burst while any follow has never
        been checked or waits for a Check now, else the round spread over
        the follows."""
        if self._releases.due_count(self._releases_due_from()) > 0 or self._releases.hinted_count() > 0:
            return _REL_BURST_BUSY_MS if self._releases_queue_busy() else _REL_BURST_MS
        n = max(1, self._releases.followed_count())
        return max(_REL_MIN_STEP_MS, min(_REL_MAX_STEP_MS, _REL_ROUND_S * 1000 // n))

    def _releases_tick(self) -> None:
        """(GUI thread) One pacer beat: pick the stalest follow and dispatch
        its check, or wait."""
        if not (getattr(self, "_logged_in", False) and getattr(self, "_rel_revealed", False)):
            return
        if self._rel_step_running or getattr(self, "_rel_import_running", False):
            return
        now = time.monotonic()
        if now < self._rel_backoff_until:
            self._releases_rearm((self._rel_backoff_until - now) * 1000)
            return
        if self._rel_mirror_due:
            self._rel_mirror_due = False
            self._rel_step_running = True
            self._rel_walk_at = now
            gen = self._rel_gen
            self._rel_pool.start(Worker(lambda: self._releases_mirror_walk(gen)))
            return
        row = self._releases.next_due(max(int(time.time()) - _REL_ROUND_S, self._releases.rescan_at()))
        # The extra work (a deep read, once the first round has seeded every
        # follow, and the MusicBrainz check) gets one beat per step slot:
        # between two steps, or one per idle beat when nothing is due. A
        # follow due at its slot goes first.
        between = row is None or now < getattr(self, "_rel_step_at", 0.0)
        if between and not getattr(self, "_rel_extra_spent", False) and self._releases_extra_turn():
            self._rel_extra_spent = True
            return
        if row is None:
            self._rel_extra_spent = False
            if self._rel_round_done:
                self._releases.meta_set("last_round_end", str(int(time.time())))
                self._rel_round_done = 0
                self._rel_mirror_due = True
                self.releasesProgress.emit(0, self._releases.followed_count())
            elif self._releases_walk_retry_due(now):
                # No round to close (no follows yet, say): a failed walk
                # would otherwise never be tried again this session.
                self._rel_mirror_due = True
            self._releases_rearm(_REL_MAX_STEP_MS)
            return
        self._rel_step_running = True
        # The next step's slot, kept whatever runs between (see
        # _releases_extra_rearm): a deep read or a MusicBrainz check never
        # spends a step's interval of its own.
        self._rel_step_at = time.monotonic() + self._releases_step_ms() / 1000.0
        self._rel_extra_spent = False
        gen = self._rel_gen
        self._rel_pool.start(Worker(lambda: self._releases_check_one(row, gen)))

    def _releases_extra_turn(self) -> bool:
        """(GUI thread) Dispatch the extra beat's work: a deep read or a
        MusicBrainz check, the two taking turns while both wait. True when
        one was dispatched."""
        deep = self._releases_deep_due()
        due = self._releases_mb_due()
        if not deep and due is None:
            return False
        self._rel_deep_turn = not getattr(self, "_rel_deep_turn", False)
        self._rel_step_running = True
        gen = self._rel_gen
        if deep and (self._rel_deep_turn or due is None):
            entry = self._rel_deep_queue.popleft()
            self._rel_pool.start(Worker(lambda: self._releases_deep_read(entry, gen)))
            return True
        # Made here, on the GUI thread, and handed over: a first check never
        # builds the shared service from a worker, and one still running
        # through a reset keeps the closed service it started with.
        client = self._releases_mb_client()
        self._rel_pool.start(Worker(lambda: self._releases_mb_check(due, gen, client)))
        return True

    def _releases_extra_rearm(self) -> None:
        """(GUI thread) After a deep read or a MusicBrainz check: the next
        beat at the step's slot, or shortly when that has passed, so the
        extra work is fitted between steps and the round keeps its pace."""
        left = (getattr(self, "_rel_step_at", 0.0) - time.monotonic()) * 1000
        self._releases_rearm(max(_REL_MIN_STEP_MS, int(left)))

    def _releases_extra_pending(self) -> bool:
        """(GUI thread) A deep read or a MusicBrainz check would take the next
        beat (see _releases_tick)."""
        return self._releases_deep_due() or self._releases_mb_due() is not None

    def _releases_deep_due(self) -> bool:
        """(GUI thread) A deep read is in line and may run: the first round
        has seeded every follow. An artist unfollowed while in line leaves
        it, so it is not read for nothing."""
        queue = getattr(self, "_rel_deep_queue", None)
        while queue and not self._releases.is_followed(queue[0][0]):
            queue.popleft()
        return bool(queue) and self._releases.never_checked_count() == 0

    def _releases_check_one(self, row: dict, gen: int) -> None:
        """(worker) Fetch one followed artist's newest releases and record
        them; the outcome is posted to the GUI thread."""
        aid = str(row.get("artist_id") or "")
        result: dict = {
            "artist_id": aid,
            "name": row.get("name") or "",
            "fresh": [],
            "failed": False,
            "rate_limited": False,
            "gen": gen,
        }
        try:
            self._rel_head_full = []
            releases, failures, limited = self._artist_recent_releases(aid)
            if gen != self._rel_gen:
                return
            if limited:
                result["rate_limited"] = True
            elif not releases and failures:
                self._releases.record_failure(aid)
                result["failed"] = True
            else:
                cut = list(getattr(self, "_rel_head_full", []) or [])
                result["joined"] = []
                result["fresh"] = self._releases.record_check(
                    aid,
                    releases,
                    complete=not failures,
                    keep_from=self._releases_keep_from(),
                    # Nothing ages out: the feed window applies when it is
                    # read, and the timeline draws every release on record.
                    prune_from="",
                    full={True: "get_albums" in cut, False: "get_ep_singles" in cut},
                    joined=result["joined"],
                )
                result["fresh"] += self._releases_check_videos(aid)
                # The releases this follow is featured on, for the
                # auto-download alone: never announced, never in the feed.
                result["guests"] = self._releases_check_guests(aid)
                # The music is on record: a rate limit on the video (or
                # featured) read still announces it, then pauses the sweep.
                if getattr(self, "_rel_video_limited", False) or getattr(self, "_rel_guest_limited", False):
                    result["video_limited"] = True
                # A section that came back full has more behind it: the
                # deep read fetches the rest, once a season.
                full = list(getattr(self, "_rel_head_full", []) or [])
                if full and time.time() - self._releases.deep_read_at(aid) >= _REL_DEEP_AGAIN_S:
                    result["deep"] = full
        except Exception:
            logger.debug("releases: step failed", exc_info=True)
            result["failed"] = True
            with contextlib.suppress(Exception):
                self._releases.record_failure(aid)
        if gen == self._rel_gen:
            self._relStepDone.emit(result)

    def _releases_check_videos(self, aid: str) -> list:
        """(worker) Read and record a follow's music videos when they are due
        on this visit (see FollowStore.video_due) and the setting shows them;
        returns the new ones. A read that fails is tried on the next visit;
        a rate limit is left in ``_rel_video_limited`` for the step."""
        self._rel_video_limited = False
        if not self._releases_videos_on():
            return []
        if not self._releases.video_due(aid, int(time.time()), self._releases.rescan_at()):
            return []
        videos, ok, limited, whole = self._artist_recent_videos(aid)
        if limited:
            self._rel_video_limited = True
            return []
        if not ok:
            return []
        return self._releases.record_videos(aid, videos, whole=whole, keep_from=self._releases_keep_from())

    def _releases_check_guests(self, aid: str) -> list:
        """(worker) Read and record the releases a follow is featured on
        when the auto-download wants them and they are due on this visit
        (see FollowStore.guest_due); returns the new ones. A read that fails
        is tried on the next visit; a rate limit is left in
        ``_rel_guest_limited`` for the step."""
        self._rel_guest_limited = False
        if not self._releases_guest_on():
            return []
        if not self._releases.guest_due(aid, int(time.time()), self._releases.rescan_at()):
            return []
        rows, ok, limited = self._artist_recent_guests(aid)
        if limited:
            self._rel_guest_limited = True
            return []
        if not ok:
            return []
        return self._releases.record_guests(aid, rows, keep_from=self._releases_keep_from())

    def _artist_recent_guests(self, artist_id: str) -> tuple[list, bool, bool]:
        """(worker) The newest releases by other named artists that this one
        is featured on (never a Various Artists compilation), deduped by
        edition as its own releases are. ``(rows, ok, rate_limited)``."""
        artist, _failures, limited = self._releases_artist_obj(artist_id)
        fn = getattr(artist, "get_other", None) if artist is not None else None
        if not callable(fn):
            return [], False, limited
        try:
            raw = list(fn(limit=_REL_FETCH_LIMIT) or [])
        except Exception as exc:
            if _is_rate_limit(exc):
                return [], False, True
            logger.debug("releases: get_other failed for artist %s", artist_id, exc_info=True)
            return [], False, False
        return collapse_atmos_twins(self._releases_section_rows(self._featured_only(raw))), True, False

    def _artist_recent_videos(self, artist_id: str) -> tuple[list, bool, bool, bool]:
        """(worker) An artist's music videos as the rows the store records
        (bare ids), deduped the way the artist page dedups them. Returns
        ``(rows, ok, rate_limited, whole)``; ``whole`` when the answer came
        back short of the limit, so it is the artist's every video."""
        artist, _failures, limited = self._releases_artist_obj(artist_id)
        fn = getattr(artist, "get_videos", None) if artist is not None else None
        if not callable(fn):
            return [], False, limited, False
        try:
            raw = list(fn(limit=_REL_FETCH_LIMIT) or [])
        except Exception as exc:
            if _is_rate_limit(exc):
                return [], False, True, False
            logger.debug("releases: get_videos failed for artist %s", artist_id, exc_info=True)
            return [], False, False, False
        return self._releases_video_rows(raw), True, False, len(raw) < _REL_FETCH_LIMIT

    def _releases_video_rows(self, raw: list) -> list:
        """(worker) Videos as release dicts: the card's still at 480x320 (the
        feed draws it about 200 px wide), the day TIDAL gives, its length."""
        out: list = []
        for v in self._dedup_videos(raw):
            d = self._video_dict(v)
            art = ""
            with contextlib.suppress(Exception):
                art = v.image(480, 320) or ""
            date = str(d.get("date") or "")[:10]
            try:
                seconds = int(getattr(v, "duration", 0) or 0)
            except (TypeError, ValueError):
                seconds = 0
            out.append(
                {
                    "id": d["id"],
                    "title": d.get("title") or "",
                    "artist": d.get("artist") or "",
                    "artist_id": str(getattr(getattr(v, "artist", None), "id", "") or ""),
                    "artists": d.get("artists") or [],
                    "art": art or d.get("art_big") or d.get("art") or "",
                    "date": date,
                    "listed": "",
                    "year": date[:4],
                    "explicit": bool(d.get("explicit")),
                    "duration_sec": seconds,
                    "quality": d.get("quality") or "",
                }
            )
        return out

    def _releases_videos_on(self) -> bool:
        """The Music videos setting: shown unless set to hide."""
        prefs = getattr(self, "_waves_prefs", {}) or {}
        return str(prefs.get("releases_videos", "show")) != "hide"

    def _releases_videos_switched(self, on: bool) -> None:
        """(GUI thread) The Music videos setting flipped: the pane redraws with or
        without them at once (the rows stay on record). Back on, each
        follow's next video read is taken in silently, so the videos that
        came out while they were hidden are not announced in a burst."""
        logger.info("music videos in Releases switched %s", "on" if on else "off")
        if on:
            self._releases.reset_video_seeding()
        self.releasesChanged.emit()

    def _releases_featured_on(self) -> bool:
        """The Featured releases setting: shown unless set to hide. Featured
        means a release TIDAL lists under a follow that an artist nobody
        follows here leads (see FollowStore.feed)."""
        prefs = getattr(self, "_waves_prefs", {}) or {}
        return str(prefs.get("releases_featured", "show")) != "hide"

    def _releases_featured_switched(self, on: bool) -> None:
        """(GUI thread) The Featured releases setting flipped: the pane and the
        Artists page redraw with or without them at once (the rows stay on
        record and keep being checked)."""
        logger.info("featured releases in Releases switched %s", "on" if on else "off")
        self.releasesChanged.emit()

    def _releases_drop_featured(self, fresh: list) -> list:
        """(GUI thread) A step's new releases less the featured ones while
        they are hidden: what the feed does not show is never announced or
        downloaded either."""
        if self._releases_featured_on():
            return fresh
        followed = self._releases.followed_ids()
        kept = [r for r in fresh if not str(r.get("artist_id") or "") or str(r.get("artist_id")) in followed]
        if len(kept) != len(fresh):
            logger.info("releases: %d featured release(s) left hidden", len(fresh) - len(kept))
        return kept

    @Slot(result=bool)
    def releasesCheckNow(self) -> bool:
        """Check every follow now (albums, EPs, singles and music videos),
        at the first-check pace: the round is not shortened for anyone,
        it starts over at once. Refused while a check of every follow is
        already running, or with nobody followed or signed out."""
        if not self.releasesCheckNowReady():
            return False
        n = self._releases.followed_count()
        self._releases.request_rescan(int(time.time()))
        self._rel_round_done = 0
        logger.info("check now: %d follow(s) to check", n)
        self.releasesProgress.emit(0, n)
        self.releasesChanged.emit()
        if not self._rel_step_running:
            self._releases_rearm(0)
        return True

    @Slot(result=bool)
    def releasesCheckNowReady(self) -> bool:
        """Whether Check now may run: signed in, someone followed, no check
        of every follow running, and the last one asked for at least
        _REL_CHECK_NOW_GAP_S ago (each costs a few requests per follow)."""
        if not getattr(self, "_logged_in", False) or self._releases.followed_count() <= 0:
            return False
        if self._releases.due_count(self._releases_due_from()) > 0:
            return False
        return time.time() - self._releases.rescan_at() >= _REL_CHECK_NOW_GAP_S

    def _artist_recent_releases(self, artist_id: str) -> tuple[list, list, bool]:
        """(worker) An artist's newest albums and EPs/singles as the release
        dicts the store records, deduped by edition exactly as the artist page
        is. Returns ``(rows, failures, rate_limited)``: ``failures`` names each
        section that could not be fetched (so a short result is told apart
        from a small catalog), and a rate limit stops the fetch at once.

        Each dict carries ``sibling_ids``: the raw (pre-dedup) ids of every
        edition in its group. Which edition survives the dedup depends on the
        quality cap and the explicit preference, so the baseline is judged on
        whole groups; keyed on the survivor alone, a settings change would
        report an old album as new."""
        # Which sections came back full (so a deep read has more to fetch)
        # is left for the step to read right after: the sweep's pool runs
        # one worker.
        self._rel_head_full: list = []
        artist, failures, limited = self._releases_artist_obj(artist_id)
        if artist is None:
            return [], failures, limited
        out: list = []
        for getter in ("get_albums", "get_ep_singles"):
            fn = getattr(artist, getter, None)
            if not callable(fn):
                continue
            try:
                raw = list(fn(limit=_REL_FETCH_LIMIT) or [])
            except Exception as exc:
                if _is_rate_limit(exc):
                    return out, [*failures, getter], True
                logger.debug("releases: %s failed for artist %s", getter, artist_id, exc_info=True)
                failures.append(getter)
                continue
            if len(raw) >= _REL_FETCH_LIMIT:
                self._rel_head_full.append(getter)
            out.extend(self._releases_section_rows(raw))
        # An Atmos twin folds into its stereo edition here, before the store
        # sees it: the feed shows one card, and a fresh album is one banner
        # and one download rather than two of the same record.
        return collapse_atmos_twins(out), failures, False

    def _releases_deep_read(self, entry: tuple, gen: int) -> None:
        """(worker) Read one followed artist's catalog whole, section by
        section, a page at a time with a pause between pages, and put the
        older releases on record silently. The read starts from the top
        (one more page than the head), so an album whose editions straddle
        the head's end is grouped once, with the row the sweep holds; but
        what the head itself covers stays the sweep's to judge (see
        ``_releases_past_head``), so a release out since the last check is
        still announced. A rate limit stops the read where it is (the step
        done handler puts the artist back in line). A catalog past the page
        cap is read as far as the cap and counts as read; a failed page
        leaves the read unfinished and tries again a day later. An artist
        unfollowed meanwhile stops the paging."""
        aid, name, sections = str(entry[0]), str(entry[1] or ""), list(entry[2] or [])
        result: dict = {
            "deep_done": True,
            "artist_id": aid,
            "name": name,
            "sections": list(sections),
            "added": 0,
            "rate_limited": False,
            "gen": gen,
        }
        complete = True
        rows: list = []
        try:
            artist = self._releases_artist_obj(aid)[0]
            if artist is None:
                complete = False
                sections = []
            for getter in sections:
                fn = getattr(artist, getter, None)
                if not callable(fn):
                    continue
                paged = self._releases_deep_pages(fn, gen, lambda: self._releases.is_followed(aid))
                if paged is None:
                    return
                raw, done, limited = paged
                complete = complete and done
                rows.extend(self._releases_past_head(raw))
                if limited:
                    result["rate_limited"] = True
                    break
            if gen != self._rel_gen:
                return
            whole = complete and not result["rate_limited"]
            result["added"] = self._releases.record_deep(aid, collapse_atmos_twins(rows), complete=whole)
            if not whole and not result["rate_limited"]:
                # Unfinished (a page failed): again after a day, not on every
                # check of the artist.
                self._releases.deep_touch(aid, int(time.time()) - _REL_DEEP_AGAIN_S + _REL_DEEP_RETRY_S)
        except Exception:
            logger.debug("releases: deep read failed", exc_info=True)
        if gen == self._rel_gen:
            self._relStepDone.emit(result)

    def _releases_past_head(self, raw: list) -> list:
        """(worker) A deep read section's release dicts for what lies past
        the sweep's head: grouped over the whole read (so an edition group
        straddling the head's end is one row), then every group with a
        member among the first ``_REL_FETCH_LIMIT`` left out. Those are the
        sweep's; one of them may have come out since the artist's last
        check, and only the check announces it."""
        head = {str(getattr(a, "id", "") or "") for a in raw[:_REL_FETCH_LIMIT]} - {""}
        out = []
        for r in self._releases_section_rows(raw):
            ids = {str(r.get("id") or ""), *(str(i) for i in r.get("sibling_ids") or [])}
            if not ids & head:
                out.append(r)
        return out

    def _releases_deep_pages(self, fn, gen: int, followed=None):
        """(worker) One section from the top, a page at a time with a pause
        before each: ``(raw, done, rate_limited)``, ``done`` once a short
        page ended it or the page cap was reached (the read cannot get
        further; a failed page leaves it unfinished), or None once the
        generation moved on. Stops, unfinished, when ``followed`` turns
        false (the artist was unfollowed mid-read)."""
        raw: list = []
        offset = 0
        for _page in range(_REL_DEEP_MAX_PAGES):
            if gen != self._rel_gen:
                return None
            if followed is not None and not followed():
                return raw, False, False
            time.sleep(_REL_DEEP_PAGE_GAP_S)
            try:
                page = list(fn(limit=_REL_DEEP_PAGE, offset=offset) or [])
            except Exception as exc:
                logger.debug("releases: a deep page failed", exc_info=True)
                return raw, False, _is_rate_limit(exc)
            raw.extend(page)
            offset += len(page)
            if len(page) < _REL_DEEP_PAGE:
                return raw, True, False
        logger.info("deep read: a section reached the page cap (%d pages)", _REL_DEEP_MAX_PAGES)
        return raw, True, False

    def _releases_mb_due(self) -> dict | None:
        """(GUI thread) The follow whose MusicBrainz check is due (the oldest),
        with the setting on and every follow seeded (the check needs the
        TIDAL side to match against); None otherwise."""
        if not self._waves_pref_bool("releases_musicbrainz") or self._releases.never_checked_count() > 0:
            return None
        return self._releases.mb_next_due(int(time.time()) - _REL_MB_AGAIN_S)

    def _releases_mb_client(self):
        """The MusicBrainz client, made on first use over the process's one
        service (shared with the library arbiter: one pacer, and what either
        one learned is on record for the other)."""
        client = getattr(self, "_rel_mb_client", None)
        if client is None:
            # Switched off mid-check, the check sends nothing more.
            client = self._rel_mb_client = musicbrainz.MusicBrainzClient(
                service=self._mb_service_instance(), alive=lambda: self._waves_pref_bool("releases_musicbrainz")
            )
        return client

    def _releases_mb_check(self, row: dict, gen: int, client=None) -> None:
        """(worker) One follow's MusicBrainz check: resolve it to an artist
        (or page the one already known), match the release groups against
        its TIDAL rows and put the result on record. A check MusicBrainz
        could not answer is stamped to go again in an hour; the outcome
        goes to the GUI thread like a step's."""
        aid = str(row.get("artist_id") or "")
        name = str(row.get("name") or "")
        result: dict = {"mb_done": True, "artist_id": aid, "name": name, "changed": False, "failed": False, "gen": gen}
        try:
            client = client or self._releases_mb_client()
            rows = self._releases.career_rows(aid)
            mbid = str(row.get("mbid") or "")
            how = "match"
            if mbid:
                groups = client.release_groups(mbid)
                found = None if groups is None else (mbid, groups)
            else:
                leaned: list = []

                def confirm() -> str:
                    # Not sure on the title rule alone: the user's own files
                    # carrying the artist's MusicBrainz id, else the library
                    # lookup's proof under the same name (unless that proof
                    # leaned on this check itself).
                    key = artist_import.name_key(name)
                    tag = self._mb_tag_ids().get(key, "") if hasattr(self, "_mb_tag_ids") else ""
                    if tag:
                        leaned.append(("file_tag", tag))
                        return tag
                    ask = getattr(client, "confirmed", None)
                    got = ask("library", name, refuse=frozenset({"follow"})) if ask else ""
                    leaned.append(("library", got))
                    return got

                found = musicbrainz.resolve_artist(client, name, rows, confirm=confirm)
                if found and found[0] and leaned and leaned[-1][1] == found[0]:
                    how = leaned[-1][0]
            if gen != self._rel_gen:
                return
            if found is None:
                result["failed"] = True
                self._releases.mb_touch(aid, int(time.time()) - _REL_MB_AGAIN_S + _REL_MB_RETRY_S)
            else:
                mbid, groups = found
                if mbid and hasattr(client, "link"):
                    # The proof, for the library lookup to lean on by name.
                    client.link("tidal", aid, name, mbid, how)
                matched = musicbrainz.match_groups(groups, rows)
                result["changed"] = self._releases.record_mb(aid, mbid, matched)
                result["known"] = bool(mbid)
                result["missing"] = sum(1 for g in matched if not g.get("release_id"))
        except Exception:
            logger.debug("releases: the MusicBrainz check failed", exc_info=True)
            result["failed"] = True
            with contextlib.suppress(Exception):
                self._releases.mb_touch(aid, int(time.time()) - _REL_MB_AGAIN_S + _REL_MB_RETRY_S)
        if gen == self._rel_gen:
            self._relStepDone.emit(result)

    def _releases_artist_obj(self, artist_id: str) -> tuple:
        """(worker) The artist to ask for album lists: the one a page or
        search already loaded, else an id-only handle. The album lists are
        addressed by artist id alone, so a step never spends a request on
        the artist's own record. The handle is never cached: it has no
        name or picture for a page to show. ``(artist, failures,
        rate_limited)``."""
        artist = self._objs["artist"].get(artist_id) if hasattr(self, "_objs") else None
        if artist is not None:
            return artist, [], False
        try:
            artist = Artist(self.tidal.session, None)
            artist.id = int(artist_id)
        except Exception:
            logger.debug("releases: no handle for artist %s", artist_id, exc_info=True)
            return None, ["artist"], False
        return artist, [], False

    def _releases_section_rows(self, raw: list) -> list:
        """(worker) One section's albums as release dicts: the edition the
        settings pick for each group, carrying the whole group's ids."""
        items = self._dedup_albums(raw)
        groups: dict = {}
        for a in raw:
            with contextlib.suppress(Exception):
                groups.setdefault(self._album_key(a), []).append(str(getattr(a, "id", "") or ""))
        out: list = []
        for a in items:
            d = self._album_dict(a)
            try:
                d["sibling_ids"] = [i for i in groups.get(self._album_key(a), []) if i]
            except Exception:
                d["sibling_ids"] = []
            out.append(d)
        return out

    def _releases_keep_from(self, months: int | None = None) -> str:
        """The first day of the month ``months`` months ago (the
        ``releases_window_months`` pref when not given): the feed shows rows
        from that day on. A storage policy of the sweep (the payload
        builders themselves never read the clock)."""
        if months is None:
            try:
                months = int(str(self._waves_prefs.get("releases_window_months", "12")).strip() or 12)
            except (TypeError, ValueError):
                months = 12
        months = max(1, min(_REL_WINDOW_MAX_MONTHS, int(months)))
        today = _dt.date.today()
        total = today.year * 12 + (today.month - 1) - months
        return f"{total // 12:04d}-{total % 12 + 1:02d}-01"

    def _releases_window_changed(self) -> None:
        """The window pref moved: the feed is read through the new window at
        once (narrower hides rows, wider shows the ones the store kept)."""
        self.releasesChanged.emit()

    @Slot("QVariant")
    def _on_rel_step_done(self, result) -> None:
        """(GUI thread) A step landed: back off on a rate limit, else count it,
        announce what was new, and arm the next step. A step that lands after
        a sign-out or reset announces, downloads and re-arms nothing."""
        result = dict(result or {})
        if int(result.get("gen", self._rel_gen)) != self._rel_gen:
            return
        self._rel_step_running = False
        if result.get("deep_done"):
            self._on_rel_deep_done(result)
            return
        if result.get("mb_done"):
            self._on_rel_mb_done(result)
            return
        if result.get("rate_limited"):
            self._releases_back_off()
            return
        # A rate limit on the videos alone still counts as one: the level
        # climbs with each, as it does for the albums.
        if not result.get("video_limited"):
            self._rel_backoff_level = 0
        self._releases_deep_ask(result)
        self._rel_round_done += 1
        self.releasesProgress.emit(self._rel_round_done, self._releases.followed_count())
        fresh = self._releases_drop_featured(list(result.get("fresh") or []))
        if fresh:
            self._releases_announce(result, fresh)
            self._releases_auto_download(fresh)
        self._releases_auto_download_joined(list(result.get("joined") or []))
        self._releases_auto_download_guests(list(result.get("guests") or []))
        self._releases_drain_auto_downloads()
        self.releasesChanged.emit()
        if result.get("video_limited"):
            self._releases_back_off()
        elif self._releases_extra_pending():
            # Halfway to the next step: a deep read or a MusicBrainz check
            # fits in the interval instead of taking one of its own.
            self._releases_rearm(self._releases_step_ms() // 2)
        else:
            self._releases_rearm(self._releases_step_ms())
        if getattr(self, "_rel_lib_look_deferred", False):
            self._rel_lib_look_deferred = False
            self._releases_library_moved()

    def _releases_announce(self, result: dict, fresh: list) -> None:
        """(GUI thread) Put a step's new releases in line for the next banner."""
        logger.info("%d new release(s) from %s", len(fresh), diagnostics.content(str(result.get("name") or "")))
        self._rel_notify_pending += len(fresh)
        for r in fresh:
            if len(self._rel_notify_items) >= _REL_NOTIFY_KEEP:
                break
            self._rel_notify_items.append(
                {
                    "artist": str(result.get("name") or r.get("artist") or ""),
                    "title": str(r.get("title") or ""),
                    "rtype": str(r.get("rtype") or ""),
                }
            )
        with contextlib.suppress(Exception):
            if not self._rel_notify_timer.isActive():
                self._rel_notify_timer.start()

    def _releases_deep_ask(self, result: dict) -> None:
        """(GUI thread) A step whose head came back full puts its artist in
        line for a deep read, once."""
        deep = list(result.get("deep") or [])
        aid = str(result.get("artist_id") or "")
        if not deep or not aid or result.get("failed"):
            return
        if all(e[0] != aid for e in self._rel_deep_queue):
            self._rel_deep_queue.append((aid, str(result.get("name") or ""), deep))

    def _on_rel_deep_done(self, result: dict) -> None:
        """(GUI thread) A deep read landed: it counts for no round and
        announces nothing; the timeline redraws when older releases went
        on record. A rate limit backs the sweep off and keeps the artist
        first in line."""
        if result.get("rate_limited"):
            self._rel_deep_queue.appendleft(
                (str(result.get("artist_id") or ""), str(result.get("name") or ""), list(result.get("sections") or []))
            )
            self._releases_back_off()
            return
        self._rel_backoff_level = 0
        added = int(result.get("added") or 0)
        if added:
            logger.info(
                "deep read: %d older release(s) from %s", added, diagnostics.content(str(result.get("name") or ""))
            )
            self.releasesChanged.emit()
        self._releases_extra_rearm()

    def _on_rel_mb_done(self, result: dict) -> None:
        """(GUI thread) A MusicBrainz check landed: it counts for no round
        and announces nothing; the timeline redraws when the set of
        releases TIDAL lacks changed, unless the setting went off meanwhile
        (the marks are hidden then, whatever is on record)."""
        name = diagnostics.content(str(result.get("name") or ""))
        if result.get("failed"):
            logger.info("MusicBrainz check could not be made for %s", name)
        elif result.get("known"):
            logger.info("MusicBrainz check: %d release(s) not on TIDAL for %s", int(result.get("missing") or 0), name)
        else:
            logger.info("MusicBrainz check: no sure match for %s", name)
        if result.get("changed") and self._waves_pref_bool("releases_musicbrainz"):
            self.releasesChanged.emit()
        self._releases_extra_rearm()

    def _releases_musicbrainz_switched(self, on: bool) -> None:
        """(GUI thread) The MusicBrainz setting flipped: the marks show or
        hide at once (the rows stay on record), and on it starts checking
        on the next beat."""
        self._rel_extra_spent = False
        logger.info("MusicBrainz check switched %s", "on" if on else "off")
        self.releasesChanged.emit()
        if on:
            self._releases_rearm(0)

    # ----- the fresh-releases hint -------------------------------------------

    def _releases_fresh_on(self) -> bool:
        return self._waves_pref_bool("releases_fresh_hints")

    def _releases_fresh_every(self, now: float) -> int:
        """Seconds between two reads of the list: shorter on release night."""
        night = fresh_releases.in_release_window(_dt.datetime.fromtimestamp(now))
        return _REL_FRESH_NIGHT_S if night else _REL_FRESH_EVERY_S

    def _releases_fresh_tick(self) -> None:
        """(GUI thread) The hint's beat: read the list when it is due, or at
        once when the wall clock jumped past the beat (the machine slept;
        a timer does not run while it does, on any platform)."""
        now = time.time()
        last_beat = getattr(self, "_rel_fresh_beat_at", 0.0) or now
        self._rel_fresh_beat_at = now
        if now - last_beat > _REL_FRESH_TICK_MS / 1000.0 + _REL_FRESH_WAKE_S:
            logger.info("releases: the machine woke, the fresh list is read next")
            # Held until a read starts: a beat that cannot read now (a read
            # running, a pause) keeps it for the next one.
            self._rel_fresh_woke = True
        woke = getattr(self, "_rel_fresh_woke", False)
        if not (getattr(self, "_logged_in", False) and getattr(self, "_rel_revealed", False)):
            return
        if self._rel_fresh_running or not self._releases_fresh_on():
            return
        # The pauses run on the monotonic clock, so a clock set back cannot
        # stretch them.
        mono = time.monotonic()
        lb_ok = mono >= getattr(self, "_rel_fresh_backoff_until", 0.0)
        # TIDAL's feed waits out its own pause and any the sweep is under.
        tidal_ok = mono >= getattr(self, "_rel_feed_backoff_until", 0.0) and mono >= getattr(
            self, "_rel_backoff_until", 0.0
        )
        if not (lb_ok or tidal_ok):
            return
        # The first round reads every follow anyway; a hint would add nothing.
        if self._releases.followed_count() == 0 or self._releases.never_checked_count() > 0:
            return
        try:
            last = int(self._releases.meta_get("fresh_read_at", "0") or 0)
        except ValueError:
            last = 0
        # A stamp ahead of the clock (it was set back) reads as due.
        if not woke and last <= now and now - last < self._releases_fresh_every(now):
            return
        self._rel_fresh_running = True
        self._rel_fresh_woke = False
        gen = self._rel_gen
        today = self._releases_today()
        self._rel_fresh_pool.start(
            Worker(lambda: self._releases_fresh_read(gen, today, use_lb=lb_ok, use_tidal=tidal_ok))
        )

    def _releases_fresh_sources(self, use_lb: bool, use_tidal: bool, fetch, feed, result: dict) -> list:
        """(worker) Read the lists this beat may: ListenBrainz's and TIDAL's
        feed, each on its own (one failing never costs the other). A failure
        is noted in ``result`` with the pause it asked for."""
        rows: list = []
        sources = (
            ("lb", use_lb, lambda: fresh_releases.read_fresh(_REL_FRESH_DAYS, fetch)),
            ("tidal", use_tidal, feed),
        )
        for name, wanted, read in sources:
            if not wanted:
                continue
            try:
                got = read()
            except fresh_releases.FetchError as e:
                result[name + "_retry"] = e.retry_after
                result["tidal_limited"] = result.get("tidal_limited") or (name == "tidal" and "429" in str(e))
                logger.info("releases: %s list unavailable (%s)", name, e)
                continue
            except Exception as e:
                # A connection that never opened (requests' errors are
                # OSErrors too) asked nothing of the service: try again soon.
                offline = isinstance(e, OSError)
                wait = fresh_releases.OFFLINE_RETRY_SEC if offline else fresh_releases.MIN_BACKOFF_SEC
                result[name + "_retry"] = float(wait)
                logger.info("releases: %s list read failed (%s)", name, type(e).__name__)
                continue
            result[name + "_count"] = len(got)
            rows += got
        return rows

    def _releases_fresh_drop_held(self, fresh: list) -> None:
        """(worker) A TIDAL release a follow's catalog already holds is no
        news: that follow is taken off the release's credits."""
        held = self._releases.seen_pairs([r["release_id"] for r in fresh if r.get("release_id")])
        for r in fresh:
            if r.get("tidal_ids"):
                r["tidal_ids"] = [i for i in r["tidal_ids"] if (i, r["release_id"]) not in held]

    def _releases_tidal_feed(self) -> list:
        """(worker) TIDAL's feed of the account's favorite artists' new
        releases, read only (see ``waves.fresh_releases``)."""
        session = self.tidal.session
        resp = session.request.basic_request(
            "GET",
            fresh_releases.TIDAL_FEED_PATH,
            params={"userId": session.user.id},
            base_url=fresh_releases.TIDAL_V2,
        )
        status = int(getattr(resp, "status_code", 0) or 0)
        if status != 200:
            wait = fresh_releases._retry_after(getattr(resp, "headers", None))
            raise fresh_releases.FetchError(f"HTTP {status}", wait)  # noqa: TRY003
        try:
            body = resp.json()
        except ValueError:
            raise fresh_releases.FetchError("not the TIDAL feed") from None  # noqa: TRY003
        return fresh_releases.parse_tidal_feed(body)

    def _releases_fresh_read(
        self, gen: int, today: str, fetch=None, feed=None, *, use_lb: bool = True, use_tidal: bool = True
    ) -> None:
        """(worker) Read the lists, match them against the follows and mark
        the ones they name; the outcome is posted to the GUI thread."""
        result: dict = {"gen": gen, "hinted": 0}
        try:
            if fetch is None:
                agent = default_user_agent()

                def fetch(url):
                    return fresh_releases.http_fetch(url, agent)

            fresh = self._releases_fresh_sources(use_lb, use_tidal, fetch, feed or self._releases_tidal_feed, result)
            if gen != self._rel_gen:
                return
            self._releases_fresh_drop_held(fresh)
            try:
                kept = json.loads(self._releases.meta_get("fresh_known", "") or "null")
            except ValueError:
                kept = None
            known = set(kept) if isinstance(kept, dict) else None
            names, seen = fresh_releases.hinted_artists(
                fresh, self._releases.follows(), today=today, known=known, cap=_REL_FRESH_HINT_CAP
            )
            # Checked again before each write: a quit or a sign-out may have
            # closed the store while the matching ran.
            if gen != self._rel_gen:
                return
            result["hinted"] = self._releases.hint(names)
            days = _releases_fresh_known(seen, kept, today)
            if gen != self._rel_gen:
                return
            self._releases.meta_set("fresh_known", json.dumps(days, separators=(",", ":")))
            if "lb_count" in result or "tidal_count" in result:
                self._releases.meta_set("fresh_read_at", str(int(time.time())))
            logger.info(
                "releases: fresh lists read (ListenBrainz %s, TIDAL %s), %d follow(s) to check first",
                result.get("lb_count", "-"),
                result.get("tidal_count", "-"),
                result["hinted"],
            )
        except Exception:
            result["lb_retry"] = result["tidal_retry"] = float(fresh_releases.MIN_BACKOFF_SEC)
            logger.debug("releases: fresh lists read failed", exc_info=True)
        if gen == self._rel_gen:
            self._relFreshDone.emit(result)

    def _on_rel_fresh_done(self, result: dict) -> None:
        """(GUI thread) A read landed: a list that failed keeps away for the
        pause it asked for (a TIDAL rate limit pauses the sweep too); hinted
        follows bring the sweep's next beat forward."""
        if result.get("gen") != self._rel_gen:
            return
        self._rel_fresh_running = False
        now = time.monotonic()
        if "lb_retry" in result:
            self._rel_fresh_backoff_until = now + float(result["lb_retry"] or 0)
        elif "lb_count" in result:
            self._rel_fresh_backoff_until = 0.0
        if "tidal_retry" in result:
            self._rel_feed_backoff_until = now + float(result["tidal_retry"] or 0)
        elif "tidal_count" in result:
            self._rel_feed_backoff_until = 0.0
        if result.get("tidal_limited"):
            self._releases_back_off()
            return
        if int(result.get("hinted") or 0) > 0 and not self._rel_step_running:
            # Sooner, never later: a pause after a rate limit still holds
            # (the beat itself waits it out).
            left = self._rel_timer.remainingTime() if self._rel_timer.isActive() else -1
            if left < 0 or left > _REL_MIN_STEP_MS:
                self._releases_rearm(_REL_MIN_STEP_MS)

    def _releases_fresh_switched(self, on: bool) -> None:
        """(GUI thread) The hint setting flipped: on, the list is read on the
        next beat; off, the hints still waiting are dropped."""
        logger.info("fresh-releases hint switched %s", "on" if on else "off")
        with contextlib.suppress(Exception):
            if on:
                self._releases.meta_set("fresh_read_at", "0")
            else:
                # Off: the hints still waiting no longer jump the round (and
                # keep the sweep at its burst pace).
                self._releases.clear_hints()

    def _releases_back_off(self) -> None:
        """(GUI thread) A rate limit: pause the sweep, longer each time."""
        level = min(self._rel_backoff_level, len(_REL_BACKOFF_S) - 1)
        pause = _REL_BACKOFF_S[level]
        self._rel_backoff_level = min(level + 1, len(_REL_BACKOFF_S) - 1)
        self._rel_backoff_until = time.monotonic() + pause
        logger.warning("releases: the catalog sweep pauses %d min after a rate limit", pause // 60)
        self._releases_rearm(pause * 1000)

    @Slot(int, int, bool)
    def _on_rel_mirror_done(self, added: int, removed: int, limited: bool = False) -> None:
        """(GUI thread) The favorites walk landed: the pacer resumes, after
        the sweep's own pause when the walk met a rate limit."""
        self._rel_step_running = False
        if added or removed:
            self.releasesChanged.emit()
        if limited:
            self._on_rel_step_done({"rate_limited": True})
            return
        self._releases_rearm(0)

    def _on_rel_notify(self) -> None:
        """(GUI thread) One banner for everything found over the last minute."""
        n, self._rel_notify_pending = self._rel_notify_pending, 0
        items, self._rel_notify_items = list(getattr(self, "_rel_notify_items", []) or []), []
        if n <= 0 or not self._waves_pref_bool("releases_notify"):
            return
        title, message = _releases_banner(n, items)
        self._releases_notify(title, message)

    def _releases_notify(self, title: str, message: str) -> None:
        """Hand one banner to the bridge's shared notify pool."""
        self._notify_send(title, message, "releases: new-release notification handed to the OS")

    # ----- auto-download ----------------------------------------------------

    def _releases_today(self) -> str:
        return _dt.date.today().isoformat()

    def _releases_atmos_on(self) -> bool:
        """The Download Dolby Atmos setting: an album's bulk download takes
        both editions with it on, and so does an auto-download."""
        data = getattr(getattr(self, "settings", None), "data", None)
        return bool(getattr(data, "download_dolby_atmos", False))

    def _releases_auto_kinds(self) -> frozenset:
        """The kinds the auto-download takes (see REL_AUTO_KINDS)."""
        prefs = getattr(self, "_waves_prefs", {}) or {}
        return frozenset(releases_auto_kinds_text(prefs.get("releases_auto_kinds", REL_AUTO_KINDS_DEFAULT)).split(","))

    def _releases_kind_wanted(self, r: dict) -> bool:
        """Whether the auto-download takes this one of a follow's own
        releases: its kind (album, EP or single) is ticked."""
        return str(r.get("rtype") or "album") in self._releases_auto_kinds()

    def _releases_guest_on(self) -> bool:
        """Whether featured releases are read and downloaded: the switch is
        on and Featured is ticked."""
        return self._waves_pref_bool("releases_auto_download") and "featured" in self._releases_auto_kinds()

    def _releases_guest_switched(self, on: bool) -> None:
        """(GUI thread) The featured auto-download turned on or off. On, each
        follow's next featured read is a silent baseline, so what came out
        while it was off is never downloaded; off, nothing featured waits."""
        logger.info("releases: featured auto-download switched %s", "on" if on else "off")
        with contextlib.suppress(Exception):
            if on:
                self._releases.reset_guest_seeding()
            else:
                self._releases.guests_due("9999-12-31")

    def _releases_auto_download(self, fresh: list) -> None:
        """(GUI thread) With the switch on, queue each new release of a kind
        the setting ticks the day it is out; one dated ahead waits for its
        day (drained by later steps). With Download Dolby Atmos on, a
        release's folded Atmos twin goes too, as the artist page's bulk
        download takes both editions."""
        if not self._waves_pref_bool("releases_auto_download"):
            return
        today = self._releases_today()
        atmos = self._releases_atmos_on()
        for r in fresh:
            rid = str(r.get("id") or "")
            if not rid:
                continue
            if r.get("kind") == "video":
                # A music video follows the Music videos download setting.
                if not self._releases_video_download_on():
                    continue
                day = str(r.get("listed") or r.get("date") or "")[:10]
                if day and day > today:
                    self._releases.set_dl_state(rid, DL_WAITING, video=True)
                else:
                    self._releases_queue_video(rid)
                continue
            if not self._releases_kind_wanted(r):
                continue
            twins = [str(t) for t in r.get("atmos_ids") or [] if t] if atmos else []
            kind = str(r.get("rtype") or "album")
            day = str(r.get("listed") or r.get("date") or "")[:10]
            if day and day > today:
                self._releases.set_dl_state(rid, DL_WAITING)
                self._releases.atmos_wait(dict.fromkeys(twins, day), kind, str(r.get("artist_id") or ""))
                continue
            self._releases_queue_release(rid)
            for t in twins:
                self._releases_queue_twin(t, kind)

    def _releases_auto_download_joined(self, joined: list) -> None:
        """(GUI thread) An edition TIDAL listed after its release was
        announced (a Dolby Atmos twin days late, or the stereo edition after
        its twin, see FollowStore._joined_editions) follows that release: it
        goes, or waits for the release day, when the release did, and never
        when the release was not downloaded. A late twin needs Download Dolby
        Atmos, like one listed with its release."""
        if not joined or not self._waves_pref_bool("releases_auto_download"):
            return
        atmos = self._releases_atmos_on()
        today = self._releases_today()
        for j in self._releases_drop_featured(joined):
            eid, state = str(j.get("id") or ""), int(j.get("state") or 0)
            if not eid or state not in (DL_WAITING, DL_QUEUED) or not self._releases_kind_wanted(j):
                continue
            if j.get("atmos") and not atmos:
                continue
            kind = str(j.get("rtype") or "album")
            day = str(j.get("day") or "")
            if state == DL_WAITING and day > today:
                if j.get("atmos"):
                    self._releases.atmos_wait({eid: day}, kind, str(j.get("artist_id") or ""))
                else:
                    self._releases.set_dl_state(eid, DL_WAITING)
            elif j.get("atmos"):
                self._releases_queue_twin(eid, kind)
            else:
                self._releases_queue_release(eid)

    def _releases_drain_auto_downloads(self) -> None:
        if not self._waves_pref_bool("releases_auto_download"):
            return
        today = self._releases_today()
        pending = self._releases.pending_auto_downloads(today)
        # A featured release that started waiting while they were shown is
        # left alone once they are hidden, like one whose kind was unticked.
        shown = {id(r) for r in self._releases_drop_featured([r for r in pending if r.get("kind") != "video"])}
        for r in pending:
            if r.get("kind") != "video" and id(r) not in shown:
                self._releases.set_dl_state(str(r.get("id") or ""), 0)
                continue
            if r.get("kind") == "video":
                if self._releases_video_download_on():
                    self._releases_queue_video(str(r.get("id") or ""))
                else:
                    self._releases.set_dl_state(str(r.get("id") or ""), 0, video=True)
                continue
            if not self._releases_kind_wanted(r):
                # Its kind was unticked while it waited for its day.
                self._releases.set_dl_state(str(r.get("id") or ""), 0)
                continue
            self._releases_queue_release(str(r.get("id") or ""))
        self._releases_drain_atmos_twins(today)
        guests = self._releases.guests_due(today)
        if self._releases_guest_on():
            for rid, aid in guests:
                self._releases_queue_guest(rid, aid)

    def _releases_drain_atmos_twins(self, today: str) -> None:
        """Queue the waiting Atmos twins whose day has come. One is held back
        like its stereo row when its kind was unticked while it waited (a
        kind of "" is from before the kind was kept: it goes), or when its
        release is a featured one and those are hidden now."""
        twins = self._releases.atmos_due(today)
        if not self._releases_atmos_on():
            return
        kinds = self._releases_auto_kinds()
        shown = {t for t, _kind, lead in twins if self._releases_drop_featured([{"artist_id": lead}])}
        for t, kind, _lead in twins:
            if (not kind or kind in kinds) and t in shown:
                self._releases_queue_twin(t, kind)

    def _releases_auto_download_guests(self, fresh: list) -> None:
        """(GUI thread) Queue the follow's tracks on each new release it is
        featured on, the day it is out; one dated ahead waits for its day."""
        if not fresh or not self._releases_guest_on():
            return
        today = self._releases_today()
        for r in fresh:
            rid, aid = str(r.get("id") or ""), str(r.get("guest_of") or "")
            if not rid or not aid:
                continue
            day = str(r.get("listed") or r.get("date") or "")[:10]
            if day and day > today:
                self._releases.guest_wait(rid, day, aid)
            else:
                self._releases_queue_guest(rid, aid)

    def _releases_queue_guest(self, rid: str, aid: str) -> None:
        """(GUI thread) Queue the tracks a follow is credited on from a
        release it is featured on, read on the general pool. A release some
        follow lists as its own, of a kind the setting ticks, is left to
        that row, which downloads it whole. A read that fails goes back in
        line for the next step, a few times."""
        kind = self._releases.release_kind(rid)
        if kind and kind in self._releases_auto_kinds():
            return
        gen = self._scan_gen
        tries = self.__dict__.setdefault("_rel_guest_tries", {})

        def work() -> None:
            try:
                keys = self._guest_track_keys(rid, aid)
            except Exception:
                logger.debug("releases: featured release could not be read", exc_info=True)
                tries[rid] = tries.get(rid, 0) + 1
                if not self._releases_guest_on():
                    # Switched off (or Featured unticked) while it was read.
                    tries.pop(rid, None)
                elif tries[rid] < _REL_AUTO_DL_TRIES:
                    logger.info("releases: featured auto-download could not read its release, tries again")
                    self._releases.guest_wait(rid, self._releases_today(), aid)
                else:
                    logger.warning("releases: featured auto-download gave up after %d tries", tries[rid])
                return
            tries.pop(rid, None)
            logger.info("releases: auto-download queued %d track(s) from a featured release", len(keys))
            if keys:
                self._tracksQueued.emit(gen, keys)

        self.threadpool.start(Worker(work))

    def _releases_queue_twin(self, rid: str, kind: str = "") -> None:
        """Queue a folded Atmos twin (``kind`` its stereo row's). It has no
        row of its own to wait in, so a failed try waits in the store's twin
        list instead."""
        self.__dict__.setdefault("_rel_auto_twins", {})[rid] = kind
        self._releases_queue_release(rid)

    def _releases_forget_waiting(self) -> None:
        """The auto-download switch went off: nothing waits for its day."""
        with contextlib.suppress(Exception):
            if self._releases.clear_waiting():
                logger.info("releases: auto-download switched off, waiting releases dropped")

    def _releases_queue_release(self, rid: str) -> None:
        """Queue one release through the normal album path (quality, merge
        and edition settings apply exactly as for a click). The album is
        usually fetched first, so the outcome arrives later through
        ``downloadState`` (see _releases_on_download_state)."""
        if not rid:
            return
        self._releases.set_dl_state(rid, DL_QUEUED)
        self._rel_auto_watch.add(rid)
        logger.info("releases: auto-download queued")
        with contextlib.suppress(Exception):
            self.downloadAlbum(rid)

    def _releases_video_download_on(self) -> bool:
        """The Music videos download setting: an auto-download takes a new
        video only with it on, as a discography download does."""
        data = getattr(getattr(self, "settings", None), "data", None)
        return bool(getattr(data, "video_download", False))

    def _releases_queue_video(self, vid: str) -> None:
        """Queue one music video through the normal video path; the outcome
        arrives through ``downloadState`` like an album's."""
        if not vid:
            return
        self._releases.set_dl_state(vid, DL_QUEUED, video=True)
        self._rel_auto_watch.add(vid)
        self._rel_auto_videos.add(vid)
        logger.info("releases: auto-download queued a music video")
        with contextlib.suppress(Exception):
            self.downloadVideo(vid)

    @Slot(str, str)
    def _releases_on_download_state(self, media_id: str, state: str) -> None:
        """(GUI thread) How an auto-download's album fared on its way to the
        queue. Reaching it settles the release. A failed fetch puts it back
        in line for the next step, a few times; a download that was refused
        outright (no download folder, a sign-out or STOP meanwhile) leaves it
        not queued, for the user to download by hand."""
        mid = str(media_id or "")
        if mid not in self._rel_auto_watch or state == "preparing":
            return
        self._rel_auto_watch.discard(mid)
        video = mid in self._rel_auto_videos
        if state != "failed":
            self._rel_auto_videos.discard(mid)
        if state in ("queued", "running", "done"):
            self._rel_auto_tries.pop(mid, None)
            self.__dict__.get("_rel_auto_twins", {}).pop(mid, None)
            return
        if state == "failed":
            tries = self._rel_auto_tries.get(mid, 0) + 1
            self._rel_auto_tries[mid] = tries
            if tries < _REL_AUTO_DL_TRIES:
                logger.info("releases: auto-download could not reach the queue, tries again")
                twins = self.__dict__.get("_rel_auto_twins", {})
                if mid in twins:
                    self._releases.atmos_wait({mid: self._releases_today()}, twins[mid])
                else:
                    self._releases.set_dl_state(mid, DL_WAITING, video=video)
                return
            logger.warning("releases: auto-download gave up after %d tries", tries)
            self._rel_auto_videos.discard(mid)
        self._releases.set_dl_state(mid, 0, video=video)
        self.releasesChanged.emit()

    # ----- the feed the pane reads ------------------------------------------

    @Slot(int)
    def loadReleases(self, months: int = 0) -> None:
        """Build the feed on a worker: the releases of the last ``months``
        months (the whole window when 0, never past it), dressed with their
        library and ownership answers like any album card. The pane asks for
        a few months and asks for more as it is scrolled, so a year of
        releases is never laid out at once."""
        gen = self._rel_gen
        months = max(0, int(months or 0))

        def work() -> None:
            more = False
            try:
                window = self._releases_keep_from()
                floor = window
                if months:
                    floor = max(window, self._releases_keep_from(months))
                shown = {"videos": self._releases_videos_on(), "guests": self._releases_featured_on()}
                rows = self._releases.feed(floor, **shown)
                more = floor > window and self._releases.has_older(floor, window, **shown)
                dress = getattr(self, "_dress_card", None)
                if dress is not None:
                    rows = [dress(r) for r in rows]
            except Exception:
                logger.debug("releases: feed build failed", exc_info=True)
                rows = []
            if gen == self._rel_gen:
                self.releasesLoaded.emit(rows, more, months)

        self.threadpool.start(Worker(work))

    @Slot(str)
    @Slot(str, str)
    def searchReleases(self, needle: str, kind: str = "") -> None:
        """The feed's search box: every stored release whose title or artist
        holds the words, past the window too (browsing stops at the window, a
        search does not), dressed like the feed, under the feed's filter
        ``kind`` (so the limit counts what the page shows). A newer search
        drops an older answer still in flight; the needle travels with the
        answer so the pane can drop one for text it no longer shows."""
        raw = str(needle or "")
        text = " ".join(raw.split())[:99]
        kind = str(kind or "")
        self._rel_find_gen = getattr(self, "_rel_find_gen", 0) + 1
        find_gen = self._rel_find_gen
        gen = self._rel_gen
        if not text:
            self._releases.search_forget()
            self.releasesSearched.emit(raw, [], False)
            return

        def work() -> None:
            more = False
            try:
                rows, more = self._releases.search(
                    text,
                    _REL_FIND_LIMIT,
                    videos=self._releases_videos_on(),
                    guests=self._releases_featured_on(),
                    kind=kind,
                    today=_dt.date.today().isoformat(),
                )
                dress = getattr(self, "_dress_card", None)
                if dress is not None:
                    rows = [dress(r) for r in rows]
            except Exception:
                logger.debug("releases: the feed search failed", exc_info=True)
                rows = []
            if gen != self._rel_gen or find_gen != self._rel_find_gen:
                return
            logger.info("release search: %d hit(s) for %s", len(rows), diagnostics.content(text))
            self.releasesSearched.emit(raw, rows, more)

        self.threadpool.start(Worker(work))

    @Slot(result=int)
    def releasesUnseenCount(self) -> int:
        try:
            return self._releases.unseen_count(
                self._releases_keep_from(), videos=self._releases_videos_on(), guests=self._releases_featured_on()
            )
        except Exception:
            return 0

    @Slot(result="QVariant")
    def acknowledgeAllReleases(self) -> list:
        """The pane opened: every unseen release is now seen. Returns their
        ids so the pane can keep their dots for this visit."""
        ids = self._releases.acknowledge_all()
        if ids:
            self.releasesChanged.emit()
        return ids

    @Slot(result=int)
    def followedCount(self) -> int:
        try:
            return self._releases.followed_count()
        except Exception:
            return 0

    @Slot(result=int)
    def releasesUnchecked(self) -> int:
        """Followed artists the sweep has not checked yet, or not since the
        last Check now (the burst is running while this is above zero)."""
        try:
            return self._releases.due_count(self._releases_due_from())
        except Exception:
            return 0

    @Slot(result=int)
    def releasesCheckedThisRound(self) -> int:
        return int(getattr(self, "_rel_round_done", 0))

    # ----- follow / unfollow ------------------------------------------------

    @Slot(str, result=bool)
    def isFollowed(self, artist_id: str) -> bool:
        return self._releases.is_followed(str(artist_id))

    @Slot(str, result="QVariant")
    def followInfo(self, artist_id: str) -> dict:
        return self._releases.follow_info(str(artist_id)) or {}

    @Slot(str, str, str)
    def followArtist(self, artist_id: str, name: str, art: str) -> None:
        """Follow by hand (the artist page, the search dialog). The pacer is
        re-armed at once so the new follow's first catalog lands now rather
        than at the next beat."""
        aid = str(artist_id or "")
        if not aid:
            return
        self._releases.follow(aid, name or "", art or "", origin=ORIGIN_MANUAL, touched=True)
        logger.info("followed %s", diagnostics.content(str(name or "")))
        self._releases_followed_forget()
        self.followStateChanged.emit(aid, True)
        self.releasesChanged.emit()
        self._releases_mirror_to_tidal(aid, True)
        if not getattr(self, "_rel_step_running", False):
            self._releases_rearm(0)

    @Slot(str)
    def unfollowArtist(self, artist_id: str) -> None:
        aid = str(artist_id or "")
        if not aid:
            return
        info = self._releases.follow_info(aid) or {}
        remember = info.get("origin") == ORIGIN_TIDAL or bool(info.get("tidal_fav"))
        self._releases.unfollow(aid, remember_tidal=remember)
        logger.info("unfollowed %s", diagnostics.content(str(info.get("name") or "")))
        self._releases_followed_forget()
        self.followStateChanged.emit(aid, False)
        self.releasesChanged.emit()
        self._releases_mirror_to_tidal(aid, False, was_fav=bool(info.get("tidal_fav")))

    @Slot()
    def loadFollowed(self) -> None:
        """The followed artists for the Following page, each with how much of
        them the library already holds (the IN LIBRARY bar) and its releases
        for the shelves and the timeline (see ``FollowStore.followed_artists``).
        Only the Ledger draws the timeline and only the Shelves every release:
        in the other shapes each row's ``career`` goes out empty and its
        ``releases`` hold just the latest, so a reload (every couple of
        seconds through a sweep) does not carry every follow's history
        across. The MusicBrainz marks (``missing``) go out only with the
        setting on, and only for the Ledger."""
        gen = self._rel_gen
        view = str(getattr(self, "_waves_prefs", {}).get("releases_follow_view") or "")
        marks = view == "ledger" and self._waves_pref_bool("releases_musicbrainz")
        videos = self._releases_videos_on()
        guests = self._releases_featured_on()
        self._rel_followed_seq = seq = getattr(self, "_rel_followed_seq", 0) + 1

        def work() -> None:
            try:
                rows = self._releases.followed_artists(
                    _dt.date.today().isoformat(), videos=videos, guests=guests, marks=marks
                )
                for r in rows:
                    if view != "ledger":
                        r["career"] = []
                    if view != "shelves":
                        r["releases"] = r["releases"][:1]
                dress = getattr(self, "_dress_library_row", None)
                if dress is not None:
                    rows = [dress("artists", r) for r in rows]
            except Exception:
                logger.debug("releases: followed list failed", exc_info=True)
                rows = []
            if gen == self._rel_gen and self._releases_followed_fresh(gen, seq, view, rows):
                self.followedLoaded.emit(rows)

        self.threadpool.start(Worker(work))

    def _releases_followed_forget(self) -> None:
        """(GUI thread) The page changes its followed list on its own (an
        unfollow drops the row at once), so the next list read is handed
        over even if it matches the last one sent."""
        with self.__dict__.setdefault("_rel_followed_lock", threading.Lock()):
            last = getattr(self, "_rel_followed_last", (None, 0, None))
            self._rel_followed_last = (last[0], last[1], None)

    def _releases_followed_fresh(self, gen: int, seq: int, view: str, rows: list) -> bool:
        """(worker) Whether a followed list just read is worth handing to the
        page: not one asked for before a newer load already landed, not one
        for a shape the page no longer shows (its own load follows), and not
        the very rows the page already holds. Every sweep step reloads the
        list, and the page spends tens of milliseconds taking 900 rows in,
        so an unchanged list is dropped here, off the GUI thread. When each
        follow was last checked is left out: every step stamps it, and the
        page never shows it."""
        if str(getattr(self, "_waves_prefs", {}).get("releases_follow_view") or "") != view:
            return False
        try:
            shown = [{k: v for k, v in r.items() if k not in _FOLLOW_STAMPS} for r in rows]
            sig = hashlib.blake2b(json.dumps(shown, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            sig = None
        with self.__dict__.setdefault("_rel_followed_lock", threading.Lock()):
            last = getattr(self, "_rel_followed_last", (None, 0, None))
            if last[0] == gen and (seq < last[1] or (sig is not None and sig == last[2])):
                return False
            self._rel_followed_last = (gen, seq, sig)
        return True

    # ----- follow by search ---------------------------------------------------

    @Slot(str)
    def searchArtists(self, needle: str) -> None:
        """The follow dialog's search: artists only, one request, each row
        saying whether it is already followed. A newer search drops an older
        answer still in flight; the needle travels with the answer so the
        dialog can drop one for text it no longer shows."""
        raw = str(needle or "")
        text = " ".join(raw.split())[:99]
        self._rel_search_gen = getattr(self, "_rel_search_gen", 0) + 1
        gen = self._rel_search_gen
        if not text:
            self.artistSearchResults.emit(raw, [], True)
            return
        if not getattr(self, "_logged_in", False):
            self.artistSearchResults.emit(raw, [], False)
            return

        def work() -> None:
            try:
                found = self.tidal.session.search(text, models=[Artist], limit=_REL_SEARCH_LIMIT)
                rows = []
                for artist in list((found or {}).get("artists") or [])[:_REL_SEARCH_LIMIT]:
                    row = self._fav_artist_dict(artist)
                    if not row.get("id"):
                        continue
                    row["followed"] = self._releases.is_followed(str(row["id"]))
                    rows.append(row)
            except Exception:
                logger.warning("releases: the artist search failed", exc_info=True)
                if gen == self._rel_search_gen:
                    self.artistSearchResults.emit(raw, [], False)
                return
            if gen != self._rel_search_gen:
                return
            logger.info("follow search: %d artist(s) for %s", len(rows), diagnostics.content(text))
            self.artistSearchResults.emit(raw, rows, True)

        self.threadpool.start(Worker(work))

    # ----- TIDAL favorites: read -------------------------------------------

    def _releases_mirror_walk(self, gen: int) -> None:
        """(worker, on the sweep's pool) Read the account's favorite artists
        into the follow list, a page a second. A walk that reached the end is
        a full reconcile (a favorite dropped on TIDAL drops its untouched
        mirrored follow); one cut short, by a failed page or the page cap, is
        partial and only adds. An empty answer is never taken as "no
        favorites": one glitch must not unfollow a whole mirrored list."""
        added = removed = 0
        limited = False
        try:
            last = int(self._releases.meta_get("last_walk", "0") or 0)
        except (TypeError, ValueError):
            last = 0
        if last and time.time() - last < _REL_WALK_MIN_GAP_S:
            # Recent, and a sign-out clears it: this account's own walk.
            if gen == self._rel_gen:
                self._rel_fav_known_gen = gen
                self._relMirrorDone.emit(0, 0, False)
            return
        try:
            walked = self._releases_walk_pages(gen)
            if walked is None:
                return  # signed out or reset mid-walk
            rows, complete, limited = walked
            if gen != self._rel_gen:
                # Signed out (or reset) between the last page and here: these
                # favorites are the account that left, so nothing is stored,
                # stamped or counted for the one that signs in next.
                return
            added, removed = self._releases_mirror_store(gen, rows, complete, limited)
        except Exception:
            logger.debug("releases: the favorites walk failed", exc_info=True)
            self._releases_walk_failed(gen)
        if gen == self._rel_gen:
            self._relMirrorDone.emit(added, removed, limited)

    def _releases_mirror_store(self, gen: int, rows: list, complete: bool, limited: bool) -> tuple[int, int]:
        """(worker) Store a finished walk whose generation is still current:
        reconcile the follow list, mark the favorites known, and stamp the
        walk (or count the failure) so the idle sweep paces the next one."""
        added = removed = 0
        if rows:
            added, removed = self._releases.sync_tidal_favorites(rows, partial=not complete)
        if complete:
            self._rel_fav_known_gen = gen
        if not limited and (complete or rows):
            self._releases.meta_set("last_walk", str(int(time.time())))
            self._rel_walk_fails = 0
        else:
            self._releases_walk_failed(gen)
        return added, removed

    def _releases_walk_failed(self, gen: int) -> None:
        """(worker) A walk read nothing, met a rate limit or broke, so it is
        not a walk: the idle sweep tries again (after the sweep's own pause,
        see _releases_walk_retry_due). One from a past session counts for
        nothing."""
        if gen == self._rel_gen:
            self._rel_walk_fails = getattr(self, "_rel_walk_fails", 0) + 1

    def _releases_walk_retry_due(self, now: float) -> bool:
        """(GUI thread) The last walk read nothing and its retry pause is
        over (see _REL_WALK_RETRY_S)."""
        fails = getattr(self, "_rel_walk_fails", 0)
        if not fails or getattr(self, "_rel_fav_known_gen", -1) == self._rel_gen:
            return False
        wait = _REL_WALK_RETRY_S[min(fails, len(_REL_WALK_RETRY_S)) - 1]
        return now - getattr(self, "_rel_walk_at", 0.0) >= wait

    def _releases_walk_pages(self, gen: int):
        """(worker) The favorite artists, page by page, spaced a second
        apart: ``(rows, complete, rate_limited)``, or None once the
        generation moved on. Only an empty page marks the end: "complete"
        lets the full reconcile unfollow, and a glitched short page mid-list
        would read everyone past it as unfavorited. So a short page just
        moves the offset on, and the real end costs one extra, empty
        answer."""
        favs = self.tidal.session.user.favorites
        rows: list = []
        offset = 0
        for page in range(_REL_WALK_MAX_PAGES):
            if gen != self._rel_gen:
                return None
            if page:
                time.sleep(_REL_WALK_PAGE_GAP_S)
            try:
                raw = list(favs.artists(limit=_REL_WALK_PAGE, offset=offset) or [])
            except Exception as exc:
                logger.debug("releases: a favorites page failed", exc_info=True)
                return rows, False, _is_rate_limit(exc)
            offset += len(raw)
            rows.extend(r for r in (self._fav_artist_dict(a) for a in raw) if r.get("id"))
            if not raw:
                return (rows, True, False) if gen == self._rel_gen else None
        return (rows, False, False) if gen == self._rel_gen else None

    def _releases_note_favorites(self, rows: list) -> None:
        """(any thread) A page of the My Tidal artists tab just loaded: its
        favorites join the follow list now, without waiting for the next
        walk. Adds and marks only; removal is the full walk's call."""
        try:
            added, _ = self._releases.sync_tidal_favorites(list(rows or []), partial=True)
        except Exception:
            logger.debug("releases: noting favorites failed", exc_info=True)
            return
        if added:
            self.releasesChanged.emit()

    # ----- TIDAL favorites: write ------------------------------------------

    def _releases_mirror_to_tidal(self, artist_id: str, follow: bool, *, was_fav: bool | None = None) -> None:
        """A single hand follow or unfollow, sent on to TIDAL when the mirror
        pref is on. ``was_fav`` is whether TIDAL holds the artist already
        (read before an unfollow erased the row)."""
        if not self._waves_pref_bool("follow_mirror_tidal"):
            return
        self._releases_tidal_enqueue(artist_id, follow, was_fav=was_fav)

    def _releases_tidal_enqueue(
        self, artist_id: str, want: bool, *, was_fav: bool | None = None, hand: bool = False
    ) -> None:
        """(GUI thread) Ask for one artist's favorite state. The last ask per
        artist wins, and an ask that matches what TIDAL already holds sends
        nothing (a follow then unfollow inside the settle never leaves).
        What TIDAL holds is only trusted once this account's favorites were
        walked: until then the flags may be a previous account's, and the ask
        goes out. ``hand`` marks an ask the mirror pref does not own."""
        aid = str(artist_id or "")
        if not aid or not getattr(self, "_logged_in", False):
            return
        self._releases_tidal_check_gen()
        if was_fav is None:
            was_fav = bool((self._releases.follow_info(aid) or {}).get("tidal_fav"))
        # Judged against what TIDAL held before the first ask still waiting:
        # an unfollow then a re-follow inside the settle sends nothing.
        if aid not in self._rel_tidal_out:
            self._rel_tidal_base[aid] = bool(was_fav)
        base = self._rel_tidal_base.get(aid, bool(was_fav))
        known = getattr(self, "_rel_fav_known_gen", -1) == self._rel_gen
        if known and aid not in self._rel_tidal_inflight and bool(want) == base:
            self._rel_tidal_out.pop(aid, None)
            self._rel_tidal_base.pop(aid, None)
            self._rel_tidal_hand.discard(aid)
            return
        self._rel_tidal_out.pop(aid, None)
        self._rel_tidal_out[aid] = bool(want)
        if hand:
            self._rel_tidal_hand.add(aid)
        else:
            self._rel_tidal_hand.discard(aid)
        self._releases_tidal_arm(_REL_TIDAL_SETTLE_MS)

    def _releases_tidal_check_gen(self) -> None:
        """A sign-out or reset since the last ask: what was pending (and a
        request whose answer will be dropped) belonged to that session."""
        if self._rel_tidal_gen != self._rel_gen:
            self._rel_tidal_out = {}
            self._rel_tidal_inflight = set()
            self._rel_tidal_hand = set()
            self._rel_tidal_base = {}
            self._rel_tidal_gen = self._rel_gen

    def _releases_tidal_arm(self, at_least_ms: int) -> None:
        """Arm the drain no sooner than ``at_least_ms`` and never inside the
        spacing after the last request."""
        if self._rel_tidal_inflight:
            return  # the landing re-arms
        since = (time.monotonic() - self._rel_tidal_last) * 1000
        ms = max(int(at_least_ms), int(_REL_TIDAL_GAP_MS - since))
        with contextlib.suppress(Exception):
            if self._rel_tidal_timer.isActive():
                return
            self._rel_tidal_timer.start(max(0, ms))

    def _releases_tidal_drain(self) -> None:
        """(GUI thread) Send the next favorites request: every pending follow
        (up to the batch) in one, else one unfollow."""
        self._releases_tidal_check_gen()
        if not getattr(self, "_logged_in", False):
            self._rel_tidal_out = {}
            return
        if not self._rel_tidal_out or self._rel_tidal_inflight:
            return
        now = time.monotonic()
        if now < self._rel_tidal_backoff_until:
            self._rel_tidal_timer.start(int((self._rel_tidal_backoff_until - now) * 1000) + 1)
            return
        while self._rel_tidal_sent and now - self._rel_tidal_sent[0] >= 3600:
            self._rel_tidal_sent.popleft()
        if len(self._rel_tidal_sent) >= _REL_TIDAL_PER_HOUR:
            wait = 3600 - (now - self._rel_tidal_sent[0])
            logger.info("releases: TIDAL favorites changes wait %d min (hourly limit)", int(wait // 60) + 1)
            self._rel_tidal_timer.start(int(wait * 1000) + 1)
            return
        adds = [a for a, w in self._rel_tidal_out.items() if w][:_REL_TIDAL_ADD_BATCH]
        if adds:
            ids, want = adds, True
        else:
            ids, want = [next(iter(self._rel_tidal_out))], False
        for a in ids:
            self._rel_tidal_out.pop(a, None)
            self._rel_tidal_base.pop(a, None)
        self._rel_tidal_inflight = set(ids)
        self._rel_tidal_last = now
        self._rel_tidal_sent.append(now)
        gen = self._rel_gen
        self._rel_tidal_pool.start(Worker(lambda: self._releases_tidal_write(ids, want, gen)))

    def _releases_tidal_write(self, ids: list, want: bool, gen: int) -> None:
        """(worker) One favorites request. Never retried here: a failure
        leaves the local follow as it is and says so in the log."""
        result = {"ids": list(ids), "want": bool(want), "ok": False, "limited": False}
        if gen != self._rel_gen or not getattr(self, "_logged_in", False):
            return  # asked under a session that has ended: never sent to the next one
        try:
            favs = self.tidal.session.user.favorites
            ok = favs.add_artist(list(ids)) if want else favs.remove_artist(str(ids[0]))
            result["ok"] = bool(ok)
        except Exception as exc:
            result["limited"] = _is_rate_limit(exc)
            logger.debug("releases: a favorites write failed", exc_info=True)
        if gen == self._rel_gen:
            self._relTidalDone.emit(result)

    @Slot("QVariant")
    def _on_rel_tidal_done(self, result) -> None:
        """(GUI thread) A favorites request landed: record it, then the next."""
        result = dict(result or {})
        ids = [str(i) for i in result.get("ids") or []]
        want = bool(result.get("want"))
        self._rel_tidal_inflight = set()
        # The spacing runs from the answer, not the dispatch: a request that
        # waited for the pool must not be followed by the next straight away.
        self._rel_tidal_last = time.monotonic()
        if result.get("limited"):
            self._rel_tidal_backoff_until = time.monotonic() + _REL_TIDAL_BACKOFF_S
            for a in ids:
                self._rel_tidal_out.setdefault(a, want)
            logger.warning(
                "releases: TIDAL favorites changes pause %d min after a rate limit", _REL_TIDAL_BACKOFF_S // 60
            )
        elif result.get("ok"):
            for a in ids:
                self._releases.mark_tidal_fav(a, want)
            logger.info("%d artist(s) %s TIDAL favorites", len(ids), "added to" if want else "removed from")
        else:
            logger.warning("releases: TIDAL did not take a favorites change (%d artist(s))", len(ids))
        if not result.get("limited"):
            # Settled one way or the other: no longer a pending hand ask.
            self._rel_tidal_hand.difference_update(ids)
        for a in ids:
            info = self._releases.follow_info(a) or {}
            self.followTidalChanged.emit(a, bool(info.get("tidal_fav")))
        if self._rel_tidal_out:
            self._releases_tidal_arm(0)

    @Slot(str)
    def followOnTidal(self, artist_id: str) -> None:
        """The artist page's ALSO FOLLOW ON TIDAL: one artist, by hand, through
        the same rationed writes (whatever the mirror pref says)."""
        self._releases_tidal_enqueue(artist_id, True, hand=True)

    def _releases_mirror_switched(self, on: bool) -> None:
        """(GUI thread) The mirror pref flipped. Off: the changes it queued
        and has not sent are dropped at once (ALSO FOLLOW ON TIDAL asks stay),
        so switching it off really stops writes to the account."""
        if not on:
            self._releases_tidal_check_gen()
            dropped = [a for a in self._rel_tidal_out if a not in self._rel_tidal_hand]
            for a in dropped:
                self._rel_tidal_out.pop(a, None)
                self._rel_tidal_base.pop(a, None)
            if dropped:
                logger.info("releases: mirror switched off, %d queued TIDAL change(s) dropped", len(dropped))
            for a in dropped:
                info = self._releases.follow_info(a) or {}
                self.followTidalChanged.emit(a, bool(info.get("tidal_fav")))

    @Slot(str, result=bool)
    def tidalFavouritePending(self, artist_id: str) -> bool:
        """A favorites change for this artist is queued or in flight."""
        aid = str(artist_id or "")
        return aid in self._rel_tidal_out or aid in self._rel_tidal_inflight

    @Slot(result=bool)
    def followMirrorsTidal(self) -> bool:
        return self._waves_pref_bool("follow_mirror_tidal")

    # ----- the library import -----------------------------------------------

    def _releases_library_cache(self):
        """The scan cache behind the published library index, or None while
        the library is off or not scanned yet."""
        cache = getattr(getattr(self, "_library_index", None), "cache", None)
        return cache if hasattr(cache, "distinct_artists") else None

    @Slot()
    def startArtistImport(self) -> None:
        """Read every artist out of the library and settle each on a TIDAL
        artist, for the import dialog's review. One run at a time; the sweep
        waits until it ends."""
        self._releases_import_begin(only_new=False, auto=False)

    @Slot()
    def startArtistImportMore(self) -> None:
        """The same review, over only the artists the library gained since
        the last import (the Releases tab's "more artists" offer)."""
        self._releases_import_begin(only_new=True, auto=False)

    @Slot("QVariant")
    def startArtistImportNames(self, names) -> None:
        """The same review, over the named library artists only, whatever
        the track floor says: the Excluded page's way to follow one of the
        artists the import left out."""
        if hasattr(names, "toVariant"):
            names = names.toVariant()
        clean = [str(n) for n in list(names or []) if str(n or "").strip()]
        if clean:
            self._releases_import_begin(only_new=False, auto=False, names=clean)

    def _releases_import_begin(self, *, only_new: bool, auto: bool, names: list | None = None) -> bool:
        """Start a run. ``auto`` is the automatic import: it follows
        what it matches by itself, reports nothing to the dialog, and gives
        way to a run the user starts by hand. ``names`` narrows the run to
        those library artists, floor or no floor."""
        running = bool(getattr(self, "_rel_import_running", False))
        if running and (auto or not getattr(self, "_rel_import_auto", False)):
            return False
        if not getattr(self, "_logged_in", False):
            if not auto:
                self.artistImportError.emit("not-signed-in")
            return False
        if auto and time.monotonic() < self._rel_backoff_until:
            return False
        cache = self._releases_library_cache()
        scanning = bool(getattr(self, "_library_index_building", False))
        if cache is None:
            if not auto:
                # Before a first scan has saved anything, say it is coming.
                self.artistImportError.emit("library-scanning" if scanning else "no-library")
            return False
        if running:
            # A hand run outranks the automatic one, whose answer is then
            # discarded on arrival; its artists stay unseen for the next.
            # Only once this run can really start: set aside for nothing,
            # the automatic run would leave the sweep waiting for good.
            self._rel_import_gen += 1
            logger.info("library import (automatic) set aside for one started by hand")
        self._rel_import_running = True
        self._rel_import_auto = auto
        self._rel_import_gen = getattr(self, "_rel_import_gen", 0) + 1
        gen, igen = self._rel_gen, self._rel_import_gen
        opts = {"only_new": only_new, "auto": auto, "scanning": scanning, "names": list(names or [])}
        logger.info("library import started%s", " (automatic)" if auto else "")
        self._rel_pool.start(Worker(lambda: self._releases_import_scan(cache, gen, igen, opts)))
        return True

    def _releases_min_tracks(self) -> int:
        """The import's floor: an artist with fewer tracks in the library than
        this is not followed (``releases_import_min_tracks``, 5 by default).
        A stray single or a guest spot is no reason to follow anyone."""
        raw = self._waves_prefs.get("releases_import_min_tracks", _REL_IMPORT_MIN_TRACKS)
        try:
            n = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            n = _REL_IMPORT_MIN_TRACKS
        return max(1, min(_REL_IMPORT_MIN_TRACKS_MAX, n))

    def _releases_enough_tracks(self, row: dict, floor: int) -> bool:
        """Whether a library artist clears the import's floor; a row that
        does not say how many tracks it has is never held back."""
        tracks = row.get("tracks")
        if tracks is None:
            return True
        try:
            return int(tracks) >= floor
        except (TypeError, ValueError):
            return True

    def _releases_import_floor_changed(self) -> None:
        """The track floor moved: the library is looked at again, so the
        artists it now lets through are imported, or counted for the tab's
        offer, without waiting for the next scan."""
        self._rel_lib_stamp_seen = -1
        self._releases_library_moved()
        self.followFloorChanged.emit()

    def _releases_thin_follows(self) -> list:
        """(GUI thread) The artists followed from the library that the floor
        would leave out today: the library holds fewer of their tracks than
        it asks. Only library follows: hand follows and TIDAL favorites are
        the user's own, whatever the library holds. Reads the scan cache and
        the follow list, both quick."""
        cache = self._releases_library_cache()
        if cache is None:
            return []
        floor = self._releases_min_tracks()
        try:
            rows = list(cache.distinct_artists())
        except Exception:
            logger.debug("releases: the library could not be read for the floor", exc_info=True)
            return []
        tracks: dict = {}
        for r in rows:
            key = str(r.get("key") or "") or artist_import.name_key(str(r.get("name") or ""))
            if key:
                tracks[key] = max(tracks.get(key, 0), int(r.get("tracks") or 0))
        out = []
        for f in self._releases.follows():
            # A TIDAL favorite stays too: dropped here, the next walk would
            # bring it back with a fresh baseline.
            if f.get("origin") != ORIGIN_LIBRARY or f.get("tidal_fav"):
                continue
            key = artist_import.name_key(str(f.get("name") or ""))
            if key in tracks and tracks[key] < floor:
                out.append(f)
        return out

    @Slot(result=int)
    def followsUnderFloor(self) -> int:
        """How many library follows the floor would leave out today (the
        Library card's offer to unfollow them)."""
        return len(self._releases_thin_follows())

    @Slot()
    def loadExcluded(self) -> None:
        """The library artists the track floor leaves out and nobody
        follows, for the Excluded page: ``[{name, key, albums, tracks, held,
        covers, best, span}]`` by name, through ``excludedLoaded``. ``held``
        is what the library has of them (see ``_releases_held``), so each
        one is known by its covers and titles. Read on a worker: a big
        library's artist list is not a GUI-thread read."""
        cache = self._releases_library_cache()
        if cache is None:
            self.excludedLoaded.emit([])
            return
        # One run at a time: a follow, the floor or an import landing while
        # one is out marks it stale, and it runs once more when it ends.
        flags = self.__dict__.setdefault("_rel_ex_flags", {"lock": threading.Lock(), "busy": False, "again": False})
        with flags["lock"]:
            if flags["busy"]:
                flags["again"] = True
                return
            flags["busy"] = True

        def work() -> None:
            try:
                while True:
                    self._releases_excluded_run()
                    with flags["lock"]:
                        if not flags["again"]:
                            flags["busy"] = False
                            return
                        flags["again"] = False
            except BaseException:
                with flags["lock"]:
                    flags["busy"] = flags["again"] = False
                raise

        self.threadpool.start(Worker(work))

    def _releases_excluded_run(self) -> None:
        """(worker) One Excluded page read. The rows go out first with the
        covers already known, then (when some album folders were never
        listed, a first open over a network share) once more with every
        cover, so the page is never held back by the listings."""
        cache = self._releases_library_cache()
        if cache is None:
            self.excludedLoaded.emit([])
            return
        gen = self._rel_gen
        try:
            rows, albums = self._releases_excluded_rows(cache)
            unlisted = False
            for r in rows:
                dressed = self._releases_held(albums.get(r["key"]) or [], look=False)
                unlisted = unlisted or dressed.pop("unlisted")
                r.update(dressed)
        except Exception:
            logger.debug("releases: the library could not be read for the excluded list", exc_info=True)
            rows, albums, unlisted = [], {}, False
        if gen != self._rel_gen:
            return
        # Copies: the cover pass below fills these rows in place.
        self.excludedLoaded.emit([dict(r) for r in rows])
        if not unlisted:
            return
        try:
            for r in rows:
                if gen != self._rel_gen or self._releases_library_cache() is not cache:
                    return
                dressed = self._releases_held(albums.get(r["key"]) or [], look=True)
                dressed.pop("unlisted")
                r.update(dressed)
        except Exception:
            logger.debug("releases: the excluded covers could not be read", exc_info=True)
            return
        if gen == self._rel_gen:
            self.excludedLoaded.emit(rows)

    def _releases_excluded_rows(self, cache) -> tuple[list, dict]:
        """(worker) The library artists under the floor nobody follows, by
        name, and their albums as the index holds them (by artist key)."""
        floor = self._releases_min_tracks()
        followed = {artist_import.name_key(str(f.get("name") or "")) for f in self._releases.follows()}
        rows = []
        for r in cache.distinct_artists():
            key = str(r.get("key") or "") or artist_import.name_key(str(r.get("name") or ""))
            if not key or self._releases_enough_tracks(r, floor):
                continue
            if artist_import.name_key(str(r.get("name") or "")) in followed:
                continue
            rows.append(
                {
                    "name": str(r.get("name") or ""),
                    "key": key,
                    "albums": int(r.get("albums") or 0),
                    "tracks": int(r.get("tracks") or 0),
                }
            )
        rows.sort(key=lambda r: (r["name"].casefold(), r["name"]))
        reader = getattr(cache, "artist_albums", None)
        return rows, (reader([r["key"] for r in rows]) if reader is not None else {})

    def _releases_held(self, albums: list[dict], *, look: bool = True) -> dict:
        """An excluded artist's library albums, dressed for the page:
        ``held`` (up to ``_REL_HELD_CAP``, newest first: title, year, tracks,
        a quality label and the folder's own cover as a file URL, "" when it
        has none), ``covers`` (the first four found), ``best`` (the label of
        the best copy) and ``span`` (the years, "2019" or "2012-2021").
        With ``look`` off a folder never listed yet reads as no cover, and
        ``unlisted`` says whether any was."""
        held, ranked = [], []
        unlisted = False
        for a in albums[:_REL_HELD_CAP]:
            label = _quality_label(a.get("codec"), a.get("bits"), a.get("rate"), a.get("bitrate"))
            folder, mtime = str(a.get("folder") or ""), float(a.get("mtime") or 0)
            cover = self._releases_folder_cover(folder, mtime, look=look)
            if cover is None:
                unlisted, cover = True, ""
            held.append(
                {
                    "title": str(a.get("title") or ""),
                    "year": str(a.get("year") or ""),
                    "tracks": int(a.get("tracks") or 0),
                    "q": label,
                    "cover": cover,
                }
            )
            ranked.append(((int(a.get("bits") or 0), int(a.get("rate") or 0), int(a.get("bitrate") or 0)), label))
        years = sorted(str(a.get("year") or "") for a in albums if a.get("year"))
        span = "" if not years else years[0] if years[0] == years[-1] else f"{years[0]}\u2013{years[-1]}"
        return {
            "held": held,
            "covers": [h["cover"] for h in held if h["cover"]][:4],
            "best": max(ranked)[1] if ranked else "",
            "span": span,
            "unlisted": unlisted,
        }

    def _releases_folder_cover(self, folder: str, mtime: float, *, look: bool = True) -> str | None:
        """The cover image an album folder carries (cover.jpg, folder.jpg,
        front.jpg, any case, jpg or png) as a file URL, or "". One listing
        per folder, remembered until the folder changes, so reopening the
        page does not walk a network share again. Only folders the library
        index holds ever reach here. With ``look`` off, a folder not listed
        yet (or changed since) answers None instead of being listed."""
        if not folder:
            return ""
        memo = self.__dict__.setdefault("_rel_cover_memo", {})
        hit = memo.get(folder)
        if hit is not None and hit[0] == mtime:
            return hit[1]
        if not look:
            return None
        found = ""
        try:
            with os.scandir(folder) as it:
                names = {e.name.lower(): e.name for e in it if e.is_file()}
            for stem in _REL_COVER_NAMES:
                if stem in names:
                    found = pathlib.Path(folder, names[stem]).as_uri()
                    break
        except (OSError, ValueError):
            found = ""
        memo[folder] = (mtime, found)
        return found

    @Slot(result=int)
    def unfollowUnderFloor(self) -> int:
        """Unfollow the library follows under the floor, in one go: an import
        run again with today's floor would not have followed them. Never a
        TIDAL write (the import made none for them) and never a hand follow.
        Returns how many were unfollowed."""
        thin = self._releases_thin_follows()
        for f in thin:
            aid = str(f.get("artist_id") or "")
            self._releases.unfollow(aid)
            self._releases_followed_forget()
            self.followStateChanged.emit(aid, False)
        if thin:
            logger.info("releases: %d library follow(s) under the track floor unfollowed", len(thin))
            self.releasesChanged.emit()
        self.followFloorChanged.emit()
        return len(thin)

    @Slot()
    def cancelArtistImport(self) -> None:
        """Drop a run in flight (its answer is discarded on arrival)."""
        if not getattr(self, "_rel_import_running", False):
            return
        self._rel_import_gen += 1
        self._rel_import_running = False
        logger.info("library import canceled")
        if not self._rel_step_running:
            self._releases_rearm(0)
        # A scan that ended meanwhile is looked at now.
        self._releases_library_moved()

    def _releases_import_alive(self, gen: int, igen: int) -> bool:
        return gen == self._rel_gen and igen == self._rel_import_gen

    def _releases_import_scan(self, cache, gen: int, igen: int, opts: dict | None = None) -> None:
        """(worker, on the sweep's pool) The two passes: the id tags in the
        files, then a paced name search for the rest. Posts the outcome to
        the GUI thread; a canceled or superseded run posts nothing. Anything
        unexpected still posts, as a failure: the sweep waits for this run
        to end, and a run that never reports would park it for good."""
        try:
            self._releases_import_run(cache, gen, igen, opts or {})
        except Exception:
            logger.warning("library import: the library could not be read", exc_info=True)
            auto = bool((opts or {}).get("auto"))
            self._relImportDone.emit({"gen": gen, "igen": igen, "error": "scan-failed", "auto": auto})

    def _releases_import_run(self, cache, gen: int, igen: int, opts: dict) -> None:
        auto = bool(opts.get("auto"))
        result: dict = {"gen": gen, "igen": igen, "error": "", "auto": auto, "scanning": bool(opts.get("scanning"))}
        rows = list(cache.distinct_artists())
        result["artists"] = len(rows)
        # Under the track floor: left out of this run and not remembered as
        # looked at, so a lower floor (or more of their tracks) brings them.
        # A run over named artists (the Excluded page) is the user asking
        # for exactly those, so the floor does not apply.
        thin = 0
        wanted = {artist_import.name_key(str(n)) for n in opts.get("names") or []}
        if wanted:
            rows = [r for r in rows if artist_import.name_key(str(r.get("name") or "")) in wanted]
        else:
            floor = self._releases_min_tracks()
            thin = sum(1 for r in rows if not self._releases_enough_tracks(r, floor))
            rows = [r for r in rows if self._releases_enough_tracks(r, floor)]
        if opts.get("only_new"):
            seen = self._releases_seen_norms()
            rows = [r for r in rows if artist_import.name_key(str(r.get("name") or "")) not in seen]
        # What this run looks at, remembered once it lands (never on a
        # cancel or a rate limit, so those artists come round again).
        result["norms"] = sorted({n for n in (artist_import.name_key(str(r.get("name") or "")) for r in rows) if n})
        followed = self._releases.follows()
        followed_ids = {str(r.get("artist_id") or "") for r in followed}
        followed_norms = {artist_import.name_key(str(r.get("name") or "")) for r in followed}
        # Named artists are the user asking for exactly those, even one they
        # once set aside with FORGET.
        dismissed = set() if wanted else self._releases.tray_dismissed_norms()

        def alive() -> bool:
            # The library folder changed or was turned off: its files are
            # no longer Waves' to read (the two-folder rule).
            return self._releases_import_alive(gen, igen) and self._releases_library_cache() is cache

        tagged = self._releases_import_tags(
            cache, rows, followed_ids, followed_norms | dismissed, alive, report=not auto
        )
        if tagged is None:
            if self._releases_import_alive(gen, igen):
                self._relImportDone.emit({**result, "error": "library-changed"})
            return
        matched, rest, skipped = tagged
        skipped += thin
        limited = [False]
        wait = self._releases_import_pacer()
        out = artist_import.resolve(
            rest,
            search=self._releases_import_searcher(limited, wait),
            library=self._releases_import_library(cache),
            catalog=self._releases_import_catalog(limited, wait),
            followed_ids=followed_ids,
            followed_names=[r.get("name") or "" for r in followed],
            dismissed_norms=dismissed,
            progress=lambda done, total: (
                self.artistImportProgress.emit(done, total, "scan") if not auto and alive() else None
            ),
            stop=lambda: limited[0] or not alive(),
        )
        if not alive():
            if self._releases_import_alive(gen, igen):
                # Superseded by a library change, not by a cancel: still post
                # (as a failure), the sweep waits for this run to end.
                self._relImportDone.emit({**result, "error": "library-changed"})
            return
        if limited[0] or out.get("offline"):
            # Nothing is remembered as looked at: these artists come round again.
            result["error"] = "rate-limited" if limited[0] else "offline"
            self._relImportDone.emit(result)
            return
        # A name whose search failed was never looked at.
        failed = {
            str(r.get("norm") or artist_import.name_key(str(r.get("name") or ""))) for r in out.get("failed") or []
        }
        if failed:
            result["norms"] = [n for n in result["norms"] if n not in failed]
        result.update(
            self._releases_import_payload(matched + out["matched"], out, skipped + len(out["skipped"]), tray=not wanted)
        )
        result["tagged"] = len(matched)
        self._relImportDone.emit(result)

    def _releases_import_tags(
        self, cache, rows: list, followed_ids: set, skip_norms: set, alive, *, report: bool = True
    ):
        """(worker) The tag pass: an artist whose album files carry exactly
        one TIDAL album-artist id is matched by it, no request needed.
        Returns ``(matched, rest, skipped)``, or None once the run is dead."""
        matched: list = []
        rest: list = []
        skipped = 0
        total = len(rows)
        for i, row in enumerate(rows, start=1):
            if not alive():
                return None
            name = str(row.get("name") or "")
            norm = artist_import.name_key(name)
            if not norm or norm in skip_norms:
                skipped += 1
            else:
                aid = _tagged_artist_id(cache, row.get("folders") or [])
                if not aid:
                    rest.append(
                        {
                            "name": name,
                            "albums": int(row.get("albums") or 0),
                            "norm": norm,
                            "key": str(row.get("key") or ""),
                        }
                    )
                elif aid in followed_ids:
                    skipped += 1
                else:
                    matched.append(
                        {
                            "name": name,
                            "albums": int(row.get("albums") or 0),
                            "norm": norm,
                            "reason": "tag",
                            "artist": {"id": aid, "name": name, "art": ""},
                        }
                    )
            if report and (i % _REL_IMPORT_TAG_PROGRESS_EVERY == 0 or i == total):
                self.artistImportProgress.emit(i, total, "tags")
        return matched, rest, skipped

    def _releases_import_pacer(self):
        """(worker) One request a second across a run's name searches and
        catalog reads alike: a wait() that sleeps out the rest of the gap."""
        last = [0.0]

        def wait() -> None:
            left = _REL_IMPORT_SEARCH_GAP_S - (time.monotonic() - last[0])
            if left > 0:
                time.sleep(left)
            last[0] = time.monotonic()

        return wait

    def _releases_import_searcher(self, limited: list, wait=None):
        """(worker) The resolver's search: artists only, at most one request
        a second. A rate limit is flagged so the run stops at the next row."""
        wait = wait or self._releases_import_pacer()

        def search(name: str) -> list[dict]:
            wait()
            try:
                found = self.tidal.session.search(
                    " ".join(name.split())[:99], models=[Artist], limit=_REL_IMPORT_SEARCH_LIMIT
                )
            except Exception as exc:
                if _is_rate_limit(exc):
                    limited[0] = True
                raise
            rows = []
            for artist in list((found or {}).get("artists") or [])[:_REL_IMPORT_SEARCH_LIMIT]:
                row = self._fav_artist_dict(artist)
                if row.get("id"):
                    rows.append({"id": str(row["id"]), "name": row.get("name") or "", "art": row.get("art") or ""})
            return rows

        return search

    def _releases_import_catalog(self, limited: list, wait=None):
        """(worker) The resolver's catalog: a TIDAL artist's album titles (or
        its EP and single titles), one paced request each, for weighing
        namesakes against the library. A rate limit is flagged so the run
        stops at the next row; any other failure reads as None (unchecked)."""
        wait = wait or self._releases_import_pacer()

        def catalog(artist_id: str, singles: bool):
            artist, _failures, _limited = self._releases_artist_obj(str(artist_id))
            if artist is None:
                return None
            fn = getattr(artist, "get_ep_singles" if singles else "get_albums", None)
            if not callable(fn):
                return None
            wait()
            try:
                raw = list(fn(limit=_REL_IMPORT_CATALOG_LIMIT) or [])
            except Exception as exc:
                if _is_rate_limit(exc):
                    limited[0] = True
                raise
            return [str(getattr(a, "name", "") or "") for a in raw]

        return catalog

    def _releases_import_library(self, cache):
        """(worker) The resolver's library: what the scan holds under a row's
        artist key. A cache that cannot say (or a row with no key) answers
        empty, and the row is then judged on names alone."""
        read = getattr(cache, "artist_titles", None)

        def library(row: dict) -> dict:
            if read is None:
                return {}
            try:
                return dict(read(str(row.get("key") or "")) or {})
            except Exception:
                return {}

        return library

    def _releases_import_payload(self, matched: list, out: dict, skipped: int, *, tray: bool = True) -> dict:
        """(worker) The review payload, with the tray brought up to date:
        names still unsettled are kept for later, names now matched leave.
        A run over named artists (the Excluded page) keeps nothing for later:
        its picks are the user's own, and a name it leaves open is still on
        that page to ask again."""
        seen: set = set()
        uniq = []
        dropped = self._releases.tidal_unfollowed_ids()
        for r in matched:
            aid = str((r.get("artist") or {}).get("id") or "")
            if aid and aid not in seen:
                seen.add(aid)
                # Unfollowed here while a TIDAL favorite: offered, not ticked.
                uniq.append({**r, "unfollowed": True} if aid in dropped else r)
        unsettled = [
            {**r, "reason": r.get("reason") or bucket} for bucket in ("fuzzy", "unmatched") for r in out[bucket]
        ]
        if tray:
            self._releases.tray_record(unsettled)
        for r in uniq:
            self._releases.tray_remove(r.get("norm") or artist_import.name_key(r.get("name") or ""))
        return {
            "matched": uniq,
            "fuzzy": out["fuzzy"],
            "unmatched": out["unmatched"],
            "skipped": skipped,
        }

    @Slot("QVariant")
    def _on_rel_import_done(self, result) -> None:
        """(GUI thread) The run landed: hand the review to the dialog (or say
        why not) and let the sweep go on."""
        result = dict(result or {})
        if not self._releases_import_alive(int(result.get("gen", -1)), int(result.get("igen", -1))):
            return
        self._rel_import_running = False
        auto = bool(result.get("auto"))
        self._rel_import_auto = False
        error = str(result.get("error") or "")
        if error:
            logger.warning("library import%s stopped: %s", " (automatic)" if auto else "", error)
            if not auto:
                self.artistImportError.emit(error)
            if error == "rate-limited":
                self._releases_back_off()
                return
        elif auto:
            if self._waves_pref_bool("releases_auto_import"):
                self._releases_import_auto_settle(result)
        else:
            logger.info(
                "library import ready: %d matched (%d from tags, %d settled by the library), %d to look at,"
                " %d not found, %d skipped",
                len(result.get("matched") or []),
                int(result.get("tagged") or 0),
                sum(1 for r in result.get("matched") or [] if r.get("reason") == "library"),
                len(result.get("fuzzy") or []),
                len(result.get("unmatched") or []),
                int(result.get("skipped") or 0),
            )
            self._releases_seen_add(result.get("norms") or [])
            if self._rel_notice.get("kind") == "more":
                self._rel_more_norms = []
                self._releases_set_notice("", 0)
            self.artistImportReady.emit(
                {
                    k: result.get(k)
                    for k in ("matched", "fuzzy", "unmatched", "skipped", "tagged", "scanning", "artists")
                }
            )
        if not self._rel_step_running:
            self._releases_rearm(0)
        if error != "offline":
            # The scan may have moved on (or ended) while this run worked.
            self._releases_library_moved()

    @Slot("QVariant")
    def confirmArtistImport(self, rows) -> None:
        """Follow the review's picks, ``[{id, name, art, norm}]``, in one
        transaction. A bulk follow: it never reaches TIDAL favorites, even
        with the mirror pref on. Picked names leave the tray."""
        self._releases_confirm_picks(rows, ORIGIN_LIBRARY)

    @Slot("QVariant")
    def confirmArtistPicks(self, rows) -> None:
        """The same follow for picks the user asked for one by one (the
        Excluded page): theirs, so the track floor never offers to drop
        them again. Still a bulk follow, never a TIDAL write."""
        self._releases_confirm_picks(rows, ORIGIN_MANUAL)

    def _releases_confirm_picks(self, rows, origin: str) -> None:
        if hasattr(rows, "toVariant"):
            rows = rows.toVariant()
        clean = []
        for r in list(rows or []):
            if isinstance(r, dict) and str(r.get("id") or "").strip():
                clean.append(
                    {"id": str(r["id"]).strip(), "name": str(r.get("name") or ""), "art": str(r.get("art") or "")}
                )
                norm = str(r.get("norm") or "")
                if norm:
                    self._releases.tray_remove(norm)
        if not clean:
            return
        added = self._releases.follow_many(clean, origin=origin)
        logger.info("library import: %d artist(s) followed", added)
        self.artistImportDone.emit(added)
        self.releasesChanged.emit()
        if not self._rel_step_running and not getattr(self, "_rel_import_running", False):
            self._releases_rearm(0)

    @Slot(result="QVariant")
    def artistImportTray(self) -> list:
        """The names an import could not settle, minus any followed since
        (by search or by hand), which leave the tray here."""
        followed = {artist_import.name_key(str(r.get("name") or "")) for r in self._releases.follows()}
        out = []
        for r in self._releases.tray_rows():
            if r.get("norm") in followed:
                self._releases.tray_remove(r["norm"])
                continue
            out.append(r)
        return out

    @Slot(result=int)
    def artistImportTrayCount(self) -> int:
        return self._releases.tray_count()

    @Slot(str)
    def dismissArtistImportTray(self, norm: str) -> None:
        """Wave a name off for good: later imports skip it."""
        if norm:
            self._releases.tray_dismiss(str(norm))

    # ----- the library, after the import --------------------------------------

    def _releases_seen_norms(self) -> set:
        """The library names some import has already looked at."""
        raw = self._releases.meta_get(_REL_IMPORT_SEEN_KEY, "")
        try:
            return {str(n) for n in json.loads(raw)} if raw else set()
        except (ValueError, TypeError):
            return set()

    def _releases_seen_add(self, norms) -> None:
        norms = {str(n) for n in norms or [] if n}
        if not norms:
            return
        seen = self._releases_seen_norms()
        if norms <= seen:
            return
        self._releases.meta_set(_REL_IMPORT_SEEN_KEY, json.dumps(sorted(seen | norms)))

    def _releases_set_notice(self, kind: str, n: int) -> None:
        notice = {"kind": kind, "n": int(n)} if kind and n > 0 else {"kind": "", "n": 0}
        if notice != self._rel_notice:
            self._rel_notice = notice
            self.artistImportNotice.emit(dict(notice))

    @Slot()
    def _releases_library_moved(self) -> None:
        """(GUI thread) The library scan saved something or ended. Once per
        library publish, with nothing else of ours running: the automatic
        import (``releases_auto_import``, on by default) takes the artists found
        since the last look, a few minutes apart while the scan still runs
        and once more at its end; without it, a finished scan counts the
        artists an earlier import has not seen, for the tab's offer."""
        if not (getattr(self, "_logged_in", False) and getattr(self, "_rel_revealed", False)):
            return
        if getattr(self, "_releases", None) is None or getattr(self, "_rel_import_running", False):
            return
        cache = self._releases_library_cache()
        if cache is None:
            return
        stamp = int(getattr(self, "_library_stamp", 0) or 0)
        if stamp == self._rel_lib_stamp_seen:
            return
        scanning = bool(getattr(self, "_library_index_building", False))
        auto = self._waves_pref_bool("releases_auto_import")
        if auto and time.monotonic() < self._rel_backoff_until:
            # TIDAL asked for a pause: the automatic import searches too, so
            # it waits with the sweep and looks once a step lands again.
            self._rel_lib_look_deferred = True
            return
        if scanning and (not auto or time.monotonic() - self._rel_auto_last < _REL_AUTO_IMPORT_GAP_S):
            return
        self._rel_lib_stamp_seen = stamp
        if auto:
            self._rel_auto_last = time.monotonic()
            self._releases_import_begin(only_new=True, auto=True)
            return
        gen = self._rel_gen
        self._rel_pool.start(Worker(lambda: self._releases_more_count(cache, gen)))

    def _releases_more_count(self, cache, gen: int) -> None:
        """(worker) The library names no import has looked at, once some
        import has run: nothing to offer to someone who never imported."""
        try:
            seen = self._releases_seen_norms()
            norms: list = []
            if seen:
                skip = seen | self._releases.tray_dismissed_norms()
                skip |= {artist_import.name_key(str(r.get("name") or "")) for r in self._releases.follows()}
                floor = self._releases_min_tracks()
                rows = [r for r in cache.distinct_artists() if self._releases_enough_tracks(r, floor)]
                norms = sorted(
                    {n for n in (artist_import.name_key(str(r.get("name") or "")) for r in rows) if n} - skip
                )
        except Exception:
            logger.warning("library import: the library could not be counted", exc_info=True)
            return
        self._relMoreCounted.emit({"gen": gen, "norms": norms})

    @Slot("QVariant")
    def _on_rel_more_counted(self, result) -> None:
        result = dict(result or {})
        if int(result.get("gen", -1)) != self._rel_gen or self._waves_pref_bool("releases_auto_import"):
            return
        norms = list(result.get("norms") or [])
        self._rel_more_norms = norms
        if norms:
            logger.info("library import: %d artist(s) in the library not looked at yet", len(norms))
            self._releases_set_notice("more", len(norms))
        elif self._rel_notice.get("kind") == "more":
            self._releases_set_notice("", 0)

    def _releases_import_auto_settle(self, result: dict) -> None:
        """(GUI thread) An automatic run landed: follow what it matched (a
        tag id, or exactly one TIDAL artist of the very same name), the rest
        already waits in the tray. Like the hand import, it never reaches
        TIDAL favorites, and never brings back an artist the user unfollowed
        while it was a TIDAL favorite (that is the user's call, in the
        review)."""
        rows = []
        for r in list(result.get("matched") or []):
            artist = r.get("artist") or {}
            if r.get("unfollowed"):
                continue
            if str(artist.get("id") or "").strip():
                rows.append(
                    {
                        "id": str(artist["id"]).strip(),
                        "name": str(artist.get("name") or r.get("name") or ""),
                        "art": str(artist.get("art") or ""),
                    }
                )
        added = self._releases.follow_many(rows, origin=ORIGIN_LIBRARY) if rows else 0
        self._releases_seen_add(result.get("norms") or [])
        looks = len(result.get("fuzzy") or []) + len(result.get("unmatched") or [])
        if added or looks:
            logger.info("library import (automatic): %d artist(s) followed, %d to look at", added, looks)
        if added:
            self.artistImportDone.emit(added)
            self.releasesChanged.emit()
        if looks:
            self._releases_set_notice("look", self._releases.tray_count())

    def _releases_auto_import_switched(self, on: bool) -> None:
        """The pref flipped: on takes a look at the library right away;
        either way the tab's offer is recounted under the new rule. Off sets
        an automatic run in flight aside: it follows nothing when it lands."""
        if not on and getattr(self, "_rel_import_running", False) and getattr(self, "_rel_import_auto", False):
            self._rel_import_gen += 1
            self._rel_import_running = False
            self._rel_import_auto = False
            logger.info("library import (automatic) set aside: switched off")
            if not self._rel_step_running:
                self._releases_rearm(0)
        self._rel_lib_stamp_seen = -1
        self._rel_auto_last = 0.0
        if on and self._rel_notice.get("kind") == "more":
            self._rel_more_norms = []
            self._releases_set_notice("", 0)
        self._releases_library_moved()

    @Slot(result="QVariant")
    def artistImportNoticeNow(self) -> dict:
        return dict(self._rel_notice)

    @Slot()
    def dismissArtistImportNotice(self) -> None:
        """NOT NOW on the tab's line. The "more" artists count as looked at,
        so the same names are not offered again; the next ones are."""
        if self._rel_notice.get("kind") == "more":
            self._releases_seen_add(self._rel_more_norms)
            self._rel_more_norms = []
        self._releases_set_notice("", 0)
