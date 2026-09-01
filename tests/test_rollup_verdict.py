"""The one verdict behind the DOWNLOADED / IN LIBRARY state on every album,
playlist and mix card: ``WavesBridge._rollup_verdict`` (backend.py), reached
through ``collectionOwnership`` and ``collectionOwnershipFor``.

The stake is the quality conjunct. A member is only counted as "owned" for the
roll-up when its copy is ALSO up_to_date against the current audio quality
setting. Drop that conjunct and an album saved at HIGH keeps reading "owned"
after the user raises the setting to Lossless, so the upgrade the card is
supposed to offer is never offered. Nothing else in the suite pinned it.

Two layers:

  * the pure roll-up, called unbound on a Qt-free stub whose ownershipOf is a
    plain dict lookup, one test per branch of the loop, and
  * one wire test through the REAL ownershipOf on a WavesBridge carcass with a
    real OwnershipStore, so the stored quality_rank is what decides the verdict
    (not a dict the test invented).

These import WavesBridge, so they collect only in the full runtime venv
(PySide6 present), like tests/test_ownership_bridge.py.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

from waves.ownership import OwnershipStore
from waves.waves_ui.backend import WavesBridge

OWNED_CURRENT = {"owned": True, "up_to_date": True}
OWNED_STALE = {"owned": True, "up_to_date": False}
NOT_OWNED = {"owned": False}
PENDING = {"owned": False, "pending": True}


class _LookupStub:
    """A stand-in whose ownershipOf is a dict lookup, so each test states the
    per-member answers outright and only the roll-up logic is under test.
    Unknown ids answer like a firm, refreshed "not owned"."""

    def __init__(self, answers: dict):
        self._answers = {str(k): v for k, v in answers.items()}
        self.asked: list[str] = []

    def ownershipOf(self, tid):
        self.asked.append(tid)
        return self._answers.get(str(tid), NOT_OWNED)


def _verdict(answers: dict, ids=None) -> str:
    """The REAL _rollup_verdict, unbound, over the given per-member answers."""
    stub = _LookupStub(answers)
    return WavesBridge._rollup_verdict(stub, list(answers) if ids is None else ids)


# --------------------------------------------------------------------------- #
# The pure roll-up, one branch at a time.
# --------------------------------------------------------------------------- #
def test_every_member_owned_and_current_reads_owned():
    assert _verdict({"1": OWNED_CURRENT, "2": OWNED_CURRENT, "3": OWNED_CURRENT}) == "owned"


def test_an_owned_but_out_of_date_member_reads_no():
    """The quality conjunct itself: owned is not enough, the copy must also be
    current against today's setting, or the card must offer the upgrade."""
    assert _verdict({"1": OWNED_STALE}) == "no"


def test_one_stale_member_demotes_a_collection_of_current_ones():
    """N tracks at the target quality and ONE below it: the album card must
    not read as downloaded, since a download would still fetch that one."""
    answers = {str(i): OWNED_CURRENT for i in range(1, 12)}
    answers["7"] = OWNED_STALE
    assert _verdict(answers) == "no"


def test_a_member_not_owned_reads_no():
    assert _verdict({"1": OWNED_CURRENT, "2": NOT_OWNED, "3": OWNED_CURRENT}) == "no"


def test_a_pending_member_among_current_ones_reads_pending():
    """Nothing firmly against, one cold query still in flight: "pending", so
    the card holds its roll animation instead of flashing DOWNLOAD first."""
    assert _verdict({"1": OWNED_CURRENT, "2": PENDING, "3": OWNED_CURRENT}) == "pending"


def test_a_stale_member_outranks_a_pending_one():
    """A stale copy is a firm "no" whatever a pending member turns out to be:
    the download would fetch the stale track regardless. The loop only NOTES a
    pending member and keeps going, so it reaches the stale one and returns
    "no" on it; with the stale member first it returns "no" before the pending
    one is even asked. Both orders are pinned so neither can regress into
    "pending" (which would hold the card in its waiting state, or worse, into
    "owned" once the cold answer lands)."""
    assert _verdict({"1": PENDING, "2": OWNED_STALE}) == "no"
    assert _verdict({"1": OWNED_STALE, "2": PENDING}) == "no"


def test_no_members_reads_no():
    """An empty or unknown collection is not "owned": nothing to have."""
    assert _verdict({}, ids=[]) == "no"
    assert _verdict({}, ids=None) == "no"


