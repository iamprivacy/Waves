"""A queue row states a quality, and states the RIGHT one.

WHAT THIS FENCES OFF
--------------------
Two ways the drawer's quality readout goes quiet or goes wrong, both silent.

1. The field never arriving. The bridge puts `quality` on every queue row, but
   the drawer's model is a ListModel, and a ListModel fixes its roles from the
   first object appended to it. A field the reconcile forgets to name therefore
   does not exist on ANY row: `model.quality` reads undefined in the delegate,
   with no warning and no binding error, and the pill silently renders nothing.
   That shipped: every row was carrying a tier the drawer could not see.

2. The field arriving and being outranked by a stale promise. The pill starts
   out saying what the job ASKED for, which is all there is to say until the
   files land. Once they land they are the truth, and the case that matters is
   a release with no hi-res master answering a HI-RES request in LOSSLESS from
   end to end. MIXED cannot catch that (one tier is not a mix), so without an
   explicit "they all agree, and they agree on something else" the pill kept
   stating the request while the ledger under it stated the delivery.

3. Both of those readouts only working once the row is EXPANDED. They were fed
   from root.queueTracks[qid], filled by loadQueueTracks, which is a network
   fetch and so only runs on expand. A row nobody opened kept advertising its
   request forever and could never say MIXED, which is exactly the row that
   needed to: nobody was reading its ledger, so the pill was the only place the
   downgrade could have shown. The bridge now rolls its per-track registry up
   onto the row itself, and this pins that, by never expanding the row at all
   and asserting the track list stays unloaded while the pill still follows.

4. The expanded ledger stating a tier only once a track's turn comes. A track
   that has not started is "pending", which is what a fetched track list starts
   in and so what a queued album is made of from end to end, and no track event
   ever says "queued" (the relay emits running/failed/done/skipped only). Read
   the request for "queued" and "running" alone, as it was, and the tier column
   is blank for every track but the one in flight, which is the stretch where
   the request is the only thing there is to say.

So this drives the REAL bridge, the REAL reconcile and the REAL delegate: it
enqueues through the bridge, reads the tier off the instantiated row, then
reports deliveries through the bridge's own _track_lifecycle (the one path a
delivered tier is ever learned on) and watches the pill follow them.

Runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"


def test_a_queue_row_states_the_quality_it_is_being_fetched_at():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-queue-quality-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert (
        proc.returncode == _EXIT_OK
    ), f"the queue drawer's quality readout regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    # THIS checkout's waves, not the venv's editable install.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
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

    # An album job, enqueued through the bridge, asking for hi-res.
    bridge._target_tier = lambda: "HI-RES"
    qid = bridge._enqueue("Album A", "album", media_id="m1", collection=True, tracks=3)
    if bridge._queue_item(qid).get("quality") != "HI-RES":
        print("the bridge did not put the target tier on the row", file=sys.stderr)
        return _EXIT_PRECONDITION
    bridge.queueChanged.emit(list(bridge._queue))
    settle(120)

    def pill_tier() -> str:
        """The tier the instantiated row actually renders."""
        return str(q("(function(){ var it = queueList.itemAtIndex(0); return it ? '' + it.tier : '<no row>' })()"))

    def pill_mix() -> int:
        return int(q("(function(){ var it = queueList.itemAtIndex(0); return it ? it.tierMix.length : -1 })()"))

    def deliver(track_id: int, tier: str, status: str = "done") -> None:
        """Report one track the way a real download does: through the bridge's
        own lifecycle handler, which is where a delivered tier is learned and
        where the row's rollup is kept. No `path` on the event, so the ownership
        write no-ops rather than touching this machine."""
        bridge._track_lifecycle(
            qid,
            {
                "id": str(track_id),
                "num": track_id,
                "title": f"t{track_id}",
                "status": status,
                "quality": {"tier": tier},
            },
        )
        settle(60)

    bad = []

    # 1. The field survives the bridge -> ListModel hop at all. This is the
    #    exact assertion whose absence let an invisible role ship.
    on_the_row = str(q("'' + queueModel.get(0).quality"))
    if on_the_row != "HI-RES":
        bad.append(f"the row's quality did not reach the model (got {on_the_row!r}, want 'HI-RES')")

    # 2. Before any file lands, the asked-for tier is all there is to say.
    if pill_tier() != "HI-RES":
        bad.append(f"a queued row did not state the tier it is asking for (pill={pill_tier()!r})")

    # The row is NEVER expanded from here on. Everything below therefore rides
    # the bridge's rollup, not the expansion's track fetch, which is the whole
    # point: loadQueueTracks is never called, so root.queueTracks stays empty.

    # 3. The release has no hi-res master and every track lands LOSSLESS. The
    #    delivery outranks the request: this is the downgrade the readout exists
    #    to show, and no MIXED will ever flag it.
    for i in (1, 2, 3):
        deliver(i, "LOSSLESS")
    if pill_tier() != "LOSSLESS":
        bad.append(f"a uniform downgrade kept advertising the request (pill={pill_tier()!r}, want 'LOSSLESS')")
    if pill_mix() != 0:
        bad.append(f"tracks that agree are not a mix (tierMix length={pill_mix()})")

    # 4. Genuinely mixed tiers still win: the pill drops its single word and
    #    hands over to MIXED, again with the row shut.
    deliver(2, "HI-RES")
    if pill_mix() != 2:
        bad.append(f"disagreeing tiers did not read as MIXED (tierMix length={pill_mix()})")
    if pill_tier() != "":
        bad.append(f"a mixed row must not also claim one tier (pill={pill_tier()!r})")

    # 5. And prove the readouts above really were unexpanded. Without this the
    #    test would keep passing if the pill quietly went back to depending on
    #    the expansion, since a later expand would load the same tiers.
    if not bool(q(f"root.queueTracks[{qid}] === undefined")):
        bad.append("the row's track list was loaded, so the readout above did not prove the collapsed case")

    # 6. A retried track that lands at a different tier makes the album uniform
    #    again. The rollup is recomputed, never accumulated, so MIXED clears.
    deliver(2, "LOSSLESS")
    if pill_mix() != 0 or pill_tier() != "LOSSLESS":
        bad.append(f"a re-delivered track left MIXED stuck (pill={pill_tier()!r}, mix={pill_mix()})")

    # 7. Expanded, a track states a tier BEFORE its turn comes: the request,
    #    faded, until a file lands at one. The state to get right is "pending",
    #    which is what a fetched track list starts in and therefore what a
    #    queued album is made of end to end (no track event ever says "queued":
    #    the relay emits running/failed/done/skipped only). While the ledger
    #    read the request for "queued" and "running" alone, the column sat
    #    blank for every track that had not started, which is most of them for
    #    most of the download.
    bridge._merge_queue_tracks(
        qid,
        [
            {"id": "1", "num": 1, "title": "t1", "duration": "3:00"},
            {"id": "9", "num": 9, "title": "t9", "duration": "3:00"},
        ],
    )
    q(f"root.queueExpanded = ({{ {qid}: true }})")
    settle(400)
    # Every tier cell the expanded row renders, in order, with whether it is
    # showing faded (a promise) or full (a delivery).
    ledger = str(q("""(function () {
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
    # Track 1 landed (registry says LOSSLESS, full strength); track 9 has not
    # started (no registry row, so _merge_queue_tracks calls it pending) and
    # states the job's own HI-RES request, faded.
    if ledger != "LOSSLESS@full | HI-RES@faded":
        bad.append(f"the expanded ledger's tier column read {ledger!r}, want 'LOSSLESS@full | HI-RES@faded'")

    # 8. A row that leaves takes its expansion state with it, not just its
    #    track list: same per-qid state, same leak if it outlives its row.
    q(f"root.queueExpanded = ({{ {qid}: true }})")
    q("reconcileQueue([])")
    settle(120)
    if bool(q(f"root.queueExpanded[{qid}] !== undefined")):
        bad.append("a removed row left its expansion flag behind")

    if bad:
        print("\n".join(bad), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok: the pill follows the delivery, not the promise")
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
