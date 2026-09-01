"""An Atmos-only track downloads whatever the Atmos setting says.

THE RULE (decided 2026-08-18)
-----------------------------
TIDAL lists the Dolby Atmos version of a song as its own track id with no
stereo stream behind it. "Download Dolby Atmos" means "prefer stereo where
there is a choice"; for an Atmos-only track there is no choice, so honoring the
setting by skipping it put a permanent hole in every discography that carried a
spatial-only single, and a hole is worse than a song you cannot play today.

So the engine downloads it either way, through the Atmos session, because the
normal session has nothing to offer for it. The whole exclusion apparatus that
skip built (the pre-path bail-out, the _note_excluded hook, excluded_count,
the "excluded" track status and the ATMOS ONLY word) is retired with it, and
this file pins both halves: the download happens, and the apparatus is gone,
so it cannot half-return and report albums finished over files never fetched.

WHAT IS REAL
------------
The session-choice tests drive the engine's own _get_track_stream_info with a
stub tidal that only records which session was reached for, the same shape
tests/test_atmos_ownership_scale.py uses. The gate tests drive the real
_TrackedDownload._ownership_decision over a real OwnershipStore with real
files, because the mirror _delivers_atmos now carries the engine's "nothing
else to fetch" clause and the two must not drift.
"""

from __future__ import annotations

from types import SimpleNamespace

from tidalapi.media import AudioMode, Quality, Track

from waves.constants import ATMOS_REQUEST_QUALITY
from waves.download import Download
from waves.ownership import OwnershipStore, quality_rank
from waves.waves_ui import backend
from waves.waves_ui.backend import _delivers_atmos, _TrackedDownload

ATMOS = AudioMode.dolby_atmos.value
ATMOS_TIER = str(getattr(ATMOS_REQUEST_QUALITY, "value", ATMOS_REQUEST_QUALITY))


def _track(tid="101", modes=None):
    t = Track.__new__(Track)
    t.id = tid
    t.name = "Song"
    t.artist = SimpleNamespace(name="Artist")
    t.audio_modes = [ATMOS] if modes is None else list(modes)
    return t


def _session_reached(media, atmos_on: bool) -> str:
    """Drive the REAL Download._get_track_stream_info and report which session
    it reached for. Only the session and the stream are stand-ins; the branch
    under observation is the engine's own."""
    reached: list[str] = []
    stream = SimpleNamespace(get_stream_manifest=lambda: SimpleNamespace(file_extension=".m4a", codecs="EAC3"))
    dl = Download.__new__(Download)
    dl.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=atmos_on, extract_flac=False))
    dl.fn_logger = SimpleNamespace(error=lambda *a, **k: None, info=lambda *a, **k: None)
    dl.tidal = SimpleNamespace(
        switch_to_atmos_session=lambda: (reached.append("atmos"), True)[1],
        restore_normal_session=lambda: (reached.append("normal"), True)[1],
    )
    dl.session = SimpleNamespace(track=lambda _id: SimpleNamespace(get_stream=lambda: stream))
    media.get_stream = lambda: stream
    dl._get_track_stream_info(media)
    return reached[0] if reached else ""


# --------------------------------------------------------------------------- #
# The engine: the Atmos session serves the track that has nothing else
# --------------------------------------------------------------------------- #
def test_an_atmos_only_track_takes_the_atmos_session_with_the_setting_off():
    assert _session_reached(_track(modes=[ATMOS]), atmos_on=False) == "atmos"


def test_the_setting_still_decides_where_there_is_a_choice():
    """A dual-mode track is a stereo release that also carries Atmos: the
    setting keeps its whole meaning there, in both directions."""
    assert _session_reached(_track(modes=[ATMOS, "STEREO"]), atmos_on=False) == "normal"
    assert _session_reached(_track(modes=[ATMOS, "STEREO"]), atmos_on=True) == "atmos"
    assert _session_reached(_track(modes=["STEREO"]), atmos_on=False) == "normal"
    assert _session_reached(_track(modes=["STEREO"]), atmos_on=True) == "normal"


