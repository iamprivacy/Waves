"""A queued row's ledger says which tracks you already have, before the run.

WHAT THIS FENCES OFF
--------------------
The ledger learned that a track was already yours only when the download
reached it: _emit_skip fired, _track_lifecycle recorded it, and the row turned
IN LIBRARY one at a time as the job walked the list. Expanding a row that had
not started yet showed every track as QUEUED, including the ones the run was
certain to skip, so the drawer could not answer the question people actually
open it to ask ("what is this download going to do?").

The expansion now predicts them: after the track list is on screen, a worker
asks the same two gates the download asks and marks the tracks the run will
find you already hold. The marks are a PREDICTION, so they wear the drawer's
prediction strength (half) until the run reaches the track and confirms it,
exactly like the tier cell's promised-vs-delivered convention.

The prediction must ask the download's question, not a similar one, or the
drawer promises a skip that never happens. What is pinned here:

* ownership skips only at equal-or-better quality: an owned copy BELOW the
  job's quality is an upgrade, and predicts nothing;
* the quality compared against is the JOB's, not the current setting, so a
  row queued at HI-RES keeps its answer when Settings moves afterwards;
* the library's tag claim rides only on a collection job with the bulk-skip
  pref on, and a DOWNLOAD ANYWAY override turns it off;
* a redownload forces every item and predicts nothing at all;
* a merge job predicts nothing (its members are filed under identity ids and
  may only be skipped at this job's own destination);
* a live event always wins over a prediction;
* the marks never touch the collapsed row's delivered rollup, which stays the
  record of what the run really did.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from tidalapi.media import Quality

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78


class _Store:
    """The ownership store's one method the prediction uses."""

    def __init__(self, recs):
        self.recs = recs
        self.asked = []

    def ownership_of(self, tid):
        self.asked.append(str(tid))
        return self.recs.get(str(tid))


def _bridge(*, recs=None, claim=None, quality=Quality.hi_res_lossless, atmos=False):
    """A WavesBridge carcass with only what _predict_skips reads, and the real
    method bound onto it."""
    from tidaler.waves_ui import backend

    b = backend.WavesBridge.__new__(backend.WavesBridge)
    b._ownership = _Store(recs or {})
    b._redownload_overrides = set()
    b._library_claim_overrides = set()
    b._merge_plans = {}
    b._objs = {"album": {}}
    b._queue_index = {}
    b.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=quality, download_dolby_atmos=atmos))
    # Not something the prediction may read: the bulk-skip pref is consulted
    # once, when the row is queued, and the prediction reads what the row kept
    # (see _job_library_skip). Wired to fail the test so a reader that went back
    # to the live setting is caught here rather than in the drawer.
    b._library_bulk_skip_on = lambda: pytest.fail("the prediction read the live pref instead of the row's pin")
    b._library_claim_media = claim if claim is not None else (lambda media, album=None: False)
    for name in ("_predict_skips", "_target_quality_rank", "_job_quality", "_job_library_skip", "_queue_item"):
        setattr(b, name, getattr(backend.WavesBridge, name).__get__(b, backend.WavesBridge))
    return b


def _row(qid=1, **kw):
    """A queue row as _enqueue builds it, carrying both of the values pinned
    there for the job's whole life: the quality it will ask for and whether the
    library scan's claim may skip its tracks."""
    row = {
        "qid": qid,
        "media_id": "m1",
        "type": "album",
        "collection": True,
        "askQuality": "",
        "askLibrarySkip": True,
        **kw,
    }
    return row


def _predict(b, qid, row, tracks):
    """Predict through the real row lookup, the shape the production call has:
    the claim gate lives ON the row, put there by _enqueue, so a bridge whose
    queue index does not hold the row reads the gate as off and marks nothing."""
    b._queue_index[int(qid)] = row
    return b._predict_skips(int(qid), row, tracks)


def _tracks(*ids):
    return [SimpleNamespace(id=i, name=f"t{i}", duration=200) for i in ids]


