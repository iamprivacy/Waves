"""A failed artist lookup must never be reported as a finished edition scan.

WHAT THIS FENCES OFF
--------------------
Clicking DOWNLOAD on an album with the best-of-both preference on runs a quiet
scan first: it asks the album's artist for every release bucket and keeps the
ones that are other editions of the same album. That scan answers the caller
with a list AND a "did I actually manage to look" flag.

The flag used to start out True and could only ever be turned off inside the
bucket loop, which never runs when the artist could not be fetched at all. The
artist fetch swallows every failure (a dropped session, a rate limit) and hands
back nothing, and an album carrying no artist id is never even asked. Both of
those paths returned "one edition, scan complete", so the app told the user as
a fact "Only one edition of this album; downloading it" and quietly fetched the
plain album. On a release that really does have a richer deluxe sibling, best
of both was lost and nothing on screen said so.

The scan now reports incomplete whenever it could not read the artist, and the
click turns that into the honest "Could not scan editions, try again" the
discography path already gives for a half-read scan.

WHAT IS PINNED HERE
-------------------
* a swallowed artist failure means incomplete, and the album itself is still
  the single edition returned (the list half of the answer must not change);
* an album with no artist id is incomplete too, and nothing is even asked of
  the session;
* a clean scan is still complete and still finds every sibling edition sharing
  the album's merge key (and only those), naming the clicked album once even
  though the artist's own bucket hands it back too;
* a failing bucket is still incomplete with whatever the working buckets did
  find, whether it is the first bucket or the last one (pre-existing
  behaviour, pinned here as well, and pinned at both ends: "I already found
  something, so call it a finished scan" is the same lie in a smaller coat);
* an artist that answered every bucket and simply has no other edition is
  complete, so this fix cannot turn a genuinely single-edition album into a
  false alarm;
* the user-visible consequence: with the artist unreadable, the click does NOT
  say "Only one edition of this album" and does NOT queue the album as a plain
  download, and with the artist readable it still says exactly that.

Everything below is observed from the real methods (the real
``_sibling_editions``, the real ``_get_artist``, the real
``downloadAlbumBestOfBoth``) bound onto a bare ``WavesBridge`` carcass. Nothing
is asserted by construction.

One thing is worth stating plainly. The click's worker funnels every failure
into one ``except Exception``, so "the app refused" on its own does not prove
the refusal came from the scan coming up short: a worker that died on its way
to the scan would look identical on screen. The two click tests therefore also
watch the session traffic and the failure the app wrote down, so an unrelated
crash cannot wear the honest refusal's clothes.
"""

from __future__ import annotations

import contextlib
import logging
from threading import Lock
from types import SimpleNamespace

import pytest
from tidalapi.album import Album

from waves.waves_ui.backend import WavesBridge

ARTIST_ID = 7
_ARTIST_CREDIT = SimpleNamespace(name="Halcyon Drift", id=ARTIST_ID)
# Same credit, but with no id on it: an album shaped like this can never be
# used to look the artist up.
_ANONYMOUS_CREDIT = SimpleNamespace(name="Halcyon Drift", id=None)


def _album(album_id: str, title: str, credit=_ARTIST_CREDIT):
    """A real tidalapi Album carrying only what the scan reads off one."""
    a = Album.__new__(Album)
    a.id = album_id
    a.name = title
    a.artist = credit
    a.artists = [credit]
    return a


class _Session:
    """Stands in for the tidalapi session the artist lookup goes through.

    Counts its calls so "never even asked" is observable, and can fail the way
    a dropped session or a rate limit does.
    """

    def __init__(self, artist=None, error=None):
        self._artist = artist
        self._error = error
        self.calls: list = []

    def artist(self, artist_id):
        self.calls.append(artist_id)
        if self._error is not None:
            raise self._error
        return self._artist


class _Artist:
    """The three release buckets the scan walks, each independently able to
    answer, come back empty, or fail."""

    def __init__(self, albums=None, ep_singles=None, other=None, failing=()):
        self._buckets = {
            "get_albums": list(albums or []),
            "get_ep_singles": list(ep_singles or []),
            "get_other": list(other or []),
        }
        self._failing = set(failing)
        self.asked: list = []

    def _answer(self, name):
        self.asked.append(name)
        if name in self._failing:
            raise RuntimeError("429 Too Many Requests")
        return self._buckets[name]

    def get_albums(self):
        return self._answer("get_albums")

    def get_ep_singles(self):
        return self._answer("get_ep_singles")

    def get_other(self):
        return self._answer("get_other")


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args)


class _StatusLog:
    """Stands in for statusChanged, recording the status line the real
    _set_status has just published."""

    def __init__(self, owner):
        self.owner = owner
        self.texts: list = []

    def emit(self, *args):
        self.texts.append(self.owner._status)


