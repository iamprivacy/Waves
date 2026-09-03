"""REDOWNLOAD and the upgrade verdict: when an owned copy is re-fetched.

Two things bring an owned track back through the engine. Clicking the
DOWNLOADED half of a card opens the owned gate; REDOWNLOAD must actually
fetch, so every pre-fetch gate stands down (without the force, the
ownership gate would skip every owned track and the job would fetch nothing
while reporting done). The first tests pin that the override reaches the
engine and that its verdict is "force", decided before any store or library
lookup gets a say.

The second is the plain upgrade path, and it is a tier comparison: an owned
copy below the quality a download targets is "force" (overwrite in place),
equal-or-better is "skip", tier-less (a video) is always "skip". The rest of
the file pins that comparison at every rank boundary, LOW (rank 0) included,
both in the download gate (_ownership_decision) and in the answer the button
reads (ownershipOf.up_to_date). LOW is the one that was pinned by nothing:
a slip that treats rank 0 as "no tier" would make every LOW copy current
forever, so a LOW library could never be upgraded.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from waves.ownership import OwnershipStore
from waves.waves_ui import backend


def _gate(force: bool) -> backend._TrackedDownload:
    dl = backend._TrackedDownload.__new__(backend._TrackedDownload)
    dl._force_redownload = force
    # Booby-trapped gates: a forced job must never even consult them.
    dl._ownership_of = lambda mid: (_ for _ in ()).throw(AssertionError("ownership consulted"))
    dl._library_claim = lambda media: (_ for _ in ()).throw(AssertionError("library claim consulted"))
    return dl


def test_forced_job_verdict_is_force_without_consulting_any_gate():
    m = MagicMock()
    m.id = "123"
    m.waves_identity_id = None
    assert _gate(True)._claim_verdict(m) == "force"


def test_register_redownload_marks_both_overrides():
    stub = MagicMock()
    stub._redownload_overrides = set()
    stub._library_claim_overrides = set()
    backend.WavesBridge.registerRedownload(stub, "a1")
    assert stub._redownload_overrides == {"a1"}
    assert stub._library_claim_overrides == {"a1"}, "a forced job must not be re-gated by a tag match"


# --------------------------------------------------------------------------- #
# The tier boundary itself: which owned copies are "current" and which force
# an upgrade. Ranks follow waves.ownership.QUALITY_RANK (LOW 0, HIGH 1,
# LOSSLESS 2, HI_RES_LOSSLESS 3); -1 is a tier-less (video) record.
# --------------------------------------------------------------------------- #
def _upgrade_gate(rec: dict | None, target_rank: int) -> backend._TrackedDownload:
    """A _TrackedDownload whose ownership lookup answers ``rec`` for every id
    and whose job targets ``target_rank``, Atmos off (the plain tier path)."""
    dl = backend._TrackedDownload.__new__(backend._TrackedDownload)
    dl._ownership_of = lambda mid: rec
    dl._target_rank = target_rank
    dl.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=False))
    return dl


def _plain_track(tid: str = "123") -> MagicMock:
    m = MagicMock()
    m.id = tid
    m.waves_identity_id = None  # a plain single-track job, no merge placement
    m.audio_modes = []
    return m


def test_low_copy_is_stale_against_lossless_and_forces_an_upgrade():
    # The LOW tier is rank 0. It is a real tier, not "no tier": treating it as
    # tier-less would make every LOW copy read as current forever (skip on
    # every download, button stuck on DOWNLOAD, a LOW library that can never
    # be upgraded).
    rec = {"quality_rank": 0, "quality_tier": "LOW", "path": "/x/song.m4a"}
    assert backend._copy_is_current(rec, target_rank=3, wants_atmos=False) is False
    assert _upgrade_gate(rec, 3)._ownership_decision(_plain_track()) == ("force", rec)


@pytest.mark.parametrize(
    "rec, target_rank, verdict",
    [
        ({"quality_rank": 0}, 0, "skip"),  # LOW copy, LOW target: nothing to gain
        ({"quality_rank": 0}, 1, "force"),  # LOW under HIGH: upgrade
        ({"quality_rank": 1}, 1, "skip"),  # equal rank is current
        ({"quality_rank": 2}, 3, "force"),  # LOSSLESS under HI_RES: upgrade
        ({"quality_rank": 3}, 3, "skip"),  # equal at the top
        ({"quality_rank": 4}, 3, "skip"),  # better than the target is current
        ({"quality_rank": -1}, 3, "skip"),  # tier-less video record: never re-fetched
        ({"path": "/x/clip.mp4"}, 3, "skip"),  # no quality_rank at all: same as tier-less
    ],
)
def test_ownership_verdict_at_every_tier_boundary(rec, target_rank, verdict):
    stale = verdict == "force"
    assert backend._copy_is_current(rec, target_rank, wants_atmos=False) is (not stale)
    assert _upgrade_gate(rec, target_rank)._ownership_decision(_plain_track()) == (verdict, rec)


class _OwnBridge:
    """The slice of WavesBridge that ownershipOf touches, bound onto a real
    OwnershipStore (the shape tests/test_ownership_bridge.py uses), with the
    refresh pool run inline so the second query serves the refreshed cache."""

    class _Pool:
        def start(self, worker):
            worker.run()

    class _Sig:
        def emit(self, *a):
            pass

    def __init__(self, tmp_path, quality_audio: str):
        self._ownership = OwnershipStore(str(tmp_path / "ownership.sqlite3"))
        self._own_cache: dict = {}
        self._own_pending: set = set()
        self._own_lock = Lock()
        self._own_announce: list = []
        self._own_announce_armed = False
        self._OWN_CACHE_MAX = backend.WavesBridge._OWN_CACHE_MAX
        self._OWN_TTL = backend.WavesBridge._OWN_TTL
        self._OWN_TTL_BUSY = backend.WavesBridge._OWN_TTL_BUSY
        self._own_pool = self._Pool()
        self._ownAnnounceArm = self._Sig()
        self._downloads_running = lambda: False
        self.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=quality_audio, download_dolby_atmos=False))
        for name in (
            "ownershipOf",
            "_would_refetch_atmos",
            "_own_refresh",
            "_announce_ownership",
            "_evict_own_cache_locked",
            "_target_quality_rank",
            # The per-item quality choice's rank (issue #36): no choice on
            # this carcass, so it answers with the setting's rank.
            "_override_target_rank",
            "_quality_override_key",
        ):
            setattr(self, name, getattr(backend.WavesBridge, name).__get__(self, _OwnBridge))

    def own(self, tid: str) -> dict:
        self.ownershipOf(tid)  # schedules the (inline) refresh
        return self.ownershipOf(tid)


def _surviving_file(tmp_path):
    """ownership_of only answers for a path that still holds bytes."""
    f = tmp_path / "song.m4a"
    f.write_text("audio")
    return f


@pytest.mark.parametrize(
    "quality_audio, up_to_date",
    [("HI_RES_LOSSLESS", False), ("LOW", True)],
)
def test_ownership_of_reports_a_low_copy_against_the_current_setting(tmp_path, quality_audio, up_to_date):
    # The button's own verdict (ownershipOf.up_to_date) must agree with the
    # download gate: a LOW copy under a HI_RES setting offers an upgrade; the
    # same copy under a LOW setting is current.
    f = _surviving_file(tmp_path)
    bridge = _OwnBridge(tmp_path, quality_audio)
    bridge._ownership.record("42", str(f), "LOW")
    info = bridge.own("42")
    assert info["owned"] is True
    assert info["quality_rank"] == 0
    assert info["up_to_date"] is up_to_date


# --------------------------------------------------------------------------- #
# gap-round G-13: a broken-formatter copy must never satisfy the gate. Old
# builds wrote "[None]" where the release year belonged (any album TIDAL
# lists no date for), and the pre-fix album-404 fallback left a literal
# "{album_track_num}" token in file names. The fixed formatter can never
# rebuild those spellings, so a record pointing at one would freeze the
# garbage file as the owned copy and skip the corrected re-download forever.
# The old file is left alone (the app never deletes user-visible files); the
# fresh download lands at the corrected path and takes over the record.
# --------------------------------------------------------------------------- #
def test_a_none_foldered_copy_never_satisfies_the_gate():
    rec = {"path": "/m/Artist/[None] Album/01 Song.flac", "quality_rank": 3}
    assert _upgrade_gate(rec, target_rank=2)._ownership_decision(_plain_track()) == (None, None)


def test_a_template_token_copy_never_satisfies_the_gate():
    rec = {"path": "/m/Artist/Album/1-{album_track_num}. Artist - Song.flac", "quality_rank": 3}
    assert _upgrade_gate(rec, target_rank=2)._ownership_decision(_plain_track()) == (None, None)


def test_a_normally_named_copy_still_skips_at_equal_quality():
    rec = {"path": "/m/Artist/[2019] Album/01 Song.flac", "quality_rank": 2}
    verdict, out = _upgrade_gate(rec, target_rank=2)._ownership_decision(_plain_track())
    assert verdict == "skip" and out is rec