def _own(tier, rank):
    return {"owned": True, "quality_tier": tier, "quality_rank": rank}


# ---- the gate, mirrored ----------------------------------------------------------
def test_an_owned_copy_at_the_jobs_quality_is_predicted_as_a_skip():
    b = _bridge(recs={"1": _own("LOSSLESS", 2)}, quality=Quality.high_lossless)
    marks = _predict(b, 1, _row(), _tracks("1", "2"))
    assert marks == {"1": {"kind": "own", "tier": "LOSSLESS"}}, marks


def test_an_owned_copy_below_the_jobs_quality_is_an_upgrade_not_a_skip():
    """The download would re-fetch and overwrite it, so the ledger must not
    say IN LIBRARY: that row is going to be downloaded."""
    b = _bridge(recs={"1": _own("HIGH", 1)}, quality=Quality.hi_res_lossless)
    assert _predict(b, 1, _row(), _tracks("1")) == {}


def test_a_tier_less_record_is_a_skip():
    """Rank -1 is a video: no quality concept, nothing to upgrade to."""
    b = _bridge(recs={"1": {"owned": True, "quality_tier": "", "quality_rank": -1}})
    assert _predict(b, 1, _row(), _tracks("1")) == {"1": {"kind": "own", "tier": ""}}


def test_the_quality_compared_against_is_the_jobs_not_the_setting():
    """The row was queued at HIGH and will be fetched at HIGH, so a HIGH copy
    is a skip even though Settings has since moved to hi-res. Reading the
    setting here would call it an upgrade and the drawer would disagree with
    the run."""
    b = _bridge(recs={"1": _own("HIGH", 1)}, quality=Quality.hi_res_lossless)
    row = _row(askQuality=Quality.low_320k.value)
    b._queue_index = {1: row}
    assert _predict(b, 1, row, _tracks("1")) == {"1": {"kind": "own", "tier": "HIGH"}}


# ---- the claim gate --------------------------------------------------------------
def test_the_library_claim_marks_a_track_gold():
    b = _bridge(claim=lambda media, album=None: {"present": True, "local_class": "lossless"})
    marks = _predict(b, 1, _row(), _tracks("1"))
    assert marks == {"1": {"kind": "claim", "tier": "LOSSLESS"}}, marks


def test_a_single_item_job_never_consults_the_claim():
    """The claim rides only on collection jobs, as the engine wires it."""
    b = _bridge(claim=lambda media, album=None: {"present": True, "local_class": "lossless"})
    assert _predict(b, 1, _row(collection=False), _tracks("1")) == {}


def test_the_bulk_skip_pref_being_off_turns_the_claim_off():
    """Off when the row was queued, so off for this job however the preference
    moves afterwards."""
    b = _bridge(claim=lambda media, album=None: {"present": True, "local_class": "lossless"})
    assert _predict(b, 1, _row(askLibrarySkip=False), _tracks("1")) == {}


def test_download_anyway_on_this_album_turns_the_claim_off():
    b = _bridge(claim=lambda media, album=None: {"present": True, "local_class": "lossless"})
    b._library_claim_overrides.add("m1")
    assert _predict(b, 1, _row(), _tracks("1")) == {}


def test_an_owned_record_stops_the_claim_being_asked():
    """Ownership answers first and its answer is final, both when it skips and
    when it forces an upgrade."""
    calls = []

    def claim(media, album=None):
        calls.append(getattr(media, "id", ""))
        return {"present": True, "local_class": "high"}

    b = _bridge(recs={"1": _own("HIGH", 1)}, claim=claim, quality=Quality.hi_res_lossless)
    assert _predict(b, 1, _row(), _tracks("1")) == {}
    assert calls == [], "an owned-but-outdated track asked the claim gate as well"


