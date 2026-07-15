"""Regression guard for the broadcast-progress rate gate (WavesBridge).

A single DASH-delivered track emits item() progress per segment with no upstream
throttle. Each downloadProgress broadcast reaches every instantiated download
control on the GUI thread, so an ungated burst stole scroll/art frames. The gate
(_should_broadcast_pct) coalesces the broadcast to a 0.5% min delta or a ~10 Hz
ceiling per media id, while never swallowing the first tick or the terminal 100%.

The gate is a pure function of (per-id last emit, monotonic clock), so a fake
clock exercises the time branch without sleeping.
"""

from __future__ import annotations

import tidaler.waves_ui.backend as backend
from tidaler.waves_ui.backend import WavesBridge


class _Stub:
    """Minimal stand-in carrying only the state the gate touches."""

    def __init__(self) -> None:
        self._pct_last: dict[str, tuple[float, float]] = {}


def test_gate_delta_and_interval(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(backend.time, "monotonic", lambda: clock["t"])
    gate = WavesBridge._should_broadcast_pct
    s = _Stub()

    assert gate(s, "A", 3.0) is True  # first tick always broadcasts
    assert gate(s, "A", 3.2) is False  # <0.5% delta, no time passed -> coalesced
    assert gate(s, "A", 3.6) is True  # >=0.5% delta -> broadcasts
    assert gate(s, "A", 3.7) is False  # <0.5% since last, no time passed

    clock["t"] += 0.1  # ~10 Hz ceiling reached
    assert gate(s, "A", 3.75) is True  # tiny delta but interval elapsed


def test_gate_never_swallows_terminal(monkeypatch):
    clock = {"t": 500.0}
    monkeypatch.setattr(backend.time, "monotonic", lambda: clock["t"])
    gate = WavesBridge._should_broadcast_pct
    s = _Stub()

    assert gate(s, "A", 99.9) is True
    # 100% arrives immediately after, with a sub-0.5% delta and no time passed:
    # it must still broadcast so a bar can actually reach complete.
    assert gate(s, "A", 100.0) is True


def test_gate_is_per_media_id(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(backend.time, "monotonic", lambda: clock["t"])
    gate = WavesBridge._should_broadcast_pct
    s = _Stub()

    assert gate(s, "A", 10.0) is True
    # A different id's first tick is independent of A's throttle window.
    assert gate(s, "B", 0.2) is True
    assert gate(s, "B", 0.3) is False  # B now has its own window
