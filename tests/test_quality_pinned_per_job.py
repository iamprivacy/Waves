"""A download finishes at the quality it was queued at.

WHAT THIS FENCES OFF
--------------------
Streams are asked for at the SHARED tidal session's audio quality, and saving
a new audio quality writes it to that session at once (issue #9, so the choice
takes effect without a restart). That also silently retargeted work the user
had already started: a queue full of albums lined up at HI-RES began arriving
at HIGH the moment the setting changed, mid-run, and the drawer re-stated
every queued row to match.

Now each row records the quality it was queued at (``askQuality``) and its job
carries that quality (``_TrackedDownload._pinned_quality``), written onto the
session inside ``tidal.stream_lock``, the lock that already serialises every
stream fetch in the process. A change in Settings therefore applies to what is
queued NEXT; what is queued or running keeps its quality, and the drawer keeps
stating it (test_queue_expected_tier.py).

HOW THIS STAYS FIXED
--------------------
Method-bound stubs, no display and no session: the row records the setting at
enqueue; the runner hands that quality to the download it builds; the download
writes it on the session for the fetch and puts back what it found; an Atmos
track is left alone (it carries its own session and quality); and the skip /
upgrade rank follows the job's quality, not the current setting.

The Atmos case is fenced twice, and both fences perform the session switch
inside the call the way config.py really does. A recorder-only parent cannot:
it returns with the session as it entered, so a restore of that same value is
invisible, and that is how the pin once passed every test while restoring the
stereo tier over the Atmos session. The single-fetch test now says the session
still holds the Atmos quality afterwards and nothing was restored; the
MULTI-track run says every Atmos track is asked at the Atmos quality, because
the switch writes that quality only when it has to BUILD the session, so a
restore over it is never undone and the damage shows on the NEXT track.
"""

from __future__ import annotations

import inspect
from threading import Lock
from types import SimpleNamespace

import pytest
from tidalapi.media import AudioMode, Quality

from tidaler.constants import ATMOS_REQUEST_QUALITY
from tidaler.waves_ui import backend


class _Session:
    def __init__(self, quality=Quality.high_lossless):
        self.audio_quality = quality


class _Tidal:
    def __init__(self, session):
        self.session = session
        self.restores = 0

    def restore_normal_session(self, force: bool = False) -> bool:
        self.restores += 1
        return True


def _tracked(pinned, session, *, atmos_setting=False):
    """A _TrackedDownload with only the fields the pin touches, and a parent
    _get_track_stream_info that reports the quality it was called at."""
    dl = backend._TrackedDownload.__new__(backend._TrackedDownload)
    dl._pinned_quality = pinned
    dl._delivered = {}
    dl._delivered_lock = Lock()
    dl.tidal = _Tidal(session)
    dl.session = session
    dl.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=atmos_setting))
    return dl


def _call_with_parent_recording(dl, media, seen):
    """Run the override with the engine's method replaced by a recorder."""
    original = backend.Download._get_track_stream_info

    def spy(self, m):
        seen.append(self.session.audio_quality)
        return SimpleNamespace(media_stream=None, stream_manifest=None)

    backend.Download._get_track_stream_info = spy
    try:
        return backend._TrackedDownload._get_track_stream_info(dl, media)
    finally:
        backend.Download._get_track_stream_info = original


def test_the_fetch_uses_the_jobs_quality_and_the_session_is_left_as_found():
    session = _Session(Quality.low_320k)
    dl = _tracked(Quality.hi_res_lossless, session)
    seen: list = []
    _call_with_parent_recording(dl, SimpleNamespace(id="1", audio_modes=[]), seen)
    assert seen == [Quality.hi_res_lossless], "the stream was not asked for at the job's quality"
    assert session.audio_quality == Quality.low_320k, "the shared session kept the job's quality"
    # Applied AFTER a session restore, which re-reads the setting when it has
    # to rebuild a normal session.
    assert dl.tidal.restores == 1