# ---- jobs that predict nothing ---------------------------------------------------
def test_a_redownload_predicts_nothing():
    b = _bridge(recs={"1": _own("LOSSLESS", 2)}, quality=Quality.high_lossless)
    b._redownload_overrides.add("m1")
    assert _predict(b, 1, _row(), _tracks("1")) == {}


def test_a_merge_job_predicts_nothing():
    b = _bridge(recs={"1": _own("LOSSLESS", 2)}, quality=Quality.high_lossless)
    b._merge_plans["m1"] = [object()]
    assert _predict(b, 1, _row(), _tracks("1")) == {}


def test_an_ownership_lookup_failure_never_gates():
    class _Boom:
        def ownership_of(self, tid):
            raise RuntimeError("mount is gone")

    b = _bridge()
    b._ownership = _Boom()
    assert _predict(b, 1, _row(), _tracks("1")) == {}


# ---- the overlay -----------------------------------------------------------------
def test_a_live_event_outranks_a_prediction():
    """The run reached the track and said something: that is fact, and the
    prediction must not paint over it."""
    from tidaler.waves_ui import backend

    b = backend.WavesBridge.__new__(backend.WavesBridge)
    b._job_tracks = {1: {"1": {"id": "1", "status": "running", "pct": 40.0, "quality": ""}}}
    b._job_owned = {1: {"1": {"kind": "own", "tier": "LOSSLESS"}, "2": {"kind": "claim", "tier": "HIGH"}}}
    b._job_fetched = {}
    emitted = []
    b.queueTracksLoaded = SimpleNamespace(emit=lambda qid, rows: emitted.append(rows))
    backend.WavesBridge._merge_queue_tracks(
        b,
        1,
        [
            {"id": "1", "num": 1, "title": "a", "duration": "3:00"},
            {"id": "2", "num": 2, "title": "b", "duration": "3:00"},
        ],
    )
    rows = emitted[-1]
    assert rows[0]["status"] == "running", "a prediction overwrote a running track"
    assert rows[0]["quality"] == ""
    assert rows[1]["status"] == "owned", rows[1]
    assert rows[1]["owned"] == "claim" and rows[1]["quality"] == "HIGH"


def test_marks_landing_after_the_list_are_merged_into_it():
    from tidaler.waves_ui import backend

    b = backend.WavesBridge.__new__(backend.WavesBridge)
    b._job_tracks = {}
    b._job_owned = {}
    b._job_fetched = {}
    emitted = []
    b.queueTracksLoaded = SimpleNamespace(emit=lambda qid, rows: emitted.append(rows))
    fetched = [{"id": "1", "num": 1, "title": "a", "duration": "3:00"}]
    backend.WavesBridge._merge_queue_tracks(b, 1, fetched)
    assert emitted[-1][0]["status"] == "pending"
    backend.WavesBridge._apply_owned_marks(b, 1, {"1": {"kind": "own", "tier": "HI-RES"}})
    assert emitted[-1][0]["status"] == "owned"
    assert emitted[-1][0]["quality"] == "HI-RES"


def test_a_prediction_never_reaches_the_collapsed_rows_rollup():
    """The row's word is the record of what the RUN delivered (including the
    tier of a copy it really skipped). A prediction is not a delivery and must
    leave it alone until the run speaks."""
    from tidaler.waves_ui import backend

    b = backend.WavesBridge.__new__(backend.WavesBridge)
    b._job_tracks = {}
    b._job_owned = {1: {"1": {"kind": "own", "tier": "LOSSLESS"}}}
    b._job_fetched = {}
    b.queueTracksLoaded = SimpleNamespace(emit=lambda *a: None)
    backend.WavesBridge._merge_queue_tracks(b, 1, [{"id": "1", "num": 1, "title": "a", "duration": "3:00"}])
    assert backend._delivered_rollup(b._job_tracks.get(1, {})) == ("", [])


