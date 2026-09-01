"""The boot-paced incubation controller can never leave incubation dead.

THE FAILURE THIS FENCES OFF
---------------------------
The first shipped _BootPacedIncubation started its pacing timer from the
incubatingObjectCountChanged virtual, and overrode it with no parameters.
The binding passes the new count positionally, so EVERY call raised
TypeError, the timer never started, and, because the controller had
replaced the window's for the whole session, no async Loader in the whole
app could ever complete: the launch revealed a blank, dead landing
(reported from livetesting, crash.log full of the TypeError).

Two contracts, pinned with the method-bound stub pattern (no display):

1. The count virtual accepts both call spellings (with and without the
   count argument) and stays informational: nothing that keeps incubation
   alive may live inside it.
2. release_throttle hands the engine back to the window's own controller
   and only stops the pacing timer when that handback succeeded; with no
   handback the timer keeps driving, so incubation cannot go dead.
"""

from __future__ import annotations

from waves.waves_ui.app import _BootPacedIncubation


class _Timer:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _stub(handback=None):
    class _S:
        pass

    s = _S()
    s._boot = True
    s._released = False
    s._notify = None
    s._handback = handback
    s._timer = _Timer()
    s.incubatingObjectCount = lambda: 7
    return s


def test_the_count_virtual_accepts_the_bindings_argument():
    seen = []
    s = _stub()
    s._notify = seen.append
    _BootPacedIncubation.incubatingObjectCountChanged(s, 3)
    _BootPacedIncubation.incubatingObjectCountChanged(s)
    assert seen == [7, 7], "both call spellings must reach the notifier with the queried count"


def test_the_count_virtual_never_touches_the_timer():
    # Pacing must not depend on this virtual firing (it is the exact call
    # that silently died); the timer is driven by __init__ and release only.
    s = _stub()
    s._timer = None
    _BootPacedIncubation.incubatingObjectCountChanged(s, 1)
    _BootPacedIncubation.incubatingObjectCountChanged(s)


def test_release_stops_the_timer_only_after_a_successful_handback():
    s = _stub(handback=lambda: True)
    seen = []
    s._notify = seen.append
    _BootPacedIncubation.release_throttle(s)
    assert s._boot is False
    assert s._timer.stopped, "handback succeeded, the pacing timer must stop"
    assert seen == [0], "the reveal gate must see the count cleared"


def test_release_keeps_driving_without_a_window_to_hand_back_to():
    for handback in (None, lambda: False):
        s = _stub(handback=handback)
        _BootPacedIncubation.release_throttle(s)
        assert s._boot is False, "the open slice must apply"
        assert not s._timer.stopped, "no handback: the timer must keep incubation alive"


def test_release_survives_a_raising_handback_and_keeps_driving():
    def _boom():
        raise RuntimeError("no window")

    s = _stub(handback=_boom)
    _BootPacedIncubation.release_throttle(s)
    assert not s._timer.stopped


def test_release_is_idempotent():
    calls = []
    s = _stub(handback=lambda: calls.append(1) or True)
    _BootPacedIncubation.release_throttle(s)
    _BootPacedIncubation.release_throttle(s)
    assert calls == [1], "the reveal hook and the 20s fallback both fire; the handback runs once"