def test_a_job_without_a_pin_is_left_entirely_alone():
    session = _Session(Quality.high_lossless)
    dl = _tracked(None, session)
    seen: list = []
    _call_with_parent_recording(dl, SimpleNamespace(id="1", audio_modes=[]), seen)
    assert seen == [Quality.high_lossless]
    assert dl.tidal.restores == 0


class _SwitchingTidal:
    """The session switch as tidaler/config.py really performs it.

    The two facts that matter, and that a recorder-only stub hides: the Atmos
    request quality is written only when the session has to be BUILT, and the
    flag is sticky, so a second switch on an already-Atmos session writes
    nothing at all. Restoring a normal session re-reads the setting.
    """

    def __init__(self, session, setting_quality):
        self.session = session
        self.is_atmos_session = False
        self.restores = 0
        self.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=setting_quality.value))

    def switch_to_atmos_session(self) -> bool:
        if self.is_atmos_session:
            return True
        self.session.audio_quality = ATMOS_REQUEST_QUALITY
        self.is_atmos_session = True
        return True

    def restore_normal_session(self, force: bool = False) -> bool:
        self.restores += 1
        if not self.is_atmos_session and not force:
            return True
        self.session.audio_quality = Quality(self.settings.data.quality_audio)
        self.is_atmos_session = False
        return True


_ATMOS = SimpleNamespace(id="a", audio_modes=[AudioMode.dolby_atmos.value])
_STEREO = SimpleNamespace(id="s", audio_modes=[])


def _run(pinned, session, media_list, *, setting=Quality.hi_res_lossless, dl_out=None):
    """Drive the override over a RUN of tracks, with a parent that performs the
    engine's own session switch (download.py) inside the call the way the real
    one does. Reports the quality each fetch was actually asked at; the built
    download is handed back through dl_out when a test wants its stand-ins."""
    dl = backend._TrackedDownload.__new__(backend._TrackedDownload)
    if dl_out is not None:
        dl_out.append(dl)
    dl._pinned_quality = pinned
    dl._target_rank = -1
    dl._delivered = {}
    dl._delivered_lock = Lock()
    dl.tidal = _SwitchingTidal(session, setting)
    dl.session = session
    dl.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=True))

    asked: list = []

    def spy(self, m):
        if self._wants_atmos(m):
            self.tidal.switch_to_atmos_session()
        else:
            self.tidal.restore_normal_session()
        asked.append(self.session.audio_quality)
        return SimpleNamespace(media_stream=None, stream_manifest=None)

    original = backend.Download._get_track_stream_info
    backend.Download._get_track_stream_info = spy
    try:
        for m in media_list:
            backend._TrackedDownload._get_track_stream_info(dl, m)
    finally:
        backend.Download._get_track_stream_info = original
    return asked


def test_an_atmos_track_keeps_the_engines_own_session_handling():
    """The single fetch, with the switch REALLY performed inside the call. A
    parent that only records the quality returns with the session as it
    entered, and a restore of that same value is invisible: eleven tests
    passed while the pin was restoring the stereo tier over the Atmos session.
    So the parent switches, and the session must still hold the Atmos quality
    after the override returns, with the pin having restored nothing."""
    session = _Session(Quality.hi_res_lossless)
    built: list = []
    asked = _run(Quality.low_320k, session, [_ATMOS], dl_out=built)
    assert asked == [ATMOS_REQUEST_QUALITY], "the pin overrode an Atmos fetch"
    assert session.audio_quality == ATMOS_REQUEST_QUALITY, "the pin put the job's tier back over the Atmos session"
    assert built[0].tidal.restores == 0, "an Atmos fetch is the engine's business, the pin restores nothing"


def test_every_atmos_track_in_a_run_is_asked_for_at_the_atmos_quality():
    """Not just the first one. The pin may capture nothing on an Atmos track,
    because the switch that sets the Atmos quality runs after that point and
    putting the job's own tier back would leave the Atmos session asking for
    stereo for the rest of the job."""
    session = _Session(Quality.hi_res_lossless)
    asked = _run(Quality.hi_res_lossless, session, [_ATMOS, _ATMOS, _ATMOS])
    assert asked == [ATMOS_REQUEST_QUALITY] * 3, asked


