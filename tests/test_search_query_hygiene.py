"""Issue #39: a pasted title with a line break never reached TIDAL as typed.

Two backend rules behind ``WavesBridge.search``:

* every run of whitespace in the query collapses to one space before it is
  sent, so a multi-line paste searches the words the field shows, and
* a fetch that raises reports "Search failed", never "0 results" (which read
  as a search that found nothing), emits nothing and caches nothing, so a
  stale page already painted stays.
"""

from __future__ import annotations

from test_search_stale_revalidate import _STALE_STAMP, _payload, _payloads, _Stub, _wire

from waves.waves_ui import backend


def test_interior_whitespace_collapses_before_the_wire(monkeypatch):
    _wire(monkeypatch)
    sent = []
    real = backend.search_results_all
    monkeypatch.setattr(
        backend, "search_results_all", lambda session, needle, **kw: sent.append(needle) or real(session, needle, **kw)
    )
    stub = _Stub()
    stub.search("  Le Guinness World Record\nSoft Power\tgonzales  ")
    assert sent == ["Le Guinness World Record Soft Power gonzales"]


def _boom(session, needle, **kw):
    raise RuntimeError("network down")


def test_a_raised_fetch_reports_failure_not_zero_results(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(backend, "search_results_all", _boom)
    stub = _Stub()
    stub.search("needle")
    assert stub.statuses[-1] == "Search failed"
    assert not any(s.endswith(" results") for s in stub.statuses)
    assert stub.busy[-1] is False
    assert stub.searchResults.emits == []
    assert stub.saves == 0 and "needle" not in stub._search_cache


def test_a_raised_fetch_keeps_the_stale_page(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(backend, "search_results_all", _boom)
    stub = _Stub()
    stub._search_cache["needle"] = (_STALE_STAMP, _payload(("al1",)))
    stub.search("needle")
    emitted = _payloads(stub)
    assert len(emitted) == 1 and "refresh" not in emitted[0], "only the stale page, nothing replaces it"
    assert stub.statuses[-1] == "Search failed"
    assert stub._search_cache["needle"][0] == _STALE_STAMP