def test_collection_ownership_for_stringifies_ids_and_rolls_up():
    """QML hands the member list over as a QVariantList that may carry ints;
    the store keys ids as strings, so the slot must stringify before asking."""
    stub = _LookupStub({"10": OWNED_CURRENT, "20": OWNED_CURRENT})
    stub._rollup_verdict = WavesBridge._rollup_verdict.__get__(stub, _LookupStub)
    assert WavesBridge.collectionOwnershipFor(stub, [10, 20]) == "owned"
    assert stub.asked == ["10", "20"], "ids must reach ownershipOf as strings"
    stub = _LookupStub({"10": OWNED_CURRENT, "20": OWNED_STALE})
    stub._rollup_verdict = WavesBridge._rollup_verdict.__get__(stub, _LookupStub)
    assert WavesBridge.collectionOwnershipFor(stub, [10, 20]) == "no"
    assert WavesBridge.collectionOwnershipFor(stub, []) == "no"


# --------------------------------------------------------------------------- #
# The wire: stored quality_rank -> real ownershipOf -> roll-up.
# --------------------------------------------------------------------------- #
class _InlinePool:
    def start(self, worker):
        worker.run()


def _bridge(store, *, quality):
    """The real ownershipOf/_rollup_verdict on a WavesBridge carcass (the same
    shape tests/test_atmos_ownership_scale.py builds), so up_to_date is
    computed by production against the real _target_quality_rank."""
    b = WavesBridge.__new__(WavesBridge)
    b._ownership = store
    b._own_cache = {}
    b._own_lock = Lock()
    b._own_pending = set()
    b._own_pool = _InlinePool()
    b._announce_ownership = lambda tid: None
    b._downloads_running = lambda: False
    b.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=quality, download_dolby_atmos=False))
    for name in (
        "ownershipOf",
        "_would_refetch_atmos",
        "_target_quality_rank",
        "_own_refresh",
        "_evict_own_cache_locked",
        "_rollup_verdict",
        "collectionOwnershipFor",
    ):
        setattr(b, name, getattr(WavesBridge, name).__get__(b, WavesBridge))
    return b


def _file(tmp_path, name):
    p = tmp_path / name
    p.write_text("audio")
    return str(p)


def _warm(bridge, ids):
    """Land every member's background refresh: the first ownershipOf on a cold
    id schedules the (inline) refresh and answers pending, the next reads the
    real record. The roll-up under test then sees firm answers only."""
    for tid in ids:
        bridge.ownershipOf(tid)
        bridge.ownershipOf(tid)


def test_a_member_stored_below_the_target_rank_un_says_the_album(tmp_path):
    """Two tracks saved at HI_RES_LOSSLESS (rank 3) and one at HIGH (rank 1),
    with the setting at HI_RES_LOSSLESS: the stored rank alone must make the
    album read "no", and once that track is re-recorded at rank 3, "owned".
    This pins the wire from the sqlite row to the card, not the pure function."""
    store = OwnershipStore(str(tmp_path / "ownership.sqlite3"))
    store.record("101", _file(tmp_path, "01.flac"), "HI_RES_LOSSLESS")
    store.record("102", _file(tmp_path, "02.flac"), "HI_RES_LOSSLESS")
    store.record("103", _file(tmp_path, "03.m4a"), "HIGH")
    b = _bridge(store, quality="HI_RES_LOSSLESS")
    assert b._target_quality_rank() == 3
    ids = ["101", "102", "103"]
    # Cold: every member is still being answered, nothing firmly against.
    assert b._rollup_verdict(ids) == "pending"
    _warm(b, ids)
    assert b.ownershipOf("103")["owned"] is True, "the HIGH copy IS owned; only its quality is behind"
    assert b.ownershipOf("103")["up_to_date"] is False
    assert b._rollup_verdict(ids) == "no"
    assert b.collectionOwnershipFor([101, 102, 103]) == "no"

    # The upgrade lands: a rank-3 copy of the third track is recorded, and the
    # cache entries are aged out so the next query re-reads the store.
    store.record("103", _file(tmp_path, "03.flac"), "HI_RES_LOSSLESS")
    for tid in ids:
        b._own_cache[tid] = (-1e9, b._own_cache[tid][1])
    _warm(b, ids)
    assert b._rollup_verdict(ids) == "owned"
    assert b.collectionOwnershipFor([101, 102, 103]) == "owned"