def test_an_atmos_fetch_leaves_the_session_at_the_atmos_quality():
    session = _Session(Quality.hi_res_lossless)
    _run(Quality.hi_res_lossless, session, [_ATMOS])
    assert session.audio_quality == ATMOS_REQUEST_QUALITY


def test_a_run_that_mixes_atmos_and_stereo_asks_each_at_its_own_quality():
    """And the stereo track's restore is what decides the tier it puts back,
    so the Atmos quality never leaks onto a rebuilt normal session."""
    session = _Session(Quality.hi_res_lossless)
    asked = _run(Quality.hi_res_lossless, session, [_ATMOS, _STEREO, _ATMOS])
    assert asked == [ATMOS_REQUEST_QUALITY, Quality.hi_res_lossless, ATMOS_REQUEST_QUALITY], asked


def test_the_row_records_the_setting_it_was_queued_at():
    stub = SimpleNamespace()
    stub._queue_seq = 0
    stub._queue = []
    stub._queue_index = {}
    stub._queue_lock = Lock()
    stub._qdirty_added = []  # _enqueue marks the new row for the delta flush
    stub._emit_queue = lambda: None
    stub._target_tier = lambda: "HI-RES"
    stub.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=Quality.hi_res_lossless))
    stub._queued_quality_value = backend.WavesBridge._queued_quality_value.__get__(stub, type(stub))
    # The other value a row pins at birth, tested on its own in
    # tests/test_queue_row_pins_the_library_skip.py.
    stub._library_bulk_skip_on = lambda: True
    qid = backend.WavesBridge._enqueue.__get__(stub, type(stub))("Album", "album")
    assert stub._queue[0]["askQuality"] == Quality.hi_res_lossless.value
    assert stub._queue[0]["qid"] == qid


def test_an_unreadable_setting_pins_nothing_rather_than_failing_to_queue():
    stub = SimpleNamespace()  # no settings at all
    assert backend.WavesBridge._queued_quality_value.__get__(stub, type(stub))() == ""


@pytest.mark.parametrize(
    ("raw", "want"),
    [(Quality.low_320k.value, Quality.low_320k), ("", None), ("NOT_A_TIER", None)],
)
def test_job_quality_reads_the_row(raw, want):
    stub = SimpleNamespace()
    stub._queue_item = lambda qid: {"qid": qid, "askQuality": raw}
    assert backend.WavesBridge._job_quality.__get__(stub, type(stub))(1) == want


def test_a_missing_row_pins_nothing():
    stub = SimpleNamespace(_queue_item=lambda qid: None)
    assert backend.WavesBridge._job_quality.__get__(stub, type(stub))(1) is None


def test_the_runner_hands_its_rows_quality_to_the_download():
    src = inspect.getsource(backend.WavesBridge)
    call = src[src.index("dl = self._build_download(") :]
    call = call[: call.index("\n        )")]
    assert "pinned_quality=self._job_quality(qid)" in call, call


def test_the_skip_rank_follows_the_jobs_quality_not_the_setting():
    stub = SimpleNamespace(settings=SimpleNamespace(data=SimpleNamespace(quality_audio=Quality.low_320k)))
    rank = backend.WavesBridge._target_quality_rank.__get__(stub, type(stub))
    assert rank() == rank(Quality.low_320k)
    assert rank(Quality.hi_res_lossless) > rank(), "a job queued higher must still count as an upgrade"


# --------------------------------------------------------------------------- #
# A failed restore is not pinned over.
# --------------------------------------------------------------------------- #
class _FlakyRestoreTidal:
    """config.py's restore_normal_session, transcribed with the part the
    switching stand-in above leaves out: it writes the LIVE setting onto the
    session and only THEN re-authenticates, and when that fails it returns
    False without ever clearing is_atmos_session. The script says which
    re-logins succeed, in order."""

    def __init__(self, session, setting_quality, relogin_script):
        self.session = session
        self.is_atmos_session = True  # an Atmos track just went through
        self.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=setting_quality.value))
        self._script = list(relogin_script)
        self.restores = 0

    def switch_to_atmos_session(self) -> bool:
        return True

    def restore_normal_session(self, force: bool = False) -> bool:
        self.restores += 1
        if not self.is_atmos_session and not force:
            return True
        self.session.audio_quality = Quality(self.settings.data.quality_audio)
        if not (self._script.pop(0) if self._script else True):
            return False
        self.is_atmos_session = False
        return True


