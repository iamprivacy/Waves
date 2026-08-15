"""REDOWNLOAD from the owned gate: the force override plumbing.

Clicking the DOWNLOADED half of a card opens the owned gate; REDOWNLOAD
must actually fetch, which means every pre-fetch gate stands down: without
the force, the ownership gate would skip every owned track and the job
would fetch nothing while reporting done. These tests pin that the
override reaches the engine and that the verdict it produces is "force"
(the upgrade path's overwrite-in-place), consulted before any store or
library lookup gets a say.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from tidaler.waves_ui import backend


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