class _GaveUpBecause(logging.Handler):
    """Collects the failures the app writes down while working, so a test can
    tell "I could not finish looking" apart from "something else went wrong".

    Both spellings of the reason are kept. Once the diagnostics stack has been
    installed anywhere in this process, its redacting filter pre-formats a
    record's traceback into ``exc_text`` and clears ``exc_info`` before a later
    handler sees the record, so reading only the exception object makes this
    silently blind depending on which tests ran first."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.failures: list = []

    def emit(self, record):
        exc = record.exc_info[1] if record.exc_info else None
        self.failures.append((record.getMessage(), exc, record.exc_text or ""))


@contextlib.contextmanager
def _watching_failures():
    log = logging.getLogger("waves")
    handler = _GaveUpBecause()
    was_level, was_disabled = log.level, log.disabled
    log.setLevel(logging.ERROR)
    log.disabled = False
    log.addHandler(handler)
    try:
        yield handler.failures
    finally:
        log.removeHandler(handler)
        log.setLevel(was_level)
        log.disabled = was_disabled


class _InlinePool:
    """Runs the scan worker on the calling thread, so the click is testable."""

    @staticmethod
    def start(worker):
        worker.fn()


def _bridge(session):
    """A bare WavesBridge carcass carrying only the fields the scan touches."""
    bridge = WavesBridge.__new__(WavesBridge)
    bridge._objs = {"artist": {}, "album": {}}
    bridge._objs_lock = Lock()
    bridge._objs_max = 32
    bridge.tidal = SimpleNamespace(session=session)
    return bridge


def _clicking_bridge(session, album):
    """The same carcass, plus what a DOWNLOAD click on an album needs."""
    bridge = _bridge(session)
    bridge._objs["album"]["a1"] = album
    bridge._dl = object()
    bridge._merge_plans = {}
    bridge._merge_scanned = {"a1"}
    bridge._scan_pool = _InlinePool()
    bridge._scan_gen = 0  # the generation STOP bumps; never bumped here
    bridge._scans_in_flight = 0
    bridge._scan_count_lock = Lock()
    bridge.scanningChanged = _Signal()
    bridge._waves_prefs = {"explicit_mode": "explicit"}
    bridge.downloadState = _Signal()
    bridge._albumsQueued = _Signal()
    bridge._status = ""
    bridge.statusChanged = _StatusLog(bridge)
    return bridge


# ---- the scan itself --------------------------------------------------------


def test_a_swallowed_artist_failure_is_not_a_finished_scan():
    """The session dropped mid-click. Not one release bucket was read, so the
    scan cannot claim it looked, and the album is still the only edition it can
    name."""
    session = _Session(error=RuntimeError("session dropped"))
    bridge = _bridge(session)
    album = _album("a1", "Nightfall")

    editions, complete = bridge._sibling_editions(album)

    assert complete is False, "an unreadable artist was reported as a complete edition scan"
    assert editions == [album], "the list half of the answer changed"
    assert session.calls == [ARTIST_ID], "the artist really was asked for, and really did fail"


def test_an_album_with_no_artist_id_is_not_a_finished_scan():
    """Nothing to ask, so nothing was asked, so nothing is known."""
    session = _Session(artist=_Artist())
    bridge = _bridge(session)
    album = _album("a1", "Nightfall", credit=_ANONYMOUS_CREDIT)

    editions, complete = bridge._sibling_editions(album)

    assert complete is False, "an album with no artist to ask was reported as a complete scan"
    assert editions == [album]
    assert session.calls == [], "an album with no artist id must not reach the session at all"


def test_a_clean_scan_is_complete_and_finds_every_sibling_edition():
    """Every bucket answered, so the app is entitled to act on the answer: the
    deluxe and the explicit cut are other editions of this album, the remaster
    and a different album are not, and the album you clicked is named once."""
    deluxe = _album("a2", "Nightfall (Deluxe Edition)")
    explicit = _album("a3", "Nightfall (Explicit)")
    remaster = _album("a4", "Nightfall (Remastered)")  # a different release, not an edition
    unrelated = _album("a5", "Daybreak")
    # The artist's own bucket hands back the album that was clicked as well, as
    # a freshly built object. It must not be counted a second time: an album
    # merged with a copy of itself is not a best of both, and it would also rob
    # a genuinely single-edition album of its honest "only one edition" line.
    own_copy = _album("a1", "Nightfall")
    artist = _Artist(albums=[own_copy, deluxe, remaster], ep_singles=[explicit], other=[unrelated])
    bridge = _bridge(_Session(artist=artist))
    album = _album("a1", "Nightfall")

    editions, complete = bridge._sibling_editions(album)

    assert complete is True, "a scan that read every bucket was reported incomplete"
    assert editions == [album, deluxe, explicit], editions
    assert artist.asked == ["get_albums", "get_ep_singles", "get_other"]


@pytest.mark.parametrize(
    ("failing", "kept"),
    [
        ("get_albums", ("explicit",)),
        ("get_other", ("deluxe", "explicit")),
    ],
    ids=["the_first_bucket_failed", "the_last_bucket_failed_holding_a_sibling"],
)
def test_one_failing_bucket_still_reports_incomplete_with_what_was_found(failing, kept):
    """Pre-existing behaviour, pinned here too: two buckets answered, one did
    not, so the scan keeps what it found and still says it is short.

    Both ends of the walk are pinned on purpose. When the bucket that gets rate
    limited is the last one, the scan is already holding a sibling, and
    "something turned up, so I must have finished looking" is the same false
    claim in a smaller coat: the bucket that never answered is exactly the one
    that might have held the richer edition.
    """
    siblings = {
        "deluxe": _album("a2", "Nightfall (Deluxe Edition)"),
        "explicit": _album("a3", "Nightfall (Explicit)"),
    }
    artist = _Artist(
        albums=[siblings["deluxe"]],
        ep_singles=[siblings["explicit"]],
        failing={failing},
    )
    bridge = _bridge(_Session(artist=artist))
    album = _album("a1", "Nightfall")

    editions, complete = bridge._sibling_editions(album)

    assert complete is False, "a half-read scan was reported complete"
    assert editions == [album, *(siblings[name] for name in kept)], editions


def test_an_artist_with_no_other_edition_is_a_complete_scan():
    """The case this fix must NOT turn into a false alarm: every bucket
    answered, and this album really is the only edition there is."""
    artist = _Artist(albums=[_album("a5", "Daybreak")])
    bridge = _bridge(_Session(artist=artist))
    album = _album("a1", "Nightfall")

    editions, complete = bridge._sibling_editions(album)

    assert complete is True, "a genuinely single-edition album was reported as a failed scan"
    assert editions == [album]


# ---- the click, which is where the lie was visible --------------------------


@pytest.mark.parametrize(
    ("credit", "make_session", "expected_calls"),
    [
        (_ARTIST_CREDIT, lambda: _Session(error=RuntimeError("session dropped")), [ARTIST_ID]),
        (_ANONYMOUS_CREDIT, lambda: _Session(artist=_Artist()), []),
    ],
    ids=["artist_fetch_failed", "no_artist_id"],
)
def test_an_unreadable_artist_never_claims_there_is_only_one_edition(credit, make_session, expected_calls):
    """What a person saw: DOWNLOAD on an album while the session has dropped or
    TIDAL is rate limiting, and the app stated as a fact that this album has no
    other edition, then fetched it plain. It must say it could not look."""
    album = _album("a1", "Nightfall", credit=credit)
    session = make_session()
    bridge = _clicking_bridge(session, album)

    with _watching_failures() as failures:
        bridge.downloadAlbumBestOfBoth("a1")

    said = bridge.statusChanged.texts
    assert not any("Only one edition" in text for text in said), said
    assert not any("No higher-quality edition" in text for text in said), said
    assert bridge._albumsQueued.emits == [], "the album was silently queued as a plain download"
    assert said[-1] == "Could not scan editions, try again", said
    assert bridge.downloadState.emits == [("a1", "preparing"), ("a1", "failed")]
    assert "a1" not in bridge._merge_scanned, "the retry the status line invites would download it plain"
    # The refusal has to be the scan's, not any old failure. The click swallows
    # everything into one handler, so without these two the same honest wording
    # would appear (and this test would still pass) if the work never reached
    # the scan at all.
    assert session.calls == expected_calls, "the scan did not reach the session the way this path does"
    assert failures, "the app gave up without writing down why"
    message, exc, traceback_text = failures[-1]
    assert "Edition scan failed" in message, message
    reason = f"{exc!r}\n{traceback_text}"
    assert "RuntimeError" in reason, f"gave up on something other than the scan's own refusal: {reason}"
    assert "incomplete" in reason, f"gave up for an unrelated reason: {reason}"


def test_a_genuinely_single_edition_album_still_says_so_and_downloads():
    """The other side of the same coin: the scan really did read everything, so
    the app is entitled to say there is only one edition, and to fetch it."""
    artist = _Artist(albums=[_album("a5", "Daybreak")])
    album = _album("a1", "Nightfall")
    session = _Session(artist=artist)
    bridge = _clicking_bridge(session, album)

    bridge.downloadAlbumBestOfBoth("a1")

    assert bridge.statusChanged.texts[-1] == "Only one edition of this album; downloading it"
    assert bridge._albumsQueued.emits == [(0, ["a1"])]
    assert bridge.downloadState.emits == [("a1", "preparing")]
    assert session.calls == [ARTIST_ID]
    assert artist.asked == ["get_albums", "get_ep_singles", "get_other"]
