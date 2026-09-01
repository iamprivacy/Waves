"""The failed row's reason reaches the drawer, not just the bridge (issue #35).

The bridge now stores WHY a download failed on the queue row ("6 of 501 tracks
failed"). That value is worth nothing unless the drawer can read it, and QML
fails at exactly this silently: a ListModel fixes its roles from the first
object appended, so a role missing from ``queueRowObject`` does not exist on
any row, reads as ``undefined`` in the delegate, and produces no warning and no
binding error. The reported symptom was a row whose entire diagnosis was the
word "Failed", so a check that only asked the bridge would pass over the very
gap this is about.

So the real Main.qml is booted offscreen, driven through the real bridge slots,
and asked what the row holds and what the sub-status line renders.

Runs in a SUBPROCESS like the other Main.qml scenarios (shares the sibling's
``_boot``).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from test_progress_matrix_stable_width import _EXIT_NO_QT, _EXIT_OK, _EXIT_PRECONDITION, _boot

_REASON = "6 of 501 tracks failed"


def _row_reason(q, qid: int) -> str:
    return str(
        q(f"(function(){{ var i = queueRowIndexOf({qid}); return i < 0 ? '<no row>' : queueModel.get(i).reason }})()")
    )


def _row_status(q, qid: int) -> str:
    return str(
        q(f"(function(){{ var i = queueRowIndexOf({qid}); return i < 0 ? '<no row>' : queueModel.get(i).status }})()")
    )


def _scenario() -> int:
    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    _root, q, settle, bridge = booted
    problems: list[str] = []

    qid = bridge._enqueue("Pa Que Hablen", "playlist", media_id="pl-35", artist="", tracks=501, collection=True)
    settle(80)

    # Born with the role, so it exists on every row the model ever holds.
    if _row_reason(q, qid) != "":
        problems.append(f"a fresh row already carries a reason: {_row_reason(q, qid)!r}")

    bridge._set_queue_status(qid, "running")
    bridge._set_queue_status(qid, "failed", _REASON)
    settle(120)

    if _row_status(q, qid) != "failed":
        problems.append(f"the row did not reach failed: {_row_status(q, qid)!r}")
    if _row_reason(q, qid) != _REASON:
        problems.append(f"the reason did not reach the model: {_row_reason(q, qid)!r}")

    # And what the row actually SAYS. The sub-status expression is the thing
    # the reporter photographed; a role that exists but is not read would pass
    # every check above and still show "Failed". The drawer has to be open for
    # its delegates to exist at all.
    q("queueDrawer.open()")
    settle(250)
    said = str(
        q(
            "(function(){"
            " function walk(it){"
            "  if (!it) return null;"
            "  if (it.text !== undefined && String(it.text).indexOf('tracks failed') >= 0) return it;"
            "  for (var i = 0; i < it.children.length; i++) {"
            "   var hit = walk(it.children[i].item || it.children[i]);"
            "   if (hit) return hit;"
            "  }"
            "  return null;"
            " }"
            " var hit = walk(queueDrawer.contentItem);"
            " return hit ? String(hit.text) : '<not rendered>';"
            "})()"
        )
    )
    if _REASON not in said:
        problems.append(f"the failed row still does not say why: {said!r}")

    # The full-resync path is a SEPARATE writer from the per-row delta, and a
    # rebuild (or a STOP over a big queue) is delivered that way. Poke the row
    # directly so only the resync can carry the change, which is the shape
    # that would leave a row explaining a failure it no longer has.
    bridge._queue_item(qid)["reason"] = "2 of 501 tracks failed"
    bridge._queue_resync()
    settle(120)
    if _row_reason(q, qid) != "2 of 501 tracks failed":
        problems.append(f"a resync did not carry the reason: {_row_reason(q, qid)!r}")

    bridge._queue_item(qid)["reason"] = ""
    bridge._queue_resync()
    settle(120)
    if _row_reason(q, qid) != "":
        problems.append(f"a resync kept a stale reason: {_row_reason(q, qid)!r}")

    # A row that is retried in place and settles may not keep explaining a
    # failure that no longer stands.
    bridge._set_queue_status(qid, "failed", _REASON)
    settle(120)
    bridge._set_queue_status(qid, "done")
    settle(120)
    if _row_reason(q, qid) != "":
        problems.append(f"a settled row kept its old reason: {_row_reason(q, qid)!r}")

    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("REASON: OK")
    return _EXIT_OK


def test_a_failed_queue_row_shows_why_it_failed():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    # Sandboxed: this scenario builds a REAL WavesBridge, and a bridge that
    # finds the packaged app's config dir adopts its settings, writes its log,
    # and starts a real scan of the user's music library.
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-queue-reason-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    # The scenario prints its findings before Qt's own teardown chatter, so
    # keep enough of the tail that they are still in it.
    lines = [ln for ln in (proc.stdout + proc.stderr).strip().splitlines() if "waves.qt" not in ln]
    tail = "\n".join(lines[-12:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the failed queue row lost its reason again:\n{tail}"


if __name__ == "__main__":
    sys.exit(_scenario())
