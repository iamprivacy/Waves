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
2. release_throttle never stops the pacing timer: it hooks the window's
   frames (a hook that fails, raises, or has no window leaves the timer
   path driving), so incubation cannot go dead.
3. The open pacing mirrors Qt's own controller: a short slice after each
   frame while frames flow, the timer slice between frames otherwise, and
   an idle beat when nothing incubates (a 50 ms open slice on a 16 ms tick
   once drove every page build and stuttered scrolling, probe 2026-09-30).
"""

from __future__ import annotations

from waves.waves_ui.app import _BootPacedIncubation


class _Timer:
    def __init__(self) -> None:
        self.stopped = False
        self._interval = _BootPacedIncubation._TICK_MS

    def stop(self) -> None:
        self.stopped = True

    def interval(self) -> int:
        return self._interval

    def setInterval(self, ms: int) -> None:
        self._interval = ms


def _stub(hook=None, count=7):
    class _S:
        pass

    for name in ("_BOOT_SLICE_MS", "_OPEN_SLICE_MS", "_FRAME_SLICE_MS", "_TICK_MS", "_IDLE_TICK_MS", "_FRAME_FRESH_S"):
        setattr(_S, name, getattr(_BootPacedIncubation, name))
    s = _S()
    s._boot = True
    s._released = False
    s._notify = None
    s._win = None
    s._relay = None
    s._last_frame = 0.0
    # A 60 Hz screen: the live slices equal the class constants.
    s._frame_slice_ms, s._open_slice_ms, s._frame_period_s = _BootPacedIncubation._slices_for(60.0)
    s._read_screen = lambda: None
    s._timer = _Timer()
    s.hooks = 0
    s.slices = []
    s.incubatingObjectCount = lambda: count

    def _hook_frames():
        s.hooks += 1
        return hook() if hook is not None else False

    s._hook_frames = _hook_frames
    s._set_tick = lambda ms: _BootPacedIncubation._set_tick(s, ms)
    s.incubateFor = lambda ms: s.slices.append(ms)
    return s


def test_the_count_virtual_is_not_overridden():
    """Qt calls incubatingObjectCountChanged on every incubation start and
    finish, and a Python override made Shiboken take the interpreter for
    each call: one wait per card behind the launch workers, inside the frame
    (sampled 2026-09-12). With no override the wrapper caches the miss and
    never crosses again; the count is polled through count_reader instead."""
    assert "incubatingObjectCountChanged" not in _BootPacedIncubation.__dict__


def test_the_count_reader_answers_the_live_count():
    s = _stub()
    assert _BootPacedIncubation.count_reader(s)() == 7


def test_release_hooks_the_frames_and_keeps_the_timer_driving():
    for hook in (None, lambda: False, lambda: True):
        s = _stub(hook=hook)
        _BootPacedIncubation.release_throttle(s)
        assert not s._boot
        assert s.hooks == 1, "the reveal hooks the window's frames"
        assert not s._timer.stopped, "the timer must keep incubation alive whatever the hook answered"


def test_release_survives_a_raising_hook_and_keeps_driving():
    def _boom():
        raise RuntimeError("no window")

    s = _stub(hook=_boom)
    _BootPacedIncubation.release_throttle(s)
    assert not s._boot
    assert not s._timer.stopped


def test_release_is_idempotent():
    s = _stub(hook=lambda: True)
    _BootPacedIncubation.release_throttle(s)
    _BootPacedIncubation.release_throttle(s)
    assert s.hooks == 1, "the reveal hook and the 20s fallback both fire; the frames are hooked once"


def test_boot_ticks_use_the_boot_slice_only():
    s = _stub()
    _BootPacedIncubation._tick(s)
    assert s.slices == [_BootPacedIncubation._BOOT_SLICE_MS]
    _BootPacedIncubation._frame(s)
    assert s.slices == [_BootPacedIncubation._BOOT_SLICE_MS], "a frame during boot incubates nothing"


def test_open_ticks_incubate_between_frames_and_yield_while_frames_flow():
    import time

    s = _stub()
    s._boot = False
    _BootPacedIncubation._tick(s)
    assert s.slices == [_BootPacedIncubation._OPEN_SLICE_MS], "no frame flowing: the timer carries the slice"
    # A frame 20 ms ago: fresh for the timer (within _FRAME_FRESH_S), and a
    # whole frame ago for the hook, so the next frame slot is a real frame.
    s._last_frame = time.monotonic() - 0.02
    _BootPacedIncubation._tick(s)
    assert s.slices == [_BootPacedIncubation._OPEN_SLICE_MS], "a fresh frame means the frame hook carries incubation"
    _BootPacedIncubation._frame(s)
    assert s.slices[-1] == _BootPacedIncubation._FRAME_SLICE_MS
    assert s._timer.interval() == _BootPacedIncubation._TICK_MS


def test_an_idle_pacer_relaxes_and_a_busy_one_wakes():
    s = _stub(count=0)
    s._boot = False
    _BootPacedIncubation._tick(s)
    assert s.slices == [], "nothing incubating: no slice"
    assert s._timer.interval() == _BootPacedIncubation._IDLE_TICK_MS
    _BootPacedIncubation._frame(s)
    assert s.slices == [], "an idle frame incubates nothing"
    assert s._timer.interval() == _BootPacedIncubation._IDLE_TICK_MS
    s.incubatingObjectCount = lambda: 3
    s._last_frame = 0.0  # the last frame is long gone: the timer carries the slice
    _BootPacedIncubation._tick(s)
    assert s._timer.interval() == _BootPacedIncubation._TICK_MS
    assert s.slices == [_BootPacedIncubation._OPEN_SLICE_MS]


def test_the_open_slices_stay_short():
    assert _BootPacedIncubation._OPEN_SLICE_MS <= 12
    assert _BootPacedIncubation._FRAME_SLICE_MS <= 6
    assert _BootPacedIncubation._IDLE_TICK_MS > _BootPacedIncubation._TICK_MS