def _stereo_after_atmos(pinned, setting, relogin_script):
    """A download whose session is in Atmos mode (an Atmos track just went
    through), about to fetch a stereo track, driven through the REAL engine
    Download._get_track_stream_info (not a spy) so the engine's own restore
    call inside it really runs. Returns the download, the stereo track, and
    the list get_stream appends the asked quality to."""
    session = _Session(ATMOS_REQUEST_QUALITY)
    dl = backend._TrackedDownload.__new__(backend._TrackedDownload)
    dl._pinned_quality = pinned
    dl._target_rank = -1
    dl._delivered = {}
    dl._delivered_lock = Lock()
    dl.tidal = _FlakyRestoreTidal(session, setting, relogin_script)
    dl.session = session
    dl.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=True, extract_flac=False))
    dl.fn_logger = SimpleNamespace(error=lambda *a, **k: None, info=lambda *a, **k: None)
    asked: list = []
    stream = SimpleNamespace(get_stream_manifest=lambda: SimpleNamespace(file_extension=".flac", codecs="FLAC"))

    def get_stream():
        asked.append(session.audio_quality)
        return stream

    return dl, SimpleNamespace(id="s", audio_modes=[], get_stream=get_stream), asked


def _fetch(dl, media):
    return backend._TrackedDownload._get_track_stream_info(dl, media)


def test_a_stereo_track_after_atmos_is_fetched_at_the_pin_when_the_restore_works():
    dl, media, asked = _stereo_after_atmos(Quality.hi_res_lossless, Quality.low_320k, [True])
    info = _fetch(dl, media)
    assert asked == [Quality.hi_res_lossless]
    assert info.media_stream is not None


def test_a_failed_restore_never_fetches_at_the_live_setting_over_the_pin():
    """The job was queued at HI-RES; Settings has since moved to HIGH. The
    override's restore fails on a network flap and the engine's own restore
    then succeeds. Before the fix the pin was written between the two, the
    engine's restore wrote HIGH over it, and the track was fetched and written
    at HIGH with the ledger agreeing with itself: precisely the harm the pin
    exists to prevent, and silent."""
    dl, media, asked = _stereo_after_atmos(Quality.hi_res_lossless, Quality.low_320k, [False, True])
    info = _fetch(dl, media)
    assert Quality.low_320k not in asked, "the pin was defeated: the track was fetched at today's setting"
    assert asked == [], "a stream was fetched at all after a failed restore"
    assert info.media_stream is None and info.stream_manifest is None, "the engine's own answer to this failure"


def test_a_failed_restore_is_answered_the_way_the_engine_answers_it():
    """The engine returns an empty TrackStreamInfo when its own restore fails
    (download.py); the override answers the same shape, so item() counts a
    failed track a retry picks up, not a crash and not a phantom write."""
    dl, media, _ = _stereo_after_atmos(Quality.hi_res_lossless, Quality.low_320k, [False, False])
    info = _fetch(dl, media)
    fields = (info.stream_manifest, info.file_extension, info.requires_flac_extraction, info.media_stream)
    assert fields == (None, "", False, None)
    assert dl.tidal.is_atmos_session is True, "the session is still Atmos, and honestly so; the next track retries"


def test_a_failed_restore_leaves_the_session_for_the_next_track_to_recover():
    """The next stereo track's restore is a fresh attempt: nothing here holds
    the session in a state that makes recovery impossible."""
    dl, media, asked = _stereo_after_atmos(Quality.hi_res_lossless, Quality.low_320k, [False, True])
    first = _fetch(dl, media)
    second = _fetch(dl, media)
    assert first.media_stream is None
    assert second.media_stream is not None
    assert asked == [Quality.hi_res_lossless], asked