def test_the_stores_are_dropped_with_the_queue_row():
    from tidaler.waves_ui import backend

    b = backend.WavesBridge.__new__(backend.WavesBridge)
    b._queue = [{"qid": 2}]
    b._job_tracks = {1: {}, 2: {}}
    b._job_owned = {1: {}, 2: {}}
    b._job_fetched = {1: [], 2: []}
    backend.WavesBridge._prune_job_tracks(b)
    assert set(b._job_owned) == {2} and set(b._job_fetched) == {2}


# ---- the real drawer -------------------------------------------------------------
def test_the_drawer_says_in_library_before_the_run_and_fills_in_after():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-queue-owned-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the predicted IN LIBRARY row is wrong:\n{tail}"


def _run_scenario() -> int:  # (one straight line of scene setup)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()
    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from tidaler.waves_ui.app import _load_mono
        from tidaler.waves_ui.backend import WavesBridge
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    engine = QQmlApplicationEngine()
    bridge = WavesBridge(tidal=None)
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", _load_mono())
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    roots = engine.rootObjects()
    if not roots:
        print("Main.qml failed to load", file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle(120)
    q(PARK_LOGIN_QML)
    q("queueDrawer.open()")
    settle(120)
    if not bool(q("queueDrawer.visible")):
        print("the queue drawer would not open", file=sys.stderr)
        return _EXIT_PRECONDITION

    qid = bridge._enqueue("Owned Album", "album", media_id="m1", collection=True, tracks=3)
    bridge.queueChanged.emit(list(bridge._queue))
    settle(150)
    bridge._merge_queue_tracks(
        qid,
        [{"id": str(i), "num": i, "title": f"track {i}", "duration": "3:00"} for i in (1, 2, 3)],
    )
    settle(150)
    # Track 1 is Waves' own copy, track 2 a tag match, track 3 not held.
    bridge._apply_owned_marks(
        qid,
        {"1": {"kind": "own", "tier": "LOSSLESS"}, "2": {"kind": "claim", "tier": "HIGH"}},
    )
    q(f"root.queueExpanded = ({{ {qid}: true }})")
    settle(500)

    def ledger() -> str:
        return str(q("""(function () {
                var out = [];
                function walk(o) {
                    if (!o) return;
                    if (o.objectName === 'qTrackWord')
                        out.push(('' + o.text) + '@' + (o.opacity < 0.9 ? 'faded' : 'full'));
                    var kids = o.children;
                    for (var i = 0; i < (kids ? kids.length : 0); ++i) walk(kids[i]);
                }
                walk(queueList.itemAtIndex(0));
                return out.join(' | ');
            })()"""))

    def tiers() -> str:
        return str(q("""(function () {
                var out = [];
                function walk(o) {
                    if (!o) return;
                    if (o.objectName === 'qTrackTier' && o.visible) out.push('' + o.text);
                    var kids = o.children;
                    for (var i = 0; i < (kids ? kids.length : 0); ++i) walk(kids[i]);
                }
                walk(queueList.itemAtIndex(0));
                return out.join(' | ');
            })()"""))

    bad: list[str] = []
    got = ledger()
    want = "IN LIBRARY@faded | IN LIBRARY@faded | QUEUED@full"
    if got != want:
        bad.append(f"before the run the ledger read {got!r}, want {want!r}")
    got = tiers()
    if not got.startswith("LOSSLESS | HIGH"):
        bad.append(f"the predicted rows state the tiers {got!r}, want the held copies' LOSSLESS then HIGH")

    # The run reaches track 1 and really skips it: the prediction becomes fact.
    bridge._track_lifecycle(
        qid, {"id": "1", "title": "track 1", "status": "skipped", "owned": "own", "quality": "LOSSLESS"}
    )
    settle(300)
    got = ledger()
    want = "IN LIBRARY@full | IN LIBRARY@faded | QUEUED@full"
    if got != want:
        bad.append(f"after track 1 was really skipped the ledger read {got!r}, want {want!r}")

    if bad:
        for b in bad:
            print(b, file=sys.stderr)
        return _EXIT_REGRESSED
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