def test_no_modes_at_all_is_a_normal_fetch():
    """A track with an empty or missing mode list has no Atmos to want."""
    assert _session_reached(_track(modes=[]), atmos_on=False) == "normal"
    assert _session_reached(_track(modes=[]), atmos_on=True) == "normal"


def test_the_bridge_mirror_agrees_with_the_engine_on_every_shape():
    """_delivers_atmos claims to be the engine's want_atmos, and the ownership
    gate ranks owned copies on the scale it names. Check every combination
    against the engine itself rather than against a transcription of it."""
    for atmos_on in (True, False):
        for modes in ([ATMOS], [ATMOS, "STEREO"], ["STEREO"], []):
            media = _track(modes=modes)
            engine = _session_reached(media, atmos_on) == "atmos"
            assert _delivers_atmos(media, atmos_on) is engine, (atmos_on, modes)


# --------------------------------------------------------------------------- #
# The gate: an owned Atmos-only copy stays a skip, with no special case
# --------------------------------------------------------------------------- #
def _gate(store, *, target, atmos_on):
    dl = _TrackedDownload.__new__(_TrackedDownload)
    dl._ownership_of = store.ownership_of
    dl._target_rank = quality_rank(target)
    dl.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=atmos_on))
    return dl


def _own(store, tmp_path, tid):
    p = tmp_path / f"{tid}.m4a"
    p.write_text("audio")
    store.record(tid, str(p), ATMOS_TIER, audio_mode=ATMOS)


def test_an_owned_atmos_only_copy_skips_at_any_target_and_either_setting(tmp_path):
    """The re-fetch would take the Atmos session (nothing else to take) and
    deliver exactly what is already on disk, so the copy is current. This is
    what the retired exclusion mirror used to patch in by hand for the
    setting-off half; the mirror of the engine's own condition now answers
    both halves."""
    store = OwnershipStore(str(tmp_path / "own.db"))
    _own(store, tmp_path, "101")
    for atmos_on in (True, False):
        for target in (Quality.low_320k, Quality.high_lossless, Quality.hi_res_lossless):
            dl = _gate(store, target=target.value, atmos_on=atmos_on)
            verdict, _rec = dl._ownership_decision(_track("101", modes=[ATMOS]))
            assert verdict == "skip", (atmos_on, target)


def test_a_dual_mode_copy_still_upgrades_when_stereo_can_do_better(tmp_path):
    """The setting off + a stereo stream on offer + owned below target is a
    real upgrade, and the new clause must not have swallowed it."""
    store = OwnershipStore(str(tmp_path / "own.db"))
    _own(store, tmp_path, "202")
    dl = _gate(store, target=Quality.hi_res_lossless.value, atmos_on=False)
    verdict, _rec = dl._ownership_decision(_track("202", modes=[ATMOS, "STEREO"]))
    assert verdict == "force"


def test_an_atmos_only_track_not_on_disk_gates_nothing(tmp_path):
    store = OwnershipStore(str(tmp_path / "own.db"))
    dl = _gate(store, target=Quality.hi_res_lossless.value, atmos_on=False)
    verdict, rec = dl._ownership_decision(_track("303", modes=[ATMOS]))
    assert (verdict, rec) == (None, None)


# --------------------------------------------------------------------------- #
# The apparatus is gone, whole
# --------------------------------------------------------------------------- #
def test_the_exclusion_apparatus_is_fully_retired():
    """No half-retirement: a leftover counter or status would let some surface
    keep reporting exclusions that can no longer happen. The names are asserted
    absent so a partial revert cannot slip back in through one file."""
    for name in ("_excluded_by_setting", "_excluded_note"):
        assert not hasattr(backend, name), name
    for name in ("_note_excluded", "_take_excluded", "_note_exclusion"):
        assert not hasattr(_TrackedDownload, name), name
        assert not hasattr(Download, name), name
    import inspect

    assert "excluded_count" not in inspect.signature(backend._collection_incomplete_reason).parameters
    assert "excluded" not in inspect.getsource(backend.WavesBridge._download_merge_plan)
    qml = (backend.pathlib.Path(backend.__file__).parent / "qml" / "Main.qml").read_text()
    assert "ATMOS ONLY" not in qml
