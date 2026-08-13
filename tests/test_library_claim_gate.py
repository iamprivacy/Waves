"""The library scan's bulk claim gate only fires where it is meant to.

WHAT THIS FENCES OFF
--------------------
"Bulk downloads skip what you have" (library_bulk_skip) is the second gate in
the download path that can skip a track nothing else would, and the first one
fed by TAG GUESSES rather than exact identifiers, so its carve-offs matter
even more than the duplicate-recording gate's:

* Only injected at all for collection jobs (the bridge passes library_claim
  None for a single-track click, which is an explicit ask): covered by the
  None-callable case here.
* Never ahead of ownership. An owned record's verdict stands: 'skip' stays
  'skip', and 'force' (a genuine quality upgrade) must never be talked down
  to a skip by a tag match.
* Never inside a "best of both" merge. A merge assembles one complete folder,
  and the claim points at the library, not this job's destination. A merge
  member is recognised by ``waves_identity_id``.
* A claim lookup failure never gates: downloading twice beats not
  downloading at all.

The verdict method is exercised directly on a bare instance (no Qt, no
network, no files): everything it consults is injected, mirroring
test_duplicate_recording_gate.py.
"""

from __future__ import annotations

import types

from tidaler.waves_ui.backend import _TrackedDownload


def _media(identity: str | None = None):
    m = types.SimpleNamespace(id=99, name="Song", artist=types.SimpleNamespace(name="Artist"))
    if identity is not None:
        m.waves_identity_id = identity
    return m


def _gate(*, claim, ownership_of=None, target_rank=3, identity=None):
    dl = _TrackedDownload.__new__(_TrackedDownload)
    dl._ownership_of = ownership_of
    dl._target_rank = target_rank
    dl._recording_scan = None
    dl._skip_duplicate_recordings = False
    dl._library_claim = claim
    return dl, _media(identity=identity)


def test_a_claimed_track_skips():
    dl, m = _gate(claim=lambda media: True)
    assert dl._claim_verdict(m) == "skip"


def test_an_unclaimed_track_downloads():
    dl, m = _gate(claim=lambda media: False)
    assert dl._claim_verdict(m) is None


def test_no_callable_no_gate():
    # The bridge injects None for single-item jobs and whenever the pref or
    # the master switch is off; the engine then never asks.
    calls = []
    dl, m = _gate(claim=None)
    assert dl._claim_verdict(m) is None
    assert calls == []


def test_a_merge_member_is_never_claim_skipped():
    dl, m = _gate(claim=lambda media: True, identity="123")
    # Ownership is None here, so the identity carve-off is the only thing
    # standing between this claimed track and a hole in the merged folder.
    assert dl._claim_verdict(m) is None


def test_an_ownership_upgrade_beats_the_claim():
    # Owned at a lower quality than this run targets: ownership says 'force'
    # (overwrite in place), and the tag guess must not turn that into a skip.
    rec = {"quality_rank": 1, "path": ""}
    dl, m = _gate(claim=lambda media: True, ownership_of=lambda mid: rec, target_rank=3)
    assert dl._claim_verdict(m) == "force"


def test_an_ownership_skip_never_reaches_the_claim():
    seen = []
    rec = {"quality_rank": 3, "path": ""}

    def claim(media):
        seen.append(media)
        return False

    dl, m = _gate(claim=claim, ownership_of=lambda mid: rec, target_rank=3)
    assert dl._claim_verdict(m) == "skip"
    assert seen == []  # ownership answered; the guess was never consulted


def test_a_claim_lookup_failure_never_gates():
    def boom(media):
        raise RuntimeError("index went away")

    dl, m = _gate(claim=boom)
    assert dl._claim_verdict(m) is None
