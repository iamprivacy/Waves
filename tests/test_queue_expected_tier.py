"""The queue predicts a tier honestly: the request floored by the catalog.

WHAT THIS FENCES OFF
--------------------
Before a file lands, the drawer used to state the tier the SETTING asked for,
per row and per track. TIDAL advertises every track's and release's ceiling
(``media_metadata_tags`` / ``audio_quality``, read by ``_quality_label``, the
same source as the quality pills on search results), so a lossless-only album
queued under a HI-RES setting was promising HI-RES that its first delivery
contradicted, and a playlist mixing hi-res and lossless-only tracks read
HI-RES down the whole column until each track "changed" to LOSSLESS.

Now:

* every queue row carries ``expected`` (the release's advertised ceiling) and
  every ledger track carries its own, from the fetched list, the merge seed,
  or the running event;
* the QML states ``tierFloor(request, expected)`` until a delivery outranks
  it (``queueTrackTier`` for tracks, the row's ``tier`` for the pill);
* a row's prediction is the quality it was QUEUED at and never moves: each
  job pins that quality (``askQuality``, applied to the session inside the
  stream lock) so a change in Settings retargets nothing already queued or
  running, and applies to what is queued next. Pinned here and in
  test_quality_pinned_per_job.py.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Lock

import pytest

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78


# ---- bridge side --------------------------------------------------------------
class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *a):
        self.calls.append(a)


class _Stub:
    """Just enough bridge for the registry."""

    def __init__(self, target="HI-RES"):
        from tidaler.waves_ui import backend

        self._job_tracks = {}
        # The ledger merge also overlays an expansion's predicted skips
        # (test_queue_owned_prediction.py); empty here, so every row in
        # this scenario is the live registry's answer alone.
        self._job_owned = {}
        self._job_fetched = {}
        self._job_signals = {}
        self._queue = []
        self._queue_index = {}
        self._outcome_lock = Lock()
        self.queueTrackState = _Signal()
        self.emits = 0
        self._emit_queue = lambda: setattr(self, "emits", self.emits + 1)
        # A "done" event hands ownership recording to a pool; inline no-op here.
        self._own_pool = type("P", (), {"start": lambda self_, w: None})()
        self.settings = type("S", (), {"data": type("D", (), {"download_base_path": ""})()})()
        self._target_tier = lambda: target
        for name in ("_track_lifecycle", "_queue_item"):
            setattr(self, name, getattr(backend.WavesBridge, name).__get__(self, _Stub))


def test_a_setting_change_leaves_every_queued_row_alone():
    """The bridge has no retarget at all any more: a queued row's request is
    what it was queued at, and applySettings must not reach into the queue."""
    from tidaler.waves_ui import backend

    assert not hasattr(backend.WavesBridge, "_retarget_unfinished_rows")
    src = inspect.getsource(backend.WavesBridge.applySettings)
    block = src[src.index('if "quality_audio" in values:') :]
    assert "retarget" not in block.replace("NOT retarget", ""), block[:400]
    # Not by name. A retarget written inline, whatever it were called, would
    # have to reach the queue store or the per-track registry, or republish
    # the drawer's model, and applySettings does none of that anywhere in its
    # body (a comment may mention the row's askQuality; code may not touch
    # it). One hidden behind a helper is caught by the drawer scenario below,
    # which reads the target from the real setting and watches the pill.
    code = "\n".join(line for line in src.splitlines() if not line.strip().startswith("#"))
    for touch in ("_queue", "_job_tracks", "_emit_queue", "queueChanged", "askQuality", "_target_tier"):
        assert touch not in code, f"applySettings reaches for {touch!r}: a setting change may not retarget the queue"


def test_running_event_seeds_the_track_ceiling_and_a_later_one_keeps_it():
    b = _Stub()
    b._track_lifecycle(1, {"id": "9", "title": "t", "status": "running", "expected": "LOSSLESS"})
    assert b._job_tracks[1]["9"]["expected"] == "LOSSLESS"
    b._track_lifecycle(1, {"id": "9", "status": "done"})  # the early done carries none
    assert b._job_tracks[1]["9"]["expected"] == "LOSSLESS"
    assert b.queueTrackState.calls[-1][1]["expected"] == "LOSSLESS"


def test_merge_carries_the_ceiling_from_the_fetch_and_from_the_registry():
    from tidaler.waves_ui import backend

    class _B(_Stub):
        def __init__(self):
            super().__init__()
            self.queueTracksLoaded = _Signal()
            self._merge_queue_tracks = backend.WavesBridge._merge_queue_tracks.__get__(self, _B)

    b = _B()
    b._job_tracks[1] = {"2": {"id": "2", "title": "b", "status": "running", "pct": 0.0, "expected": "HI-RES"}}
    b._merge_queue_tracks(
        1,
        [
            {"id": "1", "num": 1, "title": "a", "duration": "3:00", "expected": "LOSSLESS"},
            {"id": "2", "num": 2, "title": "b", "duration": "3:00"},
        ],
    )
    rows = b.queueTracksLoaded.calls[-1][1]
    assert [r["expected"] for r in rows] == ["LOSSLESS", "HI-RES"]
    b._merge_queue_tracks(1, [])  # registry alone
    assert [r["expected"] for r in b.queueTracksLoaded.calls[-1][1]] == ["HI-RES"]


def test_merge_seed_and_load_queue_tracks_read_the_catalog_ceiling(monkeypatch):
    from tidaler.waves_ui import backend

    monkeypatch.setattr(backend, "_quality_label", lambda o: getattr(o, "adv", ""))
    monkeypatch.setattr(backend, "name_builder_title", lambda t: getattr(t, "name", ""))
    src = backend.Track.__new__(backend.Track)
    src.id, src.name, src.duration, src.adv, src.artists, src.artist = "s", "S", 10, "LOSSLESS", [], None
    reg = backend._seed_merge_registry([(src, 1, 1, "i-1")])
    assert reg["i-1"]["expected"] == "LOSSLESS"


# ---- the real drawer -------------------------------------------------------------
def test_drawer_states_the_floor_and_holds_it():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-queue-expected-")
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
    assert proc.returncode == _EXIT_OK, f"the drawer's predicted tier is dishonest again:\n{tail}"


def _run_scenario() -> int:
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

    bad: list[str] = []
    # The pure function first: the floor in every direction.
    for req, ceil, want in (
        ("HI-RES", "LOSSLESS", "LOSSLESS"),
        ("LOSSLESS", "HI-RES", "LOSSLESS"),
        ("HI-RES", "", "HI-RES"),
        ("", "LOSSLESS", "LOSSLESS"),
        ("HIGH", "HI-RES", "HIGH"),
    ):
        got = str(q(f"root.tierFloor('{req}', '{ceil}')"))
        if got != want:
            bad.append(f"tierFloor({req!r}, {ceil!r}) = {got!r}, want {want!r}")

    # A lossless-only album, asked for in HI-RES. The target comes from the
    # REAL setting, not a frozen stub: a stub that always answers HI-RES
    # would let a retarget of any name floor to the same LOSSLESS and pass.
    # Read from the setting, a retarget after the change below would floor
    # HIGH against LOSSLESS and move the pill to HIGH, which is what the
    # assertions after the change exist to catch.
    bridge.applySettings({"quality_audio": "hi_res_lossless"})
    settle(120)
    if bridge._target_tier() != "HI-RES":
        print(f"the setting did not take: target reads {bridge._target_tier()!r}", file=sys.stderr)
        return _EXIT_PRECONDITION
    qid = bridge._enqueue("Album A", "album", media_id="m1", collection=True, tracks=2, expected="LOSSLESS")
    bridge.queueChanged.emit(list(bridge._queue))
    settle(120)
    row = "(function(){ var it = queueList.itemAtIndex(0); return it })()"
    if not bool(q(row + " !== null")):
        print("no drawer row", file=sys.stderr)
        return _EXIT_PRECONDITION
    tier = str(q(row + ".tier"))
    if tier != "LOSSLESS":
        bad.append(f"a lossless-only album queued under HI-RES states {tier!r} on its pill, want LOSSLESS")

    # Its ledger: track 1 is advertised LOSSLESS, track 2 has no advertised
    # ceiling (falls back to the request), neither has landed.
    bridge._merge_queue_tracks(
        qid,
        [
            {"id": "1", "num": 1, "title": "t1", "duration": "3:00", "expected": "LOSSLESS"},
            {"id": "2", "num": 2, "title": "t2", "duration": "3:00"},
        ],
    )
    q(f"root.queueExpanded = ({{ {qid}: true }})")
    settle(400)

    def ledger() -> str:
        return str(q("""(function () {
                var out = [];
                function walk(o) {
                    if (!o) return;
                    if (o.objectName === 'qTrackTier')
                        out.push(('' + o.text) + '@' + (o.opacity < 0.9 ? 'faded' : 'full'));
                    var kids = o.children;
                    for (var i = 0; i < (kids ? kids.length : 0); ++i) walk(kids[i]);
                }
                walk(queueList.itemAtIndex(0));
                return out.join(' | ');
            })()"""))

    got = ledger()
    if got != "LOSSLESS@faded | HI-RES@faded":
        bad.append(f"ledger before any delivery read {got!r}, want 'LOSSLESS@faded | HI-RES@faded'")

    # The setting drops to HIGH while the job is queued. Nothing in the
    # drawer may move: this row was queued at the old quality and will be
    # fetched at it (see test_quality_pinned_per_job.py).
    bridge.applySettings({"quality_audio": "low_320k"})
    settle(300)
    if bridge._target_tier() != "HIGH":
        print(f"the setting change did not take: target reads {bridge._target_tier()!r}", file=sys.stderr)
        return _EXIT_PRECONDITION
    tier = str(q(row + ".tier"))
    if tier != "LOSSLESS":
        bad.append(f"after the setting changed the pill states {tier!r}, want the queued LOSSLESS")
    got = ledger()
    if got != "LOSSLESS@faded | HI-RES@faded":
        bad.append(f"after the setting changed the ledger read {got!r}, want it unchanged")

    # A delivery still outranks every prediction.
    bridge._track_lifecycle(
        qid, {"id": "1", "num": 1, "title": "t1", "status": "done", "quality": {"tier": "LOSSLESS"}}
    )
    settle(300)
    got = ledger()
    if got != "LOSSLESS@full | HI-RES@faded":
        bad.append(f"after track 1 landed at LOSSLESS the ledger read {got!r}")

    if bad:
        for b in bad:
            print(b, file=sys.stderr)
        return _EXIT_REGRESSED
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
