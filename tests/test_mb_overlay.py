"""The MusicBrainz arbitration OVERLAY: how a stored verdict changes (and does
not change) what the presence slot answers.

The rules fenced off here, each the safe direction:

* The overlay may only UPGRADE an unproven verdict to proven. It never
  creates presence, never downgrades, and never touches an already-proven
  answer.
* It is inert unless BOTH the library master switch and the opt-in
  library_mb_arbiter pref are on (default off: it sends artist and title
  search terms to a third party).
* A pending lookup answers the unproven verdict immediately (the badge is
  never blocked on the network) and is deduplicated by key.
* The bulk claim gate does not read the overlay: it calls the matcher
  directly, so a third-party opinion can never cost a download.

Exercised on a bare mixin instance with everything injected, mirroring
test_library_claim_gate.py: no Qt, no network, no files.
"""

from __future__ import annotations

import tidaler.matching as matching
from tidaler.waves_ui.bridge_library import LibraryMixin


class _Stub:
    """A bare object carrying just what the overlay methods consult."""

    _mb_arbitrated = LibraryMixin._mb_arbitrated
    _mb_enqueue = LibraryMixin._mb_enqueue
    _mb_arbiter_on = LibraryMixin._mb_arbiter_on

    def __init__(self, *, on=True):
        self._prefs = {"library_enabled": on, "library_mb_arbiter": on}
        self._queued = []

    def _waves_pref_bool(self, key):
        return bool(self._prefs.get(key, False))

    # The enqueue resolves the arbiter on the GUI thread (so racing first
    # lookups cannot each build one); the captured worker never runs here, so
    # a marker object is all the stub needs.
    def _mb_arbiter_instance(self):
        return object()

    # Capture instead of spawning workers: the queueing decision is what is
    # under test, the arbiter itself has its own hermetic suite.
    class _Pool:
        def __init__(self, sink):
            self._sink = sink

        def start(self, worker):
            self._sink.append(worker)

    @property
    def threadpool(self):
        return self._Pool(self._queued)


def _unproven():
    return {
        "present": True,
        "partial": True,
        "sure": False,
        "full": True,
        "local_album_id": "/lib/A/Album",
        "local_tracks": 12,
        "local_runtime": 2400,
        "local_year": "",
    }


ARGS = ("Album", "Artist", "", 12, 2400)


def _key(stub):
    return (
        matching.presence_key("Album", "Artist"),
        "",
        12,
        2400,
        "/lib/A/Album",
        12,
        2400,
    )


def test_stored_proof_upgrades_sure_and_partial():
    s = _Stub()
    s._mb_verdicts = {_key(s): True}
    got = s._mb_arbitrated(_unproven(), *ARGS)
    assert got["sure"] is True
    # Identity proven and coverage already full: the strict bar clears too.
    assert got["partial"] is False
    assert s._queued == []  # answered from the map, nothing spawned


def test_stored_refusal_changes_nothing():
    s = _Stub()
    s._mb_verdicts = {_key(s): False}
    got = s._mb_arbitrated(_unproven(), *ARGS)
    assert got["sure"] is False and got["partial"] is True
    assert s._queued == []


def test_upgrade_never_clears_partial_when_coverage_is_short():
    s = _Stub()
    s._mb_verdicts = {_key(s): True}
    verdict = dict(_unproven(), full=False)
    got = s._mb_arbitrated(verdict, *ARGS)
    assert got["sure"] is True and got["partial"] is True  # short is short


def test_unknown_key_queues_one_lookup_and_answers_unproven():
    s = _Stub()
    got = s._mb_arbitrated(_unproven(), *ARGS)
    assert got["sure"] is False  # never blocked on the network
    assert len(s._queued) == 1
    # Asking again while pending queues nothing new.
    s._mb_arbitrated(_unproven(), *ARGS)
    assert len(s._queued) == 1


def test_proven_and_absent_verdicts_are_left_alone():
    s = _Stub()
    proven = dict(_unproven(), sure=True, partial=False)
    assert s._mb_arbitrated(proven, *ARGS) is proven
    absent = {"present": False, "sure": False}
    assert s._mb_arbitrated(absent, *ARGS) is absent
    assert s._queued == []


def test_pref_off_is_inert():
    s = _Stub(on=False)
    s._mb_verdicts = {_key(s): True}  # even a stored proof is ignored
    got = s._mb_arbitrated(_unproven(), *ARGS)
    assert got["sure"] is False
    assert s._queued == []


def test_master_switch_off_is_inert():
    s = _Stub()
    s._prefs["library_enabled"] = False
    got = s._mb_arbitrated(_unproven(), *ARGS)
    assert got["sure"] is False and s._queued == []


def test_a_rescanned_copy_misses_the_old_key():
    # The key carries the local copy's identity facts: a rescan that changed
    # the folder re-arbitrates instead of reusing a stale proof.
    s = _Stub()
    s._mb_verdicts = {_key(s): True}
    changed = dict(_unproven(), local_runtime=2500)
    got = s._mb_arbitrated(changed, *ARGS)
    assert got["sure"] is False  # old proof not applied
    assert len(s._queued) == 1  # and a fresh lookup went out


def test_bulk_gate_never_reads_the_overlay():
    # The engine-side claim helpers call the matcher directly; the overlay
    # method must appear in exactly one place: the presence slot.
    import inspect

    import tidaler.waves_ui.bridge_library as bl

    src = inspect.getsource(bl)
    assert src.count("self._mb_arbitrated(") == 1
    claims = inspect.getsource(bl.LibraryMixin._library_claims_album)
    assert "_mb" not in claims
