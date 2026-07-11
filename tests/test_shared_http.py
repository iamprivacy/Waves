"""The segment-download HTTP session is process-wide (Download._shared_http):
a per-instance session meant a cold connection pool for every queued album, so
each album start paid its TLS handshakes all at once (a brief all-core CPU
spike per click of Download). All instances must share one warm pool, growing
it only when the configured concurrency rises.
"""

from __future__ import annotations

import pytest

from tidaler.download import Download


@pytest.fixture(autouse=True)
def _reset_shared_session():
    """Isolate each test from the process-wide singleton."""
    Download._http_shared = None
    Download._http_pool = 0
    yield
    Download._http_shared = None
    Download._http_pool = 0


def test_same_session_across_calls():
    s1 = Download._shared_http(10)
    s2 = Download._shared_http(10)
    assert s1 is s2
    assert Download._http_pool == 10


def test_adapter_kept_when_pool_is_enough():
    s = Download._shared_http(60)
    adapter = s.get_adapter("https://example.com")
    # A later instance configured smaller must not shrink or remount
    # (remounting drops the warm keep-alive connections).
    assert Download._shared_http(10) is s
    assert s.get_adapter("https://example.com") is adapter
    assert Download._http_pool == 60


def test_pool_grows_by_remounting_once():
    s = Download._shared_http(10)
    small = s.get_adapter("https://example.com")
    assert Download._shared_http(60) is s, "growing must keep the session (and its cookies)"
    grown = s.get_adapter("https://example.com")
    assert grown is not small
    assert grown._pool_maxsize == 60
    assert Download._http_pool == 60


def test_both_schemes_mounted():
    s = Download._shared_http(10)
    assert s.get_adapter("https://example.com")._pool_maxsize == 10
    assert s.get_adapter("http://example.com")._pool_maxsize == 10
