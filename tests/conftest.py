"""Shared test doubles for the WavesBridge unit tests.

The bridge tests are Qt-free: they bind the real, unbound ``WavesBridge`` methods
onto a minimal stand-in and drive them with fakes instead of a live QObject, a
QThreadPool, or an event loop. Two of those fakes were copy-pasted into ~10 test
files, and had already drifted into two spellings of the same ``_Signal`` and
several near-identical ``_InlinePool`` copies. They live here now as the single
source of truth; import them with ``from conftest import _Signal, _InlinePool``.

Deliberately NOT centralized:
  * ``_Stub`` stays per-file. It is not one fake but many: each test's stand-in
    carries exactly the state that test's bound methods read and write, so a
    shared version would be a grab-bag, not a contract.
  * A couple of purpose-built variants keep their own copy where the extra
    behavior is the point (e.g. an _InlinePool that counts ``start()`` calls, or
    a signal double that records single values under a different name).
"""

from __future__ import annotations


class _Signal:
    """Stand-in for a Qt signal that records what was emitted.

    ``emit`` stores a single argument as itself and multiple arguments as a
    tuple, so a test can assert on ``sig.emits`` exactly what QML (or a connected
    slot) would have received. It has no ``connect``: the bound code paths under
    test only ever ``emit`` these, never connect to them.
    """

    def __init__(self) -> None:
        self.emits: list = []

    def emit(self, *args) -> None:
        self.emits.append(args[0] if len(args) == 1 else args)


class _InlinePool:
    """Stand-in for a ``QThreadPool`` that runs a dispatched ``Worker``
    synchronously on the calling thread, so worker dispatch is exercised without
    a real thread or event loop and the slot completes before ``start`` returns.
    """

    def start(self, worker) -> None:
        worker.run()
